"""VariationService — Client Variation workflow engine.

Owns all business logic for construction client variations:
  - CRUD variations + line items
  - Deterministic cost calculation (NEVER LLM)
  - Excel generation from template
  - PDF export
  - Submission email drafting (LLM-assisted)
  - Outlook email sending

Follows the project service pattern:
  QObject + pyqtSignal + threading.Thread + queue.Queue

The UI layer (FastAPI routes, PyQt6 GUI, WebUI) is a thin consumer.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import uuid
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal


# =============================================================================
# Helpers
# =============================================================================

def _resolve_output_dir() -> Path:
    """Resolve the variations output directory."""
    from shared_tools.ipc_bridge import CREWAI_DIR
    var_dir = CREWAI_DIR / "variations"
    var_dir.mkdir(parents=True, exist_ok=True)
    return var_dir


def _new_entry_id() -> str:
    return str(uuid.uuid4())


# =============================================================================
# VariationService
# =============================================================================

class VariationService(QObject):
    """Central service for the Client Variation workflow.

    Signals (UI connects to these):
      variation_created(entry_id)
      variation_updated(entry_id)
      excel_generated(entry_id, file_path)
      pdf_exported(entry_id, file_path)
      email_drafted(entry_id, draft_text)
      email_sent(entry_id)
      error_occurred(entry_id, error_message)
      progress_update(percentage, description)
    """

    # ── Signals ───────────────────────────────────────────────────────
    variation_created = pyqtSignal(str)
    variation_updated = pyqtSignal(str)
    excel_generated = pyqtSignal(str, str)
    pdf_exported = pyqtSignal(str, str)
    email_drafted = pyqtSignal(str, str)
    email_sent = pyqtSignal(str)
    error_occurred = pyqtSignal(str, str)
    progress_update = pyqtSignal(int, str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._work_queue: queue.Queue = queue.Queue()
        self._llm_semaphore = threading.Semaphore(1)
        self._running = False
        self._output_dir = _resolve_output_dir()

        # Initialize DB on creation
        from shared_tools.variation_db import init_variation_db
        init_variation_db()

    # ── Lifecycle ─────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the worker thread."""
        if self._running:
            return
        self._running = True
        self._worker_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._worker_thread.start()

    def stop(self) -> None:
        """Stop the worker thread."""
        self._running = False
        self._work_queue.put(None)  # poison pill

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
        """Queue a task for the worker thread."""
        self._work_queue.put((action, kwargs))

    # ── Public Methods (thin — queue work and return immediately) ──────

    def create_variation(self, data: dict) -> str:
        """Create a new variation. Returns entry_id immediately.
        Emits variation_created(entry_id) when done."""
        entry_id = data.get("entry_id") or _new_entry_id()
        data["entry_id"] = entry_id
        data.setdefault("status", "draft")
        data.setdefault("vo_type", "Head Contract VO")
        data.setdefault("company_name", "Welink Construction")

        from shared_tools.variation_db import upsert_variation
        upsert_variation(data)
        self.variation_created.emit(entry_id)
        return entry_id

    def update_variation(self, entry_id: str, **fields) -> bool:
        """Update variation fields. Emits variation_updated."""
        from shared_tools.variation_db import update_variation as db_update
        ok = db_update(entry_id, **fields)
        if ok:
            self.variation_updated.emit(entry_id)
        return ok

    def get_variation(self, entry_id: str) -> dict | None:
        """Return a full variation with items."""
        from shared_tools.variation_db import get_variation as db_get, get_variation_items
        var = db_get(entry_id)
        if var:
            var["items"] = get_variation_items(entry_id)
        return var

    def list_variations(self, project_entry_id: str = "", project: str | None = None,
                        status: str | None = None) -> list[dict]:
        """Return filtered variations. Each includes item count and totals."""
        from shared_tools.variation_db import get_variations as db_list, get_variation_items
        variations = db_list(project_entry_id=project_entry_id or None,
                            project=project, status=status)
        for var in variations:
            items = get_variation_items(var["entry_id"])
            var["items"] = items
            var["item_count"] = len(items)
            if items:
                from shared_tools.variation_template import calculate_variation_costs
                var["totals"] = calculate_variation_costs(items)
        return variations

    def delete_variation(self, entry_id: str) -> bool:
        """Soft-delete a variation (status → 'void')."""
        from shared_tools.variation_db import delete_variation as db_delete
        return db_delete(entry_id)

    # ── Line Items ────────────────────────────────────────────────────

    def add_item(self, variation_entry_id: str, item_data: dict) -> int | None:
        """Add a line item. Emits variation_updated."""
        item_data["variation_entry_id"] = variation_entry_id
        from shared_tools.variation_db import upsert_variation_item, get_variation_items
        item_id = upsert_variation_item(item_data)
        if item_id:
            self.variation_updated.emit(variation_entry_id)
        return item_id

    def update_item(self, item_id: int, **fields) -> bool:
        """Update a line item. Emits variation_updated."""
        from shared_tools.variation_db import upsert_variation_item, get_variation_items
        fields["id"] = item_id
        result = upsert_variation_item(fields)
        if result:
            # Find the parent variation to emit
            from shared_tools.variation_db import get_variation_items as get_items
            items = get_items("")  # can't look up parent directly, skip for now
        return result is not None

    def remove_item(self, item_id: int, variation_entry_id: str) -> bool:
        """Delete a line item. Emits variation_updated."""
        from shared_tools.variation_db import delete_variation_item
        ok = delete_variation_item(item_id)
        if ok:
            self.variation_updated.emit(variation_entry_id)
        return ok

    def reorder_items(self, variation_entry_id: str, item_ids: list[int]) -> bool:
        """Reorder line items. Emits variation_updated."""
        from shared_tools.variation_db import reorder_variation_items
        ok = reorder_variation_items(variation_entry_id, item_ids)
        if ok:
            self.variation_updated.emit(variation_entry_id)
        return ok

    # ── Cost Calculation ──────────────────────────────────────────────

    def calculate_costs(self, variation_entry_id: str) -> dict:
        """Run deterministic cost math. Never uses LLM.
        Updates item costs and variation totals in DB. Emits variation_updated."""
        from shared_tools.variation_db import get_variation_items, upsert_variation_item
        from shared_tools.variation_template import calculate_variation_costs

        items = get_variation_items(variation_entry_id)
        result = calculate_variation_costs(items)

        # Persist calculated item costs
        for item in items:
            upsert_variation_item({
                "id": item["id"],
                "item_number": item["item_number"],
                "description": item["description"],
                "qty": item["qty"],
                "unit": item["unit"],
                "rate": item["rate"],
                "cost": item.get("cost", 0),
                "credit": item.get("credit", 0),
                "sort_order": item["sort_order"],
            })

        self.variation_updated.emit(variation_entry_id)
        return result

    # ── Excel Generation (async) ──────────────────────────────────────

    def generate_excel(self, entry_id: str) -> None:
        """Queue Excel generation. Emits excel_generated when done."""
        self._queue("generate_excel", entry_id=entry_id)

    def _handle_generate_excel(self, entry_id: str) -> None:
        """Worker: generate the variation Excel file."""
        from shared_tools.variation_db import get_variation, get_variation_items, update_variation as db_update
        from shared_tools.variation_template import (
            TemplateMapping, VariationExcelBuilder, calculate_variation_costs,
        )

        var = get_variation(entry_id)
        if not var:
            self.error_occurred.emit(entry_id, "Variation not found")
            return

        items = get_variation_items(entry_id)
        calculated = calculate_variation_costs(items)

        self.progress_update.emit(10, "Loading template mapping...")

        # Load template mapping
        try:
            mapping_path = Path(__file__).parent.parent.parent.parent / "knowledge" / "variation_template_mapping.yaml"
            if mapping_path.exists():
                mapping = TemplateMapping.from_yaml(mapping_path)
            else:
                mapping = TemplateMapping.default()
        except Exception:
            mapping = TemplateMapping.default()

        self.progress_update.emit(20, "Opening template...")

        # Open template
        template_path = self._find_template(mapping)
        builder = VariationExcelBuilder(mapping, template_path)
        builder.open()

        self.progress_update.emit(30, f"Creating VO{var.get('vo_number', 1)} sheet...")

        # Create and fill the VO sheet
        vo_number = var.get("vo_number", 1) or 1
        sheet_name = builder.create_vo_sheet(vo_number)
        ws = builder.wb[sheet_name]

        builder.build_vo_sheet(ws, var, items)

        self.progress_update.emit(60, "Updating Register sheet...")

        # Update Register sheet
        register_name = mapping.register_sheet_name
        if register_name in builder.wb.sheetnames:
            reg_ws = builder.wb[register_name]
            builder.fill_register_project_info(reg_ws, var)
            builder.fill_register_vo_row(reg_ws, var, calculated)
            # Load all variations for totals
            from shared_tools.variation_db import get_variations as db_list
            all_vars = db_list(project=var.get("project_name"))
            builder.update_register_totals(reg_ws, all_vars)

        self.progress_update.emit(80, "Saving Excel file...")

        # Save
        project = var.get("project_name", "Project").replace(" ", "_")
        job = var.get("job_number", "")
        date_str = datetime.now().strftime("%Y%m%d")
        filename = f"{date_str} {job} - {project} Client Variations.xlsx"
        output_path = self._output_dir / filename
        builder.save(output_path)
        builder.close()

        # Update DB with file path
        db_update(entry_id, excel_path=str(output_path))

        self.progress_update.emit(100, "Excel generated")
        self.excel_generated.emit(entry_id, str(output_path))

    def _find_template(self, mapping: TemplateMapping) -> Path:
        """Find the template file. Checks DB first, then knowledge/ folder."""
        # Check knowledge folder for cleaned template
        knowledge_dir = Path(__file__).parent.parent.parent.parent / "knowledge"
        cleaned = knowledge_dir / "variation_template.xlsx"
        if cleaned.exists():
            return cleaned
        # Fall back to original
        original = knowledge_dir / "drafted simple workflow" / "20260602 47CBR - Welink Construction Client Variations.xlsx"
        if original.exists():
            return original
        raise FileNotFoundError("No variation template found in knowledge/")

    # ── PDF Export (async) ────────────────────────────────────────────

    def export_pdf(self, entry_id: str) -> None:
        """Queue PDF export. Requires Excel to be generated first.
        Emits pdf_exported when done."""
        self._queue("export_pdf", entry_id=entry_id)

    def _handle_export_pdf(self, entry_id: str) -> None:
        """Worker: export the variation Excel to PDF."""
        from shared_tools.variation_db import get_variation, update_variation as db_update

        var = get_variation(entry_id)
        if not var:
            self.error_occurred.emit(entry_id, "Variation not found")
            return

        excel_path = var.get("excel_path")
        if not excel_path or not Path(excel_path).exists():
            self.error_occurred.emit(entry_id, "Excel file not generated yet. Run generate-excel first.")
            return

        self.progress_update.emit(20, "Exporting to PDF via Excel...")

        try:
            pdf_path = self._excel_to_pdf(excel_path, var)
            if not pdf_path.exists():
                raise RuntimeError(f"PDF file was not created at {pdf_path}")
            db_update(entry_id, pdf_path=str(pdf_path))
            self.progress_update.emit(100, "PDF exported")
            self.pdf_exported.emit(entry_id, str(pdf_path))
        except Exception as e:
            self.error_occurred.emit(entry_id, f"PDF export failed: {e}")

    def _excel_to_pdf(self, excel_path: str, variation: dict) -> Path:
        """Convert Excel to PDF. Uses Excel COM on Windows.
        Raises RuntimeError if conversion fails (caller must handle)."""
        pdf_path = Path(excel_path).with_suffix(".pdf")

        if os.name != "nt":
            raise RuntimeError("PDF export requires Windows with Excel installed")

        import pythoncom
        pythoncom.CoInitialize()
        import win32com.client

        excel = win32com.client.Dispatch("Excel.Application")
        try:
            excel.DisplayAlerts = False
            wb = excel.Workbooks.Open(str(Path(excel_path).resolve()))

            # Find the VO sheet — try by number, or pick the first VO* sheet
            vo_number = variation.get("vo_number") or 1
            sheet_name = f"VO{vo_number}"
            sheet_names = [s.Name for s in wb.Sheets]

            if sheet_name not in sheet_names:
                # Fall back: find first sheet starting with "VO" (skip Register, VOXX)
                for name in sheet_names:
                    if name.startswith("VO") and name != "VOXX":
                        sheet_name = name
                        break

            if sheet_name not in sheet_names:
                # Last resort: use the first non-template sheet
                for name in sheet_names:
                    if name not in ("Register", "Internal VO Register", "VOXX"):
                        sheet_name = name
                        break

            ws = wb.Sheets(sheet_name)
            ws.PageSetup.Orientation = 2  # xlLandscape
            ws.PageSetup.FitToPagesWide = 1
            ws.PageSetup.FitToPagesTall = 1
            ws.ExportAsFixedFormat(
                Type=0,  # xlTypePDF
                Filename=str(pdf_path.resolve()),
                Quality=0,  # xlQualityStandard
                IncludeDocProperties=True,
                IgnorePrintAreas=False,
            )

            wb.Close(SaveChanges=False)
            return pdf_path
        finally:
            excel.Quit()

    # ── Submission Email (LLM-assisted) ───────────────────────────────

    def generate_submission_email(self, entry_id: str) -> None:
        """Queue email draft generation (uses LLM for natural language).
        Emits email_drafted when done."""
        self._queue("generate_email", entry_id=entry_id)

    def _handle_generate_email(self, entry_id: str) -> None:
        """Worker: generate submission email using LLM."""
        from shared_tools.variation_db import get_variation, get_variation_items
        from shared_tools.variation_template import calculate_variation_costs
        from shared_tools.llm_config import get_llm

        var = get_variation(entry_id)
        if not var:
            self.error_occurred.emit(entry_id, "Variation not found")
            return

        items = get_variation_items(entry_id)
        calculated = calculate_variation_costs(items)

        vo_title = var.get("vo_title", f"VO{var.get('vo_number', '')}")
        project = var.get("project_name", "the project")
        total = calculated.get("total_incl_gst", 0)

        # Build item summary for the email
        item_lines = "\n".join(
            f"  - {i.get('description', 'Item')}: "
            f"{i.get('qty', 0)} x ${i.get('rate', 0):.2f} = ${i.get('cost', 0):.2f}"
            for i in items if i.get("description")
        )

        prompt = f"""Write a professional email from Amy Chen (amy@welink.com.au) to the client
or superintendent submitting a construction variation.

Variation: {vo_title}
Project: {project}
Job Number: {var.get('job_number', 'N/A')}
Items:
{item_lines}

Total Variation (incl GST): ${total:,.2f}

The email should:
1. State the purpose (submission of variation for review and approval)
2. Briefly describe the scope of works
3. Mention the attached variation document outlines costs and supporting documentation
4. Invite questions or requests for further information
5. Express looking forward to approval to proceed
6. Use a professional but warm tone matching Amy's style
7. Include a proper subject line starting with the job/project reference

Output ONLY the email text. Start with "Subject:" on the first line."""

        with self._llm_semaphore:
            try:
                llm = get_llm("fast")
                result = llm.call(prompt)
                draft = result.strip() if isinstance(result, str) else str(result)
            except Exception as e:
                draft = (
                    f"Subject: {var.get('job_number', '')} - Variation Submission - {vo_title}\n\n"
                    f"Dear Team,\n\n"
                    f"Please find attached Variation Submission {vo_title} "
                    f"for {project}.\n\n"
                    f"The attached variation outlines the scope of works, associated costs, "
                    f"and supporting documentation for your review and approval.\n\n"
                    f"Total Variation Cost (incl. GST): ${total:,.2f}\n\n"
                    f"Should you have any queries or require further information, "
                    f"please do not hesitate to contact me.\n\n"
                    f"We look forward to receiving your approval to proceed.\n\n"
                    f"Kind regards,\n"
                    f"Amy Chen"
                )

        self.email_drafted.emit(entry_id, draft)

    def send_submission_email(self, entry_id: str, to_recipients: str,
                               cc_recipients: str = "", subject: str = "",
                               body: str = "") -> bool:
        """Send the submission email via Outlook. Emits email_sent."""
        from shared_tools.variation_db import get_variation, update_variation as db_update
        from datetime import datetime

        var = get_variation(entry_id)
        if not var:
            self.error_occurred.emit(entry_id, "Variation not found")
            return False

        self._queue("send_email", entry_id=entry_id, to=to_recipients,
                     cc=cc_recipients, subject=subject, body=body)
        return True

    def _handle_send_email(self, entry_id: str, to: str, cc: str,
                            subject: str, body: str) -> None:
        """Worker: send email via Outlook COM."""
        from shared_tools.variation_db import get_variation, update_variation as db_update

        var = get_variation(entry_id)
        if not var:
            self.error_occurred.emit(entry_id, "Variation not found")
            return

        # Attach PDF if available
        attachments = []
        pdf_path = var.get("pdf_path")
        if pdf_path and Path(pdf_path).exists():
            attachments.append(pdf_path)

        try:
            import pythoncom
            pythoncom.CoInitialize()
            import win32com.client

            outlook = win32com.client.Dispatch("Outlook.Application")
            mail = outlook.CreateItem(0)  # olMailItem
            mail.Subject = subject or f"{var.get('job_number', '')} - Variation Submission - {var.get('vo_title', '')}"
            mail.To = to
            if cc:
                mail.CC = cc
            mail.Body = body

            for att in attachments:
                mail.Attachments.Add(str(Path(att).resolve()))

            mail.Send()

            db_update(entry_id,
                       status="submitted",
                       submitted_at=datetime.now().isoformat())
            self.email_sent.emit(entry_id)
        except Exception as e:
            self.error_occurred.emit(entry_id, f"Failed to send email: {e}")

    # ── Create from Email ─────────────────────────────────────────────

    def create_from_email(self, email_entry_id: str) -> str | None:
        """Parse an email to pre-fill variation fields (LLM-assisted).
        Returns the new variation entry_id, or None on failure."""
        from shared_tools.ipc_bridge import get_processed_email

        email = get_processed_email(email_entry_id)
        if not email:
            from shared_tools.outlook_tool import fetch_inbox_emails
            # Try to fetch from Outlook
            pass  # For now, error if not in processed store

        if not email:
            self.error_occurred.emit("", f"Email {email_entry_id} not found")
            return None

        # Use LLM to extract variation details from email
        with self._llm_semaphore:
            from shared_tools.llm_config import get_llm

            subject = email.get("subject", "")
            body = email.get("body", "")[:3000]
            sender = email.get("sender", "")

            prompt = f"""Analyze this construction email and extract any variation-related information.
Return a JSON object with these fields (use null for any you can't determine):
{{
  "vo_title": "e.g., VO - Tree Removal",
  "project_name": "project name if mentioned",
  "description": "what the variation is about (1-2 sentences)",
  "estimated_items": [
    {{"description": "...", "qty": number, "unit": "item/m2/hr/etc", "rate": number}}
  ],
  "is_estimate": true/false,
  "vo_type": "Head Contract VO" or "Client Direct VO"
}}

Email Subject: {subject}
Email Sender: {sender}
Email Body:
{body}

Output ONLY the JSON object, no other text."""

            try:
                llm = get_llm("fast")
                result = llm.call(prompt)
                result_text = result.strip() if isinstance(result, str) else str(result)
                # Extract JSON from response
                parsed = self._parse_llm_json(result_text)
            except Exception:
                parsed = {}

        # Build variation data
        data = {
            "entry_id": _new_entry_id(),
            "project_name": parsed.get("project_name", ""),
            "vo_title": parsed.get("vo_title", subject),
            "vo_type": parsed.get("vo_type", "Head Contract VO"),
            "is_estimate": 1 if parsed.get("is_estimate") else 0,
            "source_email_entry_id": email_entry_id,
            "status": "draft",
            "company_name": "Welink Construction",
        }

        entry_id = self.create_variation(data)

        # Add extracted items
        for item in parsed.get("estimated_items", []):
            item["variation_entry_id"] = entry_id
            self.add_item(entry_id, item)

        return entry_id

    # ── Internal handlers for queued operations ──────────────────────

    def _handle_push_project(self, entry_id: str) -> None:
        """Worker: push project to xlsx."""
        try:
            path = self.push_project(entry_id)
            if path:
                self.progress_update.emit(100, f"Project pushed to {path}")
        except Exception as e:
            self.error_occurred.emit(entry_id, str(e))

    def _handle_export_project_pdf(self, entry_id: str, xlsx_path: str) -> None:
        """Worker: export project xlsx to PDF."""
        from shared_tools.variation_db import get_project, update_project
        try:
            pdf_path = self._excel_to_pdf(xlsx_path, {"vo_number": 1})
            if pdf_path.exists():
                update_project(entry_id)
                self.progress_update.emit(100, f"PDF exported to {pdf_path}")
        except Exception as e:
            self.error_occurred.emit(entry_id, str(e))

    def _parse_llm_json(self, text: str) -> dict:
        """Extract a JSON object from LLM output (may have markdown fences)."""
        import re
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

    # ── Project Methods ───────────────────────────────────────────────

    def create_project(self, data: dict) -> str:
        """Create a new project. Returns entry_id."""
        from shared_tools.variation_db import upsert_project
        entry_id = data.get("entry_id") or _new_entry_id()
        data["entry_id"] = entry_id
        data.setdefault("source_type", "new")
        upsert_project(data)
        return entry_id

    def import_project(self, xlsx_path: str) -> str | None:
        """Import an existing xlsx: parse project + VOs + items into DB.
        Returns project_entry_id or None on failure."""
        from shared_tools.variation_template import import_project_from_xlsx
        from shared_tools.variation_db import upsert_project, upsert_variation, upsert_variation_item

        try:
            result = import_project_from_xlsx(xlsx_path)
        except Exception as e:
            self.error_occurred.emit("", f"Failed to parse xlsx: {e}")
            return None

        proj_data = result["project"]
        variations_data = result["variations"]

        proj_entry_id = _new_entry_id()
        proj_data["entry_id"] = proj_entry_id
        proj_data["xlsx_path"] = xlsx_path
        upsert_project(proj_data)

        for var in variations_data:
            var["project_entry_id"] = proj_entry_id
            var["project_name"] = proj_data.get("name", "")
            var["project_location"] = proj_data.get("location", "")
            var["job_number"] = proj_data.get("job_number", "")
            var["base_contract_amount"] = proj_data.get("base_contract_amount", 0)
            upsert_variation(var)
            for item in var.get("items", []):
                item["variation_entry_id"] = var["entry_id"]
                upsert_variation_item(item)

        return proj_entry_id

    def push_project(self, project_entry_id: str) -> str | None:
        """Compile all VOs + Registers into xlsx with backup. Returns output path."""
        import shutil
        from datetime import datetime
        from shared_tools.variation_db import get_project, get_variations, get_variation_items, update_project
        from shared_tools.variation_template import compile_project_to_xlsx

        project = get_project(project_entry_id)
        if not project:
            self.error_occurred.emit(project_entry_id, "Project not found")
            return None

        xlsx_path = project.get("xlsx_path", "")
        if not xlsx_path:
            self.error_occurred.emit(project_entry_id, "Project has no xlsx_path set")
            return None

        output = Path(xlsx_path)
        self.progress_update.emit(5, "Loading variations...")

        variations = get_variations(project_entry_id=project_entry_id)
        for var in variations:
            var["items"] = get_variation_items(var["entry_id"])

        if output.exists():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M")
            backup = output.parent / f"{output.stem}_{timestamp}_backup{output.suffix}"
            shutil.copy2(str(output), str(backup))
            self.progress_update.emit(10, f"Backup created: {backup.name}")

        self.progress_update.emit(20, "Compiling xlsx...")
        try:
            compile_project_to_xlsx(project, variations, output)
        except Exception as e:
            self.error_occurred.emit(project_entry_id, f"Compile failed: {e}")
            return None

        update_project(project_entry_id, updated_at=datetime.now().isoformat())
        self.progress_update.emit(100, f"Pushed to {output}")
        return str(output)

    def get_register(self, project_entry_id: str) -> dict:
        """Compute the Register view for a project."""
        from shared_tools.variation_db import get_project, get_variations, get_variation_items
        from shared_tools.variation_template import calculate_variation_costs

        project = get_project(project_entry_id)
        if not project:
            return {"error": "Project not found"}

        variations = get_variations(project_entry_id=project_entry_id)
        rows = []
        total_value = total_pending = total_approved = total_not_approved = 0.0

        for var in variations:
            items = get_variation_items(var["entry_id"])
            costs = calculate_variation_costs(items) if items else {}
            total = costs.get("total_incl_gst", 0)
            status = var.get("status", "draft")

            # Map status to Register columns (matching real construction practice):
            #   Submitted → PENDING APPROVAL (awaiting client decision)
            #   Approved / Approved for Signing → use stored approved_value, fallback to total
            #   Not Approved → use stored not_approved_value, fallback to total
            if status == "submitted":
                pending = total
                not_appr = 0
                approved = 0
            elif status in ("approved", "approved_for_signing"):
                stored_approved = var.get("approved_value") or 0
                stored_not = var.get("not_approved_value") or 0
                if stored_approved > 0 or stored_not > 0:
                    approved = stored_approved
                    not_appr = stored_not
                else:
                    approved = total
                    not_appr = 0
                pending = 0
            elif status == "not_approved":
                stored_approved = var.get("approved_value") or 0
                stored_not = var.get("not_approved_value") or 0
                if stored_approved > 0 or stored_not > 0:
                    approved = stored_approved
                    not_appr = stored_not
                else:
                    approved = 0
                    not_appr = total
                pending = 0
            else:
                pending = 0
                not_appr = 0
                approved = 0

            row = {
                "vo_number": var.get("vo_number"),
                "description": var.get("vo_title", ""),
                "date_issued": (var.get("date_issued") or "")[:10],
                "variation_value": total,
                "pending_approval": pending,
                "not_approved": not_appr,
                "total_approved": approved,
                "status": status.replace("_", " ").title(),
                "notes": var.get("vo_type", ""),
            }
            rows.append(row)
            total_value += total
            total_pending += pending
            total_approved += approved
            total_not_approved += not_appr

        base = project.get("base_contract_amount", 0)
        return {
            "project": {
                "name": project.get("name", ""),
                "job_number": project.get("job_number", ""),
                "location": project.get("location", ""),
                "base_contract_amount": base,
                "projected_total": base + total_approved + total_pending,
            },
            "rows": rows,
            "totals": {
                "variation_value": total_value,
                "pending_approval": total_pending,
                "total_approved": total_approved,
                "not_approved": total_not_approved,
            },
        }

    def get_internal_register(self, project_entry_id: str) -> dict:
        """Compute the Internal VO Register view for a project.
        Uses same status-based logic as the main Register.
        Approved VOs: approval_type determines bank vs client column."""
        from shared_tools.variation_db import get_project, get_variations, get_variation_items
        from shared_tools.variation_template import calculate_variation_costs

        project = get_project(project_entry_id)
        if not project:
            return {"error": "Project not found"}

        variations = get_variations(project_entry_id=project_entry_id)
        rows = []
        total_value = total_bank = total_client = total_pending = 0.0
        seq = 1

        for var in variations:
            items = get_variation_items(var["entry_id"])
            costs = calculate_variation_costs(items) if items else {}
            total = costs.get("total_incl_gst", 0)
            status = var.get("status", "submitted")

            bank_approved = 0
            client_approved = 0

            if status == "submitted":
                pending = total
            elif status in ("approved", "approved_for_signing"):
                stored = var.get("approved_value") or 0
                approved_amt = stored if stored > 0 else total
                pending = 0
                # Route to bank or client based on approval_type
                if var.get("approval_type") == "bank":
                    bank_approved = approved_amt
                else:
                    client_approved = approved_amt
            elif status == "not_approved":
                stored = var.get("not_approved_value") or 0
                pending = stored if stored > 0 else total
            else:
                pending = 0

            rows.append({
                "seq": seq,
                "vo_number": var.get("vo_number"),
                "description": var.get("vo_title", ""),
                "variation_value": total,
                "bank_approved": bank_approved,
                "client_approved": client_approved,
                "pending": pending,
            })
            total_value += total
            total_bank += bank_approved
            total_client += client_approved
            total_pending += pending
            seq += 1

        return {
            "project": {"name": project.get("name", "")},
            "rows": rows,
            "totals": {
                "variation_value": total_value,
                "bank_approved": total_bank,
                "client_approved": total_client,
                "pending": total_pending,
            },
        }

    def list_projects(self) -> list[dict]:
        """Return all projects with VO counts."""
        from shared_tools.variation_db import get_projects, get_project_vo_count
        projects = get_projects()
        for p in projects:
            p["vo_count"] = get_project_vo_count(p["entry_id"])
        return projects

    def delete_project(self, entry_id: str) -> bool:
        """Delete a project and all its VOs + items."""
        from shared_tools.variation_db import delete_project as db_delete
        return db_delete(entry_id)

    # ── Utility ───────────────────────────────────────────────────────

    def get_next_vo_number(self, project_entry_id: str) -> int:
        """Return the next VO number: count of active (non-void) VOs + 1."""
        from shared_tools.variation_db import get_variations
        existing = get_variations(project_entry_id=project_entry_id)  # excludes void
        return len(existing) + 1
