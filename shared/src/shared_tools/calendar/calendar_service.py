"""CalendarService — standalone ACalendar business-logic orchestrator.

Extracted from ACalendar's ``gui_viewer.py``.  Owns event CRUD, conflict
detection, weekly-digest generation, AMail status polling, and IPC operations.
Communicates with the GUI via PyQt signals.
"""

import json
from datetime import datetime, date, timedelta
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal


# ---------------------------------------------------------------------------
# CalendarService
# ---------------------------------------------------------------------------

class CalendarService(QObject):
    """Orchestrates ACalendar's data and business logic."""

    events_changed = pyqtSignal(list)           # full event list after change
    new_emails_arrived = pyqtSignal(int)        # count of unconsumed emails
    amail_status_changed = pyqtSignal(bool)    # is AMail running?

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._events: list[dict] = []
        self._amail_running = False

    # ------------------------------------------------------------------
    # Event CRUD
    # ------------------------------------------------------------------

    def load_events(self, project: str | None = None,
                    date_type: str | None = None,
                    status: str | None = None) -> list[dict]:
        """Fetch calendar events from the shared DB, optionally filtered."""
        from shared_tools.core.ipc_bridge import pull_calendar_events
        self._events = pull_calendar_events(
            project=project,
            date_type=date_type,
            status=status,
        )
        return self._events

    def create_event(self, data: dict) -> int:
        """Insert a new event into the shared DB via IPC push."""
        from shared_tools.core.ipc_bridge import push_calendar_events
        push_calendar_events([data])
        return 0  # ID assigned by SQLite; reload to get it

    def update_event(self, event_id: int, **fields) -> bool:
        """Update fields of an existing event in the shared DB."""
        from shared_tools.core.ipc_bridge import update_calendar_event_db
        update_calendar_event_db(event_id, **fields)
        return True

    def delete_event(self, event_id: int, also_delete_outlook: bool = False) -> bool:
        """Mark event as cancelled in DB; optionally delete from Outlook."""
        outlook_id = None
        if also_delete_outlook:
            for e in self._events:
                if e.get("id") == event_id:
                    outlook_id = e.get("outlook_event_id")
                    break
            if outlook_id:
                from shared_tools.outlook.outlook_tool import delete_calendar_event
                delete_calendar_event(outlook_id)
        from shared_tools.core.ipc_bridge import update_calendar_event_db
        update_calendar_event_db(event_id, status="cancelled")
        return True

    def push_to_outlook(self, event_id: int) -> str:
        """Create an Outlook appointment from a calendar event.
        Returns the Outlook EntryID or an error string."""
        event = next((e for e in self._events if e.get("id") == event_id), None)
        if not event:
            return "Error: event not found"

        from shared_tools.outlook.outlook_tool import create_calendar_event
        result = create_calendar_event(
            subject=event.get("description", "AMail Event"),
            start_date=event.get("start_date") or "",
            end_date=event.get("end_date") or event.get("start_date") or "",
            body=f"Source: {event.get('source_email_subject', '')}",
            location="",
            categories=[event.get("project", "")] if event.get("project") else None,
        )
        if not result.startswith("Error"):
            from shared_tools.core.ipc_bridge import update_calendar_event_db
            update_calendar_event_db(event_id, outlook_event_id=result, status="created")
        return result

    def update_outlook_event(self, event_id: int, **fields) -> bool:
        """Sync local event changes to the linked Outlook appointment."""
        event = next((e for e in self._events if e.get("id") == event_id), None)
        if not event or not event.get("outlook_event_id"):
            return False
        from shared_tools.outlook.outlook_tool import update_calendar_event
        return update_calendar_event(event["outlook_event_id"], **fields)

    # ------------------------------------------------------------------
    # Conflict detection
    # ------------------------------------------------------------------

    def detect_conflicts(self) -> list[dict]:
        """Find overlapping pending events. Returns list of conflict dicts."""
        conflicts = []
        pending = [
            e for e in self._events
            if e.get("status") == "pending" and e.get("start_date")
        ]
        for i in range(len(pending)):
            for j in range(i + 1, len(pending)):
                e1, e2 = pending[i], pending[j]
                s1 = e1.get("start_date") or ""
                e1_end = e1.get("end_date") or s1
                s2 = e2.get("start_date") or ""
                e2_end = e2.get("end_date") or s2
                if not s1 or not s2:
                    continue
                if s1 <= e2_end and s2 <= e1_end:
                    conflicts.append({
                        "event_id_1": e1["id"],
                        "event_id_2": e2["id"],
                        "conflict_type": "overlap" if s1 != s2 else "same_day",
                    })
        return conflicts

    # ------------------------------------------------------------------
    # Weekly digest
    # ------------------------------------------------------------------

    def build_weekly_digest(self) -> str:
        """Return HTML body for a weekly digest of upcoming events."""
        today = date.today()
        next_week = today + timedelta(days=7)
        upcoming = [
            e for e in self._events
            if e.get("start_date") and e.get("status") != "cancelled"
            and today <= datetime.fromisoformat(e["start_date"][:10]).date() <= next_week
        ]

        if not upcoming:
            return "<p>No upcoming events for the next 7 days.</p>"

        # Group by project
        by_project: dict[str, list[dict]] = {}
        for ev in upcoming:
            proj = ev.get("project") or "General"
            by_project.setdefault(proj, []).append(ev)

        lines = [
            "<h2>Weekly Calendar Digest</h2>",
            f"<p>{today} — {next_week}</p>",
        ]
        for proj, events in sorted(by_project.items()):
            lines.append(f"<h3>{proj}</h3><ul>")
            for ev in events:
                start = ev.get("start_date", "TBC")[:10]
                dtype = ev.get("date_type", "tbd")
                desc = ev.get("description", "Untitled")
                lines.append(f"<li><b>{start}</b> ({dtype}) — {desc}</li>")
            lines.append("</ul>")
        return "\n".join(lines)

    def send_weekly_digest(self, recipient: str) -> bool:
        """Build digest and send via Outlook. Returns True on success."""
        from shared_tools.outlook.outlook_tool import OutlookSendTool
        html = self.build_weekly_digest()
        tool = OutlookSendTool()
        result = tool._run(
            recipient=recipient,
            subject=f"Weekly Calendar Digest — {date.today()}",
            body=html,
            is_html=True,
        )
        ok = "successfully sent" in result.lower()
        if ok:
            # Log digest
            from shared_tools.core.ipc_bridge import init_shared_db
            import sqlite3
            init_shared_db()
            from shared_tools.core.ipc_bridge import DB_PATH
            conn = sqlite3.connect(str(DB_PATH))
            conn.execute(
                "INSERT INTO weekly_digests (sent_date, recipient) VALUES (?, ?)",
                (date.today().isoformat(), recipient),
            )
            conn.commit()
            conn.close()
        return ok

    # ------------------------------------------------------------------
    # AMail bridge
    # ------------------------------------------------------------------

    def poll_amail_status(self) -> bool:
        """Check whether AMail is running. Emits amail_status_changed."""
        from shared_tools.core.ipc_bridge import get_app_status
        running = get_app_status("amail")
        if running != self._amail_running:
            self._amail_running = running
            self.amail_status_changed.emit(running)
        return running

    def pull_new_emails(self) -> list[dict]:
        """Pull unconsumed categorized emails from the shared DB."""
        from shared_tools.core.ipc_bridge import (
            pull_new_categorized_emails, mark_email_consumed,
        )
        emails = pull_new_categorized_emails()
        if emails:
            for em in emails:
                mark_email_consumed(em.get("email_entry_id", ""))
            self.new_emails_arrived.emit(len(emails))
        return emails

    @staticmethod
    def navigate_to_amail(source_entry_id: str):
        """Write a nav-request file that AMail will pick up."""
        from shared_tools.core.ipc_bridge import CREWAI_DIR
        nav_path = CREWAI_DIR / "nav_request.json"
        nav_path.parent.mkdir(parents=True, exist_ok=True)
        nav_path.write_text(json.dumps({
            "target_entry_id": source_entry_id,
            "from": "acalendar",
            "timestamp": datetime.now().isoformat(),
        }), encoding="utf-8")

    @staticmethod
    def consume_emails(email_ids: list[str]):
        from shared_tools.core.ipc_bridge import mark_email_consumed
        for eid in email_ids:
            mark_email_consumed(eid)
