# CLAUDE.md — ASummary1

Email summarizer with PyQt6 GUI for the lilAmy platform. Fetches Outlook inbox emails (or reads AMail-processed emails from the shared DB), generates Chinese summaries with assignees and todo items, and can auto-draft replies as Amy Chen. **Currently violates the service-first pattern — business logic is embedded in QThread subclasses inside `gui_viewer.py`. This is the next extraction target.**

## Commands

```bash
uv sync                               # Install dependencies
uv run asummary1                      # Launch GUI (default)
uv run asummary1-gui                  # Launch GUI directly
uv run asummary1 --cli                # CLI mode (batch processing)
uv run asummary1 --cli --list         # List saved summaries
uv run asummary1 --cli --all          # Re-process all emails
uv run asummary1 --cli --latest 10    # Process 10 most recent
```

## Architecture

```
┌─────────────────────────────────────────────────┐
│  SummaryWindow (gui_viewer.py)  ← ⚠️ 1,000 lines │
│  ┌───────────────┐ ┌──────────────────────────┐ │
│  │ Saved / TO DO  │ │ New (fetch from Outlook) │ │
│  │ (from DB)      │ │ SummaryWorker (QThread)  │ │
│  └───────┬───────┘ │   → Outlook COM           │ │
│          │         │   → SummarizerCrew         │ │
│   LoadSavedWorker  │   → SQLite writes          │ │
│   (QThread)        │ │ ReplyDialog              │ │
│                    │ │   → ReplyWorker (QThread) │ │
│                    │ │   → RefineWorker (QThread)│ │
│                    │ └──────────────────────────┘ │
│  Shared state: _llm_semaphore (module-level!)    │
│  Direct: SQLite connections, Outlook COM calls   │
└─────────────────────────────────────────────────┘
```

## Source Map

| File | Purpose |
|---|---|
| `src/asummary1/main.py` | Entry point: GUI by default, `--cli` for terminal mode |
| `src/asummary1/crew.py` | SummarizerCrew — Chinese summary + assignee + todos |
| `src/asummary1/reply_crew.py` | ReplyCrew — auto-reply as Amy Chen |
| `src/asummary1/gui_viewer.py` | ⚠️ 1,000-line PyQt6 GUI with embedded business logic |
| `config/agents.yaml` | Chinese summarizer agent (bilingual construction analyst) |
| `config/tasks.yaml` | Summarization task (JSON output: summary, assignee, todos) |
| `config/reply_agents.yaml` | Amy Chen persona for auto-reply |
| `config/reply_tasks.yaml` | Reply drafting task |

## CrewAI Crews

### SummarizerCrew
Single-agent crew. Takes `{email_subject, email_sender, email_content, email_category}` → returns JSON:
```json
{
  "chinese_summary": "承包商提交了23号图纸的RFI审批请求",
  "assignee": "项目经理",
  "todos": ["审核RFI内容", "周五前回复审批结果"]
}
```

### ReplyCrew
Single-agent crew. Takes `{email_subject, email_sender, email_content}` → returns plain-text email reply in Amy Chen's voice. Direct, professional, 3-6 sentences, ends with "Kind regards, Amy Chen".

## GUI Modes

| Mode | Description | Data Source |
|---|---|---|
| 📋 Saved | Load previously-summarized emails (status=active) | `email_summaries` table in shared DB |
| 🚩 TO DO | Load emails flagged as todo (status=todo) | `email_summaries` table |
| 🔄 New | Fetch from Outlook inbox, summarize live, save | Outlook COM → SummarizerCrew → DB |

## Keyboard Shortcuts

| Key | Action |
|---|---|
| Click | Select card (deselects others) |
| Ctrl+Click | Multi-select toggle |
| Ctrl+R | Open reply dialog for selected |
| Ctrl+F | Flag selected + mark read in Outlook → move to TO DO |
| Enter | Open selected in Outlook |
| Backspace | Remove selected + mark read |
| ↑ / ↓ | Navigate cards |

## ⚠️ Architecural Debt — Needs Service Extraction

This tool is **where AMail was before its service extraction.** The following violations must be addressed:

| Problem | Location | Fix |
|---|---|---|
| QThread subclasses for LLM work | `SummaryWorker`, `ReplyWorker`, `RefineWorker`, `LoadSavedWorker` | Extract to SummaryService with `threading.Thread` |
| Module-level `_llm_semaphore` | `gui_viewer.py:44` | Move into SummaryService as instance attribute |
| Direct SQLite in GUI | `_get_conn()`, `_load_saved_summaries()`, `_set_card_status()` | Route through SummaryService |
| Direct Outlook COM in GUI | `open_outlook_email()`, `flag_and_mark_read()` | Route through `outlook_tool.py` or SummaryService |
| Identical code duplicated from asummary | `main.py`, `crew.py` | Deprecate asummary, consolidate here |

## Relationship to ASummary

**ASummary** (`tools/asummary/`) is a CLI-only prototype. ASummary1 is its successor with a GUI. The `crew.py` and YAML configs are **byte-for-byte identical** between the two. ASummary should be deprecated in favor of ASummary1. See `tools/asummary/AGENTS.md` for the deprecation notice.

## NEVER

- ❌ Add more business logic to `gui_viewer.py` — extract to a SummaryService first
- ❌ Create new QThread subclasses — the existing ones already need extraction
- ❌ Duplicate crew.py or YAML changes to `tools/asummary/` — it's deprecated
- ❌ Call `fetch_inbox_emails()` or `win32com.client` directly — use `outlook_tool.py`
- ❌ Access `_llm_semaphore` directly — it belongs in a service, not at module level

## Gotchas

### The GUI has no service layer
Unlike AMail and ACalendar (which have MailService and CalendarService), ASummary1 has no `SummaryService`. All business logic, threading, DB access, and Outlook COM calls are embedded in `gui_viewer.py`. New features should be built as service methods first.

### Shared DB path
Uses `<project_root>/data/mail_history.db` (via `ipc_bridge.DB_PATH`) — same as AMail and ACalendar. Reads from `categorized_emails` (AMail's output) and writes to `email_summaries`.

### Gemini model configuration
Uses `get_llm("fast")` from `llm_config.py`. Model is configured via `MODEL=gemini/gemini-3.1-flash-lite` in `.env` (not `gemini-3.1-flash`, which doesn't exist).

## Testing

No tests exist for ASummary1. This is a known gap. When adding tests:
- Mock `fetch_inbox_emails()` from `outlook_tool.py`
- Mock SummarizerCrew and ReplyCrew
- Test the JSON parsing logic (including `{{...}}` template delimiter handling)
- Test DB save/load with an in-memory SQLite database
