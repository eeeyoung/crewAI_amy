# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Amy is a CrewAI-powered email triage and auto-reply workstation for construction project management. It reads unread emails from Microsoft Outlook (via COM on Windows), runs them through a pipeline of AI agents to clean, categorize, draft replies, and generate task workflows — all presented through an interactive PyQt6 GUI.

## Commands

```bash
# Install dependencies (Python >=3.10, <3.14 required)
uv sync

# Run the main workflow: fetch unread Outlook emails and launch interactive GUI
uv run amy

# Extract writing style blueprint from historical emails
uv run extract_style

# Train ReplyGeneratorCrew (arg: number of iterations)
uv run train 3

# View all facts stored in the project knowledge base
uv run view_facts

# Run the test suite
uv run pytest
```

## Architecture

The system is a **6-agent pipeline** with a PyQt6 GUI orchestrating everything:

### Pipeline Stages (in order)

1. **MessageFilterCrew** — Cleans raw email bodies: strips signatures, legal disclaimers, social media links. For multi-party threads, restructures into chronological sections with sender identity headers.
2. **TriageSingleCrew** — Classifies each email by construction domain (RFI, Submittal, Financial, Safety, Scheduling, etc.) and urgency. Returns JSON: `{category, urgency, extra_info}`.
3. **ReplyGeneratorCrew** — Generates a professional email reply in Amy Chen's writing style. Has CrewAI memory enabled with Google Generative AI embeddings. Dynamically injects `knowledge/style_blueprint.md` and recent few-shot examples from `knowledge/reply_examples.jsonl` into the agent's backstory.
4. **WorkflowGeneratorCrew** — Generates a step-by-step task workflow for handling the email, identifying other specialized AI agents to activate.
5. **FactExtractorCrew** — On user request ("Save Key Facts"), extracts durable project facts (project names, reference numbers, dates, decisions, specs) from an email and stores them in an FTS5 full-text search database. This knowledge is then injected into future reply generation via `search_facts()`.

### Source File Map (`src/amy/`)

| File | Purpose |
|------|---------|
| `main.py` | Entry points: `run`, `train`, `extract_style`, `view_facts`, `test` |
| `crew.py` | All 6 CrewAI crew definitions + `get_llm()` provider routing |
| `gui_viewer.py` | PyQt6 GUI — TriageWindow, background workers (FilterWorker, TriageWorker, ReplyWorker, WorkflowWorker, RegenerateWorker), dialogs |
| `fact_store.py` | SQLite FTS5 knowledge base for project facts: `init_db()`, `save_facts()`, `search_facts()`, `list_all_facts()` |
| `tools/outlook_tool.py` | Outlook COM integration: fetch emails, send replies with inline images, mark read/unread, attachment management |
| `tools/outlook_reply_tool.py` | Legacy reply tool |
| `tools/check_email_body.py` | Email body validation utility |
| `tools/custom_tool.py` | CrewAI custom tool base |

### GUI (`src/amy/gui_viewer.py`)

PyQt6 window with left panel (original email + filtered view overlay) and right panel (draft reply, category/urgency labels, workflow dialog). Uses background `QThread` workers processing emails through the pipeline concurrently via queues. Keyboard shortcuts: `A`/`D` (prev/next), `R` (regenerate), `W` (workflow), `1` (send), `2`/`3` (skip read/unread), `4` (save as example), `5` (save key facts), `Q` (attachments).

### LLM Provider Toggle

Controlled by `AI_PROVIDER` env var in `.env`:
- `ds` → DeepSeek (`deepseek-chat` for standard tasks, `deepseek-reasoner` for "smart" role)
- `gem` (default) → Gemini (`gemini-2.5-flash` / `gemini-2.5-pro`)

The `get_llm(role)` function in `crew.py` routes all LLM requests.

### Outlook Integration (`src/amy/tools/outlook_tool.py`)

Windows-only (COM via `win32com.client`). Key functions:
- `fetch_inbox_emails(count, max_body, unread_only)` — returns list of email dicts with subject, sender, cc, received_time, body, entry_id
- `OutlookSendTool` — CrewAI tool that sends emails, attaches inline images (logo, signature icons) with Content-ID, embeds `amy_signature.html`
- `mark_email_as_read()` / `mark_email_as_unread()` by EntryID
- `fetch_attachments_for_email()` / `save_attachment()` — attachment management

### Knowledge Base (`knowledge/`)

- `style_blueprint.md` — Generated writing style analysis (output of `extract_style`)
- `reply_examples.jsonl` — User-curated few-shot examples for reply generation (appended via "Save as Example" button)
- `workflow_examples.jsonl` — User-curated workflow examples
- `historical_emails/` — Historical emails used by StyleLearnerCrew
- `amy_signature.html` — HTML email signature with logo/images
- `logo_meritor_welink.png`, `logo_hia_awards.png`, `icon_instagram.png`, `icon_facebook.png` — Signature/email images
- `fact_store.db` — SQLite FTS5 database storing extracted project facts (created automatically on first run)

### Key Configuration

- Agents/tasks defined as YAML in `src/amy/config/` (filter, triage, reply, workflow, fact_extractor, style_learner)
- `agents.yaml` and `tasks.yaml` are **legacy/abandoned** — the active pipeline uses the per-crew config files
- `pyproject.toml` has `[tool.crewai] type = "crew"` which tells the CLI this is a crew project
- `.env` contains API keys and model selection — never commit changes to it

### Style Extraction (`convert_pst_txt.py`)

Standalone script to convert Outlook PST exports to text files in `historical_emails/`. Requires `libpff-python`. Extracts only from "Sent Items" folder to capture the user's outgoing writing style.
