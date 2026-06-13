# AGENTS.md — ASummary1

> Cross-tool project context. For Claude-specific internals, see `CLAUDE.md`.

Email summarizer with PyQt6 GUI. Generates Chinese summaries, assignees, and todo items from Outlook emails. Can auto-draft replies as Amy Chen.

## Commands

```bash
uv run asummary1                      # GUI (default)
uv run asummary1 --cli --list         # List saved summaries
uv run asummary1 --cli --latest 10    # Process 10 most recent (CLI)
uv run asummary1 --cli --all          # Re-process everything (CLI)
```

## Crews

| Crew | File | Purpose |
|---|---|---|
| SummarizerCrew | `crew.py` | Chinese summary + assignee + todos (JSON output) |
| ReplyCrew | `reply_crew.py` | Auto-reply as Amy Chen (plain text) |

## Architecture (⚠️ needs service extraction)

The GUI (`gui_viewer.py`) currently embeds all business logic in QThread subclasses. This violates the service-first pattern. A `SummaryService` needs to be extracted (same pattern as MailService + CalendarService). Until then, all new logic should go into standalone functions that a future service can import.

## NEVER

- ❌ Add more QThread subclasses to `gui_viewer.py`
- ❌ Duplicate crew.py or YAML changes to `tools/asummary/` (deprecated)
- ❌ Call `fetch_inbox_emails()` directly — use `outlook_tool.py`
- ❌ Access `_llm_semaphore` at module level — it belongs in a service
