"""Client Variation REST API — wraps VariationService as HTTP endpoints."""

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path

router = APIRouter(prefix="/api/variations", tags=["Variations"])

# Lazy-init service (singleton)
_service = None


def _get_service():
    global _service
    if _service is None:
        from shared_tools.variation_service import VariationService
        _service = VariationService()
        _service.start()
    return _service


# ── Schemas ────────────────────────────────────────────────────────────


class VariationCreate(BaseModel):
    project_entry_id: str = ""
    project_name: str = ""
    project_location: str = ""
    job_number: str = ""
    base_contract_amount: float = 0
    vo_number: int | None = None
    vo_title: str = ""
    vo_type: str = "Head Contract VO"
    is_estimate: bool = False
    date_issued: str | None = None
    site_instruction_ref: str = ""
    company_name: str = "Welink Construction"
    source_email_entry_id: str | None = None
    approved_value: float | None = None
    not_approved_value: float | None = None
    approval_type: str | None = None
    sort_order: int | None = None


class VariationUpdate(BaseModel):
    project_name: str | None = None
    project_location: str | None = None
    job_number: str | None = None
    base_contract_amount: float | None = None
    vo_title: str | None = None
    vo_type: str | None = None
    is_estimate: bool | None = None
    date_issued: str | None = None
    site_instruction_ref: str | None = None
    status: str | None = None
    approved_value: float | None = None
    not_approved_value: float | None = None
    approval_type: str | None = None
    sort_order: int | None = None


class ItemCreate(BaseModel):
    item_number: int = 1
    description: str = ""
    qty: float = 0
    unit: str = "item"
    rate: float = 0
    credit: float = 0


class ItemUpdate(BaseModel):
    item_number: int | None = None
    description: str | None = None
    qty: float | None = None
    unit: str | None = None
    rate: float | None = None
    cost: float | None = None
    credit: float | None = None


class ReorderRequest(BaseModel):
    item_ids: list[int]


class SendEmailRequest(BaseModel):
    to: str
    cc: str = ""
    subject: str = ""
    body: str = ""


# ── Routes ─────────────────────────────────────────────────────────────


@router.get("")
async def list_variations(project_entry_id: str = Query(""), project: str = Query(None),
                          status: str = Query(None)):
    """List variations, scoped to a project_entry_id. Optional status filter."""
    svc = _get_service()
    variations = svc.list_variations(project_entry_id=project_entry_id, project=project, status=status)
    return {"count": len(variations), "variations": variations}


@router.get("/next-vo-number")
async def next_vo_number(project_entry_id: str = Query("")):
    """Get the next VO number for a project (count of active VOs + 1)."""
    svc = _get_service()
    num = svc.get_next_vo_number(project_entry_id)
    return {"vo_number": num}


@router.get("/{entry_id}")
async def get_variation(entry_id: str):
    """Get a single variation with all line items."""
    svc = _get_service()
    var = svc.get_variation(entry_id)
    if not var:
        return {"error": "Variation not found"}
    return var


@router.post("")
async def create_variation(data: VariationCreate):
    """Create a new variation. Returns the entry_id."""
    svc = _get_service()
    entry_id = svc.create_variation(data.model_dump())
    return {"entry_id": entry_id}


@router.patch("/{entry_id}")
async def update_variation(entry_id: str, data: VariationUpdate):
    """Partially update a variation. Only provided fields are changed."""
    svc = _get_service()
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        return {"error": "No fields to update"}
    ok = svc.update_variation(entry_id, **fields)
    return {"ok": ok}


@router.delete("/{entry_id}")
async def delete_variation(entry_id: str):
    """Soft-delete a variation (status → 'void')."""
    svc = _get_service()
    ok = svc.delete_variation(entry_id)
    return {"ok": ok}


@router.post("/{entry_id}/restore")
async def restore_variation(entry_id: str):
    """Restore a voided variation back to draft."""
    from shared_tools.variation_db import restore_variation as db_restore
    ok = db_restore(entry_id)
    return {"ok": ok}


@router.delete("/{entry_id}/permanent")
async def permanent_delete_variation(entry_id: str):
    """Permanently delete a variation and all its items."""
    from shared_tools.variation_db import hard_delete_variation
    ok = hard_delete_variation(entry_id)
    return {"ok": ok}


class ReorderRequest(BaseModel):
    ordered_ids: list[str]


@router.put("/reorder")
async def reorder_variations(data: ReorderRequest):
    """Update sort_order for a list of variations based on their position in the list."""
    from shared_tools.variation_db import reorder_variations as db_reorder
    ok = db_reorder(data.ordered_ids)
    return {"ok": ok}


# ── Line Items ─────────────────────────────────────────────────────────


@router.post("/{entry_id}/items")
async def add_item(entry_id: str, data: ItemCreate):
    """Add a line item to a variation."""
    svc = _get_service()
    item_data = data.model_dump()
    item_id = svc.add_item(entry_id, item_data)
    return {"ok": item_id is not None, "item_id": item_id}


@router.patch("/{entry_id}/items/{item_id}")
async def update_item(entry_id: str, item_id: int, data: ItemUpdate):
    """Update a line item."""
    svc = _get_service()
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        return {"error": "No fields to update"}
    ok = svc.update_item(item_id, **fields)
    return {"ok": ok}


@router.delete("/{entry_id}/items/{item_id}")
async def remove_item(entry_id: str, item_id: int):
    """Delete a line item."""
    svc = _get_service()
    ok = svc.remove_item(item_id, entry_id)
    return {"ok": ok}


@router.post("/{entry_id}/export-single-pdf")
async def export_single_vo_pdf(entry_id: str):
    """Generate a PDF for a single VO and return it for download."""
    import tempfile
    from pathlib import Path
    from shared_tools.variation_db import get_variation, get_variation_items
    from shared_tools.variation_template import TemplateMapping, VariationExcelBuilder, calculate_variation_costs

    var = get_variation(entry_id)
    if not var:
        return {"error": "Variation not found"}

    items = get_variation_items(entry_id)

    # Get project info for the header
    from shared_tools.variation_db import get_project
    proj = get_project(var.get("project_entry_id", "")) if var.get("project_entry_id") else None

    # Load mapping and template
    try:
        mapping_path = Path(__file__).parent.parent.parent / "knowledge" / "variation_template_mapping.yaml"
        if mapping_path.exists():
            import yaml
            with open(mapping_path, "r") as f:
                mapping = TemplateMapping(yaml.safe_load(f))
        else:
            mapping = TemplateMapping.default()
    except Exception:
        mapping = TemplateMapping.default()

    knowledge_dir = Path(__file__).parent.parent.parent / "knowledge"
    cleaned = knowledge_dir / "variation_template.xlsx"
    original = knowledge_dir / "drafted simple workflow" / "20260602 47CBR - Welink Construction Client Variations.xlsx"
    template_path = cleaned if cleaned.exists() else original

    if not template_path.exists():
        return {"error": "Template not found"}

    # Build a temp xlsx with just this one VO
    builder = VariationExcelBuilder(mapping, template_path)
    builder.open()
    vo_number = var.get("vo_number", 1) or 1
    sheet_name = builder.create_vo_sheet(vo_number)
    ws = builder.wb[sheet_name]

    # Use project-level fields if available
    var_for_sheet = dict(var)
    if proj:
        var_for_sheet["project_name"] = proj.get("name", var.get("project_name", ""))
        var_for_sheet["project_location"] = proj.get("location", var.get("project_location", ""))
        var_for_sheet["job_number"] = proj.get("job_number", var.get("job_number", ""))
        var_for_sheet["company_name"] = proj.get("company_name", "Welink Construction")

    builder.build_vo_sheet(ws, var_for_sheet, items, initials=var.get("raised_by", "AC"))

    # Copy logo images from VOXX and set fonts
    from shared_tools.variation_template import _extract_images as _ext, _add_image_to_sheet as _add_img, _set_all_fonts_arial
    if "VOXX" in builder.wb.sheetnames:
        for img_info in _ext(builder.wb["VOXX"]):
            _add_img(ws, img_info)
    _set_all_fonts_arial(builder.wb)

    # Remove other sheets, keep just this VO
    for name in list(builder.wb.sheetnames):
        if name != sheet_name:
            del builder.wb[name]

    # Save temp xlsx
    tmp_xlsx = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
    tmp_xlsx.close()
    builder.save(Path(tmp_xlsx.name))
    builder.close()

    # Convert to PDF
    svc = _get_service()
    try:
        pdf_path = svc._excel_to_pdf(tmp_xlsx.name, {"vo_number": vo_number})
        if pdf_path.exists():
            from fastapi.responses import FileResponse
            import re
            # Build filename: ProjectName_VO{number}_Description.pdf
            project_name = var_for_sheet.get("project_name", "Project")
            vo_num = var.get("vo_number", 1) or 1
            vo_title = var.get("vo_title", "")
            # Extract description from vo_title (strip "VO{num} - " prefix)
            desc_match = re.match(r'VO\d+\s*[-–—]\s*(.+)', vo_title)
            description = desc_match.group(1) if desc_match else vo_title
            # Sanitize: remove characters invalid in Windows filenames
            safe_desc = re.sub(r'[<>:"/\\|?*]', '', description).strip()
            safe_project = re.sub(r'[<>:"/\\|?*]', '', project_name).strip()
            download_name = f"{safe_project}_VO{vo_num}_{safe_desc}.pdf" if safe_desc else f"{safe_project}_VO{vo_num}.pdf"
            return FileResponse(pdf_path, media_type="application/pdf",
                               filename=download_name)
    except Exception:
        pass
    finally:
        try:
            Path(tmp_xlsx.name).unlink(missing_ok=True)
        except Exception:
            pass

    return {"error": "PDF export failed — Excel COM required (Windows only)"}


@router.put("/{entry_id}/items/reorder")
async def reorder_items(entry_id: str, data: ReorderRequest):
    """Reorder line items."""
    svc = _get_service()
    ok = svc.reorder_items(entry_id, data.item_ids)
    return {"ok": ok}


# ── Calculations ───────────────────────────────────────────────────────


@router.post("/{entry_id}/calculate")
async def calculate_costs(entry_id: str):
    """Run deterministic cost calculation. No LLM involved."""
    svc = _get_service()
    result = svc.calculate_costs(entry_id)
    return result


# ── Excel & PDF ────────────────────────────────────────────────────────


@router.post("/{entry_id}/generate-excel")
async def generate_excel(entry_id: str):
    """Generate the variation Excel workbook (async).
    Poll GET /{entry_id} to check excel_path for completion."""
    svc = _get_service()
    svc.generate_excel(entry_id)
    return {"status": "started", "message": "Excel generation queued"}


@router.post("/{entry_id}/export-pdf")
async def export_pdf(entry_id: str):
    """Export the variation to PDF (async). Requires Excel to exist first.
    Poll GET /{entry_id} to check pdf_path for completion."""
    svc = _get_service()
    svc.export_pdf(entry_id)
    return {"status": "started", "message": "PDF export queued"}


@router.get("/{entry_id}/download/excel")
async def download_excel(entry_id: str):
    """Download the generated Excel file."""
    svc = _get_service()
    var = svc.get_variation(entry_id)
    if not var or not var.get("excel_path"):
        return {"error": "Excel not generated yet"}
    path = Path(var["excel_path"])
    if not path.exists():
        return {"error": "Excel file not found on disk"}
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=path.name,
    )


@router.get("/{entry_id}/download/pdf")
async def download_pdf(entry_id: str):
    """Download the generated PDF file."""
    svc = _get_service()
    var = svc.get_variation(entry_id)
    if not var or not var.get("pdf_path"):
        return {"error": "PDF not generated yet"}
    path = Path(var["pdf_path"])
    if not path.exists():
        return {"error": "PDF file not found on disk"}
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=path.name,
    )


# ── Email ──────────────────────────────────────────────────────────────


@router.post("/{entry_id}/generate-email")
async def generate_email(entry_id: str):
    """Generate a submission email draft (LLM-assisted). Async.
    Poll GET /{entry_id}/email-draft to get the result."""
    svc = _get_service()
    svc.generate_submission_email(entry_id)
    return {"status": "started", "message": "Email generation queued"}


@router.post("/{entry_id}/send")
async def send_email(entry_id: str, data: SendEmailRequest):
    """Send the submission email via Outlook."""
    svc = _get_service()
    ok = svc.send_submission_email(
        entry_id,
        to_recipients=data.to,
        cc_recipients=data.cc,
        subject=data.subject,
        body=data.body,
    )
    return {"ok": ok}


# ── Create from Email ──────────────────────────────────────────────────


@router.post("/from-email/{email_entry_id}")
async def create_from_email(email_entry_id: str):
    """Parse an email and create a pre-filled variation."""
    svc = _get_service()
    entry_id = svc.create_from_email(email_entry_id)
    if entry_id:
        return {"entry_id": entry_id}
    return {"error": "Failed to create variation from email"}
