"""Variation Agent — multi-modal LLM analysis for variation requests.

Accepts text + files (PDFs, images) and uses Gemini multi-modal API to:
  1. Extract variation details from the input
  2. Match against existing projects
  3. Return a structured plan for VO creation

The agent is READ-ONLY — it analyzes and returns a plan. The frontend
executes the actual project/VO creation after user confirmation.
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


def _get_gemini_model():
    """Return a raw Gemini generative model for multi-modal use."""
    import google.generativeai as genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set in .env")

    genai.configure(api_key=api_key)
    # Use gemini-3.5-flash — latest stable multi-modal model
    return genai.GenerativeModel("models/gemini-3.5-flash")


# =============================================================================
# Prompt template
# =============================================================================

SYSTEM_PROMPT = """You are a construction variation analyst for Welink Construction.
Analyze the user's input (text + any attached documents) and extract structured
information for creating a Variation Order (VO).

Return ONLY a JSON object with these fields (use null for unknown values):

{
  "project_name": "extracted or inferred project name (e.g., ARCO, Ferguson Residence)",
  "project_match_confidence": "high/medium/low/none",
  "vo_summary": "1-2 sentence summary of what the variation is about",
  "vo_title": "descriptive title, e.g., Wet Fire Variation to Ground Floor",
  "vo_type": "Head Contract VO or Client Direct VO",
  "line_items": [
    {
      "description": "short phrase describing the work item (max 8 words)",
      "qty": number,
      "unit": "item/m2/m3/hr/etc",
      "rate": number
    }
  ],
  "total_estimated_cost": number (sum of qty*rate for all items),
  "notes": "any additional context or caveats from the documents"
}

CRITICAL RULES:
- For line_items DESCRIPTION: keep it SHORT and SIMPLE. Use brief phrases like
  "100mm fire sprinkler pipework" or "12 new sprinkler heads" — NOT full sentences.
  Maximum 8 words per description. Be specific but concise.
- For project_name: look for project codes (ARCO, CBR, etc.) or full project names.
- If the project name is ambiguous, set confidence to "low".
- Extract quantities and rates from documents if present. If a rate isn't specified,
  estimate a reasonable one.
- vo_type: "Client Direct VO" only if client is directly requesting/paying.
  Default to "Head Contract VO".
- Output ONLY the JSON object, no markdown fences, no explanation."""


# =============================================================================
# Project matching
# =============================================================================


def _match_project(project_name: str) -> dict | None:
    """Find the best matching project by name. Returns {entry_id, name} or None."""
    if not project_name:
        return None

    from shared_tools.variation_db import get_projects

    projects = get_projects()
    if not projects:
        return None

    target = project_name.lower().strip()

    # Try exact match first
    for p in projects:
        if (p.get("name") or "").lower().strip() == target:
            return {"entry_id": p["entry_id"], "name": p["name"]}

    # Try substring match (longest match wins)
    best = None
    best_len = 0
    for p in projects:
        pname = (p.get("name") or "").lower().strip()
        if target in pname or pname in target:
            if len(pname) > best_len:
                best = {"entry_id": p["entry_id"], "name": p["name"]}
                best_len = len(pname)

    return best


# =============================================================================
# Main agent function
# =============================================================================


def analyze_variation_request(
    text: str = "",
    files: list[dict] | None = None,
) -> dict:
    """Analyze a variation request and return a structured plan.

    Args:
        text: User's text description of the variation
        files: List of {name: str, content: bytes, mime_type: str} for attached files

    Returns:
        {
            "analysis": { ... LLM output ... },
            "project_match": {entry_id, name} or None,
            "next_vo_number": int or None,
            "existing_projects": [...],
        }
    """
    model = _get_gemini_model()

    # ── Build multi-modal message parts ───────────────────────────────
    parts = [SYSTEM_PROMPT]

    if text:
        parts.append(f"USER TEXT INPUT:\n{text}")

    if files:
        for f in files:
            try:
                mime = f.get("mime_type", "application/pdf")
                # Send file bytes directly as a Part dict — upload_file() doesn't
                # accept raw bytes in the deprecated SDK, but inline Part dicts work.
                parts.append({"mime_type": mime, "data": f["content"]})
            except Exception as e:
                parts.append(
                    f"[Note: Could not process attached file '{f.get('name', 'unknown')}': {e}]"
                )

    # ── Call Gemini ───────────────────────────────────────────────────
    try:
        response = model.generate_content(parts)
        raw_text = response.text.strip()
    except Exception as e:
        return {
            "error": f"Gemini API call failed: {e}",
            "analysis": None,
            "project_match": None,
            "next_vo_number": None,
        }

    # ── Parse JSON from response ──────────────────────────────────────
    analysis = _parse_json(raw_text)

    # ── Match project ─────────────────────────────────────────────────
    project_name = analysis.get("project_name", "")
    match = _match_project(project_name)

    # ── Get next VO number if project matched ─────────────────────────
    next_vo = None
    if match:
        from shared_tools.variation_db import get_variations

        variations = get_variations(project_entry_id=match["entry_id"])
        active = [v for v in variations if v.get("status") != "void"]
        max_vo = max((v.get("vo_number") or 0 for v in active), default=0)
        next_vo = max_vo + 1

    # ── List all projects for the frontend ────────────────────────────
    from shared_tools.variation_db import get_projects

    all_projects = get_projects()

    return {
        "analysis": analysis,
        "project_match": match,
        "next_vo_number": next_vo,
        "existing_projects": [
            {"entry_id": p["entry_id"], "name": p.get("name", "")}
            for p in all_projects
        ],
    }


def _parse_json(text: str) -> dict:
    """Extract a JSON object from LLM output. Handles markdown fences and
    language tags like ```json."""
    text = text.strip()

    # Remove markdown fences (with optional language tag)
    fence_pattern = re.compile(r'^```(\w+)?\s*\n')
    m = fence_pattern.match(text)
    if m:
        text = text[m.end():]  # strip opening fence
        # Strip closing fence
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

    # Return raw text as fallback
    return {"raw_response": text, "parse_error": True}
