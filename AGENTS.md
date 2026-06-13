# AGENTS.md — lilAmy Platform

> Compatible with: Claude Code, Cursor, GitHub Copilot, OpenAI Codex, Windsurf, Aider.
> For Claude-specific details, see `CLAUDE.md`.

Python 3.10–3.13, CrewAI 1.14.2, PyQt6, SQLite + ChromaDB, Windows (Outlook COM).

## Quick Commands

```bash
uv sync                                          # Install all dependencies
uv run lilamy                                    # Platform desktop GUI (card UI)
uv run lilamy --web                              # WebUI server (http://127.0.0.1:8765)
uv run lilamy --amail                            # Legacy AMail
uv run amail                                     # Legacy AMail (unchanged)
uv run acalendar                                 # ACalendar GUI
uv run pytest tools/amail/tests/ -v              # Run tests (83 pass)
```

## Architecture

**Service-first pattern — ALWAYS separate core logic from UI.** Every feature starts as a standalone QObject service class in `shared/src/shared_tools/` with `threading.Thread` concurrency and PyQt signals. The UI layer (PyQt6, FastAPI, web) is a thin consumer.

```
UI Layer (thin)  ──calls──▶  Service Class (QObject + signals)
                               owns: threading, state, CrewAI, DB, COM
```

## NEVER

- ❌ Put business logic in a QWidget/QDialog — extract to a service first
- ❌ Create new QThread subclasses — use `threading.Thread` in a QObject service
- ❌ Put `_llm_semaphore` or `queue.Queue` at module level in GUI files — belongs in service
- ❌ Call Outlook COM or SQLite directly from GUI code — route through a service or tool
- ❌ Duplicate a crew.py or YAML config across tools — extract shared logic to `shared_tools/`
- ❌ Store .db, .bin, .pkl, .jsonl, .chromadb/ inside the repo — use `LILAMY_DATA_DIR`
- ❌ Hardcode file paths — always import `CREWAI_DIR` / `DB_PATH` from `ipc_bridge` or read `LILAMY_DATA_DIR`
- ❌ Install new PyPI packages without explicit approval
- ❌ Use `ChatOpenAI(model_name=...)` — always `crewai.LLM` via `get_llm(role)` from `llm_config.py`

## Project Structure

```
crewAI_amy/
├── lilamy/                    ← **NEW** Platform (uv run lilamy)
│   ├── main.py, gui_viewer.py, web_server.py
│   ├── modules/ (registry, amail_routes)
│   └── static/ (SPA: index.html, app.js)
├── shared/src/shared_tools/   ← reusable services
│   ├── mail_service.py        ← Unified pipeline + fetch/sync modes
│   ├── calendar_service.py, memory_service.py, file_registry.py
│   ├── pdf_vision_service.py, graph_service.py
│   ├── outlook_tool.py, ipc_bridge.py, llm_config.py
├── tools/
│   ├── amail/                 ← UnifiedSummarizerCrew + legacy pipeline
│   ├── acalendar/             ← Schedule dashboard
│   ├── asummary/              ← Deprecated CLI → use lilamy
│   └── asummary1/             ← Email summarizer GUI
├── data/                      ← Gitignored: mail_history.db
```

## Services (built, available for reuse)

| Service | File | What it owns |
|---|---|---|
| MailService | `mail_service.py` | Fetch → filter → triage → reply → workflow pipeline |
| CalendarService | `calendar_service.py` | Event CRUD, conflict detection, weekly digest, IPC |
| MemoryService | `memory_service.py` | ChromaDB ingestion, embedding, hybrid search |
| FileRegistry | `file_registry.py` | SQLite file tracking with MD5 change detection |
| PDFVisionService | `pdf_vision_service.py` | PDF → PNG render → Gemini Flash vision description |
| GraphService | `graph_service.py` | Microsoft Graph API device-code OAuth, email classification |

## Data Rules

- **All persisted data goes to `<project_root>/data/`** (gitignored), overridable via `LILAMY_DATA_DIR` env var
- **Never inside the repo.** `.gitignore` excludes: `.chromadb/`, `*.sqlite3`, `.lilamy_*`
- **Hybrid architecture:** SQLite FileRegistry (relational) + ChromaDB (vectors) linked by `file_id` FK
- **LLM routing:** `get_llm(role)` from `llm_config.py` — roles: `"fast"`, `"smart"`. Provider selected by `AI_PROVIDER` env var

## Gotchas

1. **ONNX embedding OOM:** Set `enable_cpu_mem_arena=False` on the InferenceSession or it crashes on 90+ batches. Singleton the ONNXMiniLM_L6_V2 instance.
2. **PyQt signals in CLI:** Requires `QApplication` + `processEvents()` in polling loops. No `app.exec()`.
3. **Gemini model names:** `gemini-3.1-flash` doesn't exist. Use `gemini-3.1-flash-lite`.
4. **Outlook COM:** Windows-only. All COM calls go through `outlook_tool.py` — never call `win32com.client` directly from new code.

## Testing

```bash
uv run pytest tools/amail/tests/ -v    # 83 pass, 6 pre-existing failures
```

Services with no tests yet: CalendarService, MemoryService, FileRegistry, PDFVisionService, GraphService, ASummary1 GUI.

---

## CrewAI Quick Reference (version 1.14.2)

The canonical CrewAI template is at `.venv/Lib/site-packages/crewai/cli/templates/AGENTS.md` or at `https://github.com/crewAIInc/crewAI`. Always check the installed version before writing CrewAI code — the API evolves rapidly.

### CrewAI rules for this project

- **Every crew lives in its own tool directory** with `config/agents.yaml` + `config/tasks.yaml`
- **LLM always via `get_llm(role)`** — never construct `LLM()` or `ChatOpenAI()` directly
- **`# type: ignore[index]`** required on all `self.agents_config["key"]` and `self.tasks_config["key"]` accesses
- **Crew class pattern:** `@CrewBase` decorator, `@agent`/`@task`/`@crew` decorated methods, method names match YAML keys
- **Never** leave commented-out code in crew classes
- **Use `Process.sequential`** unless there's a specific reason to use hierarchical (which requires `manager_llm`)
- **Set `verbose=False`** in production crews (it's for development only)
