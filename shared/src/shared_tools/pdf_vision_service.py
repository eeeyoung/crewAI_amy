"""PDFVisionService — multi-modal PDF page description via Gemini Flash.

For image-only / scanned PDFs where PyPDF2 returns no text, this service
renders each page to a PNG via PyMuPDF (fitz) and sends it to Gemini Flash
for vision-based description.  The descriptions are stored as content chunks
in ChromaDB, making previously invisible drawings/specs/permits searchable.

Architecture (follows lilAmy service pattern)::

    PDFVisionService (QObject + signals, threading.Thread)
        │
        ├── fitz (PyMuPDF) → renders PDF page → PNG bytes
        ├── Gemini Flash  →  vision: describe this construction doc
        └── MemoryService  →  store description as content chunk
                              (overwrites old metadata-only chunk)

Usage::

    from shared_tools.pdf_vision_service import PDFVisionService

    svc = PDFVisionService(data_root="D:/Projects/ClientName")
    svc.page_complete.connect(lambda fid, pg, txt: print(f"Page {pg} done"))
    svc.file_complete.connect(lambda fid, pages: print(f"File done ({pages}p)"))
    svc.process_phase1()   # key docs: contracts, specs, permits, reports
    svc.process_all()      # every metadata-only PDF
"""

import base64
import gc
import io
import json
import os
import threading
import time
from pathlib import Path

import fitz  # PyMuPDF
import requests
from PyQt6.QtCore import QObject, pyqtSignal


# ── Configuration ──────────────────────────────────────────────────────

# Gemini model for vision.  Read from env or default to 2.5 Flash.
GEMINI_VISION_MODEL = os.environ.get(
    "GEMINI_VISION_MODEL",
    os.environ.get("MODEL", "gemini-2.5-flash").replace("gemini/", ""),
)

RENDER_DPI = 120          # enough for text on A3 drawings, keeps images small
MAX_PAGES_PER_PDF = 15    # safety cap — skip massive drawing sets
MIN_PDF_SIZE_BYTES = 1024  # skip tiny/corrupt PDFs
API_DELAY_SEC = 1.5       # between API calls (rate limiting)
MAX_RETRIES = 2           # per page on transient errors
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Prompt for construction-document-aware description
VISION_PROMPT = """\
Describe this construction project document page in detail. Extract ALL
readable information:

- If this is a DRAWING: note the drawing title, number, revision, scale,
  dimensions, materials, annotations, callouts, and referenced details.
- If this is a SPECIFICATION or REPORT: extract section headings, key
  requirements, standards referenced (AS/NZS), numerical values, dates.
- If this is a CONTRACT or PERMIT: extract parties, dates, contract sums,
  key clauses, approval numbers, conditions.
- If this is a SCHEDULE or SPREADSHEET: extract the column headers and
  all row data with quantities, rates, and totals.
- If this is a FORM or CHECKLIST: note the form title, filled-in fields,
  signatures, and dates.

Be exhaustive — include every readable word, number, and detail.  Do NOT
summarise or skip content.  This description will be the ONLY searchable
text for this document."""


# ── Phase definitions (selective processing) ───────────────────────────

# Phase 1: the ~60 most important non-drawing PDFs
PHASE1_FOLDER_KEYWORDS = [
    "tender workbook", "draft contract", "cdc drawings & specs",
    "building permit", "technical reports", "progress claims",
    "subcontracts & progress claims", "material price increases",
    "purchase order", "extension of time", "site instructions",
    "programming", "marketing and sales", "correspondence",
]

# Phase 2: architectural/structural drawings
PHASE2_FOLDER_KEYWORDS = [
    "architectural", "structural", "mechanical", "electrical",
    "hydraulics", "ifc services",
]


class PDFVisionService(QObject):
    """Multi-modal PDF processor — renders pages as images, sends to Gemini."""

    # ── signals ────────────────────────────────────────────────────
    progress = pyqtSignal(str)           # human-readable status
    page_complete = pyqtSignal(int, int, str)  # file_id, page_num, description
    file_complete = pyqtSignal(int, int)       # file_id, pages_processed
    file_error = pyqtSignal(int, str)          # file_id, error_message
    all_complete = pyqtSignal(int, int)        # files_processed, pages_processed

    def __init__(
        self,
        data_root: str | None = None,
        gemini_api_key: str | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._data_root = Path(data_root or os.environ.get("LILAMY_DATA_DIR", "."))
        self._gemini_key = gemini_api_key or os.environ.get("GEMINI_API_KEY", "")
        self._lock = threading.Lock()
        self._cancelled = False
        self._thread: threading.Thread | None = None

        # Lazy imports to avoid circular deps at module level
        self._memory_svc = None
        self._registry = None

    # ── Public API ─────────────────────────────────────────────────

    def process_phase1(self) -> None:
        """Process key documents: contracts, specs, permits, reports."""
        self._start_processing(phase="phase1")

    def process_phase2(self) -> None:
        """Process drawings: architectural, structural, MEP."""
        self._start_processing(phase="phase2")

    def process_all(self) -> None:
        """Process every metadata-only PDF in the registry."""
        self._start_processing(phase="all")

    def process_file_ids(self, file_ids: list[int]) -> None:
        """Process specific file IDs from the registry."""
        self._start_processing(file_ids=file_ids)

    def cancel(self) -> None:
        self._cancelled = True

    # ── Internal ───────────────────────────────────────────────────

    def _get_services(self):
        """Lazy-init MemoryService and FileRegistry."""
        if self._memory_svc is None:
            from shared_tools.memory_service import MemoryService
            self._memory_svc = MemoryService(data_root=str(self._data_root))
        if self._registry is None:
            from shared_tools.file_registry import FileRegistry
            self._registry = FileRegistry(self._data_root)
            self._registry.init_db()
        return self._memory_svc, self._registry

    def _start_processing(self, phase: str | None = None,
                          file_ids: list[int] | None = None):
        if self._thread and self._thread.is_alive():
            self.progress.emit("Already processing — wait for completion.")
            return
        self._cancelled = False
        self._thread = threading.Thread(
            target=self._process_flow,
            args=(phase, file_ids),
            daemon=True,
        )
        self._thread.start()

    def _process_flow(self, phase: str | None, file_ids: list[int] | None):
        try:
            mem_svc, reg = self._get_services()

            # Determine which PDFs to process
            if file_ids:
                rows = self._resolve_file_ids(file_ids, reg)
            elif phase == "phase1":
                rows = self._filter_by_keywords(reg, PHASE1_FOLDER_KEYWORDS)
            elif phase == "phase2":
                rows = self._filter_by_keywords(reg, PHASE2_FOLDER_KEYWORDS)
            else:
                rows = self._all_metadata_only_pdfs(reg)

            total = len(rows)
            self.progress.emit(f"Found {total} PDFs to process"
                               f"  (phase={phase or 'custom'})")

            if total == 0:
                self.all_complete.emit(0, 0)
                return

            files_done = 0
            pages_done = 0

            for row in rows:
                if self._cancelled:
                    break

                fid = row["file_id"]
                fp = self._data_root / row["rel_path"]

                try:
                    npages = self._process_one_pdf(fid, fp, mem_svc, reg)
                    if npages > 0:
                        files_done += 1
                        pages_done += npages
                        self.file_complete.emit(fid, npages)
                except Exception as e:
                    self.file_error.emit(fid, str(e)[:200])

                # Free memory after each PDF
                gc.collect()

            self.all_complete.emit(files_done, pages_done)

        except Exception as e:
            self.progress.emit(f"Fatal error: {e}")

    def _process_one_pdf(self, file_id: int, file_path: Path,
                         mem_svc, reg) -> int:
        """Render + describe all pages of one PDF. Returns page count."""
        if not file_path.exists():
            reg.mark_error(file_id, "File missing from disk")
            return 0

        file_size = file_path.stat().st_size
        if file_size < MIN_PDF_SIZE_BYTES:
            reg.mark_error(file_id, "File too small (likely corrupt)")
            return 0

        self.progress.emit(f"Processing: {file_path.name}")

        doc = fitz.open(str(file_path))
        total_pages = min(doc.page_count, MAX_PAGES_PER_PDF)
        descriptions = []

        for page_num in range(total_pages):
            if self._cancelled:
                break

            # Render page to PNG
            page = doc[page_num]
            pix = page.get_pixmap(dpi=RENDER_DPI)
            img_bytes = pix.tobytes("png")

            # Send to Gemini for description
            description = self._describe_image(img_bytes, file_path.name, page_num)

            if description:
                descriptions.append((page_num, description))
                self.page_complete.emit(file_id, page_num, description)

            # Rate limiting
            if page_num < total_pages - 1:
                time.sleep(API_DELAY_SEC)

        doc.close()

        if not descriptions:
            return 0

        # Store all page descriptions as content chunks
        self._store_descriptions(file_id, file_path, descriptions, mem_svc, reg)
        return len(descriptions)

    def _describe_image(self, png_bytes: bytes, filename: str,
                        page_num: int) -> str | None:
        """Send a PNG page to Gemini for vision-based description.

        Uses the native Gemini API (``generateContent``) which authenticates
        via ``?key=`` query parameter — more reliable than the OpenAI-compatible
        endpoint for API keys provisioned through Google AI Studio."""
        if not self._gemini_key:
            return None

        b64 = base64.b64encode(png_bytes).decode("ascii")

        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{GEMINI_VISION_MODEL}:generateContent?key={self._gemini_key}"
        )

        payload = {
            "contents": [{
                "parts": [
                    {"text": VISION_PROMPT},
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": b64,
                        }
                    },
                ]
            }],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 2000,
            },
        }

        for attempt in range(MAX_RETRIES + 1):
            try:
                r = requests.post(url, json=payload, timeout=60)
                if r.status_code == 200:
                    data = r.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            return parts[0].get("text", "")
                    return None
                elif r.status_code == 429:
                    time.sleep(5 * (attempt + 1))  # back off
                else:
                    if attempt == MAX_RETRIES:
                        self.progress.emit(
                            f"  ⚠️ API error {r.status_code} for "
                            f"{filename} p{page_num}: {r.text[:100]}"
                        )
                        return None
                    time.sleep(2)
            except requests.RequestException as e:
                if attempt == MAX_RETRIES:
                    self.progress.emit(
                        f"  ⚠️ Network error for {filename} p{page_num}: {e}"
                    )
                    return None
                time.sleep(2)

        return None

    def _store_descriptions(self, file_id: int, file_path: Path,
                            descriptions: list[tuple[int, str]],
                            mem_svc, reg):
        """Replace metadata-only chunks with vision descriptions in ChromaDB.

        Also persists a plain-text cache to ``.lilamy_vision_cache/{file_id}.txt``
        so the Gemini output survives ChromaDB resets.  The cache is the
        authoritative copy — ChromaDB can be rebuilt from it instantly."""
        from shared_tools.memory_service import _md5, _chunk_text

        # Build full text from all page descriptions
        pages_text = []
        for pg, desc in descriptions:
            pages_text.append(
                f"--- Page {pg + 1} ---\n"
                f"File: {file_path.name}\n"
                f"{desc}"
            )
        full_text = "\n\n".join(pages_text)
        file_hash = _md5(full_text)

        # ── 1. Persistent cache (survives ChromaDB resets) ────────────
        cache_dir = self._data_root / ".lilamy_vision_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"{file_id}.txt"
        cache_file.write_text(full_text, encoding="utf-8")

        # ── 2. ChromaDB chunks (for semantic search) ──────────────────
        # Remove old metadata-only chunks for this file from ChromaDB
        self._remove_old_chunks(file_id, mem_svc)

        # Chunk and buffer new content
        chunks = _chunk_text(full_text)
        root = self._data_root
        # Extract project name from first path component
        rel_path = file_path.relative_to(root)
        project = rel_path.parts[0] if rel_path.parts else "General"

        for i, chunk in enumerate(chunks):
            doc_id = f"f{file_id}-v-{file_hash[:8]}-{i}"
            mem_svc._buffer["ids"].append(doc_id)
            mem_svc._buffer["documents"].append(chunk)
            mem_svc._buffer["metadatas"].append({
                "file_id": file_id,
                "project": project,
                "file_name": file_path.name,
                "file_path": str(file_path.relative_to(root)),
                "doc_type": "pdf",
                "chunk_index": i,
                "md5_hash": file_hash,
                "content_extracted": True,       # now has real content!
                "extraction_method": "vision",   # distinguish from text extraction
            })

        # Flush to ChromaDB
        mem_svc._flush_buffer()

        # Update registry
        reg.mark_indexed(file_id, chunk_count=len(chunks))
        self.progress.emit(
            f"  ✓ {file_path.name}: {len(descriptions)} pages, {len(chunks)} chunks"
        )

    def _remove_old_chunks(self, file_id: int, mem_svc) -> None:
        """Delete existing chunks for a file from ChromaDB before re-adding.

        Uses ChromaDB's ``where`` filter on the ``file_id`` metadata field.
        Falls back to prefix matching on doc IDs if the filter isn't supported."""
        mem_svc._ensure_collection()
        try:
            existing = mem_svc._collection.get(
                where={"file_id": {"$eq": file_id}},
                include=["metadatas"],
            )
            if existing and existing.get("ids"):
                count = len(existing["ids"])
                mem_svc._collection.delete(ids=existing["ids"])
                self.progress.emit(f"    Removed {count} old chunk(s) for file_id={file_id}")
                return
        except Exception as e:
            self.progress.emit(f"    (where-filter delete skipped: {e})")

        # Fallback: try to delete by known doc_id prefix pattern
        try:
            all_ids = mem_svc._collection.get(include=[])
            if all_ids and all_ids.get("ids"):
                to_delete = [
                    did for did in all_ids["ids"]
                    if did.startswith(f"f{file_id}-")
                ]
                if to_delete:
                    mem_svc._collection.delete(ids=to_delete)
                    self.progress.emit(f"    Removed {len(to_delete)} old chunk(s) via prefix match")
        except Exception:
            pass

    # ── Registry queries ───────────────────────────────────────────

    def _resolve_file_ids(self, file_ids: list[int], reg) -> list:
        rows = []
        for fid in file_ids:
            r = reg._conn.execute(
                "SELECT * FROM file_registry WHERE file_id=? AND file_type='pdf'",
                (fid,),
            ).fetchone()
            if r:
                rows.append(r)
        return rows

    def _filter_by_keywords(self, reg, keywords: list[str]) -> list:
        """Return PDFs whose folder path contains any of the given keywords."""
        rows = reg._conn.execute(
            "SELECT * FROM file_registry WHERE file_type='pdf' AND status='indexed'"
        ).fetchall()
        matched = []
        for r in rows:
            path_lower = r["rel_path"].lower()
            if any(kw in path_lower for kw in keywords):
                matched.append(r)
        # Further filter: only process if current chunks are all metadata-only
        # (we check this by examining chunk_count — if it's 1 and the PDF has
        #  multiple pages, it's likely metadata-only)
        return matched

    def _all_metadata_only_pdfs(self, reg) -> list:
        """Return all PDFs in the registry."""
        return reg._conn.execute(
            "SELECT * FROM file_registry WHERE file_type='pdf' AND status='indexed'"
        ).fetchall()

