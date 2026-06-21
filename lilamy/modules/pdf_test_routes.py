"""PDF Test routes — HTML-to-PDF generation test tool.

Serves the standalone test page at /test/pdf and provides a
POST endpoint for on-the-fly PDF generation from form data.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from shared_tools.pdf_renderer.pdf_renderer_service import (
    PdfRendererService,
    calculate_po_totals,
    calculate_vo_costs,
    PROJECT_ROOT,
    _style_to_css,
    _format_value,
)

router = APIRouter(prefix="", tags=["PDF Test"])

# ── Paths ───────────────────────────────────────────────────────────────

STATIC_DIR = Path(__file__).parent.parent / "static"
LOGO_PATH = PROJECT_ROOT / "knowledge" / "welink_logo.jpeg"

# ── Lazy-init renderer ─────────────────────────────────────────────────

_renderer: PdfRendererService | None = None


def _get_renderer() -> PdfRendererService:
    """Lazy-init the PdfRendererService (singleton)."""
    global _renderer
    if _renderer is None:
        _renderer = PdfRendererService()
    return _renderer


# ── Schemas ─────────────────────────────────────────────────────────────


class TestPdfRequest(BaseModel):
    """Request body for POST /api/test/pdf/generate."""
    doc_type: str  # "po" or "vo"
    metadata: dict[str, Any]
    items: list[dict[str, Any]]


# ── Routes ──────────────────────────────────────────────────────────────


@router.get("/test/pdf")
async def serve_pdf_test():
    """Serve the standalone PDF test page."""
    html_path = STATIC_DIR / "test" / "pdf_test.html"
    if not html_path.exists():
        return Response(
            content="<h1>PDF test page not found</h1>",
            media_type="text/html",
            status_code=404,
        )
    return FileResponse(html_path)


@router.post("/api/test/pdf/generate")
async def generate_test_pdf(data: TestPdfRequest):
    """Generate a PDF from the submitted form data.

    Accepts:
      - doc_type: "po" | "vo"
      - metadata: dict of document fields
      - items: list of line item dicts

    Returns the rendered PDF as a downloadable file.
    """
    doc_type = data.doc_type.lower()
    if doc_type not in ("po", "vo"):
        return Response(
            content='{"error": "doc_type must be po or vo"}',
            media_type="application/json",
            status_code=400,
        )

    try:
        context = dict(data.metadata)
        context["items"] = data.items

        if doc_type == "po":
            # Calculate PO totals
            totals = calculate_po_totals(data.items)
            context.update(totals)

            # Logo path (file:// URL for Playwright/Chromium)
            if LOGO_PATH.exists():
                context["logo_path"] = str(LOGO_PATH.resolve()).replace("\\", "/")
            else:
                context["logo_path"] = ""

            # Terms & Conditions
            from shared_tools.pdf_renderer.pdf_renderer_service import PO_TERMS
            context["terms"] = PO_TERMS

            template_name = "po"
            filename = f"PO_{context.get('reference_number', 'export')}.pdf"

        else:  # vo
            # Calculate VO costs
            costs = calculate_vo_costs(data.items)
            context.update(costs)

            # Landscape for VO
            context["_landscape"] = True

            template_name = "vo"
            vo_num = context.get("vo_number", "")
            proj = context.get("project_name", "export")
            filename = f"VO{vo_num}_{_sanitize_filename(proj)}.pdf"

        # Render PDF in a thread to avoid Playwright Sync API / asyncio conflict
        renderer = _get_renderer()
        pdf_bytes = await asyncio.to_thread(renderer.render_sync, template_name, context)

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(len(pdf_bytes)),
            },
        )

    except Exception as e:
        return Response(
            content=f'{{"error": "PDF generation failed: {str(e)}"}}',
            media_type="application/json",
            status_code=500,
        )


@router.post("/api/test/pdf/preview")
async def preview_schema(data: dict):
    """Render schema to HTML for iframe preview. Returns HTML, not PDF."""
    import copy
    try:
        schema = data.get("schema", {})
        context = data.get("context", {})
        if not schema:
            return Response(content='{"error":"schema required"}', media_type="application/json", status_code=400)

        schema = copy.deepcopy(schema)
        page = schema.get("page", {})
        is_landscape = page.get("orientation") == "landscape"
        page_width = 297 if is_landscape else 210
        page_height = 210 if is_landscape else 297

        # Resolve positions
        renderer = _get_renderer()
        elements = renderer._resolve_positions(schema.get("elements", []), page_height)
        schema["elements"] = elements

        # Render HTML via Jinja2
        env = renderer._get_jinja_env()
        env.globals["_style_str"] = _style_to_css
        env.globals["_fmt"] = _format_value
        template = env.get_template("_generic.html")
        html = template.render(
            schema=schema,
            page_width=page_width,
            page_height=page_height,
            elements=elements,
            context=context,
        )
        env.globals.pop("_style_str", None)
        env.globals.pop("_fmt", None)

        # Inject element selector script
        selector_js = "<script>document.querySelectorAll('[data-el-id]').forEach(el=>{el.style.cursor='pointer';el.addEventListener('click',e=>{e.stopPropagation();window.parent.postMessage({type:'selectEl',elId:el.dataset.elId},'*')})})</script>"
        html = html.replace("</body>", selector_js + "</body>")

        return Response(content=html, media_type="text/html")
    except Exception as e:
        return Response(content=f'{{"error":"{str(e)}"}}', media_type="application/json", status_code=500)


@router.post("/api/test/pdf/generate-schema")
async def generate_from_schema(data: dict):
    """Generate a PDF from a JSON schema template directly."""
    try:
        schema = data.get("schema")
        context = data.get("context", {})
        if not schema:
            return Response(content='{"error":"schema required"}', media_type="application/json", status_code=400)

        renderer = _get_renderer()
        pdf_bytes = await asyncio.to_thread(renderer.render_schema, schema, context)

        name = schema.get("name", "output")
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{_sanitize_filename(name)}.pdf"',
                "Content-Length": str(len(pdf_bytes)),
            },
        )
    except Exception as e:
        return Response(content=f'{{"error":"{str(e)}"}}', media_type="application/json", status_code=500)


# ── Template management ──────────────────────────────────────────────────

import json as _json
from shared_tools.pdf_renderer.pdf_renderer_service import TEMPLATES_DIR


@router.get("/api/test/pdf/template/list")
async def list_templates():
    """List available JSON schema templates."""
    try:
        templates = sorted(
            p.name for p in TEMPLATES_DIR.glob("*.json")
        )
        return {"templates": templates}
    except Exception as e:
        return {"templates": [], "error": str(e)}


@router.get("/api/test/pdf/template/{name}")
async def load_template(name: str):
    """Load a JSON schema template by name."""
    try:
        schema_path = TEMPLATES_DIR / name
        if not schema_path.exists() and not name.endswith(".json"):
            schema_path = TEMPLATES_DIR / f"{name}.json"
        if not schema_path.exists():
            return Response(content='{"error":"Template not found"}', media_type="application/json", status_code=404)
        return _json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception as e:
        return Response(content=f'{{"error":"{str(e)}"}}', media_type="application/json", status_code=500)


@router.post("/api/test/pdf/template/save")
async def save_template(data: dict):
    """Save a JSON schema template to disk."""
    try:
        name = data.get("name", "untitled").replace(".json", "")
        schema = data.get("schema")
        if not schema:
            return Response(content='{"error":"schema required"}', media_type="application/json", status_code=400)

        schema["name"] = name
        out_path = TEMPLATES_DIR / f"{name}.json"
        out_path.write_text(_json.dumps(schema, indent=2, default=str), encoding="utf-8")
        return {"status": "saved", "name": name, "path": str(out_path)}
    except Exception as e:
        return Response(content=f'{{"error":"{str(e)}"}}', media_type="application/json", status_code=500)


# ── Helpers ─────────────────────────────────────────────────────────────


def _sanitize_filename(name: str) -> str:
    """Remove characters unsafe for filenames."""
    return re.sub(r"[^a-zA-Z0-9_\- ]", "", name).replace(" ", "_")[:80]
