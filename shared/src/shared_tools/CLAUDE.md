# CLAUDE.md — Shared Tools (Service Layer)

The foundation of the lilAmy platform. Every service, utility, and tool here follows the **service-first pattern**: QObject class, PyQt signals, `threading.Thread` + `queue.Queue` concurrency. UI layers in `tools/` are thin consumers.

## Services

| Service | File | Owns |
|---|---|---|
| MailService | `mail_service.py` | AMail pipeline: fetch → filter → triage → reply → workflow → grammar. State, threading, LLM orchestration, Outlook send, IPC. |
| CalendarService | `calendar_service.py` | Event CRUD, conflict detection, weekly digest, AMail status polling, Outlook push, IPC. |
| MemoryService | `memory_service.py` | ChromaDB ingestion, embedding (ONNX), hybrid search (content-first sorting). |
| FileRegistry | `file_registry.py` | SQLite file tracker: project, path, MD5 hash, incremental change detection, per-project stats. |
| PDFVisionService | `pdf_vision_service.py` | PDF → PNG render (PyMuPDF) → Gemini Flash vision → ChromaDB chunks. Persistent cache. |
| GraphService | `graph_service.py` | Microsoft Graph API: device-code OAuth, token cache, batched inferenceClassification queries. |

## Utilities

| Utility | File | Purpose |
|---|---|---|
| Outlook COM | `outlook_tool.py` | `fetch_inbox_emails()`, `mark_email_as_read()`, `OutlookSendTool`, attachment management. Windows-only. |
| IPC Bridge | `ipc_bridge.py` | Filesystem-based inter-app comm: lock files (presence), shared SQLite DB (`mail_history.db`). No networking. |
| LLM Config | `llm_config.py` | `get_llm(role)` — provider routing. Roles: `"fast"`, `"smart"`. Provider: `AI_PROVIDER` env var. |

## Service Pattern (the standard)

Every service must follow this template:

```python
from PyQt6.QtCore import QObject, pyqtSignal
import threading, queue

class ExampleService(QObject):
    # Signals — the UI connects to these
    result_ready = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)
    progress_update = pyqtSignal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._work_queue = queue.Queue()
        self._llm_semaphore = threading.Semaphore(1)  # serialize LLM calls
        self._running = False

    def start(self):
        self._running = True
        self._worker_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._worker_thread.start()

    def stop(self):
        self._running = False
        self._work_queue.put(None)  # poison pill

    def do_thing(self, arg1, arg2):
        """Public method called by UI."""
        self._work_queue.put(("do_thing", {"arg1": arg1, "arg2": arg2}))

    def _run_loop(self):
        while self._running:
            try:
                task = self._work_queue.get(timeout=0.5)
                if task is None:
                    break
                action, kwargs = task
                if action == "do_thing":
                    self._handle_do_thing(**kwargs)
            except queue.Empty:
                continue

    def _handle_do_thing(self, arg1, arg2):
        try:
            with self._llm_semaphore:
                result = some_crew.kickoff(inputs={...})
            self.result_ready.emit({"output": result.raw})
        except Exception as e:
            self.error_occurred.emit(str(e))
```

### Rules

1. **QObject + pyqtSignal** for async results — UI connects to signals
2. **`threading.Thread`** (NOT QThread) for internal concurrency
3. **`queue.Queue`** for work dispatch from public methods to worker threads
4. **`threading.Semaphore(1)`** for `_llm_semaphore` — serializes LLM calls
5. **Public methods are thin** — they queue work and return immediately (or return cached state)
6. **Daemon threads** — `daemon=True` so they don't block process exit
7. **Poison pill** — `queue.put(None)` in `stop()` to unblock the worker loop

## IPC Bridge (`ipc_bridge.py`)

Filesystem-based, no networking. All data flows through `<project_root>/data/mail_history.db`.

### Tables

| Table | Writer | Reader(s) | Purpose |
|---|---|---|---|
| `categorized_emails` | AMail (via `push_categorized_email`) | ACalendar, ASummary1 | Triage results with category, urgency, extra_info |
| `calendar_events` | ACalendar (via `push_calendar_events`) | AMail | Extracted dates: site visits, deadlines, milestones |
| `conflicts` | ACalendar (via `push_conflict`) | ACalendar | Overlapping event detection |
| `weekly_digests` | ACalendar | ACalendar | Digest history |

### Lock Files

`<data_dir>/{app_name}.lock` — JSON with `{app_name, pid, timestamp, status}`. `get_app_status("amail")` returns `None` if the app isn't running (stale PID cleaned automatically).

## LLM Config (`llm_config.py`)

```python
from shared_tools.llm_config import get_llm

llm = get_llm("fast")   # Gemini Flash / DeepSeek Chat — triage, filter, summarization
llm = get_llm("smart")  # Gemini Pro / DeepSeek Reasoner — complex reasoning, reply drafting
```

**Provider toggle:** `AI_PROVIDER=gem` (default, Gemini) or `AI_PROVIDER=ds` (DeepSeek). Set in `.env`.

**Never** construct `LLM()` or `ChatOpenAI()` directly outside this file — always route through `get_llm()`.

## Outlook COM (`outlook_tool.py`)

Windows-only. COM via `win32com.client`. All Outlook access MUST go through this module:

- `fetch_inbox_emails(count=20, max_body=3000, unread_only=False)` → `list[dict]`
- `mark_email_as_read(entry_id)` / `mark_email_as_unread(entry_id)`
- `OutlookSendTool` — CrewAI tool for sending emails with inline images + HTML signature
- `fetch_attachments_for_email(entry_id)` / `save_attachment(attachment, output_dir)`

## NEVER

- ❌ Create a new tool with `ChatOpenAI()` — use `get_llm(role)` from `llm_config.py`
- ❌ Call `win32com.client` directly — use `outlook_tool.py`
- ❌ Access the shared DB directly from tools — use `ipc_bridge.py` functions
- ❌ Use QThread — `threading.Thread` + `queue.Queue` is the standard
- ❌ Put `_llm_semaphore` at module level — it's an instance attribute of the service class
- ❌ Hardcode `<project_root>/data/mail_history.db` — use `ipc_bridge.DB_PATH`
- ❌ Add new services without PyQt signals — every service must emit results asynchronously

## Gotchas

### ONNX embedding configuration
`enable_cpu_mem_arena=False` is CRITICAL. Without it, 90+ embedding batches OOM-crash the machine. Singleton the ONNXMiniLM_L6_V2 instance (bypass ChromaDB's `@cached_property` which creates default SessionOptions). Thread count: 2 for both intra_op and inter_op.

### PyQt signals require event loop
When using services from CLI scripts, create a `QApplication` and call `processEvents()` in polling loops. Never call `app.exec()` in CLI mode.

### Gemini model names
`gemini-3.1-flash` returns 404. Use `gemini-3.1-flash-lite`. Check `.env` for the current `MODEL` setting.

### IPC bridge is filesystem-only
No networking, no sockets, no HTTP. The shared DB at `<project_root>/data/mail_history.db` is the sole communication channel between apps. Lock files provide presence detection (with stale PID cleanup).

### Thread safety
The `_llm_semaphore` prevents concurrent CrewAI kickoff calls (which would OOM with multiple ONNX sessions). All public service methods that queue work are thread-safe (queue.Queue is inherently thread-safe).
