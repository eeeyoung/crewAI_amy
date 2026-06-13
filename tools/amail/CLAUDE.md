# CLAUDE.md — AMail

Email triage and auto-reply agent for the lilAmy platform. Reads Outlook inbox, runs emails through a 6-agent CrewAI pipeline, presents results in a PyQt6 GUI. **Now follows the service-first pattern: MailService owns all pipeline logic; GUI is a thin consumer.**

## Commands

```bash
uv sync                               # Install dependencies
uv run amail                          # Launch GUI (fetch unread + interactive triage)
uv run python -m amail.main           # Same as above
uv run extract_style                  # Build writing style blueprint from historical emails
uv run view_facts                     # View all FTS5 stored facts
uv run pytest tools/amail/tests/ -v   # 83 pass, 6 pre-existing (test_crew.py)
```

## Architecture

### Service-First (after refactor)

```
gui_viewer.py (PyQt6)          ← Thin UI: widgets, layouts, signal handlers
        │  calls methods, connects to signals
        ▼
MailService (shared_tools)      ← QObject owning ALL pipeline logic
        │  uses threading.Thread + queue.Queue (NOT QThread)
        ▼
CrewAI crews (crew.py)          ← 6 crews, each with YAML config
Outlook COM (outlook_tool.py)   ← Fetch, send, mark, attachments
IPC Bridge (ipc_bridge.py)      ← AMail↔ACalendar shared DB
Fact Store (fact_store.py)      ← FTS5 knowledge base
LLM Config (llm_config.py)      ← Provider routing
```

### Pipeline (sequential, 6 stages)

1. **MessageFilterCrew** — Strips signatures, disclaimers, social links. Restructures multi-party threads chronologically.
2. **TriageSingleCrew** — Classifies: RFI, Submittal, Financial, Safety, Scheduling, etc. Returns `{category, urgency, extra_info, dates}`.
3. **ReplyGeneratorCrew** — Drafts reply as Amy Chen. Uses `knowledge/style_blueprint.md` + `reply_examples.jsonl` as few-shot. Has CrewAI memory (Google Generative AI embeddings).
4. **WorkflowGeneratorCrew** — Step-by-step task workflow. Identifies specialist agents to activate.
5. **FactExtractorCrew** — On-demand ("Save Key Facts"). Extracts project names, dates, decisions, specs → FTS5.
6. **GrammarPolisherCrew** — Polish draft text before send.

## Source Map

| File | Purpose |
|---|---|
| `src/amail/main.py` | Entry points: `run`, `train`, `extract_style`, `view_facts` |
| `src/amail/crew.py` | All 6 crew definitions |
| `src/amail/gui_viewer.py` | PyQt6 GUI — TriageWindow (thin, consumes MailService) |
| `src/amail/fact_store.py` | SQLite FTS5: `init_db()`, `save_facts()`, `search_facts()` |
| `src/amail/graph_dialog.py` | Graph API sign-in dialog (optional, detachable) |
| `src/amail/mail_knowledge.py` | Email FTS5 knowledge base |
| `config/*.yaml` | Per-crew agent/task configs (filter, triage, reply, workflow, fact_extractor, grammar) |

## External Services Used

| Service | Source | What it provides |
|---|---|---|
| MailService | `shared_tools/mail_service.py` | Pipeline orchestration, state, threading, signals |
| Outlook COM | `shared_tools/outlook_tool.py` | `fetch_inbox_emails()`, `mark_email_as_read()`, `OutlookSendTool` |
| LLM Config | `shared_tools/llm_config.py` | `get_llm(role)` — `"fast"` or `"smart"` |
| IPC Bridge | `shared_tools/ipc_bridge.py` | Shared DB for AMail↔ACalendar comm |

## NEVER (AMail-specific)

- ❌ Put pipeline logic back in `gui_viewer.py` — MailService owns it now
- ❌ Create new QThread subclasses — use `threading.Thread` in MailService
- ❌ Call Outlook COM or SQLite directly from GUI — route through MailService or existing tools
- ❌ Touch `_llm_semaphore` from GUI code — it lives inside MailService
- ❌ Modify root `config/agents.yaml` or `config/tasks.yaml` — they're **abandoned/legacy**. Use the per-crew configs
- ❌ Construct `LLM()` or `ChatOpenAI()` directly — always `get_llm(role)` from `llm_config.py`
- ❌ Add new crews without corresponding YAML configs

## Gotchas

### MailService is the source of truth
After the service extraction, all email state (`_emails`, `_state`, `_processed_entry_ids`, `_skipped_indices`) lives in MailService. The GUI's `TriageWindow` accesses state through MailService properties. Never add state-tracking attributes directly to TriageWindow.

### Outlook COM is Windows-only
`outlook_tool.py` requires `win32com.client`. All Outlook access must go through this module — never import `win32com` directly in new code.

### CrewAI YAML configs
`agents.yaml` and `tasks.yaml` at the config root are **abandoned**. The active pipeline uses: `filter_agents.yaml`, `filter_tasks.yaml`, `triage_agents.yaml`, `triage_tasks.yaml`, `reply_agents.yaml`, `reply_tasks.yaml`, `workflow_agents.yaml`, `workflow_tasks.yaml`, `fact_extractor_agents.yaml`, `fact_extractor_tasks.yaml`, `grammar_agents.yaml`, `grammar_tasks.yaml`.

### LLM provider toggle
`AI_PROVIDER=gem` (default, Gemini) or `AI_PROVIDER=ds` (DeepSeek). Set in `.env`. The `get_llm()` function in `crew.py` handles routing — don't duplicate this logic.

## GUI Keyboard Map

| Key | Action |
|---|---|
| `A` / `D` | Prev / Next email |
| `R` | Regenerate reply |
| `W` | Workflow dialog |
| `1` | Send email |
| `2` | Skip + mark read |
| `3` | Skip + leave unread |
| `4` | Save as reply example |
| `5` | Save key facts |
| `Q` | Attachment dialog |

## Knowledge Base (`knowledge/`)

- `style_blueprint.md` — Amy Chen's writing style (generated by `extract_style`)
- `reply_examples.jsonl` — Curated few-shot reply examples
- `workflow_examples.jsonl` — Curated workflow examples
- `historical_emails/` — PST exports from Sent Items (used by StyleLearnerCrew)
- `amy_signature.html` — HTML email signature with embedded images
- `fact_store.db` — SQLite FTS5 facts database (created on first run)

## Testing

```bash
uv run pytest tools/amail/tests/ -v    # 83 pass, 6 pre-existing failures (test_crew.py)
```

The 6 failures are CrewAI decorator resolution issues in test environments — not runtime bugs. New tests should use `pytest` with mocked crews.
