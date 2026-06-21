"""PdfRenderer Service — HTML/CSS → PDF via Playwright + Jinja2.

Replaces legacy Excel COM PDF generation. Cross-platform, design-controllable.

Service pattern: QObject + pyqtSignal + threading.Thread + queue.Queue
Provides both async (queued) and sync (direct) rendering methods.

Usage:
    # Sync (FastAPI / CLI):
    svc = PdfRendererService()
    pdf_bytes = svc.render_sync("po", context)

    # Async (PyQt6 GUI):
    svc = PdfRendererService()
    svc.render_complete.connect(on_pdf_ready)
    svc.start()
    svc.render("vo", context)
"""

from __future__ import annotations

import base64
import logging
import queue
import threading
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright
from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)

# ── Path resolution ─────────────────────────────────────────────────────

def _resolve_project_root() -> Path:
    """Resolve project root relative to this file.

    pdf_renderer_service.py → shared/src/shared_tools/pdf_renderer/
    parent × 5 → project root
    """
    return Path(__file__).resolve().parent.parent.parent.parent.parent


PROJECT_ROOT = _resolve_project_root()
TEMPLATES_DIR = PROJECT_ROOT / "knowledge" / "templates" / "pdf"
FONTS_DIR = PROJECT_ROOT / "knowledge" / "fonts"
LOGO_PATH = PROJECT_ROOT / "knowledge" / "welink_logo.jpeg"


# ── PO Terms & Conditions ───────────────────────────────────────────────

PO_TERMS = [
    {
        "number": "1.",
        "title": "ACCEPTANCE",
        "body": (
            "The Supplier agrees to supply the Goods and/or Services described in this "
            "Purchase Order strictly in accordance with these terms and conditions. "
            "Unless otherwise agreed in writing, acceptance of this Purchase Order is "
            "limited to these terms and conditions."
        ),
    },
    {
        "number": "2.",
        "title": "PRICE",
        "body": (
            "The prices stated in this Purchase Order are fixed and include all charges "
            "for packing, insurance, delivery, and any other costs unless otherwise "
            "agreed in writing. No variation in price will be accepted without written "
            "approval from the Purchasing Officer."
        ),
    },
    {
        "number": "3.",
        "title": "DELIVERY",
        "body": (
            "Time is of the essence. The Supplier must deliver the Goods and/or Services "
            "by the delivery date specified in this Purchase Order. If no date is "
            "specified, delivery must be made within a reasonable time. The Supplier "
            "must notify the Purchaser immediately of any anticipated delay."
        ),
    },
    {
        "number": "4.",
        "title": "QUALITY AND INSPECTION",
        "body": (
            "All Goods and Services must conform to the specifications, drawings, and "
            "standards referenced in this Purchase Order. The Purchaser reserves the "
            "right to inspect all Goods and to reject any that do not conform."
        ),
    },
    {
        "number": "5.",
        "title": "INVOICING AND PAYMENT",
        "body": (
            "The Supplier must submit a tax invoice quoting this Purchase Order number. "
            "Payment will be made within 30 days from the end of the month in which a "
            "correctly rendered tax invoice is received."
        ),
    },
    {
        "number": "6.",
        "title": "INSURANCE AND INDEMNITY",
        "body": (
            "The Supplier must maintain public liability insurance for at least "
            "$20,000,000 and workers compensation insurance as required by law. "
            "The Supplier indemnifies the Purchaser against all loss, damage, or "
            "injury arising from the supply of Goods and Services."
        ),
    },
    {
        "number": "7.",
        "title": "COMPLIANCE WITH LAWS",
        "body": (
            "The Supplier must comply with all applicable laws, regulations, codes, "
            "and standards including workplace health and safety, environmental "
            "protection, and industrial relations legislation."
        ),
    },
    {
        "number": "8.",
        "title": "TERMINATION",
        "body": (
            "The Purchaser may terminate this Purchase Order at any time by giving "
            "written notice. Upon termination, the Supplier is entitled to payment "
            "for Goods delivered and Services performed up to the date of termination."
        ),
    },
    {
        "number": "9.",
        "title": "CONFIDENTIALITY",
        "body": (
            "The Supplier must keep confidential all information relating to the "
            "Purchaser's business, operations, and this Purchase Order, and must "
            "not disclose such information to any third party without prior written "
            "consent."
        ),
    },
    {
        "number": "10.",
        "title": "GOVERNING LAW",
        "body": (
            "This Purchase Order is governed by the laws of New South Wales, "
            "Australia. The parties submit to the non-exclusive jurisdiction of "
            "the courts of New South Wales."
        ),
    },
]

# ── Cost calculations (deterministic, no AI) ────────────────────────────


def calculate_vo_costs(items: list[dict]) -> dict:
    """Calculate VO costs from line items.

    Pure Python math — 10% margin, 10% GST. No LLM involved.

    Returns dict with: sub_total_cost, sub_total_credit, nett_variation_cost,
    margin, excl_gst, gst, total_incl_gst
    """
    sub_total_cost = sum(
        (item.get("qty", 0) or 0) * (item.get("rate", 0) or 0)
        for item in items
    )
    sub_total_credit = sum(item.get("credit", 0) or 0 for item in items)
    nett = sub_total_cost - sub_total_credit
    margin = round(nett * 0.10, 2)
    excl_gst = round(nett + margin, 2)
    gst = round(excl_gst * 0.10, 2)
    total = round(excl_gst + gst, 2)
    return {
        "sub_total_cost": sub_total_cost,
        "sub_total_credit": sub_total_credit,
        "nett_variation_cost": nett,
        "margin": margin,
        "excl_gst": excl_gst,
        "gst": gst,
        "total_incl_gst": total,
    }


def calculate_po_totals(items: list[dict]) -> dict:
    """Calculate PO totals from line items.

    Pure Python math — 10% GST. No LLM involved.

    Returns dict with: total_ex_gst, gst, gross
    """
    total_ex = sum(item.get("amount", 0) or 0 for item in items)
    # Fallback: compute from qty * rate if amount not set
    if total_ex == 0:
        total_ex = sum(
            (item.get("qty", 0) or 0) * (item.get("rate", 0) or 0)
            for item in items
        )
    gst = round(total_ex * 0.10, 2)
    gross = round(total_ex + gst, 2)
    return {
        "total_ex_gst": total_ex,
        "gst": gst,
        "gross": gross,
    }


# ── Service class ───────────────────────────────────────────────────────


class PdfRendererService(QObject):
    """Render HTML/CSS templates to PDF via Playwright + Jinja2.

    Signals:
        render_complete(template_name: str, pdf_bytes: bytes)
        error_occurred(template_name: str, error_message: str)

    Usage:
        # Sync (direct, for FastAPI / CLI):
        svc = PdfRendererService()
        pdf_bytes = svc.render_sync("po", context)

        # Async (queued, for PyQt6 GUI):
        svc = PdfRendererService()
        svc.render_complete.connect(handle_pdf)
        svc.error_occurred.connect(handle_error)
        svc.start()
        svc.render("vo", context)
    """

    render_complete = pyqtSignal(str, bytes)
    render_to_file_complete = pyqtSignal(str, str)
    error_occurred = pyqtSignal(str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._work_queue: queue.Queue = queue.Queue()
        self._running = False
        self._jinja_env: Environment | None = None
        self._playwright = None
        self._browser = None
        self._browser_lock = threading.Lock()

    # ── Public methods ──────────────────────────────────────────────

    def start(self) -> None:
        """Start the worker thread (daemon)."""
        self._running = True
        self._worker_thread = threading.Thread(
            target=self._run_loop, daemon=True
        )
        self._worker_thread.start()

    def stop(self) -> None:
        """Stop the worker thread and clean up browser."""
        self._running = False
        self._work_queue.put(None)  # poison pill
        self._close_browser()

    def render(self, template_name: str, context: dict) -> None:
        """Queue an async render. Emits render_complete when done."""
        self._work_queue.put(("render", {
            "template_name": template_name,
            "context": context,
        }))

    def render_to_file(
        self, template_name: str, context: dict, output_path: str | Path
    ) -> None:
        """Queue an async render-to-file. Emits render_to_file_complete when done."""
        self._work_queue.put(("render_to_file", {
            "template_name": template_name,
            "context": context,
            "output_path": str(output_path),
        }))

    def render_sync(self, template_name: str, context: dict) -> bytes:
        """Render PDF synchronously. Returns PDF bytes.

        Used by FastAPI route handlers — no threading overhead needed.
        """
        return self._do_render(template_name, context)

    def render_to_file_sync(
        self, template_name: str, context: dict, output_path: str | Path
    ) -> Path:
        """Render PDF synchronously and save to disk. Returns the output Path."""
        output_path = Path(output_path)
        pdf_bytes = self._do_render(template_name, context)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(pdf_bytes)
        return output_path

    def render_schema(self, schema: dict, context: dict) -> bytes:
        """Render a PDF from a JSON schema template.

        The schema defines elements (type, position, size, bind) that
        the generic _generic.html template renders.

        Args:
            schema: Dict with 'name', 'page', and 'elements' keys.
            context: Dict of data bindings referenced by element 'bind' keys.

        Returns:
            PDF bytes.
        """
        return self._do_render_schema(schema, context)

    def render_schema_to_file(
        self, schema: dict, context: dict, output_path: str | Path
    ) -> Path:
        """Render schema PDF and save to disk."""
        output_path = Path(output_path)
        pdf_bytes = self._do_render_schema(schema, context)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(pdf_bytes)
        return output_path

    def load_schema(self, name: str) -> dict:
        """Load a JSON schema template from knowledge/templates/pdf/{name}.json.

        Falls back to {name} if the .json extension is omitted.
        """
        import json as _json
        if not name.endswith(".json"):
            name = f"{name}.json"
        path = TEMPLATES_DIR / name
        if not path.exists():
            raise FileNotFoundError(f"Schema template not found: {path}")
        return _json.loads(path.read_text(encoding="utf-8"))

    # ── Schema rendering internals ─────────────────────────────────

    def _do_render_schema(self, schema: dict, context: dict) -> bytes:
        """Core schema rendering pipeline.

        1. Resolve auto-layout positions (null y → stacked)
        2. Build template context: page_width, page_height, elements, data
        3. Render _generic.html via Jinja2
        4. Convert HTML to PDF via Playwright
        """
        import copy
        schema = copy.deepcopy(schema)

        page = schema.get("page", {})
        is_landscape = page.get("orientation") == "landscape"
        page_width = 297 if is_landscape else 210
        page_height = 210 if is_landscape else 297

        margin = context.pop("_margin", {
            "top": "15mm", "bottom": "20mm", "left": "15mm", "right": "15mm",
        })

        # Resolve auto-layout for elements with null y
        elements = self._resolve_positions(schema.get("elements", []), page_height)
        schema["elements"] = elements  # update schema with resolved positions

        # Build the template render context
        template_ctx = {
            "schema": schema,
            "page_width": page_width,
            "page_height": page_height,
            "elements": elements,
            "context": context,
        }

        env = self._get_jinja_env()
        # Register schema-specific helpers
        env.globals["_style_str"] = _style_to_css
        env.globals["_fmt"] = _format_value
        template = env.get_template("_generic.html")
        html_str = template.render(**template_ctx)

        # Clear schema helpers so they don't leak into other templates
        env.globals.pop("_style_str", None)
        env.globals.pop("_fmt", None)

        # CSS
        css_str = self._get_css("_base")
        font_css = self._get_font_face_css()
        if font_css:
            css_str = font_css + "\n" + css_str

        # Render via Playwright
        browser = self._get_browser()
        page_obj = browser.new_page()
        try:
            page_obj.set_content(html_str, timeout=30000)
            page_obj.add_style_tag(content=css_str)
            page_obj.wait_for_timeout(500)
            if is_landscape:
                return page_obj.pdf(
                    width=f"{page_width}mm", height=f"{page_height}mm",
                    print_background=True, margin=margin,
                )
            else:
                return page_obj.pdf(
                    format="A4", print_background=True, margin=margin,
                )
        finally:
            page_obj.close()

    @staticmethod
    def _resolve_positions(elements: list[dict], page_height: float) -> list[dict]:
        """Fill in null y positions by stacking elements with 4mm gap.

        Elements with explicit y values keep their position.
        Elements with null y are stacked below the previous element.
        """
        current_y = 12  # start 12mm from top
        resolved = []
        for el in elements:
            el = dict(el)
            if el.get("type") == "page-break":
                resolved.append(el)
                current_y = 12  # reset on new page
                continue
            if el.get("y") is None:
                el["y"] = current_y
                # Estimate height for stacking
                h = el.get("h")
                if h is None:
                    # Guess height from element type
                    if el.get("type") == "table":
                        h = 30  # conservative estimate
                    elif el.get("type") == "text":
                        h = 8
                    elif el.get("type") == "rect":
                        h = 20
                    elif el.get("type") == "signature":
                        h = 15
                    else:
                        h = 10
                current_y += h + 4  # 4mm gap
            else:
                current_y = el["y"] + (el.get("h") or 10) + 4
            resolved.append(el)
        return resolved


    # ── Internal: worker loop ───────────────────────────────────────

    def _run_loop(self) -> None:
        """Worker thread loop. Processes queued render tasks."""
        while self._running:
            try:
                task = self._work_queue.get(timeout=0.5)
                if task is None:
                    break
                action, kwargs = task
                if action == "render":
                    self._handle_render(**kwargs)
                elif action == "render_to_file":
                    self._handle_render_to_file(**kwargs)
            except queue.Empty:
                continue

    def _handle_render(self, template_name: str, context: dict) -> None:
        try:
            pdf_bytes = self._do_render(template_name, context)
            self.render_complete.emit(template_name, pdf_bytes)
        except Exception as e:
            logger.exception("Render failed for %s", template_name)
            self.error_occurred.emit(template_name, str(e))

    def _handle_render_to_file(
        self, template_name: str, context: dict, output_path: str
    ) -> None:
        try:
            path = self.render_to_file_sync(template_name, context, output_path)
            self.render_to_file_complete.emit(template_name, str(path))
        except Exception as e:
            logger.exception("Render-to-file failed for %s", template_name)
            self.error_occurred.emit(template_name, str(e))

    # ── Internal: core rendering pipeline ───────────────────────────

    def _do_render(self, template_name: str, context: dict) -> bytes:
        """Core rendering pipeline: Jinja2 → HTML → Playwright → PDF bytes.

        1. Lazy-init Jinja2 environment (FileSystemLoader)
        2. Load and render HTML template with context
        3. Read CSS files (_base.css + {template_name}.css)
        4. Embed Rage Italic font as base64 data-URI in CSS
        5. Launch Chromium, set content, generate PDF
        6. Return PDF bytes

        Context may include special keys (stripped before template render):
          - _landscape: bool — if True, use A4 landscape (default: False)
          - _margin: dict — custom page margins (default: 15mm/20mm/15mm/15mm)
        """
        # Extract PDF options from context before template render
        landscape = bool(context.pop("_landscape", False))
        margin = context.pop("_margin", {
            "top": "15mm", "bottom": "20mm", "left": "15mm", "right": "15mm",
        })

        env = self._get_jinja_env()
        template = env.get_template(f"{template_name}.html")
        html_str = template.render(**context)

        # Read CSS (from disk each time → hot-reload during development)
        css_str = self._get_css(template_name)

        # Embed base64 font for Rage Italic signature
        font_css = self._get_font_face_css()
        if font_css:
            css_str = font_css + "\n" + css_str

        # Render PDF via Playwright
        browser = self._get_browser()
        page = browser.new_page()
        try:
            page.set_content(html_str, timeout=30000)
            # Add CSS as a style tag after content is set
            page.add_style_tag(content=css_str)
            # Wait for fonts/images to load
            page.wait_for_timeout(500)
            # Generate PDF — use width/height for landscape support
            if landscape:
                pdf_bytes = page.pdf(
                    width="297mm", height="210mm",
                    print_background=True,
                    margin=margin,
                )
            else:
                pdf_bytes = page.pdf(
                    format="A4",
                    print_background=True,
                    margin=margin,
                )
            return pdf_bytes
        finally:
            page.close()

    # ── Internal: lazy init helpers ─────────────────────────────────

    def _get_jinja_env(self) -> Environment:
        """Lazy-init Jinja2 Environment with FileSystemLoader."""
        if self._jinja_env is None:
            TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
            self._jinja_env = Environment(
                loader=FileSystemLoader(str(TEMPLATES_DIR)),
                autoescape=True,
                trim_blocks=True,
                lstrip_blocks=True,
            )
        return self._jinja_env

    def _get_css(self, template_name: str) -> str:
        """Read CSS files from disk — _base.css + {template_name}.css.

        Read fresh each call so CSS edits take effect without restart.
        """
        parts: list[str] = []
        for css_file in ("_base.css", f"{template_name}.css"):
            css_path = TEMPLATES_DIR / css_file
            if css_path.exists():
                parts.append(css_path.read_text(encoding="utf-8"))
        return "\n".join(parts)

    def _get_font_face_css(self) -> str:
        """Generate @font-face CSS with base64-embedded Rage Italic font."""
        font_path = FONTS_DIR / "RageItalic.ttf"
        if not font_path.exists():
            return ""
        font_bytes = font_path.read_bytes()
        b64 = base64.b64encode(font_bytes).decode("ascii")
        return f"""@font-face {{
    font-family: 'Rage Italic';
    src: url(data:font/truetype;charset=utf-8;base64,{b64}) format('truetype');
    font-weight: normal;
    font-style: italic;
}}"""

    def _get_browser(self):
        """Lazy-init Playwright browser (Chromium headless). Thread-safe."""
        with self._browser_lock:
            if self._browser is None:
                self._playwright = sync_playwright().start()
                self._browser = self._playwright.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-setuid-sandbox"],
                )
            return self._browser

    def _close_browser(self) -> None:
        """Close Playwright browser and stop playwright."""
        with self._browser_lock:
            if self._browser is not None:
                try:
                    self._browser.close()
                except Exception:
                    pass
                self._browser = None
            if self._playwright is not None:
                try:
                    self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None


    # ── Data contract validation ───────────────────────────────────

    @staticmethod
    def validate_context(schema: dict, context: dict) -> list[str]:
        """Check that all bound fields in the schema are present in the context.

        Returns a list of missing field names (empty = all good).
        """
        missing = []
        contract = schema.get("data_contract", {}).get("fields", {})
        for field_name in contract:
            if field_name not in context or context[field_name] is None:
                # Only warn if some element actually binds this field
                for el in schema.get("elements", []):
                    if el.get("bind") == field_name:
                        missing.append(field_name)
                        break
                    if el.get("type") == "table":
                        for col in el.get("columns", []):
                            if col.get("bind") == field_name:
                                missing.append(field_name)
                                break
        return missing

    @staticmethod
    def validate_and_log(schema: dict, context: dict) -> bool:
        """Validate context and log warnings. Returns True if no missing fields."""
        missing = PdfRendererService.validate_context(schema, context)
        if missing:
            logger.warning("Schema '%s' missing context fields: %s",
                           schema.get("name", "?"), missing)
        return len(missing) == 0

    @staticmethod
    def build_context_from_flat(schema: dict, metadata: dict, items: list[dict]) -> dict:
        """Build a schema-compatible context from flat metadata + items.

        This is the bridge between flat form/DB data and the nested structure
        that schemas expect. It reads the data_contract to know what to build.

        For simple text/image/signature binds: copies metadata[key] directly.
        For table binds: the caller must pre-build the rows in metadata
        (e.g. 'totals_rows', 'info_rows') or pass the raw items list.

        Args:
            schema: The JSON schema dict (must have data_contract).
            metadata: Flat dict of document-level fields.
            items: List of line item dicts (used for 'items' table bind).

        Returns:
            Context dict ready for render_schema().
        """
        contract = schema.get("data_contract", {}).get("fields", {})
        context: dict = {}

        for field_name, field_def in contract.items():
            field_type = field_def.get("type", "string")

            if field_type == "table":
                if field_name == "items":
                    # Enrich items with defaults
                    enriched = []
                    for i, item in enumerate(items):
                        it = dict(item)
                        if "item_number" not in it or not it.get("item_number"):
                            it["item_number"] = i + 1
                        if "amount" not in it or not it.get("amount"):
                            it["amount"] = (it.get("qty", 0) or 0) * (it.get("rate", 0) or 0)
                        if "cost" not in it or not it.get("cost"):
                            it["cost"] = (it.get("qty", 0) or 0) * (it.get("rate", 0) or 0)
                        it.setdefault("discount", 0)
                        it.setdefault("credit", 0)
                        enriched.append(it)
                    context[field_name] = enriched
                elif field_name == "totals_rows":
                    context[field_name] = metadata.get("totals_rows", [])
                elif field_name == "info_rows":
                    context[field_name] = metadata.get("info_rows", [])
                elif field_name == "project_rows":
                    context[field_name] = metadata.get("project_rows", [])
                elif field_name == "summary_rows":
                    context[field_name] = metadata.get("summary_rows", [])
                else:
                    context[field_name] = metadata.get(field_name, [])
            else:
                # string, image, signature — pass through from metadata
                context[field_name] = metadata.get(field_name, "")

        PdfRendererService.validate_and_log(schema, context)
        return context


# ── Jinja2 helpers (module-level, registered as globals during schema render) ──


def _style_to_css(style: dict | None) -> str:
    """Convert a style dict to a CSS string.

    CamelCase keys are converted to kebab-case:
      {"fontSize": "14pt", "fontWeight": "bold"} → "font-size: 14pt; font-weight: bold;"
    """
    if not style:
        return ""
    import re
    parts = []
    for key, val in style.items():
        css_key = re.sub(r"([A-Z])", r"-\1", key).lower()
        parts.append(f"{css_key}: {val}")
    return "; ".join(parts)


def _format_value(value, fmt_str: str | None) -> str:
    """Format a value using a printf-style format string."""
    if value is None:
        return ""
    if fmt_str:
        try:
            return fmt_str % value
        except Exception:
            return str(value)
    return str(value)


# ── Schema context builders ──────────────────────────────────────────────


def build_po_schema_context(metadata: dict, items: list[dict]) -> dict:
    """Transform flat PO form data into schema-compatible context.

    The PO JSON schema expects structured context with rows for info tables,
    totals tables, etc. This function builds that from the flat test-page form data.
    """
    ref = metadata.get("reference_number", "PO-XXXX")
    vendor = metadata.get("vendor_name", "")
    order_num = metadata.get("order_number", "")
    order_date = metadata.get("order_date", "")
    creditor_phone = metadata.get("creditor_phone", "")
    creditor_code = metadata.get("creditor_code", "")

    # Info table rows
    info_rows = [
        {
            "label": "Supplier",
            "value": vendor,
            "note": (
                "Order Number must be quoted on Delivery Dockets and Invoices.\n\n"
                "All Enquiries and Correspondence must be addressed to:\n"
                "✉ admin@welink.com.au\n"
                "☏ (02) 9979 8200"
            ),
            "field_label": f"Order No: {order_num}\nDate: {order_date}\nCreditor Phone: {creditor_phone}\nCreditor Code: {creditor_code}",
        },
        {
            "label": "Address",
            "value": metadata.get("vendor_address_line1", ""),
            "note": "",
            "field_label": "",
        },
        {
            "label": "",
            "value": metadata.get("vendor_address_line2", ""),
            "note": "",
            "field_label": "",
        },
        {
            "label": "",
            "value": f"ABN: {metadata.get('vendor_abn', '')}",
            "note": "",
            "field_label": "",
        },
    ]

    # Project info rows
    proj = metadata.get("project_name", "")
    loc = metadata.get("project_location", "")
    project_rows = [
        {
            "label": "Project:",
            "value": f"{proj} — {loc}" if loc else proj,
            "label2": "Project Code:",
            "value2": metadata.get("project_code", ""),
        },
        {
            "label": "Delivery Instructions:",
            "value": metadata.get("delivery_instructions", ""),
            "label2": "Delivery date required:",
            "value2": metadata.get("delivery_date", ""),
        },
        {
            "label": "Attention:",
            "value": metadata.get("attention", ""),
            "label2": "",
            "value2": "",
        },
    ]

    # Items — add item_number if missing
    enriched_items = []
    for i, item in enumerate(items):
        it = dict(item)
        if "item_number" not in it or not it["item_number"]:
            it["item_number"] = i + 1
        if "amount" not in it or not it["amount"]:
            it["amount"] = (it.get("qty", 0) or 0) * (it.get("rate", 0) or 0)
        if "discount" not in it:
            it["discount"] = 0
        enriched_items.append(it)

    # Totals
    totals = calculate_po_totals(items)
    totals_rows = [
        {"label": "Total $", "value": f"${totals['total_ex_gst']:,.2f}"},
        {"label": "GST", "value": f"${totals['gst']:,.2f}"},
        {"label": "Gross", "value": f"${totals['gross']:,.2f}"},
    ]

    # Logo
    logo = ""
    if LOGO_PATH.exists():
        logo = str(LOGO_PATH.resolve()).replace("\\", "/")

    return {
        "logo_path": logo,
        "company_name": "Welink Construction Pty Ltd",
        "company_address": "",  # could be from metadata
        "company_abn": "92 623 700 269",
        "title": "PURCHASE ORDER",
        "info_rows": info_rows,
        "scope_text": (
            "The Supplier agrees to supply the Goods and/or Services described below "
            "strictly in accordance with the terms and conditions set out in this "
            "Purchase Order (including any special conditions)."
        ),
        "project_rows": project_rows,
        "items": enriched_items,
        "totals_rows": totals_rows,
        "special_instructions_label": "Special Instructions:",
        "special_instructions": metadata.get("special_instructions", ""),
        "footer_text": (
            f"PURCHASING OFFICER    |    Requested by <<REQ>>    |    "
            f"Approved by {metadata.get('approved_by', 'ACHEN')}"
        ),
        "terms_title": "PURCHASE ORDER TERMS & CONDITIONS",
        "terms_body": "\n\n".join(
            f"{c['number']} {c['title']}\n{c['body']}" for c in PO_TERMS
        ),
    }


def build_vo_schema_context(metadata: dict, items: list[dict]) -> dict:
    """Transform flat VO form data into schema-compatible context."""
    vo_num = metadata.get("vo_number", 1)
    vo_title = metadata.get("vo_title", "")
    is_est = metadata.get("is_estimate", False)

    # Title
    title = "CONTRACT VARIATION"
    if is_est:
        title += " — ESTIMATE"

    # Project info rows
    project_rows = [
        {
            "label": "PROJECT:",
            "value": metadata.get("project_name", ""),
            "label2": "Date:",
            "value2": metadata.get("date_issued", ""),
        },
        {
            "label": "NAME:",
            "value": metadata.get("company_name", "Welink Construction Pty Ltd"),
            "label2": "Site Instruction / Ref:",
            "value2": metadata.get("site_instruction_ref", ""),
        },
        {
            "label": "SITE ADDRESS:",
            "value": metadata.get("project_location", ""),
            "label2": "",
            "value2": "",
        },
        {
            "label": "JOB No:",
            "value": metadata.get("job_number", ""),
            "label2": "",
            "value2": "",
        },
    ]

    # Items
    enriched_items = []
    for i, item in enumerate(items):
        it = dict(item)
        if "item_number" not in it or not it["item_number"]:
            it["item_number"] = i + 1
        if "cost" not in it or not it["cost"]:
            it["cost"] = (it.get("qty", 0) or 0) * (it.get("rate", 0) or 0)
        if "credit" not in it:
            it["credit"] = 0
        enriched_items.append(it)

    # Summary
    costs = calculate_vo_costs(items)
    summary_rows = [
        {"label": "SUB TOTAL", "value": f"${costs['sub_total_cost']:,.2f}"},
        {"label": "Less Credits", "value": f"${costs['sub_total_credit']:,.2f}"},
        {"label": "NETT VARIATION COST", "value": f"${costs['nett_variation_cost']:,.2f}"},
        {"label": "MARGIN AND OVERHEAD COSTS (10%)", "value": f"${costs['margin']:,.2f}"},
        {"label": "VARIATION COST EXCLUDING GST", "value": f"${costs['excl_gst']:,.2f}"},
        {"label": "GST", "value": f"${costs['gst']:,.2f}"},
        {"label": "TOTAL INCLUDING GST", "value": f"${costs['total_incl_gst']:,.2f}"},
    ]

    return {
        "title_text": title,
        "project_rows": project_rows,
        "vo_header": f"VO{vo_num} — {vo_title}",
        "items": enriched_items,
        "summary_rows": summary_rows,
        "raised_by_label": "Variation Raised By:",
        "initials": metadata.get("initials", "AC"),
        "client_acceptance_label": "ACCEPTED FOR AND ON BEHALF OF CLIENT:",
    }


# ── Convenience function ─────────────────────────────────────────────────


def render_pdf(template_name: str, context: dict) -> bytes:
    """One-shot PDF rendering without instantiating the service manually.

    Usage:
        pdf_bytes = render_pdf("po", {"company_name": "...", ...})
    """
    svc = PdfRendererService()
    return svc.render_sync(template_name, context)
