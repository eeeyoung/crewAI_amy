"""Invoice Allocation REST API — wraps InvoiceAllocationService as HTTP endpoints."""

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/invoice", tags=["Invoice Allocation"])


# ── Service singleton ───────────────────────────────────────────────────

_service = None


def _get_service():
    global _service
    if _service is None:
        from shared_tools.invoice_allocation.invoice_allocation_service import (
            InvoiceAllocationService,
        )
        _service = InvoiceAllocationService()
        _service.start()
    return _service


# ── Schemas ─────────────────────────────────────────────────────────────


class ScanRequest(BaseModel):
    folder_path: str


class AllocateRequest(BaseModel):
    folder_path: str


class ConfirmRequest(BaseModel):
    original_path: str
    project_code: str


class DeclineRequest(BaseModel):
    original_path: str


# ── Routes ──────────────────────────────────────────────────────────────


@router.post("/scan")
async def scan_folder(req: ScanRequest):
    """Dry-run: scan folder and return matching results without moving files."""
    from pathlib import Path
    from shared_tools.invoice_allocation.invoice_allocation_service import (
        _scan_project_folders,
    )

    folder = Path(req.folder_path)
    if not folder.exists() or not folder.is_dir():
        return {"error": f"Folder not found: {req.folder_path}"}

    projects, control_folders = _scan_project_folders(folder)

    if not projects:
        return {"error": "No project folders found (4-digit prefix)", "projects": []}

    svc = _get_service()

    # Analyze each root-level PDF
    pdfs = sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() == ".pdf"
    )

    results = []
    for pdf_path in pdfs:
        fname_match = svc._match_by_filename(pdf_path.name, projects)
        text = svc._extract_pdf_text(pdf_path)
        text_match = svc._match_by_text(text or "", projects) if text else None
        best = svc._combine_matches(fname_match, text_match)

        status = "auto_move" if (best and best["score"] >= svc.AUTO_MOVE_THRESHOLD) else (
            "pending" if (best and best["score"] > 0) else "no_match"
        )

        results.append({
            "filename": pdf_path.name,
            "status": status,
            "suggested_project_code": best.get("code") if best else None,
            "suggested_project_name": best.get("name") if best else None,
            "suggested_project_full_name": best.get("full_name") if best else None,
            "confidence": best.get("score", 0) if best else 0,
            "match_method": "combined" if (fname_match and text_match and fname_match.get("code") == text_match.get("code")) else (
                "filename" if fname_match else ("text" if text_match else "none")
            ),
            "text_preview": (text or "")[:200] if text else "",
        })

    return {
        "folder_path": str(folder),
        "projects": [{"code": p["code"], "name": p["name"], "full_name": p["full_name"]}
                      for p in projects],
        "control_folders": [{"prefix": c["prefix"], "name": c["name"]}
                            for c in control_folders],
        "total": len(results),
        "auto_move": sum(1 for r in results if r["status"] == "auto_move"),
        "pending": sum(1 for r in results if r["status"] == "pending"),
        "no_match": sum(1 for r in results if r["status"] == "no_match"),
        "results": results,
    }


@router.post("/allocate")
async def allocate_folder(req: AllocateRequest):
    """Run full allocation: auto-move high-confidence files, return pending list."""
    svc = _get_service()
    result = svc.allocate_folder_sync(req.folder_path)

    # Format moved items for the frontend
    moved_items = []
    for item in result.get("moved_items", []):
        moved_items.append({
            "filename": item.get("filename", ""),
            "target_project": item.get("target_project", ""),
            "confidence": item.get("confidence", 0),
            "match_method": item.get("match_method", ""),
        })

    # Format pending items
    pending_items = []
    for item in result.get("pending", []):
        pending_items.append({
            "filename": item.get("filename", ""),
            "original_path": item.get("original_path", ""),
            "suggested_project_code": item.get("suggested_project_code", ""),
            "suggested_project_name": item.get("suggested_project_name", ""),
            "suggested_project_full_name": item.get("suggested_project_full_name", ""),
            "confidence": item.get("confidence", 0),
            "match_method": item.get("match_method", ""),
            "llm_reasoning": item.get("llm_reasoning", ""),
        })

    # Format no-match items
    no_match_items = []
    for item in result.get("no_match", []):
        no_match_items.append({
            "filename": item.get("filename", ""),
            "original_path": item.get("original_path", ""),
        })

    return {
        "total": result["total"],
        "moved": result["moved"],
        "pending_count": len(pending_items),
        "no_match_count": len(no_match_items),
        "failed": result["failed"],
        "moved_items": moved_items,
        "pending_items": pending_items,
        "no_match_items": no_match_items,
        "projects": result.get("projects", []),
    }


@router.post("/confirm")
async def confirm_pending(req: ConfirmRequest):
    """Confirm a pending item — move it to the suggested project folder."""
    from pathlib import Path
    from shared_tools.invoice_allocation.invoice_allocation_service import (
        _scan_project_folders,
    )

    folder = Path(req.original_path).parent
    projects, _ = _scan_project_folders(folder)

    svc = _get_service()
    result = svc.confirm_move(req.original_path, req.project_code, projects)

    if result is None:
        return {"error": "File not found or project not found"}

    return {"ok": True, "result": result}


@router.post("/decline")
async def decline_pending(req: DeclineRequest):
    """Decline a pending item — leave it in root, update DB record."""
    from pathlib import Path
    from shared_tools.invoice_allocation.invoice_allocation_db import (
        get_history,
        set_record_status,
    )

    # Find the pending record for this file
    records = get_history(limit=50)
    filename = Path(req.original_path).name
    for r in records:
        if r.get("filename") == filename and r.get("status") == "pending_confirmation":
            set_record_status(r["id"], "declined")
            return {"ok": True, "filename": filename}

    return {"error": "Pending record not found", "filename": filename}


@router.get("/history")
async def get_history(limit: int = Query(50)):
    """Return allocation history with run-level grouping."""
    svc = _get_service()
    records = svc.get_allocation_history(limit)

    # Group by run_id
    runs = {}
    for r in records:
        rid = r.get("run_id")
        if rid not in runs:
            runs[rid] = {
                "run_id": rid,
                "records": [],
                "moved": 0,
                "pending": 0,
                "no_match": 0,
            }
        status = r.get("status", "")
        if status in ("moved", "confirmed"):
            runs[rid]["moved"] += 1
        elif status == "pending_confirmation":
            runs[rid]["pending"] += 1
        elif status == "no_match":
            runs[rid]["no_match"] += 1
        runs[rid]["records"].append({
            "id": r.get("id"),
            "filename": r.get("filename", ""),
            "target_project_name": r.get("target_project_name", ""),
            "target_project_code": r.get("target_project_code", ""),
            "match_method": r.get("match_method", ""),
            "confidence": r.get("confidence", 0),
            "status": status,
            "llm_reasoning": r.get("llm_reasoning", ""),
            "original_path": r.get("original_path", ""),
            "moved_to_path": r.get("moved_to_path", ""),
            "created_at": r.get("created_at", ""),
        })

    return {"runs": list(runs.values()), "total_records": len(records)}


@router.post("/undo/{record_id}")
async def undo_allocation(record_id: int):
    """Undo an allocation — move file back to original location."""
    svc = _get_service()
    ok = svc.undo_allocation(record_id)
    return {"ok": ok, "record_id": record_id}
