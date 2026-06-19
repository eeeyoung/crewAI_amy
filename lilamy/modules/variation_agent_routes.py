"""Variation Agent REST API — multi-modal variation request analysis."""

from fastapi import APIRouter, UploadFile, File, Form

router = APIRouter(prefix="/api/variations/agent", tags=["Variation Agent"])


@router.post("/analyze")
async def analyze_request(
    text: str = Form(""),
    files: list[UploadFile] = File(default=[]),
):
    """Analyze a variation request with multi-modal LLM.

    Accepts text + file uploads (PDFs, images).
    Returns structured analysis + project match + suggested VO plan.
    """
    from shared_tools.variation.variation_agent import analyze_variation_request

    # Read file contents into memory
    file_data = []
    for f in files:
        content = await f.read()
        mime = f.content_type or "application/octet-stream"
        file_data.append({
            "name": f.filename or "document",
            "content": content,
            "mime_type": mime,
        })

    result = analyze_variation_request(text=text, files=file_data if file_data else None)
    return result
