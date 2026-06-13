"""AMail REST API — wraps MailService as HTTP endpoints."""

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/amail", tags=["AMail"])

# Lazy-init service (singleton)
_service = None


def _get_service():
    global _service
    if _service is None:
        from shared_tools.mail_service import MailService
        _service = MailService(auto_refresh=False)
        _service.start()
    return _service


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


class EmailList(BaseModel):
    count: int
    emails: list[dict]


# ── Routes ─────────────────────────────────────────────────────────────

@router.get("/emails")
async def get_emails(status: str = Query("active"), limit: int = Query(0)):
    """Return all processed emails (from local DB, instant)."""
    from shared_tools.ipc_bridge import get_processed_emails
    emails = get_processed_emails(status=status, limit=limit)
    return {"count": len(emails), "emails": emails}


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
async def sync_inbox():
    """Full reconciliation: fetch earlier → detect changes → update bodies."""
    svc = _get_service()
    svc.sync_inbox()
    return {"status": "started", "message": "Sync started — fetch earlier, detect changes, update bodies"}


@router.post("/emails/{entry_id}/reply")
async def generate_reply(entry_id: str):
    """Generate an AI reply for a specific email (lazy)."""
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
            pass

    from amail.crew import ReplyGeneratorCrew
    body = email.get("body") or email.get("email_body", "")
    inputs = {
        "email_subject": email.get("subject", ""),
        "email_sender": email.get("sender", ""),
        "email_content": body,
        "email_category": email.get("category", "General"),
        "email_context": email.get("chinese_summary", ""),
        "email_cc": "",
        "amy_name": "Amy Chen",
        "amy_email": "amy@welink.com.au",
        "relevant_facts": "",
    }
    result = ReplyGeneratorCrew().crew().kickoff(inputs=inputs)
    draft = result.raw if hasattr(result, "raw") else str(result)

    # Persist the draft
    from shared_tools.ipc_bridge import upsert_processed_email
    email["reply_draft"] = draft.strip()
    upsert_processed_email(email)

    return {"entry_id": entry_id, "draft": draft.strip()}


@router.post("/emails/{entry_id}/remove")
async def remove_email(entry_id: str):
    """Soft-delete an email from the store."""
    from shared_tools.ipc_bridge import remove_processed_email
    ok = remove_processed_email(entry_id)
    return {"ok": ok}


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
