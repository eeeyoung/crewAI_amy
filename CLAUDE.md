# CLAUDE.md — lilAmy Platform

## Core Architectural Principle

**Always separate core logic from UI.** Every feature must be built as a standalone service class with a clean Python API before any GUI is attached. The UI layer (PyQt6, FastAPI, web frontend) must be a thin consumer of the service — never the owner of business logic.

### The Pattern (established by MailService & CalendarService)

```
┌──────────────────────┐
│  UI Layer             │  PyQt6 / FastAPI / React / CLI
│  (thin, replaceable)  │  calls service methods, connects to signals
└──────────┬───────────┘
           │  Python method calls
┌──────────▼───────────┐
│  Service Class        │  QObject with signals, owns all logic
│  (reusable, testable) │  threading, state, CrewAI, COM, DB, IPC
└──────────────────────┘
```

### Rules

1. **New features → service class first.** Create a service in `shared/src/shared_tools/` with public methods, PyQt signals for async results, and `threading.Thread` + `queue.Queue` for internal concurrency.
2. **UI calls service, never touches internals.** GUI widgets call `self.service.do_thing()`. They never access queues, threads, or raw state dicts directly.
3. **Forwarding properties for minimal change.** When refactoring existing GUI code, use `@property` to delegate `self.state` → `self.service._state` — keeps existing GUI code working without rewrites.
4. **Pydantic schemas at API boundaries.** When adding HTTP/FastAPI later, a thin Pydantic model layer translates between stable JSON contracts and the service's internal Python objects.
5. **No package bloat.** Use only `requests` (already present) for HTTP calls. Do not install new packages without explicit approval.

### Existing Services

| Service | Location | Purpose |
|---|---|---|
| `MailService` | `shared/src/shared_tools/mail_service.py` | AMail pipeline: fetch → filter → triage → reply → workflow, plus Outlook send, fact extraction, grammar polish, IPC |
| `CalendarService` | `shared/src/shared_tools/calendar_service.py` | Event CRUD, conflict detection, weekly digest, AMail status polling, IPC |

### Project Structure

```
crewAI_amy/
├── shared/src/shared_tools/   ← reusable services & utilities
│   ├── mail_service.py
│   ├── calendar_service.py
│   ├── outlook_tool.py        ← Outlook COM wrappers
│   ├── ipc_bridge.py          ← AMail↔ACalendar shared DB
│   └── llm_config.py          ← LLM provider routing
├── tools/
│   ├── amail/                 ← AMail (email triage)
│   └── acalendar/             ← ACalendar (schedule dashboard)
├── lilAmy_Architecture_and_Roadmap.md
└── SERVICE_EXTRACTION_PLAN.md
```

### Testing

```bash
uv run pytest tools/amail/tests/ -v    # 83 pass, 6 pre-existing failures (test_crew.py)
```
