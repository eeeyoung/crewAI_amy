"""Subcontractor Agent REST API — multi-modal quote analysis.

Following the variation_agent_routes.py pattern: accepts file uploads,
calls the agent for AI analysis, returns structured JSON for the editing page.
"""

from fastapi import APIRouter, UploadFile, File, Form

router = APIRouter(prefix="/api/subcontractors/agent", tags=["Subcontractor Agent"])


@router.post("/analyze-quote")
async def analyze_quote(
    text: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    project_entry_id: str = Form(""),
):
    """Analyze a construction quote PDF with multi-modal LLM.

    Accepts text + file uploads (quote PDFs).
    Returns structured analysis + vendor match + suggested commitment plan.

    The frontend populates the editing page from this result.
    The user MUST confirm before the system creates any database records.
    """
    from shared_tools.subcontractor.subcontractor_agent import analyze_quote as agent_analyze

    # Read file contents into memory
    file_data = []
    for f in files:
        content = await f.read()
        mime = f.content_type or "application/pdf"
        file_data.append({
            "name": f.filename or "quote_document",
            "content": content,
            "mime_type": mime,
        })

    result = agent_analyze(
        text=text,
        files=file_data if file_data else None,
        project_entry_id=project_entry_id,
    )
    return result


@router.post("/create-from-analysis")
async def create_from_analysis(
    analysis: str = Form(""),
    project_entry_id: str = Form(""),
    vendor_type: str = Form("subcontractor"),
):
    """Create quote, vendor, and commitment from confirmed agent analysis.

    This endpoint is called AFTER the user has reviewed and confirmed
    the editing page content. It bridges agent output → database records.

    The $100K upgrade rule is enforced automatically.
    """
    import json
    from shared_tools.subcontractor.subcontractor_service import SubcontractorService

    try:
        analysis_data = json.loads(analysis)
    except json.JSONDecodeError:
        return {"error": "Invalid JSON in analysis field"}

    svc = SubcontractorService()
    svc.start()

    entry_id = svc.create_from_agent_result(
        agent_result={
            "analysis": analysis_data,
            "vendor_type_suggestion": vendor_type,
        },
        project_entry_id=project_entry_id,
    )

    svc.stop()
    return {"entry_id": entry_id}
