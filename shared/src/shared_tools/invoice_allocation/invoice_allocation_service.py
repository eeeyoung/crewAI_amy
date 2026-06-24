"""InvoiceAllocationService — auto-allocate invoice PDFs to project subfolders.

Scans a directory, reads invoice PDFs, determines which project each belongs to,
and moves them into the correct NNNN - ProjectName subfolder.

Matching pipeline (cheapest → most expensive, short-circuits on confidence):
  Phase 1: FILENAME — tokenize filename, match project codes/names
  Phase 2: TEXT — extract PDF text via pdfplumber, search for codes/names
  Phase 3: LLM — send text + project list to LLM for classification (fallback)

Follows the project service pattern:
  QObject + pyqtSignal + threading.Thread + queue.Queue
"""
from __future__ import annotations

import hashlib
import json
import queue
import re
import shutil
import threading
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal


# =============================================================================
# Helpers (module-level, no state)
# =============================================================================


def _scan_project_folders(root: Path) -> tuple[list[dict], list[dict]]:
    """Scan root for project subfolders and control subfolders.

    Returns (projects, control_folders) where:
      projects: [{code, name, full_name, path}]
      control_folders: [{prefix, name, path}]
    """
    projects: list[dict] = []
    control_folders: list[dict] = []

    for item in sorted(root.iterdir()):
        if not item.is_dir():
            continue
        name = item.name.strip()

        # Match 4-digit prefix: "1502 - Laviche"
        m4 = re.match(r"^(\d{4})\s*[-–—]\s*(.+)$", name)
        if m4:
            projects.append({
                "code": m4.group(1),
                "name": m4.group(2).strip(),
                "full_name": name,
                "path": item,
            })
            continue

        # Match 2-digit prefix: "01. HOLD", "02. Next Run"
        m2 = re.match(r"^(\d{2})\.\s*(.+)$", name)
        if m2:
            control_folders.append({
                "prefix": m2.group(1),
                "name": m2.group(2).strip(),
                "path": item,
            })
            continue

    return projects, control_folders


def _match_project(
    project_name: str, projects: list[dict]
) -> dict | None:
    """Find the best matching project by name or code.

    Pattern adapted from variation_agent.py:_match_project().

    Args:
        project_name: extracted/guessed project name or code string
        projects: list of {code, name, full_name, path} dicts

    Returns: {code, name, full_name, path, score} or None
    """
    if not project_name or not projects:
        return None

    target = project_name.lower().strip()

    # 1. Exact code match (e.g., "2698" matches project code "2698")
    for p in projects:
        if p["code"] == target:
            return {**p, "score": 1.0}

    # 2. Exact name match (case-insensitive)
    for p in projects:
        if p["name"].lower().strip() == target:
            return {**p, "score": 1.0}

    # 3. Substring match — longest project name wins
    best: dict | None = None
    best_len = 0
    for p in projects:
        pname = p["name"].lower().strip()
        if target in pname or pname in target:
            if len(pname) > best_len:
                best = {**p, "score": 0.85}
                best_len = len(pname)

    return best


def _tokenize(name: str) -> set[str]:
    """Tokenize a string into lowercase tokens for matching."""
    # Split on common delimiters
    tokens = re.split(r"[\s\-_.,;:()\[\]{}]+", name.lower())
    return {t for t in tokens if len(t) >= 2}


# =============================================================================
# InvoiceAllocationService
# =============================================================================


class InvoiceAllocationService(QObject):
    """Auto-allocate invoice PDFs to project subfolders.

    Signals (UI connects to these):
      allocation_started(total_files: int)
      file_processed(filename: str, target_project: str, confidence: float)
      file_skipped(filename: str, reason: str)
      file_error(filename: str, error_message: str)
      allocation_complete(moved: int, flagged: int, failed: int)
      progress_update(percentage: int, description: str)
    """

    # ── Signals ───────────────────────────────────────────────────────
    allocation_started = pyqtSignal(int)
    file_processed = pyqtSignal(str, str, float)
    file_skipped = pyqtSignal(str, str)
    file_error = pyqtSignal(str, str)
    allocation_complete = pyqtSignal(int, int, int)
    progress_update = pyqtSignal(int, str)

    # ── Matching thresholds ───────────────────────────────────────────
    AUTO_MOVE_THRESHOLD = 0.8    # ≥ 0.8 → auto-move to project folder
    PENDING_THRESHOLD = 0.01     # > 0 → pending user confirmation
                                  # == 0 → no match, leave in place

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._work_queue: queue.Queue = queue.Queue()
        self._llm_semaphore = threading.Semaphore(1)
        self._running = False

        # Initialize DB on creation
        from shared_tools.invoice_allocation.invoice_allocation_db import (
            init_invoice_allocation_db,
        )
        init_invoice_allocation_db()

    # ── Lifecycle ─────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the worker thread."""
        if self._running:
            return
        self._running = True
        self._worker_thread = threading.Thread(
            target=self._run_loop, daemon=True
        )
        self._worker_thread.start()

    def stop(self) -> None:
        """Stop the worker thread."""
        self._running = False
        self._work_queue.put(None)

    def _run_loop(self) -> None:
        while self._running:
            try:
                task = self._work_queue.get(timeout=0.5)
                if task is None:
                    break
                action, kwargs = task
                handler = getattr(self, f"_handle_{action}", None)
                if handler:
                    try:
                        handler(**kwargs)
                    except Exception as e:
                        self.file_error.emit("", str(e))
            except queue.Empty:
                continue

    def _queue(self, action: str, **kwargs) -> None:
        self._work_queue.put((action, kwargs))

    # ── Public Methods ────────────────────────────────────────────────

    def allocate_folder(self, folder_path: str) -> None:
        """Queue a full folder allocation run (async — returns immediately).

        Connect to signals for results.
        """
        self._queue("allocate_folder", folder_path=folder_path)

    def allocate_folder_sync(self, folder_path: str) -> dict:
        """Run allocation synchronously (for CLI use).

        Returns: {total, moved, flagged, failed, records: [...]}
        """
        return self._handle_allocate_folder(folder_path)

    def get_allocation_history(self, limit: int = 50) -> list[dict]:
        """Return recent allocation records."""
        from shared_tools.invoice_allocation.invoice_allocation_db import (
            get_history,
        )
        return get_history(limit)

    def confirm_allocation(self, record_id: int) -> bool:
        """Mark a flagged allocation as confirmed."""
        from shared_tools.invoice_allocation.invoice_allocation_db import (
            confirm_allocation,
        )
        return confirm_allocation(record_id)

    def undo_allocation(self, record_id: int) -> bool:
        """Move a file back to its original location."""
        from shared_tools.invoice_allocation.invoice_allocation_db import (
            get_record,
            set_record_status,
        )
        record = get_record(record_id)
        if not record:
            return False

        src = Path(record["moved_to_path"])
        dst = Path(record["original_path"])
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))

        set_record_status(record_id, "undone", undone_at=True)
        return True

    # ── Main Handler ──────────────────────────────────────────────────

    def _handle_allocate_folder(self, folder_path: str) -> dict:
        """Full allocation pipeline: scan → extract → match → move."""
        root = Path(folder_path)
        if not root.exists() or not root.is_dir():
            self.file_error.emit("", f"Folder not found: {folder_path}")
            return {"total": 0, "moved": 0, "flagged": 0, "failed": 0}

        # Phase 0: Scan folder structure
        projects, _control_folders = _scan_project_folders(root)

        if not projects:
            self.file_error.emit(
                "", f"No project folders found (4-digit prefix) in: {folder_path}"
            )
            return {"total": 0, "moved": 0, "flagged": 0, "failed": 0}

        # Find root-level PDFs
        invoice_pdfs = sorted(
            p for p in root.iterdir()
            if p.is_file() and p.suffix.lower() == ".pdf"
        )
        total = len(invoice_pdfs)

        if total == 0:
            self.allocation_complete.emit(0, 0, 0)
            return {"total": 0, "moved": 0, "flagged": 0, "failed": 0}

        self.allocation_started.emit(total)

        # Create DB run record
        from shared_tools.invoice_allocation.invoice_allocation_db import (
            create_run,
            finalize_run,
        )
        run_id = create_run(
            folder_path, total,
            [p["full_name"] for p in projects],
        )

        moved = pending_count = no_match_count = failed = 0
        moved_items: list[dict] = []
        pending_items: list[dict] = []
        no_match_items: list[dict] = []

        for i, pdf_path in enumerate(invoice_pdfs):
            self.progress_update.emit(
                int((i / total) * 100),
                f"Processing: {pdf_path.name}",
            )

            try:
                result = self._allocate_one(pdf_path, projects, run_id)

                if result["status"] == "skipped":
                    self.file_skipped.emit(
                        pdf_path.name, result.get("reason", "")
                    )
                    continue

                if result["status"] == "moved":
                    self.file_processed.emit(
                        pdf_path.name,
                        result.get("target_project", ""),
                        result.get("confidence", 0),
                    )
                    moved_items.append(result)
                    moved += 1
                elif result["status"] == "pending_confirmation":
                    pending_items.append(result)
                    pending_count += 1
                elif result["status"] == "no_match":
                    no_match_items.append(result)
                    no_match_count += 1

            except Exception as e:
                self._record_error(run_id, pdf_path, str(e))
                self.file_error.emit(pdf_path.name, str(e))
                failed += 1

        # Finalize run
        finalize_run(run_id, moved, pending_count, no_match_count, failed,
                     status="error" if failed > 0 else "completed")
        self.allocation_complete.emit(moved, pending_count, failed)

        return {
            "total": total,
            "moved": moved,
            "moved_items": moved_items,
            "pending": pending_items,
            "no_match": no_match_items,
            "failed": failed,
            "projects": [p["full_name"] for p in projects],
            "run_id": run_id,
        }

    # ── Single File Allocation ────────────────────────────────────────

    def _allocate_one(
        self, pdf_path: Path, projects: list[dict], run_id: int
    ) -> dict:
        """Process a single invoice PDF and determine its target.

        Runs filename + text phases and combines scores. Only falls back
        to LLM when deterministic signals are inconclusive.

        Resolution:
          - score ≥ 0.8 → auto-move to project folder
          - score > 0   → pending confirmation (CLI asks user)
          - score == 0  → no match (file stays in root)

        Returns: {status, ...} where status is one of:
          "moved", "pending_confirmation", "no_match", "skipped"
        """
        filename = pdf_path.name

        # Check if already in a project folder
        parent_name = pdf_path.parent.name
        if re.match(r"^\d{4}\s*[-–—]", parent_name):
            self._record_skip(
                run_id, pdf_path, "Already in project folder"
            )
            return {
                "status": "skipped",
                "reason": "Already in project folder",
                "filename": filename,
            }

        # Phase 1 & 2: Filename + text deterministic matching
        fname_match = self._match_by_filename(filename, projects)
        text = self._extract_pdf_text(pdf_path)
        text_match = self._match_by_text(text or "", projects) if text else None

        # Combine scores
        best_match = self._combine_matches(fname_match, text_match)

        # High confidence → auto-move
        if best_match and best_match["score"] >= self.AUTO_MOVE_THRESHOLD:
            method = "combined" if (fname_match and text_match and
                fname_match.get("code") == text_match.get("code")) else (
                "filename" if fname_match and fname_match["score"] >= self.AUTO_MOVE_THRESHOLD
                else "text"
            )
            return self._execute_move(
                pdf_path, best_match, method, run_id
            )

        # Phase 3: LLM fallback for inconclusive cases
        llm_result = self._classify_with_llm(
            text or "", filename, projects
        )

        # Pick the best match: prefer LLM if it's more confident, else deterministic
        if llm_result:
            # Cap LLM confidence when deterministic found nothing
            if best_match is None:
                llm_result["score"] = min(
                    llm_result.get("score", 0), self.PENDING_THRESHOLD + 0.39
                )

            if llm_result.get("score", 0) >= self.AUTO_MOVE_THRESHOLD:
                return self._execute_move(
                    pdf_path, llm_result, "llm", run_id
                )

            # LLM has a suggestion but not confident enough → pending
            if llm_result.get("score", 0) > 0:
                return self._record_pending_confirmation(
                    pdf_path, llm_result, "llm", run_id
                )

        # Deterministic match below threshold but > 0 → pending
        if best_match and best_match["score"] > 0:
            return self._record_pending_confirmation(
                pdf_path, best_match,
                "filename" if fname_match and fname_match == best_match else "text",
                run_id,
            )

        # Zero confidence → no match, file stays in root
        return self._record_no_match(pdf_path, run_id)

    def _record_pending_confirmation(
        self,
        pdf_path: Path,
        match: dict,
        match_method: str,
        run_id: int,
    ) -> dict:
        """Record a low-confidence match that needs user confirmation.

        File stays in place. DB record is created for tracking.
        """
        from shared_tools.invoice_allocation.invoice_allocation_db import (
            record_pending,
        )
        record_pending(
            run_id=run_id,
            filename=pdf_path.name,
            original_path=str(pdf_path),
            target_project_code=match.get("code"),
            target_project_name=match.get("name"),
            match_method=match_method,
            confidence=match.get("score", 0),
            llm_reasoning=match.get("llm_reasoning"),
            md5_hash=self._md5(pdf_path) if pdf_path.exists() else None,
        )

        return {
            "status": "pending_confirmation",
            "filename": pdf_path.name,
            "original_path": str(pdf_path),
            "suggested_project_code": match.get("code"),
            "suggested_project_name": match.get("name"),
            "suggested_project_full_name": match.get("full_name", ""),
            "confidence": match.get("score", 0),
            "match_method": match_method,
            "llm_reasoning": match.get("llm_reasoning", ""),
        }

    def _record_no_match(
        self, pdf_path: Path, run_id: int
    ) -> dict:
        """Record a file with zero confidence — file stays in root."""
        from shared_tools.invoice_allocation.invoice_allocation_db import (
            record_no_match,
        )
        record_no_match(
            run_id=run_id,
            filename=pdf_path.name,
            original_path=str(pdf_path),
        )

        return {
            "status": "no_match",
            "filename": pdf_path.name,
            "original_path": str(pdf_path),
        }

    def confirm_move(
        self, original_path: str, project_code: str, projects: list[dict]
    ) -> dict | None:
        """Move a pending-confirmation file to the chosen project folder.

        Called after user confirms the suggested move.
        Returns the move result dict, or None if the file/project not found.
        """
        pdf_path = Path(original_path)
        if not pdf_path.exists():
            return None

        # Find the project
        target = next((p for p in projects if p["code"] == project_code), None)
        if not target:
            return None

        # Find the pending DB record to update
        from shared_tools.invoice_allocation.invoice_allocation_db import (
            get_history,
            confirm_pending_move,
        )
        records = get_history(limit=50)
        pending_record = None
        for r in records:
            if (r.get("filename") == pdf_path.name and
                r.get("status") == "pending_confirmation"):
                pending_record = r
                break

        # Execute the move
        target_dir = target["path"]
        target_path, is_dup = self._check_duplicate(pdf_path, target_dir)

        if is_dup:
            return {
                "status": "skipped",
                "reason": f"Duplicate in {target_dir.name}",
                "filename": pdf_path.name,
            }

        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(pdf_path), str(target_path))

        # Update DB record
        if pending_record:
            confirm_pending_move(
                record_id=pending_record["id"],
                moved_to_path=str(target_path),
                file_size_bytes=target_path.stat().st_size if target_path.exists() else 0,
            )

        return {
            "status": "confirmed",
            "filename": pdf_path.name,
            "target_project": target.get("full_name", ""),
            "moved_to": str(target_path),
        }

    def _combine_matches(
        self,
        fname_match: dict | None,
        text_match: dict | None,
    ) -> dict | None:
        """Combine filename and text match results.

        When both phases point to the same project, boost the confidence
        score to reflect corroborating evidence from independent sources.

        When they point to different projects, prefer text match (content
        is more reliable than filename).
        """
        if not fname_match and not text_match:
            return None
        if fname_match and not text_match:
            return fname_match
        if text_match and not fname_match:
            return text_match

        # Both exist — check if they agree
        fname_code = fname_match.get("code")
        text_code = text_match.get("code")

        if fname_code == text_code:
            # Same project: compound the scores
            compound = min(1.0, fname_match["score"] + text_match["score"] * 0.6)
            return {
                **text_match,
                "score": compound,
            }
        else:
            # Different projects: prefer text (content > filename)
            return text_match

    # ── Phase 1: Filename Matching ────────────────────────────────────

    def _match_by_filename(
        self, filename: str, projects: list[dict]
    ) -> dict | None:
        """Tokenize filename and match against project codes and names.

        Scoring:
          - Exact 4-digit code in filename → 0.9
          - Project name token (≥3 chars) in filename → 0.8
          - Multiple indicators compound up to 1.0
        """
        name_lower = filename.lower()
        best: dict | None = None
        best_score = 0.0

        for p in projects:
            score = 0.0
            # Code match
            if p["code"] in name_lower:
                score += 0.9
            # Name token match
            name_tokens = _tokenize(p["name"])
            for token in name_tokens:
                if len(token) >= 3 and token in name_lower:
                    score += 0.5
                    break  # one name token match is enough
            score = min(score, 1.0)

            if score > best_score:
                best_score = score
                best = {**p, "score": score}

        return best if best_score > 0 else None

    # ── Phase 2: Text-based Matching ──────────────────────────────────

    def _match_by_text(
        self, text: str, projects: list[dict]
    ) -> dict | None:
        """Search extracted PDF text for project codes and names.

        Uses token-based matching for project names (like filename matching)
        to handle partial name matches (e.g., "Econolodge" matches project
        "Econolodge - MAY DONE", or "ARCO" matches "ARCO").

        Scoring:
          - Code in text → 0.6
          - Name token (≥3 chars) in text → 0.5
          - Both → 1.0
        """
        if not text:
            return None

        text_lower = text.lower()
        best: dict | None = None
        best_score = 0.0

        for p in projects:
            score = 0.0
            # Code found in text (exact 4-digit match)
            if p["code"] in text_lower:
                score += 0.6
            # Name token found in text
            name_tokens = _tokenize(p["name"])
            name_matched = False
            for token in name_tokens:
                if len(token) >= 3 and token in text_lower:
                    name_matched = True
                    break
            if name_matched:
                score += 0.5
            score = min(score, 1.0)

            if score > best_score:
                best_score = score
                best = {**p, "score": score}

        return best if best_score > 0 else None

    # ── PDF Text Extraction ───────────────────────────────────────────

    def _extract_pdf_text(
        self, pdf_path: Path, max_pages: int = 5
    ) -> str | None:
        """Extract text from PDF using pdfplumber, with PyMuPDF fallback.

        Returns None if no text could be extracted (scanned PDF).
        """
        # Primary: pdfplumber (better text extraction for structured PDFs)
        try:
            import pdfplumber
            texts = []
            with pdfplumber.open(str(pdf_path)) as pdf:
                for i, page in enumerate(pdf.pages):
                    if i >= max_pages:
                        break
                    t = page.extract_text()
                    if t:
                        texts.append(t)
            result = "\n".join(texts).strip()
            if result:
                return result
        except Exception:
            pass

        # Fallback: PyMuPDF (fitz) for corrupted or encrypted PDFs
        try:
            import fitz
            texts = []
            doc = fitz.open(str(pdf_path))
            for i in range(min(doc.page_count, max_pages)):
                texts.append(doc[i].get_text())
            doc.close()
            result = "\n".join(texts).strip()
            if result:
                return result
        except Exception:
            pass

        return None

    # ── Phase 3: LLM Fallback ─────────────────────────────────────────

    def _classify_with_llm(
        self, text: str, filename: str, projects: list[dict]
    ) -> dict | None:
        """Use LLM to classify which project this invoice belongs to.

        Serialized via _llm_semaphore. Returns None on failure.
        """
        with self._llm_semaphore:
            from shared_tools.core.llm_config import get_llm

            project_list = "\n".join(
                f'  - Code {p["code"]}: {p["name"]} (folder: "{p["full_name"]}")'
                for p in projects
            )

            text_snippet = (text or "")[:2000]

            prompt = (
                "You are an invoice allocation assistant for a construction company.\n"
                "Given the invoice filename and its content below, determine which "
                "project this invoice belongs to.\n\n"
                "Known projects:\n"
                f"{project_list}\n\n"
                f"Invoice filename: {filename}\n\n"
                f"Invoice content (first 2000 chars):\n{text_snippet}\n\n"
                "Return ONLY a JSON object (no other text):\n"
                "{\n"
                '  "project_code": "4-digit code or null",\n'
                '  "project_name": "full project name or null",\n'
                '  "confidence": 0.0-1.0,\n'
                '  "reasoning": "brief explanation"\n'
                "}"
            )

            try:
                llm = get_llm("fast")
                raw = llm.call(prompt)
                response_text = raw if isinstance(raw, str) else str(raw)

                # Parse JSON from response (handle markdown code blocks)
                parsed = self._parse_llm_json(response_text)
                if not parsed:
                    return None

                code = str(parsed.get("project_code", "")).strip()
                name = str(parsed.get("project_name", "")).strip()
                llm_confidence = float(parsed.get("confidence", 0))
                reasoning = str(parsed.get("reasoning", ""))

                # Find matching project
                for p in projects:
                    if p["code"] == code or p["name"].lower() == name.lower():
                        return {
                            **p,
                            "score": llm_confidence,
                            "llm_reasoning": reasoning,
                        }

                # Try fuzzy match by name
                if name:
                    match = _match_project(name, projects)
                    if match:
                        return {
                            **match,
                            "score": max(llm_confidence, match.get("score", 0.5)),
                            "llm_reasoning": reasoning,
                        }

                return None

            except Exception:
                return None

    def _parse_llm_json(self, text: str) -> dict | None:
        """Extract JSON object from LLM response.

        Handles: raw JSON, markdown code blocks, and trailing text.
        Pattern from variation_agent.py.
        """
        if not text:
            return None

        # Try direct parse first
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass

        # Try extracting from ```json ... ``` block
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass

        # Try finding any {...} block
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass

        return None

    # ── File Move Operations ──────────────────────────────────────────

    def _execute_move(
        self,
        pdf_path: Path,
        match: dict,
        match_method: str,
        run_id: int,
    ) -> dict:
        """Move file to matched project folder with duplicate detection."""
        target_dir = match["path"]
        target_path, is_dup = self._check_duplicate(pdf_path, target_dir)

        if is_dup:
            self._record_skip(
                run_id, pdf_path,
                f"Duplicate — already in {target_dir.name}",
            )
            return {
                "status": "skipped",
                "reason": f"Duplicate in {target_dir.name}",
                "filename": pdf_path.name,
            }

        # Move the file
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(pdf_path), str(target_path))

        # Record in DB
        from shared_tools.invoice_allocation.invoice_allocation_db import (
            record_allocation,
        )
        record_allocation(
            run_id=run_id,
            filename=pdf_path.name,
            original_path=str(pdf_path),
            moved_to_path=str(target_path),
            target_project_code=match.get("code"),
            target_project_name=match.get("name"),
            match_method=match_method,
            confidence=match.get("score", 0),
            status="moved",
            llm_reasoning=match.get("llm_reasoning"),
            md5_hash=self._md5(target_path),
            file_size_bytes=target_path.stat().st_size if target_path.exists() else 0,
        )

        return {
            "status": "moved",
            "target_project": match.get("full_name", ""),
            "confidence": match.get("score", 0),
            "match_method": match_method,
            "filename": pdf_path.name,
        }

    # ── Duplicate Detection ───────────────────────────────────────────

    def _check_duplicate(
        self, pdf_path: Path, target_dir: Path
    ) -> tuple[Path, bool]:
        """Check if file already exists at target.

        Returns (target_path, is_duplicate).
          - is_duplicate=True: exact same file (by MD5) — skip
          - is_duplicate=False: new or same-name-different-content
        """
        target_path = target_dir / pdf_path.name

        if not target_path.exists():
            return target_path, False

        # Hash comparison for exact duplicate detection
        try:
            source_hash = self._md5(pdf_path)
            target_hash = self._md5(target_path)
            if source_hash == target_hash:
                return target_path, True  # exact duplicate
        except Exception:
            pass

        # Different content, same name — append counter
        stem = pdf_path.stem
        suffix = pdf_path.suffix
        counter = 1
        while target_path.exists():
            target_path = target_dir / f"{stem} ({counter}){suffix}"
            counter += 1

        return target_path, False

    @staticmethod
    def _md5(path: Path) -> str:
        """Compute MD5 hash of a file."""
        hasher = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    # ── DB Record Helpers ─────────────────────────────────────────────

    def _record_error(
        self, run_id: int, pdf_path: Path, error_message: str
    ) -> None:
        """Record a processing error in the DB."""
        try:
            from shared_tools.invoice_allocation.invoice_allocation_db import (
                record_error,
            )
            record_error(
                run_id=run_id,
                filename=pdf_path.name,
                original_path=str(pdf_path),
                error_message=error_message,
                md5_hash=self._md5(pdf_path) if pdf_path.exists() else None,
            )
        except Exception:
            pass

    def _record_skip(
        self, run_id: int, pdf_path: Path, reason: str
    ) -> None:
        """Record a skipped file in the DB."""
        try:
            from shared_tools.invoice_allocation.invoice_allocation_db import (
                record_skip,
            )
            record_skip(
                run_id=run_id,
                filename=pdf_path.name,
                original_path=str(pdf_path),
                reason=reason,
            )
        except Exception:
            pass


# =============================================================================
# Singleton accessor
# =============================================================================

_invoice_allocation_service: InvoiceAllocationService | None = None


def get_invoice_allocation_service() -> InvoiceAllocationService:
    """Return the singleton InvoiceAllocationService instance."""
    global _invoice_allocation_service
    if _invoice_allocation_service is None:
        _invoice_allocation_service = InvoiceAllocationService()
        _invoice_allocation_service.start()
    return _invoice_allocation_service
