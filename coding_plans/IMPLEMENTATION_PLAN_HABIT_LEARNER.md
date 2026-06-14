# Habit Learner — Implementation Plan

**Status (2026-06-14):** Phase A 90% complete (missing `fetch_sent_emails()`). Phase B 100% complete + WebUI integration. Phase C not started.

**Last updated:** 2026-06-14 — fixed reply_rate bug, confidence formula, example injection, low-confidence guard, WebUI agent info panel, terminal logging.

## Context

The current reply system uses a single static `style_blueprint.md` applied uniformly to every email. Amy's real behavior is conditional — it varies by sender, category, intent, and context. This plan builds a **Habit Learner** that rigorously extracts Amy's email replying patterns from her historical emails and produces behavioral profiles that the existing `ReplyGeneratorCrew` can consume. The output replaces "one static persona" with "behaviorally-tuned context per email."

We also need a **training session UI** so the user can see what's being learned, how, and whether the learning is working correctly.

---

## SECTION 0 — DATA ACQUISITION

### 0.1 Raw Email Storage

Emails are fetched from Outlook and stored in **two formats** side-by-side:

| Format | Location | Purpose |
|---|---|---|
| **JSON files** | `<LILAMY_DATA_DIR>/mail_fetch/inbox/*.json` and `sent/*.json` | Human-browsable, visualizable in training UI |
| **SQLite DB** | `<LILAMY_DATA_DIR>/habit_learner.db` → `raw_inbox` / `raw_sent` tables | Queryable, indexed, used for learning pipeline |

```
<LILAMY_DATA_DIR>/
├── mail_fetch/                   ← NEW: raw email archive
│   ├── inbox/                    ← One JSON file per received email
│   │   └── {entry_id}.json
│   └── sent/                     ← One JSON file per sent email  
│       └── {entry_id}.json
├── habit_learner.db              ← Learning database
├── mail_history.db               ← Existing IPC database (unchanged)
└── ...
```

**Time range:** From now back to **9 months** earlier. The fetch function takes `months_back=9` and computes the cutoff date.

**Source folders:**
- Inbox: `outlook.GetDefaultFolder(6)` — already exists via `fetch_inbox_emails()`
- Sent Items: `outlook.GetDefaultFolder(5)` — **NEW**, needs `fetch_sent_emails()` in `outlook_tool.py`

### 0.2 New Function: `fetch_sent_emails()` in `outlook_tool.py`

```python
def fetch_sent_emails(count=100, max_body=4000,
                      sent_after=None, sent_before=None) -> list[dict]:
    """Fetch emails from Sent Items folder.
    
    Returns per email dict:
      entry_id, subject, sender (Amy), recipients_to (JSON string),
      recipients_cc (JSON string), sent_time (ISO), body (plain text),
      conversation_id
    """
```

Uses the same `_fetch_inbox_emails_windows` pattern but against `GetDefaultFolder(5)`. Key difference: sent emails have `SentOn` (not `ReceivedTime`), `To` (not `Sender`).

### 0.3 HTML Stripping Utility

`beautifulsoup4` is already in dependencies (`pyproject.toml` line 8) but never imported. We add a proper stripper:

**File:** `shared/src/shared_tools/email_parser.py` (new)

```python
from bs4 import BeautifulSoup

def strip_html_to_text(html_content: str) -> str:
    """Convert MSO HTML email to clean plain text."""
    soup = BeautifulSoup(html_content, 'html.parser')
    for tag in soup(['style', 'script', 'head', 'xml']):
        tag.decompose()
    text = soup.get_text(separator='\n')
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return '\n'.join(lines)

def parse_email_address(raw: str) -> tuple[str, str]:
    """Parse 'Name <email>' → ('Name', 'email')."""

def normalize_subject(subject: str) -> str:
    """Strip RE:/FW:/FWD: prefixes for thread matching."""
```

### 0.4 Fetch Orchestration

The `HabitLearnerService` has a `fetch_from_outlook()` method that:
1. Computes `cutoff = now - 9 months`
2. Calls `fetch_inbox_emails(count=5000, received_after=cutoff)` (batched)
3. Calls `fetch_sent_emails(count=5000, sent_after=cutoff)` (batched)
4. Saves each email as `<dir>/{entry_id}.json`
5. Inserts into `raw_inbox` / `raw_sent` tables
6. Emits per-batch progress signals

---
## SECTION 1 — LEARNING SYSTEM

### 1.1 Data Layer: `habit_learner.db`

New SQLite database in `LILAMY_DATA_DIR` (NOT the IPC `mail_history.db`). **Nine tables** (two raw + seven learning):

```
raw_inbox (
    entry_id TEXT PRIMARY KEY,
    subject TEXT,
    sender_name TEXT,
    sender_email TEXT,
    recipients_to TEXT,              -- JSON array
    recipients_cc TEXT,              -- JSON array
    body_plain TEXT,
    body_html TEXT,
    received_time TEXT,              -- ISO datetime
    conversation_id TEXT,
    has_attachment INTEGER DEFAULT 0,
    json_path TEXT                   -- path to JSON file for visualization
)

raw_sent (
    entry_id TEXT PRIMARY KEY,
    subject TEXT,
    sender_name TEXT,                -- always "Amy Chen"
    sender_email TEXT,               -- always "amy@welink.com.au"
    recipients_to TEXT,              -- JSON array
    recipients_cc TEXT,              -- JSON array
    body_plain TEXT,
    body_html TEXT,
    sent_time TEXT,                  -- ISO datetime
    conversation_id TEXT,
    thread_subject_norm TEXT,
    json_path TEXT
)

sent_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT UNIQUE,          -- hash of file_path + position in thread
    sender_name TEXT,                -- "Amy Chen"
    sender_email TEXT,               -- "amy@welink.com.au"
    recipients_to TEXT,              -- JSON array of emails
    recipients_cc TEXT,              -- JSON array of emails  
    subject TEXT,
    body_plain TEXT,                 -- HTML stripped to plain text
    body_html TEXT,                  -- original HTML
    timestamp TEXT,                  -- ISO datetime parsed from email headers
    thread_subject_norm TEXT,        -- normalized subject for matching (no RE:/FW: prefixes)
    source_file TEXT,                -- which .txt file this came from
    thread_position INTEGER          -- 0 = newest in file, N = oldest
)

received_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- same columns as sent_messages, plus:
    matched_reply_id INTEGER REFERENCES sent_messages(id),  -- NULL if no reply found
    reply_latency_hours REAL,        -- NULL if no reply
    was_replied INTEGER NOT NULL DEFAULT 0  -- 0 or 1
)

reply_pairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_id INTEGER NOT NULL REFERENCES received_messages(id),
    reply_id INTEGER NOT NULL REFERENCES sent_messages(id),
    latency_hours REAL NOT NULL,
    intent TEXT,                     -- classified by LLM
    formality_level INTEGER,         -- 1-5, classified by LLM  
    greeting_used TEXT,              -- e.g. "Hi John," or NULL
    signoff_used TEXT,               -- e.g. "Kind regards," or NULL
    reply_word_count INTEGER,
    reply_paragraph_count INTEGER,
    uses_bullet_points INTEGER,      -- 0 or 1
    contains_question INTEGER,       -- 0 or 1
    contains_commitment INTEGER,     -- 0 or 1
    structure_type TEXT,             -- "full_4part" | "brief_ack" | "defer" | "answer_only"
    classification_confidence REAL   -- LLM's confidence
)

sender_profiles (
    sender_email TEXT PRIMARY KEY,
    sender_name TEXT,
    domain TEXT,
    tier INTEGER,                    -- 1-5
    tier_label TEXT,
    total_received INTEGER,
    total_replied INTEGER,
    reply_rate REAL,
    avg_latency_hours REAL,
    latency_std_hours REAL,
    preferred_greeting TEXT,
    avg_reply_words REAL,
    formality_level REAL,
    top_intent TEXT,                  -- most frequent intent
    signoff_preference TEXT,
    last_updated TEXT
)

style_matrix (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_tier INTEGER,
    category TEXT,
    avg_words REAL,
    formality REAL,
    greeting_style TEXT,
    signoff TEXT,
    uses_bullet_points REAL,
    structure_type TEXT,
    sample_count INTEGER,
    examples_json TEXT                -- JSON array of 3 representative reply pair IDs
)

intent_priors (
    dimension TEXT,                   -- "category" or "sender_tier"
    dimension_value TEXT,             -- e.g. "RFI" or "tier_1"
    intent TEXT,                      -- e.g. "defer_redirect"
    probability REAL,
    sample_count INTEGER
)

learning_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    completed_at TEXT,
    total_files INTEGER,
    parsed_messages INTEGER,
    matched_pairs INTEGER,
    unmatched_sent INTEGER,
    unmatched_received INTEGER,
    senders_discovered INTEGER,
    errors_json TEXT                  -- JSON array of error strings
)
```

### 1.2 HabitLearnerService Class

**File:** `shared/src/shared_tools/habit_learner_service.py`

Follows the exact service pattern: `QObject` + `pyqtSignal` + `threading.Thread` + `queue.Queue`.

#### Signals

```python
# Build phase signals
build_started = pyqtSignal(int)              # total_files
build_progress = pyqtSignal(int, int, str)   # current, total, stage_description
stage_complete = pyqtSignal(str, dict)       # stage_name, summary_stats
build_complete = pyqtSignal(dict)            # final summary
build_error = pyqtSignal(str)                # error message

# Per-message signals (for live visualization)
message_parsed = pyqtSignal(dict)            # parsed message info
pair_matched = pyqtSignal(dict)              # matched (received, reply) pair
intent_classified = pyqtSignal(int, str, float)  # pair_id, intent, confidence
sender_updated = pyqtSignal(str, dict)       # sender_email, updated profile
```

#### Stages (called via `build_profiles()`)

```
Stage 0: FETCH — Pull emails from Outlook Inbox + Sent Items
    Input: Outlook COM (live), time range = now to 9 months back
    Process: Call fetch_inbox_emails() + fetch_sent_emails() in batches,
             strip HTML to plain text, save JSON files to mail_fetch/inbox/ and sent/,
             insert into raw_inbox + raw_sent tables
    Output: raw_inbox + raw_sent tables populated, JSON files written
    Progress: batch / total batches, email count

Stage 1: NORMALIZE — Extract and normalize messages from raw tables
    Input: raw_inbox, raw_sent tables
    Process: Parse email addresses, normalize subjects (strip RE:/FW:),
             compute thread_subject_norm, classify each message as Amy's or external
    Output: sent_messages + received_messages tables populated
    Progress: message_count / total_messages

Stage 2: MATCH — Thread-match received messages to sent replies
    Process: For each sent message, find the received message it replies to
             Match criteria: normalized subject match + temporal proximity 
             (sent.timestamp > received.timestamp AND sent.timestamp - received.timestamp < 168 hours)
             + sender is in received's recipients
    Output: matched_reply_id set on received_messages, reply_pairs populated
    Progress: matched_pair_count / total_sent

Stage 3: CLASSIFY — LLM-classify each reply pair for intent and style features
    Process: For each reply_pair, call get_llm("fast") with a classification prompt
             Extract: intent, formality_level, greeting_used, signoff_used,
                      structure_type, contains_question, contains_commitment
    Output: reply_pairs enriched with classification columns
    Progress: classified_count / total_pairs

Stage 4: BUILD — Compute statistical profiles from labeled data
    Process: SQL aggregation queries, no LLM
    Output: sender_profiles, style_matrix, intent_priors tables populated
    Progress: sender_count, matrix_entry_count
```

#### Key Methods

```python
class HabitLearnerService(QObject):
    def fetch_from_outlook(self, months_back: int = 9):
        """Stage 0: Pull inbox + sent from Outlook, save JSON + insert into raw tables."""
        
    def build_profiles(self):
        """Run full 5-stage pipeline (Fetch → Normalize → Match → Classify → Build)."""
        
    def infer(self, email: dict) -> BehavioralContext | None:
        """Fast inference from pre-built profiles. No LLM call.
        Given an incoming email, returns behavioral predictions.
        email dict must have: sender, subject, body, cc, category, urgency"""
        
    def record_feedback(self, entry_id: str, generated_reply: str,
                        actual_reply: str | None, was_sent: bool):
        """Record what Amy actually did. Updates sender profile with moving average."""
        
    def get_learning_summary(self) -> dict:
        """Return stats about what was learned: total senders, pairs, 
        reply rate, tier distribution, etc."""
        
    def get_unmatched_received(self, limit: int = 100) -> list[dict]:
        """Return received emails that had NO matching reply. 
        These are emails Amy didn't reply to — useful for triage learning."""
        
    def get_sender_detail(self, sender_email: str) -> dict | None:
        """Full profile for one sender including example replies."""
```

### 1.3 BehavioralContext

**File:** `shared/src/shared_tools/habit_learner_service.py` (same file)

```python
@dataclass
class BehavioralContext:
    sender_profile: dict | None
    predicted_intent: str
    style_params: dict          # greeting, signoff, formality, avg_length, structure
    matched_examples: list[dict]  # top-K behaviorally similar reply examples
    confidence: float           # 0-1
    
    def to_injection_text(self) -> str:
        """Serialize to the text block injected into the reply agent's backstory."""
        # Produces something like:
        # "SENDER PROFILE: Amy replies to subcontractors in this tier 85% of the time,
        #  typically within 3 hours, using 'Hi [Name],' and ending with 'Cheers.'
        #  PREDICTED INTENT: acknowledge + defer (this is an RFI — Amy typically
        #  acknowledges receipt and forwards to engineer)
        #  STYLE: ~45 words, informal-professional, full 4-part structure"
```

### 1.4 Intent Classification Prompt (LLM)

The classification task in Stage 3 uses a CrewAI task (no new agent needed — direct LLM call with structured output):

```
Classify this email reply pair:

ORIGINAL EMAIL (received by Amy):
From: {sender}
Subject: {subject}
Body: {body_first_500_chars}

AMY'S REPLY:
Body: {reply_body}

Return JSON:
{
  "intent": "acknowledge|answer|ask_clarification|defer_redirect|commit_to_action|decline|social|escalate|close_loop",
  "formality_level": 1-5,
  "structure_type": "full_4part|brief_ack|defer|answer_only|cc_note",
  "contains_question": true/false,
  "contains_commitment": true/false,
  "greeting_used": "Hi John," or null,
  "signoff_used": "Kind regards," or null,
  "confidence": 0.0-1.0
}
```

---

## SECTION 2 — INTEGRATION

### 2.1 Changes to `MailService._run_reply_loop()`

**File:** `shared/src/shared_tools/mail_service.py`, lines 917-991

Before building `inputs` dict (line 948), add:

```python
# Get behavioral context for this email
behavioral_text = ""
try:
    from shared_tools.habit_learner_service import get_habit_service
    habit_svc = get_habit_service()
    ctx = habit_svc.infer(email)
    if ctx:
        behavioral_text = ctx.to_injection_text()
except Exception:
    pass  # graceful degradation — reply still works without habits
```

Add to `inputs` dict:
```python
inputs = {
    ...
    "behavioral_context": behavioral_text,    # NEW
    ...
}
```

### 2.2 Changes to `ReplyGeneratorCrew.reply_assistant()`

**File:** `tools/amail/src/amail/crew.py`, lines 144-183

Add behavioral injection between style_injection and examples_injection:

```python
# Dynamically load behavioral context (from habit learner)
behavioral_injection = ""
# The behavioral context text is passed via task inputs and injected here
# (Read from a temp file or passed through the task description)
# Actual mechanism: the {behavioral_context} variable in reply_tasks.yaml 
# receives the text; the agent backstory reads:
behavioral_path = os.path.join(_AMAIL_ROOT, "knowledge/behavioral_context.txt")
if os.path.exists(behavioral_path):
    with open(behavioral_path, "r", encoding="utf-8") as f:
        behavioral_injection = f.read()

agent_config['backstory'] = (
    agent_config.get('backstory', '') 
    + style_injection 
    + behavioral_injection    # NEW — injected after style, before examples
    + examples_injection 
    + identity_injection
)
```

**Alternative / cleaner approach:** Instead of a temp file, the behavioral context can be embedded directly into the task `description` via the `{behavioral_context}` template variable. The CrewAI task interpolation will substitute it automatically. This avoids the temp file and keeps the injection in the prompt layer, not the code layer.

### 2.3 Changes to `reply_tasks.yaml`

**File:** `tools/amail/src/amail/config/reply_tasks.yaml`

Add after the IDENTITY CHECK section (after line 27), before STANDARD REPLY STRUCTURE:

```yaml
BEHAVIORAL CONTEXT — How Amy typically handles emails like this one:
{behavioral_context}

If behavioral context is provided above, use it to guide your tone, length, 
structure, greeting, and sign-off choices. The context describes how Amy 
actually replies to similar emails from similar senders — match it.
If empty, fall back to the standard style blueprint.
```

### 2.4 Changes to `MailService._run_reply_loop()` — Writing Context for Task

Since the CrewAI task uses `{behavioral_context}` as a template variable, we inject it into the task's `description` field before kickoff. The cleanest approach: write it as part of the `inputs` dict, and the task YAML references it as `{behavioral_context}`:

```python
inputs = {
    ...
    "behavioral_context": behavioral_text,  # CrewAI interpolates {behavioral_context}
    ...
}
```

This works because CrewAI's `Task` interpolates `{variable}` placeholders in `description` from the `inputs` dict passed to `kickoff()`.

### 2.5 Changes to `main.py` — New Entry Point

**File:** `tools/amail/src/amail/main.py`

Add new CLI entry point:

```python
def build_habits():
    """Build Amy's email reply habit profiles from Outlook data (9 months back)."""
    import sys
    from PyQt6.QtWidgets import QApplication
    from shared_tools.habit_learner_service import HabitLearnerService
    
    app = QApplication(sys.argv)
    service = HabitLearnerService()
    
    # Connect signals for console progress
    service.build_progress.connect(
        lambda c, t, s: print(f"[{c}/{t}] {s}")
    )
    service.stage_complete.connect(
        lambda name, stats: print(f"\n--- Stage {name} complete: {stats} ---")
    )
    service.build_complete.connect(
        lambda summary: print(f"\nDone: {json.dumps(summary, indent=2)}")
    )
    
    service.build_profiles()
    
    # Process events to allow signal delivery
    import time
    while service._running:
        app.processEvents()
        time.sleep(0.1)
```

Register in `pyproject.toml`:
```toml
[project.scripts]
build_habits = "amail.main:build_habits"
```

---

## SECTION 3 — TRAINING VISUALIZATION

### 3.1 Desktop Training Dialog: `HabitLearnerDialog`

**File:** `tools/amail/src/amail/habit_learner_dialog.py`

A `QDialog` with a three-zone layout:

```
┌──────────────────────────────────────────────────────────────┐
│  Habit Learner — Building Amy's Reply Profiles               │
│  Progress: Stage 2/4 — Matching replies...  [████████░░] 67% │
├──────────────────────┬───────────────────────────────────────┤
│  LEFT: Email Feed    │  RIGHT: Detail / Stats                │
│  (scrollable list)   │                                       │
│                      │  CURRENT STAGE STATS:                 │
│  ✓ 198 files parsed  │  Files parsed: 198                    │
│  ✓ 847 messages      │  Sent messages found: 412             │
│  ● Matching pairs... │  Received messages found: 435         │
│    ├─ "ARCO RFI 30"  │  Pairs matched: 287/412 (70%)         │
│    │  matched ✓      │  Unmatched sent: 125                  │
│    ├─ "CBR Demo..."  │  Unmatched received: 148              │
│    │  no reply ✗     │  ← These are non-replied emails       │
│    ├─ "Autura inv..."│                                       │
│    │  matched ✓      │  DETECTED ISSUES:                     │
│    ├─ "Apt 606..."   │  ⚠ 125 sent emails couldn't be        │
│    │  matched ✓      │    matched to any received email      │
│    └─ ...            │  ⚠ 148 received emails had no reply   │
│                      │  ℹ 3 senders had < 3 emails each      │
│                      │    (profiles may be unreliable)        │
├──────────────────────┴───────────────────────────────────────┤
│  [View Non-Replied]  [View Sender Profiles]  [Close]         │
└──────────────────────────────────────────────────────────────┘
```

#### Left Panel: Live Email Feed

A `QScrollArea` containing a vertical list of cards. Each card represents one parsed file or one matched pair, depending on the current stage:

- **Stage 1 (Parse):** One card per .txt file. Shows filename, message count extracted, status (pending/parsing/done/error).
- **Stage 2 (Match):** One card per sent message. Shows subject, recipient, whether a match was found (✓ matched / ✗ no reply found).
- **Stage 3 (Classify):** One card per reply pair. Shows subject, classified intent, confidence score.
- **Stage 4 (Build):** One card per sender. Shows email, tier, reply rate, preferred greeting.

Cards are color-coded:
- Green left border: matched/replied
- Red left border: no reply found
- Yellow left border: uncertain (low confidence classification)
- Gray: pending/processing

Selection: Click a card → right panel shows details for that item.

#### Right Panel: Stats + Detail

Two stacked sections:

1. **Stage Stats** (top, always visible): Live counters for the current stage. Updates via signal handlers. Shows:
   - Files parsed / total files
   - Messages extracted
   - Pairs matched / unmatched counts
   - Current operation description
   - Overall reply rate (pairs / received messages)

2. **Item Detail** (bottom, changes on card selection): Shows full detail for the selected card:
   - For a matched pair: original email body, Amy's reply body, classified intent, latency, style features
   - For an unmatched received: full email body, why no match was found
   - For a sender profile: all stats, example replies

3. **Issues/Warnings section**: Accumulates warnings during the build:
   - Sent emails that couldn't be matched (Amy wrote something but we can't find what she was replying to)
   - Received emails with no reply (Amy chose not to reply — valuable triage signal)
   - Low-confidence classifications
   - Senders with too few samples for reliable profiling

#### Buttons

- **View Non-Replied:** Filters the left panel to show only unmatched received emails. Opens a separate dialog listing all emails Amy didn't reply to, sorted by sender. This is the triage learning signal.
- **View Sender Profiles:** Opens a table dialog showing all discovered sender profiles (email, name, tier, reply rate, preferred greeting). Sortable columns.
- **Export:** Writes all profiles + stats to `knowledge/` as JSON files.
- **Close:** Dismisses the dialog.

### 3.2 Signal Wiring

The dialog connects to `HabitLearnerService` signals:

```python
service.build_started.connect(self._on_build_started)
service.build_progress.connect(self._on_progress)         # updates progress bar + stage label
service.stage_complete.connect(self._on_stage_complete)   # updates stats, switches card mode
service.message_parsed.connect(self._on_message_parsed)   # adds card to left panel
service.pair_matched.connect(self._on_pair_matched)       # updates card status
service.intent_classified.connect(self._on_intent_classified)  # updates card detail
service.sender_updated.connect(self._on_sender_updated)   # adds/updates sender card
service.build_complete.connect(self._on_build_complete)   # enables export, shows final stats
service.build_error.connect(self._on_error)               # shows error card
```

### 3.3 WebUI Training View

**File:** `lilamy/modules/habit_learner_routes.py` (new)

API endpoints:

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/habits/build` | Start profile build (async) |
| GET | `/api/habits/status` | Current build status + progress |
| GET | `/api/habits/senders` | List all sender profiles |
| GET | `/api/habits/senders/{email}` | One sender's full profile |
| GET | `/api/habits/pairs?matched=true` | List reply pairs (matched) |
| GET | `/api/habits/pairs?matched=false` | List non-replied emails |
| GET | `/api/habits/summary` | Learning summary stats |
| GET | `/api/habits/errors` | Build errors/warnings |
| POST | `/api/habits/feedback` | Record feedback on a generated reply |

**File:** `lilamy/static/habits.html` (new) or embedded in `app.js`

Training view page with:
- Progress bar at top (stage name + percentage)
- Stat cards row: files parsed, messages, pairs, senders, reply rate
- Main content area with tabs: All Pairs | Non-Replied | Sender Profiles
- Each tab has filterable/searchable card list
- Click card → expands to show detail

**File:** `lilamy/modules/registry.py` — add module entry:

```python
{
    "id": "habits",
    "name": "Habit Learner",
    "icon": "brain",
    "description": "Learn Amy's email reply patterns",
    "enabled": True,
    "router_path": "lilamy.modules.habit_learner_routes:router",
}
```

### 3.4 What the User Sees During a Training Session

1. **Before build:** Dialog opens, showing "Ready to learn from X files in historical_emails/" with a count of files found.

2. **Stage 1 — Parse (fast, ~10 seconds for 198 files):** Left panel fills with file cards. Each card shows filename → number of messages extracted. Progress bar at top: "Parsing files... 150/198".

3. **Stage 2 — Match (fast, ~5 seconds):** Cards switch to sent-message view. Each card shows subject + match result. Stats update live: "Pairs matched: 200/412". Mismatches accumulate in the issues list.

4. **Stage 3 — Classify (slow, ~1-2 min for 300 pairs):** Cards switch to pair view. Each card updates as the LLM classifies it — shows intent label + confidence. Progress bar: "Classifying replies... 150/300". This is the stage where the user watches the LLM label each reply.

5. **Stage 4 — Build (fast, ~2 seconds):** Cards switch to sender view. Profiles appear. Stats finalize: "87% reply rate across 45 senders".

6. **After build:** User can browse non-replied emails ("Why didn't Amy reply to these?"), view sender profiles, and export. The "View Non-Replied" button is highlighted if there are many unmatched received emails.

---

## SECTION 4 — IMPLEMENTATION ORDER

### Phase A: Data Acquisition + Core Service (no UI) — **90% DONE**
1. ~~`outlook_tool.py` — add `fetch_sent_emails()` function~~ ⚠️ **NOT DONE** — imported but undefined
2. ✅ `email_parser.py` — HTML stripping + address parsing utilities
3. ✅ `habit_learner_db.py` — database schema (9 tables) + CRUD
4. ✅ `habit_learner_service.py` — `HabitLearnerService` class with fetch + 5 build stages + `infer()` + `record_feedback()`
5. ❌ Unit tests for parsing, matching, classification

### Phase B: Integration — **100% DONE + WebUI**
4. ✅ Modify `mail_service.py` — inject behavioral context into `_run_reply_loop()`
5. ✅ Modify `crew.py` — (unnecessary — CrewAI template interpolation handles `{behavioral_context}`)
6. ✅ Modify `reply_tasks.yaml` — add behavioral context template variable
7. ✅ Modify `main.py` — add `build_habits` + `view_habits` CLI entry points
8. ✅ Modify `amail_routes.py` — WebUI reply endpoint + `agent_info` in response + terminal logging
9. ✅ Modify `index.html` — agent info panel (sender profile, intent, style, context, examples)
10. ✅ Modify `app.js` — `renderAgentInfo()`, `showAgentInfoLoading()`, `hideAgentInfo()`, `toggleAgentContext()`

### Phase B — Bug Fixes (2026-06-14)
- ✅ Reply rate always 100% → fixed with `get_sender_received_stats()` querying `received_messages`
- ✅ Confidence always 100% → new formula scaling with sample size (base 0.15, sample bonus 0→0.35, cap 0.90)
- ✅ Examples had no content → `to_injection_text()` now includes subject + reply snippet for each example
- ✅ Examples were random → `_select_examples()` min score threshold of 5
- ✅ WebUI Draft Reply 500 error → added `behavioral_context` to endpoint inputs
- ✅ Agent info panel DOM destroyed → `showAgentInfoLoading()` no longer uses `innerHTML =`

### Phase C: Training Visualization — **NOT STARTED**
8. `habit_learner_dialog.py` — desktop training dialog
9. `habit_learner_routes.py` — WebUI API routes
10. WebUI training view (static HTML/JS or embedded in existing app.js)
11. Wire dialog launch into lilAmy desktop GUI and WebUI sidebar

---

## SECTION 5 — VERIFICATION

### Manual Testing
1. `uv run build_habits` — runs full 4-stage pipeline, prints progress
2. `uv run lilamy` — opens desktop GUI, launch Habit Learner from menu
3. `uv run lilamy --web` — opens WebUI, navigate to Habit Learner module
4. Check `knowledge/sender_profiles.json` exists and contains reasonable data
5. Generate a reply for an email from a known sender — verify behavioral context is injected and reply tone/length/greeting matches Amy's habits

### Automated Testing
```bash
uv run pytest tools/amail/tests/test_habit_learner.py -v
```
Tests cover:
- HTML thread parsing (extract messages from MSO HTML)
- Subject normalization for thread matching
- Match logic (correct pair identification, latency calculation)
- Intent classification prompt (JSON parse validation)
- `BehavioralContext.to_injection_text()` output format
- `infer()` returns correct defaults when no profile matches
- `record_feedback()` updates moving averages correctly

### Key Files Summary

| File | Action | Purpose |
|---|---|---|
| `shared/src/shared_tools/email_parser.py` | **NEW** | HTML stripping (beautifulsoup4), address parsing, subject normalization |
| `shared/src/shared_tools/outlook_tool.py` | MODIFY | Add `fetch_sent_emails()` for Sent Items folder |
| `shared/src/shared_tools/habit_learner_db.py` | **NEW** | Database schema (9 tables) + CRUD |
| `shared/src/shared_tools/habit_learner_service.py` | **NEW** | HabitLearnerService + BehavioralContext |
| `shared/src/shared_tools/mail_service.py` | MODIFY | Inject behavioral context in `_run_reply_loop()` |
| `tools/amail/src/amail/crew.py` | MODIFY | `ReplyGeneratorCrew` reads behavioral context |
| `tools/amail/src/amail/config/reply_tasks.yaml` | MODIFY | Add `{behavioral_context}` template variable |
| `tools/amail/src/amail/main.py` | MODIFY | Add `build_habits` entry point |
| `tools/amail/src/amail/habit_learner_dialog.py` | **NEW** | Desktop training visualization |
| `lilamy/modules/habit_learner_routes.py` | **NEW** | WebUI API for habits |
| `lilamy/modules/registry.py` | MODIFY | Register habit learner module |
| `lilamy/static/habits.html` or `app.js` | **NEW/MODIFY** | WebUI training view |
| `tools/amail/tests/test_habit_learner.py` | **NEW** | Unit tests |
