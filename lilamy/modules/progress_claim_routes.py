"""Client Progress Claim REST API — wraps ProgressClaimService as HTTP endpoints.

Routes are grouped under /api/progress-claims and cover:
  - project CRUD
  - cashflow import (upload) + read + per-cell progress edit
  - claim generation + list + detail
  - Excel / PDF export + download
"""
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Query, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api/progress-claims", tags=["Progress Claims"])

# Lazy-init service (singleton)
_service = None


def _get_service():
    global _service
    if _service is None:
        from shared_tools.progress_claim.progress_claim_service import ProgressClaimService
        _service = ProgressClaimService()
        _service.start()
    return _service


# ── Schemas ────────────────────────────────────────────────────────────


class ProjectCreate(BaseModel):
    name: str = ""
    job_number: str = ""
    location: str = ""
    client: str = ""
    client_contact: str = ""
    superintendent: str = ""
    company_name: str = "Welink Construction"
    company_abn: str = ""
    company_address: str = ""
    base_contract_amount: float = 0


class ProjectUpdate(BaseModel):
    name: str | None = None
    job_number: str | None = None
    location: str | None = None
    site_address: str | None = None
    client: str | None = None
    client_contact: str | None = None
    superintendent: str | None = None
    company_name: str | None = None
    company_abn: str | None = None
    company_address: str | None = None
    base_contract_amount: float | None = None


class ProgressUpdate(BaseModel):
    percentage: float | None = None
    amount: float | None = None


class ClaimGenerate(BaseModel):
    claim_month: str
    claim_date: str | None = None


class ImportPathRequest(BaseModel):
    xlsx_path: str


class AddSectionRequest(BaseModel):
    label: str = ""
    claimable: bool = True
    section_type: str = "normal"


class UpdateSectionRequest(BaseModel):
    label: str | None = None
    claimable: bool | None = None


class UpdateClaimSummaryRequest(BaseModel):
    claim_number: int | None = None
    section_totals: dict[str, float] | None = None
    less_previous_claims: float | None = None
    retention_amount: float | None = None


class AddMonthRequest(BaseModel):
    month_key: str | None = None


class AddWorkItemRequest(BaseModel):
    section: str
    description: str = ""
    cost: float = 0


class UpdateWorkItemRequest(BaseModel):
    description: str | None = None
    cost: float | None = None


class UpdateClaimItemRequest(BaseModel):
    description: str | None = None
    cost: float | None = None
    cumulative_percentage: float | None = None
    total_claimed: float | None = None
    previously_claimed: float | None = None
    current_claim: float | None = None
    balance_remaining: float | None = None


# ── Projects ───────────────────────────────────────────────────────────


@router.get("/projects")
async def list_projects():
    """List all progress-claim projects."""
    svc = _get_service()
    return {"projects": svc.list_projects()}


@router.get("/projects/{entry_id}")
async def get_project(entry_id: str):
    svc = _get_service()
    p = svc.get_project(entry_id)
    if not p:
        return {"error": "Project not found"}
    return p


@router.post("/projects")
async def create_project(data: ProjectCreate):
    svc = _get_service()
    entry_id = svc.create_project(data.model_dump())
    return {"entry_id": entry_id}


@router.patch("/projects/{entry_id}")
async def update_project(entry_id: str, data: ProjectUpdate):
    svc = _get_service()
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        return {"error": "No fields to update"}
    ok = svc.update_project(entry_id, **fields)
    return {"ok": ok}


@router.delete("/projects/{entry_id}")
async def delete_project(entry_id: str):
    svc = _get_service()
    return {"ok": svc.delete_project(entry_id)}


# ── Cashflow ───────────────────────────────────────────────────────────


@router.post("/projects/import-upload")
async def import_cashflow_upload(file: UploadFile = File(...)):
    """Upload a cashflow xlsx → save → import → auto-create project.

    The cashflow header (project name, client, job#) is extracted and a project
    record is created. Returns the new project's entry_id plus summary stats.
    """
    if not file.filename or not file.filename.endswith(".xlsx"):
        return {"error": "Only .xlsx files are accepted"}

    svc = _get_service()
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = tmp.name

        # Keep a permanent copy under data/progress_claims_sources/
        from shared_tools.progress_claim.progress_claim_db import DATA_DIR
        dest_dir = DATA_DIR / "progress_claims_sources"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / Path(file.filename).name
        shutil.copy2(tmp_path, str(dest_path))

        # Run the import synchronously (fast) so we can return the project id
        svc._handle_import_cashflow("", str(dest_path), auto_create_project=True)
        projects = svc.list_projects()
        if not projects:
            return {"error": "Import failed"}
        project = projects[0]  # most recently created
        items = svc.get_cashflow(project["entry_id"])
        return {
            "entry_id": project["entry_id"],
            "project": project,
            "item_count": sum(len(v) for v in items.get("sections", {}).values()),
            "month_count": len(items.get("months", [])),
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass


@router.post("/projects/{entry_id}/cashflow/import")
async def import_cashflow_path(entry_id: str, data: ImportPathRequest):
    """Import a cashflow xlsx from a server-side path into an existing project."""
    svc = _get_service()
    try:
        svc._handle_import_cashflow(entry_id, data.xlsx_path, auto_create_project=False)
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}


@router.get("/projects/{entry_id}/cashflow")
async def get_cashflow(entry_id: str):
    """Return the full cashflow state (sections, items, months, progress grid)."""
    svc = _get_service()
    return svc.get_cashflow(entry_id)


@router.patch("/projects/{entry_id}/cashflow/progress/{work_item_id}/{month_id}")
async def update_progress(entry_id: str, work_item_id: int, month_id: int,
                          data: ProgressUpdate):
    """Update a single cell. Accepts percentage OR amount (mutual sync).
    Queues the update; returns immediately."""
    svc = _get_service()
    if data.percentage is None and data.amount is None:
        return {"error": "Provide percentage or amount"}
    svc.update_progress(entry_id, work_item_id, month_id,
                        percentage=data.percentage, amount=data.amount)
    return {"ok": True}


# ── Cashflow drafting: months + work items ──────────────────────────────


@router.post("/projects/{entry_id}/cashflow/months")
async def add_month(entry_id: str, data: AddMonthRequest):
    """Append a new month column (defaults to the next calendar month)."""
    svc = _get_service()
    try:
        month = svc.add_month(entry_id, data.month_key)
        if not month:
            return {"error": "Failed to add month"}
        return {"ok": True, "month": month}
    except Exception as e:
        return {"error": str(e)}


@router.delete("/projects/{entry_id}/cashflow/months/{month_id}")
async def remove_month(entry_id: str, month_id: int):
    """Remove a month column and all its progress."""
    svc = _get_service()
    return {"ok": svc.remove_month(entry_id, month_id)}


@router.post("/projects/{entry_id}/cashflow/items")
async def add_work_item(entry_id: str, data: AddWorkItemRequest):
    """Add a work item to a section (D/E drafting)."""
    svc = _get_service()
    try:
        item = svc.add_work_item(entry_id, data.section, data.description, data.cost)
        if not item:
            return {"error": "Failed to add work item"}
        return {"ok": True, "item": item}
    except Exception as e:
        return {"error": str(e)}


@router.patch("/projects/{entry_id}/cashflow/items/{item_id}")
async def update_work_item(entry_id: str, item_id: int, data: UpdateWorkItemRequest):
    """Update a work item's description / cost."""
    svc = _get_service()
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        return {"error": "No fields to update"}
    return {"ok": svc.update_work_item(item_id, **fields)}


@router.delete("/projects/{entry_id}/cashflow/items/{item_id}")
async def remove_work_item(entry_id: str, item_id: int):
    """Remove a work item and all its progress."""
    svc = _get_service()
    try:
        ok = svc.remove_work_item(item_id)
        if not ok:
            return {"error": "Item not found"}
        return {"ok": True}
    except ValueError as e:
        return {"error": str(e)}


# ── Cashflow sections (freeform add/remove/rename) ──────────────────────


@router.post("/projects/{entry_id}/cashflow/sections")
async def add_section(entry_id: str, data: AddSectionRequest):
    """Add a new cashflow section."""
    svc = _get_service()
    try:
        section = svc.add_section(entry_id, data.label, data.claimable, data.section_type)
        if not section:
            return {"error": "Failed to add section"}
        return {"ok": True, "section": section}
    except Exception as e:
        return {"error": str(e)}


@router.patch("/projects/{entry_id}/cashflow/sections/{section_code}")
async def update_section(entry_id: str, section_code: str, data: UpdateSectionRequest):
    """Rename a section and/or toggle claimable."""
    svc = _get_service()
    if data.label is not None:
        svc.rename_section(entry_id, section_code, data.label)
    if data.claimable is not None:
        svc.set_section_claimable(entry_id, section_code, data.claimable)
    return {"ok": True}


@router.delete("/projects/{entry_id}/cashflow/sections/{section_code}")
async def remove_section(entry_id: str, section_code: str):
    """Remove a section and all its items + progress."""
    svc = _get_service()
    return {"ok": svc.remove_section(entry_id, section_code)}


@router.post("/projects/{entry_id}/cashflow/push-excel")
async def push_cashflow_to_excel(entry_id: str):
    """Regenerate the imported cashflow Excel from the DB (old file backed up)."""
    svc = _get_service()
    return svc.push_to_excel(entry_id)


# ── Claims ─────────────────────────────────────────────────────────────


@router.get("/projects/{entry_id}/claims")
async def list_claims(entry_id: str):
    svc = _get_service()
    return {"claims": svc.list_claims(entry_id)}


@router.post("/projects/{entry_id}/claims")
async def generate_claim(entry_id: str, data: ClaimGenerate):
    """Generate (or regenerate) a progress claim for the given month."""
    svc = _get_service()
    try:
        claim_entry_id = svc.generate_claim(entry_id, data.claim_month, data.claim_date)
        if not claim_entry_id:
            return {"error": "Claim generation failed"}
        return {"entry_id": claim_entry_id}
    except Exception as e:
        return {"error": str(e)}


@router.get("/claims/{claim_entry_id}")
async def get_claim(claim_entry_id: str):
    svc = _get_service()
    summary = svc.get_claim_summary(claim_entry_id)
    if not summary:
        return {"error": "Claim not found"}
    return summary


@router.delete("/claims/{claim_entry_id}")
async def delete_claim(claim_entry_id: str):
    svc = _get_service()
    return {"ok": svc.delete_claim(claim_entry_id)}


@router.patch("/claims/{claim_entry_id}/items/{item_id}")
async def update_claim_item(claim_entry_id: str, item_id: int,
                            data: UpdateClaimItemRequest):
    """Edit a claim item (e.g. override previously_claimed). Derived fields
    and the claim summary are recomputed server-side to preserve the math."""
    svc = _get_service()
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        return {"error": "No fields to update"}
    ok = svc.update_claim_item(item_id, **fields)
    return {"ok": ok}


@router.patch("/claims/{claim_entry_id}/summary")
async def update_claim_summary(claim_entry_id: str, data: UpdateClaimSummaryRequest):
    """Manual Mode: edit the claim summary card directly (claim number,
    section cumulative values, Less Previous, Retention). Gross/Net/GST/Total
    recompute server-side; claim number is validated unique per project."""
    svc = _get_service()
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        return {"error": "No fields to update"}
    result = svc.update_claim_summary(claim_entry_id, **fields)
    if result.get("error"):
        return result
    return {"ok": True}


@router.post("/projects/{entry_id}/claims/import-upload")
async def import_claim_upload(entry_id: str, file: UploadFile = File(...)):
    """Import a previously-finished claim from an XLSX or PDF file.

    The claim is parsed, matched to the project's cashflow items, and stored.
    Future claims for later months learn `previously_claimed` from it.
    """
    if not file.filename:
        return {"error": "No file provided"}
    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".xlsx", ".pdf"):
        return {"error": "Only .xlsx or .pdf files are accepted"}
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = tmp.name
        svc = _get_service()
        claim_entry_id = svc.import_claim(entry_id, tmp_path)
        if not claim_entry_id:
            return {"error": "Import failed — could not parse the claim"}
        return {"entry_id": claim_entry_id}
    except Exception as e:
        return {"error": str(e)}
    finally:
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass


# ── Export / download ──────────────────────────────────────────────────


@router.post("/claims/{claim_entry_id}/export-excel")
async def export_excel(claim_entry_id: str):
    """Generate the Excel workbook for a claim (queued)."""
    svc = _get_service()
    svc.export_excel(claim_entry_id)
    return {"status": "started"}


@router.post("/claims/{claim_entry_id}/export-pdf")
async def export_pdf(claim_entry_id: str):
    """Export a claim to PDF (queued, requires Excel first)."""
    svc = _get_service()
    svc.export_pdf(claim_entry_id)
    return {"status": "started"}


@router.get("/claims/{claim_entry_id}/download/excel")
async def download_excel(claim_entry_id: str):
    """Download the generated Excel file (generates synchronously if missing)."""
    from shared_tools.progress_claim.progress_claim_db import get_claim
    svc = _get_service()
    claim = get_claim(claim_entry_id)
    if not claim:
        return {"error": "Claim not found"}
    excel_path = claim.get("excel_path")
    if not excel_path or not Path(excel_path).exists():
        # Generate on demand
        svc._handle_export_excel(claim_entry_id)
        claim = get_claim(claim_entry_id)
        excel_path = claim.get("excel_path")
    if not excel_path or not Path(excel_path).exists():
        return {"error": "Excel not available"}
    return FileResponse(excel_path, filename=Path(excel_path).name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.get("/claims/{claim_entry_id}/download/pdf")
async def download_pdf(claim_entry_id: str):
    """Download the generated PDF (generates synchronously if missing)."""
    from shared_tools.progress_claim.progress_claim_db import get_claim
    svc = _get_service()
    claim = get_claim(claim_entry_id)
    if not claim:
        return {"error": "Claim not found"}
    pdf_path = claim.get("pdf_path")
    if not pdf_path or not Path(pdf_path).exists():
        svc._handle_export_pdf(claim_entry_id)
        claim = get_claim(claim_entry_id)
        pdf_path = claim.get("pdf_path")
    if not pdf_path or not Path(pdf_path).exists():
        return {"error": "PDF not available"}
    return FileResponse(pdf_path, filename=Path(pdf_path).name,
                        media_type="application/pdf")
