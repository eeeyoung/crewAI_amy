"""Subcontractor Management REST API — wraps SubcontractorService as HTTP endpoints.

Following the variation_routes.py pattern: thin FastAPI routes that delegate
all business logic to SubcontractorService.
"""

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path

router = APIRouter(prefix="/api/subcontractors", tags=["Subcontractors"])

# Lazy-init service (singleton)
_service = None


def _get_service():
    global _service
    if _service is None:
        from shared_tools.subcontractor.subcontractor_service import SubcontractorService
        _service = SubcontractorService()
        _service.start()
    return _service


# =============================================================================
# Schemas
# =============================================================================


class ProjectCreate(BaseModel):
    entry_id: str = ""
    name: str = ""
    job_number: str = ""
    location: str = ""
    head_contract_sum: float = 0
    contract_type: str = "AS 4000-1997"
    client_name: str = ""
    company_name: str = "Welink Construction"
    start_date: str | None = None
    pc_date: str | None = None
    retention_pct: float = 5
    status: str = "active"


class VendorCreate(BaseModel):
    entry_id: str = ""
    vendor_type: str = "subcontractor"
    company_name: str = ""
    trading_name: str = ""
    abn: str = ""
    contact_name: str = ""
    contact_email: str = ""
    contact_phone: str = ""
    address: str = ""
    trade_categories: str = "[]"
    notes: str = ""


class VendorUpdate(BaseModel):
    vendor_type: str | None = None
    company_name: str | None = None
    trading_name: str | None = None
    abn: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    address: str | None = None
    trade_categories: str | None = None
    prequalification_status: str | None = None
    insurance_expiry: str | None = None
    safety_rating: str | None = None
    performance_score: float | None = None
    notes: str | None = None
    status: str | None = None


class CommitmentCreate(BaseModel):
    entry_id: str = ""
    project_entry_id: str = ""
    vendor_entry_id: str = ""
    commitment_type: str = "purchase_order"
    reference_number: str = ""
    title: str = ""
    description: str = ""
    commitment_value: float = 0
    retention_pct: float = 0
    retention_limit: float = 0
    start_date: str | None = None
    end_date: str | None = None
    defects_liability_end: str | None = None
    delivery_date: str | None = None
    status: str = "draft"


class CommitmentUpdate(BaseModel):
    project_entry_id: str | None = None
    vendor_entry_id: str | None = None
    commitment_type: str | None = None
    reference_number: str | None = None
    title: str | None = None
    description: str | None = None
    commitment_value: float | None = None
    retention_pct: float | None = None
    retention_limit: float | None = None
    start_date: str | None = None
    end_date: str | None = None
    defects_liability_end: str | None = None
    delivery_date: str | None = None
    goods_receipt_date: str | None = None
    status: str | None = None
    securities_held: float | None = None
    insurance_verified: int | None = None


class ItemCreate(BaseModel):
    item_number: int = 1
    description: str = ""
    qty: float = 0
    unit: str = "item"
    rate: float = 0
    amount: float = 0
    wbs_code: str = ""
    notes: str = ""


class ItemUpdate(BaseModel):
    item_number: int | None = None
    description: str | None = None
    qty: float | None = None
    unit: str | None = None
    rate: float | None = None
    amount: float | None = None
    wbs_code: str | None = None
    notes: str | None = None


class ReorderRequest(BaseModel):
    item_ids: list[int]


class SendEmailRequest(BaseModel):
    to: str
    cc: str = ""
    subject: str = ""
    body: str = ""


class AwardQuoteRequest(BaseModel):
    quote_entry_id: str


# =============================================================================
# Projects
# =============================================================================


@router.get("/projects")
async def list_projects():
    svc = _get_service()
    projects = svc.list_projects()
    return {"count": len(projects), "projects": projects}


@router.post("/projects")
async def create_project(data: ProjectCreate):
    svc = _get_service()
    entry_id = svc.create_project(data.model_dump())
    return {"entry_id": entry_id}


@router.get("/projects/{entry_id}")
async def get_project(entry_id: str):
    svc = _get_service()
    proj = svc.get_project(entry_id)
    if not proj:
        return {"error": "Project not found"}
    return proj


# =============================================================================
# Vendors
# =============================================================================


@router.get("/vendors")
async def list_vendors(vendor_type: str = Query(None), trade: str = Query(None)):
    svc = _get_service()
    vendors = svc.list_vendors(vendor_type=vendor_type, trade=trade)
    return {"count": len(vendors), "vendors": vendors}


@router.post("/vendors")
async def create_vendor(data: VendorCreate):
    svc = _get_service()
    entry_id = svc.create_vendor(data.model_dump())
    return {"entry_id": entry_id}


@router.get("/vendors/{entry_id}")
async def get_vendor(entry_id: str):
    svc = _get_service()
    vendor = svc.get_vendor(entry_id)
    if not vendor:
        return {"error": "Vendor not found"}
    return vendor


@router.patch("/vendors/{entry_id}")
async def update_vendor(entry_id: str, data: VendorUpdate):
    svc = _get_service()
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        return {"error": "No fields to update"}
    ok = svc.update_vendor(entry_id, **fields)
    return {"ok": ok}


@router.delete("/vendors/{entry_id}")
async def delete_vendor(entry_id: str):
    svc = _get_service()
    from shared_tools.subcontractor.subcontractor_db import delete_vendor
    ok = delete_vendor(entry_id)
    return {"ok": ok}


# =============================================================================
# Commitments (POs + Subcontracts)
# =============================================================================


@router.get("/commitments")
async def list_commitments(
    project_entry_id: str = Query(""),
    commitment_type: str = Query(None),
    status: str = Query(None),
):
    svc = _get_service()
    commitments = svc.list_commitments(
        project_entry_id=project_entry_id,
        commitment_type=commitment_type,
        status=status,
    )
    return {"count": len(commitments), "commitments": commitments}


@router.post("/commitments")
async def create_commitment(data: CommitmentCreate):
    """Create a new commitment (PO or Subcontract).
    The $100K upgrade rule is enforced automatically:
    subcontractor + PO ≥ $100K → auto-upgraded to subcontract."""
    svc = _get_service()
    entry_id = svc.create_commitment(data.model_dump())
    return {"entry_id": entry_id}


@router.get("/commitments/{entry_id}")
async def get_commitment(entry_id: str):
    svc = _get_service()
    c = svc.get_commitment(entry_id)
    if not c:
        return {"error": "Commitment not found"}
    return c


@router.patch("/commitments/{entry_id}")
async def update_commitment(entry_id: str, data: CommitmentUpdate):
    svc = _get_service()
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        return {"error": "No fields to update"}
    ok = svc.update_commitment(entry_id, **fields)
    return {"ok": ok}


@router.delete("/commitments/{entry_id}")
async def delete_commitment(entry_id: str):
    svc = _get_service()
    ok = svc.delete_commitment(entry_id)
    return {"ok": ok}


# ── Commitment Items ────────────────────────────────────────────────


@router.post("/commitments/{entry_id}/items")
async def add_item(entry_id: str, data: ItemCreate):
    svc = _get_service()
    item_data = data.model_dump()
    item_id = svc.add_item(entry_id, item_data)
    return {"ok": item_id is not None, "item_id": item_id}


@router.patch("/commitments/{entry_id}/items/{item_id}")
async def update_item(entry_id: str, item_id: int, data: ItemUpdate):
    svc = _get_service()
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        return {"error": "No fields to update"}
    ok = svc.update_item(item_id, **fields)
    return {"ok": ok}


@router.delete("/commitments/{entry_id}/items/{item_id}")
async def remove_item(entry_id: str, item_id: int):
    svc = _get_service()
    ok = svc.remove_item(item_id, entry_id)
    return {"ok": ok}


@router.put("/commitments/{entry_id}/items/reorder")
async def reorder_items(entry_id: str, data: ReorderRequest):
    svc = _get_service()
    ok = svc.reorder_items(entry_id, data.item_ids)
    return {"ok": ok}


# ── Document Generation ──────────────────────────────────────────────


@router.post("/commitments/{entry_id}/generate")
async def generate_document(entry_id: str):
    """Generate the PO or Subcontract document (async).
    Poll GET /commitments/{entry_id} to check document_path for completion."""
    svc = _get_service()
    svc.generate_document(entry_id)
    return {"status": "started", "message": "Document generation queued"}


@router.post("/commitments/{entry_id}/export-pdf")
async def export_pdf(entry_id: str):
    """Export the document to PDF (async). Requires document to exist first."""
    svc = _get_service()
    svc.export_pdf(entry_id)
    return {"status": "started", "message": "PDF export queued"}


@router.get("/commitments/{entry_id}/download")
async def download_document(entry_id: str):
    """Download the generated document (Excel)."""
    svc = _get_service()
    c = svc.get_commitment(entry_id)
    if not c or not c.get("document_path"):
        return {"error": "Document not generated yet"}
    path = Path(c["document_path"])
    if not path.exists():
        return {"error": "Document file not found on disk"}
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=path.name,
    )


@router.get("/commitments/{entry_id}/download/pdf")
async def download_pdf(entry_id: str):
    """Download the generated PDF."""
    svc = _get_service()
    c = svc.get_commitment(entry_id)
    if not c or not c.get("pdf_path"):
        return {"error": "PDF not generated yet"}
    path = Path(c["pdf_path"])
    if not path.exists():
        return {"error": "PDF file not found on disk"}
    return FileResponse(path, media_type="application/pdf", filename=path.name)


# ── Email ────────────────────────────────────────────────────────────


@router.post("/commitments/{entry_id}/generate-email")
async def generate_email(entry_id: str):
    """Generate a submission email draft (LLM-assisted). Async."""
    svc = _get_service()
    svc.generate_email(entry_id)
    return {"status": "started", "message": "Email generation queued"}


@router.post("/commitments/{entry_id}/send")
async def send_email(entry_id: str, data: SendEmailRequest):
    """Send the document via Outlook."""
    svc = _get_service()
    ok = svc.send_email(
        entry_id,
        to_recipients=data.to,
        cc_recipients=data.cc,
        subject=data.subject,
        body=data.body,
    )
    return {"ok": ok}


# =============================================================================
# Quotes
# =============================================================================


@router.get("/quotes")
async def list_quotes(
    project_entry_id: str = Query(""),
    trade_name: str = Query(None),
    is_awarded: int = Query(None),
):
    svc = _get_service()
    quotes = svc.list_quotes(
        project_entry_id=project_entry_id,
        trade_name=trade_name,
        is_awarded=is_awarded,
    )
    return {"count": len(quotes), "quotes": quotes}


@router.get("/quotes/{entry_id}")
async def get_quote(entry_id: str):
    svc = _get_service()
    q = svc.get_quote(entry_id)
    if not q:
        return {"error": "Quote not found"}
    return q


@router.post("/quotes/{entry_id}/items")
async def add_quote_item(entry_id: str, data: ItemCreate):
    svc = _get_service()
    item_id = svc.add_quote_item(entry_id, data.model_dump())
    return {"ok": item_id is not None, "item_id": item_id}


@router.delete("/quotes/{entry_id}/items/{item_id}")
async def remove_quote_item(entry_id: str, item_id: int):
    svc = _get_service()
    ok = svc.remove_quote_item(item_id)
    return {"ok": ok}


# ── Award Quote → Generate Commitment ────────────────────────────────


@router.post("/award-quote")
async def award_quote(data: AwardQuoteRequest):
    """Award a quote and create a commitment (PO or Subcontract) from it.
    The $100K rule applies automatically."""
    svc = _get_service()
    entry_id = svc.award_quote(data.quote_entry_id)
    if entry_id:
        return {"entry_id": entry_id}
    return {"error": "Failed to award quote"}


# =============================================================================
# Knowledge Pool — browse learned data
# =============================================================================

@router.get("/knowledge/clauses")
async def list_clauses(subcontract: str = Query(None)):
    """List clause library entries, optionally filtered by subcontract ref."""
    from shared_tools.subcontractor.subcontractor_db import get_clauses
    clauses = get_clauses(source_commitment_ref=subcontract)
    return {"count": len(clauses), "clauses": clauses}


@router.get("/knowledge/benchmarks")
async def list_benchmarks(trade: str = Query(None)):
    """List rate benchmarks, optionally filtered by trade."""
    from shared_tools.subcontractor.subcontractor_db import get_rate_benchmarks
    benchmarks = get_rate_benchmarks(trade_name=trade)
    return {"count": len(benchmarks), "benchmarks": benchmarks}


@router.get("/knowledge/competitive")
async def list_competitive(trade: str = Query(None)):
    """List competitive sets, optionally filtered by trade."""
    from shared_tools.subcontractor.subcontractor_db import get_competitive_sets
    sets = get_competitive_sets(trade_name=trade)
    # Resolve vendor entry_ids to names
    from shared_tools.subcontractor.subcontractor_db import get_vendor
    import json
    for s in sets:
        ids = json.loads(s.get("vendor_entry_ids", "[]"))
        s["vendor_names"] = []
        for vid in ids:
            v = get_vendor(vid)
            if v:
                s["vendor_names"].append(v.get("company_name", vid))
    return {"count": len(sets), "competitive_sets": sets}
