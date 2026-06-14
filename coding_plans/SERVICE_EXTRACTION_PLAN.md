# AMail & ACalendar — Service Extraction & API Migration Plan

## Context

AMail and ACalendar currently have their core business logic tightly coupled to PyQt6 GUI code. Per the AMY Architecture & Roadmap (Milestones 1-2), we need clean service boundaries so:
1. The pipeline logic can be tested independently of the GUI
2. A FastAPI backend can wrap these services later (for the web dashboard)
3. LLM orchestration, data persistence, and Outlook COM access are in one place

This plan extracts `MailService` and `CalendarService` classes, then refactors the existing PyQt6 apps to use them.

## Existing Code That Must Be Reused (not rewritten)

The following already work well and should be imported by the new services:
- **CrewAI crews**: `crew.py` in both tools — all `@CrewBase` classes and YAML configs
- **Outlook COM**: `shared_tools/outlook_tool.py` — all fetch/send/mark/category/attachment functions
- **IPC bridge**: `shared_tools/ipc_bridge.py` — all shared DB and lock file functions
- **Fact store**: `amail/fact_store.py` — FTS5 search/save/list
- **LLM config**: `shared_tools/llm_config.py` — `get_llm(role)` provider routing

## Files to Create

### `shared/src/shared_tools/mail_service.py` — MailService

A `QObject` subclass that owns all AMail business logic. The GUI calls its methods and connects to its signals.

**State owned by the service:**
- `_emails: list[dict]` — all active emails (replaces `TriageWindow.emails`)
- `_state: dict[int, dict]` — per-email processing state
- `_processed_entry_ids: set[str]` — session blocklist
- `_skipped_indices: set[int]` — indices to skip
- `_email_index_counter: int` — monotonic index allocator

**Signals emitted (the GUI connects to these):**

| Signal | Args | When |
|---|---|---|
| `filter_done` | `(idx, cleaned_body)` | After MessageFilterCrew completes |
| `category_ready` | `(idx, category, urgency, extra_info, dates)` | After TriageSingleCrew completes |
| `reply_generated` | `(idx, html_body)` | After ReplyGeneratorCrew completes |
| `workflow_generated` | `(idx, text)` | After WorkflowGeneratorCrew completes |
| `contacts_loaded` | `(contacts_list)` | After Outlook contacts fetched |
| `grammar_polished` | `(idx, polished_text)` | After GrammarPolisherCrew completes |

**Public methods:**

| Method | Returns | Purpose |
|---|---|---|
| `start()` | None | Init DB, register IPC, start pipeline threads |
| `stop()` | None | Stop threads, unregister IPC |
| `submit_emails(emails)` | `list[int]` | Add emails to pipeline, return assigned indices |
| `get_state(idx)` | `dict` | Return per-email state snapshot |
| `skip_email(idx, mark_read)` | None | Skip and optionally mark read in Outlook |
| `regenerate(idx)` | None | Re-run pipeline from failed stage |
| `send_email(idx, recipient, cc, subject, body_html)` | `bool` | Send via Outlook and mark original as read |
| `polish_grammar(idx, draft_text)` | None | Async grammar polish |
| `extract_facts(idx)` | `list[dict]` | Run FactExtractorCrew and save to FTS5 |
| `save_reply_example(idx, text)` | None | Append to `reply_examples.jsonl` |
| `get_attachments(idx)` | `list[dict]` | Fetch attachment metadata |
| `save_attachment(idx, att_idx, dir)` | `str` | Save single attachment to disk |
| `fetch_contacts()` | `list[dict]` | Trigger async contact fetch |
| `check_nav_request()` | `str \| None` | Check for ACalendar nav request, return EntryID |
| `get_attachment_count(idx)` | `int` | Lazy count (cached) |

**Internal (threading):**
- Uses `threading.Thread` + `queue.Queue` instead of `QThread` — keeping PyQt6 out of the service
- A single `_llm_semaphore = threading.Semaphore(1)` serializes LLM calls
- Each pipeline stage runs in a daemon thread pulling from its input queue
- The service provides a `_emit_signal(signal_name, *args)` method that uses `QMetaObject.invokeMethod` for thread-safe signal emission (since signals are PyQt signals)

### `shared/src/shared_tools/calendar_service.py` — CalendarService

A `QObject` subclass that owns all ACalendar business logic.

**State owned by the service:**
- `_events: list[dict]` — all calendar events
- `_categorized_emails: list[dict]` — unconsumed categorized emails from AMail

**Signals emitted:**

| Signal | Args | When |
|---|---|---|
| `events_changed` | `(events_list)` | After any CRUD operation |
| `new_emails_arrived` | `(count)` | After polling finds unconsumed emails |
| `amail_status_changed` | `(is_running)` | When AMail lock file status changes |

**Public methods:**

| Method | Returns | Purpose |
|---|---|---|
| `start()` | None | Init DB, register IPC, start poll timers |
| `stop()` | None | Unregister IPC, stop timers |
| `load_events(project, date_type, status)` | `list[dict]` | Read from shared DB |
| `create_event(data)` | `int` | Insert into shared DB |
| `update_event(event_id, **fields)` | `bool` | Update in shared DB |
| `delete_event(event_id, also_outlook)` | `bool` | Mark cancelled in DB, optionally delete from Outlook |
| `push_to_outlook(event_id)` | `str` | Create Outlook appointment, persist outlook_event_id |
| `detect_conflicts()` | `list[dict]` | Find overlapping pending events |
| `build_weekly_digest()` | `str` | Return HTML body for weekly digest email |
| `send_weekly_digest()` | `bool` | Build digest and send via Outlook |
| `poll_amail_status()` | `bool` | Check if AMail is running |
| `pull_new_emails()` | `list[dict]` | Pull unconsumed categorized emails |
| `navigate_to_amail(entry_id)` | None | Write `nav_request.json` |
| `send_to_outlook(event_id)` | `str` | Create/update Outlook appointment |

## Files to Modify

### `tools/amail/src/amail/gui_viewer.py`
- **Remove**: FilterWorker, TriageWorker, ReplyWorker, WorkflowWorker, RegenerateWorker, GrammarPolishWorker, ContactFetchWorker classes, the `_llm_semaphore`, queue objects, and all pipeline state (`emails`, `pending_emails`, `state`, `skipped_indices`, `email_index_counter`, `max_active`, `processed_entry_ids`, `filter_queue`, `triage_queue`, `reply_queue`, `workflow_queue`)
- **Remove**: `start_workers()`, `_promote_pending()`, `_count_active()`, `_append_email()`, `_all_emails_done()`, `_poll_nav_requests()`
- **Keep**: All GUI widget construction (`init_ui`), layout, stylesheets, `update_ui_state()`, navigation (`load_email`, `prev_email`, `next_email`), dialogs (`WorkflowDialog`, `AttachmentDialog`), autocomplete widgets
- **Modify**: `TriageWindow.__init__` — create `MailService` instead of workers; connect service signals to existing signal handlers (`on_filter_done`, etc.)
- **Modify**: `send_email()`, `skip_read()`, `skip_unread()`, `regenerate_current()`, `grammar_polish()`, `save_reply_feedback()`, `save_key_facts()`, `open_attachment_dialog()` — call service methods instead of directly manipulating state
- **Add**: A `_service_state_changed(idx, state)` handler if full state snapshot updates are needed
- **Modify**: `show_triage_report()` — accept a pre-created MailService or create one

### `tools/acalendar/src/acalendar/gui_viewer.py`
- **Remove**: `_detect_conflicts()` logic from the CalendarWindow class (moves to service)
- **Remove**: Direct `ipc_bridge` calls and Outlook COM calls from GUI methods
- **Keep**: All widget construction, table views, `EventEditDialog`, stylesheets
- **Modify**: `CalendarWindow.__init__` — create `CalendarService` instead of direct DB/COM calls
- **Modify**: `load_events()`, `on_refresh_from_mail()`, `on_weekly_digest()`, `on_open_in_amail()` — call service methods
- **Modify**: `EventEditDialog._on_save()`, `_on_push_outlook()`, `_on_delete()` — call service methods

### `tools/amail/src/amail/main.py`
- Modify `run_triage()` and `show_triage_report()` to create/accept a `MailService` instance

### `tools/acalendar/src/acalendar/main.py`
- Modify `run()` and `show_calendar()` to create/accept a `CalendarService` instance

### `shared/src/shared_tools/__init__.py`
- No changes needed (already empty, package marker)

## Data Flow After Refactor

```
┌─ AMail GUI (gui_viewer.py) ───────────────────────┐
│  TriageWindow (UI only)                            │
│  ┌────────────────┐  ┌───────────────────────────┐ │
│  │ MailListerDialog│  │ Signal handlers           │ │
│  │ (select emails) │  │ on_filter_done()          │ │
│  └───────┬────────┘  │ on_category_ready()       │ │
│          │            │ on_reply_generated()      │ │
│  ┌───────▼────────┐  │ on_workflow_generated()   │ │
│  │ submit_emails() │  │ on_contacts_loaded()      │ │
│  └───────┬────────┘  │ update_ui_state()          │ │
│          │            └──────────┬────────────────┘ │
└──────────┼──────────────────────┼──────────────────┘
           │                      │ (signals)
           │              ┌───────▼──────────────────┐
           │              │    MailService (QObject)  │
           │              │                           │
           │              │  _pipeline_threads        │
           │              │  _filter_queue            │
           │              │  _triage_queue            │
           │              │  _reply_queue             │
           │              │  _workflow_queue          │
           │              │  _llm_semaphore           │
           │              │  _state: dict[int, dict]   │
           │              │                           │
           │              │  → CrewAI crews           │
           │              │  → Outlook COM            │
           │              │  → IPC bridge             │
           │              │  → Fact store             │
           │              └───────────────────────────┘
           │
           │  (same pattern for ACalendar)
           │
           ▼
   ┌────────────────────────────┐
   │  CalendarService (QObject) │
   │  → IPC bridge              │
   │  → Outlook COM             │
   │  → Conflict detection       │
   └────────────────────────────┘
```

## Verification Plan

1. **Unit tests** for MailService: mock `crew.py` calls, verify correct queue routing, state transitions, and signal emission
2. **Unit tests** for CalendarService: mock `ipc_bridge` and `outlook_tool`, verify CRUD operations and conflict detection
3. **Integration**: `uv run pytest tools/amail/tests/ tools/acalendar/tests/` — all existing tests must pass
4. **Manual**: `uv run amail` — the GUI must function identically to before (same look, same behavior)
5. **Manual**: `uv run acalendar` — same verification
6. **Cross-app**: Verify AMail → ACalendar IPC still works (triage dates appear in calendar)

## Implementation Order

1. Create `mail_service.py` with the service class, signals, and pipeline logic (extracted from `gui_viewer.py`)
2. Refactor `gui_viewer.py` (AMail) to use MailService
3. Create `calendar_service.py` with the service class and event management logic
4. Refactor `gui_viewer.py` (ACalendar) to use CalendarService
5. Run all tests, fix any regressions
