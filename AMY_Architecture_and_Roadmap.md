# lilAmy: Agentic RAG Workstation for Construction Administration
## Technical Blueprint & Development Roadmap

**Document Purpose:** This document serves as the master architectural guide and development roadmap for "lilAmy," an enterprise-grade RAG and multi-agent system designed for Construction Contract Administration (CA). It is written for senior software developers, AI engineering agents, and system architects.

---

## 1. Core Architectural Directives (CRITICAL)

To ensure this system reaches commercial-grade reliability and can scale without collapsing under technical debt, developers **MUST** adhere to the following principles:

* **⚠️ DO NOT mix UI with Core Logic:** The front-end (React/Electron/Streamlit) must be completely "dumb." It should only handle rendering and passing user inputs. All logic, RAG retrieval, and agent orchestration must live in an isolated backend exposed via REST/GraphQL/FastAPI APIs.
* **⚠️ ALWAYS extract a Service class first:** Before building any UI, implement the feature as a standalone Python service class in `shared/src/shared_tools/` with public methods, PyQt signals for async results, and internal `threading.Thread` + `queue.Queue` concurrency. The UI layer (PyQt6 or web) must be a thin consumer of the service — never the owner of business logic. **This pattern was established by `MailService` and `CalendarService` (see `SERVICE_EXTRACTION_PLAN.md` for the full refactoring plan).**
* **⚠️ DO NOT use a single generic vector dump:** Dumping all PDFs, emails, and financial data into a single ChromaDB or Pinecone index is strictly prohibited. You must use the Hybrid Relational-Vector architecture defined below.
* **⚠️ DO NOT let LLMs do math:** LLMs are linguistic models, not calculators. All financial forecasting, retention deductions, and claim calculations must be routed to deterministic, hard-coded Python/SQL functions. The LLM only formats the output.
* **⚠️ NEVER store project data in version control:** All storage files — databases (SQLite, ChromaDB, pgvector), embeddings, indexes, fact stores, IPC databases, and configuration caches — MUST be written to a user-specified root directory (`LILAMY_DATA_DIR`), NEVER inside the repository tree. The repo's `.gitignore` must exclude `*.db`, `*.bin`, `*.pkl`, `*.jsonl`, and any embedding/index directories. This is critical because the software is sold to multiple clients — pushing one client's project data into version control is a confidentiality breach and a commercial liability.
* **✅ ALWAYS support a configurable data root:** The system must read a `LILAMY_DATA_DIR` environment variable (or config file) that points to the user's project files root. All ingestion, indexing, and retrieval operates relative to this path. No hardcoded paths. Each client deployment uses its own independent data directory.
* **✅ ALWAYS maintain strict modularity:** If we swap the LLM provider (e.g., OpenAI to Anthropic) or the Vector DB (pgvector to Qdrant) in the future, it should require changing *only one specific wrapper file*. Use abstract base classes and dependency injection.
* **✅ ALWAYS isolate project data:** Security is paramount. Queries must always enforce Row-Level Security (RLS) or strict `project_id` filtering before vector search occurs to prevent cross-project data leakage.

---

## 2. The 4-Layer System Architecture

lilAmy is built on an Event-Driven, Agentic Flow Architecture separated into four independent layers.

### Layer 1: Hybrid Data Repository & Memory (The Foundation)
> **🚨 IMPORTANT NOTE:** The memory architecture outlined below is a **Model Archetype**. Construction companies have deeply entrenched, idiosyncratic file systems. This schema *must* be audited and adapted to match the specific physical directory structures and legacy ERP systems of the customer during onboarding.

* **Relational Core (PostgreSQL):**
    * **Project Index:** `project_id`, metadata, status.
    * **File Registry:** Maps the local directory structure (`file_id`, `path`, `md5_hash` for version control).
    * **Structured Financials:** Schedules of Rates, BoQs, and historical claim integers.
* **Vector Store (pgvector within PostgreSQL):**
    * Stores chunked text linked by Foreign Key to the File Registry.
    * Enforces Hierarchical Indexing (e.g., maintaining parent clause relationships).
* **Profile Configs (Experience DataStorage):**
    * Stored locally as editable `.yaml` or `.json` files.
    * Contains the user's specific writing style, negotiation strictness, and workflow definitions.

### Layer 2: Custom Tool Registry (The "Hands")
Atomic, single-purpose Python functions decorated as callable tools for the agents. Agents never touch the DB directly.
* `query_project_sql(query, project_id)`
* `fetch_unread_amail(date_range)`
* `calculate_progress_claim(item_id, percentage)`

### Layer 3: Agentic Orchestration (The "Brain")
Built on frameworks like LangGraph or CrewAI. This layer manages the workflows using specialized personas:
* *Financial Auditor Agent:* Parses quotes against baseline budgets.
* *Legal Review Agent:* Scans for time-bar violations and scope deviations.
* *Orchestrator:* Routes the user's query to the correct sub-agent or tool.

### Layer 4: User Interface
A decoupled frontend (React, Electron, or Streamlit for prototyping). Contains the chat interface, the 2-panel AMail triage dashboard, and project configuration panels.

---

## 3. Phased Development Schedule

This roadmap prioritizes foundational stability over rapid feature bloat. Do not advance to the next milestone until the previous one achieves 99% retrieval accuracy.

### Milestone 1: The Local SQL-Vector Chatbot (Weeks 1-4)
**Goal:** Prove the foundational database schema and hierarchical RAG capabilities.
1.  **DB Initialization:** Set up local PostgreSQL with `pgvector`. Define schemas.
2.  **Directory Watcher:** Build the Python daemon that monitors local project folders, hashes files, and updates the File Registry.
3.  **Document Pipeline:** Implement text extraction, chunking (with metadata), and embedding into pgvector.
4.  **Intent Router:** Build the query router to dynamically choose between SQL execution (for exact numbers) and Vector Search (for semantic clauses).
5.  **Output:** A local CLI or basic web chatbot that can reliably answer: *"What is the approved rate for concrete on Project Alpha, and what does Clause 4 say about delays?"*

### Milestone 2: AMail Triage & Human-in-the-Loop Pipeline (Weeks 5-7)
**Goal:** Integrate the unstructured email stream safely into the system memory.
1.  **Email Ingestion:** Build the secure IMAP/Graph API connector.
2.  **Noise Filtering:** Strip signatures, footers, and legal disclaimers.
3.  **Triage UI:** Develop the 2-panel interface mapping to `toBeLearntList` and `junkList`.
4.  **JSON State Management:** Implement `mail_learnt.json` to track processed message IDs and prevent duplicate vectorization.

### Milestone 3: Advanced Agentic Workflows (Weeks 8-11)
**Goal:** Transition from passive answering to active task execution.
1.  **Agent Crews:** Instantiate the Legal, Financial, and Orchestrator agents.
2.  **Comparative RAG:** Build the workflow that compares an incoming subcontractor quote against the Main Contract specification to flag scope gaps.
3.  **Drafting Engine:** Implement structured outputs (using Pydantic models) to automatically format Progress Certificates and Variation Responses based on retrieved data.

### Milestone 4: Autonomous Defensive Automations (Weeks 12+)
**Goal:** Transform lilAmy into a predictive commercial guardian.
1.  **Background Schedulers:** Set up cron jobs / Celery workers to run nightly audits.
2.  **Event Triggers:** If an email mentions "rain delay", trigger the Legal Agent to calculate the statutory notice window.
3.  **Proactive Alerts:** Push notifications to the UI with pre-drafted Extension of Time (EOT) letters.

---

## 4. Engineering Standards for Commercial Deployment

* **Pydantic Everywhere:** Use Pydantic to validate all data passing between the UI, the Database, and the LLM tools. This prevents agent hallucination loops.
* **Audit Logging:** Every action taken by an agent, and every document retrieved to form an answer, MUST be logged locally. In construction litigation, the system must be able to prove *why* it made a specific recommendation.
* **Security Context Windows:** Implement Pre-Query Filtering. The LLM must never receive data from a project the user does not have RBAC (Role-Based Access Control) clearance for.
