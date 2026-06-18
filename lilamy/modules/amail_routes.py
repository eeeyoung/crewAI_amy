"""AMail REST API — wraps MailService as HTTP endpoints."""

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/amail", tags=["AMail"])

# Lazy-init service (singleton)
_service = None
_habit_service = None


def _get_service():
    global _service
    if _service is None:
        from shared_tools.mail_service import MailService
        _service = MailService(auto_refresh=False)
        _service.start()
    return _service


def _get_habit_service():
    global _habit_service
    if _habit_service is None:
        from shared_tools.habit_learner_service import get_habit_service
        _habit_service = get_habit_service()
    return _habit_service


def _extract_sender_email(sender_raw: str) -> str:
    """Extract bare email from 'Name <email>' format."""
    import re
    if not sender_raw:
        return ""
    m = re.search(r'<([^>]+@[^>]+)>', sender_raw)
    if m:
        return m.group(1).strip()
    if "@" in sender_raw:
        return sender_raw.strip()
    return ""


# ── Schemas ────────────────────────────────────────────────────────────

class EmailSummary(BaseModel):
    entry_id: str
    subject: str
    sender: str
    received_time: str
    category: str
    urgency: str
    chinese_summary: str
    assignee: str
    todos: list[str]
    reply_draft: str = ""
    sort_label: str = "focused"


class EmailList(BaseModel):
    count: int
    emails: list[dict]


# ── Routes ─────────────────────────────────────────────────────────────

@router.get("/emails")
async def get_emails(status: str = Query("active"), limit: int = Query(0)):
    """Return all processed emails enriched with sort_label from habit learner."""
    from shared_tools.ipc_bridge import get_processed_emails
    emails = get_processed_emails(status=status, limit=limit)

    # Enrich each email with sort_label from habit learner
    try:
        habit_svc = _get_habit_service()
        for em in emails:
            sender_email = _extract_sender_email(em.get("sender", ""))
            em["sort_label"] = habit_svc.classify_sender(sender_email)
    except Exception:
        for em in emails:
            em["sort_label"] = "focused"

    return {"count": len(emails), "emails": emails}


@router.get("/fetch-status")
async def get_fetch_status():
    """Return real-time fetch progress for the noticeboard."""
    svc = _get_service()
    return svc.get_fetch_status()


@router.post("/refresh")
async def refresh_inbox(count: int = Query(30)):
    """Trigger an Outlook fetch + summarize cycle (async)."""
    svc = _get_service()
    # Kick off in background — the caller polls /emails for results
    svc.refresh_inbox(count=count)
    return {"status": "started", "message": f"Fetching up to {count} emails"}


@router.post("/fetch-earlier")
async def fetch_earlier(count: int = 100):
    """Fetch UNREAD emails older than the earliest stored email."""
    svc = _get_service()
    svc.fetch_earlier(count=count)
    return {"status": "started", "message": f"Fetching up to {count} earlier emails"}


@router.post("/fetch-latest")
async def fetch_latest(count: int = 100):
    """Fetch UNREAD emails newer than the latest stored email."""
    svc = _get_service()
    svc.fetch_latest(count=count)
    return {"status": "started", "message": f"Fetching up to {count} latest emails"}


@router.post("/sync")
async def sync_inbox(from_date: str = Query(None), to_date: str = Query(None)):
    """Fill gaps between date range. Uses storage range if not provided."""
    svc = _get_service()
    svc.sync_inbox(from_date=from_date, to_date=to_date)
    return {"status": "started", "message": f"Sync started{f' ({from_date} → {to_date})' if from_date else ''}"}


@router.post("/emails/{entry_id}/reply")
async def generate_reply(entry_id: str, data: dict = {}):
    """Generate an AI reply for a specific email (lazy).
    Optional body: {prompt_guide: "..."} — user guidance that becomes the primary
    instruction for the reply agent, with behavioral context as secondary input."""
    from shared_tools.ipc_bridge import get_processed_email, upsert_processed_email
    prompt_guide = (data.get("prompt_guide") or "").strip() if data else ""
    email = get_processed_email(entry_id)
    if not email:
        return {"error": "Email not found"}

    # Backfill body from Outlook if missing
    if not email.get("body"):
        try:
            import win32com.client
            outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
            msg = outlook.GetItemFromID(entry_id)
            body = getattr(msg, "Body", "")[:5000]
            if body:
                email["body"] = body
                upsert_processed_email(email)
        except Exception:
            pass

    from amail.crew import ReplyGeneratorCrew
    body = email.get("body") or email.get("email_body", "")

    # ── Logging: reply workflow ──────────────────────────────────────
    print(f"\n{'═'*70}")
    print(f"📧 REPLY AGENT — WebUI")
    print(f"{'═'*70}")
    print(f"  Email: {email.get('subject', '(no subject)')[:80]}")
    print(f"  Sender: {email.get('sender', 'unknown')}")
    print(f"  Category: {email.get('category', 'General')}")
    print(f"  Urgency: {email.get('urgency', 'low')}")

    # Behavioral context from Habit Learner (graceful degradation)
    behavioral_text = ""
    agent_info = None
    print(f"\n  ── Habit Learner ──────────────────────────────────────")
    try:
        habit_svc = _get_habit_service()
        sender_raw = email.get("sender", "")
        sender_email = _extract_sender_email(sender_raw)
        print(f"  Sender email: {sender_email or '(not extractable)'}")

        ctx = habit_svc.infer(email)
        if ctx:
            print(f"  Profile found:  {'YES' if ctx.sender_profile else 'NO'}")
            if ctx.sender_profile:
                sp = ctx.sender_profile
                print(f"    Tier:         {sp.get('tier_label', '?')} (level {sp.get('tier', '?')})")
                print(f"    Reply rate:   {sp.get('reply_rate', 0):.0%}")
                print(f"    Avg latency:  {sp.get('avg_latency_hours', 'N/A')}")
                print(f"    Top intent:   {sp.get('top_intent', 'N/A')}")
                print(f"    Greeting:     \"{sp.get('preferred_greeting', 'N/A')}\"")
                print(f"    Sign-off:     \"{sp.get('signoff_preference', 'N/A')}\"")
                print(f"    Avg words:    {sp.get('avg_reply_words', 'N/A')}")
            print(f"  Predicted intent: {ctx.predicted_intent}")
            if ctx.style_params:
                sp = ctx.style_params
                print(f"  Style params:")
                print(f"    Structure:   {sp.get('structure_type', 'N/A')}")
                print(f"    Formality:   {sp.get('formality', 'N/A')}")
                print(f"    Greeting:    \"{sp.get('greeting_style', 'N/A')}\"")
                print(f"    Sign-off:    \"{sp.get('signoff', 'N/A')}\"")
                print(f"    Samples:     {sp.get('sample_count', 0)}")
            print(f"  Matched examples: {len(ctx.matched_examples)}")
            print(f"  Confidence:  {ctx.confidence:.0%}")
            behavioral_text = ctx.to_injection_text()
            if behavioral_text:
                print(f"\n  ── Injected Behavioral Context ──────────────────────")
                for line in behavioral_text.split("\n"):
                    print(f"  | {line}")
            else:
                print(f"  (no behavioral context — confidence too low)")

            # Build agent_info for the frontend panel
            agent_info = {
                "confidence": ctx.confidence,
                "predicted_intent": ctx.predicted_intent,
                "matched_examples": len(ctx.matched_examples),
                "behavioral_context": behavioral_text,
                "sender_profile": None,
                "style_params": None,
            }
            if ctx.sender_profile:
                sp = ctx.sender_profile
                agent_info["sender_profile"] = {
                    "name": sp.get("sender_name", sp.get("sender_email", "")),
                    "email": sp.get("sender_email", ""),
                    "tier": sp.get("tier_label", "unknown"),
                    "tier_level": sp.get("tier", 3),
                    "reply_rate": sp.get("reply_rate", 0),
                    "avg_latency_hours": sp.get("avg_latency_hours"),
                    "avg_reply_words": sp.get("avg_reply_words"),
                    "preferred_greeting": sp.get("preferred_greeting", ""),
                    "signoff_preference": sp.get("signoff_preference", ""),
                    "top_intent": sp.get("top_intent", ""),
                    "total_received": sp.get("total_received", 0),
                    "total_replied": sp.get("total_replied", 0),
                }
            if ctx.style_params:
                sp = ctx.style_params
                formality = sp.get("formality")
                formality_label = None
                if formality is not None:
                    formality_label = {1: "very casual", 2: "casual", 3: "neutral",
                                       4: "formal", 5: "very formal"}.get(round(formality), "neutral")
                agent_info["style_params"] = {
                    "structure_type": sp.get("structure_type", ""),
                    "formality": formality,
                    "formality_label": formality_label,
                    "greeting_style": sp.get("greeting_style", ""),
                    "signoff": sp.get("signoff", ""),
                    "sample_count": sp.get("sample_count", 0),
                }
        else:
            print(f"  Result: No profiles loaded — infer() returned None")
    except Exception as e:
        print(f"  Habit Learner: skipped ({e})")
    print(f"  ─────────────────────────────────────────────────────")

    # ── Prompt Guide mode: user's prompt is the PRIMARY instruction ──
    if prompt_guide:
        print(f"\n  ── PROMPT GUIDE MODE ────────────────────────────────")
        print(f"  User prompt: {prompt_guide[:200]}...")
        print(f"  behavioral_context (secondary): {len(behavioral_text)} chars")

        from shared_tools.llm_config import get_llm

        prompt = f"""You are {email.get('assignee', 'Amy Chen')} ({email.get('assignee_email', 'amy@welink.com.au')}), a construction contract administrator.

ORIGINAL EMAIL:
Subject: {email.get('subject', '')}
Sender: {email.get('sender', '')}
CC: {email.get('cc', '')}
Content: {body}

YOUR BACKGROUND CONTEXT (Amy's usual style for similar emails):
{behavioral_text if behavioral_text else '(no behavioral data available — use standard professional tone)'}

USER'S PRIMARY INSTRUCTION (this is what you MUST follow above all else):
{prompt_guide}

Write a professional email reply following the user's instruction above. The reply must be the raw body text ONLY — STRICTLY OMIT:
- No subject line (no "Subject:" or "RE:")
- No greeting (no "Hi X," or "Dear Y,")
- No closing (no "Kind Regards", "Best regards", "Thanks", "Cheers")
- No signature block
- No preamble (no "Here is the response:")

Use the behavioral context above to match Amy's usual tone, formality, and style — but the user's instruction is the PRIMARY driver of what the reply should say and how it should be structured.

Output ONLY the raw body text."""
        try:
            llm = get_llm("fast")
            draft = llm.call(prompt).strip()
        except Exception as e:
            draft = f"Error generating reply: {e}"

        # Build agent_info for prompt guide mode
        agent_info = {
            "confidence": 0.5,
            "predicted_intent": "PROMPT GUIDE MODE",
            "matched_examples": 0,
            "behavioral_context": f"ANALYSING USER PROMPT AND INJECTING BEHAVIOURAL TEXT\n\nUser prompt: {prompt_guide[:500]}\n\nBehavioural context ({len(behavioral_text)} chars) used as secondary style reference.",
            "sender_profile": None,
            "style_params": None,
        }
        if ctx and ctx.sender_profile:
            sp = ctx.sender_profile
            agent_info["sender_profile"] = {
                "name": sp.get("sender_name", sp.get("sender_email", "")),
                "email": sp.get("sender_email", ""),
                "tier": sp.get("tier_label", "unknown"),
                "tier_level": sp.get("tier", 3),
                "reply_rate": sp.get("reply_rate", 0),
                "avg_latency_hours": sp.get("avg_latency_hours"),
                "avg_reply_words": sp.get("avg_reply_words"),
                "preferred_greeting": sp.get("preferred_greeting", ""),
                "signoff_preference": sp.get("signoff_preference", ""),
                "top_intent": sp.get("top_intent", ""),
                "total_received": sp.get("total_received", 0),
                "total_replied": sp.get("total_replied", 0),
            }
        else:
            agent_info = {
                "confidence": 0,
                "predicted_intent": None,
                "matched_examples": 0,
                "behavioral_context": "",
                "sender_profile": None,
                "style_params": None,
            }
    else:
        inputs = {
            "email_subject": email.get("subject", ""),
            "email_sender": email.get("sender", ""),
            "email_content": body,
            "email_category": email.get("category", "General"),
            "email_urgency": email.get("urgency", "low"),
            "email_context": email.get("chinese_summary", ""),
            "email_cc": email.get("cc", ""),
            "amy_name": "Amy Chen",
            "amy_email": "amy@welink.com.au",
            "relevant_facts": "",
            "relevant_schedule": "",
            "behavioral_context": behavioral_text,
        }

        print(f"\n  ── LLM Call ─────────────────────────────────────────")
        print(f"  Prompt template: reply_tasks.yaml")
        print(f"  Input variables: {', '.join(inputs.keys())}")
        print(f"  behavioral_context length: {len(behavioral_text)} chars")
        print(f"  email_content length: {len(body)} chars")
        print(f"  Calling ReplyGeneratorCrew.kickoff()...")

        result = ReplyGeneratorCrew().crew().kickoff(inputs=inputs)
        draft = result.raw if hasattr(result, "raw") else str(result)

    print(f"  ✅ LLM call complete")
    print(f"  Draft length: {len(draft)} chars")
    print(f"  Draft preview: {draft[:200]}...")
    print(f"{'═'*70}\n")

    # Persist the draft
    from shared_tools.ipc_bridge import upsert_processed_email
    email["reply_draft"] = draft.strip()
    upsert_processed_email(email)

    return {
        "entry_id": entry_id,
        "draft": draft.strip(),
        "agent_info": agent_info,
    }


@router.post("/emails/{entry_id}/remove")
async def remove_email(entry_id: str):
    """Soft-delete an email from the store."""
    from shared_tools.ipc_bridge import remove_processed_email
    ok = remove_processed_email(entry_id)
    return {"ok": ok}


class BatchEntryIds(BaseModel):
    entry_ids: list[str]


@router.post("/emails/mark-read")
async def mark_emails_read(data: BatchEntryIds):
    """Mark one or more Outlook emails as READ."""
    from shared_tools.outlook_tool import mark_email_as_read
    count = 0
    for eid in data.entry_ids:
        if mark_email_as_read(eid):
            count += 1
    return {"ok": True, "count": count}


@router.post("/emails/mark-unread")
async def mark_emails_unread(data: BatchEntryIds):
    """Mark one or more Outlook emails as UNREAD."""
    from shared_tools.outlook_tool import mark_email_as_unread
    count = 0
    for eid in data.entry_ids:
        if mark_email_as_unread(eid):
            count += 1
    return {"ok": True, "count": count}


@router.post("/emails/mark-flagged")
async def mark_emails_flagged(data: BatchEntryIds):
    """Flag one or more Outlook emails (set follow-up flag)."""
    from shared_tools.outlook_tool import mark_email_as_flagged
    count = 0
    for eid in data.entry_ids:
        if mark_email_as_flagged(eid):
            count += 1
    return {"ok": True, "count": count}


@router.post("/emails/attachments-check")
async def check_attachments(data: BatchEntryIds):
    """Batch check which emails have non-inline attachments.
    Returns {entry_id: count} mapping."""
    from shared_tools.outlook_tool import fetch_attachments_for_email
    result = {}
    for eid in data.entry_ids:
        try:
            atts = fetch_attachments_for_email(eid)
            result[eid] = len(atts)
        except Exception:
            result[eid] = 0
    return {"ok": True, "counts": result}


@router.get("/emails/{entry_id}/attachments")
async def list_attachments(entry_id: str):
    """List non-inline attachments for an email."""
    from shared_tools.outlook_tool import fetch_attachments_for_email
    try:
        atts = fetch_attachments_for_email(entry_id)
        return {"ok": True, "attachments": atts}
    except Exception as e:
        return {"ok": False, "error": str(e), "attachments": []}


@router.get("/emails/{entry_id}/attachments/{index}/download")
async def download_attachment(entry_id: str, index: int, open_inline: bool = False):
    """Download a single attachment by its 1-based index.
    If open_inline=True, uses inline disposition so browser may open it directly."""
    import tempfile
    from pathlib import Path
    from shared_tools.outlook_tool import save_attachment, fetch_attachments_for_email

    # Get filename first
    atts = fetch_attachments_for_email(entry_id)
    filename = None
    for a in atts:
        if a.get("index") == index:
            filename = a.get("filename", f"attachment_{index}")
            break
    if not filename:
        filename = f"attachment_{index}"

    tmp_dir = tempfile.mkdtemp()
    saved = save_attachment(entry_id, index, tmp_dir)
    if not saved or saved.startswith("Error:"):
        return {"error": saved or "Failed to save attachment"}

    from fastapi.responses import FileResponse
    ext = Path(filename).suffix.lower()
    mime_map = {
        ".pdf": "application/pdf",
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
        ".txt": "text/plain", ".csv": "text/csv",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    # Download mode: use octet-stream so no app (Adobe, etc.) intercepts the download
    # Open mode: use real MIME type so the browser can open it properly
    media_type = mime_map.get(ext) if open_inline else "application/octet-stream"

    return FileResponse(
        saved,
        media_type=media_type,
        filename=filename,
        content_disposition_type="inline" if open_inline else "attachment",
    )


@router.post("/emails/{entry_id}/refine")
async def refine_reply(entry_id: str, data: dict):
    """Refine an existing draft based on user instructions."""
    from shared_tools.llm_config import get_llm
    from shared_tools.ipc_bridge import get_processed_email, upsert_processed_email

    email = get_processed_email(entry_id)
    if not email:
        return {"error": "Email not found"}

    instructions = data.get("instructions", "")
    current_draft = data.get("draft", "")
    if not instructions or not current_draft:
        return {"error": "Missing instructions or draft"}

    prompt = (
        f"ORIGINAL EMAIL SUBJECT: {email.get('subject', '')}\n"
        f"ORIGINAL EMAIL SENDER: {email.get('sender', '')}\n\n"
        f"CURRENT DRAFT REPLY:\n{current_draft}\n\n"
        f"EDIT INSTRUCTIONS: {instructions}\n\n"
        f"Revise the draft reply based on the edit instructions. "
        f"Keep it professional and direct. Output ONLY the revised text."
    )
    try:
        llm = get_llm("fast")
        refined = llm.call(prompt).strip()
        email["reply_draft"] = refined
        upsert_processed_email(email)
        return {"entry_id": entry_id, "draft": refined}
    except Exception as e:
        return {"error": str(e)}


@router.get("/emails/{entry_id}")
async def get_email_detail(entry_id: str):
    """Return a single email with full detail.
    If body is empty, fetches from Outlook on demand."""
    from shared_tools.ipc_bridge import get_processed_email, upsert_processed_email
    email = get_processed_email(entry_id)
    if not email:
        return {"error": "Email not found"}

    # Backfill body from Outlook if missing
    if not email.get("body"):
        try:
            import win32com.client
            outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
            msg = outlook.GetItemFromID(entry_id)
            body = getattr(msg, "Body", "")[:5000]
            if body:
                email["body"] = body
                upsert_processed_email(email)
        except Exception:
            pass  # Outlook not available (web server, non-Windows)

    return email


@router.post("/emails/{entry_id}/open-in-outlook")
async def open_in_outlook(entry_id: str, reply_mode: bool = Query(False),
                           reply_all: bool = Query(False),
                           forward: bool = Query(False)):
    """Open the source email in Outlook.
    - reply_mode=True: reply to sender only
    - reply_all=True: reply to all recipients (overrides reply_mode)
    - forward=True: forward the email (includes all attachments)
    Requires Outlook COM (Windows only)."""
    try:
        import pythoncom
        pythoncom.CoInitialize()
        import win32com.client
        outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        mail = outlook.GetItemFromID(entry_id)
        if mail is None:
            return {"ok": False, "error": "Email not found in Outlook"}
        if forward:
            fwd = mail.Forward()
            fwd.Display()
        elif reply_all:
            reply = mail.ReplyAll()
            reply.Display()
        elif reply_mode:
            reply = mail.Reply()
            reply.Display()
        else:
            mail.Display()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
