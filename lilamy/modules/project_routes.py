"""Project REST API — wraps VariationService project methods as HTTP endpoints."""

import shutil
import tempfile

from fastapi import APIRouter, Query, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path

router = APIRouter(prefix="/api/projects", tags=["Projects"])


def _get_service():
    from lilamy.modules.variation_routes import _get_service as _gs
    return _gs()


# ── Schemas ────────────────────────────────────────────────────────────


class ProjectCreate(BaseModel):
    name: str = ""
    job_number: str = ""
    location: str = ""
    base_contract_amount: float = 0
    company_name: str = "Welink Construction"
    xlsx_path: str = ""


class ProjectUpdate(BaseModel):
    name: str | None = None
    job_number: str | None = None
    location: str | None = None
    base_contract_amount: float | None = None
    xlsx_path: str | None = None


class ImportRequest(BaseModel):
    path: str


# ── Routes ─────────────────────────────────────────────────────────────


@router.get("")
async def list_projects():
    """List all projects with VO counts."""
    svc = _get_service()
    projects = svc.list_projects()
    return {"projects": projects}


@router.get("/{entry_id}")
async def get_project(entry_id: str):
    """Get a single project."""
    from shared_tools.variation_db import get_project
    p = get_project(entry_id)
    if not p:
        return {"error": "Project not found"}
    return p


@router.post("")
async def create_project(data: ProjectCreate):
    """Create a new empty project."""
    svc = _get_service()
    entry_id = svc.create_project(data.model_dump())
    return {"entry_id": entry_id}


@router.post("/import")
async def import_project(data: ImportRequest):
    """Import an existing xlsx file by server path → parse project + all VOs + items."""
    svc = _get_service()
    entry_id = svc.import_project(data.path)
    if entry_id:
        return {"entry_id": entry_id}
    return {"error": "Import failed"}


@router.post("/import-upload")
async def import_project_upload(file: UploadFile = File(...)):
    """Upload an xlsx file → save to temp → import → return project."""
    if not file.filename or not file.filename.endswith('.xlsx'):
        return {"error": "Only .xlsx files are accepted"}

    svc = _get_service()

    # Save uploaded file to a temp location
    tmp_path = None
    try:
        suffix = Path(file.filename).suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = tmp.name

        # Also save a permanent copy to data/variations/ for reference
        from shared_tools.variation_db import DATA_DIR
        dest_dir = DATA_DIR / "variations"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / file.filename
        shutil.copy2(tmp_path, str(dest_path))

        # Import from the permanent copy
        entry_id = svc.import_project(str(dest_path))
        if entry_id:
            return {"entry_id": entry_id}
        return {"error": "Import failed"}
    except Exception as e:
        return {"error": str(e)}
    finally:
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass


@router.patch("/{entry_id}")
async def update_project(entry_id: str, data: ProjectUpdate):
    """Update project fields."""
    from shared_tools.variation_db import update_project as db_update
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        return {"error": "No fields to update"}
    ok = db_update(entry_id, **fields)
    return {"ok": ok}


@router.delete("/{entry_id}")
async def delete_project(entry_id: str):
    """Delete a project and all its VOs + items."""
    svc = _get_service()
    ok = svc.delete_project(entry_id)
    return {"ok": ok}


@router.post("/{entry_id}/push")
async def push_project(entry_id: str):
    """Compile all VOs + Register + Internal VO Register to xlsx (with backup). Async."""
    svc = _get_service()
    svc._queue("push_project", entry_id=entry_id)
    return {"status": "started", "message": "Push queued — poll GET /{entry_id} for updated_at"}


@router.get("/{entry_id}/register")
async def get_register(entry_id: str):
    """Computed Register view for a project."""
    svc = _get_service()
    return svc.get_register(entry_id)


@router.get("/{entry_id}/internal-register")
async def get_internal_register(entry_id: str):
    """Computed Internal VO Register view for a project."""
    svc = _get_service()
    return svc.get_internal_register(entry_id)


@router.post("/{entry_id}/export-pdf")
async def export_project_pdf(entry_id: str):
    """Export the project xlsx to PDF (requires Excel COM)."""
    from shared_tools.variation_db import get_project
    project = get_project(entry_id)
    if not project or not project.get("xlsx_path"):
        return {"error": "Project has no xlsx — push first"}

    svc = _get_service()
    # Queue PDF export using existing _excel_to_pdf
    svc._queue("export_project_pdf", entry_id=entry_id, xlsx_path=project["xlsx_path"])
    return {"status": "started"}
