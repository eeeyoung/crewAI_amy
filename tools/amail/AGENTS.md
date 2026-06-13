# AGENTS.md — AMail (Email Triage & Auto-Reply)

> Cross-tool project context. For Claude-specific internals, see `CLAUDE.md`.
> For the generic CrewAI API reference (LLM config, Flows, deployment, etc.), see the upstream template at `.venv/Lib/site-packages/crewai/cli/templates/AGENTS.md` or `https://github.com/crewAIInc/crewAI`.
> Installed version: CrewAI 1.14.2.

## What AMail Does

Reads unread Outlook emails, runs them through a 6-agent CrewAI pipeline, and presents results in an interactive PyQt6 GUI. Can draft replies as Amy Chen, generate task workflows, extract project facts, and store everything in a shared DB for ACalendar and ASummary1 to consume.

## Pipeline (sequential, 6 stages)

```
Outlook Inbox
     │
     ▼
MessageFilterCrew    — Strip signatures, disclaimers, restructure threads
     │
     ▼
TriageSingleCrew     — Classify: RFI, Submittal, Financial, Safety, Scheduling, etc.
     │                + urgency (low/medium/high/critical)
     ▼
ReplyGeneratorCrew   — Draft reply as Amy Chen (30yr AU construction PM/CA)
     │                Uses style_blueprint.md + reply_examples.jsonl as few-shot
     ▼
WorkflowGeneratorCrew — Step-by-step task workflow + activate specialist agents
     │
     ▼
[FactExtractorCrew]  — On-demand (user clicks "Save Key Facts")
     │                Extracts project names, dates, decisions → FTS5 search DB
     ▼
GrammarPolisherCrew  — Polish draft text before send
```

## Source Map

| File | Purpose |
|---|---|
| `src/amail/main.py` | Entry points: `run`, `train`, `extract_style`, `view_facts` |
| `src/amail/crew.py` | All 6 CrewAI crew definitions + `get_llm()` routing |
| `src/amail/gui_viewer.py` | PyQt6 GUI — TriageWindow consumes MailService |
| `src/amail/fact_store.py` | SQLite FTS5 knowledge base for project facts |
| `src/amail/graph_dialog.py` | Graph API sign-in dialog (optional, detachable) |
| `src/amail/mail_knowledge.py` | FTS5 email knowledge base |
| `config/` | Per-crew YAML: filter, triage, reply, workflow, fact_extractor, grammar |

## Custom Tools Used

| Tool | Source | Functions |
|---|---|---|
| Outlook COM | `shared_tools/outlook_tool.py` | `fetch_inbox_emails()`, `mark_email_as_read()`, `OutlookSendTool` |
| LLM Config | `shared_tools/llm_config.py` | `get_llm(role)` — roles: `"fast"`, `"smart"`. Provider: `AI_PROVIDER` env var |
| IPC Bridge | `shared_tools/ipc_bridge.py` | Shared DB for AMail↔ACalendar communication |
| Fact Store | `src/amail/fact_store.py` | `save_facts()`, `search_facts()`, `list_all_facts()` |

## Service Architecture (after refactor)

AMail now follows the **service-first pattern** established in the root `CLAUDE.md`:

- **`MailService`** (`shared/src/shared_tools/mail_service.py`) — QObject owning all pipeline logic, state, threading, and LLM orchestration
- **`gui_viewer.py`** — Thin PyQt6 consumer: connects to MailService signals (`filter_done`, `category_ready`, `reply_generated`, `workflow_generated`), calls service methods
- **No QThread subclasses in GUI** — all async work uses `threading.Thread` + `queue.Queue` inside MailService

## NEVER (AMail-specific)

- ❌ Put pipeline logic back in `gui_viewer.py` — it was extracted to MailService for a reason
- ❌ Create new QThread subclasses — use MailService methods
- ❌ Call `fetch_inbox_emails()` or `mark_email_as_read()` directly from GUI — go through MailService
- ❌ Add new CrewAI crews without corresponding YAML configs in `config/`
- ❌ Touch `_llm_semaphore` from GUI code — MailService owns it
- ❌ Modify `agents.yaml` or `tasks.yaml` at root of config — those are **abandoned/legacy**. Use per-crew config files

## LLM Routing

```python
from shared_tools.llm_config import get_llm

# Fast tasks (triage, filter, grammar): get_llm("fast")
# Complex tasks (reply, workflow): get_llm("smart")

# Environment: AI_PROVIDER=gem → Gemini | AI_PROVIDER=ds → DeepSeek
```

## GUI Keyboard Shortcuts

| Key | Action |
|---|---|
| `A` / `D` | Previous / Next email |
| `R` | Regenerate reply |
| `W` | Open workflow dialog |
| `1` | Send email |
| `2` / `3` | Skip (mark read / leave unread) |
| `4` | Save as reply example |
| `5` | Save key facts (FactExtractorCrew) |
| `Q` | Open attachment dialog |

## Test Expectations

```bash
uv run pytest tools/amail/tests/ -v    # 83 pass, 6 pre-existing failures in test_crew.py
```

The 6 failures in `test_crew.py` are pre-existing and not blockers — they relate to CrewAI decorator resolution in test environments, not runtime behavior.
