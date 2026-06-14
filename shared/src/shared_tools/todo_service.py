"""TodoService — manages the To-Do List for the lilAmy platform.

Service-first pattern: QObject + pyqtSignal + threading.Thread + queue.Queue.

Usage:
    service = get_todo_service()
    service.push_from_emails(["entry_id_1", "entry_id_2"])  # async
    ctx = service.push_from_emails_sync(["entry_id_1"])      # sync (for CLI/API)
    service.load_items()                                      # emit todos_changed
"""

import json
import queue
import threading
import uuid

from PyQt6.QtCore import QObject, pyqtSignal


# ---------------------------------------------------------------------------
# Singleton accessor (follows habit_learner_service pattern)
# ---------------------------------------------------------------------------

_todo_service: "TodoService | None" = None


def get_todo_service() -> "TodoService":
    """Return the process-wide TodoService singleton, creating it if needed."""
    global _todo_service
    if _todo_service is None:
        _todo_service = TodoService()
        _todo_service.start()
    return _todo_service


# ---------------------------------------------------------------------------
# TodoService
# ---------------------------------------------------------------------------

class TodoService(QObject):
    """CRUD and push-from-emails logic for the To-Do List module."""

    # Signals
    todos_changed = pyqtSignal(list)       # full item list after any change
    items_pushed = pyqtSignal(int)         # count of items pushed from emails
    error_occurred = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._items: list[dict] = []
        self._work_queue: queue.Queue = queue.Queue()
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        """Start the worker thread."""
        if self._running:
            return
        self._running = True
        t = threading.Thread(target=self._run_loop, daemon=True, name="todo-svc")
        t.start()
        self.load_items()

    def stop(self):
        """Stop the worker thread."""
        self._running = False
        self._work_queue.put(None)  # poison pill

    # ------------------------------------------------------------------
    # Public API — synchronous / immediate
    # ------------------------------------------------------------------

    def load_items(self, status: str | None = None, limit: int = 0) -> list[dict]:
        """Reload items from DB and emit todos_changed. Returns the list."""
        from shared_tools.ipc_bridge import get_todo_items
        self._items = get_todo_items(status=status, limit=limit)
        _emit(self.todos_changed, self._items)
        return self._items

    def update_item(self, entry_id: str, **fields) -> bool:
        """Update fields of a to-do item. Emits todos_changed on success."""
        from shared_tools.ipc_bridge import update_todo_item
        ok = update_todo_item(entry_id, **fields)
        if ok:
            self.load_items()
        return ok

    def delete_item(self, entry_id: str) -> bool:
        """Soft-delete a to-do item. Emits todos_changed on success."""
        from shared_tools.ipc_bridge import delete_todo_item
        ok = delete_todo_item(entry_id)
        if ok:
            self.load_items()
        return ok

    def create_item(self, data: dict) -> bool:
        """Create a single manual to-do item (no source email)."""
        from shared_tools.ipc_bridge import upsert_todo_item
        item = {
            "entry_id": str(uuid.uuid4()),
            "source_email_id": data.get("source_email_id"),
            "description": data.get("description", ""),
            "category": data.get("category", "General"),
            "urgency": data.get("urgency", "low"),
            "assignee": data.get("assignee", ""),
            "status": "pending",
            "deadline_date": data.get("deadline_date"),
            "deadline_type": data.get("deadline_type", "tbd"),
            "project": data.get("project", ""),
        }
        ok = upsert_todo_item(item)
        if ok:
            self.load_items()
        return ok

    # ------------------------------------------------------------------
    # Public API — async (queue-based)
    # ------------------------------------------------------------------

    def push_from_emails(self, email_ids: list[str]):
        """Push selected emails' todos+deadlines into todo_items (async).
        Runs in worker thread; emits items_pushed when done."""
        self._work_queue.put(("push_emails", {"email_ids": email_ids}))

    # ------------------------------------------------------------------
    # Synchronous push (for CLI / FastAPI direct calls)
    # ------------------------------------------------------------------

    def push_from_emails_sync(self, email_ids: list[str]) -> int:
        """Synchronous version — useful for API endpoints that want the
        count immediately without threading/signals."""
        return self._handle_push_emails(email_ids)

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------

    def _run_loop(self):
        while self._running:
            try:
                task = self._work_queue.get(timeout=0.5)
                if task is None:
                    break
                action, kwargs = task
                if action == "push_emails":
                    count = self._handle_push_emails(**kwargs)
                    _emit(self.items_pushed, count)
            except queue.Empty:
                continue

    def _handle_push_emails(self, email_ids: list[str]) -> int:
        """For each email ID, read its todos_json + deadlines_json from
        processed_emails and create todo_items rows."""
        from shared_tools.ipc_bridge import get_processed_email, upsert_todo_item

        count = 0
        for eid in email_ids:
            email = get_processed_email(eid)
            if not email:
                continue

            # ── Push todos as todo items ───────────────────────────
            try:
                todos = json.loads(email.get("todos_json", "[]"))
            except (json.JSONDecodeError, TypeError):
                todos = []

            for todo_text in todos:
                if not todo_text or not todo_text.strip():
                    continue
                item = {
                    "entry_id": str(uuid.uuid4()),
                    "source_email_id": eid,
                    "description": todo_text.strip(),
                    "category": email.get("category", "General"),
                    "urgency": email.get("urgency", "low"),
                    "assignee": email.get("assignee", ""),
                    "status": "pending",
                    "deadline_date": None,
                    "deadline_type": "tbd",
                    "project": "",
                }
                upsert_todo_item(item)
                count += 1

            # ── Push deadlines as todo items ───────────────────────
            try:
                deadlines = json.loads(email.get("deadlines_json", "[]"))
            except (json.JSONDecodeError, TypeError):
                deadlines = []

            for dl in deadlines:
                desc = dl.get("description", "") if isinstance(dl, dict) else str(dl)
                if not desc or not desc.strip():
                    continue
                start_date = dl.get("start_date") if isinstance(dl, dict) else None
                date_type = dl.get("date_type", "tbd") if isinstance(dl, dict) else "tbd"
                item = {
                    "entry_id": str(uuid.uuid4()),
                    "source_email_id": eid,
                    "description": desc.strip(),
                    "category": email.get("category", "General"),
                    "urgency": email.get("urgency", "low"),
                    "assignee": email.get("assignee", ""),
                    "status": "pending",
                    "deadline_date": start_date,
                    "deadline_time": "12:00" if start_date else None,
                    "deadline_type": date_type,
                    "project": "",
                }
                upsert_todo_item(item)
                count += 1

        # Reload after batch push
        self.load_items()
        return count


# ---------------------------------------------------------------------------
# Helper — safe signal emit
# ---------------------------------------------------------------------------

def _emit(signal: pyqtSignal, *args):
    """Emit a PyQt signal safely (no-op if no event loop, e.g. CLI)."""
    try:
        signal.emit(*args)
    except Exception:
        pass  # signal emission is best-effort
