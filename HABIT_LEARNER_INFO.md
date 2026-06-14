# Habit Learner — Information Sheet

**Status:** Core service DONE (5-stage pipeline + infer + classify_sender). WebUI integration DONE. Some items remaining (see below).

## What It Does

Learns Amy's email replying patterns from Outlook data (Inbox + Sent Items, 9 months back). Produces behavioral profiles that the ReplyGeneratorCrew uses to generate context-aware replies — matching sender tier, predicted intent, and conditional style.

## How Reply Detection Works

Stage 2 (MATCH) determines whether Amy replied to each received email:

```
For each of Amy's sent emails:
  1. PRIMARY: Match by Outlook ConversationID (most reliable)
     Outlook groups all messages in a thread under one ID,
     regardless of subject changes.
  2. SECONDARY: Match by normalized subject + time window
     Strip RE:/FW: prefixes → compare subjects → check that
     reply was sent after receipt and within 7 days.

After matching:
  was_replied = 1  →  Amy replied (matched pair found)
  was_replied = 0  →  No reply found (Amy chose not to reply,
                       or reply is outside the 9-month window)

unmatched_received = inbox emails with was_replied = 0
  → These are the "Amy didn't reply" emails — valuable for
    learning triage behavior (which emails does Amy ignore?)
```

## Data Storage

```
<LILAMY_DATA_DIR>/
├── habit_learner.db              ← 9 SQLite tables (queryable)
├── mail_fetch/
│   ├── inbox/{entry_id}.json    ← One JSON file per received email
│   └── sent/{entry_id}.json     ← One JSON file per sent email
└── mail_history.db               ← Unchanged IPC database
```

Each email gets a UNIQUE JSON file named by its Outlook EntryID, so no overwrites. The DB uses `INSERT OR REPLACE` on primary key — new emails insert, duplicates update in-place.

## Fetch Pagination

The fetch loop uses date-based pagination:
1. Fetch 200 newest emails fitting the date range
2. Track the oldest timestamp in that batch
3. Next fetch: emails strictly OLDER than that timestamp
4. Repeat until exhausted (batch < 200)

This prevents re-fetching the same emails and ensures complete coverage.

```
Outlook COM (Inbox + Sent Items)
        │
        ▼
┌───────────────────────────────────────────┐
│  HabitLearnerService (QObject + signals)   │
│                                            │
│  Stage 0: FETCH      Pull from Outlook    │
│  Stage 1: NORMALIZE  Extract + clean       │
│  Stage 2: MATCH      Thread-match pairs    │
│  Stage 3: CLASSIFY   LLM-label intents     │
│  Stage 4: BUILD      Statistical profiles  │
│                                            │
│  infer(email) → BehavioralContext           │
└──────────────────┬────────────────────────┘
                   │ {behavioral_context} injected
                   ▼
┌───────────────────────────────────────────┐
│  ReplyGeneratorCrew                        │
│  (reply_tasks.yaml + crew.py)              │
│                                            │
│  Now receives per-email behavioral guide:  │
│  - Sender tier & reply rate                │
│  - Predicted intent                        │
│  - Conditional style (greeting, signoff)   │
└───────────────────────────────────────────┘
```

## Files

| File | Purpose | Status |
|---|---|---|
| `shared/src/shared_tools/email_parser.py` | HTML→text, address parsing, subject normalization | ✅ Done |
| `shared/src/shared_tools/outlook_tool.py` | Added `fetch_sent_emails()` (Sent Items folder) | ⚠️ **NOT IMPLEMENTED** — imported but undefined |
| `shared/src/shared_tools/habit_learner_db.py` | 9-table SQLite schema + CRUD | ✅ Done (2026-06-14: +`get_sender_received_stats`) |
| `shared/src/shared_tools/habit_learner_service.py` | Service class + BehavioralContext | ✅ Done (2026-06-14: fixed reply_rate, confidence, examples, low-confidence guard) |
| `shared/src/shared_tools/mail_service.py` | Injects behavioral context into reply loop | ✅ Done (2026-06-14: +terminal logging) |
| `tools/amail/src/amail/config/reply_tasks.yaml` | `{behavioral_context}` template variable | ✅ Done |
| `tools/amail/src/amail/main.py` | `build_habits` CLI entry point | ✅ Done |
| `tools/amail/pyproject.toml` | Registered `build_habits` script | ✅ Done |
| `lilamy/modules/amail_routes.py` | WebUI reply endpoint + agent_info | ✅ Done (2026-06-14: +behavioral_context, +agent_info, +logging) |
| `lilamy/static/index.html` | WebUI agent info panel | ✅ Done (2026-06-14) |
| `lilamy/static/app.js` | WebUI agent info rendering | ✅ Done (2026-06-14) |

## Database Tables (habit_learner.db)

| Table | Purpose |
|---|---|
| `raw_inbox` | Raw received emails from Outlook fetch |
| `raw_sent` | Raw sent emails from Outlook fetch |
| `sent_messages` | Normalized Amy replies |
| `received_messages` | Normalized incoming emails |
| `reply_pairs` | Matched pairs with LLM-classified features |
| `sender_profiles` | Per-sender behavioral profiles |
| `style_matrix` | Conditional style (tier × category) |
| `intent_priors` | P(intent | dimension) |
| `learning_sessions` | Audit trail of build runs |

## JSON Files (for visualization)

```
<LILAMY_DATA_DIR>/mail_fetch/
├── inbox/{entry_id}.json
└── sent/{entry_id}.json
```

## Commands

```bash
uv run build_habits        # Run full 5-stage learning pipeline
```

## Key Methods

```python
from shared_tools.habit_learner_service import get_habit_service

svc = get_habit_service()

# Build profiles from Outlook
svc.build_profiles()              # async, emits progress signals

# Fetch only (no learning)
svc.fetch_from_outlook(months_back=9)

# Per-email inference (used by reply pipeline)
ctx = svc.infer(email_dict)       # → BehavioralContext
print(ctx.to_injection_text())    # → prompt-ready text

# Query learned data
svc.get_learning_summary()        # → stats dict
svc.get_unmatched_received(100)   # → emails Amy didn't reply to
svc.get_sender_detail(email)      # → full sender profile + examples

# Online learning
svc.record_feedback(entry_id, generated_reply, actual_reply, was_sent=True)
```

## BehavioralContext Output Format

```
SENDER PROFILE:
- John Smith (john@builder.com) — Tier: client
- Amy replies to this sender 85% of the time
- Typical reply latency: 2.3 hours
- Average reply length: 45 words
- Preferred greeting: "Hi John,"
- Preferred sign-off: "Cheers"
- Most common reply intent: acknowledge

PREDICTED INTENT: defer_redirect

RECOMMENDED STYLE:
- Greeting: "Hi {first},"
- Sign-off: "Cheers"
- Formality: neutral
- Structure: full_4part

Overall confidence in these behavioral predictions: 70%
```

## Remaining Work

### ⚠️ Must Fix (blocks functionality)
- **`fetch_sent_emails()` in `outlook_tool.py`** — function is imported and called in `habit_learner_service.py` but the implementation doesn't exist. Stage 0 (FETCH) can only pull inbox emails; sent items fetch will crash. Estimated ~1 hour.

### Phase C — Training Visualization
- Desktop training dialog (`habit_learner_dialog.py`) — live card feed during build
- WebUI API routes for habit browsing (`habit_learner_routes.py`)
- WebUI training visualization page
- Habit learner module registry entry

### Testing & Polish
- Unit tests (`test_habit_learner.py`)
- `record_feedback()` — online learning from actual sent replies (stub exists, `_do_record_feedback` is `pass`)
- Style matrix current only has greeting=Hi Paul; needs more diverse data
