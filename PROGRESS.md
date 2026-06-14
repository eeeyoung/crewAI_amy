# PROGRESS.md — lilAmy Platform Development

**Latest session:** 2026-06-14
**Previous:** 2026-06-13 ([AMail Redesign & lilAmy Platform](#session-2026-06-13--amail-redesign--lilamy-platform))
**Branch:** `main`
**Git user:** `eeeyoung`

---

## Session 2026-06-14 — Habit Learner Integration & Bug Fixes

### What We Did

#### 1. WebUI Reply Agent Info Panel — **DONE**

New collapsible panel below the reply textbox showing real-time behavioral data used by the reply agent:

- **👤 Sender Profile** — name, email, tier, reply rate, latency, greeting, signoff, top intent
- **🎯 Predicted Intent** — classified intent label
- **🎨 Recommended Style** — structure, formality, greeting, signoff, sample count
- **📝 Behavioral Context** — raw text injected into the LLM prompt (click to expand)
- **📚 Matched Examples** — count of historical examples used
- **Confidence badge** — green (>60%), yellow (30-60%), red (<30%)

Files: `lilamy/modules/amail_routes.py` (+`agent_info` in response), `lilamy/static/index.html` (+panel HTML), `lilamy/static/app.js` (+`renderAgentInfo`, `showAgentInfoLoading`, `hideAgentInfo`, `toggleAgentContext`)

#### 2. Habit Learner Integration Bug Fixes — **DONE**

**Bug: Reply rate always 100%.**
`_compute_sender_profile` only counted `reply_pairs` — which by definition are all matched. Never queried `received_messages` for unreplied emails.

Fix: Added `get_sender_received_stats(sender_email)` DB function → queries `received_messages` for real total_received + total_replied counts.

**Bug: Confidence always 100%.**
`infer()` formula: `0.5 + 0.2 + 0.2 + 0.1 = 1.0` — saturated immediately.

Fix: New formula scales with data:
- Base 0.15 + exact match 0.25 + sample bonus (0→0.35 for 0→50 emails) + style bonus (0→0.15 for 0→20 samples) + examples 0.05
- Domain-only match gets halved bonus
- Capped at 0.90 — never claims 100%

**Bug: Behavioral context text was vague/empty.**
- Examples section said "3 examples available" but showed NO content
- Examples matched on category="General" alone (score 4/11) — effectively random
- Style params for tier×category had no quality gate

Fixes:
- `to_injection_text()` now includes actual example content (subject, intent, reply snippet) with a separator and instruction to match tone/length/structure
- `_select_examples()` now requires minimum score 5 (same-domain+same-category or direct sender match)
- Low-confidence guard: when confidence < 40%, injection text warns to fall back to standard style blueprint

**Bug: WebUI Draft Reply 500 error.**
`reply_tasks.yaml` required `{behavioral_context}` but `generate_reply` endpoint didn't provide it.

Fix: Added behavioral context injection + `email_urgency`, `relevant_schedule`, `email_cc` to the endpoint's inputs dict (matching `MailService._run_reply_loop()`)

**Bug: Agent info panel disappeared on render.**
`showAgentInfoLoading()` used `panel.innerHTML = ...` destroying all DOM elements that `renderAgentInfo()` later tried to populate → `Cannot set properties of null`.

Fix: Added dedicated `#agent-loading` element in HTML, `showAgentInfoLoading()` now only toggles visibility. Added `setText()` safe DOM setter as defense-in-depth.

#### 3. Terminal Logging — **DONE**

Both WebUI (`amail_routes.py`) and Desktop (`mail_service.py`) reply paths now print structured logs:
- Email being replied to (subject, sender, category, urgency)
- Habit learner: sender email, profile found?, tier, reply rate, greeting, signoff, predicted intent, style params, confidence
- Injected behavioral context text
- LLM call status + draft preview

#### 4. `fetch_sent_emails()` — **STILL MISSING** ⚠️

Function is imported and called in `habit_learner_service.py` but NOT defined in `outlook_tool.py`. Without it, Stage 0 (FETCH) can only pull inbox — sent items fetch will crash. This is a **Phase A incomplete item**.

### Files Changed This Session

| File | Status | Change |
|---|---|---|
| `shared/src/shared_tools/habit_learner_db.py` | Modified | +`get_sender_received_stats()` for real reply_rate |
| `shared/src/shared_tools/habit_learner_service.py` | Modified | Fixed `_compute_sender_profile` (real reply_rate), `infer()` (honest confidence), `to_injection_text()` (real example content), `_select_examples()` (min score threshold), low-confidence guard |
| `shared/src/shared_tools/mail_service.py` | Modified | +terminal logging in `_run_reply_loop` |
| `lilamy/modules/amail_routes.py` | Modified | +`behavioral_context` in reply inputs, +`agent_info` in response, +terminal logging |
| `lilamy/static/index.html` | Modified | +agent info panel HTML, +CSS styles |
| `lilamy/static/app.js` | Modified | +agent info rendering, +safe DOM setter, +loading state |

### Remaining Work

| Item | Priority | Effort |
|---|---|---|
| `fetch_sent_emails()` in `outlook_tool.py` | **HIGH** — blocks Stage 0 | ~1 hour |
| Desktop training dialog (`habit_learner_dialog.py`) | Medium — Phase C | ~3 hours |
| WebUI habit learner routes + page | Medium — Phase C | ~3 hours |
| Unit tests (`test_habit_learner.py`) | Medium | ~2 hours |
| `record_feedback()` online learning implementation | Low — stub exists | ~2 hours |
| Habit learner module registry entry | Low | ~5 minutes |

---

## Session 2026-06-13 — AMail Redesign & lilAmy Platform

### What We Built

#### 1. Unified Single-Pass Summarizer — **DONE & WORKING**

Replaced the 6-stage AMail pipeline (Filter → Triage → Reply → Workflow → Facts → Grammar) with one LLM call:

| Old | New |
|---|---|
| 6 sequential CrewAI crews | `UnifiedSummarizerCrew` (1 agent, 1 task) |
| ~30-60s per email | ~3-5s per email |
| Manual JSON parsing per stage | Single structured JSON output |
| Reply/workflow auto-generated for every email | Reply/workflow lazy (on-demand only) |

**Output:** `{chinese_summary, category, urgency, assignee, todos}`

Files: `tools/amail/src/amail/config/summarizer_agents.yaml`, `summarizer_tasks.yaml`, `tools/amail/src/amail/crew.py` (+ `UnifiedSummarizerCrew`, `UnifiedEmailOutput`)

#### 2. `processed_emails` — Unified Mail Store — **DONE & WORKING**

New table in `data/mail_history.db` (renamed from `shared_data.db`) — the single source of truth for all processed emails:

```sql
processed_emails (
    entry_id TEXT PRIMARY KEY,   -- Outlook EntryID (dedup by upsert)
    subject, sender, received_time, body,
    category, urgency, chinese_summary, assignee, todos_json,
    reply_draft,                 -- lazy-filled on demand
    status DEFAULT 'active',     -- active | removed (soft delete)
    processed_at, updated_at
)
```

CRUD: `upsert_processed_email()` (ON CONFLICT upsert), `get_processed_emails(limit=0 for unlimited)`, `get_processed_entry_ids()` (active-only for dedup), `get_latest_received_time()`, `get_earliest_received_time()`, `remove_processed_email()` (soft delete), `get_active_entry_ids_in_range()`

#### 3. Three Fetch Modes — **DONE & WORKING**

| Mode | API | Behavior |
|---|---|---|
| ⬆ Fetch Earlier | `POST /api/amail/fetch-earlier?count=N` | Unread emails OLDER than earliest stored (ascending sort). On empty storage: fetches oldest unread first. |
| ⬇ Fetch Latest | `POST /api/amail/fetch-latest?count=N` | Unread emails NEWER than latest stored (descending sort). UI styled as primary blue button. |
| 🔄 Sync | `POST /api/amail/sync` | 5-step reconciliation: (1) fetch earlier gap fill, (2a) add new unread in range, (2b) detect actual deletions via ALL-emails fetch, (3) body backfill from Outlook. Confirmation modal with date range + time estimate. |

All modes: unread-only, dedup by EntryID, incremental by received_time watermark.

#### 4. lilAmy Platform Launcher — **DONE & WORKING**

```
uv run lilamy              # Desktop card UI
uv run lilamy --web        # WebUI server at http://127.0.0.1:8765
uv run lilamy --amail      # Legacy AMail (unchanged)
```

Package: root `lilamy/` with `main.py`, `gui_viewer.py`, `web_server.py`, `modules/`, `static/`

#### 5. Desktop Card UI — **DONE & WORKING**

`lilamy/gui_viewer.py` — `LilAmyWindow`:
- Left panel: scrollable summary cards (Chinese summary, category, urgency, assignee, todos)
- Right panel: detail view + AI draft reply (ReplyGeneratorCrew lazy) + refine + copy
- Uses `MailService(auto_refresh=True)` — QTimer every 10s fetches latest
- Double-click card → opens in Outlook
- Dark Catppuccin Mocha theme

#### 6. WebUI — **DONE & WORKING**

Zero-dependency SPA (vanilla JS, Tailwind CDN, no npm):

```
lilamy/
├── web_server.py              # FastAPI + static files + module registry
├── modules/
│   ├── registry.py            # MODULES dict (AMail enabled, 3 greyed out)
│   └── amail_routes.py        # 9 REST endpoints
└── static/
    ├── index.html             # SPA shell: sidebar + workspace + cards + detail
    └── app.js                 # All logic: API calls, rendering, multi-select, context menu
```

**API endpoints:**
```
GET  /api/modules                  → sidebar module list
GET  /api/health                   → health check
GET  /api/amail/emails?limit=0     → all active emails (unlimited)
POST /api/amail/fetch-earlier?count=N
POST /api/amail/fetch-latest?count=N
POST /api/amail/sync
POST /api/amail/emails/{id}/reply  → AI draft (lazy)
POST /api/amail/emails/{id}/refine → refine draft
POST /api/amail/emails/{id}/remove → soft delete
GET  /api/amail/emails/{id}        → detail (body from Outlook if missing)
```

**Module registry** (ready for expansion):
```
📧 AMail       enabled  ← built and running
📅 ACalendar   disabled ← entry point ready
📄 ADocuments  disabled ← entry point ready
📊 AReport     disabled ← entry point ready
```

#### 7. WebUI Features — **DONE**

- Multi-select: Click (single), Ctrl+Click (toggle), Shift+Click (range), Ctrl+A (all), click-empty (deselect)
- Right-click context menu: Open in Outlook, Remove (works on all selected)
- Custom count spinboxes per fetch button (step=10, range 10-2000, defaults=20)
- Sync confirmation modal with date range + time estimate
- Auto-refresh: every 10s silently fetches latest
- Progress bar during operations
- Toast notifications
- Keyboard: Ctrl+R refresh, Escape dismiss menus

#### 8. Data Path Standardization — **DONE**

- All data in `<project_root>/data/` (gitignored via `data/` in `.gitignore`)
- `ipc_bridge.py` resolves via `LILAMY_DATA_DIR` env var → fallback to `<project_root>/data/`
- `mail_history.db` (renamed from `shared_data.db`)
- All path references centralized in `ipc_bridge.CREWAI_DIR` / `ipc_bridge.DB_PATH`

---

## Files Changed This Session

| File | Status | Change |
|---|---|---|
| `shared/src/shared_tools/ipc_bridge.py` | Modified | +`processed_emails` table, 8 CRUD functions, data dir resolution |
| `shared/src/shared_tools/mail_service.py` | Modified | +unified pipeline, 3 fetch modes, sync, auto-refresh, 8 new signals |
| `shared/src/shared_tools/outlook_tool.py` | Modified | +`received_after`/`received_before`/`ascending` params |
| `tools/amail/src/amail/crew.py` | Modified | +`UnifiedSummarizerCrew`, `UnifiedEmailOutput` Pydantic model |
| `tools/amail/src/amail/config/summarizer_agents.yaml` | **NEW** | Single-pass summarizer agent |
| `tools/amail/src/amail/config/summarizer_tasks.yaml` | **NEW** | Single-pass summarizer task |
| `lilamy/__init__.py` | **NEW** | Platform package |
| `lilamy/main.py` | **NEW** | Entry point (desktop/web/amail modes) |
| `lilamy/gui_viewer.py` | **NEW** | Desktop card UI (LilAmyWindow, EmailCard, DetailPanel) |
| `lilamy/web_server.py` | **NEW** | FastAPI app + static files + module loader |
| `lilamy/modules/registry.py` | **NEW** | Module registry (4 modules) |
| `lilamy/modules/amail_routes.py` | **NEW** | 9 REST endpoints wrapping MailService |
| `lilamy/static/index.html` | **NEW** | SPA shell (Tailwind, dark theme, sidebar, modal) |
| `lilamy/static/app.js` | **NEW** | All frontend logic (API, rendering, selection, context menu) |
| `pyproject.toml` | Modified | +`[project.scripts]`, `[tool.uv]`, `[build-system]`, FastAPI dep |
| `.gitignore` | Modified | +`data/` directory |
| `CLAUDE.md` (root) | Modified | Enhanced: NEVER section, gotchas, commands, all services |
| `AGENTS.md` (root) | **NEW** | Cross-tool universal |
| `shared/src/shared_tools/CLAUDE.md` | **NEW** | Service layer documentation |
| `tools/acalendar/CLAUDE.md` | **NEW** | ACalendar documentation |
| `tools/asummary1/CLAUDE.md` | **NEW** | ASummary1 documentation + architectural debt notes |
| `tools/asummary1/AGENTS.md` | **NEW** | ASummary1 cross-tool |
| `tools/asummary/AGENTS.md` | **NEW** | Deprecation notice |
| `tools/amail/AGENTS.md` | Replaced | AMail-specific (was 1KB generic CrewAI template) |
| `tools/amail/CLAUDE.md` | Enhanced | Service-first references, NEVER section, gotchas |

---

## Architecture Decisions

1. **Single LLM pass > multi-stage pipeline.** 90% of email triage needs category + summary + assignee. Reply, workflow, and facts are lazy/on-demand. This saves 80%+ tokens.

2. **Soft delete with re-fetch.** Removed emails stay in DB (`status='removed'`) but their EntryIDs are excluded from dedup. If the email becomes unread again in Outlook, re-fetching brings it back. Dedup only blocks active emails.

3. **Two-pass sync for deletion detection.** Pass 1: unread-only for adding new emails. Pass 2: ALL emails (read+unread) for detecting actual deletions. This prevents read emails from being falsely marked as removed.

4. **Unlimited by default.** `get_processed_emails(limit=0)` returns all emails. Frontend requests `?limit=0`. User controls fetch count via spinboxes.

5. **WebUI as vanilla SPA.** Zero npm, zero build step. Tailwind via CDN. All logic in one JS file. FastAPI serves the shell + API. Replaceable with React later without API changes.

---

## Key Takeaways

- **Service-first pattern enabled rapid WebUI.** `MailService` already had clean method boundaries. FastAPI just wraps them — zero business logic moved into the server.
- **Dedup is the hardest problem.** Multiple rounds of fixes: session-only → DB-backed → active-only → two-pass sync. Getting dedup right means understanding the full lifecycle of an email across Outlook + storage.
- **Sync is dangerous without confirmation.** The first version silently removed 623 emails because it confused "read" with "deleted." The modal + two-pass fix makes sync safe.
- **Vanilla JS SPA is surprisingly productive.** No build step, instant refresh, ~400 lines of JS does everything. React can come later when we need component reuse across modules.
