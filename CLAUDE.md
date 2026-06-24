# CLAUDE.md — lilAmy Platform

Python 3.10–3.13, CrewAI 1.14.2, PyQt6, SQLite + ChromaDB, Windows (Outlook COM).

## Commands

```bash
uv sync                                          # Install all dependencies
uv run lilamy                                    # Launch platform (desktop card UI)
uv run lilamy --web                              # Launch WebUI (http://127.0.0.1:8765)
uv run lilamy --amail                            # Launch legacy AMail GUI
uv run amail                                     # Legacy AMail (unchanged)
uv run acalendar                                 # ACalendar GUI
uv run asummary1                                 # Email summary GUI
uv run pytest tools/amail/tests/ -v              # AMail tests (83 pass, 6 pre-existing)
uv run python ingest.py --data-dir D:/path       # Ingest documents into hybrid DB
uv run python chat.py                            # Terminal RAG chatbot
```

## Core Architectural Principle

**Always separate core logic from UI.** Every feature must be built as a standalone service class with a clean Python API before any GUI is attached. The UI layer (PyQt6, FastAPI, web frontend) must be a thin consumer of the service — never the owner of business logic.

### The Pattern

```
┌──────────────────────┐
│  UI Layer             │  PyQt6 / FastAPI / React / CLI
│  (thin, replaceable)  │  calls service methods, connects to signals
└──────────┬───────────┘
           │  Python method calls
┌──────────▼───────────┐
│  Service Class        │  QObject with signals, owns all logic
│  (reusable, testable) │  threading.Thread + queue.Queue, CrewAI, COM, DB
└──────────────────────┘
```

### Rules (with WHY)

1. **New features → service class first.** Create in `shared/src/shared_tools/` as a QObject with public methods, PyQt signals, and `threading.Thread` + `queue.Queue` concurrency. **Why:** A service can have multiple UIs (CLI today, web tomorrow) and can be tested without a GUI.
2. **UI calls service, never touches internals.** GUI widgets call `self.service.do_thing()`. They never access queues, threads, semaphores, or raw state dicts. **Why:** If the UI touches internals, refactoring the service breaks the UI.
3. **QThread is deprecated for new code.** Use `threading.Thread` + `queue.Queue` in services. QThread subclasses in GUI are legacy that must be extracted. **Why:** QThread ties logic to PyQt6; threading.Thread is portable.
4. **Forwarding properties for minimal change.** When refactoring existing GUI code, use `@property` to delegate `self.state` → `self.service._state`. **Why:** Keeps existing GUI code working without full rewrites.
5. **No package bloat.** Use only `requests` (already present) for HTTP. Do not install new packages without explicit approval. **Why:** Keeps the dependency surface auditable and deployment lightweight.
6. **ALL data outside repo.** Databases, embeddings, caches, tokens go to `LILAMY_DATA_DIR` (env var). Never inside the repository tree. **Why:** Multi-client deployments; pushing client data to git is a confidentiality breach.

## Coding Rules — Communication & Decision Making

- 🛑 **If you are confused or unclear about ANYTHING, STOP and ASK immediately.** Do not proceed, do not assume, do not guess. Clarify first.
- 🛑 **Any suggestion or recommendation MUST be backed by evidence.** Cite code, documentation, or test results as proof. No hand-waving.
- 🛑 **DO NOT try to please me.** I value correctness over agreement. If my idea is flawed, tell me plainly and explain why with supporting evidence.
- 🛑 **DO NOT agree with my opinion just because it's mine.** Challenge assumptions. Propose better alternatives when you see them.
- 🛑 **Ask about ANYTHING you find confusing — immediately.** Even small ambiguities compound into bugs.

## NEVER

- ❌ **Put business logic in a QWidget/QDialog subclass** — extract to a service first
- ❌ **Create new QThread subclasses** — use `threading.Thread` in a QObject service
- ❌ **Put `_llm_semaphore` or `queue.Queue` at module level in GUI files** — they belong in a service class
- ❌ **Call Outlook COM or SQLite directly from GUI code** — route through a service or existing tool
- ❌ **Duplicate a crew.py or YAML config across tools** — extract the shared logic to `shared_tools/`
- ❌ **Store .db, .bin, .pkl, .jsonl, .chromadb/ inside the repo** — everything goes to `LILAMY_DATA_DIR`
- ❌ **Hardcode paths** — always import from `ipc_bridge` (`CREWAI_DIR`, `DB_PATH`) or read `LILAMY_DATA_DIR`
- ❌ **Install new PyPI packages** without explicit approval
- ❌ **Use `ChatOpenAI(model_name=...)`** — always `crewai.LLM` via `get_llm(role)` from `llm_config.py`
- ❌ **Silently catch exceptions** — always log or surface errors. An empty `except` block is a time bomb.
- ❌ **Assume API compatibility** — verify the exact function signatures before calling. The `genai.upload_file()` bug (wrong kwarg silently caught) is the canonical example.

## Existing Services

| Service | Location | Purpose |
|---|---|---|
| `MailService` | `shared/src/shared_tools/mail_service.py` | AMail pipeline: fetch → filter → triage → reply → workflow |
| `CalendarService` | `shared/src/shared_tools/calendar_service.py` | Event CRUD, conflict detection, weekly digest, IPC |
| `MemoryService` | `shared/src/shared_tools/memory_service.py` | ChromaDB ingestion + hybrid search |
| `FileRegistry` | `shared/src/shared_tools/file_registry.py` | SQLite file tracker with MD5 hashing |
| `PDFVisionService` | `shared/src/shared_tools/pdf_vision_service.py` | Multi-modal PDF processing via Gemini Flash |
| `GraphService` | `shared/src/shared_tools/graph_service.py` | Microsoft Graph API (device-code OAuth) |
| `VariationService` | `shared/src/shared_tools/variation_service.py` | Variation workflow: CRUD, import/push, register, email, PDF |
| `VariationAgent` | `shared/src/shared_tools/variation_agent.py` | Multi-modal Gemini analysis for VO requests |
| `VariationDB` | `shared/src/shared_tools/variation_db.py` | Dedicated variations database (projects, VOs, items, templates) |
| `VariationTemplate` | `shared/src/shared_tools/variation_template.py` | Excel template engine: mapping, import, compile |
| `HabitLearnerService` | `shared/src/shared_tools/habit_learner_service.py` | Amy's email reply pattern learning |
| `TodoService` | `shared/src/shared_tools/todo_service.py` | To-Do List with Outlook calendar push |
| `SubcontractorService` | `shared/src/shared_tools/subcontractor_service.py` | **(PLANNED)** Quote → PO → Subcontract → Claims workflow |
| `SubcontractorAgent` | `shared/src/shared_tools/subcontractor_agent.py` | **(PLANNED)** Quote ingestion, tender analysis AI agents |
| `SubcontractorDB` | `shared/src/shared_tools/subcontractor_db.py` | **(PLANNED)** Dedicated subcontractor database (vendors, commitments, claims) |
| `SubcontractorTemplate` | `shared/src/shared_tools/subcontractor_template.py` | **(PLANNED)** PO, Subcontract, Tender Analysis document generators |
| `SubcontractorLearner` | `shared/src/shared_tools/subcontractor_learner.py` | **(PLANNED)** Batch knowledge builder from project subcontract folders |
| `ProgressClaimService` | `shared/src/shared_tools/progress_claim/progress_claim_service.py` | Client progress claims: cashflow import, monthly % grid, claim generation, Excel/PDF export |
| `ProgressClaimDB` | `shared/src/shared_tools/progress_claim/progress_claim_db.py` | Dedicated progress-claim database (projects, work items, months, progress, claims, claim items) |
| `ProgressClaimExcelBuilder` | `shared/src/shared_tools/progress_claim/progress_claim_template.py` | Claim workbook builder (Summary + Detail sheets) |

## Project Structure

```
crewAI_amy/
├── shared/src/shared_tools/   ← reusable services & utilities (the foundation)
│   ├── mail_service.py        ← AMail pipeline service
│   ├── calendar_service.py    ← Calendar service
│   ├── memory_service.py      ← ChromaDB ingestion + RAG search
│   ├── file_registry.py       ← SQLite file tracker
│   ├── pdf_vision_service.py  ← PDF → PNG → Gemini vision
│   ├── graph_service.py       ← Microsoft Graph API client
│   ├── outlook_tool.py        ← Outlook COM wrappers
│   ├── ipc_bridge.py          ← AMail↔ACalendar shared DB
│   ├── variation_db.py        ← Variations dedicated DB
│   ├── variation_service.py   ← Variation workflow engine
│   ├── variation_template.py  ← Excel template engine
│   ├── variation_agent.py     ← Multi-modal VO agent
│   ├── habit_learner_db.py    ← Habit learner DB schema
│   ├── habit_learner_service.py ← Amy's reply pattern learning
│   ├── todo_service.py        ← To-Do List service
│   ├── email_parser.py        ← HTML stripping utilities
│   └── llm_config.py          ← LLM provider routing (get_llm)
├── tools/
│   ├── amail/                 ← AMail (email triage + PyQt6 GUI)
│   ├── acalendar/             ← ACalendar (schedule dashboard)
│   ├── asummary/              ← CLI-only email summarizer (superseded by asummary1)
│   └── asummary1/             ← Email summarizer with PyQt6 GUI
├── ingest.py                  ← Hybrid ingestion CLI
├── chat.py                    ← Grounded RAG chatbot
├── coding_plans/
│   ├── SUBCONTRACTOR_MANAGEMENT_PLAN.md  ← Tier 3 Subcontractor Platform (current focus)
│   ├── VARIATION_WORKFLOW_PLAN.md
│   ├── IMPLEMENTATION_PLAN_HABIT_LEARNER.md
│   ├── CALENDAR_ASSISTANT_PLAN.md
│   ├── AMY_Architecture_and_Roadmap.md
│   └── SERVICE_EXTRACTION_PLAN.md
├── CLAUDE.md                  ← This file
```

## Gotchas

### ONNX embedding crashes
`enable_cpu_mem_arena=True` (default) causes OOM on 90+ batches. Fix: `enable_cpu_mem_arena=False`, `intra_op_num_threads=2`, `inter_op_num_threads=2`. Singleton ONNXMiniLM_L6_V2 instance prevents duplicate InferenceSessions. See `memory_service.py:_get_persistent_embedding_function()`.

### PyQt6 signals in CLI scripts
PyQt signals don't deliver without a running event loop. CLI scripts that use QObject services must: (a) create `QApplication`, (b) call `app.processEvents()` in polling loops, (c) never call `app.exec()`.

### Gemini model names
`gemini-3.1-flash` does NOT exist (404). Use `gemini-3.1-flash-lite` or check the latest valid model names. `MODEL=gemini/gemini-3.1-flash-lite` in `.env`.

### CrewAI LLM pattern
Always use `get_llm(role)` from `shared_tools.llm_config.py`. Roles: `"fast"` (lightweight tasks), `"smart"` (complex reasoning). Provider routing: `AI_PROVIDER=gem` → Gemini, `AI_PROVIDER=ds` → DeepSeek. **Never** construct `LLM()` or `ChatOpenAI()` directly in tools.

### Data storage
All persisted data lives in `<project_root>/data/` (gitignored). Override with `LILAMY_DATA_DIR` env var for production deployments. The `.gitignore` excludes: `data/`, `.chromadb/`, `*.sqlite3`, `.lilamy_registry.db`, `.lilamy_vision_cache/`, `.lilamy_graph_token.json`.

## Testing

```bash
uv run pytest tools/amail/tests/ -v              # 83 pass, 6 pre-existing (test_crew.py)
```

No tests exist yet for: CalendarService, MemoryService, FileRegistry, PDFVisionService, GraphService, ASummary1. This is a known gap.

## Linked Memory

This file works with Claude Code's persistent memory system at `~/.claude/projects/C--crewAI-crewAI-amy/memory/`. Key memories: session recaps, technical decisions (ONNX fix, Gemini model names), and project milestones. Check `MEMORY.md` index on session start for context.
