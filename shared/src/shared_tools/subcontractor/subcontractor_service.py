"""SubcontractorService — Quote → PO → Subcontract workflow engine.

Owns all business logic for construction subcontractor management:
  - CRUD commitments (POs + Subcontracts) with line items
  - Deterministic cost calculation (NEVER LLM)
  - Vendor management (supplier vs. subcontractor classification)
  - PO/Subcontract document generation from templates
  - The $100K upgrade rule (subcontractor + PO ≥ $100K → subcontract)

Follows the project service pattern:
  QObject + pyqtSignal + threading.Thread + queue.Queue

The UI layer (FastAPI routes, PyQt6 GUI, WebUI) is a thin consumer.
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal


# =============================================================================
# Helpers
# =============================================================================

def _resolve_output_dir() -> Path:
    """Resolve the subcontractor output directory."""
    from shared_tools.core.ipc_bridge import CREWAI_DIR
    sub_dir = CREWAI_DIR / "subcontractor_output"
    sub_dir.mkdir(parents=True, exist_ok=True)
    return sub_dir


def _new_entry_id() -> str:
    return str(uuid.uuid4())


# =============================================================================
# SubcontractorService
# =============================================================================

class SubcontractorService(QObject):
    """Central service for the Subcontractor Management workflow.

    Signals (UI connects to these):
      commitment_created(entry_id)
      commitment_updated(entry_id)
      document_generated(entry_id, file_path)
      pdf_exported(entry_id, file_path)
      email_drafted(entry_id, draft_text)
      email_sent(entry_id)
      error_occurred(entry_id, error_message)
      progress_update(percentage, description)
      upgrade_required(entry_id, vendor_name, amount)  ← $100K rule triggered
    """

    # ── Signals ───────────────────────────────────────────────────────
    commitment_created = pyqtSignal(str)
    commitment_updated = pyqtSignal(str)
    document_generated = pyqtSignal(str, str)
    pdf_exported = pyqtSignal(str, str)
    email_drafted = pyqtSignal(str, str)
    email_sent = pyqtSignal(str)
    error_occurred = pyqtSignal(str, str)
    progress_update = pyqtSignal(int, str)
    upgrade_required = pyqtSignal(str, str, float)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._work_queue: queue.Queue = queue.Queue()
        self._llm_semaphore = threading.Semaphore(1)
        self._running = False
        self._output_dir = _resolve_output_dir()

        # Initialize DB on creation
        from shared_tools.subcontractor.subcontractor_db import init_subcontractor_db
        init_subcontractor_db()

    # ── Lifecycle ─────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._worker_thread.start()

    def stop(self) -> None:
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
                        entry_id = kwargs.get("entry_id", "")
                        self.error_occurred.emit(str(entry_id), str(e))
            except queue.Empty:
                continue

    def _queue(self, action: str, **kwargs) -> None:
        self._work_queue.put((action, kwargs))

    # ── Projects ──────────────────────────────────────────────────────

    def create_project(self, data: dict) -> str:
        entry_id = data.get("entry_id") or _new_entry_id()
        data["entry_id"] = entry_id
        data.setdefault("company_name", "Welink Construction")
        data.setdefault("retention_pct", 5)
        data.setdefault("status", "active")

        from shared_tools.subcontractor.subcontractor_db import upsert_project
        upsert_project(data)
        return entry_id

    def get_project(self, entry_id: str) -> dict | None:
        from shared_tools.subcontractor.subcontractor_db import get_project as db_get
        return db_get(entry_id)

    def list_projects(self) -> list[dict]:
        from shared_tools.subcontractor.subcontractor_db import get_projects
        return get_projects()

    # ── Vendors ───────────────────────────────────────────────────────

    def create_vendor(self, data: dict) -> str:
        entry_id = data.get("entry_id") or _new_entry_id()
        data["entry_id"] = entry_id
        data.setdefault("vendor_type", "subcontractor")
        data.setdefault("trade_categories", "[]")
        data.setdefault("status", "active")

        from shared_tools.subcontractor.subcontractor_db import upsert_vendor
        upsert_vendor(data)
        return entry_id

    def get_vendor(self, entry_id: str) -> dict | None:
        from shared_tools.subcontractor.subcontractor_db import (
            get_vendor as db_get,
            get_vendor_summary,
        )
        return get_vendor_summary(entry_id) or db_get(entry_id)

    def list_vendors(self, vendor_type: str | None = None,
                     trade: str | None = None) -> list[dict]:
        from shared_tools.subcontractor.subcontractor_db import get_vendors
        return get_vendors(vendor_type=vendor_type, trade=trade)

    def update_vendor(self, entry_id: str, **fields) -> bool:
        from shared_tools.subcontractor.subcontractor_db import update_vendor
        return update_vendor(entry_id, **fields)

    # ── Commitments (POs + Subcontracts) ───────────────────────────────

    def create_commitment(self, data: dict) -> str:
        """Create a new commitment (PO or Subcontract).

        Enforces the $100K upgrade rule: if vendor_type is 'subcontractor'
        and commitment_type is 'purchase_order' and value ≥ $100K,
        the system emits upgrade_required.

        Returns entry_id immediately.
        """
        entry_id = data.get("entry_id") or _new_entry_id()
        data["entry_id"] = entry_id
        data.setdefault("commitment_type", "purchase_order")
        data.setdefault("status", "draft")

        # ── $100K Upgrade Rule ─────────────────────────────────────────
        if (data.get("commitment_type") == "purchase_order"
                and data.get("commitment_value", 0) >= 100_000):
            # Check vendor type
            vendor_entry_id = data.get("vendor_entry_id", "")
            if vendor_entry_id:
                from shared_tools.subcontractor.subcontractor_db import get_vendor
                vendor = get_vendor(vendor_entry_id)
                if vendor and vendor.get("vendor_type") == "subcontractor":
                    # Upgrade required — auto-switch to subcontract
                    data["commitment_type"] = "subcontract"
                    data.setdefault("retention_pct", 5)

        from shared_tools.subcontractor.subcontractor_db import upsert_commitment
        upsert_commitment(data)
        self.commitment_created.emit(entry_id)

        # Auto-assign reference number if not set
        if not data.get("reference_number"):
            self._auto_assign_reference(entry_id, data)

        return entry_id

    def _auto_assign_reference(self, entry_id: str, data: dict) -> None:
        """Auto-assign reference number: PO16815, S15, etc."""
        project_entry_id = data.get("project_entry_id", "")
        commitment_type = data.get("commitment_type", "purchase_order")

        from shared_tools.subcontractor.subcontractor_db import (
            get_next_po_number,
            get_next_subcontract_number,
        )

        if commitment_type == "subcontract":
            num = get_next_subcontract_number(project_entry_id)
            ref = f"S{num:02d}"
        else:
            num = get_next_po_number(project_entry_id)
            ref = f"PO{num}"

        from shared_tools.subcontractor.subcontractor_db import update_commitment
        update_commitment(entry_id, reference_number=ref)

    def get_commitment(self, entry_id: str) -> dict | None:
        """Return a full commitment with items and vendor info."""
        from shared_tools.subcontractor.subcontractor_db import get_commitment_with_items
        return get_commitment_with_items(entry_id)

    def list_commitments(self, project_entry_id: str = "",
                         commitment_type: str | None = None,
                         status: str | None = None) -> list[dict]:
        """Return filtered commitments. Each includes item count and total."""
        from shared_tools.subcontractor.subcontractor_db import (
            get_commitments as db_list,
            get_commitment_items,
        )
        commitments = db_list(
            project_entry_id=project_entry_id or None,
            commitment_type=commitment_type,
            status=status,
        )
        for c in commitments:
            items = get_commitment_items(c["entry_id"])
            c["items"] = items
            c["item_count"] = len(items)
            if items:
                c["calculated_total"] = sum(
                    i.get("amount", i.get("qty", 0) * i.get("rate", 0))
                    for i in items
                )
        return commitments

    def update_commitment(self, entry_id: str, **fields) -> bool:
        """Update commitment fields. Emits commitment_updated."""
        # Re-check $100K rule if value or type is changing
        if "commitment_value" in fields or "commitment_type" in fields:
            from shared_tools.subcontractor.subcontractor_db import (
                get_commitment,
                get_vendor,
            )
            c = get_commitment(entry_id)
            if c:
                new_type = fields.get("commitment_type", c.get("commitment_type"))
                new_value = fields.get("commitment_value", c.get("commitment_value", 0))
                if new_type == "purchase_order" and new_value >= 100_000:
                    vendor = get_vendor(c.get("vendor_entry_id", ""))
                    if vendor and vendor.get("vendor_type") == "subcontractor":
                        self.upgrade_required.emit(
                            entry_id,
                            vendor.get("company_name", "Unknown"),
                            new_value,
                        )

        from shared_tools.subcontractor.subcontractor_db import update_commitment
        ok = update_commitment(entry_id, **fields)
        if ok:
            self.commitment_updated.emit(entry_id)
        return ok

    def delete_commitment(self, entry_id: str) -> bool:
        from shared_tools.subcontractor.subcontractor_db import delete_commitment
        return delete_commitment(entry_id)

    # ── Commitment Items (Line Items) ─────────────────────────────────

    def add_item(self, commitment_entry_id: str, item_data: dict) -> int | None:
        item_data["commitment_entry_id"] = commitment_entry_id
        # Auto-calculate amount if not provided
        if not item_data.get("amount") and item_data.get("qty") and item_data.get("rate"):
            item_data["amount"] = item_data["qty"] * item_data["rate"]
        from shared_tools.subcontractor.subcontractor_db import upsert_commitment_item
        item_id = upsert_commitment_item(item_data)
        if item_id:
            self.commitment_updated.emit(commitment_entry_id)
        return item_id

    def update_item(self, item_id: int, **fields) -> bool:
        from shared_tools.subcontractor.subcontractor_db import upsert_commitment_item
        fields["id"] = item_id
        result = upsert_commitment_item(fields)
        return result is not None

    def remove_item(self, item_id: int, commitment_entry_id: str) -> bool:
        from shared_tools.subcontractor.subcontractor_db import delete_commitment_item
        ok = delete_commitment_item(item_id)
        if ok:
            self.commitment_updated.emit(commitment_entry_id)
        return ok

    def reorder_items(self, commitment_entry_id: str, item_ids: list[int]) -> bool:
        from shared_tools.subcontractor.subcontractor_db import reorder_commitment_items
        ok = reorder_commitment_items(commitment_entry_id, item_ids)
        if ok:
            self.commitment_updated.emit(commitment_entry_id)
        return ok

    # ── Quotes ────────────────────────────────────────────────────────

    def create_quote(self, data: dict) -> str:
        entry_id = data.get("entry_id") or _new_entry_id()
        data["entry_id"] = entry_id
        data.setdefault("is_awarded", 0)
        data.setdefault("ai_extracted", 0)

        from shared_tools.subcontractor.subcontractor_db import upsert_quote
        upsert_quote(data)
        return entry_id

    def get_quote(self, entry_id: str) -> dict | None:
        from shared_tools.subcontractor.subcontractor_db import get_quote_with_items
        return get_quote_with_items(entry_id)

    def list_quotes(self, project_entry_id: str = "",
                    trade_name: str | None = None,
                    is_awarded: int | None = None) -> list[dict]:
        from shared_tools.subcontractor.subcontractor_db import (
            get_quotes as db_list,
            get_quote_items,
        )
        quotes = db_list(
            project_entry_id=project_entry_id or None,
            trade_name=trade_name,
            is_awarded=is_awarded,
        )
        for q in quotes:
            items = get_quote_items(q["entry_id"])
            q["items"] = items
            q["item_count"] = len(items)
        return quotes

    def add_quote_item(self, quote_entry_id: str, item_data: dict) -> int | None:
        item_data["quote_entry_id"] = quote_entry_id
        from shared_tools.subcontractor.subcontractor_db import upsert_quote_item
        return upsert_quote_item(item_data)

    def update_quote_item(self, item_id: int, **fields) -> bool:
        from shared_tools.subcontractor.subcontractor_db import upsert_quote_item
        fields["id"] = item_id
        result = upsert_quote_item(fields)
        return result is not None

    def remove_quote_item(self, item_id: int) -> bool:
        from shared_tools.subcontractor.subcontractor_db import delete_quote_item
        return delete_quote_item(item_id)

    # ── Award Quote → Generate Commitment ──────────────────────────────

    def award_quote(self, quote_entry_id: str) -> str | None:
        """Mark a quote as awarded and create a commitment from it.
        Returns the new commitment entry_id, or None on failure.
        """
        from shared_tools.subcontractor.subcontractor_db import (
            get_quote_with_items,
            update_quote,
            get_vendor,
        )

        quote = get_quote_with_items(quote_entry_id)
        if not quote:
            self.error_occurred.emit("", f"Quote {quote_entry_id} not found")
            return None

        # Determine commitment type
        vendor = get_vendor(quote.get("vendor_entry_id", ""))
        vendor_type = vendor.get("vendor_type", "subcontractor") if vendor else "subcontractor"
        total = quote.get("total_amount", 0)
        commitment_type = "subcontract" if (
            vendor_type == "subcontractor" and total >= 100_000
        ) else "purchase_order"

        # Create commitment from quote data
        commitment_data = {
            "entry_id": _new_entry_id(),
            "project_entry_id": quote.get("project_entry_id", ""),
            "vendor_entry_id": quote.get("vendor_entry_id", ""),
            "commitment_type": commitment_type,
            "title": f"{quote.get('trade_name', 'Works')} — {quote.get('vendor_name', 'Vendor')}",
            "description": f"Scope per quote {quote.get('quote_ref', '')}",
            "commitment_value": total,
            "status": "draft",
        }

        if commitment_type == "subcontract":
            commitment_data["retention_pct"] = 5

        entry_id = self.create_commitment(commitment_data)

        # Copy quote line items to commitment items
        for item in quote.get("items", []):
            self.add_item(entry_id, {
                "item_number": item.get("item_number", 1),
                "description": item.get("description", ""),
                "qty": item.get("qty", 0),
                "unit": item.get("unit", "item"),
                "rate": item.get("rate", 0),
                "amount": item.get("amount", item.get("qty", 0) * item.get("rate", 0)),
                "notes": item.get("notes", ""),
                "sort_order": item.get("sort_order", 0),
            })

        # Update quote with commitment link
        update_quote(quote_entry_id, is_awarded=1,
                     commitment_entry_id=entry_id)

        return entry_id

    # ── Document Generation (async) ────────────────────────────────────

    def generate_document(self, entry_id: str) -> None:
        """Queue document generation. Emits document_generated when done."""
        self._queue("generate_document", entry_id=entry_id)

    def _handle_generate_document(self, entry_id: str) -> None:
        """Worker: generate the PO or Subcontract Excel document."""
        from shared_tools.subcontractor.subcontractor_db import (
            get_commitment_with_items as db_get_commitment,
            update_commitment,
        )

        c = db_get_commitment(entry_id)
        if not c:
            self.error_occurred.emit(entry_id, "Commitment not found")
            return

        commitment_type = c.get("commitment_type", "purchase_order")

        try:
            from shared_tools.subcontractor.subcontractor_template import (
                generate_po_document,
                generate_subcontract_document,
            )

            self.progress_update.emit(30, f"Generating {commitment_type} document...")

            if commitment_type == "subcontract":
                output_path = generate_subcontract_document(c, self._output_dir)
            else:
                output_path = generate_po_document(c, self._output_dir)

            update_commitment(entry_id, document_path=str(output_path))

            self.progress_update.emit(100, "Document generated")
            self.document_generated.emit(entry_id, str(output_path))

        except Exception as e:
            self.error_occurred.emit(entry_id, f"Document generation failed: {e}")

    def export_pdf(self, entry_id: str) -> None:
        """Queue PDF export. Requires document to be generated first."""
        self._queue("export_pdf", entry_id=entry_id)

    def _handle_export_pdf(self, entry_id: str) -> None:
        """Worker: export the document to PDF via Excel COM."""
        from shared_tools.subcontractor.subcontractor_db import (
            get_commitment,
            update_commitment,
        )

        c = get_commitment(entry_id)
        if not c:
            self.error_occurred.emit(entry_id, "Commitment not found")
            return

        doc_path = c.get("document_path")
        if not doc_path or not Path(doc_path).exists():
            self.error_occurred.emit(entry_id,
                "Document not generated yet. Run generate-document first.")
            return

        self.progress_update.emit(20, "Exporting to PDF...")

        try:
            pdf_path = self._excel_to_pdf(doc_path)
            if pdf_path.exists():
                update_commitment(entry_id, pdf_path=str(pdf_path))
                self.progress_update.emit(100, "PDF exported")
                self.pdf_exported.emit(entry_id, str(pdf_path))
            else:
                raise RuntimeError("PDF file was not created")
        except Exception as e:
            self.error_occurred.emit(entry_id, f"PDF export failed: {e}")

    def _excel_to_pdf(self, excel_path: str) -> Path:
        """Convert Excel to PDF — exports the entire workbook (all sheets)
        so that the Terms & Conditions sheet is included, with each sheet
        starting on a fresh page. Then normalizes each page to A4 portrait
        via PyMuPDF.
        """
        pdf_path = Path(excel_path).with_suffix(".pdf")
        raw_path = pdf_path.with_suffix(".raw.pdf")

        if os.name != "nt":
            raise RuntimeError("PDF export requires Windows with Excel installed")

        import pythoncom
        pythoncom.CoInitialize()
        import win32com.client

        excel = win32com.client.Dispatch("Excel.Application")
        try:
            excel.DisplayAlerts = False
            wb = excel.Workbooks.Open(str(Path(excel_path).resolve()))

            # Set up A4 portrait on every sheet with explicit margins.
            # Excel COM ignores openpyxl's page_margins for PDF export and
            # uses printer defaults (~0.7") instead — we override that here.
            for sheet_idx in range(1, wb.Sheets.Count + 1):
                ws = wb.Sheets(sheet_idx)
                ws.PageSetup.PaperSize = 9      # xlPaperA4
                ws.PageSetup.Orientation = 1    # xlPortrait
                if sheet_idx == 2:               # Terms & Conditions sheet
                    ws.PageSetup.FitToPagesWide = 1
                    ws.PageSetup.FitToPagesTall = False
                    ws.PageSetup.LeftMargin = 28.8     # ~0.40"
                    ws.PageSetup.RightMargin = 28.8    # ~0.40" — balanced
                    ws.PageSetup.TopMargin = 28.8      # ~0.40" — print-title row below
                    ws.PageSetup.BottomMargin = 28.8   # ~0.40" — footer space
                else:
                    ws.PageSetup.FitToPagesWide = 1
                    ws.PageSetup.FitToPagesTall = False
                    ws.PageSetup.LeftMargin = 36.0     # ~0.50"
                    ws.PageSetup.RightMargin = 36.0    # ~0.50"

            # Export the entire workbook (all sheets)
            wb.ExportAsFixedFormat(
                Type=0,  # xlTypePDF
                Filename=str(raw_path.resolve()),
                Quality=0,
                IncludeDocProperties=True,
                IgnorePrintAreas=False,
            )

            wb.Close(SaveChanges=False)
        finally:
            excel.Quit()

        # ── Normalize every page to A4 portrait ─────────────────────
        self._normalize_pdf_to_a4(raw_path, pdf_path)
        try:
            raw_path.unlink(missing_ok=True)
        except Exception:
            pass
        return pdf_path

    @staticmethod
    def _normalize_pdf_to_a4(src_path: Path, out_path: Path) -> None:
        """Re-render every page onto A4 portrait after cropping excess
        printer margins. Detects the body-content bounding box (excluding
        footers/headers), then scales the cropped region to fill the A4
        page."""
        import fitz  # PyMuPDF

        A4_W, A4_H = 595.28, 841.89  # A4 portrait in points
        PAD = 36.0                    # 0.5" padding — comfortable margin on each side

        src = fitz.open(str(src_path))
        out = fitz.open()

        for page in src:
            ph = page.rect.height
            pw = page.rect.width

            # Detect body-content bounding box, skipping footer/page-number text
            x0_vals, x1_vals, y0_vals, y1_vals = [], [], [], []
            for b in page.get_text("blocks"):
                txt = b[4].strip()
                if not txt:
                    continue
                # Skip: footer zone (bottom 12%) and page-number-only text
                is_footer = b[1] > ph * 0.88
                is_page_num = ("Page" in txt and "of" in txt and len(txt) < 30)
                if is_footer or is_page_num:
                    continue
                x0_vals.append(b[0])
                x1_vals.append(b[2])
                y0_vals.append(b[1])
                y1_vals.append(b[3])

            if not x0_vals:
                new_page = out.new_page(width=A4_W, height=A4_H)
                new_page.show_pdf_page(new_page.rect, src, page.number)
                continue

            body_left = min(x0_vals)
            body_right = max(x1_vals)

            # Crop only horizontally (left/right margins), not vertically —
            # top/bottom alignment comes from the Excel sheet layout itself.
            clip_rect = fitz.Rect(
                max(0, body_left - PAD),
                0,
                min(pw, body_right + PAD),
                ph,
            )
            clip_w = clip_rect.width
            clip_h = clip_rect.height

            # Scale to fill A4 width, preserve aspect ratio
            scale = A4_W / clip_w
            new_w = A4_W
            new_h = clip_h * scale

            # If scaled height exceeds A4, fit by height instead
            if new_h > A4_H:
                scale = A4_H / clip_h
                new_w = clip_w * scale
                new_h = A4_H

            new_page = out.new_page(width=A4_W, height=A4_H)
            # Center horizontally, top-align vertically (y=0)
            x0 = (A4_W - new_w) / 2

            new_page.show_pdf_page(
                fitz.Rect(x0, 0, x0 + new_w, new_h),
                src, page.number, clip=clip_rect,
            )

        out.save(str(out_path))
        out.close()
        src.close()

    # ── Email (LLM-assisted) ──────────────────────────────────────────

    def generate_email(self, entry_id: str) -> None:
        """Queue email draft generation."""
        self._queue("generate_email", entry_id=entry_id)

    def _handle_generate_email(self, entry_id: str) -> None:
        """Worker: generate submission email using LLM."""
        from shared_tools.subcontractor.subcontractor_db import get_commitment_with_items
        from shared_tools.core.llm_config import get_llm

        c = get_commitment_with_items(entry_id)
        if not c:
            self.error_occurred.emit(entry_id, "Commitment not found")
            return

        commitment_type = c.get("commitment_type", "purchase_order")
        doc_label = "Subcontract" if commitment_type == "subcontract" else "Purchase Order"
        ref = c.get("reference_number", "")
        title = c.get("title", "")
        vendor = c.get("vendor_name", "the vendor")
        total = c.get("commitment_value", 0)

        item_lines = "\n".join(
            f"  - {i.get('description', 'Item')}: "
            f"{i.get('qty', 0)} x ${i.get('rate', 0):.2f} = "
            f"${i.get('amount', i.get('qty', 0) * i.get('rate', 0)):,.2f}"
            for i in c.get("items", []) if i.get("description")
        )

        prompt = f"""Write a professional email from Amy Chen (amy@welink.com.au)
to a subcontractor or supplier issuing a {doc_label}.

Document: {doc_label} {ref} — {title}
Vendor: {vendor}
Total (ex GST): ${total:,.2f}

Scope Items:
{item_lines}

The email should:
1. State the purpose (issuing a {doc_label} for the project)
2. Briefly describe the scope
3. Request the recipient review and sign the attached document
4. Ask for required compliance documents (insurance, SWMS if applicable)
5. Use a professional but direct tone matching Amy's style
6. Include a proper subject line

Output ONLY the email text. Start with "Subject:" on the first line."""

        with self._llm_semaphore:
            try:
                llm = get_llm("fast")
                result = llm.call(prompt)
                draft = result.strip() if isinstance(result, str) else str(result)
            except Exception:
                draft = (
                    f"Subject: {ref} — {doc_label} — {title}\n\n"
                    f"Dear {vendor},\n\n"
                    f"Please find attached {doc_label} {ref} — {title}.\n\n"
                    f"The total value is ${total:,.2f} (ex GST).\n\n"
                    f"Please review, sign, and return the attached document "
                    f"at your earliest convenience.\n\n"
                    f"Should you have any questions, please contact me directly.\n\n"
                    f"Kind regards,\n"
                    f"Amy Chen"
                )

        self.email_drafted.emit(entry_id, draft)

    def send_email(self, entry_id: str, to_recipients: str,
                   cc_recipients: str = "", subject: str = "",
                   body: str = "") -> bool:
        """Send the document via Outlook. Emits email_sent."""
        from shared_tools.subcontractor.subcontractor_db import (
            get_commitment,
            update_commitment,
        )

        c = get_commitment(entry_id)
        if not c:
            self.error_occurred.emit(entry_id, "Commitment not found")
            return False

        self._queue("send_email", entry_id=entry_id, to=to_recipients,
                     cc=cc_recipients, subject=subject, body=body)
        return True

    def _handle_send_email(self, entry_id: str, to: str, cc: str,
                            subject: str, body: str) -> None:
        """Worker: send email via Outlook COM."""
        from shared_tools.subcontractor.subcontractor_db import (
            get_commitment,
            update_commitment,
        )

        c = get_commitment(entry_id)
        if not c:
            self.error_occurred.emit(entry_id, "Commitment not found")
            return

        attachments = []
        pdf_path = c.get("pdf_path")
        if pdf_path and Path(pdf_path).exists():
            attachments.append(pdf_path)

        try:
            import pythoncom
            pythoncom.CoInitialize()
            import win32com.client

            outlook = win32com.client.Dispatch("Outlook.Application")
            mail = outlook.CreateItem(0)  # olMailItem
            ref = c.get("reference_number", "")
            title = c.get("title", "")
            ct = c.get("commitment_type", "purchase_order")
            doc_label = "Subcontract" if ct == "subcontract" else "Purchase Order"
            mail.Subject = subject or f"{ref} — {doc_label} — {title}"
            mail.To = to
            if cc:
                mail.CC = cc
            mail.Body = body

            for att in attachments:
                mail.Attachments.Add(str(Path(att).resolve()))

            mail.Send()

            update_commitment(entry_id, status="issued",
                             updated_at=datetime.now().isoformat())
            self.email_sent.emit(entry_id)
        except Exception as e:
            self.error_occurred.emit(entry_id, f"Failed to send email: {e}")

    # ── Create from Agent Result ──────────────────────────────────────

    def create_from_agent_result(self, agent_result: dict,
                                  project_entry_id: str = "") -> str:
        """Create a quote + vendor + commitment from agent analysis result.

        This is the bridge between the agent (read-only analysis) and the
        service layer (database creation). The frontend calls this after
        the user confirms the agent's extracted data on the editing page.

        Returns the commitment entry_id.
        """
        analysis = agent_result.get("analysis", {})
        vendor_match = agent_result.get("vendor_match", {})

        # Step 1: Create or reuse vendor
        vendor_entry_id = vendor_match.get("entry_id") or _new_entry_id()
        vendor_data = {
            "entry_id": vendor_entry_id,
            "vendor_type": agent_result.get("vendor_type_suggestion", "subcontractor"),
            "company_name": analysis.get("vendor_name", ""),
            "trade_categories": json.dumps([analysis.get("trade_name", "")]),
        }
        from shared_tools.subcontractor.subcontractor_db import (
            upsert_vendor,
            get_vendor,
        )
        existing = get_vendor(vendor_entry_id)
        if not existing:
            upsert_vendor(vendor_data)

        # Step 2: Create quote from extracted data
        quote_entry_id = _new_entry_id()
        quote_data = {
            "entry_id": quote_entry_id,
            "project_entry_id": project_entry_id,
            "vendor_entry_id": vendor_entry_id,
            "trade_name": analysis.get("trade_name", ""),
            "quote_ref": analysis.get("quote_ref", ""),
            "total_amount": analysis.get("total_estimated_cost", 0),
            "date_submitted": analysis.get("date", ""),
            "source_file_path": agent_result.get("source_file", ""),
            "ai_extracted": 1,
        }
        from shared_tools.subcontractor.subcontractor_db import upsert_quote
        upsert_quote(quote_data)

        # Step 3: Add quote line items
        for item in analysis.get("line_items", []):
            self.add_quote_item(quote_entry_id, {
                "item_number": item.get("item_number", 1),
                "description": item.get("description", ""),
                "qty": item.get("qty", 0),
                "unit": item.get("unit", "item"),
                "rate": item.get("rate", 0),
                "amount": item.get("qty", 0) * item.get("rate", 0),
                "sort_order": item.get("sort_order", 0),
            })

        # Step 4: Determine commitment type and create
        total = analysis.get("total_estimated_cost", 0)
        vendor_type = vendor_data["vendor_type"]
        commitment_type = "subcontract" if (
            vendor_type == "subcontractor" and total >= 100_000
        ) else "purchase_order"

        commitment_data = {
            "project_entry_id": project_entry_id,
            "vendor_entry_id": vendor_entry_id,
            "commitment_type": commitment_type,
            "title": f"{analysis.get('trade_name', 'Works')} — {analysis.get('vendor_name', 'Vendor')}",
            "description": analysis.get("scope_summary", ""),
            "commitment_value": total,
            "special_instructions": analysis.get("notes", ""),
            "approved_by": "ACHEN",
            "status": "draft",
        }
        if commitment_type == "subcontract":
            commitment_data["retention_pct"] = 5

        entry_id = self.create_commitment(commitment_data)

        # Step 5: Copy quote items to commitment items
        for item in analysis.get("line_items", []):
            self.add_item(entry_id, {
                "item_number": item.get("item_number", 1),
                "description": item.get("description", ""),
                "qty": item.get("qty", 0),
                "unit": item.get("unit", "item"),
                "rate": item.get("rate", 0),
                "amount": item.get("qty", 0) * item.get("rate", 0),
                "sort_order": item.get("sort_order", 0),
            })

        # Link quote → commitment
        from shared_tools.subcontractor.subcontractor_db import update_quote
        update_quote(quote_entry_id, is_awarded=1,
                     commitment_entry_id=entry_id)

        return entry_id

    def _parse_llm_json(self, text: str) -> dict:
        """Extract a JSON object from LLM output (may have markdown fences)."""
        # Try to extract from code fences
        m = re.search(r'\{[\s\S]*\}', text)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        # Try raw parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {}
