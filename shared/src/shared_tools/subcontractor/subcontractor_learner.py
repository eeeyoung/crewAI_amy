"""Subcontractor Learner — batch knowledge builder from project subcontract folders.

Scans a project's subcontractor directory structure and populates the global
subcontractor database with vendors, quotes, commitments, and knowledge:

  Vendors (70+, classified as supplier/subcontractor)
  Quotes (258, with line items extracted via Gemini Flash)
  Commitments (39 POs + 14 Subcontracts)
  Rate Benchmarks (per-trade market intelligence)
  Clause Library (standard subcontract terms from .docx files)
  Competitive Sets (which vendors bid against each other per trade)

Usage:
  uv run python -m shared_tools.subcontractor.subcontractor_learner \
      --project-dir "C:/crewAI/lilamy_test_project/5.17 Sub Contractors"

Or call from Python:
  learner = SubcontractorLearner(project_dir="...", project_name="ARCO")
  learner.run()
"""

from __future__ import annotations

import json
import os
import re
import statistics
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
load_dotenv()


# =============================================================================
# Trade → Vendor Type Classification Map
# =============================================================================

# Trades that ALWAYS involve on-site labor/installation → subcontractor
SUBCONTRACTOR_TRADES: set[str] = {
    "concrete", "concrete & formwork", "formwork",
    "earthworks", "earth & siteworks", "5.17.3 earth & siteworks",
    "piling",
    "hydraulics", "plumbing",
    "electrical",
    "mechanical", "hvac",
    "glazing", "windows & glazing",
    "joinery", "carpentry",
    "masonry", "brickwork", "blockwork",
    "plastering", "gyproc", "drywall",
    "painting", "decorating",
    "tiling",
    "waterproofing",
    "flooring",
    "roofing",
    "structural steel", "metalwork",
    "landscaping",
    "external rendering", "external acratex", "acratex",
    "hebel", "cladding",
    "fire", "wet fire", "dry fire", "fire services",
    "security",
    "lifts", "vertical transportation", "elevators",
    "concrete tilt-up panels", "precast", "pre cast",
    "mastic sealant", "sealant",
    "roof safety system",
    "scaffold & hoist",  # on-site installation work
}

# Trades that are materials/equipment supply only → supplier
SUPPLIER_TRADES: set[str] = {
    "doors", "door supply", "doors supply",
    "door hardware",
    "windows",  # supply only (no install)
    "ffe", "furniture",
    "modular buildings", "supply of modular buildings",
    "crane", "crane hire", "crane operator",
    "surveying", "surveyor",
    "traffic management",
    "equipment hire", "site hiring",
    "scaffolding",  # equipment hire
    "chemical toilet",
    "site amenities",
    "site camera", "security system supply",
    "work shirts", "uniforms",
    "authority fees", "permits",
    "preliminaries",
    "car stackers",
    "materials supply", "wall supply",
    "cabin relocation",
    "vibration monitor",
    "penetrometer test", "testing",
}

# Normalized trade names (folder name → canonical trade name)
TRADE_NORMALIZATION: dict[str, str] = {
    # ── Subcontractor trades (on-site labor) ──
    "5.17.3 earth & siteworks": "Earthworks",
    "earth & siteworks": "Earthworks",
    "earthworks": "Earthworks",
    "concrete & formwork": "Concrete & Formwork",
    "concrete": "Concrete & Formwork",
    "formwork": "Concrete & Formwork",
    "brickwork": "Masonry",
    "masonry": "Masonry",
    "hydraulics": "Hydraulics",
    "plumbing": "Hydraulics",
    "electrical": "Electrical",
    "electrical initial po": "Electrical",
    "electrical services": "Electrical",
    "mechanical": "Mechanical",
    "hvac": "Mechanical",
    "glazing": "Glazing",
    "windows & glazing": "Glazing",
    "joinery": "Joinery",
    "carpentry": "Joinery",
    "c&p cladding & joinery": "Joinery",
    "plastering": "Plastering",
    "gyproc": "Plastering",
    "drywall": "Plastering",
    "painting": "Painting",
    "decorating": "Painting",
    "tiling": "Tiling",
    "waterproofing": "Waterproofing",
    "flooring": "Flooring",
    "piling": "Piling",
    "piled wall cages": "Piling",
    "precast": "Precast Panels",
    "pre cast": "Precast Panels",
    "concrete tilt-up panels": "Precast Panels",
    "concrete tilt": "Precast Panels",
    "atlas precast panels": "Precast Panels",
    "lifts": "Vertical Transportation",
    "vertical transportation": "Vertical Transportation",
    "elevators": "Vertical Transportation",
    "fire": "Fire Services",
    "wet fire": "Fire Services",
    "dry fire": "Fire Services",
    "fire services": "Fire Services",
    "security": "Security & Dry Fire",
    "security & dry fire": "Security & Dry Fire",
    "external acratex": "External Rendering",
    "acratex": "External Rendering",
    "external rendering": "External Rendering",
    "hebel": "Cladding",
    "cladding": "Cladding",
    "structural steel": "Metalwork",
    "metalwork": "Metalwork",
    "landscaping": "Landscaping",
    "scaffold & hoist": "Scaffolding",
    "scaffolding": "Scaffolding",
    "stormwater": "Stormwater",
    "civil stromwater": "Stormwater",
    "stormwater drainage": "Stormwater",
    "roof safety system": "Roof Safety",
    "mastic sealant": "Sealant",
    "sealant": "Sealant",
    "panel rigging": "Rigging",
    "grout block": "Grouting",
    "site power": "Electrical",
    # ── Supplier trades (materials/equipment supply) ──
    "crane": "Crane Hire",
    "crane hire": "Crane Hire",
    "crane operator hiring": "Crane Hire",
    "crane, rigging & bracing": "Crane Hire",
    "site hiring": "Equipment Hire",
    "site hiring - crane": "Crane Hire",
    "doors": "Doors",
    "door supply": "Doors",
    "doors supply": "Doors",
    "doors and fire door frames supply": "Doors",
    "door hardware": "Door Hardware",
    "windows": "Windows",
    "ffe": "FFE",
    "furniture": "FFE",
    "surveying": "Surveying",
    "surveyor": "Surveying",
    "traffic management": "Traffic Management",
    "traffic management service for enabling works": "Traffic Management",
    "preliminaries": "Preliminaries",
    "car stackers": "Car Stackers",
    "supply of modular buildings": "Modular Buildings",
    "modular buildings": "Modular Buildings",
    "tss wall supply": "Wall Supply",
    "tss walls": "Wall Supply",
    "tss walls - main works upper floors initial po": "Wall Supply",
    "chemical toilet": "Site Amenities",
    "site amenities": "Site Amenities",
    "site camera": "Security Equipment",
    "work shirts": "Uniforms",
    "authority fees": "Permits & Fees",
    "obstruction permit": "Permits & Fees",
    "vabration monitor": "Monitoring Equipment",
    "vibration monitor": "Monitoring Equipment",
    "cabin relocation": "Logistics",
    "hoarding": "Site Setup",
    "fortawall water barrier": "Site Setup",
    "bunnings orders": "Materials Supply",
    "three phase underground pole installation": "Electrical Infrastructure",
    "penetrometer test": "Testing Services",
    "psp testing foundation engineering": "Testing Services",
    "asphalt driveway": "Civil Works",
    "basement fire door frame": "Doors",
    "basement lintel 1": "Metalwork",
}


def _normalize_trade(raw: str) -> str:
    """Normalize a trade name to its canonical form."""
    cleaned = raw.strip().lower()
    return TRADE_NORMALIZATION.get(cleaned, raw.strip())


def _classify_vendor_type(vendor_name: str, trade: str,
                          has_subcontract: bool = False) -> tuple[str, str]:
    """Classify a vendor as supplier or subcontractor.

    Returns (vendor_type, confidence).
    """
    trade_lower = trade.lower().strip()

    # Rule 1: If vendor has a formal subcontract → definitely subcontractor
    if has_subcontract:
        return ("subcontractor", "high")

    # Rule 2: Trade-based classification
    for st in SUBCONTRACTOR_TRADES:
        if st in trade_lower:
            return ("subcontractor", "high")

    for st in SUPPLIER_TRADES:
        if st in trade_lower:
            return ("supplier", "high")

    # Rule 3: Keyword heuristics on vendor name
    vendor_lower = vendor_name.lower()
    supplier_keywords = ["supply", "hire", "bunnings", "authority", "camera"]
    for kw in supplier_keywords:
        if kw in vendor_lower:
            return ("supplier", "medium")

    # Default: assume subcontractor (most construction trades involve labor)
    return ("subcontractor", "medium")


def _normalize_vendor_name(name: str) -> str:
    """Clean a vendor name for matching."""
    name = name.strip()
    # Remove common suffixes/prefixes
    name = re.sub(r'\s*\(.*?\)\s*', ' ', name)
    name = re.sub(r'\s*-\s*(Cancelled|sent|Executed|Initial PO|Main Works).*$', '', name, flags=re.I)
    name = re.sub(r'\s+', ' ', name)
    name = name.strip()
    # Cap at reasonable length — anything over 60 chars is likely a filename, not a vendor
    if len(name) > 60:
        # Take first reasonable-looking word group
        words = name.split()
        for i in range(len(words), 0, -1):
            candidate = ' '.join(words[:i])
            if len(candidate) <= 60:
                name = candidate
                break
    return name


def _vendor_name_key(name: str) -> str:
    """Generate a matching key for vendor name deduplication."""
    n = _normalize_vendor_name(name).lower()
    # Remove Pty Ltd, Pty, Ltd, Limited, PL, etc.
    n = re.sub(r'\b(pty|ltd|limited|pl|co|company|corporation|corp)\b\.?', '', n, flags=re.I)
    # Remove ABN/ACN numbers
    n = re.sub(r'\b\d{2}\s*\d{3}\s*\d{3}\s*\d{3}\b', '', n)
    n = re.sub(r'\b\d{9,11}\b', '', n)
    n = re.sub(r'\s+', ' ', n)
    return n.strip()


# =============================================================================
# PDF Extraction via Gemini Flash
# =============================================================================

def _get_gemini_client():
    """Return a Gemini client (google.genai) for quote extraction."""
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set in .env")

    return genai.Client(api_key=api_key)


QUOTE_EXTRACTION_PROMPT = """You are a construction cost analyst. Extract structured pricing data from this quote document.

Return a JSON object with these fields (use null for unknown values):
{
  "vendor_name": "company name of the quoting vendor",
  "quote_ref": "reference number on the quote (e.g., Q-2025-001)",
  "date": "quote date in YYYY-MM-DD format",
  "trade_name": "construction trade category",
  "scope_summary": "1-2 sentence summary of what the quote covers (max 150 chars)",
  "total_ex_gst": number (total before GST),
  "total_incl_gst": number (total including GST if stated),
  "line_items": [
    {
      "item_number": 1,
      "description": "brief phrase, max 10 words",
      "qty": number,
      "unit": "item/LS/m2/m3/hr/m",
      "rate": number
    }
  ],
  "notes": "any special conditions, exclusions, or payment terms noted"
}

IMPORTANT:
- Extract TOP-LEVEL items only (A-level), not sub-items without individual prices
- Keep descriptions SHORT (max 10 words)
- If only a lump sum is given, use qty=1, unit="LS", rate=<total>
- Rates must be numbers, not strings
- If the document is NOT actually a quote (e.g., a drawing, spec, or cover letter), return {"not_a_quote": true}"""


def _extract_quote_pdf(pdf_path: str, log=None) -> dict | None:
    """Extract structured data from a quote PDF using Gemini Flash vision.

    Returns the parsed JSON dict, or None on failure.
    """
    import fitz  # PyMuPDF

    client = _get_gemini_client()

    # ── Render PDF pages as images ──────────────────────────────────
    images = []
    doc = None
    total_pages = 0
    try:
        doc = fitz.open(pdf_path)
        total_pages = min(3, len(doc))
        for page_num in range(total_pages):
            try:
                page = doc[page_num]
                pix = page.get_pixmap(dpi=120)
                images.append(pix.tobytes("png"))
            except Exception as page_err:
                if log:
                    log(f"Page {page_num} render failed: {page_err}", "WARN")
                continue
    except Exception as e:
        if log:
            log(f"PDF open/render failed: {e}", "ERROR")
        return None
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass

    if not images:
        return None

    if log:
        log(f"Rendered {len(images)}/{total_pages} pages", "RESULT")

    # ── Call Gemini Flash ───────────────────────────────────────────
    # Build contents in google.genai format (uses inline_data wrapper)
    contents = [QUOTE_EXTRACTION_PROMPT]
    for img_bytes in images:
        contents.append({
            "inline_data": {
                "mime_type": "image/png",
                "data": img_bytes,
            }
        })

    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash",
            contents=contents,
            config={
                "temperature": 0,
                "top_p": 1.0,
                "response_mime_type": "application/json",
            },
        )
        raw = response.text.strip()
        result = _parse_json(raw)
        if result.get("not_a_quote"):
            if log:
                log("Gemini returned not_a_quote=true", "WARN")
            return None
        if result.get("parse_error"):
            if log:
                log(f"JSON parse failed, raw: {raw[:200]}", "WARN")
            return None
        # Check we got meaningful data
        if not result.get("vendor_name") and not result.get("line_items"):
            if log:
                log(f"No vendor_name or line_items in result: {str(result)[:200]}", "WARN")
            return None
        return result
    except Exception as e:
        if log:
            log(f"Gemini API error: {e}", "ERROR")
        return None


def _parse_json(text: str) -> dict:
    """Extract JSON object from LLM output, handling markdown fences."""
    text = text.strip()
    fence = re.compile(r'^```(\w+)?\s*\n')
    m = fence.match(text)
    if m:
        text = text[m.end():]
        if text.endswith("\n```"):
            text = text[:-4]
        elif text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return {}


# =============================================================================
# Clause Extraction from .docx
# =============================================================================

def _extract_clauses_from_docx(docx_path: str) -> list[dict]:
    """Extract numbered clauses from a subcontract .docx file.

    Uses paragraph styles (Word list numbering) to detect clause boundaries,
    since Welink subcontract .docx files use Word's built-in list styles
    rather than plain-text numbering like "1. GENERAL".

    Returns list of {clause_number, clause_title, clause_text} dicts.
    """
    try:
        from docx import Document
        doc = Document(docx_path)

        # Strategy: find all "Level 1" list paragraphs — these are clause titles.
        # Everything between two Level 1 titles is the body of the first clause.
        # Level 2/3 paragraphs are sub-clauses within the body.

        # First pass: collect paragraphs with style info
        entries = []
        for para in doc.paragraphs:
            text = para.text.strip()
            if not text:
                continue
            style_name = para.style.name if para.style else ""
            entries.append({
                "text": text,
                "style": style_name,
                "is_level1": "Level 1" in style_name and "Level 2" not in style_name and "Level 3" not in style_name,
            })

        if not entries:
            return []

        # Find clause boundaries: Level 1 paragraphs that look like clause titles
        # (not body text that happens to be Level 1)
        clause_starts = []
        for i, entry in enumerate(entries):
            if entry["is_level1"]:
                # Heuristic: clause titles are short (typically <120 chars) and Title Case
                text = entry["text"]
                if len(text) < 150 and text[0].isupper():
                    clause_starts.append(i)

        if len(clause_starts) < 2:
            # Fallback: try regex on combined text
            return _extract_clauses_regex_fallback(entries)

        clauses = []
        clause_num = 0
        for j, start_idx in enumerate(clause_starts):
            clause_num += 1
            title = entries[start_idx]["text"]
            end_idx = clause_starts[j + 1] if j + 1 < len(clause_starts) else len(entries)

            # Collect body text between this title and the next
            body_parts = []
            for k in range(start_idx + 1, end_idx):
                body_parts.append(entries[k]["text"])

            body = "\n".join(body_parts)
            if len(body) > 4000:
                body = body[:4000] + "..."

            clauses.append({
                "clause_number": str(clause_num),
                "clause_title": title,
                "clause_text": body,
            })

        return clauses
    except Exception:
        return []


def _extract_clauses_regex_fallback(entries: list[dict]) -> list[dict]:
    """Fallback: try regex on combined text when style-based extraction fails."""
    combined = "\n".join(e["text"] for e in entries)

    # Try "X.  TITLE" pattern
    clause_pattern = re.compile(
        r'(?:^|\n)(\d+\.?\s+)([A-Z][A-Za-z\s/&()-]{2,80})(?:\n|$)',
        re.MULTILINE,
    )
    matches = list(clause_pattern.finditer(combined))
    if not matches:
        return []

    clauses = []
    for i, m in enumerate(matches):
        clause_number = m.group(1).strip().rstrip('.')
        clause_title = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(combined)
        clause_text = combined[start:end].strip()
        if len(clause_text) > 4000:
            clause_text = clause_text[:4000] + "..."

        clauses.append({
            "clause_number": clause_number,
            "clause_title": clause_title,
            "clause_text": clause_text,
        })

    return clauses


# =============================================================================
# SubcontractorLearner
# =============================================================================

class SubcontractorLearner:
    """Batch knowledge builder that scans a project subcontractor folder.

    Usage:
        learner = SubcontractorLearner(
            project_dir="C:/crewAI/lilamy_test_project/5.17 Sub Contractors",
            project_name="ARCO",
            project_job_number="22-24 Hood Street",
            project_location="Subiaco WA 6008",
        )
        learner.run()
    """

    def __init__(
        self,
        project_dir: str,
        project_name: str = "ARCO",
        project_job_number: str = "",
        project_location: str = "",
        project_head_contract_sum: float = 18_450_000,
    ):
        self._project_dir = Path(project_dir)
        self._project_name = project_name
        self._project_job_number = project_job_number
        self._project_location = project_location
        self._project_head_contract_sum = project_head_contract_sum
        self._project_entry_id = ""

        # Internal state
        self._start_time: datetime | None = None
        self._vendor_map: dict[str, dict] = {}        # key → vendor dict
        self._vendors_by_name: dict[str, str] = {}     # normalized name → entry_id
        self._po_folders: list[dict] = []
        self._subcontract_folders: list[dict] = []
        self._quote_vendors: dict[str, list[dict]] = {}  # trade → [vendor dict]
        self._awarded_vendor_keys: set[str] = set()
        self._gemini_model = None

    # ── Logging ────────────────────────────────────────────────────────

    @staticmethod
    def _log(msg: str, level: str = "INFO") -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        prefix = {
            "INFO": "  ", "PHASE": "▶ ", "OK": "  ✅", "WARN": "  ⚠️ ",
            "ERROR": "  ❌", "STEP": "    ", "RESULT": "     → ",
        }.get(level, "  ")
        print(f"[{ts}] {prefix} {msg}", flush=True)

    @staticmethod
    def _separator() -> None:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {'─' * 62}", flush=True)

    # ── Entry Point ────────────────────────────────────────────────────

    def run(self) -> dict:
        """Run all learning phases. Returns summary dict."""
        self._start_time = datetime.now()
        self._log("SUBCONTRACTOR LEARNER STARTING", "PHASE")
        self._log(f"Project directory: {self._project_dir}", "INFO")
        self._separator()

        try:
            self._phase_1_scan()
            self._phase_2_parse_folders()
            self._phase_3_classify_vendors()
            self._phase_4_extract_awarded_quotes()
            self._phase_5_extract_sample_unawarded()
            self._phase_6_populate_database()
            self._phase_7_build_knowledge_base()
            self._print_summary()
            return self._build_summary_dict()
        except Exception as e:
            self._log(f"LEARNER FAILED: {e}", "ERROR")
            import traceback
            traceback.print_exc()
            raise

    # ── Phase 1: Scan ──────────────────────────────────────────────────

    def _phase_1_scan(self) -> None:
        self._log("Phase 1: Scanning project folder...", "PHASE")

        base = self._project_dir
        if not base.exists():
            raise FileNotFoundError(f"Project directory not found: {base}")

        # Count files in each main directory
        for label, subdir in [
            ("Purchase Orders", "1. Purchase Orders"),
            ("Subcontracts", "2. Subcontracts"),
            ("Quotes", "3. Quotes"),
            ("Claims", "4. Claims"),
        ]:
            d = base / subdir
            if d.exists():
                file_count = sum(1 for _ in d.rglob("*") if _.is_file())
                self._log(f"  {label}: {file_count} files", "INFO")
            else:
                self._log(f"  {label}: NOT FOUND", "WARN")

        total = sum(1 for _ in base.rglob("*") if _.is_file())
        self._log(f"  Total files indexed: {total}", "OK")

    # ── Phase 2: Parse Folder Names ────────────────────────────────────

    def _phase_2_parse_folders(self) -> None:
        self._log("Phase 2: Parsing folder names for vendors, trades, references...", "PHASE")
        base = self._project_dir

        # ── Parse PO folders ───────────────────────────────────────────
        po_dir = base / "1. Purchase Orders"
        if po_dir.exists():
            for folder in sorted(po_dir.iterdir()):
                if not folder.is_dir():
                    continue
                info = self._parse_po_folder(folder.name)
                if info:
                    self._po_folders.append(info)
                    key = _vendor_name_key(info["vendor_name"])
                    self._awarded_vendor_keys.add(key)
            self._log(f"  Parsed {len(self._po_folders)} Purchase Order folders", "INFO")

        # ── Parse Subcontract folders ──────────────────────────────────
        sub_dir = base / "2. Subcontracts"
        if sub_dir.exists():
            for folder in sorted(sub_dir.iterdir()):
                if not folder.is_dir():
                    continue
                info = self._parse_subcontract_folder(folder)
                if info:
                    self._subcontract_folders.append(info)
                    key = _vendor_name_key(info["vendor_name"])
                    self._awarded_vendor_keys.add(key)
            self._log(f"  Parsed {len(self._subcontract_folders)} Subcontract folders", "INFO")

        # ── Parse Quote folders ────────────────────────────────────────
        quote_dir = base / "3. Quotes"
        if quote_dir.exists():
            trade_count = 0
            vendor_count = 0
            for trade_folder in sorted(quote_dir.iterdir()):
                if not trade_folder.is_dir():
                    continue
                trade_name = _normalize_trade(trade_folder.name)
                trade_count += 1
                self._quote_vendors.setdefault(trade_name, [])

                # Vendor sub-folders
                for item in sorted(trade_folder.iterdir()):
                    if item.is_dir():
                        vendor_name = _normalize_vendor_name(item.name)
                        pdfs = list(item.glob("*.pdf"))
                        self._quote_vendors[trade_name].append({
                            "vendor_name": vendor_name,
                            "folder_path": str(item),
                            "pdfs": [str(p) for p in pdfs],
                        })
                        vendor_count += 1
                    elif item.suffix.lower() == ".pdf":
                        # Individual quote PDF — try to extract vendor from filename
                        vendor_name = self._guess_vendor_from_filename(item.stem)
                        if vendor_name:
                            self._quote_vendors[trade_name].append({
                                "vendor_name": vendor_name,
                                "folder_path": str(trade_folder),
                                "pdfs": [str(item)],
                            })
                            vendor_count += 1

            self._log(f"  Parsed {trade_count} trade folders, {vendor_count} vendor quotes", "INFO")

        # ── Claims (skip, per user instruction) ─────────────────────────
        self._log("  Claims: skipped (deferred per Phase 5 plan)", "INFO")

    def _parse_po_folder(self, folder_name: str) -> dict | None:
        """Parse a PO folder name like 'PO17142 - Concrete - J Adamini'.

        Handles edge cases:
          - 2-part: "PO16983 - Bunnings Orders" (no vendor)
          - 3-part: "PO17142 - Concrete - J Adamini" (standard)
          - 4-part: "PO16808 - Surveyor - ST Spatial - Cancelled" (with status)
          - Missing spaces: "PO17114 -Piled wall cages - Geopractika"
        """
        # Normalize: ensure space after PO number, normalize dashes
        name = re.sub(r'(PO\d+)\s*-\s*', r'\1 - ', folder_name)
        name = name.replace(' -', ' - ').replace('- ', ' - ')
        name = re.sub(r'\s+', ' ', name).strip()

        # Remove known status suffixes
        status_suffixes = [
            ' - Cancelled', ' - sent', ' - Executed', ' - Initial PO',
            ' - Main Works Upper Floors Initial PO',
        ]
        for suffix in status_suffixes:
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break

        # Split on " - "
        parts = [p.strip() for p in name.split(' - ')]
        if len(parts) < 2:
            return None

        po_number = parts[0]
        if not re.match(r'PO\d+', po_number):
            return None

        if len(parts) == 2:
            # Only PO + description — try to guess vendor
            desc = parts[1]
            # Check if this looks like it contains a vendor name
            # e.g., "Bunnings Orders" → vendor="Bunnings"
            known_vendor_words = self._extract_vendor_from_text(desc)
            trade = desc
            vendor = known_vendor_words if known_vendor_words else desc
        elif len(parts) >= 3:
            trade = parts[1]
            vendor = parts[2]
            # If there's a 4th part that's not a status, it might be part of vendor name
            # e.g., "Site Hiring - Crane - Riverside Crane" → trade="Site Hiring - Crane", vendor="Riverside Crane"
            # Re-evaluate: if parts >= 3, and parts[2] looks like a sub-descriptor, merge with trade
            if len(parts) >= 4:
                # The last part is always the vendor
                vendor = parts[-1]
                trade = ' - '.join(parts[1:-1])
        else:
            return None

        return {
            "folder_name": folder_name,
            "reference_number": po_number,
            "trade": _normalize_trade(trade),
            "vendor_name": _normalize_vendor_name(vendor),
            "commitment_type": "purchase_order",
        }

    def _extract_vendor_from_text(self, text: str) -> str:
        """Try to extract a known vendor name from descriptive text using heuristics."""
        text_lower = text.lower()
        # Common vendor name patterns to extract from descriptive text
        common = {
            "bunnings": "Bunnings",
            "bushby": "Bushby Plumbing",
            "powerhouse": "Powerhouse",
            "adamini": "J Adamini",
            "atlas": "Atlas Precast",
            "geopractika": "Geopractika",
            "solwest": "Solwest",
            "concept": "Concept Windows",
            "schindler": "Schindler",
            "morgan": "Morgan",
            "westcoast": "West Coast Brick & Stone",
            "freo": "Freo Fire",
            "ideal": "Ideal Electrical",
            "obk": "OBK",
            "fairway": "Fairway Plumbing",
            "brajkovich": "Brajkovich",
            "cabletech": "Cabletech",
            "ascent": "Ascent Steel",
            "taborda": "Taborda",
            "monitel": "Monitel",
        }
        for keyword, full_name in common.items():
            if keyword in text_lower:
                return full_name
        return ""

    def _parse_subcontract_folder(self, folder: Path) -> dict | None:
        """Parse a subcontract folder name like 'S05 - Hydraulics - Bushby Plumbing'.

        Handles edge cases:
          - "S01- Piling - Geopractika" (dash after number without space)
          - "S04 - Atlas Precast Panels" (2-part, no separate vendor)
          - "S12 - Security & Dry Fire" (2-part, no separate vendor)
          - "S07 - Glazing - Concept Windows - sent" (with status suffix)
        """
        name = folder.name
        if name == "Joinery":
            # Top-level Joinery folder — not a subcontract package
            return None

        # Normalize spacing around dashes
        name = re.sub(r'(S\d{1,2})\s*[-–]\s*', r'\1 - ', name)
        name = name.replace(' -', ' - ').replace('- ', ' - ')
        name = re.sub(r'\s+', ' ', name).strip()

        # Remove known status suffixes
        for suffix in [' - sent', ' - Executed', ' - Do Not Use', ' - executed']:
            if suffix.lower() in name.lower():
                name = name[:name.lower().rindex(suffix.lower())]
                break

        # Skip "Do Not Use"
        if "do not use" in name.lower():
            self._log(f"  Skipping: {folder.name} (Do Not Use)", "WARN")
            return None

        parts = [p.strip() for p in name.split(' - ')]
        if len(parts) < 2:
            return None

        sub_number = parts[0]
        if not re.match(r'S\d{1,2}', sub_number):
            return None

        if len(parts) == 2:
            # Only S## + trade — no explicit vendor. Extract from trade name or folder contents.
            trade_desc = parts[1]
            # Try to find vendor in folder contents (PDF filenames)
            vendor = self._find_vendor_in_folder(folder, trade_desc)
            trade = _normalize_trade(trade_desc)
        else:
            trade = parts[1]
            vendor = parts[-1]  # Last part is always vendor
            trade = _normalize_trade(trade)

        return {
            "folder_name": folder.name,
            "reference_number": sub_number,
            "trade": _normalize_trade(trade) if len(parts) >= 3 else trade,
            "vendor_name": _normalize_vendor_name(vendor),
            "commitment_type": "subcontract",
            "folder_path": str(folder),
        }

    def _find_vendor_in_folder(self, folder: Path, trade_desc: str) -> str:
        """Try to find a vendor name from files inside a subcontract folder."""
        # Check PDF/docx filenames for vendor-like words
        for f in folder.rglob("*"):
            if f.suffix.lower() in (".pdf", ".docx"):
                # e.g., "Welink Subcontract_ARCO S04 - Atlas Precast Panels (003).docx"
                name = f.stem
                # Try to extract vendor from known patterns
                for pattern in [
                    r'(?:Subcontract|ARCO)\s*(?:S\d{1,2})?\s*[-–]\s*(.+?)(?:\s*\(\d+\))?$',
                    r'(?:ARCO|Subcontract).*?[-–]\s*(.+)$',
                ]:
                    m = re.search(pattern, name, re.I)
                    if m:
                        return m.group(1).strip()
        # Fallback: use the trade description as vendor hint
        return trade_desc

    def _guess_vendor_from_filename(self, filename: str) -> str | None:
        """Try to extract a vendor name from a quote PDF filename.

        Only returns a name if the filename strongly looks like a company name.
        Returns None for drawings, specs, cover letters, etc.
        """
        # Skip filenames that are clearly NOT vendor quotes
        skip_patterns = [
            r'\d{4,}',                          # Long numbers (dates, codes, job numbers)
            r'(drawing|draw|architect|plan|markup|take.off|boq|schedule)',
            r'(combined|updated|revised|revision|markup)',
            r'(hood.st|subiaco|apartment|storey)',
            r'REF\s*\d+',
            r'(specif|specific|acoustic|requirement|standard|detail)',
            r'(page|pages|sheet)\s*\d',
            r'(section|elevation|detail|plan)\s',
            r'^p\d+',                            # Page numbers
            r'^\d{2,4}[-_]\d',                   # Drawing numbers like "2216_ARCO"
            r'(final|welink|arco).*(combined|welink|final)',  # Generic filenames
        ]
        for pat in skip_patterns:
            if re.search(pat, filename, re.I):
                return None

        # Remove common prefixes
        cleaned = filename
        cleaned = re.sub(r'(Quote|Quotation|QT|Q-|Sales Order|SO)\s*[:\d]*\s*', '', cleaned, flags=re.I)
        cleaned = re.sub(r'\d{6,}', '', cleaned)
        cleaned = re.sub(r'[_\-\s]+', ' ', cleaned)
        cleaned = cleaned.strip()

        # Filter: must look like a company name (3-40 chars, at least one vowel)
        if 3 < len(cleaned) <= 40 and re.search(r'[aeiou]', cleaned, re.I):
            return cleaned
        return None

    # ── Phase 3: Classify Vendors ──────────────────────────────────────

    def _phase_3_classify_vendors(self) -> None:
        self._log("Phase 3: Building unified vendor list & classifying...", "PHASE")

        # Collect all vendor name variants
        name_variants: list[tuple[str, str, str, bool]] = []
        # (display_name, trade, source, has_subcontract)

        for po in self._po_folders:
            name_variants.append((
                po["vendor_name"], po["trade"], "purchase_order", False
            ))

        for sub in self._subcontract_folders:
            name_variants.append((
                sub["vendor_name"], sub["trade"], "subcontract", True
            ))

        for trade, vendors in self._quote_vendors.items():
            for v in vendors:
                name_variants.append((
                    v["vendor_name"], trade, "quote", False
                ))

        self._log(f"  Collected {len(name_variants)} vendor name variants", "INFO")

        # Group by normalized key
        groups: dict[str, list[dict]] = {}
        for display_name, trade, source, has_sub in name_variants:
            key = _vendor_name_key(display_name)
            if not key or len(key) < 2:
                continue
            if key not in groups:
                groups[key] = []
            groups[key].append({
                "display_name": display_name,
                "trade": trade,
                "source": source,
                "has_subcontract": has_sub,
            })

        self._log(f"  Deduplicated to {len(groups)} unique vendors", "INFO")

        # ── Fuzzy merge: merge groups where one key is substring of another ─
        merged = self._fuzzy_merge_groups(groups)
        if len(merged) < len(groups):
            self._log(f"  Fuzzy-merged to {len(merged)} vendors (combined {len(groups) - len(merged)} duplicates)", "INFO")

        # Classify each vendor
        sub_count = 0
        sup_count = 0
        for key, refs in merged.items():
            # Pick best display name (longest, prefer subcontract/PO source)
            best = max(refs, key=lambda r: (
                0 if r["source"] == "subcontract" else 1 if r["source"] == "purchase_order" else 2,
                len(r["display_name"]),
            ))
            has_sub = any(r["has_subcontract"] for r in refs)
            trade = best["trade"]
            display_name = best["display_name"]

            vendor_type, confidence = _classify_vendor_type(display_name, trade, has_sub)

            if vendor_type == "subcontractor":
                sub_count += 1
            else:
                sup_count += 1

            # Collect all trades this vendor is associated with
            all_trades = list(set(_normalize_trade(r["trade"]) for r in refs))

            # Deterministic entry_id: re-runs update the same vendor
            vendor_eid = f"vendor-{self._project_name}-{key}".replace(" ", "-")[:80]
            self._vendor_map[key] = {
                "entry_id": vendor_eid,
                "company_name": display_name,
                "vendor_type": vendor_type,
                "vendor_type_confidence": confidence,
                "trade_categories": json.dumps(all_trades),
                "has_subcontract": has_sub,
                "source": "learner",
                "learned_from_path": f"Project: {self._project_name}",
                "refs": refs,
            }
            self._vendors_by_name[key] = self._vendor_map[key]["entry_id"]

        # ── Filter: remove obvious non-vendor entries ──────────────────
        removed = 0
        for key in list(self._vendor_map.keys()):
            if self._is_likely_not_a_vendor(key, self._vendor_map[key]):
                del self._vendor_map[key]
                self._vendors_by_name.pop(key, None)
                removed += 1
        if removed:
            sub_count = sum(1 for v in self._vendor_map.values() if v["vendor_type"] == "subcontractor")
            sup_count = sum(1 for v in self._vendor_map.values() if v["vendor_type"] == "supplier")
            self._log(f"  Removed {removed} non-vendor entries (descriptions/filenames mistaken for vendors)", "INFO")

        self._log(f"  Classification: {sub_count} subcontractors, {sup_count} suppliers", "OK")

    @staticmethod
    def _is_likely_not_a_vendor(key: str, vendor: dict) -> bool:
        """Heuristic: does this look like a description, not a company name?"""
        name = vendor.get("company_name", "")
        # Skip vendors with valid sources (PO or subcontract)
        refs = vendor.get("refs", [])
        has_strong_source = any(r["source"] in ("purchase_order", "subcontract") for r in refs)
        if has_strong_source:
            return False
        # Only filter quote-only vendors that look suspicious
        if len(name) > 50:
            return True
        # Looks like a description, not a company
        desc_indicators = [
            r'^\d',                          # Starts with number
            r'(requirement|specif|standard|testing|test|inspection)',
            r'(final|welink|arco).*(combined|welink|final|rev)',
            r'^\d{2,4}[-_]\d',              # Drawing numbers
            r'(hood.st|subiaco|apartment|storey)',
            r'(obstruction|permit|fee|authority)',  # Authority items
            r'^psp\s',                       # PSP Testing etc.
            r'(daily basis|initial po|main works)',  # Descriptions
        ]
        for pat in desc_indicators:
            if re.search(pat, name, re.I):
                return True
        return False

    def _fuzzy_merge_groups(self, groups: dict[str, list[dict]]) -> dict[str, list[dict]]:
        """Merge vendor groups where one key is a significant substring of another.

        Example: "bushy" and "bushby plumbing" should merge into one vendor.
        """
        keys = list(groups.keys())
        merged_into: dict[str, str] = {}  # key → merge_target_key

        for i, key_a in enumerate(keys):
            if key_a in merged_into:
                continue
            for j, key_b in enumerate(keys):
                if i >= j or key_b in merged_into:
                    continue
                # Check if one key is a significant substring of the other
                if self._should_merge_vendors(key_a, key_b):
                    # Merge shorter into longer
                    if len(key_a) >= len(key_b):
                        merged_into[key_b] = key_a
                    else:
                        merged_into[key_a] = key_b
                        break  # key_a merged away, stop checking key_b

        # Build merged result
        result: dict[str, list[dict]] = {}
        for key, refs in groups.items():
            target = key
            while target in merged_into:
                target = merged_into[target]
            result.setdefault(target, []).extend(refs)

        return result

    @staticmethod
    def _should_merge_vendors(key_a: str, key_b: str) -> bool:
        """Determine if two vendor keys should be merged.

        Rules (conservative, designed for construction vendor names):
        1. One is substring of the other AND >=5 chars
        2. First 4 chars match AND shorter <=10 chars (catches "Bushy"/"Bushby Plumbing")
        3. Space-normalized forms match (catches "JAdamini"/"J Adamini")
        """
        a, b = key_a.strip(), key_b.strip()
        if not a or not b or a == b:
            return a == b
        if len(a) < len(b):
            shorter, longer = a, b
        else:
            shorter, longer = b, a
        # Rule 1: Substring (>=5 chars)
        if len(shorter) >= 5 and shorter in longer:
            return True
        # Rule 2: First 4 chars match + shorter is abbreviation (<=10 chars)
        if len(shorter) >= 4 and len(shorter) <= 10 and longer[:4] == shorter[:4]:
            return True
        # Rule 3: Space-normalized match
        if a.replace(' ', '') == b.replace(' ', ''):
            return True
        return False

    # ── Phase 4: Extract Awarded Quotes ────────────────────────────────

    def _phase_4_extract_awarded_quotes(self) -> None:
        self._log("Phase 4: Extracting AWARDED quotes (Gemini Flash)...", "PHASE")
        self._log("  Awarded = quotes found inside PO/subcontract folders + vendor matches", "INFO")

        awarded_pdfs: list[dict] = []
        base = self._project_dir

        # 1. Find quote PDFs inside PO folders
        for po in self._po_folders:
            po_path = base / "1. Purchase Orders" / po["folder_name"]
            if po_path.exists():
                for pdf in po_path.glob("*.pdf"):
                    fname = pdf.name.lower()
                    if "quote" in fname or "quotation" in fname:
                        awarded_pdfs.append({
                            "pdf_path": str(pdf),
                            "vendor_name": po["vendor_name"],
                            "trade": po["trade"],
                            "reference": po["reference_number"],
                            "source": "PO folder",
                        })

        # 2. Find quote PDFs in quote folders where vendor matches an awarded vendor
        for trade, vendors in self._quote_vendors.items():
            for v in vendors:
                key = _vendor_name_key(v["vendor_name"])
                if key in self._awarded_vendor_keys:
                    for pdf_path in v["pdfs"]:
                        # Skip if already included
                        if any(a["pdf_path"] == pdf_path for a in awarded_pdfs):
                            continue
                        awarded_pdfs.append({
                            "pdf_path": pdf_path,
                            "vendor_name": v["vendor_name"],
                            "trade": trade,
                            "reference": "",
                            "source": f"Quotes/{trade}",
                        })

        self._log(f"  Found {len(awarded_pdfs)} awarded quote PDFs to extract", "INFO")

        # Extract each
        self._extracted_quotes: list[dict] = []
        success = 0
        for i, aq in enumerate(awarded_pdfs):
            fname = Path(aq['pdf_path']).name
            self._log(f"  [{i+1}/{len(awarded_pdfs)}] {aq['vendor_name']} — {fname}", "STEP")
            result = _extract_quote_pdf(aq["pdf_path"], log=self._log)
            if result:
                result["_source"] = aq
                result["_is_awarded"] = True
                self._extracted_quotes.append(result)
                items = result.get("line_items", [])
                total = result.get("total_ex_gst") or result.get("total_incl_gst") or 0
                self._log(f"Extracted: {len(items)} line items, ${total:,.2f}", "RESULT")
                success += 1

        self._log(f"  Successfully extracted {success}/{len(awarded_pdfs)} awarded quotes", "OK")

    # ── Phase 5: Extract Sample Unawarded Quotes ───────────────────────

    def _phase_5_extract_sample_unawarded(self) -> None:
        self._log("Phase 5: Sampling UNAWARDED quotes (up to 2 per trade)...", "PHASE")

        base = self._project_dir / "3. Quotes"
        sampled_pdfs: list[dict] = []
        sample_count = 0

        for trade, vendors in sorted(self._quote_vendors.items()):
            # Find unawarded vendors for this trade
            unawarded = []
            for v in vendors:
                key = _vendor_name_key(v["vendor_name"])
                if key not in self._awarded_vendor_keys and v["pdfs"]:
                    unawarded.append(v)

            if not unawarded:
                continue

            # Sample up to 2
            import random
            sampled = random.sample(unawarded, min(2, len(unawarded)))
            self._log(
                f"  Trade \"{trade}\": sampling {len(sampled)} of {len(unawarded)} unawarded "
                f"({', '.join(v['vendor_name'] for v in sampled)})",
                "INFO",
            )

            for v in sampled:
                pdf_path = v["pdfs"][0]  # first PDF
                sampled_pdfs.append({
                    "pdf_path": pdf_path,
                    "vendor_name": v["vendor_name"],
                    "trade": trade,
                    "source": f"Quotes/{trade} (unawarded sample)",
                })

        self._log(f"  Total unawarded samples to extract: {len(sampled_pdfs)}", "INFO")

        success = 0
        for i, sq in enumerate(sampled_pdfs):
            fname = Path(sq['pdf_path']).name
            self._log(f"  [{i+1}/{len(sampled_pdfs)}] {sq['vendor_name']} — {fname}", "STEP")
            result = _extract_quote_pdf(sq["pdf_path"], log=self._log)
            if result:
                result["_source"] = sq
                result["_is_awarded"] = False
                self._extracted_quotes.append(result)
                items = result.get("line_items", [])
                total = result.get("total_ex_gst") or result.get("total_incl_gst") or 0
                self._log(f"Extracted: {len(items)} line items, ${total:,.2f}", "RESULT")
                success += 1
                sample_count += 1

        self._log(f"  Successfully extracted {success}/{len(sampled_pdfs)} unawarded samples", "OK")

    # ── Phase 6: Populate Database ─────────────────────────────────────

    def _phase_6_populate_database(self) -> None:
        self._log("Phase 6: Populating database...", "PHASE")

        from shared_tools.subcontractor.subcontractor_db import (
            init_subcontractor_db, upsert_project, upsert_vendor,
            upsert_commitment, upsert_commitment_item,
            upsert_quote, upsert_quote_item,
        )

        init_subcontractor_db()

        # ── 6.1 Project ─────────────────────────────────────────────────
        self._project_entry_id = f"project-{self._project_name}"
        upsert_project({
            "entry_id": self._project_entry_id,
            "name": self._project_name,
            "job_number": self._project_job_number,
            "location": self._project_location,
            "head_contract_sum": self._project_head_contract_sum,
            "contract_type": "AS 4000-1997",
            "company_name": "Welink Construction",
            "retention_pct": 5,
            "status": "active",
        })
        self._log(f"  Upserted project: {self._project_name}", "INFO")

        # ── 6.2 Vendors ─────────────────────────────────────────────────
        for key, vendor in self._vendor_map.items():
            upsert_vendor(vendor)
        self._log(f"  Upserted {len(self._vendor_map)} vendors", "OK")

        # ── 6.3 Quotes + Quote Items ────────────────────────────────────
        quote_count = 0
        item_count = 0
        self._quote_entry_ids: dict[str, str] = {}  # (vendor_key, source_pdf) → quote_entry_id

        for eq in self._extracted_quotes:
            src = eq.get("_source", {})
            vendor_name = src.get("vendor_name", eq.get("vendor_name", ""))
            trade = src.get("trade", eq.get("trade_name", ""))
            vendor_key = _vendor_name_key(vendor_name)
            vendor_entry_id = self._vendors_by_name.get(vendor_key, "")

            if not vendor_entry_id:
                continue

            quote_entry_id = f"quote-{self._project_name}-{vendor_key}-{Path(src.get('pdf_path', '')).stem}"[:120]
            total = eq.get("total_ex_gst") or eq.get("total_incl_gst") or 0

            upsert_quote({
                "entry_id": quote_entry_id,
                "project_entry_id": self._project_entry_id,
                "vendor_entry_id": vendor_entry_id,
                "trade_name": _normalize_trade(trade),
                "quote_ref": eq.get("quote_ref", ""),
                "total_amount": total,
                "date_submitted": eq.get("date", ""),
                "is_awarded": 1 if eq.get("_is_awarded") else 0,
                "source_file_path": src.get("pdf_path", ""),
                "ai_extracted": 1,
                "notes": eq.get("notes", ""),
                "source": "learner",
            })
            quote_count += 1

            # Line items
            for item in eq.get("line_items", []):
                qty = item.get("qty", 0) or 0
                rate = item.get("rate", 0) or 0
                amount = item.get("amount") or (qty * rate)
                upsert_quote_item({
                    "quote_entry_id": quote_entry_id,
                    "item_number": item.get("item_number", 1),
                    "description": item.get("description", ""),
                    "qty": qty,
                    "unit": item.get("unit", "item"),
                    "rate": rate,
                    "amount": amount,
                    "sort_order": item.get("item_number", 1),
                })
                item_count += 1

            # Track for commitment linking
            key = f"{vendor_key}:{src.get('pdf_path', '')}"
            self._quote_entry_ids[key] = quote_entry_id

        self._log(f"  Upserted {quote_count} quotes ({item_count} line items)", "OK")

        # ── 6.4 Commitments (POs + Subcontracts) ────────────────────────
        po_count = 0
        sub_count = 0
        commit_item_count = 0

        # Create commitments from PO folders
        for po in self._po_folders:
            vendor_key = _vendor_name_key(po["vendor_name"])
            vendor_entry_id = self._vendors_by_name.get(vendor_key, "")
            if not vendor_entry_id:
                continue

            commit_entry_id = f"commitment-{self._project_name}-{po['reference_number']}"
            upsert_commitment({
                "entry_id": commit_entry_id,
                "project_entry_id": self._project_entry_id,
                "vendor_entry_id": vendor_entry_id,
                "commitment_type": "purchase_order",
                "reference_number": po["reference_number"],
                "title": f"{po['trade']} — {po['vendor_name']}",
                "description": f"PO per {po['folder_name']}",
                "commitment_value": 0,  # Will be filled when items are linked
                "status": "issued",
                "source": "learner",
            })
            po_count += 1

            # Try to link extracted quote items
            for eq in self._extracted_quotes:
                src = eq.get("_source", {})
                if (src.get("reference") == po["reference_number"] and
                        eq.get("_is_awarded")):
                    for item in eq.get("line_items", []):
                        qty = item.get("qty", 0) or 0
                        rate = item.get("rate", 0) or 0
                        amount = item.get("amount") or (qty * rate)
                        upsert_commitment_item({
                            "commitment_entry_id": commit_entry_id,
                            "item_number": item.get("item_number", 1),
                            "description": item.get("description", ""),
                            "qty": qty,
                            "unit": item.get("unit", "item"),
                            "rate": rate,
                            "amount": amount,
                            "sort_order": item.get("item_number", 1),
                        })
                        commit_item_count += 1

        # Create commitments from Subcontract folders
        for sub in self._subcontract_folders:
            vendor_key = _vendor_name_key(sub["vendor_name"])
            vendor_entry_id = self._vendors_by_name.get(vendor_key, "")
            if not vendor_entry_id:
                continue

            commit_entry_id = f"commitment-{self._project_name}-{sub['reference_number']}"
            upsert_commitment({
                "entry_id": commit_entry_id,
                "project_entry_id": self._project_entry_id,
                "vendor_entry_id": vendor_entry_id,
                "commitment_type": "subcontract",
                "reference_number": sub["reference_number"],
                "title": f"{sub['trade']} — {sub['vendor_name']}",
                "description": f"Subcontract per {sub['folder_name']}",
                "commitment_value": 0,
                "retention_pct": 5,
                "status": "executed",
                "source": "learner",
            })
            sub_count += 1

            # Try to link extracted quote items
            for eq in self._extracted_quotes:
                src = eq.get("_source", {})
                eq_vendor_key = _vendor_name_key(src.get("vendor_name", ""))
                if eq_vendor_key == vendor_key and eq.get("_is_awarded"):
                    for item in eq.get("line_items", []):
                        qty = item.get("qty", 0) or 0
                        rate = item.get("rate", 0) or 0
                        amount = item.get("amount") or (qty * rate)
                        upsert_commitment_item({
                            "commitment_entry_id": commit_entry_id,
                            "item_number": item.get("item_number", 1),
                            "description": item.get("description", ""),
                            "qty": qty,
                            "unit": item.get("unit", "item"),
                            "rate": rate,
                            "amount": amount,
                            "sort_order": item.get("item_number", 1),
                        })
                        commit_item_count += 1

        self._log(f"  Upserted {po_count} POs + {sub_count} Subcontracts ({commit_item_count} line items)", "OK")

    # ── Phase 7: Build Knowledge Base ───────────────────────────────────

    def _phase_7_build_knowledge_base(self) -> None:
        self._log("Phase 7: Building knowledge base...", "PHASE")
        self._build_rate_benchmarks()
        self._build_clause_library()
        self._build_competitive_sets()
        self._log("Knowledge base built", "OK")

    def _build_rate_benchmarks(self) -> None:
        """Compute per-trade rate statistics from extracted line items."""
        self._log("  Computing rate benchmarks...", "STEP")

        from shared_tools.subcontractor.subcontractor_db import (
            upsert_rate_benchmark, clear_rate_benchmarks,
        )

        clear_rate_benchmarks(self._project_entry_id)

        # Group items by trade + scope keyword
        groups: dict[tuple[str, str, str], list[float]] = {}
        # (trade, scope_keyword, unit) → [rates]

        for eq in self._extracted_quotes:
            src = eq.get("_source", {})
            trade = _normalize_trade(src.get("trade", eq.get("trade_name", "")))
            for item in eq.get("line_items", []):
                rate = item.get("rate", 0) or 0
                if rate <= 0:
                    continue
                desc = item.get("description", "")
                unit = item.get("unit", "item")
                # Extract scope keyword: first 2-3 meaningful words
                keywords = [w for w in desc.lower().split()
                           if w not in {"the", "a", "an", "to", "for", "of", "in", "and", "or", "with", "is"}]
                scope_kw = " ".join(keywords[:3]) if keywords else "general"

                key = (trade, scope_kw, unit)
                groups.setdefault(key, []).append(rate)

        count = 0
        for (trade, scope_kw, unit), rates in groups.items():
            if len(rates) < 1:
                continue
            sorted_rates = sorted(rates)
            mid = len(sorted_rates) // 2
            median = sorted_rates[mid] if len(sorted_rates) % 2 == 1 else (
                (sorted_rates[mid - 1] + sorted_rates[mid]) / 2
            )
            upsert_rate_benchmark({
                "entry_id": f"benchmark-{self._project_name}-{trade}-{scope_kw}"[:120],
                "trade_name": trade,
                "scope_keyword": scope_kw,
                "unit": unit,
                "min_rate": min(rates),
                "max_rate": max(rates),
                "avg_rate": round(statistics.mean(rates), 2),
                "median_rate": round(median, 2),
                "sample_count": len(rates),
                "project_entry_id": self._project_entry_id,
            })
            count += 1

        self._log(f"  Rate benchmarks: {count} entries across {len(set(k[0] for k in groups))} trades", "RESULT")

    def _build_clause_library(self) -> None:
        """Extract clauses from subcontract .docx files."""
        self._log("  Extracting clause library from subcontract .docx files...", "STEP")

        from shared_tools.subcontractor.subcontractor_db import (
            upsert_clause, clear_clause_library,
        )

        clear_clause_library(self._project_entry_id)

        total_clauses = 0
        doc_count = 0
        base = self._project_dir / "2. Subcontracts"

        for sub in self._subcontract_folders:
            folder_path = Path(sub.get("folder_path", ""))
            if not folder_path.exists():
                continue

            # Find .docx files (recursively, prefer the executed contract)
            docx_files = list(folder_path.rglob("*.docx"))
            if not docx_files:
                self._log(f"  No .docx found for {sub['reference_number']} — skipping", "WARN")
                continue

            # Prefer files with "Subcontract" or "Welink" in name, or choose first
            preferred = [d for d in docx_files
                        if "subcontract" in d.name.lower() or "welink" in d.name.lower()]
            docx_path = preferred[0] if preferred else docx_files[0]

            clauses = _extract_clauses_from_docx(str(docx_path))
            if not clauses:
                self._log(f"  No clauses extracted from {docx_path.name} — skipping", "WARN")
                continue

            for clause in clauses:
                upsert_clause({
                    "entry_id": f"clause-{self._project_name}-{sub['reference_number']}-{clause['clause_number']}"[:120],
                    "clause_number": clause["clause_number"],
                    "clause_title": clause["clause_title"],
                    "clause_text": clause["clause_text"],
                    "source_type": "subcontract",
                    "source_doc_path": str(docx_path),
                    "source_commitment_ref": sub["reference_number"],
                    "project_entry_id": self._project_entry_id,
                })
                total_clauses += 1

            doc_count += 1
            self._log(f"  {sub['reference_number']} ({docx_path.name}): {len(clauses)} clauses", "RESULT")

        self._log(f"  Clause library: {total_clauses} clauses from {doc_count} subcontracts", "RESULT")

    def _build_competitive_sets(self) -> None:
        """Build competitive sets from trade quote folders."""
        self._log("  Building competitive sets...", "STEP")

        from shared_tools.subcontractor.subcontractor_db import (
            upsert_competitive_set, clear_competitive_sets,
        )

        clear_competitive_sets(self._project_entry_id)

        count = 0
        for trade, vendors in self._quote_vendors.items():
            vendor_entry_ids = []
            awarded_id = ""
            for v in vendors:
                key = _vendor_name_key(v["vendor_name"])
                eid = self._vendors_by_name.get(key, "")
                if eid:
                    vendor_entry_ids.append(eid)
                    if key in self._awarded_vendor_keys and not awarded_id:
                        awarded_id = eid

            if len(vendor_entry_ids) >= 2:
                upsert_competitive_set({
                    "entry_id": f"compset-{self._project_name}-{trade}"[:120],
                    "trade_name": trade,
                    "vendor_entry_ids": json.dumps(vendor_entry_ids),
                    "project_entry_id": self._project_entry_id,
                    "quote_count": len(vendor_entry_ids),
                    "awarded_vendor_entry_id": awarded_id,
                })
                count += 1

        self._log(f"  Competitive sets: {count} trades with 2+ bidders", "RESULT")

    # ── Summary ────────────────────────────────────────────────────────

    def _build_summary_dict(self) -> dict:
        elapsed = (datetime.now() - self._start_time).total_seconds() if self._start_time else 0
        sub_count = sum(1 for v in self._vendor_map.values() if v["vendor_type"] == "subcontractor")
        sup_count = sum(1 for v in self._vendor_map.values() if v["vendor_type"] == "supplier")
        awarded = sum(1 for eq in self._extracted_quotes if eq.get("_is_awarded"))
        unawarded = sum(1 for eq in self._extracted_quotes if not eq.get("_is_awarded"))
        total_items = sum(len(eq.get("line_items", [])) for eq in self._extracted_quotes)
        return {
            "project": self._project_name,
            "vendors": len(self._vendor_map),
            "subcontractors": sub_count,
            "suppliers": sup_count,
            "quotes_extracted": len(self._extracted_quotes),
            "quotes_awarded": awarded,
            "quotes_unawarded_sample": unawarded,
            "line_items": total_items,
            "pos": len(self._po_folders),
            "subcontracts": len(self._subcontract_folders),
            "rate_benchmarks": 0,  # counted during phase 7
            "clauses": 0,
            "competitive_sets": 0,
            "elapsed_seconds": round(elapsed, 1),
        }

    def _print_summary(self) -> None:
        d = self._build_summary_dict()
        self._separator()
        self._log("LEARNER COMPLETE", "PHASE")
        self._separator()
        self._log(f"Project:            {d['project']}", "INFO")
        self._log(f"Vendors:            {d['vendors']} ({d['subcontractors']} subcontractors, {d['suppliers']} suppliers)", "INFO")
        self._log(f"Quotes extracted:   {d['quotes_extracted']} ({d['quotes_awarded']} awarded, {d['quotes_unawarded_sample']} unawarded samples)", "INFO")
        self._log(f"Line items:         {d['line_items']}", "INFO")
        self._log(f"Commitments:        {d['pos']} POs, {d['subcontracts']} Subcontracts", "INFO")
        self._log(f"Rate benchmarks:    {d['rate_benchmarks']}", "INFO")
        self._log(f"Clauses:            {d['clauses']}", "INFO")
        self._log(f"Competitive sets:   {d['competitive_sets']}", "INFO")
        self._log(f"Time:               {d['elapsed_seconds']:.0f}s", "INFO")
        self._separator()


# =============================================================================
# CLI Entry Point
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Subcontractor Learner — batch knowledge builder",
    )
    parser.add_argument(
        "--project-dir",
        required=True,
        help="Path to the project subcontractor folder (e.g., '5.17 Sub Contractors')",
    )
    parser.add_argument(
        "--project-name", default="ARCO",
        help="Project name (default: ARCO)",
    )
    parser.add_argument(
        "--project-location", default="22-24 Hood Street, Subiaco WA 6008",
        help="Project location",
    )
    parser.add_argument(
        "--head-contract-sum", type=float, default=18_450_000,
        help="Head contract sum in AUD (default: 18450000)",
    )
    args = parser.parse_args()

    learner = SubcontractorLearner(
        project_dir=args.project_dir,
        project_name=args.project_name,
        project_location=args.project_location,
        project_head_contract_sum=args.head_contract_sum,
    )
    result = learner.run()
    print(f"\nDone. Summary: {json.dumps(result, indent=2)}")
