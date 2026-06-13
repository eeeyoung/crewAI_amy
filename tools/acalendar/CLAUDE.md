# CLAUDE.md — ACalendar

Schedule dashboard for the lilAmy platform. Consumes categorized emails from AMail via IPC, extracts construction-relevant dates (site visits, deadlines, milestones), detects conflicts, and manages an Outlook-synced calendar. **Follows the service-first pattern: CalendarService owns all logic; GUI is a thin consumer.**

## Commands

```bash
uv sync                               # Install dependencies
uv run acalendar                      # Launch GUI dashboard
```

## Architecture

```
gui_viewer.py (PyQt6)          ← Thin UI: table views, EventEditDialog, digest
        │  calls methods, connects to signals
        ▼
CalendarService (shared_tools)  ← QObject owning ALL event logic
        │  uses threading.Thread (NOT QThread)
        ▼
IPC Bridge (ipc_bridge.py)      ← Shared DB: reads AMail categorized_emails
Outlook COM (outlook_tool.py)   ← Create/update/delete Outlook appointments
DateExtractorCrew (crew.py)     ← CrewAI: extracts dates from email bodies
```

## Source Map

| File | Purpose |
|---|---|
| `src/acalendar/main.py` | Entry point: registers IPC, launches GUI |
| `src/acalendar/crew.py` | DateExtractorCrew — Pydantic structured output |
| `src/acalendar/gui_viewer.py` | PyQt6 GUI — CalendarWindow, EventEditDialog (thin consumers of CalendarService) |
| `config/date_extractor_agents.yaml` | Date extractor agent definition |
| `config/date_extractor_tasks.yaml` | Date extraction task with Pydantic output schema |

## External Services Used

| Service | Source | What it provides |
|---|---|---|
| CalendarService | `shared_tools/calendar_service.py` | Event CRUD, conflict detection, weekly digest, AMail polling |
| IPC Bridge | `shared_tools/ipc_bridge.py` | Shared DB: `categorized_emails` table, `nav_request.json`, lock files |
| Outlook COM | `shared_tools/outlook_tool.py` | Create/update/delete Outlook appointments |
| LLM Config | `shared_tools/llm_config.py` | `get_llm(role)` — `"fast"` for date extraction |

## Data Flow

```
AMail                          ACalendar
─────                          ─────────
triage → categorized_emails ──→ CalendarService._pull_new_emails()
                                      │
                                      ▼
                                DateExtractorCrew
                                      │
                                      ▼
                                events table (shared DB)
                                      │
                        ┌─────────────┼──────────────┐
                        ▼             ▼              ▼
                   GUI table     Conflict Det.    Outlook sync
```

## Key Features

### Event Management
- CRUD via CalendarService methods (`create_event`, `update_event`, `delete_event`)
- Conflict detection: overlapping pending events flagged automatically
- Push to Outlook: creates native Outlook appointment, persists `outlook_event_id`

### Weekly Digest
- `CalendarService.build_weekly_digest()` — generates HTML email body
- `CalendarService.send_weekly_digest()` — sends via Outlook
- Covers: upcoming events, conflicts, overdue items, new categorized emails

### AMail Integration
- Polls AMail lock file to detect running status
- Pulls unconsumed categorized emails from shared DB
- `navigate_to_amail(entry_id)` — writes nav request so AMail can jump to source email

## NEVER

- ❌ Put event logic or Outlook COM calls in `gui_viewer.py` or `EventEditDialog` — CalendarService owns it
- ❌ Create new QThread subclasses — use CalendarService methods
- ❌ Access `ipc_bridge` functions directly from GUI — route through CalendarService
- ❌ Call `win32com.client` directly — use `outlook_tool.py`
- ❌ Add state tracking to CalendarWindow — CalendarService is the source of truth

## Gotchas

### CalendarService is the source of truth
All event state (`_events`, `_categorized_emails`) lives in CalendarService. GUI accesses it through service methods. Never cache event state in GUI widgets.

### IPC bridge path
The shared DB lives at `<project_root>/data/mail_history.db` (overridable via `LILAMY_DATA_DIR` env var). Both AMail and ACalendar must use the same path. The `ipc_bridge.py` module handles path resolution — import `CREWAI_DIR` or `DB_PATH`, don't construct the path manually.

### Date extraction uses Pydantic
`DateExtractorCrew` returns `ExtractedDates` (a Pydantic model with `list[ExtractedDate]`). Each `ExtractedDate` has: `description`, `date_type` (exact/approximate/range/tbd/deadline), `start_date`, `end_date`, `time_of_day`, `confidence`, `project`, and source email metadata.

### Outlook sync
Pushing to Outlook creates a native appointment. The `outlook_event_id` is persisted back to the shared DB so updates/deletes stay in sync. If an appointment is deleted from Outlook directly (not via ACalendar), the DB record becomes stale — this is a known limitation.

## Testing

No tests exist yet for ACalendar. This is a known gap. When adding tests:
- Mock `ipc_bridge` functions
- Mock `outlook_tool` functions (Windows-only COM)
- Test CalendarService CRUD operations
- Test conflict detection logic
- Test DateExtractorCrew with sample email bodies
