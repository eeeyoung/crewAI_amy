# Subcontractor Management System — Architecture & Plan

**Project:** ARCO — 22-24 Hood Street, Subiaco WA 6008 (12-storey mixed-use, $18.45M)
**Builder:** Welink Construction Pty Ltd | **Contract:** AS 4000-1997 Lump Sum
**Target:** Tier 3 — Intelligent Subcontractor Platform with AI Agents
**Date:** 2026-06-16

---

## 0. What a Senior Contract Administrator Actually Does

Before designing the system, we must understand the job. From industry research, a Senior CA's subcontractor responsibilities break into 8 workstreams:

### 0.1 Procurement Planning & Tendering
- Prepare tender packages (scope, pricing schedules, terms)
- Maintain prequalified subcontractor lists; assess market health
- Issue RFQs, manage clarifications, run bid evaluation
- Perform tender analysis: bid levelling, compliance checks, technical/financial scoring
- Prepare Recommendation for Award (RFA) with delegated authority approvals

### 0.2 Subcontract Negotiation & Formation
- Negotiate terms, pricing, delivery dates, incentives
- Ensure flow-down of head contract obligations into subcontracts (AS 4000 → AS 4901)
- Prepare and execute subcontract agreements, purchase orders, amendments
- Collect securities, insurances, warranties before works commence

### 0.3 Contract Lifecycle Administration (Cradle-to-Grave)
- Track subcontractor progress against construction schedule; flag delays
- Manage variations, change orders, extensions of time (EOTs)
- Certify monthly progress claims per AS 4000 Clause 37.2 (14-day Superintendent window)
- Calculate and track retention (5% default, 50% release at PC, 50% after DLP)
- Maintain contract registers, correspondence logs, subcontractor files

### 0.4 Risk & Compliance
- Verify insurance certificates (public liability, workers comp, professional indemnity)
- Track safety documentation (SWMS, JSA, site inductions)
- Ensure compliance with Building Code, WHS Act, Security of Payment Act
- Identify contractual/commercial risks; maintain risk register
- Monitor subcontractor financial health (solvency, capacity)

### 0.5 Cost Control & Reporting
- Monthly consolidated reports: cash flow forecast, variation status, cost-to-complete
- Budget tracking with WBS cost codes; committed vs. actual vs. forecast
- Early warning of cost overruns, scope creep, or under-recovery
- Present contractual status to senior management and client

### 0.6 Dispute Resolution & Issue Management
- Lead resolution of subcontractor disputes, claims, commercial issues
- Issue back charge notices, corrective action plans
- Manage formal dispute processes per contract; assess claims
- Maintain 100% correspondence audit trail (RFIs, site instructions, approvals)

### 0.7 Stakeholder Management
- Primary liaison: subcontractors ↔ project management ↔ client ↔ finance ↔ legal
- Facilitate pre-construction kick-off meetings; establish communication protocols
- Coordinate with design team on shop drawings, RFIs, technical submittals

### 0.8 Contract Close-Out
- Final inspection with subcontractor; remedy defects
- Issue practical completion notice; monitor defects liability period (12 months)
- Prepare final account; release securities after Final Certificate
- Evaluate subcontractor performance; feed into prequalification for future projects

### 0.9 Invoice Verification & Payment Certification — The CA's Core Gatekeeping Function

This is the workflow that separates a CA from an accountant. When an accountant forwards a subcontractor invoice/payment claim to the CA, the CA is the **technical and contractual gatekeeper** — not just a financial reviewer. The accountant knows cost codes and budgets; the CA knows what actually happened on site.

#### 0.9.1 The Dual-Role Verification Pattern

```
┌─────────────────────────────────────────────────────────────────┐
│                    ACCOUNTANT (Financial)                         │
│  • Cost coding (WBS, project, phase)                             │
│  • Budget line check (can we afford this?)                       │
│  • 3-way match prep (Invoice ↔ PO/Subcontract ↔ Budget)         │
│  • GST, tax compliance                                           │
│  • Vendor statement reconciliation                               │
└──────────────────────────┬──────────────────────────────────────┘
                           │  "Please verify and certify"
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    CA (Technical/Contractual)                     │
│  • Was the work actually DONE? (site evidence)                   │
│  • Is it in SCOPE? (contract schedule of rates)                  │
│  • Are RATES correct? (match executed contract)                   │
│  • Are QUANTITIES accurate? (measured vs. claimed)               │
│  • Is RETENTION correct? (5% default, cap check)                 │
│  • Are COMPLIANCE docs current? (insurance, SWMS, lien waivers)  │
│  • Are VARIATIONS approved? (no payment without signed VO)       │
│  • Any DUPLICATES? (same item claimed last period?)              │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │   CA issues one of:     │
              │  A) Payment Certificate │ → Accountant processes payment
              │  B) Payment Schedule    │ → Disputes detailed with reasons
              └────────────────────────┘
```

#### 0.9.2 The SOPA Deadline Hammer (Australia)

Under the *Building and Construction Industry Security of Payment Act* (state-specific, same principles nationwide):

| Deadline | Requirement | Consequence of Missing |
|----------|-------------|----------------------|
| **10 business days** from claim receipt | Issue Payment Schedule | Full claimed amount becomes **statutory debt** — payable immediately, no defence |
| **15-20 business days** from claim receipt | Pay the Scheduled Amount | Claimant can suspend work, file for adjudication, garnish accounts |

**Critical SOPA rules the CA lives by:**
- If a document says "This is a payment claim under the Security of Payment Act" — the statutory clock is RUNNING. Treat it as a payment claim immediately.
- **Silence = acceptance of the full amount.** If the CA does nothing, the subcontractor gets everything they asked for, even if the work wasn't done.
- The Payment Schedule must list **EVERY reason** for withholding payment. Reasons not in the schedule CANNOT be raised later at adjudication. (This is the *Multiplex v Luikens* principle — the CA is locked into their stated reasons.)
- A "can't identify the work" schedule is valid and better than silence — it preserves adjudication rights when a claim is vague.
- Email service is valid for payment claims (*WNA Constructions v Canberra Building and Maintenance* [2025] ACTCA 17).
- "Pay when paid" clauses are **void** under SOPA — the subcontractor's right to payment is not contingent on the head contractor being paid by the client.

#### 0.9.3 CA's Step-by-Step Verification Process

| Day | Action | Detail |
|-----|--------|--------|
| **Day 1** | Log & Diary | Record claim ref, claimed amount, date received. **Set Day 10 absolute deadline.** |
| **Day 1** | SOPA Triage | Check for SOPA endorsement. If present → statutory clock running. If absent → still treat as claim but contract terms govern. |
| **Day 1** | Contract Pull | Retrieve executed subcontract/PO, schedule of rates, all approved variations, correspondence file, compliance register. |
| **Day 1-3** | Site Verify | Walk site or review: site diaries, inspection reports, progress photos, delivery dockets, attendance logs. **Confirm work was actually done.** |
| **Day 2-4** | Quantity Check | Measure claimed quantities against actuals. Spot-check high-value line items. Check against Bill of Quantities / Schedule of Values. |
| **Day 2-4** | Rate Check | Every claimed rate against the contract schedule. Flag any deviation — even $5/hour labour rate creep compounds. |
| **Day 3-5** | Compliance Check | Verify: insurance certificates (current?), SWMS/JSA (submitted?), lien waivers (conditional received?), bank guarantees (in place?), variations (signed?), safety certifications (valid?). |
| **Day 4-6** | Retention Calculation | Apply retention % to certified amount. Check if retention cap is reached (5% of contract sum is common). Track cumulative retention. |
| **Day 5-8** | Draft Response | **IF CLEAN:** Prepare Payment Certificate (IPC) with full breakdown. **IF DISPUTED:** Draft Payment Schedule with EVERY reason for withholding — over-explain, include contract clause references, attach evidence. |
| **Day 8-10** | Serve & File | Issue Payment Schedule/Certificate. Retain proof of service (email delivery receipt). File in contract register. Update cash flow forecast. |
| **Day 15-20** | Payment or Dispute | If certified → accountant processes payment. If disputed → unresolved portion enters dispute resolution. |

#### 0.9.4 Common Discrepancies the CA Finds

| Category | Example | CA Response |
|----------|---------|-------------|
| **Defective work** | Concrete pour failed 7-day strength test | Withhold 100% of that line item until rectified; cite inspection report |
| **Work not done** | Claimed for Level 3 framing — only Level 2 complete | Measure actual progress; certify only completed portion |
| **Rate deviation** | Contract rate $85/hr, claimed $95/hr | Reject rate variance; pay at contract rate; note in schedule |
| **Unauthorised variation** | Extra work done without signed VO | Reject in full; advise subcontractor to submit variation request |
| **Duplicate claim** | Same materials claimed last month and this month | Reject duplicate; reference previous claim number |
| **Incomplete docs** | Missing conditional lien waiver | Hold payment until waiver received; flag compliance |
| **Insurance lapsed** | Public liability expired 2 weeks ago | Hold ALL payment until renewed certificate provided |
| **Over-claiming** | Claims 95% complete but site inspection shows ~60% | Adjust to actual; attach dated photos; flag for project manager |
| **Retention not deducted** | Subcontractor claims gross, forgets retention | Apply retention % per contract; calculate net payable |
| **Stored materials** | Claims for materials not yet delivered to site | Reject or hold; require delivery docket and off-site storage agreement |

#### 0.9.5 The Payment Certificate (IPC) Structure

The Interim Payment Certificate is the formal output of the CA's verification. It is the auditable record of what the subcontractor is entitled to be paid:

```
┌─────────────────────────────────────────────────────────────┐
│ INTERIM PAYMENT CERTIFICATE No. __                            │
│ Project: ARCO — 22-24 Hood Street, Subiaco                   │
│ Subcontract: S05 — Hydraulics — Bushby Plumbing              │
│ Period: 26 Feb — 25 Mar 2026                                  │
│ Certificate Date: 28 Mar 2026                                 │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│ 1. Contract Sum (excl. GST):                     $420,000.00 │
│                                                              │
│ 2. Previously Certified to Date:                  $85,000.00 │
│                                                              │
│ 3. Work This Period:                              $52,500.00 │
│    (Breakdown per attached Schedule of Values)               │
│                                                              │
│ 4. Approved Variations This Period:                 $3,200.00 │
│                                                              │
│ 5. TOTAL CERTIFIED TO DATE (2+3+4):              $140,700.00 │
│                                                              │
│ 6. LESS Retention (5% of 5):                       $7,035.00 │
│    (Cumulative retention: $7,035 / $21,000 cap)              │
│                                                              │
│ 7. LESS Previous Payments:                        $80,750.00 │
│                                                              │
│ 8. NET PAYABLE THIS PERIOD (5-6-7):               $52,915.00 │
│                                                              │
│ 9. PLUS GST (10% of 8):                             $5,291.50 │
│                                                              │
│ 10. GROSS PAYABLE (8+9):                          $58,206.50 │
│                                                              │
│ CERTIFIED BY: __________________   Date: __________          │
│ (Contract Administrator)                                     │
└─────────────────────────────────────────────────────────────┘
```

#### 0.9.6 The Payment Schedule (Disputed Claim Response)

When the CA disputes any portion, the Payment Schedule must be comprehensive because it **locks in the reasons**:

```
┌─────────────────────────────────────────────────────────────┐
│ PAYMENT SCHEDULE — Response to Payment Claim No. PC-005       │
│ Subcontract: S05 — Hydraulics — Bushby Plumbing              │
│ Claim Date: 26 Mar 2026                                       │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│ Scheduled Amount (proposed to pay):              $38,200.00  │
│                                                              │
│ Reasons for withholding $14,300:                             │
│                                                              │
│ 1. Line Item 3 — "Rough-in Level 3 bathrooms"                │
│    Amount claimed: $8,500                                     │
│    Amount certified: $0                                       │
│    REASON: Site inspection 24 Mar 2026 confirms Level 3      │
│    rough-in not yet commenced. Reference: Site Diary         │
│    SD-20260324, photos IMG_4401-4410.                        │
│    Contract reference: Clause 37.2, Schedule of Values       │
│    Item 3.                                                    │
│                                                              │
│ 2. Line Item 5 — "Pressure testing — all levels"             │
│    Amount claimed: $5,800                                     │
│    Amount certified: $0                                       │
│    REASON: Test certificates not provided as required by     │
│    Specification Section 4.2. Inspection and Test Plan       │
│    ITP-HYD-003 not yet signed off.                           │
│    Contract reference: Spec Clause 4.2, ITP Register.        │
│                                                              │
│ 3. Retention not applied (Claimed gross, no deduction)       │
│    REASON: Retention at 5% per Subcontract Clause 5.1.      │
│    $14,300 × 5% = $715 retention applied to certified.       │
│                                                              │
│ TOTAL CERTIFIED: $38,200  |  TOTAL WITHHELD: $14,300        │
│                                                              │
│ CA SIGNATURE: _______________  DATE SERVED: ____________    │
└─────────────────────────────────────────────────────────────┘
```

#### 0.9.7 System Implications for the Architecture

This workflow dictates several non-negotiable requirements:

1. **Deadline tracking is mandatory.** The system MUST track the 10-business-day SOPA clock from claim receipt. Missing this deadline converts the full claimed amount to statutory debt. Every CA's dashboard must show "Days remaining" prominently.

2. **Payment Schedule is a first-class entity.** It's not just a "rejected" status on a claim. It's a structured document with: scheduled amount, per-item reasons with evidence references, contract clause citations, and locked-in content (immutable after service).

3. **Dual verification must be explicit.** The accountant's preliminary review and the CA's technical verification are separate steps with separate sign-offs. The system must show who did what.

4. **Evidence linking is critical.** Every line-item dispute must link to specific evidence (site photos, inspection reports, contract clauses). The AI can suggest these links, but the CA confirms them.

5. **SOPA endorsement detection.** The system should automatically detect SOPA language in incoming documents and escalate the priority — statutory clock means these claims jump the queue.

6. **The CA is the final certifier — not the AI, not the accountant.** The trust/safety tier for payment certification is always "human-only." AI can flag, suggest, and draft — but the CA's signature is the sole authority.

---

## 1. Data Model — What We're Modeling

### 1.1 The Domain from ARCO Data

From the `5.17 Sub Contractors` directory analysis, the real-world data patterns are:

```
Project (ARCO, 22-24 Hood St)
  └── Trade Package (e.g., "Concrete & Formwork")
        ├── Quotes from bidders (Adamini, FTI, TSS-Trustruct, Whitehouse)
        ├── Tender Analysis (cost comparison, compliance scoring)
        ├── Awarded To → Subcontract S03 (J Adamini) or PO17142
        │     ├── Executed subcontract document (with revisions)
        │     ├── Shop drawings (stair flight, reinforcement)
        │     ├── SE inspection reports (columns, SF1)
        │     └── Progress Claims (monthly, 6 claims Nov 2025–Apr 2026)
        │           ├── Payment advise (with retention math)
        │           └── Supporting evidence (timesheets, delivery dockets)
        └── Quotations from non-awarded bidders (archived)

Cross-cutting:
  - Procurement Schedule (Rev8/Rev9 .xlsm — live procurement tracker)
  - Cost Plan / PnL (February 2026 — budget baseline)
  - Correspondence (RFIs, site instructions, .msg emails)
  - Claims register (all subcontractor claims with status)
```

### 1.2 Entity-Relationship Diagram

```
┌──────────────────────┐
│  Company              │  Welink Construction (builder) — singleton tenant
│  (multi-tenant ready) │
└──────────┬───────────┘
           │ 1:N
┌──────────▼───────────┐
│  Project              │  ARCO, Econolodge, Kearns_Crs, 47 CBR
│  - name, job_number   │  - location, contract_sum, contract_type (AS 4000)
│  - start_date, pc_date│  - retention_pct, defects_liability_months
│  - superintendent      │  - client_company_name
└──────────┬───────────┘
           │ 1:N
     ┌─────┴──────────────────────────────┐
     │                                     │
┌────▼──────────────┐           ┌──────────▼──────────┐
│  TradePackage      │           │  ProcurementSchedule │  ← Rev8/Rev9 .xlsm
│  - trade (concrete, │           │  - revision           │
│    electrical, etc.)│           │  - trade → status     │
│  - wbs_code         │           │  - target_award_date  │
│  - budget_allocation │           │  - actual_award_date  │
│  - scope_of_works   │           └──────────────────────┘
└────────┬───────────┘
         │ 1:N
    ┌────┴──────────────────────────────────────────────────────────┐
    │                                                               │
┌───▼──────────┐                              ┌─────────────────────▼──────────┐
│  Quote        │ (competitive bids)          │  Commitment                    │ (awarded)
│  - vendor     │                              │  - commitment_type:            │
│  - amount     │                              │    purchase_order OR subcontract│
│  - date       │                              │  - reference_number (PO/S##)  │
│  - is_awarded │                              │  - commitment_value            │
│  - quote_doc  │                              │  - IF subcontract:             │
└──────────────┘                              │      retention_pct, start_date, │
                                               │      end_date, formal terms    │
                                               │  - IF purchase_order:           │
                                               │      delivery_date, goods_rcvd  │
                                               │  - UPGRADE RULE:                │
                                               │    subcontractor vendor +       │
                                               │    PO ≥ $100K → subcontract     │
                                               └────────┬──────────────────────┘
                                                        │ 1:N
                                         ┌──────────────┼──────────────┐
                                         │              │              │
                               ┌─────────▼──┐  ┌────────▼──┐  ┌───────▼──────┐
                               │ Document   │  │ Claim      │  │ Variation    │
                               │ - type     │  │ - number   │  │ - vo_number  │
                    │ - path     │  │ - period   │  │ - amount     │
                    │ - version  │  │ - claimed  │  │ - status     │
                    │ - category │  │ - certified│  │ - approved_by│
                    └────────────┘  │ - retention│  └──────────────┘
                                    │ - status   │
                                    │ - payment  │
                                    └────────────┘

┌──────────────────────────────────────────────────────┐
│  Vendor                 (reusable across projects)    │
│  - vendor_type: SUPPLIER or SUBCONTRACTOR             │
│                                                       │
│  SUPPLIER                    SUBCONTRACTOR             │
│  (materials, equipment)      (labor, trade services)  │
│  → Always PO                 → PO if < $100K          │
│  → No retention              → Subcontract if ≥ $100K │
│  → 3-way match claims        → Retention + SOPA       │
│  → Examples: Ausco Modular,  → Progress claims        │
│    Tru-struct, Solwest,      → Examples: J Adamini,   │
│    Bunnings                    Bushby, Powerhouse     │
└──────────────────────────────────────────────────────┘

┌──────────────────────┐
│  Correspondence       │
│  - type (RFI, SI,    │
│    email, meeting)    │
│  - from/to            │
│  - references (links  │
│    to subcontract/    │
│    claim/variation)   │
│  - date_sent/replied  │
│  - status (open/closed│
│    /overdue)          │
└──────────────────────┘
```

### 1.3 Key Relationships (from ARCO data)

| Relationship | Example | Frequency |
|---|---|---|
| Trade → multiple Quotes | Concrete got bids from 4 vendors | 3-5 per trade |
| Quote → one winner → Subcontract | Adamini won → S03 | 1 per trade |
| Subcontract → monthly Claims | S03 has 6 claims (Nov-Apr) | 1/month |
| Subcontract → many Documents | S03: contract + shop drawings + inspection reports | 5-20 docs |
| Purchase Order → Subcontract | PO17142 (Concrete - J Adamini) → S03 | Some POs become subcontracts |
| Project → Procurement Schedule | ARCO → Rev9 .xlsm | 1 active at a time |

---

## 2. System Architecture — The Tier 3 Platform

### 2.1 Architecture Layers

```
┌────────────────────────────────────────────────────────────────┐
│                      PRESENTATION LAYER                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ PyQt6 Desktop │  │ FastAPI REST  │  │ WebUI (vanilla JS)  │  │
│  │ (lilamy)      │  │ (/api/v1/...) │  │ (lilamy --web)      │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘  │
│         │                 │                      │              │
│         └─────────────────┼──────────────────────┘              │
│                           │  Python method calls                │
└───────────────────────────┼────────────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────────────┐
│                      SERVICE LAYER                               │
│                                                                  │
│  ┌────────────────────┐  ┌──────────────────────┐               │
│  │ SubcontractorService│  │ SubcontractorAgent   │               │
│  │ (QObject + signals) │  │ (AI/LLM operations)   │               │
│  │                     │  │                      │               │
│  │ • CRUD subcontracts │  │ • Document extraction│               │
│  │ • Claims workflow   │  │ • Tender analysis    │               │
│  │ • Retention calc    │  │ • Anomaly detection  │               │
│  │ • Payment certs     │  │ • Claim validation   │               │
│  │ • Excel generation  │  │ • Compliance checks  │               │
│  │ • Email/Outlook     │  │ • Drafting assistant │               │
│  └────────┬───────────┘  └──────────┬───────────┘               │
│           │                         │                            │
└───────────┼─────────────────────────┼────────────────────────────┘
            │                         │
┌───────────▼─────────────────────────▼────────────────────────────┐
│                       DATA LAYER                                  │
│  ┌──────────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │ subcontractor.db  │  │ ChromaDB      │  │ File System      │   │
│  │ (SQLite, WAL)     │  │ (RAG search)  │  │ (documents, PDFs)│   │
│  └──────────────────┘  └──────────────┘  └──────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 Service Components (following lilAmy Service Pattern)

Each service follows: `QObject + pyqtSignal + threading.Thread + queue.Queue`

#### `subcontractor_db.py` — Database
- **Tables:** projects, trade_packages, vendors, subcontracts, purchase_orders, quotes, claims, claim_items, variations, documents, correspondence, procurement_schedule, compliance_checks
- Pattern: identical to `variation_db.py` — `_get_connection()`, `upsert_*()`, `get_*()`, `update_*()`, `delete_*()` for every entity
- Path: `<LILAMY_DATA_DIR>/subcontractor.db` (separate from variations.db, mail_history.db)

#### `subcontractor_service.py` — Business Logic
- **Signals:** `subcontract_created`, `claim_certified`, `document_processed`, `anomaly_detected`, `report_generated`, `error_occurred`, `progress_update`
- **Work queues:** CRUD, claim processing, report generation, email sending
- **Deterministic calculations** (NEVER LLM for money):
  - Retention math: `certified × retention_pct`, capped at retention limit
  - GST computation: `(certified - retention) × 0.10`
  - Cash flow forecasting: committed vs. actual vs. projected
  - Progress percentages, schedule variance

#### `subcontractor_agent.py` — AI Operations
- **Uses:** Gemini Flash (fast) for extraction, Gemini Pro (smart) for complex analysis
- **Capabilities:**
  1. **Document Ingestion Agent** — PDF/Excel/Email → structured data
  2. **Tender Analysis Agent** — Bid comparison, compliance scoring, anomaly flagging
  3. **Claim Validation Agent** — Cross-check claimed vs. contract vs. progress
  4. **Compliance Monitor Agent** — Insurance expiry, missing docs, deadline alerts
  5. **Correspondence Drafter Agent** — RFI responses, claim responses, payment certificates
  6. **Anomaly Detection Agent** — Unusual cost patterns, schedule risks, vendor risks

#### `subcontractor_template.py` — Document Generation
- Tender analysis comparison spreadsheet
- Payment certificate (AS 4000-compliant format)
- Progress claim register
- Monthly cost report / cash flow forecast
- Subcontractor performance scorecard

### 2.3 AI Agent Architecture

Following the multi-agent pattern identified in 2025-2026 construction AI platforms (Adaptive's 9 specialized agents, Facilio Atom's execution agents):

```
┌─────────────────────────────────────────────────────────────────────┐
│                     SubcontractorAgent (orchestrator)                │
│                                                                      │
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────────┐    │
│  │ Document         │  │ Tender          │  │ Claim            │    │
│  │ Ingestion Agent  │  │ Analysis Agent  │  │ Validation Agent │    │
│  │                  │  │                 │  │                  │    │
│  │ Input: PDF/Excel │  │ Input: quotes   │  │ Input: claim     │    │
│  │  /Email/.msg     │  │  from 3-5 bidders│  │  + contract +   │    │
│  │ Output: struct-  │  │ Output: scored  │  │  site progress   │    │
│  │  ured JSON       │  │  comparison,    │  │ Output: validated│    │
│  │                  │  │  recommendation │  │  with flags      │    │
│  └─────────────────┘  └─────────────────┘  └──────────────────┘    │
│                                                                      │
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────────┐    │
│  │ Compliance       │  │ Correspondence  │  │ Anomaly          │    │
│  │ Monitor Agent    │  │ Drafter Agent   │  │ Detection Agent  │    │
│  │                  │  │                 │  │                  │    │
│  │ Input: document  │  │ Input: context  │  │ Input: all       │    │
│  │  registry +      │  │  + template +   │  │  financial data  │    │
│  │  deadlines       │  │  entity data    │  │  across project  │    │
│  │ Output: alerts,  │  │ Output: draft  │  │ Output: flagged  │    │
│  │  missing docs    │  │  email/letter   │  │  anomalies +     │    │
│  │                  │  │                 │  │  severity score  │    │
│  └─────────────────┘  └─────────────────┘  └──────────────────┘    │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ RAG Pipeline (shared across agents)                          │    │
│  │  • ChromaDB collection: subcontractor_knowledge              │    │
│  │  • Ingests: contracts, specs, correspondence, claims         │    │
│  │  • Provides: clause lookup, precedent search, cost history   │    │
│  └─────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.4 Trust/Safety Boundaries for Financial Decisions

Every AI-involved financial operation falls into one of four tiers:

| Tier | Threshold | Action | Applies To |
|------|-----------|--------|------------|
| **Auto-execute** | < $1,000 impact | AI processes, system executes automatically | Document filing, data extraction, small POs, routine compliance reminders |
| **Confirm-execute** | $1K–$10K impact | AI completes, human clicks one approval | Standard progress claims, minor variations, template correspondence |
| **Propose-review** | $10K–$50K impact | AI drafts, human reviews and modifies before execution | Major variations, tender analysis, claim disputes, payment certificates |
| **Alert-only** | > $50K impact or safety-critical | AI flags for full human handling; provides analysis but no recommendation | Large claims, contract terminations, liquidated damages, dispute escalation |

**Golden rule:** `certified_amount` and `retention_amount` are ALWAYS set by human or deterministic formula — never by LLM. The AI can *suggest* and *flag*, but the dollar sign is always human-final.

---

## 3. Feature Map — The 8 Workstreams → System Modules

### 3.1 Procurement & Tendering Module

| CA Task | System Feature | AI Role |
|---------|---------------|---------|
| Prepare tender packages | Template engine: scope + pricing schedule from trade package template | AI drafts scope from project specs (RAG) |
| Issue RFQs to prequalified vendors | Vendor CRM filterable by trade, rating, availability | AI prequalification scoring from past performance |
| Manage clarifications | Correspondence module with structured Q&A tracking | AI suggests answers from contract knowledge base |
| Bid levelling & comparison | Tender analysis spreadsheet generator; side-by-side quote matrix | AI extracts line items from PDF quotes; flags outliers; scores compliance |
| Recommendation for Award | RFA document generator with approval workflow | AI drafts RFA with evidence; routes per delegated authority |

### 3.2 Subcontract Formation Module

| CA Task | System Feature | AI Role |
|---------|---------------|---------|
| Flow-down head contract terms | Clause library: AS 4000 → AS 4901 mapping; custom clause templates | AI checks subcontract for missing flow-down clauses |
| Negotiate terms | Version-tracked subcontract drafts with change highlighting | AI flags unfavorable deviations from standard terms |
| Execute agreement | Digital subcontract registry with status workflow | AI extracts key dates/amounts from executed PDF |
| Collect pre-start documents | Compliance checklist: insurances, SWMS, warranties, bank guarantees | AI monitors expiry dates; auto-reminds before lapse |

### 3.3 Contract Administration Module (the core)

This is where the CA's daily work lives. The module must cleanly separate the **accountant's financial review** from the **CA's technical/contractual verification** — two different people, two different skill sets, two sequential gates before money moves.

| CA Task | System Feature | AI Role |
|---------|---------------|---------|
| **Invoice verification (dual-gate)** | Accountant Gate 1: cost coding, budget check, 3-way match prep. CA Gate 2: scope, rates, quantities, progress, compliance. Both must sign off before payment. | AI extracts line items; suggests cost codes (Gate 1); flags rate/quantity/progress discrepancies (Gate 2) |
| **SOPA deadline management** | Automatic SOPA endorsement detection → priority escalation → 10-business-day countdown timer on CA dashboard. If deadline breached → full amount becomes statutory debt. | AI pattern-matches SOPA language (deterministic, no LLM needed); auto-diarises deadline |
| **Payment Certificate (IPC) preparation** | IPC generated from certified claim data. Full breakdown: contract sum → previous certified → this period → variations → cumulative → retention → net payable → GST → gross payable | AI populates the IPC template; CA reviews and certifies the dollar amount |
| **Payment Schedule drafting** | When claim is disputed: per-item reasons, contract clause references, evidence links. Content is IMMUTABLE after service (Multiplex v Luikens principle). | AI drafts reasons + evidence links per disputed item; CA reviews, adds missing reasons, and serves |
| **Retention tracking** | Automatic retention calculation per AS 4000; release triggers at PC and DLP expiry; cumulative retention vs. cap | AI flags when retention cap is reached or release conditions met |
| **Variation management** | Integration with existing `variation_db.py`; link variations to subcontracts; block payment for unapproved variations | AI suggests cost impact of variation on subcontract; flags claims referencing unapproved VOs |
| **EOT management** | Extension of Time register; schedule impact visualization; link EOTs to affected subcontracts | AI analyzes delay notices against critical path; suggests EOT entitlement |
| **Document control** | Version-tracked document registry per subcontract (shop drawings, ITPs, inspection reports, site photos) | AI classifies incoming documents; routes to correct subcontract folder; links evidence to claim items |
| **Correspondence register** | Immutable audit trail: every RFI, site instruction, payment schedule, certificate — date-stamped, allocated, escalated if overdue | AI detects unanswered correspondence past deadline; drafts escalation reminders |

### 3.4 Risk & Compliance Module

| CA Task | System Feature | AI Role |
|---------|---------------|---------|
| Insurance verification | Insurance register with expiry dates; certificate validation | AI extracts expiry dates from insurance PDFs; alerts 30 days before |
| Safety documentation | SWMS/JSA register per subcontractor | AI checks completeness against project requirements |
| Financial health monitoring | Vendor financial dashboard: payment history, claim patterns, dispute frequency | AI detects early warning signs (delayed claims → cash flow stress; frequent disputes → relationship risk) |
| Regulatory compliance | Compliance checklist per jurisdiction (WA Building Act, WHS, Security of Payment) | AI maps contract clauses to regulatory requirements |

### 3.5 Cost Control & Reporting Module

| CA Task | System Feature | AI Role |
|---------|---------------|---------|
| Budget vs. actual tracking | WBS cost code drill-down: committed (POs/subcontracts) → actual (certified claims) → forecast | AI predicts cost-at-completion using earned value + historical patterns |
| Cash flow forecasting | Monthly cash flow projection from subcontract payment schedules | AI models scenarios (best case, likely, worst case) |
| Monthly consolidated report | Auto-generated report: variation status, claim status, risk register, cost summary | AI drafts narrative sections; human reviews and signs off |
| Variance analysis | Real-time variance flags when actual exceeds budget by >5% | AI traces variance to root cause (scope change, rate increase, productivity) |

### 3.6 Dispute Resolution Module

| CA Task | System Feature | AI Role |
|---------|---------------|---------|
| Claim dispute tracking | Dispute register linked to claims; status workflow (raised → under review → resolved/escalated) | AI summarizes dispute history and suggests resolution pathways |
| Back charge management | Back charge notice generator; acceptance tracking | AI calculates back charge amount from supporting evidence |
| Correspondence audit trail | Immutable correspondence log (date-stamped, allocated, escalation if overdue) | AI detects unanswered RFIs past deadline; drafts escalation |

### 3.7 Stakeholder Management Module

| CA Task | System Feature | AI Role |
|---------|---------------|---------|
| Kick-off meeting | Pre-construction meeting agenda template; minutes distribution | AI generates agenda from subcontract scope; drafts minutes from notes |
| Communication protocols | Contact matrix per project; escalation paths | AI routes incoming emails to correct subcontract file automatically |
| Progress reporting | Dashboard: subcontractor progress % vs. schedule, claim status, open issues | AI summarizes status for weekly stakeholder update |

### 3.8 Close-Out Module

| CA Task | System Feature | AI Role |
|---------|---------------|---------|
| Final inspection | Defects register with photo evidence; rectification tracking | AI matches defects to subcontract scope; tracks time-to-rectify |
| Final account | Final account reconciliation: all claims + variations - back charges - retention | AI reconciles; human certifies |
| Performance evaluation | Subcontractor scorecard: quality, time, cost, safety, admin compliance | AI computes scores from project data; feeds back to prequalification |
| Securities release | Trigger-based workflow: PC achieved → 50% release; DLP expired → balance release | AI monitors triggers; drafts release letters |

---

## 4. Implementation Roadmap — Build Order

### The Variation Module Pattern (what we're replicating)

Every phase follows the same controllability principle established by the Variation module:

```
                     ┌─────────────────────────────┐
                     │   AGENT (AI)                 │
                     │   Extracts, suggests,        │
                     │   proposes — NEVER decides   │
                     └─────────────┬───────────────┘
                                   │ structured JSON
                                   ▼
                     ┌─────────────────────────────┐
                     │   EDITING PAGE (WebUI)       │
                     │   USER HAS FULL CONTROL:     │
                     │   • Edit every field         │
                     │   • Add/remove line items    │
                     │   • Adjust rates & qtys      │
                     │   • Override AI suggestions  │
                     │   • Change vendor type       │
                     └─────────────┬───────────────┘
                                   │ user confirms
                                   ▼
                     ┌─────────────────────────────┐
                     │   SERVICE LAYER               │
                     │   Saves to DB, generates     │
                     │   documents, sends emails    │
                     └─────────────┬───────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
        ┌──────────┐       ┌──────────┐         ┌──────────┐
        │ Save to  │       │ Generate │         │ Send via │
        │ SQLite   │       │ XLSX/PDF │         │ Outlook  │
        └──────────┘       └──────────┘         └──────────┘
```

**Iron rule:** The agent is a data-entry accelerant. The human is the final authority over every field, every dollar, every document.

---

### Phase 1: Quote → Commitment Pipeline (Weeks 1-3)
**Mirrors Variation module: agent extracts → editing page → confirm → push**

#### Files to create:

```
shared/src/shared_tools/
├── subcontractor_db.py         ← Schema + CRUD (vendors, commitments, quotes, line items)
├── subcontractor_service.py    ← QObject + signals + threading.Thread + queue.Queue
├── subcontractor_agent.py      ← Quote ingestion agent (Gemini Flash vision)
├── subcontractor_template.py   ← PO document generator + Subcontract document generator
                                  (xlsx template based, following variation_template.py pattern)

lilamy/modules/
├── subcontractor_routes.py     ← FastAPI router: CRUD for commitments, document gen
├── subcontractor_agent_routes.py ← FastAPI router: quote analysis endpoint
```

#### Flow:

```
1. USER uploads quote PDF(s) to WebUI
        │
        ▼
2. AGENT analyzes (subcontractor_agent.py):
   POST /api/subcontractors/agent/analyze-quote
        │  ← Gemini Flash vision: multi-page PDF → structured JSON
        │  Returns: {vendor_name, vendor_type_guess, trade, line_items[], total, ...}
        │
        ▼
3. EDITING PAGE populated with extracted data:
        │  ← USER reviews every field
        │  ← USER adjusts: vendor_type, trade, line items, rates, quantities
        │  ← USER adds scope description, delivery dates, special conditions
        │  ← System enforces: vendor_type=subcontractor + total ≥ $100K → 
        │     flags "Subcontract required, not PO"
        │
        ▼
4. USER clicks SAVE:
   POST /api/subcontractors/commitments
        │  ← Creates commitment in DB (PO or Subcontract based on vendor_type + amount)
        │  ← All line items saved
        │
        ▼
5. USER clicks GENERATE DOCUMENT:
   POST /api/subcontractors/commitments/{id}/generate
        │  ← If PO: generates PO document from template
        │  ← If Subcontract: generates subcontract document with full terms
        │  ← Uses subcontractor_template.py (xlsx-based, like variation template)
        │
        ▼
6. USER clicks EXPORT PDF:
   POST /api/subcontractors/commitments/{id}/export-pdf
        │  ← Excel COM → PDF (Windows)
        │
        ▼
7. USER clicks SEND:
   POST /api/subcontractors/commitments/{id}/send
        │  ← Drafts email via LLM
        │  ← Attaches PDF
        │  ← Sends via Outlook COM
        │  ← Updates commitment status: draft → issued
```

#### Deliverable:
- Upload a quote PDF → agent extracts vendor, trade, line items, rates, total
- CA reviews and edits in WebUI → saves as PO or Subcontract
- CA generates document (xlsx) → exports PDF → sends via Outlook
- All commitments searchable in the register
- $100K upgrade rule enforced automatically

#### What we can process with existing data:
- **258 quote PDFs** → agent can extract from every one
- **40+ POs** → system can regenerate them from their source quotes
- **14 subcontracts** → system can regenerate with full terms from their source quotes/P.Os

---

### Phase 2: Subcontract Learner (Week 4)
**Batch knowledge builder — learns from existing project subcontract folders**

This is a **new component type** — not a real-time agent, but a batch learner that builds the knowledge base from historical data.

#### File to create:

```
shared/src/shared_tools/
├── subcontractor_learner.py    ← Batch learner: scans project folder → populates KB
```

#### What it learns from `5.17 Sub Contractors`:

```
SCAN TARGET: C:\crewAI\lilamy_test_project\5.17 Sub Contractors
        │
        ├── Folder 1. Purchase Orders (94 files, 40+ POs)
        │     ├── LEARN: PO numbering convention (PO16808 → PO18814)
        │     ├── LEARN: PO structure, standard clauses, payment terms
        │     ├── LEARN: Which PO folders contain quotes, emails, technical docs
        │     ├── LEARN: Supplier names, ABNs, contact details from PO PDFs
        │     └── LEARN: Typical PO values per trade type
        │
        ├── Folder 2. Subcontracts (225 files, 14 packages)
        │     ├── LEARN: Subcontract numbering (S01-S14), standard structure
        │     ├── LEARN: Document completeness pattern per package:
        │     │     Tender Analysis → Executed Contract → Shop Drawings → Claims
        │     ├── LEARN: Standard Welink subcontract clauses (read from .docx/.pdf):
        │     │     • Retention rate: 5%? 10%?
        │     │     • Insurance requirements: public liability $20M?
        │     │     • Defects liability period: 12 months?
        │     │     • Payment terms: 30 days from claim?
        │     │     • SOPA clause language
        │     ├── LEARN: Trade scope descriptions per package
        │     └── LEARN: Subcontractor profiles with full contract history
        │
        ├── Folder 3. Quotes (258 files, 34 trades, 70+ vendors)
        │     ├── LEARN: Vendor → trade mapping (who does what?)
        │     ├── LEARN: Rate benchmarks per trade (market price intelligence):
        │     │     • Hydraulics rough-in: $85-120/hr
        │     │     • Concrete supply 50MPa: $320-380/m³
        │     │     • Electrical fit-off per apartment: $4,200-5,800
        │     ├── LEARN: Which vendors bid together (competitive sets):
        │     │     • Hydraulics → {Bushby, Ace+, Fairways}
        │     │     • Electrical → {Powerhouse, Ideal, OBK}
        │     │     • Glazing → {Concept, Direct, LUXWIN, NuChange, Ventana}
        │     ├── LEARN: Supplier vs. Subcontractor classification from context
        │     └── LEARN: Quote format patterns (what does a Welink quote look like?)
        │
        └── OUTPUT: Knowledge Base populated with:
              ├── Vendor CRM (70+ vendors with trade specialties, contacts, history)
              ├── Rate Database (trade × scope × rate benchmarks)
              ├── Clause Library (standard Welink subcontract terms)
              ├── Trade-Vendor Map (which vendors serve which trades)
              └── Competitive Sets (who bids against whom for each trade)
```

#### How the learner feeds Phase 1:

When the CA uploads a new quote in Phase 1, the editing page now has **smart defaults learned from history**:

```
New quote uploaded for "Hydraulics rough-in, 46 fixtures"
        │
        ├── Agent extracts: vendor="Bushby Plumbing", total=$142,000
        │
        └── Learner provides smart defaults (shown in editing page):
              ├── vendor_type: "subcontractor" (learned: Bushby is always subcontractor)
              ├── trade: "Hydraulics" (learned from Bushby's trade history)
              ├── rate_benchmark: "$85-120/hr typical for hydraulic rough-in"
              ├── retention: 5% (learned from Welink standard)
              ├── insurance: "$20M public liability" (learned from Welink standard)
              ├── defects: "12 months" (learned from Welink standard)
              └── competitive_set: "Bushby vs. Ace+ vs. Fairways" (learned from quote history)
```

#### Deliverable:
- Learner scans a project subcontract folder and populates the knowledge base
- Vendor CRM auto-populated with 70+ vendors from existing data
- Rate benchmarks available per trade
- Standard Welink subcontract clauses extracted and available as defaults
- Phase 1 editing page enriched with learned smart defaults

---

### Phase 3: Tender Analysis + Advanced Generation (Weeks 5-6)

#### Add to subcontractor_agent.py:
```
- Tender comparison agent: parallel analysis of 2-5 quotes for same trade
- Side-by-side scoring: price, compliance, technical, schedule
- Recommendation for Award drafter
```

#### Add to subcontractor_template.py:
```
- Tender analysis comparison spreadsheet (following ARCO's existing format)
- Subcontract document with full AS 4901 terms (not just PO template)
```

#### Add to subcontractor_routes.py:
```
POST /api/subcontractors/tender-analysis/{trade_package_id}
  → Accepts 2+ quote entry_ids
  → Agent compares them side-by-side
  → Returns scored matrix in editing page
  → User confirms winner
  → Generates Recommendation for Award
  → Generates PO or Subcontract for the winner
```

#### Deliverable:
- Full tender analysis workflow: upload N quotes → side-by-side comparison → award
- Subcontract document generator with full AS 4901 terms
- Winner automatically gets PO/Subcontract; losers archived for audit

---

### Phase 4: Platform Integration (Weeks 7-8)

```
lilamy/modules/
├── subcontractor_routes.py     ← Register in registry.py as "subcontractors"
├── subcontractor_agent_routes.py

lilamy/static/
├── subcontractors.html         ← WebUI: PO/Subcontract editing page
├── subcontractors.js           ← WebUI: agent interaction, editing, document gen

lilamy/modules/registry.py:
  Add: "subcontractors": {
    "id": "subcontractors",
    "name": "Subcontractors",
    "icon": "🏗️",
    "description": "Quote → PO → Subcontract → Claims",
    "enabled": True,
    "router_path": "lilamy.modules.subcontractor_routes:router",
    "extra_routers": [
      "lilamy.modules.subcontractor_agent_routes:router",
    ],
  }
```

#### Deliverable:
- Subcontractor module appears in lilamy sidebar
- Full WebUI editing page for Quote → PO/Subcontract workflow
- Integration with existing Mail module (auto-file subcontractor emails)
- Integration with existing Variations module (link VOs to subcontracts)

---

### Phase 5: Claims Workflow (When Data is Available)
**Deferred until the ARCO project generates more claims data**

Currently only 25 claim files across 3 subcontractors. We need at least 3+ months of claims across 5+ subcontractors before this phase is worth building.

#### Planned deliverables (when data supports it):
- Progress claim submission + verification workflow
- IPC (Interim Payment Certificate) generation
- Payment Schedule generation for disputed claims
- SOPA 10-business-day deadline enforcement
- Retention tracking and release triggers
- Payment certificate cover letter generation

---

## Phase Summary

| Phase | Weeks | Data Support | Core New Files |
|-------|-------|-------------|----------------|
| **1. Quote → Commitment** | 1-3 | ✅ 258 quotes, 94 POs, 225 subcontract docs | `subcontractor_db.py`, `_service.py`, `_agent.py`, `_template.py`, `_routes.py`, `_agent_routes.py` |
| **2. Subcontract Learner** | 4 | ✅ All 4 folders as training corpus | `subcontractor_learner.py` |
| **3. Tender Analysis** | 5-6 | ✅ 34 trades, many with 2-5 bidders | Extend `_agent.py`, `_template.py`, `_routes.py` |
| **4. Platform Integration** | 7-8 | ✅ All of Phase 1-2 data | `subcontractors.html`, `subcontractors.js`, registry update |
| **5. Claims Workflow** | 9+ | ❌ Only 25 claim files. WAIT. | `subcontractor_claims.py` (future) |

---

### File Structure (Final State)

```
shared/src/shared_tools/
├── subcontractor_db.py           ← Database schema + CRUD
├── subcontractor_service.py      ← Business logic service (QObject + signals + thread + queue)
├── subcontractor_agent.py        ← AI agents (quote ingestion, tender analysis)
├── subcontractor_template.py     ← Document generators (PO, Subcontract, Tender Analysis)
├── subcontractor_learner.py      ← Batch knowledge builder (scans project folders)

lilamy/modules/
├── subcontractor_routes.py       ← FastAPI router: commitments CRUD, document gen, email
├── subcontractor_agent_routes.py ← FastAPI router: agent analysis endpoints
├── subcontractor_learner_routes.py ← FastAPI router: learner scan/status endpoints

lilamy/static/
├── subcontractors.html           ← WebUI editing page
├── subcontractors.js             ← WebUI interaction logic

knowledge/
├── subcontractor_system_architecture.md  ← THIS DOCUMENT
├── po_template.xlsx              ← PO document template
├── subcontract_template.xlsx     ← Subcontract document template
├── tender_analysis_template.xlsx ← Tender comparison matrix

data/  (in LILAMY_DATA_DIR, gitignored)
├── subcontractor.db              ← Production database
├── subcontractor_knowledge/      ← Learner output (vendor profiles, rate DB, clause library)
└── subcontractor_output/         ← Generated POs, subcontracts, PDFs
```

---

## 5. Key Design Decisions

### 5.1 Separate DB, Not Extension of Variations DB

**Decision:** `subcontractor.db` is a standalone database, not new tables in `variations.db`.

**Why:**
- Subcontracts are a different domain from client variations — different lifecycle, different stakeholders
- The variation system models "change to head contract" — subcontract system models "agreement with trade"
- They link (a variation may impact subcontracts) but are not the same entity
- Separate DBs allow independent evolution, migration, and backup

**The link point:** `subcontracts.variation_entry_id` can reference `variations.entry_id` when a head contract variation flows down to a subcontract.

### 5.2 Vendor Type Drives the Instrument, Amount Drives the Upgrade

**Decision:** The entity type is determined by **what the vendor is**, not what document they hold.

```
┌──────────────────────────────────────────────────────────────────┐
│                      VENDOR CLASSIFICATION                        │
│                                                                   │
│  SUPPLIER                          SUBCONTRACTOR                  │
│  (provides materials,              (provides labor,               │
│   equipment, goods)                 trade services)               │
│                                                                   │
│  Always gets a PO                  Amount < $100K → PO            │
│  Regardless of amount              Amount ≥ $100K → Subcontract   │
│                                                                   │
│  Examples:                         Examples:                      │
│  • Ausco Modular (buildings)       • J Adamini (concrete)         │
│  • Tru-struct (wall supply)        • Bushby Plumbing (hydraulics) │
│  • Solwest (door frames)           • Powerhouse (electrical)      │
│  • Bunnings (retail materials)     • Atlas Precast (panels)       │
│                                                                   │
│  Claims: 3-way match               Claims (< $100K PO):           │
│  (PO ↔ GRN ↔ Invoice)              Simpler verification,          │
│  No retention, no SOPA             no formal retention            │
│                                     Claims (≥ $100K Subcontract): │
│                                     Full SOPA progress claims,    │
│                                     retention, Payment Certs,     │
│                                     Payment Schedules             │
└──────────────────────────────────────────────────────────────────┘
```

**The $100K upgrade rule (subcontractors ONLY):**
- When a PO is issued to a **subcontractor** and the value reaches $100,000 AUD → the system must flag for upgrade to a formal subcontract
- The PO can still be used for the initial commitment (enabling works while subcontract is being finalized), but the upgrade is mandatory
- The upgrade is NOT about scope complexity — it's purely a financial threshold for risk management. Above $100K, the full legal machinery (retention, insurance, formal terms, SOPA) is required.

**Suppliers are immune to this rule:** A supplier providing $500K of modular buildings gets a PO. They're delivering goods, not performing construction work on site. No retention, no SOPA, no progress claims — just delivery + invoice + 3-way match.

**Implementation:** Vendors have a `vendor_type` field (`"supplier"` or `"subcontractor"`). POs and Subcontracts are stored in a shared `commitments` table with `commitment_type` (`"purchase_order"` or `"subcontract"`). The system enforces: subcontractor + commitment_type = "purchase_order" + value ≥ $100K → requires upgrade or override with justification.

### 5.3 Deterministic Money, AI-Assisted Everything Else

**Decision:** Any calculation that produces a dollar amount uses pure Python math — never LLM inference.

**Why:** The `genai.upload_file()` bug (wrong kwarg silently caught) is the canonical example of why. A hallucinated claim amount is a liability. AI can *classify*, *extract*, *flag*, and *suggest* — but the arithmetic is always in `subcontractor_service.py` as plain Python.

### 5.4 Vendor is a First-Class Entity (Cross-Project)

**Decision:** Vendors/subcontractors are stored independently of projects, with many-to-many project relationships.

**Why:** The ARCO data already shows cross-project vendors (Bushby Plumbing appears in ARCO POs and subcontracts). Vendor prequalification, insurance, and performance history must persist across projects. When a new project starts, the system should suggest prequalified vendors from past projects.

### 5.5 Document-First, Not Form-First

**Decision:** Documents are ingested as primary sources; database fields are derived from documents, not the other way around.

**Why:** In construction, the signed PDF always prevails over the database entry. Every field in the system should trace back to a source document + page/paragraph reference. The AI extraction agent proposes values; a human confirms against the source document.

---

## 6. Integration with Existing lilAmy Platform

### 6.1 Existing Modules to Connect

| Existing Module | Integration |
|---|---|
| `variation_db.py` + `variation_service.py` | Link variations to subcontracts; flow-down tracking |
| `mail_service.py` | Auto-classify subcontractor emails; link to subcontract file |
| `calendar_service.py` | Subcontract milestones → calendar; claim due dates → reminders |
| `memory_service.py` | ChromaDB RAG over subcontract documents |
| `outlook_tool.py` | Send claim certificates, RFI responses, payment advises |
| `llm_config.py` | `get_llm("fast")` for extraction, `get_llm("smart")` for analysis |
| `ipc_bridge.py` | Shared data directory resolution (`CREWAI_DIR`) |
| `file_registry.py` | Track all subcontract documents with MD5 hashing |

### 6.2 UI Integration

```
lilamy Platform (existing tabs)
  ├── 📧 Mail      (existing)
  ├── 📅 Calendar   (existing)
  ├── 📋 Variations (existing)
  ├── 🏗️ Subcontractors  ← NEW TAB
  │     ├── Projects list
  │     ├── Trade Packages grid
  │     ├── Subcontract detail (claims, docs, compliance)
  │     └── Vendor CRM
  └── ⚙️ Settings   (existing)
```

---

## 7. Database Schema (Detailed)

### 7.1 Core Tables

**companies** — Builder/tenant (singleton for now, multi-tenant ready)
```sql
id, entry_id, name, abn, acn, address, phone, email, logo_path
```

**projects** — Construction projects
```sql
id, entry_id, company_entry_id FK, name, job_number, location,
contract_type, contract_sum, start_date, pc_date, dlp_months,
retention_pct, retention_limit, superintendent_name, client_name,
status, created_at, updated_at
```

**trade_packages** — Work breakdown by trade
```sql
id, entry_id, project_entry_id FK, trade_name, wbs_code,
scope_description, budget_allocation, target_award_date,
actual_award_date, status, sort_order
```

**vendors** — Subcontractor/vendor CRM (cross-project)
```sql
id, entry_id, vendor_type ('supplier' or 'subcontractor'),  ← DRIVES EVERYTHING
company_name, trading_name, abn, contact_name,
contact_email, contact_phone, address, trade_categories (JSON array),
prequalification_status, insurance_expiry, safety_rating,
performance_score, status, notes, created_at, updated_at
```

**commitments** — Unified table for both POs and Subcontracts
```sql
id, entry_id, project_entry_id FK, trade_package_entry_id FK,
vendor_entry_id FK,
commitment_type ('purchase_order' or 'subcontract'),  ← determined by vendor_type + amount
reference_number (PO16811, S03, etc.),
title, description, commitment_value,
-- Subcontract fields (only populated when commitment_type = 'subcontract'):
retention_pct, retention_limit, start_date, end_date,
defects_liability_end,
-- PO fields (only populated when commitment_type = 'purchase_order'):
po_delivery_date, po_goods_receipt_date,
-- Common:
status (draft→issued→executed→in_progress→goods_received→closed→superseded),
securities_held, insurance_verified,
upgraded_from_commitment_entry_id FK,  ← if this subcontract was upgraded from a PO
upgraded_at,
executed_contract_path, created_at, updated_at
```

**UPGRADE RULE (enforced by system):**
```python
if vendor.vendor_type == "subcontractor" 
   and commitment.commitment_type == "purchase_order" 
   and commitment.commitment_value >= 100_000:
    → FLAG: "Upgrade to Subcontract required"
    → Auto-create subcontract draft linked to the PO
    → When subcontract is executed: PO.status → 'superseded'
```

**quotes** — Competitive bids (pre-award)
```sql
id, entry_id, trade_package_entry_id FK, vendor_entry_id FK,
quote_ref, amount, date_submitted, is_awarded, compliance_score,
technical_score, commercial_score, total_score, quote_doc_path,
notes, created_at
```

**claims** — Monthly progress claims / payment claims
```sql
id, entry_id, subcontract_entry_id FK, claim_number, period_start,
period_end, date_received (SOPA clock starts), sopa_endorsed (bool),
sopa_deadline (date, 10 business days from receipt), date_submitted,
claimed_amount, accountant_review_date (when accountant forwards to CA),
accountant_cost_code (WBS code assigned), accountant_budget_check (pass/fail/over),
accountant_notes, ca_verified_date, ca_site_inspection_ref, certified_amount,
retention_amount, retention_rate, gst_amount, net_payable, ca_notes,
payment_date, payment_ref, status
(submitted→accountant_review→pending_ca_verification→
certified→paid→disputed→resolved),
certificate_path, payment_schedule_path, supporting_docs (JSON array),
created_at
```

**payment_schedules** — Formal SOPA response when claim is disputed
```sql
id, entry_id, claim_entry_id FK, scheduled_amount (amount proposed to pay),
date_served, served_by_email (bool), proof_of_service_path,
reasons (JSON array of {item_ref, claimed, certified, reason, contract_clause,
evidence_refs}), locked (bool, immutable after service),
adjudication_ref (if escalated), created_at
```

**claim_items** — Line items within a claim
```sql
id, claim_entry_id FK, description, wbs_code, contract_rate,
claimed_rate, claimed_qty, claimed_amount, ca_verified_qty,
ca_verified_rate, ca_verified_amount, discrepancy (bool),
discrepancy_reason, evidence_refs (JSON array), sort_order
```

**ipc_register** — Interim Payment Certificate register (audit trail)
```sql
id, entry_id, project_entry_id FK, subcontract_entry_id FK,
claim_entry_id FK, ipc_number, date_issued, certified_amount,
retention_deducted, retention_cumulative, retention_cap,
net_payable, gst_amount, gross_payable, certified_by,
status (draft→issued→paid), pdf_path
```

**variations_subcontract** — Subcontract-level variations (separate from head contract VOs)
```sql
id, entry_id, subcontract_entry_id FK, head_contract_vo_entry_id FK (optional),
vo_number, title, description, amount, status, approved_by, approved_date,
created_at
```

**documents** — All documents linked to any entity
```sql
id, entry_id, entity_type (project/subcontract/claim/vendor/variation),
entity_entry_id, category (contract/quote/shop_drawing/inspection_report/
insurance/swms/correspondence/claim/photo), file_name, file_path,
file_hash_md5, version, date_received, source (email/upload/scan),
ai_extracted_data (JSON), created_at
```

**correspondence** — All formal communications
```sql
id, entry_id, project_entry_id FK, subcontract_entry_id FK (optional),
type (RFI/site_instruction/email/meeting_minutes/notice),
ref_number, subject, from_party, to_party, date_sent, date_replied,
date_due, status (open/closed/overdue/escalated), body_text,
attachments (JSON array), linked_entities (JSON), created_at
```

**procurement_schedule** — Live procurement tracker (from Rev9 .xlsm)
```sql
id, entry_id, project_entry_id FK, trade_name, procurement_status,
target_award, actual_award, vendor_name, contract_value, notes
```

**compliance_checks** — Audit trail of compliance verification
```sql
id, entry_id, subcontract_entry_id FK, check_type
(insurance/swms/safety/quality/license), due_date, completed_date,
status (pending/passed/failed/expired), evidence_path, notes
```

### 7.2 Index Strategy
- All `entry_id` columns (UNIQUE)
- All FK columns (`project_entry_id`, `subcontract_entry_id`, `vendor_entry_id`, etc.)
- `status` columns (common filter)
- `date_submitted`, `date_sent` (time-range queries)
- `trade_name`, `company_name` (search)

---

## 8. AI Agent Detailed Design

### 8.1 Document Ingestion Agent

**Trigger:** New document uploaded or email received with attachment

**Pipeline:**
```
PDF/Excel/.msg → FileClassifier (type detection)
  → Gemini Flash vision (PDF → structured text, multi-page)
  → EntityLinker (match to project/vendor/subcontract)
  → FieldExtractor (specific fields based on document type)
  → ConfidenceScorer (high/medium/low per field)
  → UI presents extracted data for human confirmation (Confirm-execute tier)
```

**Document types handled:**
- Subcontract agreements → extracts parties, dates, amounts, clauses
- Insurance certificates → extracts insurer, coverage, expiry
- Progress claims → extracts period, claimed amount, line items
- Quotes → extracts vendor, pricing, scope
- Payment advises → extracts certified, retention, net payable
- Inspection reports → extracts defects, test results

### 8.2 Tender Analysis Agent

**Trigger:** All quotes received for a trade package

**Pipeline:**
```
Quotes (3-5 PDFs) → QuoteExtractor (parallel, one per quote)
  → BidLeveller (standardize line items for comparison)
  → ComplianceChecker (verify against tender requirements)
  → AnomalyDetector (flag unusually low/high rates, missing items)
  → ScorerEngine (technical + commercial + schedule scoring)
  → RecommendationBuilder (ranked recommendation with evidence)
```

**Output:** Tender Analysis spreadsheet + Recommendation for Award draft

### 8.3 Claim Validation Agent

**Trigger:** Subcontractor submits monthly payment claim (with or without SOPA endorsement)

**Pipeline (mirrors the CA's actual verification process):**

```
Payment Claim Received (PDF/Email)
  │
  ├── STEP 0: SOPA Triage (Day 1 — CRITICAL)
  │     ├── Scan for SOPA endorsement language
  │     ├── If YES → Flag as STATUTORY CLAIM → start 10-business-day clock
  │     │     → Escalate priority in dashboard
  │     │     → Auto-diarise Day-10 deadline
  │     └── If NO → Standard commercial claim (contract terms govern)
  │
  ├── STEP 1: Claim Extraction (Day 1)
  │     ├── Gemini Flash: extract all line items, amounts, period
  │     ├── Extract subcontractor ref, claim number
  │     └── Output: structured claim with confidence scores per field
  │
  ├── STEP 2: Accountant Pre-Review (Day 1-2) ← ACCOUNTANT'S ROLE
  │     ├── Auto-cost-code: map line items to WBS
  │     ├── Budget check: committed + this claim vs. budget line
  │     ├── 3-way match prep: claim ↔ PO/subcontract ↔ budget
  │     ├── Flag: "over budget", "no PO match", "uncoded item"
  │     └── Accountant marks: "Ready for CA review" or "Return to subcontractor"
  │
  ├── STEP 3: CA Technical Verification (Day 2-6) ← CA'S ROLE
  │     ├── ContractChecker: are claimed items in scope?
  │     ├── RateChecker: does every claimed rate match contract schedule?
  │     ├── QuantityChecker: do claimed quantities match measured actuals?
  │     ├── ProgressChecker: does claimed % match site inspection evidence?
  │     ├── DuplicateChecker: same item claimed in prior period?
  │     ├── ComplianceChecker: insurance current? SWMS submitted? VO signed?
  │     ├── RetentionCalculator: deterministic Python math
  │     └── EvidenceLinker: for each disputed item, suggest linked evidence
  │           (site photos, inspection reports, contract clauses)
  │
  ├── STEP 4: Response Drafting (Day 6-8)
  │     ├── IF ALL CLEAN:
  │     │     → Draft Payment Certificate (IPC)
  │     │     → Populate with all calculations
  │     │     → Route to CA for review and signature
  │     │
  │     └── IF DISPUTED:
  │           → Draft Payment Schedule
  │           → For EACH disputed item: reason, contract clause, evidence ref
  │           → Calculate scheduled amount (undisputed portion)
  │           → REMIND CA: "You are locking in these reasons — new reasons cannot
  │             be added later at adjudication (Multiplex v Luikens)"
  │           → Route to CA for review, signature, and service
  │
  └── STEP 5: Post-Certification (Day 8+)
        ├── Record payment schedule / certificate as served
        ├── Update cash flow forecast
        ├── If disputed → diarise adjudication deadlines
        └── Track: has scheduled amount been paid by due date?
```

**Anomaly flags (auto-detected, presented to CA for confirmation):**

| Flag | Detection Method | Severity |
|------|-----------------|----------|
| SOPA endorsement detected | Pattern match on statutory language | **CRITICAL** — jump queue, start clock |
| Claimed completion % exceeds site evidence | Compare claimed % vs. last inspection report % | High |
| Line item rate differs from contract schedule | Rate extraction vs. contract rate DB | High |
| Previously certified item re-claimed | Match claim_items.description across periods | High |
| Total-to-date exceeds scheduled value without variation | Cumulative math vs. SOV | High |
| Variation claimed without signed VO | Cross-reference variations table | High |
| Insurance certificate expired | Compliance register check | **CRITICAL** — hold all payment |
| SWMS not submitted for claimed work | Compliance register check | Medium |
| Stored materials claimed without delivery docket | Document registry check | Medium |
| Claim total > remaining contract value | Basic math | Critical |
| Retention not applied by subcontractor | Check if claimed = gross (no deduction) | Medium |
| Same vendor has disputed claim on another project | Cross-project vendor analysis | Medium |

**Trust tier for claim validation:**

| Operation | Tier | Rule |
|-----------|------|------|
| Data extraction from PDF | Auto-execute | AI extracts, populates fields |
| SOPA endorsement detection | Auto-execute | Pattern match — no AI needed |
| Cost coding suggestion | Confirm-execute | AI suggests WBS codes; accountant confirms |
| Rate comparison | Auto-execute | Pure data matching, no judgment |
| Quantity/Progress verification | Propose-review | AI flags discrepancies; CA confirms based on site evidence |
| Payment Certificate amount | **Alert-only** | CA must manually certify the final dollar figure |
| Payment Schedule reasons | Propose-review | AI drafts reasons and evidence links; CA reviews, adds, signs |
| Payment Schedule service | **Alert-only** | CA must personally serve; system only tracks deadlines

### 8.4 Compliance Monitor Agent

**Runs:** On schedule (daily) + on event (new claim received, document expiry, date change)

**SOPA-Specific Checks (daily — non-negotiable):**

| Check | Frequency | Alert Level | Action |
|-------|-----------|-------------|--------|
| Payment Schedule not served, Day 8 of 10 | Daily | **CRITICAL** | Red alert on CA dashboard; email + platform notification |
| Payment Schedule not served, Day 10 (last day) | Hourly | **CRITICAL** | Escalate to CA + Project Manager + Commercial Manager |
| Payment Schedule deadline MISSED | On breach | **EMERGENCY** | Notify CA, PM, Commercial Manager, Legal — full amount is now statutory debt. Do not pass go. |
| Scheduled Amount not paid, 15 business days from claim | Daily | High | Alert: subcontractor can suspend work or file for adjudication |
| Adjudication application received | On receipt | **CRITICAL** | 10-business-day adjudication response clock starts NOW |

**General Compliance Checks:**

| Check | Frequency | Alert |
|-------|-----------|-------|
| Insurance expiry < 30 days | Daily | Email to CA + vendor |
| Missing SWMS for active subcontract | Weekly | Flag in dashboard |
| Unanswered RFI past due date | Daily | Escalation email |
| Claim not certified within 14 days of receipt (AS 4000 Clause 37.2) | Daily | Deadline alert to CA |
| Securities not received 14 days post-execution | Daily | Flag |
| Vendor prequalification expired | Weekly | Flag in vendor CRM |
| Lien waiver not received post-payment | Weekly | Flag — subcontractor can file lien without it |
| Retention release condition met (PC achieved) but not yet released | Weekly | Reminder to CA |
| Defects Liability Period expiring < 30 days — final account pending | Weekly | Flag for close-out action |

### 8.5 Correspondence Drafter Agent

**Trigger:** User requests draft (or auto-triggered by workflow)

**Templates with AI filling:**
- Payment certificate cover letter
- RFI response
- Claim query/dispute letter
- Variation submission email (integrated with existing variation_service)
- Back charge notice
- Extension of Time response
- Pre-construction kick-off meeting agenda
- Monthly status update to client

### 8.6 Anomaly Detection Agent

**Runs:** Weekly (or on demand)

**Detection Patterns:**
| Category | Pattern | Severity |
|----------|---------|----------|
| Cost | Claim rate >15% above contract schedule without variation | High |
| Cost | Cumulative claims exceed contract sum | Critical |
| Schedule | Subcontractor claiming ahead of program by >10% | Medium |
| Vendor | Multiple disputes across projects in last 6 months | Medium |
| Vendor | Sudden increase in claim frequency (cash flow stress signal) | High |
| Compliance | Insurance lapsed while work continues on site | Critical |
| Pattern | Same line item description claimed in consecutive months (duplicate) | High |
| Cost | Variation-to-original-contract ratio > 20% (poor scope definition) | Medium |

---

## 9. What We Will NOT Build (Scope Boundaries)

| Out of Scope | Why |
|---|---|
| Full accounting/ERP (general ledger, payroll, BAS) | Use Jobpac/Xero integration; this system is contract admin, not accounting |
| BIM/3D model viewer | Specialized tool; integrate via link only |
| Safety incident management system | Separate domain; link to existing safety platforms |
| Mobile field app (native) | WebUI is responsive; WebUI serves field tablets adequately |
| Real-time IoT/sensor integration | Not needed for this project type |
| Multi-currency / international projects | ARCO is WA-only; design for future extension but don't build |
| E-signature integration (DocuSign) | Nice-to-have for Phase 5, not core |
| Public API / third-party developer platform | Internal tool; FastAPI endpoints sufficient |

---

## 10. Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Document ingestion accuracy | > 90% fields correct (vs. human verification) | Per-field confidence scoring |
| Claim processing time | From submission → certificate in < 3 days (vs. 14-day AS 4000 deadline) | Timestamp delta |
| Compliance gaps caught | 100% of insurance expiries flagged 30+ days before | Alert log |
| Anomaly detection precision | > 70% of flagged anomalies confirmed real by human | False positive rate |
| CA time savings | 60%+ reduction in manual data entry and document filing | Time study (before/after) |
| Dispute prevention | Claims certified first-pass without query > 80% | Claim status distribution |

---

## 11. File Structure (in repo)

```
shared/src/shared_tools/
├── subcontractor_db.py         ← Database schema + CRUD (following variation_db.py pattern)
├── subcontractor_service.py    ← Business logic service (QObject + signals)
├── subcontractor_agent.py      ← AI agents (ingestion, analysis, validation, compliance, drafting, anomaly)
├── subcontractor_template.py   ← Excel generators (tender analysis, payment certs, reports)

knowledge/
├── subcontractor_system_architecture.md  ← THIS DOCUMENT
├── subcontractor_template.xlsx           ← Payment certificate template (AS 4000 format)
├── tender_analysis_template.xlsx        ← Tender comparison matrix

tools/
├── subcontractor/              ← PyQt6 GUI + FastAPI routes (future)

data/  (in LILAMY_DATA_DIR, gitignored)
├── subcontractor.db            ← Production database
└── subcontractor_docs/         ← Ingested document cache (by project/subcontract)
```

---

*This architecture is designed to be built incrementally — each phase produces a working, usable system. Phase 1 alone delivers value (structured subcontract database + document ingestion). Each subsequent phase adds intelligence without breaking previous functionality.*
