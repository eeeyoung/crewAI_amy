"""Subcontractor Agent — multi-modal LLM analysis for quote documents.

Accepts text + files (quote PDFs, images) and uses Gemini multi-modal API to:
  1. Extract vendor name, trade, line items, rates, totals from quote PDFs
  2. Classify vendor type (supplier vs. subcontractor)
  3. Match against existing vendors
  4. Return a structured plan for PO/Subcontract creation

The agent is READ-ONLY — it analyzes and returns a plan. The frontend
executes the actual commitment creation after user confirmation.

Pattern: Agent extracts → Editing page → User confirms → Push to document
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()


# =============================================================================
# Gemini multi-modal helper
# =============================================================================


def _get_gemini_client():
    """Return a Gemini client (google.genai) configured for deterministic JSON output."""
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set in .env")

    return genai.Client(api_key=api_key)


# =============================================================================
# Prompt template
# =============================================================================

SYSTEM_PROMPT = """You are a construction procurement analyst for Welink Construction.
Analyze the user's quote PDF and extract structured information for creating a
Purchase Order or Subcontract.

Return a JSON object with these fields (use null for unknown values):

{
  "vendor_name": "company name of the quoting vendor",
  "vendor_type_guess": "subcontractor or supplier",
  "trade_name": "construction trade (e.g., Hydraulics, Electrical, Concrete, Glazing)",
  "quote_ref": "reference number on the quote",
  "date": "quote date in YYYY-MM-DD format",
  "project_reference": "project name or code if visible (e.g., ARCO, 47 CBR)",
  "scope_summary": "1-2 sentence summary of what the quote covers",
  "line_items": [
    {
      "item_number": 1,
      "description": "short phrase describing the work item (max 8 words)",
      "qty": number,
      "unit": "item/LS/m2/m3/hr/m/etc",
      "rate": number
    }
  ],
  "total_ex_gst": number (total before GST if stated),
  "gst_amount": number (GST amount if separately stated),
  "total_incl_gst": number (total including GST),
  "notes": "any special conditions, exclusions, or caveats stated on the quote"
}

CRITICAL RULES:

1. VENDOR TYPE GUESS:
   - "subcontractor" if the quote is for LABOR/TRADE services (installation, construction, plumbing, electrical work, concrete, formwork, earthworks, piling, etc.)
   - "supplier" if the quote is for MATERIALS/EQUIPMENT only (supply of doors, supply of modular buildings, equipment hire, materials, etc.)
   - Look for the primary activity — if it involves on-site work, it's likely a subcontractor.

2. ITEM GROUPING (A-level vs B-level):
   Construction quotations often have TOP-LEVEL items (A-level) with sub-items (B-level)
   listed underneath. B-level items usually have NO individual price — only the A-level
   parent has a total price. ALWAYS extract A-level items ONLY. Do NOT extract B-level
   sub-items as separate line items. If a document has 2 A-level items each with 10
   B-level sub-items, you should output exactly 2 line items — one per A-level item.

3. LINE ITEM DESCRIPTIONS: keep them SHORT — max 8 words. Use brief phrases like
   "Stormwater rough-in to ground floor" NOT "Supply and install all stormwater
   drainage systems including excavation...". Be concise.

4. PRICING: extract exact rates from the document. If only a lump sum is given,
   use qty=1, unit="LS", rate=<total>. If both ex-GST and incl-GST are shown,
   fill both fields. If only one total is shown, use that as total_incl_gst.

5. TRADE NAMES: use standard construction trade categories:
   Electrical, Hydraulics, Mechanical, Concrete, Formwork, Earthworks, Piling,
   Glazing, Joinery, Plastering, Painting, Tiling, Flooring, Masonry,
   Waterproofing, Scaffolding, Roofing, Structural Steel, Landscaping,
   Traffic Management, Surveying, Crane Hire, Equipment Hire, Materials Supply."""


# =============================================================================
# Vendor matching
# =============================================================================


def _match_vendor(vendor_name: str) -> dict | None:
    """Find the best matching existing vendor by name."""
    if not vendor_name:
        return None

    from shared_tools.subcontractor.subcontractor_db import get_vendors

    vendors = get_vendors()
    if not vendors:
        return None

    target = vendor_name.lower().strip()

    # Try exact match
    for v in vendors:
        if (v.get("company_name") or "").lower().strip() == target:
            return {"entry_id": v["entry_id"], "name": v["company_name"],
                    "vendor_type": v.get("vendor_type", "")}

    # Try substring match (longest wins)
    best = None
    best_len = 0
    for v in vendors:
        vname = (v.get("company_name") or "").lower().strip()
        if target in vname or vname in target:
            if len(vname) > best_len:
                best = {"entry_id": v["entry_id"], "name": v["company_name"],
                        "vendor_type": v.get("vendor_type", "")}
                best_len = len(vname)

    return best


# =============================================================================
# Main agent function
# =============================================================================


def analyze_quote(
    text: str = "",
    files: list[dict] | None = None,
    project_entry_id: str = "",
) -> dict:
    """Analyze a construction quote and return structured extraction.

    Args:
        text: User's text description (optional)
        files: List of {name: str, content: bytes, mime_type: str} for quote PDFs
        project_entry_id: Optional project to scope vendor search

    Returns:
        {
            "analysis": { ... LLM output ... },
            "vendor_match": {entry_id, name, vendor_type} or None,
            "vendor_type_suggestion": "subcontractor" or "supplier",
            "existing_vendors": [...],
            "source_file": str or None,
        }
    """
    client = _get_gemini_client()

    # ── Build multi-modal message parts ───────────────────────────────
    contents = [SYSTEM_PROMPT]

    if text:
        contents.append(f"USER TEXT INPUT:\n{text}")

    source_file = None
    if files:
        for f in files:
            try:
                mime = f.get("mime_type", "application/pdf")
                contents.append({
                    "inline_data": {
                        "mime_type": mime,
                        "data": f["content"],
                    }
                })
                if not source_file:
                    source_file = f.get("name", "")
            except Exception as e:
                contents.append(
                    f"[Note: Could not process attached file "
                    f"'{f.get('name', 'unknown')}': {e}]"
                )

    # ── Call Gemini ───────────────────────────────────────────────────
    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash",
            contents=contents,
            config={
                "temperature": 0,
                "top_p": 1.0,
                "response_mime_type": "application/json",
            },
        )
        raw_text = response.text.strip()
    except Exception as e:
        return {
            "error": f"Gemini API call failed: {e}",
            "analysis": None,
            "vendor_match": None,
            "vendor_type_suggestion": "subcontractor",
            "existing_vendors": [],
        }

    # ── Parse JSON from response ──────────────────────────────────────
    analysis = _parse_json(raw_text)

    # ── Vendor type suggestion ────────────────────────────────────────
    vendor_type = analysis.get("vendor_type_guess", "subcontractor")
    if vendor_type not in ("supplier", "subcontractor"):
        vendor_type = "subcontractor"

    # ── Match vendor ──────────────────────────────────────────────────
    vendor_name = analysis.get("vendor_name", "")
    match = _match_vendor(vendor_name)

    # ── List existing vendors for frontend ────────────────────────────
    from shared_tools.subcontractor.subcontractor_db import get_vendors
    all_vendors = get_vendors()

    return {
        "analysis": analysis,
        "vendor_match": match,
        "vendor_type_suggestion": vendor_type,
        "existing_vendors": [
            {"entry_id": v["entry_id"], "company_name": v.get("company_name", ""),
             "vendor_type": v.get("vendor_type", ""),
             "trade_categories": v.get("trade_categories", "[]")}
            for v in all_vendors[:50]  # Limit to 50 for response size
        ],
        "source_file": source_file,
    }


# =============================================================================
# Tender analysis (Phase 3 — placeholder)
# =============================================================================


def analyze_tender(
    quote_entry_ids: list[str],
    trade_name: str = "",
) -> dict:
    """Compare multiple quotes for the same trade and return a scored analysis.

    This is a Phase 3 feature. Currently returns a deterministic side-by-side
    comparison based on the quote data in the database.
    """
    from shared_tools.subcontractor.subcontractor_db import get_quote_with_items

    quotes = []
    for qid in quote_entry_ids:
        q = get_quote_with_items(qid)
        if q:
            quotes.append(q)

    if len(quotes) < 2:
        return {"error": "Need at least 2 quotes to compare"}

    # Deterministic comparison (no LLM needed for basic scoring)
    comparison = []
    for q in quotes:
        items = q.get("items", [])
        total = sum(
            i.get("amount", i.get("qty", 0) * i.get("rate", 0))
            for i in items
        )
        comparison.append({
            "vendor_name": q.get("vendor_name", "Unknown"),
            "quote_ref": q.get("quote_ref", ""),
            "total": total or q.get("total_amount", 0),
            "item_count": len(items),
            "vendor_type": q.get("vendor_type", ""),
        })

    # Sort by total ascending
    comparison.sort(key=lambda x: x["total"])

    # Score: lowest price = highest commercial score
    if comparison:
        lowest = comparison[0]["total"]
        highest = comparison[-1]["total"]
        price_range = highest - lowest if highest != lowest else 1
        for c in comparison:
            c["commercial_score"] = round(
                100 * (1 - (c["total"] - lowest) / price_range), 1
            )

    return {
        "trade_name": trade_name or (quotes[0].get("trade_name", "") if quotes else ""),
        "quote_count": len(quotes),
        "comparison": comparison,
        "recommendation": comparison[0] if comparison else None,
        "notes": "Phase 3: Full AI tender analysis (technical scoring, compliance check, "
                 "recommendation for award) will be implemented with agent-driven comparison.",
    }


def _parse_json(text: str) -> dict:
    """Extract a JSON object from LLM output."""
    text = text.strip()

    # Remove markdown fences
    fence_pattern = re.compile(r'^```(\w+)?\s*\n')
    m = fence_pattern.match(text)
    if m:
        text = text[m.end():]
        if text.endswith("\n```"):
            text = text[:-4]
        elif text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to extract JSON object with regex
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    return {"raw_response": text, "parse_error": True}
