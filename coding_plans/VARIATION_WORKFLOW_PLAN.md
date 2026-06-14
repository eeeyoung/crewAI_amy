# Client Variation Workflow — Architecture & Implementation Plan

## Context

Amy currently manages construction client variations through a manual 8-step process: open Excel template → fill register → fill breakdown sheet → update register → export PDF → save to OneDrive → issue via Procore + email → save to OneDrive. The Excel template (`knowledge/drafted simple workflow/20260602 47CBR - Welink Construction Client Variations.xlsx`) has 17 sheets (VO1–VO14, VOXX blank, Register, Internal VO Register) with complex merged cells, formulas, and varying row layouts.

This plan automates the workflow as a new lilAmy platform module, following the project's service-first pattern and integrating with existing AMail + To-Do modules.

**Intended outcome:** Amy can create a variation from scratch or from an incoming email, fill details in a WebUI wizard, auto-generate the Excel with correct formulas and the PDF for submission, and issue it to the client — all without manually touching Excel.

---

## 1. Architecture Overview

Follows the established 3-layer lilAmy pattern:

```
┌──────────────────────────────────────────────────┐
│  WebUI (variation wizard in SPA)                  │  vanilla JS + Tailwind
│  - Variation list (left panel)                    │
│  - Step wizard (right panel)                      │
└──────────────────┬───────────────────────────────┘
                   │ REST (FastAPI)
┌──────────────────▼───────────────────────────────┐
│  lilamy/modules/variation_routes.py               │  CRUD + generate + issue
│  - /api/variations/*                              │
└──────────────────┬───────────────────────────────┘
                   │ Python method calls
┌──────────────────▼───────────────────────────────┐
│  VariationService (QObject)                       │  shared/src/shared_tools/
│  - CRUD variations + items                        │
│  - Excel generation (openpyxl)                    │
│  - PDF export                                     │
│  - Template mapping engine                        │
│  - Submission email generation (LLM)              │
└──────────────────┬───────────────────────────────┘
                   │
    ┌──────────────┼──────────────┐
    ▼              ▼              ▼
┌─────────┐  ┌──────────┐  ┌──────────┐
│ ipc_    │  │ outlook_  │  │ llm_     │
│ bridge  │  │ tool     │  │ config   │
│ (DB)    │  │ (email)  │  │ (LLM)    │
└─────────┘  └──────────┘  └──────────┘
```

### Design Principles (from CLAUDE.md)

- **Service-first**: All business logic in `VariationService` — never in routes or UI
- **No new packages**: Uses only `openpyxl` (already available) + existing `requests`
- **Data outside repo**: All Excel/PDF outputs to `LILAMY_DATA_DIR`
- **LLM for language only**: LLM drafts submission emails and parses variation details from incoming emails. Math (costs, GST, margins) is deterministic Python. Per CLAUDE.md: "DO NOT let LLMs do math."
- **`threading.Thread` + `queue.Queue`**: For async Excel generation and email operations
- **Flexible by design**: Template mappings are config-driven, not hardcoded

---

## 2. Data Model

### 2.1 New Tables in `mail_history.db`

Added via `ipc_bridge.py`:

```sql
-- Master variation record (mirrors the Register sheet)
CREATE TABLE IF NOT EXISTS variations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id TEXT UNIQUE NOT NULL,          -- UUID
    project_name TEXT NOT NULL,
    project_location TEXT,
    job_number TEXT,
    base_contract_amount REAL DEFAULT 0,
    vo_number INTEGER,                       -- VO number (1, 2, 3...)
    vo_title TEXT,                           -- e.g., "VO1 - Client's Tree Removal"
    vo_type TEXT DEFAULT 'Head Contract VO', -- Head Contract VO | Client Direct VO
    is_estimate INTEGER DEFAULT 0,           -- 0=final, 1=estimate
    date_issued TEXT,                        -- ISO date
    site_instruction_ref TEXT,
    status TEXT DEFAULT 'draft',             -- draft|excel_generated|pdf_exported|submitted|approved|void|rejected
    source_email_entry_id TEXT,              -- NULL if manually created
    excel_path TEXT,                         -- path to generated Excel file
    pdf_path TEXT,                           -- path to generated PDF
    onedrive_path TEXT,                      -- OneDrive folder path
    submitted_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Line items for each variation (mirrors the breakdown sheet rows)
CREATE TABLE IF NOT EXISTS variation_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    variation_entry_id TEXT NOT NULL REFERENCES variations(entry_id),
    item_number INTEGER NOT NULL,
    description TEXT,
    qty REAL DEFAULT 0,
    unit TEXT DEFAULT 'item',
    rate REAL DEFAULT 0,
    cost REAL DEFAULT 0,                     -- qty * rate
    credit REAL DEFAULT 0,
    sort_order INTEGER DEFAULT 0
);

-- Template mappings (per-project Excel template configuration)
CREATE TABLE IF NOT EXISTS variation_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name TEXT,
    template_path TEXT,                      -- path to master template .xlsx file
    mapping_json TEXT,                       -- JSON: logical field → cell coordinate
    is_default INTEGER DEFAULT 0
);
```

### 2.2 Template Mapping Config

A JSON/YAML mapping that decouples the code from specific cell positions. Stored in `variation_templates.mapping_json` or as a standalone YAML file in `knowledge/`.

Default mapping (matches Welink template):

```yaml
# knowledge/variation_template_mapping.yaml
template:
  blank_sheet: "VOXX"
  register_sheet: "Register"

vo_sheet:
  title_cell: "A4"                # "CONTRACT VARIATION" or "- ESTIMATE" suffix
  project_name: "B6"
  date_issued: "G6"
  company_name: "B7"
  site_instruction_ref: "F7"
  site_address: "B8"
  job_number: "B9"
  vo_title: "B11"                 # "VO1 - Title of Variation"
  items_header_row: 12            # "Item | Description | Qty | Unit | Rate | Cost | Credit"
  items_start_row: 14
  items_end_row: 16               # max line items before totals (3 rows by default)
  items_columns:
    item: "A"
    description: "B"
    qty: "C"
    unit: "D"
    rate: "E"
    cost: "F"
    credit: "G"
  totals_section:
    sub_total: {row: null, col: "F"}       # null = auto-detect from "SUB TOTAL" label
    nett_cost: {row: null, col: "F"}
    margin: {row: null, col: "F"}
    excl_gst: {row: null, col: "F"}
    gst: {row: null, col: "F"}
    total_incl_gst: {row: null, col: "F"}
  raised_by: "F27"                # "Variation Raised By:" → value at F27
  acceptance_row: 29

register_sheet:
  title_row: 1
  job_number: "B3"
  base_contract_amount: "G3"
  project_name: "B4"
  project_location: "B5"
  vo_table_start_row: 12          # header row
  vo_table_first_data_row: 13
  vo_table_columns:
    vo_number: "A"
    description: "B"
    date_issued: "C"
    variation_value: "D"
    pending_approval: "E"
    not_approved: "F"
    total_approved: "G"
    not_proceeding: "H"
    in_dispute: "I"
    nod_eot: "J"
    status: "K"
    notes: "L"
    action: "M"
```

### 2.3 Calculated Fields (Deterministic, NOT LLM)

```python
def calculate_variation_costs(items: list[dict]) -> dict:
    """Pure Python math — no LLM involved."""
    for item in items:
        item["cost"] = item["qty"] * item["rate"]
        item["credit"] = item.get("credit", 0)

    sub_total = sum(i["cost"] for i in items)
    total_credits = sum(i.get("credit", 0) for i in items)
    nett = sub_total - total_credits
    margin = round(nett * 0.10, 2)      # 10% margin & overhead
    excl_gst = nett + margin
    gst = round(excl_gst * 0.10, 2)     # 10% GST
    total = excl_gst + gst

    return {
        "sub_total": sub_total,
        "credits": total_credits,
        "nett_variation_cost": nett,
        "margin": margin,
        "excl_gst": excl_gst,
        "gst": gst,
        "total_incl_gst": round(total, 2),
    }
```

---

## 3. VariationService Design

**File:** `shared/src/shared_tools/variation_service.py`

Follows the exact service pattern from `CLAUDE.md`: `QObject` + `pyqtSignal` + `threading.Thread` + `queue.Queue`.

### Signals

```python
class VariationService(QObject):
    variation_created = pyqtSignal(str)       # entry_id
    variation_updated = pyqtSignal(str)       # entry_id
    excel_generated = pyqtSignal(str, str)    # entry_id, file_path
    pdf_exported = pyqtSignal(str, str)       # entry_id, file_path
    email_sent = pyqtSignal(str)              # entry_id
    error_occurred = pyqtSignal(str, str)     # entry_id, error_message
    progress_update = pyqtSignal(int, str)    # percentage, description
```

### Public Methods

| Method | Returns | Purpose |
|---|---|---|
| `start()` | None | Initialize DB, start worker thread |
| `stop()` | None | Poison pill worker thread |
| `create_variation(data)` | `str` | Create new variation, return entry_id |
| `update_variation(entry_id, **fields)` | `bool` | Update variation fields |
| `delete_variation(entry_id)` | `bool` | Soft-delete |
| `get_variation(entry_id)` | `dict` | Full variation with items |
| `list_variations(project=None, status=None)` | `list[dict]` | Filtered list |
| `add_item(variation_id, item_data)` | `int` | Add line item |
| `update_item(item_id, **fields)` | `bool` | Update line item |
| `remove_item(item_id)` | `bool` | Delete line item |
| `reorder_items(variation_id, item_ids)` | `bool` | Reorder line items |
| `calculate_costs(variation_id)` | `dict` | Run deterministic cost math |
| `generate_excel(entry_id)` | `str` | Generate .xlsx from template + data → signal `excel_generated` |
| `export_pdf(entry_id)` | `str` | Print Excel to PDF → signal `pdf_exported` |
| `generate_submission_email(entry_id)` | `str` | LLM-generated email draft |
| `send_submission(entry_id, to, cc, subject, body)` | `bool` | Send via Outlook |
| `create_from_email(email_entry_id)` | `str` | Parse email → pre-fill variation fields (LLM-assisted) |
| `get_template_mapping(project_name)` | `dict` | Load template mapping config |
| `update_register_sheet(entry_id)` | None | Sync VO sheet data back to Register sheet |
| `get_next_vo_number(project_name)` | `int` | Auto-increment VO number for project |

### Worker Thread Pattern

```python
def _run_loop(self):
    while self._running:
        try:
            task = self._work_queue.get(timeout=0.5)
            if task is None:
                break
            action, kwargs = task
            handler = getattr(self, f"_handle_{action}", None)
            if handler:
                handler(**kwargs)
        except queue.Empty:
            continue
```

Long-running operations (Excel generation, PDF export, email sending) are dispatched to the worker thread so the UI stays responsive.

---

## 4. Excel Template Engine

**File:** `shared/src/shared_tools/variation_template.py`

### Design: Config-Driven Cell Mapping

Instead of hardcoding cell positions, a `TemplateMapping` class reads a YAML/JSON config that maps logical fields to Excel coordinates. This makes the system adaptable to different construction companies' templates without code changes.

```python
@dataclass
class TemplateMapping:
    """Reads template mapping config and provides typed accessors."""
    config: dict  # parsed YAML/JSON

    def vo_cell(self, field: str) -> str:
        """e.g., 'project_name' → 'B6'"""

    def vo_items_range(self) -> tuple[int, int, dict]:
        """Returns (start_row, end_row, {col_name: col_letter})"""

    def register_cell(self, vo_number: int, field: str) -> str:
        """Maps a VO row + field to a cell coordinate."""

class VariationExcelBuilder:
    """Opens the template, fills cells, writes formulas, saves."""

    def __init__(self, mapping: TemplateMapping, template_path: Path):
        ...

    def fill_project_info(self, ws, variation: dict):
        """Fill project name, job #, site address, date, VO title."""

    def fill_line_items(self, ws, items: list[dict]):
        """Fill item rows with description, qty, unit, rate, cost, credit."""

    def fill_formulas(self, ws, item_count: int):
        """Write Cost=Qty*Rate, SUM, margin, GST, total formulas to correct rows."""

    def fill_register(self, ws, variations: list[dict]):
        """Update the Register sheet with all VOs."""

    def save(self, output_path: Path):
        """Save the modified workbook."""
```

### Formula Handling

The formulas adapt to the actual number of line items. For example, with 3 items in rows 14-16:

```
F14 = C14*E14          (qty × rate)
F18 = SUM(F14:F16)     (sub total — row shifts based on item count)
F19 = F18-G18          (nett = sub total - credits)
F20 = F19*0.1          (10% margin)
F21 = F19+F20          (excl GST)
F22 = F21*0.1          (GST)
F23 = F21+F22          (total incl GST)
```

The `fill_formulas()` method calculates the correct row numbers dynamically based on how many items were written.

---

## 5. REST API

**File:** `lilamy/modules/variation_routes.py`

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/variations` | List variations (filter: `?project=X&status=Y`) |
| GET | `/api/variations/{entry_id}` | Full variation + items |
| POST | `/api/variations` | Create new variation (manual or `?from_email=ID`) |
| PATCH | `/api/variations/{entry_id}` | Update variation fields |
| DELETE | `/api/variations/{entry_id}` | Soft-delete |
| POST | `/api/variations/{entry_id}/items` | Add line item |
| PATCH | `/api/variations/{entry_id}/items/{item_id}` | Update line item |
| DELETE | `/api/variations/{entry_id}/items/{item_id}` | Remove line item |
| PUT | `/api/variations/{entry_id}/items/reorder` | Reorder items |
| POST | `/api/variations/{entry_id}/calculate` | Run cost calculations |
| POST | `/api/variations/{entry_id}/generate-excel` | Generate .xlsx |
| POST | `/api/variations/{entry_id}/export-pdf` | Export PDF |
| GET | `/api/variations/{entry_id}/download/:type` | Download Excel/PDF file |
| POST | `/api/variations/{entry_id}/generate-email` | LLM: generate submission email draft |
| POST | `/api/variations/{entry_id}/send` | Send submission email via Outlook |
| GET | `/api/variations/next-vo-number` | Get next VO# for a project |
| GET | `/api/variations/template-fields` | Get template mapping for a project |

Follows the same pattern as `amail_routes.py` and `todo_routes.py`:
- Lazy-init singleton `VariationService`
- Pydantic models for request/response validation
- No business logic in routes — pure delegation to service

---

## 6. WebUI Design

### 6.1 Module Registration

```python
# lilamy/modules/registry.py — add to MODULES dict
"variations": {
    "id": "variations",
    "name": "Variations",
    "icon": "📝",
    "description": "Client variation workflow — Excel, PDF, submission",
    "enabled": True,
    "router_path": "lilamy.modules.variation_routes:router",
},
```

### 6.2 Frontend Components

Integrated into the existing SPA (`index.html` + `app.js`), following the same card-panel + detail-panel layout:

**Left Panel: Variation Cards**
- Each card shows: VO#, title, project, date, status badge, total value
- Color-coded status: draft (gray), submitted (blue), approved (green), void (red), rejected (yellow)
- Filter by project, status
- "New Variation" button at top
- Right-click context menu: "Create from Email..." (opens email picker), Delete

**Right Panel: Step Wizard**

```
┌─────────────────────────────────────────────────────┐
│  VO1 — Client's Tree Removal          [Draft ▼]     │
├─────────────────────────────────────────────────────┤
│  Step 1/5: Project Setup                    ✓ Done  │
│  ┌───────────────────────────────────────────────┐  │
│  │ Project: Ferguson Residence                    │  │
│  │ Job #: 2596        Location: 22-24 Hood St    │  │
│  │ Base Contract: $1,300,000                     │  │
│  │ VO Type: Head Contract VO ⬤ Final             │  │
│  └───────────────────────────────────────────────┘  │
│                                                     │
│  Step 2/5: Line Items                   ● Current  │
│  ┌───────────────────────────────────────────────┐  │
│  │ #  Description              Qty  Unit  Rate   │  │
│  │ 1  Remove arborist, plant   1    item  $1850  │  │
│  │ 2  _______________       [___] [___] [____]   │  │
│  │ [+ Add Item]                                  │  │
│  │                                               │  │
│  │ Sub Total:    $1,850.00                       │  │
│  │ Margin (10%):   $185.00                       │  │
│  │ Excl GST:     $2,035.00                       │  │
│  │ GST (10%):      $203.50                       │  │
│  │ TOTAL:        $2,238.50                       │  │
│  └───────────────────────────────────────────────┘  │
│                                                     │
│  Step 3/5: Register Preview                         │
│  Step 4/5: Export                                   │
│  Step 5/5: Issue                                    │
│                                                     │
│  [← Back]  [Next →]  [Save Draft]                   │
└─────────────────────────────────────────────────────┘
```

### 6.3 Key Interactions

- **Real-time cost calculation**: When user edits qty/rate, costs auto-update via `/api/variations/{id}/calculate` (no LLM)
- **Auto-save**: Debounced saves to backend after each field change
- **Step navigation**: Can go back/forward freely, wizard validates required fields before proceeding
- **Create from Email**: Opens a modal listing variation-related emails from AMail. Selecting one calls `create_from_email()` which uses LLM to pre-fill VO details from the email body
- **Download buttons**: After Excel/PDF generation, download links appear with file preview
- **Submission email preview**: Editable textarea pre-filled by LLM, with recipient auto-detected from email history

---

## 7. Integration Touchpoints

### 7.1 AMail → Variations

- Right-click an email in AMail card list → "Create Variation" (new context menu item)
- Reads `source_email_entry_id` → calls `create_from_email()` which uses LLM to parse variation details from email body
- Pre-fills project info, description, and cost estimates

### 7.2 Variations → To-Do List

- When a variation is submitted, auto-create a todo item: "Follow up on VO# — awaiting client approval"
- Links back to variation for context

### 7.3 Email Sending

- Uses `outlook_tool.OutlookSendTool` for sending submission emails
- Template: "Dear Team, Please find attached Variation Submission VO{number} for {description}. ..."
- PDF automatically attached

### 7.4 OneDrive/Procore (Future — Phase D)

- These are external integrations that vary by deployment
- The service emits a signal `variation_ready_for_issue` that an optional plugin can consume
- Core workflow is complete without them — Excel + PDF + email covers 80% of the value

---

## 8. Implementation Phases

### Phase A: Core Engine (data + template + service)

| # | Task | File | Effort |
|---|---|---|---|
| A1 | Add `variations` + `variation_items` + `variation_templates` tables to DB | `ipc_bridge.py` | ~45 min |
| A2 | Create `variation_template.py` — TemplateMapping + VariationExcelBuilder | New file | ~2 hr |
| A3 | Create default template mapping YAML | `knowledge/variation_template_mapping.yaml` | ~30 min |
| A4 | Create `VariationService` class (QObject + signals + threading) | New file | ~3 hr |
| A5 | Wire up deterministic cost calculations | In `variation_service.py` | ~30 min |
| A6 | Unit tests for cost math, Excel generation, template mapping | `tests/test_variation.py` | ~1.5 hr |

### Phase B: REST API + WebUI Backend

| # | Task | File | Effort |
|---|---|---|---|
| B1 | Create `variation_routes.py` — all endpoints | New file | ~2 hr |
| B2 | Register in `lilamy/modules/registry.py` | Edit existing | ~5 min |
| B3 | Add Pydantic schemas for request/response | In `variation_routes.py` | ~30 min |

### Phase C: WebUI Frontend

| # | Task | File | Effort |
|---|---|---|---|
| C1 | Add variation wizard HTML to index.html | Edit existing | ~1.5 hr |
| C2 | Add variation JS logic (list, wizard, step nav, live calc) | `app.js` (or new `variations.js`) | ~2.5 hr |
| C3 | Add "Create Variation" context menu item in AMail cards | `app.js` | ~30 min |
| C4 | Wire up auto-save, download buttons, email preview | `app.js` | ~1 hr |

### Phase D: Integration & Polish (Future)

| # | Task | Effort |
|---|---|---|
| D1 | LLM-assisted email parsing (`create_from_email`) | ~1 hr |
| D2 | OneDrive path resolution + file save | ~1.5 hr |
| D3 | Procore Correspondence Tool integration (if API available) | Unknown |
| D4 | Variation → To-Do auto-creation | ~30 min |

### Dependency Graph

```
A1 → A2 → A4 → A5 → B1 → C1
         A3 ─┘         ↘
                  B2 → C2 → C3 → C4
```

---

## 9. Flexibility Design

### 9.1 Template-Agnostic

The template mapping YAML decouples cell positions from code. When a different construction company uses lilAmy with their own Excel template, they only need to:
1. Copy their template file to `knowledge/`
2. Create a new mapping YAML (or update the existing one)
3. Set the template path + mapping in the `variation_templates` table

No code changes needed.

### 9.2 Optional Integrations

Each external touchpoint (OneDrive, Procore, email send) is behind a feature flag checked at runtime:

```python
def _can_use_procore(self) -> bool:
    return bool(os.environ.get("PROCORE_API_URL"))

def _can_use_onedrive(self) -> bool:
    return bool(os.environ.get("ONEDRIVE_ROOT"))
```

The core workflow (create variation → fill items → generate Excel → export PDF) works with zero external dependencies.

### 9.3 Extensible Line Items

The `variation_items` table supports unlimited line items per variation. The Excel builder's `fill_formulas()` dynamically adjusts row positions based on actual item count.

### 9.4 Single Project First, Multi-Project Ready

The initial release targets a single project (e.g., "Ferguson Residence"). The data model includes `project_name` fields from the start so multi-project support is a data change, not a schema migration. `get_next_vo_number()` is scoped to the active project but defaults to a single project initially.

---

## 10. Scope Decisions (Resolved)

| Decision | Choice | Rationale |
|---|---|---|
| OneDrive integration | **Phase D (future)** | Local file generation to `LILAMY_DATA_DIR` covers the core workflow |
| Procore integration | **Phase D (future)** | Excel + PDF + email captures 80%+ of value without Procore API |
| Template approach | **Cleaned-up master** | Create a fixed template with working formulas (repair `#REF!` errors in VOXX) |
| Multi-project | **Single project first** | `project_name` columns exist from day 1; multi-project is data-level, not code-level |
| Internal VO Register | **Deferred** | Focus on main Register sheet; Internal VO Register has different columns and can be added later |

---

## 11. Verification Plan

### Manual Testing

```bash
# 1. Start the WebUI
uv run lilamy --web

# 2. Open http://127.0.0.1:8765 → switch to "Variations" module

# 3. Create a new variation:
#    - Fill project info → Save
#    - Add 3 line items → verify live cost calculation
#    - Generate Excel → verify formulas, merged cells, layout
#    - Export PDF → verify landscape orientation
#    - Generate submission email → verify content
#    - Send email (if Outlook available)

# 4. Create variation from email:
#    - In AMail, right-click email → "Create Variation"
#    - Verify LLM pre-fills variation fields from email content

# 5. Verify Register sheet updates correctly after VO changes
```

### Automated Testing

```bash
uv run pytest tests/test_variation.py -v
```

Tests cover:
- `calculate_variation_costs()` — edge cases (zero items, negative rates, credits)
- `TemplateMapping` — read mapping, resolve cell coordinates
- `VariationExcelBuilder` — open template, fill fields, write formulas, save
- `VariationService.create_variation()` — CRUD operations
- `get_next_vo_number()` — auto-increment per project
- REST API endpoint responses (with mocked service)

---

## 12. Key Files Summary

| File | Action | Purpose |
|---|---|---|
| `shared/src/shared_tools/variation_service.py` | **NEW** | VariationService QObject — all business logic |
| `shared/src/shared_tools/variation_template.py` | **NEW** | TemplateMapping + VariationExcelBuilder |
| `shared/src/shared_tools/ipc_bridge.py` | MODIFY | Add 3 tables + CRUD functions |
| `knowledge/variation_template_mapping.yaml` | **NEW** | Default template cell mapping config |
| `lilamy/modules/variation_routes.py` | **NEW** | REST API endpoints |
| `lilamy/modules/registry.py` | MODIFY | Register "variations" module |
| `lilamy/static/index.html` | MODIFY | Add variation wizard HTML |
| `lilamy/static/app.js` | MODIFY | Add variation JS logic + context menu |
| `tests/test_variation.py` | **NEW** | Unit + integration tests |

---

