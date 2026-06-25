# Quote Comparison & Tender Leveling System — Revised Design

**Status:** Planned — not yet implemented  
**Date:** 2026-06-24  
**Replaces:** `coding_plans/QUOTE_COMPARISON.pdf` (evaluated, found architectural mismatches)  
**Integrates with:** Subcontractor Management System (Tier 3), `subcontractor.db`

---

## 1. Evaluation of Original Plan (QUOTE_COMPARISON.pdf)

### What was correct
- Two-agent split: Baseline Harvester (Agent 1) + Quote Risk Auditor (Agent 2) are genuinely distinct tasks
- Pydantic schemas first — `StandardScopeItem`, `QuoteLineItem`, `CommercialTerm`, `ExtractedQuote`
- LLM money guardrail: `qty * rate == total` validated programmatically, never LLM-calculated
- Source citation on every baseline item (CA audit trail)
- Cache-first architecture: store harvested baselines locally
- Trade interface matrix for cross-trade boundaries
- Commercial term risk pattern-matching (COD, latent conditions)

### What needed rework
| Original | Revised |
|---|---|
| PostgreSQL + pgvector | SQLite (extend `subcontractor.db`) |
| `tools/quote_comparison/src/` | `shared/src/shared_tools/quote_comparison/` |
| Standalone functions | QObject service with signals/thread/queue |
| No FastAPI routes or UI | Full vertical slice: DB → Service → Agent → Routes → Frontend |
| `google.generativeai` direct import | `get_llm(role)` from `llm_config.py` |
| Web search as primary source | Seed library → File ingestion → Web search (cascade) |
| Simulated search results | Real PyMuPDF parsing + extensible source adapters |

### Legacy tables removed
- `rate_benchmarks` — replaced by `master_scope_library` (scope, not pricing)
- `competitive_sets` — replaced by quote comparison audit runs

---

## 2. System Architecture

### Two-Agent Design

```
┌──────────────────────────────────────────────────────────────────┐
│                    AGENT 1: Baseline Harvester                     │
│                                                                    │
│  Purpose: Compile "Ground Truth" scope of work for each trade      │
│                                                                    │
│  ┌──────────┐    ┌──────────────┐    ┌──────────┐                │
│  │ Seed     │───▶│ File Ingestion│───▶│ Web      │                │
│  │ Library  │    │ (NATSPEC,    │    │ Search   │                │
│  │ (today)  │    │  AS/NZS PDF) │    │ (fallback)│                │
│  └────┬─────┘    └──────┬───────┘    └────┬─────┘                │
│       │                 │                 │                       │
│       ▼                 ▼                 ▼                       │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │              Source Adapters → SourceChunk[]                  │ │
│  │  Each source produces same intermediate format:               │ │
│  │  text + source_type + source_label + source_url_or_path       │ │
│  └──────────────────────────┬──────────────────────────────────┘ │
│                             │                                     │
│                             ▼                                     │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │              LLM Synthesis (get_llm("fast"))                  │ │
│  │  Chunks → HarvestedTradeBaseline (Pydantic)                   │ │
│  │  Every item has source provenance                             │ │
│  └──────────────────────────┬──────────────────────────────────┘ │
│                             │                                     │
│                             ▼                                     │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │              master_scope_library (SQLite)                    │ │
│  │  source_type: seed | natspec_file | as_nzs_file |             │ │
│  │               web_search | ca_manual                          │ │
│  │  needs_review: bool | source_document_hash: str                │ │
│  └─────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│                AGENT 2: Quote Risk Auditor                         │
│                                                                    │
│  Purpose: Parse subcontractor PDFs, extract line items,            │
│           compare against Agent 1's baseline, flag risks           │
│                                                                    │
│  ┌────────────────┐    ┌──────────────────┐                       │
│  │ Multi-modal    │───▶│ Pydantic          │                       │
│  │ PDF Parser     │    │ Extraction        │                       │
│  │ (Gemini Vision)│    │ → ExtractedQuote  │                       │
│  └────────────────┘    └────────┬─────────┘                       │
│                                 │                                  │
│                                 ▼                                  │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │              Tender Leveling Engine                           │ │
│  │  ExtractedQuote vs HarvestedTradeBaseline → AuditFindings[]  │ │
│  │  • Scope gaps (inclusion present in baseline, missing in     │ │
│  │    quote = OMITTED; explicitly excluded = EXCLUDED)          │ │
│  │  • Rate anomalies (line item rate deviates from market)      │ │
│  │  • Commercial risks (payment terms, latent conditions, etc.) │ │
│  │  • Estimated cost impact per finding                        │ │
│  └─────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. Database Schema (extends `subcontractor.db`)

### New tables to add to `subcontractor_db.py`

```sql
-- 1. Trade Taxonomy
CREATE TABLE IF NOT EXISTS trade_registry (
    trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
    cost_code TEXT UNIQUE NOT NULL,       -- e.g., "0310", "0510"
    trade_name TEXT UNIQUE NOT NULL,      -- e.g., "Structural Concrete"
    created_at TEXT DEFAULT (datetime('now'))
);

-- 2. Master Scope Library (Ground Truth from Agent 1)
CREATE TABLE IF NOT EXISTS master_scope_library (
    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER REFERENCES trade_registry(trade_id) ON DELETE CASCADE,
    work_package_title TEXT NOT NULL,
    mandatory_inclusions JSONB NOT NULL,   -- JSON array of deliverables
    typical_exclusions JSONB,             -- JSON array of common exclusions
    source_citation_name TEXT NOT NULL,   -- e.g., "NATSPEC 0310 Concrete"
    source_citation_url TEXT,             -- URL or file path
    source_type TEXT NOT NULL DEFAULT 'seed',
        -- 'seed' | 'natspec_file' | 'as_nzs_file' | 'web_search' | 'ca_manual'
    source_document_hash TEXT,            -- MD5 of source file, for change detection
    needs_review INTEGER DEFAULT 0,       -- 1 = CA should verify (web-sourced)
    is_customized INTEGER DEFAULT 0,      -- 1 = CA manually edited
    version_date TEXT,                    -- When this baseline was last refreshed
    last_updated TEXT DEFAULT (datetime('now'))
);

-- 3. Trade Interface Matrix (cross-trade boundaries)
CREATE TABLE IF NOT EXISTS trade_interfaces (
    interface_id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER REFERENCES trade_registry(trade_id) ON DELETE CASCADE,
    boundary_trade_name TEXT NOT NULL,     -- e.g., "Electrical"
    interface_description TEXT NOT NULL,   -- e.g., "Power connection to HVAC unit"
    default_responsibility TEXT NOT NULL,  -- e.g., "Mechanical Package"
    created_at TEXT DEFAULT (datetime('now'))
);

-- 4. Quote Audit Runs (Agent 2 results — links to existing quotes table)
CREATE TABLE IF NOT EXISTS quote_audit_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_entry_id TEXT NOT NULL REFERENCES quotes(entry_id) ON DELETE CASCADE,
    baseline_trade_id INTEGER REFERENCES trade_registry(trade_id),
    audit_date TEXT DEFAULT (datetime('now')),
    total_findings INTEGER DEFAULT 0,
    critical_findings INTEGER DEFAULT 0,
    estimated_risk_exposure REAL DEFAULT 0, -- total $ of identified risks
    status TEXT DEFAULT 'pending',          -- 'pending', 'reviewed', 'resolved'
    created_at TEXT DEFAULT (datetime('now'))
);

-- 5. Individual Audit Findings
CREATE TABLE IF NOT EXISTS audit_findings (
    finding_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES quote_audit_runs(run_id) ON DELETE CASCADE,
    finding_type TEXT NOT NULL,
        -- 'SCOPE_GAP' | 'RATE_ANOMALY' | 'COMMERCIAL_RISK' | 'EXCLUSION'
    severity TEXT NOT NULL DEFAULT 'MEDIUM',
        -- 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
    description TEXT NOT NULL,
    baseline_reference TEXT,               -- link to master_scope_library.item_id
    quote_line_reference TEXT,             -- link to quote_items.id
    estimated_cost_impact REAL,            -- $ value of risk exposure
    ca_recommendation TEXT,                -- mitigation advice
    ca_resolution TEXT,                    -- how the CA resolved it
    resolved_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_msl_trade ON master_scope_library(trade_id);
CREATE INDEX IF NOT EXISTS idx_msl_source_type ON master_scope_library(source_type);
CREATE INDEX IF NOT EXISTS idx_audit_quote ON quote_audit_runs(quote_entry_id);
CREATE INDEX IF NOT EXISTS idx_findings_run ON audit_findings(run_id);
CREATE INDEX IF NOT EXISTS idx_findings_severity ON audit_findings(severity);
```

---

## 4. Pydantic Schemas

File: `shared/src/shared_tools/quote_comparison/quote_comparison_schemas.py`

```python
from pydantic import BaseModel, Field, HttpUrl
from typing import List, Optional, Literal
from enum import Enum


# ── Source provenance tracking ────────────────────────

class SourceType(str, Enum):
    SEED = "seed"
    NATSPEC_FILE = "natspec_file"
    ASNZS_FILE = "as_nzs_file"
    WEB_SEARCH = "web_search"
    CA_MANUAL = "ca_manual"


# ── Agent 1: Baseline Standards Models ────────────────

class StandardScopeItem(BaseModel):
    work_package_title: str = Field(
        description="High-level category of work, e.g., 'Formwork', 'Reinforcement'"
    )
    mandatory_inclusions: List[str] = Field(
        description="Granular, unambiguous deliverables the subcontractor MUST price"
    )
    typical_exclusions: List[str] = Field(
        description="Items commonly excluded from this trade package"
    )
    source_citation_name: str = Field(
        description="Standard code or regulation cited, e.g., 'NATSPEC 0310 Concrete'"
    )
    source_url: Optional[str] = Field(
        None, description="Direct URL or file path of the source document"
    )
    source_type: SourceType = Field(
        default=SourceType.SEED,
        description="Provenance of this scope item"
    )

class HarvestedTradeBaseline(BaseModel):
    cost_code: str = Field(description="Industrial classification, e.g., '0310'")
    trade_name: str = Field(description="Standard industry trade name")
    scope_items: List[StandardScopeItem]
    interface_notes: List[str] = Field(
        description="Boundary warnings, e.g., 'Electrical to provide isolator; "
                    "Mechanical to wire from isolator to unit'"
    )


# ── Agent 2: Quote Extraction Models ─────────────────

class QuoteLineItem(BaseModel):
    item_reference: Optional[str] = Field(
        None, description="BoQ or specification reference from the subcontractor"
    )
    description: str = Field(description="Plain text description of the work or material")
    qty: float = Field(description="Quantity. Use 1.0 for lump-sum items.")
    unit: str = Field(description="Unit: m2, m3, lm, kg, item, hr, wk, sum")
    rate: float = Field(description="Unit rate in AUD")
    total: float = Field(description="Line total (= qty × rate, validated programmatically)")
    wbs_code: Optional[str] = Field(None, description="Project cost code / WBS reference")
    is_optional: bool = Field(False, description="Provisional or alternate pricing")
    is_verified: bool = Field(False, description="CA has checked this line item")

class CommercialTerm(BaseModel):
    condition_type: str = Field(
        description="E.g., 'Payment Terms', 'Validity Period', 'Retention', 'Latent Conditions'"
    )
    raw_text: str = Field(
        description="Exact verbatim text from the quote's terms and conditions"
    )
    risk_level: Literal["Low", "Medium", "High", "Critical"] = Field(
        description="Categorized risk level"
    )
    ca_action_required: str = Field(
        description="Explicit mitigation advice for the Contract Administrator"
    )

class ExtractedQuote(BaseModel):
    subcontractor_name: str
    quote_reference_number: str
    total_net_amount: float
    priced_line_items: List[QuoteLineItem]
    explicit_inclusions: List[str] = Field(
        description="Cleaned, normalized list of items the subcontractor states are included"
    )
    explicit_exclusions: List[str] = Field(
        description="Cleaned, normalized list of items the subcontractor explicitly excludes"
    )
    commercial_conditions: List[CommercialTerm]


# ── Agent 2: Audit Output ────────────────────────────

class AuditFinding(BaseModel):
    finding_type: Literal["SCOPE_GAP", "RATE_ANOMALY", "COMMERCIAL_RISK", "EXCLUSION"]
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    description: str
    baseline_reference: Optional[str] = None     # master_scope_library.item_id
    quote_reference: Optional[str] = None         # quote_items.id
    estimated_cost_impact: Optional[float] = None  # quantified risk exposure in AUD
    ca_recommendation: str

class QuoteAuditResult(BaseModel):
    quote_entry_id: str
    baseline_trade_id: int
    audit_date: str
    findings: List[AuditFinding]
    total_risk_exposure: float  # sum of all estimated_cost_impact
    critical_count: int
    summary: str  # one-paragraph executive summary for the CA
```

---

## 5. Agent 1 — Extensible Harvester Architecture

### Source-Adapter Pattern

Each source (seed, NATSPEC file, AS/NZS file, web search) implements the same interface, producing `SourceChunk` objects that feed into the LLM synthesis step.

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SourceChunk:
    """Intermediate format — raw text from any source,
    ready for LLM structuring into StandardScopeItem."""
    text: str
    source_type: str          # 'seed' | 'natspec_file' | 'as_nzs_file' | 'web_search'
    source_label: str         # e.g., "NATSPEC 0310 Clause 3.2"
    source_url_or_path: str   # URL, file path, or seed version ref
    chunk_order: int = 0      # preserve document ordering


class StandardsSource(ABC):
    """Entry point for any standards data source.
    Implement a new subclass for each document type."""

    @abstractmethod
    def fetch(self, trade_name: str, **kwargs) -> list[SourceChunk]:
        """Retrieve raw text chunks for a given trade.
        Returns empty list if this source doesn't cover this trade."""
        ...


# ── Path A: Seed Library (available now) ─────────────

class SeedLibrarySource(StandardsSource):
    """Reads from hand-curated trade baselines.
    Lives in: shared/src/shared_tools/quote_comparison/seed_baselines.py"""

    def fetch(self, trade_name: str, **kwargs) -> list[SourceChunk]:
        ...


# ── Path B1: NATSPEC Worksection Files ───────────────

class NATSPECFileSource(StandardsSource):
    """Parses NATSPEC worksection DOCX or PDF files.

    NATSPEC structure (consistent across all worksections):
      - Header: worksection number + title
      - Part 1: General (standards referenced, interpretations)
      - Part 2: Products (materials, quality)
      - Part 3: Execution (subcontractor deliverables — THE GOLD)
      - Each clause has number + title + requirement text
      - 'SELECTIONS' and 'DATA' blocks are template variables

    Entry point: drop files into {LILAMY_DATA_DIR}/natspec_worksections/
    or upload via WebUI."""

    def fetch(self, trade_name: str, **kwargs) -> list[SourceChunk]:
        """Find matching worksection file, parse, chunk by clause.
        PDF → text via PyMuPDF. DOCX → text via python-docx or similar."""
        ...

    def _find_worksection_file(self, trade_name: str) -> str | None:
        """Search NATSPEC_WORKSECTION_DIR for files matching trade name.
        e.g., '0310*.pdf', '0310*.docx', 'Concrete*.pdf'"""
        ...

    def _parse_natspec(self, filepath: str) -> list:
        """Extract structured clauses from NATSPEC worksection file."""
        ...


# ── Path B2: Australian Standards (AS/NZS) Files ─────

class ASNZSFileSource(StandardsSource):
    """Parses Australian/New Zealand Standards PDF files.

    AS/NZS structure:
      - Clause-based: Section 1, 1.1, 1.1.1, etc.
      - 'Shall' = mandatory requirement
      - 'Should' = recommendation
      - Normative vs Informative appendices
      - Often references other AS/NZS standards

    Entry point: drop PDFs into {LILAMY_DATA_DIR}/australian_standards/
    or upload via WebUI."""

    # Mapping: cost_code → relevant Australian Standards
    TRADE_TO_STANDARDS: dict[str, list[str]] = {
        "0310": ["AS 3600", "AS 3610", "AS 3799"],   # Concrete + Formwork
        "0510": ["AS 4100", "AS/NZS 5131"],           # Structural Steel
        # ... populated as standards become available
    }

    def fetch(self, trade_name: str, **kwargs) -> list[SourceChunk]:
        """Find mapped standard(s), parse, extract requirements."""
        ...

    def _parse_as_nzs(self, filepath: str) -> list:
        """Extract clauses from AS/NZS PDF.
        Scanned/image-only PDFs need OCR (Gemini vision fallback)."""
        ...


# ── Path C: Web Search (fallback only) ───────────────

class WebSearchSource(StandardsSource):
    """Searches authoritative domains when no local files/seed match.
    Only fires when both seed AND file sources return empty."""

    def fetch(self, trade_name: str, **kwargs) -> list[SourceChunk]:
        ...
```

### Harvester Orchestrator

```python
class BaselineHarvesterAgent:
    """Orchestrates all sources in priority order,
    feeds chunks through LLM for structured extraction."""

    def __init__(self):
        self._sources: list[StandardsSource] = [
            SeedLibrarySource(),       # 1. Fast, curated, reviewed
            NATSPECFileSource(),       # 2. Actual NATSPEC docs (when available)
            ASNZSFileSource(),         # 3. Actual AS/NZS docs (when available)
            WebSearchSource(),         # 4. Fallback (nothing else covers)
        ]

    def harvest(self, trade_name: str, cost_code: str, *,
                force_refresh: bool = False) -> HarvestedTradeBaseline:
        """Main entry point.

        Cascade logic:
        1. Check DB cache (unless force_refresh)
        2. Collect chunks from sources in priority order
        3. If file-based authoritative source found, skip web search
        4. LLM synthesis → HarvestedTradeBaseline
        5. Cache to master_scope_library
        """
        ...
```

### File Drop Zone Structure

```
{LILAMY_DATA_DIR}/
├── natspec_worksections/        ← Drop .pdf / .docx files here
│   ├── 0310_Concrete.pdf
│   ├── 0510_Structural_Steel.pdf
│   └── ...
├── australian_standards/        ← Drop AS/NZS PDFs here
│   ├── AS_3600_Concrete_Structures.pdf
│   ├── AS_4100_Steel_Structures.pdf
│   └── ...
└── subcontractor.db             ← Master scope library + audit tables live here
```

---

## 6. Agent 2 — Quote Audit Engine

### Workflow

```
1. User uploads subcontractor quote PDF (or selects from quotes table)
2. Multi-modal PDF → text (PyMuPDF for text-layer PDFs, Gemini Vision for scanned)
3. LLM extraction → ExtractedQuote Pydantic (with programmatic total validation)
4. Load HarvestedTradeBaseline for matching trade from master_scope_library
5. TenderLevelingEngine.audit_scope_gaps() — rule-based comparison
6. TenderLevelingEngine.audit_commercial_terms() — rule-based risk detection
7. TenderLevelingEngine.audit_rates() — optional: flag rate outliers vs market
8. Compile AuditFindings[] → quote_audit_runs + audit_findings tables
9. Return QuoteAuditResult to UI for CA review
```

### Deterministic Audit Rules (LLM Only for Extraction)

| Rule | Trigger | Severity | Action |
|---|---|---|---|
| Mandatory inclusion explicitly excluded | Exclusion text matches baseline inclusion | CRITICAL | Flag — scope gap, must clarify with subcontractor |
| Mandatory inclusion omitted | Inclusion not found in priced items or stated inclusions | HIGH | Flag — potential hidden cost |
| Payment terms < 30 days EOM | "COD", "immediate", "7 days", "14 days" | HIGH | Flag — cash flow risk, negotiate 30-day EOM |
| Latent conditions uncapped | "subject to site conditions", "latent conditions" without dollar limit | MEDIUM | Flag — uncapped exposure |
| Retention deviation | Retention % differs from head contract (default 5%) | MEDIUM | Flag — alignment required |
| Validity period expired | Quote validity date < today | MEDIUM | Flag — reconfirm pricing |
| Rate > 2σ from median | Line item rate exceeds 2 standard deviations from rate_benchmarks | LOW | Flag — investigate, may be justified by scope |

---

## 7. File Structure (Planned)

```
shared/src/shared_tools/quote_comparison/
├── __init__.py
├── quote_comparison_db.py           # Extends subcontractor.db:
│                                    #   trade_registry, master_scope_library,
│                                    #   trade_interfaces, quote_audit_runs,
│                                    #   audit_findings
├── quote_comparison_service.py      # QuoteComparisonService(QObject)
│                                    #   signals: baseline_harvested, quote_audited,
│                                    #            comparison_complete, error_occurred
├── quote_comparison_agent.py        # BaselineHarvesterAgent + QuoteAuditAgent
├── quote_comparison_schemas.py      # All Pydantic models
├── quote_comparison_sources.py      # StandardsSource ABC + implementations
│                                    #   SeedLibrarySource, NATSPECFileSource,
│                                    #   ASNZSFileSource, WebSearchSource
└── seed_baselines.py                # Curated trade baselines (version-controlled)

lilamy/modules/
├── quote_comparison_routes.py       # CRUD + comparison endpoints
└── quote_comparison_agent_routes.py # POST /analyze-quote, POST /harvest-baseline,
                                    #   POST /upload-natspec, POST /upload-standard

lilamy/static/
├── quote_comparison.html
└── quote_comparison.js
```

---

## 8. Implementation Phases

### Phase 1: Foundation (DB + Schemas)
- Add tables to `subcontractor_db.py`
- Implement Pydantic schemas in `quote_comparison_schemas.py`
- Write `seed_baselines.py` for top 10 trades

### Phase 2: Agent 1 — Baseline Harvester
- Implement `StandardsSource` ABC + `SeedLibrarySource`
- Build `BaselineHarvesterAgent` with cascade logic
- Add `NATSPECFileSource` + `ASNZSFileSource` stubs (ready for files)
- Add `WebSearchSource` as fallback

### Phase 3: Agent 2 — Quote Auditor
- Implement multi-modal PDF extraction → `ExtractedQuote`
- Build `TenderLevelingEngine` with deterministic audit rules
- Wire Agent 1 + Agent 2 together (baseline → audit)

### Phase 4: Service Layer + WebUI
- Build `QuoteComparisonService(QObject)` following service pattern
- Create FastAPI routes
- Build frontend: quote upload, audit dashboard, baseline management

### Phase 5: File Ingestion (when documents are available)
- Activate `NATSPECFileSource` + `ASNZSFileSource` with real files
- File change detection (hash-based) for auto-refresh
- WebUI upload endpoints

---

## 9. Guardrails

- **Never let LLMs calculate money** — `qty * rate == total` validated programmatically, deviation triggers warning
- **Always show source provenance** — every baseline item displays its `source_type` and citation in the UI
- **`needs_review` flag** — web-sourced baselines require CA approval before use in audits
- **Use `get_llm(role)`** — never construct LLM providers directly
- **Deterministic rules first** — LLM used for extraction only; comparison logic is rule-based
- **All data in `LILAMY_DATA_DIR`** — database, uploaded documents, cache
- **Seed library in repo** — `seed_baselines.py` is version-controlled reference data (not user data)
- **No new packages** without explicit approval
