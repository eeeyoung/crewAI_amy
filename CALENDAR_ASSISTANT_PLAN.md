# ACalendar — Implementation Plan

This document describes the complete design and phased implementation plan for an
ACalendar agent that integrates with the existing AMail email agent. Any LLM or
developer should be able to read this and understand what to build, in what order, and why.

---

## 1. Architecture Overview

### Multi-Tool Workspace (current state after Phase 2+3)

```
lilamy/
├── pyproject.toml              # uv workspace: members = ["tools/*", "shared"]
├── shared/src/shared_tools/
│   ├── llm_config.py           # get_llm() — provider toggle (DeepSeek/Gemini)
│   ├── outlook_tool.py         # fetch_inbox_emails, OutlookSendTool, mark_read/unread,
│   │                           # fetch_attachments, save_attachment, fetch_outlook_contacts
│   └── ipc_bridge.py           # NEW — inter-app communication (Phase B)
├── tools/amail/                  # email agent (existing)
└── tools/acalendar/              # calendar agent (to be built)
```

### Inter-App Communication (the five improvements)

Both apps connect through a **shared SQLite database** at `~/.crewai/shared_data.db`.
There is no local HTTP server, no port conflicts, no firewall issues. Each app reads and
writes independently. Presence is detected via lock files at `~/.crewai/<app>.lock`.

```
┌──────────────────────────┐          ┌──────────────────────────┐
│         AMail             │          │       ACalendar         │
│                          │          │                          │
│  1. Triage emails        │          │  3. Poll shared DB       │
│     → categorizes by     │          │     → read new triage    │
│       domain & urgency   │─────────→│       results            │
│                          │  writes  │                          │
│  2. Detect scheduling    │ triage_  │  4. Extract dates from   │
│     emails → flag them   │ results  │     email body using LLM │
│                          │          │                          │
│  6. Read events when     │          │  5. Detect conflicts     │
│     composing replies ◄──│──────────│     → flag before saving │
│                          │  writes  │                          │
│  7. "Open in AMail" from │ calendar_│  6. Create in Outlook    │
│     ACalendar → jumps to  │ events   │     Calendar (COM API)   │
│     source email         │          │                          │
│                          │          │  7. Weekly digest email  │
└──────────────────────────┘          └──────────────────────────┘
         │                                     │
         └───────────┬─────────────────────────┘
                     │
          ~/.crewai/
          ├── shared_data.db      SQLite database
          ├── amail.lock           PID + timestamp + status
          └── acalendar.lock       PID + timestamp + status
```

**Why this approach:**
- No networking — works offline, no CORS, no firewall popups
- Single writer per table avoids locking conflicts (AMail writes `triage_results`,
  ACalendar writes `calendar_events`, both read freely)
- Lock files provide live presence detection (green dot in GUI)
- Survives app restarts — data persists in SQLite

---

## 2. Shared Database Schema

Located at `~/.crewai/shared_data.db`. Created automatically on first access.

```sql
-- Written by AMail after triage
CREATE TABLE IF NOT EXISTS triage_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_entry_id TEXT UNIQUE NOT NULL,       -- Outlook EntryID
    email_subject TEXT,
    email_sender TEXT,
    email_body TEXT,                           -- filtered/cleaned body
    category TEXT,                             -- RFI, Submittal, Scheduling, Financial, etc.
    urgency TEXT,                              -- High, Medium, Low
    extra_info TEXT,                           -- Triage context
    created_at TEXT DEFAULT (datetime('now')),
    consumed_by_calendar INTEGER DEFAULT 0     -- 0 = new, 1 = processed
);

-- Written by ACalendar after date extraction & Outlook creation
CREATE TABLE IF NOT EXISTS calendar_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_email_entry_id TEXT,                -- NULL if manually created
    source_email_subject TEXT,
    source_email_sender TEXT,
    description TEXT NOT NULL,                 -- "Concrete pour inspection - ARCO"
    date_type TEXT NOT NULL,                   -- exact | approximate | range | tbd | deadline
    start_date TEXT,                           -- ISO date (NULL for TBD)
    end_date TEXT,                             -- ISO date (NULL for single-day or TBD)
    confidence REAL DEFAULT 1.0,               -- 0.0 to 1.0
    project TEXT,                              -- e.g., "ARCO", "Econolodge" (extracted or manual)
    outlook_event_id TEXT,                     -- set after Create in Outlook
    status TEXT DEFAULT 'pending',             -- pending | created | confirmed | cancelled
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Written by ACalendar for conflict detection
CREATE TABLE IF NOT EXISTS conflicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id_1 INTEGER REFERENCES calendar_events(id),
    event_id_2 INTEGER REFERENCES calendar_events(id),
    conflict_type TEXT,                        -- overlap | same_day | adjacent
    resolved INTEGER DEFAULT 0
);

-- Written by ACalendar on user request
CREATE TABLE IF NOT EXISTS weekly_digests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start TEXT NOT NULL,
    events_json TEXT,                          -- JSON array of event summaries
    sent_at TEXT DEFAULT (datetime('now'))
);
```

---

## 3. Shared Code Additions

### Phase A — Outlook Calendar APIs in `shared/src/shared_tools/outlook_tool.py`

Add these functions (Windows-only, COM-based, same pattern as email functions):

```python
def create_calendar_event(
    subject: str,
    start_date: str,      # ISO format "2026-06-15T14:00:00"
    end_date: str,
    body: str = "",
    location: str = "",
    reminder_minutes: int = 15,
    categories: list[str] | None = None,  # Outlook categories for job grouping
) -> str:
    """Create an Outlook calendar appointment. Returns the EntryID or error string."""

def get_calendar_events(
    start_date: str,       # ISO format
    end_date: str,
) -> list[dict]:
    """Fetch Outlook calendar events in a date range. Returns list of event dicts."""

def delete_calendar_event(event_entry_id: str) -> bool:
    """Delete an Outlook calendar event by EntryID."""

def update_calendar_event(event_entry_id: str, **kwargs) -> bool:
    """Update fields of an existing calendar event."""
```

Implementation uses `win32com.client.Dispatch("Outlook.Application")` →
`CreateItem(1)` (olAppointmentItem), and `GetDefaultFolder(9)` (olFolderCalendar)
for reading.

### Phase B — Inter-App Bridge in `shared/src/shared_tools/ipc_bridge.py`

```python
import os
import json
import sqlite3
import time
from pathlib import Path

CREWAI_DIR = Path.home() / ".crewai"
DB_PATH = CREWAI_DIR / "shared_data.db"

# ── Presence ──────────────────────────────────────────────

def register_app(app_name: str) -> dict:
    """Write a lock file with PID, timestamp, status. Returns lock info dict."""

def unregister_app(app_name: str) -> None:
    """Remove the lock file on clean shutdown."""

def get_app_status(app_name: str) -> dict | None:
    """Read another app's lock file. Returns None if not running.
    Also checks if the PID is still alive (stale lock detection)."""

# ── Data Exchange ─────────────────────────────────────────

def init_shared_db() -> sqlite3.Connection:
    """Create tables if they don't exist. Called on app startup."""

def push_triage_result(email_data: dict) -> int:
    """AMail calls this after triage. Inserts into triage_results. Returns row id."""

def pull_new_triage_results() -> list[dict]:
    """ACalendar calls this. Returns unconsumed triage results (consumed_by_calendar=0)."""

def mark_triage_consumed(email_entry_id: str) -> None:
    """ACalendar calls this after processing a triage result."""

def push_calendar_events(events: list[dict]) -> list[int]:
    """ACalendar calls this after extracting dates. Inserts into calendar_events."""

def pull_calendar_events(
    project: str | None = None,
    date_type: str | None = None,
    status: str | None = None,
) -> list[dict]:
    """AMail calls this when composing replies. Returns filtered events."""

def push_conflict(event_id_1: int, event_id_2: int, conflict_type: str) -> int:
    """ACalendar calls this when conflict detected."""

def pull_conflicts(resolved: bool = False) -> list[dict]:
    """Pull unresolved conflicts."""

def resolve_conflict(conflict_id: int) -> None:
    """Mark a conflict as resolved."""
```

---

## 4. ACalendar Project (`tools/acalendar/`)

### Phase C — Scaffold & Date Extractor Crew

#### `tools/acalendar/pyproject.toml`

```toml
[project]
name = "acalendar"
version = "0.1.0"
description = "ACalendar — date extraction and scheduling"
requires-python = ">=3.10,<3.14"
dependencies = [
    "crewai[google-genai,tools]==1.14.2",
    "google-generativeai>=0.8.6",
    "pyqt6>=6.11.0",
    "pywin32>=308; sys_platform == 'win32'",
    "shared-tools",
]

[tool.uv.sources]
shared-tools = { workspace = true }

[project.scripts]
acalendar = "acalendar.main:run"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.crewai]
type = "crew"
```

#### `tools/acalendar/src/acalendar/crew.py` — DateExtractorCrew

A single-crew project. One agent specializes in extracting temporal information from
construction emails:

```python
from shared_tools.llm_config import get_llm
from pydantic import BaseModel

class ExtractedDate(BaseModel):
    description: str           # "Concrete pour inspection"
    date_type: str             # exact | approximate | range | tbd | deadline
    start_date: str | None     # ISO date "2026-06-15" or None
    end_date: str | None       # ISO date for ranges, or None
    time_of_day: str | None    # "14:00" or "morning" or None
    confidence: float          # 0.0 to 1.0
    project: str | None        # extracted from context, e.g. "ARCO"
    source_email_subject: str
    source_email_sender: str

class ExtractedDates(BaseModel):
    dates: list[ExtractedDate]
    no_dates_found: bool

@CrewBase
class DateExtractorCrew:
    agents_config = 'config/date_extractor_agents.yaml'
    tasks_config = 'config/date_extractor_tasks.yaml'

    @agent
    def date_extractor(self) -> Agent:
        return Agent(
            config=self.agents_config['date_extractor'],
            llm=get_llm("fast"),
            verbose=True,
        )

    @task
    def extract_dates_task(self) -> Task:
        return Task(
            config=self.tasks_config['extract_dates_task'],
            output_pydantic=ExtractedDates,
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
```

#### `tools/acalendar/src/acalendar/config/date_extractor_agents.yaml`

```yaml
date_extractor:
  role: >
    Construction Project Date Extraction Specialist
  goal: >
    Identify and extract ALL date references from construction email communications.
    Categorize each as exact, approximate, range, deadline, or TBD. Assign a confidence
    score based on how specific the date is. Identify the project/job name when present.
  backstory: >
    You are an expert scheduler who has managed timelines for dozens of construction
    projects. You can spot dates in any format — formal ("15 June 2026"), informal
    ("next Tuesday"), relative ("in 2 weeks"), ranges ("between the 10th-15th"),
    deadlines ("due by Friday"), and unconfirmed ("around mid-July", "TBC").
    You understand construction terminology and can distinguish between different
    job sites mentioned in the same email.
```

#### `tools/acalendar/src/acalendar/config/date_extractor_tasks.yaml`

```yaml
extract_dates_task:
  description: >
    Analyze the following email and extract ALL date references related to
    construction activities, deadlines, meetings, or milestones.

    Email Subject: {email_subject}
    Email Sender: {email_sender}
    Email Category (from AMail): {email_category}
    Email Content: {email_content}

    For each date found, classify it as one of:
    - "exact": A specific calendar date and/or time (e.g., "June 15th at 2pm", "15/06/2026")
    - "approximate": An imprecise date (e.g., "around mid-July", "early August", "late next week")
    - "range": A span between two dates (e.g., "between June 10-15", "week of the 20th")
    - "deadline": A due date (e.g., "due by Friday", "must be submitted before 30/6")
    - "tbd": Date explicitly stated as unconfirmed (e.g., "TBC", "to be confirmed", "TBD", "we'll confirm")

    For each date, also:
    - Extract the associated project/job name when it appears in context
    - Assign a confidence score (0.0-1.0) based on specificity
    - Capture the time of day if mentioned (e.g., "2pm", "morning")
    - If no dates are found, set no_dates_found = true

    RELATIVE DATES: If the email says "next Tuesday" or "in 2 weeks", resolve these
    to actual calendar dates using the current date as reference. The current date is
    provided in the system prompt.

  expected_output: >
    A structured ExtractedDates object containing all dates found in the email,
    or no_dates_found = true if nothing was found.
  agent: date_extractor
```

---

## 5. ACalendar GUI (`tools/acalendar/src/acalendar/gui_viewer.py`)

### Phase D — PyQt6 Dashboard

The GUI is a standalone PyQt6 window (similar pattern to AMail's `gui_viewer.py`).

#### Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  📅 ACalendar                                      [AMail: 🟢] [─][□][×] │
├─────────────────────────────────────────────────────────────────┤
│  Job Filter: [ALL ▼]  Date Type: [ALL ▼][🔄 Refresh] [📧 Digest] │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌─ UPCOMING (next 7 days) ──────────────────────────────────┐   │
│  │ Date       │ Description              │ Project │ Type    │   │
│  │ Jun 8 2pm  │ Concrete pour inspection │ ARCO    │✅ exact │   │
│  │ Jun 10     │ RFI deadline - cladding  │ Hood St │ ⚡ deadline│ │
│  │ Jun 12     │ Site walk-through        │ Econolodge│ 📅 range│  │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌─ THIS MONTH ───────────────────────────────────────────────┐   │
│  │ Jun 15     │ Lift installation         │ ARCO    │ ⏳ approx│   │
│  │ Jun 22     │ Final inspection          │ 6WS     │ ✅ exact │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌─ TBC / UNCONFIRMED ────────────────────────────────────────┐   │
│  │ ???        │ Soakwell repair           │ ARCO    │ ❓ TBD   │   │
│  │ ???        │ Handover meeting          │ 6WS     │ ❓ TBD   │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                   │
├─────────────────────────────────────────────────────────────────┤
│  [📅 Create in Outlook] [✏️ Edit] [🗑️ Delete]  [Open Email in AMail] │
└─────────────────────────────────────────────────────────────────┘
```

#### Key Behaviors

**Startup:**
1. Call `register_app("acalendar")` → write lock file
2. Call `init_shared_db()` → ensure tables exist
3. Poll `get_app_status("amail")` every 5 seconds → update green/red dot
4. Load existing events from `pull_calendar_events()`
5. Start background thread: poll `pull_new_triage_results()` every 30 seconds

**"Refresh from Mail" button:**
1. Pull all unconsumed `triage_results` from shared DB
2. For each, run `DateExtractorCrew` to extract dates
3. Push extracted dates to `calendar_events` table
4. Mark triage results as consumed
5. Refresh the GUI lists

**"Create in Outlook" button:**
1. Get selected event from the table
2. Call `create_calendar_event()` from `shared_tools.outlook_tool`
3. Update the event's `outlook_event_id` and `status` in shared DB
4. Refresh display

**"Open Email in AMail" (right-click or button):**
1. Get the source email's EntryID from the calendar event
2. If AMail is running (check lock file), write a "navigation request" to shared DB
3. AMail polls for navigation requests and jumps to the email
4. If AMail is NOT running, prompt user to launch it

**"Weekly Digest" button:**
1. Collect all events for the next 7 days
2. Format as a clean email (grouped by project, with date type indicators)
3. Use `OutlookSendTool` to send to user
4. Record in `weekly_digests` table

**Conflict Detection (runs on each refresh):**
1. Compare all events with status "pending"
2. Flag overlapping dates, same-day conflicts
3. Show a red badge and warning row in the table
4. Write to `conflicts` table

---

## 6. AMail Integration (Phase E)

### Changes to `tools/amail/src/amail/gui_viewer.py`

1. **On startup:** Call `register_app("amail")` and `init_shared_db()`
2. **After triage completes** (in `on_category_ready` signal handler): Call
   `push_triage_result()` to write to shared DB
3. **When composing replies** (in ReplyWorker): Call `pull_calendar_events()`
   and inject relevant events as context into the reply generation prompt
4. **Navigation:** Add a poll timer that checks for "navigation request" from
   ACalendar app → jump to source email
5. **On close:** Call `unregister_app("amail")`

### Changes to `tools/amail/src/amail/crew.py`

In `ReplyGeneratorCrew.reply_assistant()` — after loading style blueprint and
examples, also query calendar events related to this email's project/context and
inject them into the backstory:

```python
# NEW: Inject relevant calendar context
from shared_tools.ipc_bridge import pull_calendar_events
events = pull_calendar_events(project=extracted_project)
if events:
    calendar_context = "\n\nRELEVANT SCHEDULE CONTEXT:\n"
    for e in events:
        calendar_context += f"- {e['description']}: {e['start_date']} ({e['date_type']})\n"
    identity_injection += calendar_context
```

---

## 7. .exe Packaging (Phase F)

### PyInstaller Configuration

Both AMail and ACalendar can be packaged as standalone `.exe` files for desktop use.

```bash
# From project root
uv run pyinstaller --onefile --windowed \
    --name "AMail" \
    --add-data "tools/amail/knowledge:knowledge" \
    --add-data "tools/amail/src/amail/config:config" \
    --hidden-import=amail \
    tools/amail/src/amail/main.py

uv run pyinstaller --onefile --windowed \
    --name "ACalendar" \
    --add-data "tools/acalendar/src/acalendar/config:config" \
    --hidden-import=acalendar \
    tools/acalendar/src/acalendar/main.py
```

Key flags:
- `--onefile`: Single .exe output
- `--windowed`: No console window (pure GUI)
- `--add-data`: Bundle YAML configs and knowledge files into the .exe
- `--hidden-import`: Ensure crewai and shared_tools are bundled

Output: `dist/AMail.exe` and `dist/ACalendar.exe` — drop on desktop.

The `.env` file cannot be bundled (contains secrets). Each app should check for
`~/.crewai/.env` on startup, and if not found, show a first-run dialog asking for
API keys. This is already the pattern — just needs the code to check the home
directory location.

---

## 8. Implementation Phases (Build Order)

| Phase | Description | Dependencies | Key Files |
|-------|-------------|-------------|-----------|
| **A** | Add Outlook Calendar APIs to shared | None | `shared/src/shared_tools/outlook_tool.py` |
| **B** | Build IPC bridge (lock files + shared SQLite) | None | `shared/src/shared_tools/ipc_bridge.py` |
| **C** | Scaffold calendar project + DateExtractorCrew | A, B | `tools/acalendar/pyproject.toml`, `crew.py`, YAML configs |
| **D** | Build ACalendar GUI (schedule view, filters, conflict badges) | A, B, C | `tools/acalendar/src/acalendar/gui_viewer.py` |
| **E** | AMail integration (push triage, pull events, navigation) | B, D | `tools/amail/src/amail/gui_viewer.py`, `crew.py` |
| **F** | PyInstaller packaging for .exe | E | `pyinstaller` config, first-run setup dialog |

Phases A and B are independent and can be done in parallel. Everything else is sequential:
`A + B → C → D → E → F`.

---

## 9. File Manifest (what gets created/modified)

```
NEW:
  shared/src/shared_tools/ipc_bridge.py                 # Phase B
  tools/acalendar/pyproject.toml                        # Phase C
  tools/acalendar/src/acalendar/__init__.py             # Phase C
  tools/acalendar/src/acalendar/main.py                 # Phase C
  tools/acalendar/src/acalendar/crew.py                 # Phase C
  tools/acalendar/src/acalendar/gui_viewer.py           # Phase D
  tools/acalendar/src/acalendar/config/
      date_extractor_agents.yaml                        # Phase C
      date_extractor_tasks.yaml                         # Phase C

MODIFIED:
  shared/src/shared_tools/outlook_tool.py               # Phase A (add calendar APIs)
  tools/amail/src/amail/gui_viewer.py                   # Phase E (IPC integration)
  tools/amail/src/amail/crew.py                         # Phase E (calendar context injection)

EXISTING (unchanged):
  shared/src/shared_tools/llm_config.py
  pyproject.toml                                    # workspace already configured
```

---

## 10. Running After Implementation

```bash
# Development
uv run acalendar    # Launch ACalendar
uv run amail        # Launch AMail (in another terminal)

# After Phase F — clickable desktop icons
dist/ACalendar.exe
dist/AMail.exe
```

Both apps auto-detect each other via lock files. ACalendar polls for new AMail triage
results. AMail reads calendar events when composing replies. No manual refresh needed
when both are open.
