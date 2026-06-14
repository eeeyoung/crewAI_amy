"""To-Do List REST API — wraps TodoService as HTTP endpoints."""

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/todo", tags=["To-Do List"])


# ── Schemas ────────────────────────────────────────────────────────────

class TodoItemCreate(BaseModel):
    description: str
    category: str = "General"
    urgency: str = "low"
    assignee: str = ""
    deadline_date: str | None = None
    deadline_type: str = "tbd"
    project: str = ""


class TodoItemUpdate(BaseModel):
    description: str | None = None
    category: str | None = None
    urgency: str | None = None
    assignee: str | None = None
    status: str | None = None
    deadline_date: str | None = None
    deadline_time: str | None = None
    deadline_type: str | None = None
    project: str | None = None


class PushFromEmailsRequest(BaseModel):
    email_ids: list[str]


# ── Routes ─────────────────────────────────────────────────────────────

@router.get("/counts")
async def get_counts():
    """Return counts for all statuses in one lightweight call."""
    from shared_tools.ipc_bridge import get_todo_items
    all_items = get_todo_items(status=None, limit=0)
    pending = get_todo_items(status="pending", limit=0)
    done = get_todo_items(status="done", limit=0)
    cancelled = get_todo_items(status="cancelled", limit=0)
    return {
        "all": len(all_items),
        "pending": len(pending),
        "done": len(done),
        "cancelled": len(cancelled),
    }


@router.get("/items")
async def list_items(status: str = Query(None), limit: int = Query(0)):
    """Return all to-do items, optional status filter (pending/done/cancelled)."""
    from shared_tools.todo_service import get_todo_service
    svc = get_todo_service()
    items = svc.load_items(status=status, limit=limit)
    return {"count": len(items), "items": items}


@router.get("/items/{entry_id}")
async def get_item(entry_id: str):
    """Return a single to-do item by its UUID entry_id."""
    from shared_tools.ipc_bridge import get_todo_item
    item = get_todo_item(entry_id)
    if not item:
        return {"error": "To-do item not found"}
    return item


@router.post("/items")
async def create_item(data: TodoItemCreate):
    """Create a manual to-do item (no linked source email)."""
    from shared_tools.todo_service import get_todo_service
    svc = get_todo_service()
    ok = svc.create_item(data.model_dump())
    return {"ok": ok}


@router.patch("/items/{entry_id}")
async def update_item(entry_id: str, data: TodoItemUpdate):
    """Partially update a to-do item. Only provided fields are changed.
    Auto-sets updated_at. Returns the updated item."""
    from shared_tools.ipc_bridge import get_todo_item, update_todo_item

    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        return {"error": "No fields to update"}

    ok = update_todo_item(entry_id, **fields)
    if not ok:
        return {"error": "Update failed"}

    # Return the updated item
    item = get_todo_item(entry_id)
    return {"ok": True, "item": item}


@router.delete("/items/{entry_id}")
async def delete_item(entry_id: str):
    """Soft-delete a to-do item (sets status = 'cancelled')."""
    from shared_tools.todo_service import get_todo_service
    svc = get_todo_service()
    ok = svc.delete_item(entry_id)
    return {"ok": ok}


@router.post("/push-from-emails")
async def push_from_emails(data: PushFromEmailsRequest):
    """Push selected emails into the to-do list.
    Reads todos_json + deadlines_json from each email and creates todo_items.
    Returns the count of items created."""
    from shared_tools.todo_service import get_todo_service
    svc = get_todo_service()
    count = svc.push_from_emails_sync(data.email_ids)
    return {"ok": True, "count": count}


@router.post("/items/{entry_id}/restore")
async def restore_item(entry_id: str):
    """Restore a cancelled to-do item back to pending status."""
    from shared_tools.ipc_bridge import restore_todo_item, get_todo_item
    ok = restore_todo_item(entry_id)
    if not ok:
        return {"error": "Restore failed"}
    item = get_todo_item(entry_id)
    return {"ok": True, "item": item}


@router.delete("/items/{entry_id}/permanent")
async def permanent_delete_item(entry_id: str):
    """Permanently delete a to-do item from the database."""
    from shared_tools.ipc_bridge import hard_delete_todo_item
    ok = hard_delete_todo_item(entry_id)
    return {"ok": ok}


@router.post("/push-to-calendar")
async def push_to_calendar(data: dict):
    """Push selected to-do items to Outlook Calendar as appointments.
    Requires each item to have both deadline_date AND deadline_time set.
    Body: {"todo_ids": ["uuid1", "uuid2"]}
    Returns list of results and a list of items missing deadline datetime."""
    from shared_tools.ipc_bridge import get_todo_item
    from shared_tools.outlook_tool import create_calendar_event

    todo_ids = data.get("todo_ids", [])
    if not todo_ids:
        return {"error": "No to-do items specified"}

    # ── Validate all items have deadline_date AND deadline_time ──────
    missing = []
    valid = []
    for tid in todo_ids:
        item = get_todo_item(tid)
        if not item:
            continue
        if not item.get("deadline_date") or not item.get("deadline_time"):
            missing.append({
                "entry_id": tid,
                "description": item.get("description", "")[:60],
            })
        else:
            valid.append(item)

    if missing:
        return {
            "ok": False,
            "error": "Some items are missing deadline date or time",
            "missing": missing,
        }

    # ── Create Outlook appointments ──────────────────────────────────
    import pythoncom
    pythoncom.CoInitialize()
    import win32com.client
    from datetime import datetime, timedelta

    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
    except Exception as e:
        return {"ok": False, "error": f"Outlook COM init failed: {e}"}

    results = []
    for item in valid:
        try:
            # Normalize: deadline_date may be full ISO datetime from LLM
            date_str = item['deadline_date'][:10]
            time_str = item['deadline_time']
            if time_str.count(':') == 1:
                time_str += ':00'  # "13:00" → "13:00:00"
            start_dt = f"{date_str}T{time_str}"
            start = datetime.fromisoformat(start_dt)
            end = start + timedelta(hours=1)

            body_parts = [item.get("description", "")]
            if item.get("project"):
                body_parts.append(f"Project: {item['project']}")

            appointment = outlook.CreateItem(1)  # olAppointmentItem
            appointment.Subject = item.get("description", "Task")[:100]
            appointment.Start = start.strftime("%Y-%m-%d %H:%M")
            appointment.End = end.strftime("%Y-%m-%d %H:%M")
            appointment.Body = "\n".join(body_parts)
            appointment.ReminderMinutesBeforeStart = 15
            if item.get("project"):
                appointment.Categories = item["project"]

            appointment.Save()
            results.append({"entry_id": item["entry_id"], "ok": True, "outlook_id": str(appointment.EntryID)})
        except Exception as e:
            print(f"[CALENDAR PUSH] Error for {item['entry_id']}: {e}")
            results.append({"entry_id": item["entry_id"], "ok": False, "error": str(e)})

    return {"ok": True, "results": results}
