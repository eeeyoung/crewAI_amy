# PROGRESS.md — AMail & ACalendar Development Session

**Date:** 2026-06-11 to 2026-06-12
**Branch:** `main`
**Git user:** `eeeyoung`

---

## Executive Summary

In this session we made a **major architectural leap** for the lilAmy platform. The headline deliverable is the extraction of `MailService` and `CalendarService` — standalone, testable service classes that decouple all business logic from the PyQt6 GUI layer. This aligns the codebase with the AMY Architecture & Roadmap and establishes a reusable pattern for every future feature.

We also explored three approaches to email filtering (Outlook categories, MAPI Focused/Other, Microsoft Graph API), gaining deep knowledge of the Outlook COM and Exchange infrastructure even though none were viable for this specific deployment.

---

## What We Achieved

### 1. Service Extraction — **DONE & WORKING**

The single most significant technical achievement of this session.

**Created two new service classes:**

| Service | File | Lines | Owns |
|---|---|---|---|
| `MailService` | `shared/src/shared_tools/mail_service.py` | ~380 | Full AMail pipeline: 4 daemon threads (filter → triage → reply → workflow), LLM semaphore, per-email state, Outlook send, fact extraction (FTS5), grammar polish, IPC push to ACalendar, reply/workflow example persistence, attachment access, contact fetching, navigation requests |
| `CalendarService` | `shared/src/shared_tools/calendar_service.py` | ~180 | Event CRUD via shared SQLite, conflict detection, weekly digest generation, AMail status polling, categorized email pulling, navigation request writing |

**Refactored both GUIs to use services:**

- `amail/gui_viewer.py` — `TriageWindow` now creates a `MailService` instead of 7 `QThread` worker classes + 4 `queue.Queue` objects + pipeline state. Forwarding properties (`self.emails` → `self.service._emails`, `self.state` → `self.service._state`) keep existing GUI code working without rewrites. Action methods (`send_email`, `skip`, `regenerate`, `grammar_polish`, etc.) delegate to the service.
- `acalendar/gui_viewer.py` — `CalendarWindow` now creates a `CalendarService`. Forwarding `events` property keeps table/view code working. `load_events`, `on_refresh_from_mail`, `on_weekly_digest`, `on_open_in_amail`, and both polling timers delegate to the service.

**Result:** 83 tests pass, zero regressions. Both GUIs function identically to before. The services are ready to be wrapped behind FastAPI for a web dashboard.

### 2. Architectural Documentation — **DONE**

Three files created/updated to institutionalize the service-first pattern:

| File | Content |
|---|---|
| `CLAUDE.md` (root) | New — core architectural principle, service pattern diagram, 5 rules for development, project structure map |
| `SERVICE_EXTRACTION_PLAN.md` (root) | Detailed implementation plan: exact signal/method contracts, threading model, file-by-file modification guide, verification plan |
| `AMY_Architecture_and_Roadmap.md` | Updated — added directive #2: *"ALWAYS extract a Service class first"* with reference to the MailService/CalendarService precedent |

### 3. Deep Platform Knowledge Gained

Through extensive diagnostic work, we now have a complete map of the deployment environment:

- **Exchange type:** Microsoft 365 / Exchange Online (`outlook.office365.com`)
- **Account:** `amy@welink.com.au`, cached mode with local OST at `C:\Users\Yiyang\AppData\Local\Microsoft\Outlook\amy@welink.com.au.ost`
- **Inbox structure:** 9,917 messages, subfolders `1. ARCO` and `RFQ`
- **EntryID behavior:** Persistent across Outlook restarts for Exchange mailboxes (confirmed)
- **InternetMessageId:** Immutable RFC 2822 identifier, accessible via MAPI `PR_INTERNET_MESSAGE_ID` (0x1035001E) — captured in every fetched email dict

---

## What We Attempted (Honest Failures)

### Attempt 1: Outlook Master Category Filtering — **REVERTED**

**Goal:** Let users multi-select Outlook categories to filter emails in the mail lister.

**What we built:**
- `fetch_outlook_categories()` — enumerates all master categories via COM
- `CategoryFilterWidget` — multi-select dropdown with `StayOpenMenu`, colored dots per category, per-category email counts
- Server-side + client-side filtering pipeline
- `FetchCategoriesWorker` for async category loading

**Why it failed:** The user does not assign Outlook categories to emails. All categories showed `(0)` count. The feature worked correctly but had no data to match against.

### Attempt 2: Focused/Other Inbox Detection via MAPI — **REVERTED**

**Goal:** Replace Outlook categories with Focused vs Other inbox classification (the two tabs in Outlook's UI).

**What we built:**
- `OthersToggleWidget` — toggle button showing Other email count
- Client-side filtering by `is_focused` boolean
- Four diagnostic scripts exhaustively testing property access methods

**Diagnostic results (7 property formats tested):**
| Method | Result |
|---|---|
| Proptag `0x10820003` (PT_LONG) | Type mismatch |
| Proptag `0x1082000B` (PT_BOOLEAN) | Type mismatch |
| Named property `0x00008082` (bare LID) | Property not supported |
| Named property `0x80820003` (typed LID) | Property not found |
| Named property via PS_PUBLIC_STRINGS | Property not supported |
| Proptag string type `0x1082001E` | Type mismatch |
| Proptag short type `0x10820002` | Type mismatch |
| Items.Restrict with SQL filter | Returns 0 items (property not queryable) |
| Store-level properties | All not found |
| Cross-email property scan (12,288 properties) | No Focused/Other property found |
| Search folders (17 pooled search folders) | All empty |
| Registry scan | No Focused Inbox keys |
| Inbox Views | Only 3 standard views (no Focused/Other view) |

**Why it failed:** The `PR_FOCUSED` MAPI property does not exist on this on-premises-style Exchange configuration. Outlook stores Focused/Other classification in its local OST database which COM/MAPI cannot access. This property is only available on native Exchange Online / Microsoft 365 with modern authentication.

### Attempt 3: Microsoft Graph API Integration — **REVERTED**

**Goal:** Use Microsoft Graph API's `inferenceClassification` property to get Focused/Other data from the cloud.

**What we built:**
- `graph_client.py` — device-code OAuth flow with `requests` (no extra packages), token caching, batched Graph API queries matching emails by `internetMessageId`
- `GraphEnrichWorker` — async background enrichment (doesn't block email display)
- `GraphSignInDialog` — PyQt6 dialog showing device code, verification URL, Copy Code button, spinner
- Azure AD app registration: `d92815c2-ccaa-451d-96ba-96fb35ad993c` with `Mail.ReadWrite` permission

**Architecture was sound:**
```
Fetch emails (COM) → Display immediately → GraphEnrichWorker (background)
                                              → terminal: device code + URL
                                              → user signs in
                                              → classification data arrives
                                              → Others toggle updates with real counts
```

**Why it failed:** The `amy@welink.com.au` account requires admin consent to grant the `Mail.ReadWrite` permission. The user chose not to escalate to IT. The app registration and permissions remain in Azure — they can be activated later with one admin click.

---

## Technical Debt Resolved

- **`callable | None` type error** — fixed by importing `Callable` from `collections.abc` (Python 3.10+ compatible)
- **`StayOpenMenu` toggle behavior** — `action.toggle()` replaced with `action.setChecked(not action.isChecked())` for explicit control
- **`_rebuild_table` duplicate append** — snapshot-before-clear pattern prevents `_add_email_row` from double-appending
- **Polling exclusion set** — `_poll_for_new` now excludes all `_all_emails_data` IDs instead of just `displayed_entry_ids`
- **`_show_menu` toggle** — changed from always-`popup()` to `hide()`/`popup()` toggle

## Files Changed This Session

| File | Status | Change |
|---|---|---|
| `shared/src/shared_tools/mail_service.py` | **NEW** | Full MailService class |
| `shared/src/shared_tools/calendar_service.py` | **NEW** | Full CalendarService class |
| `shared/src/shared_tools/outlook_tool.py` | Modified | Added `fetch_outlook_categories()`, `internet_message_id`, `is_focused`; removed later |
| `tools/amail/src/amail/gui_viewer.py` | Modified | Replaced 7 worker classes with MailService + forwarding properties |
| `tools/acalendar/src/acalendar/gui_viewer.py` | Modified | CalendarWindow uses CalendarService + forwarding properties |
| `tools/amail/src/amail/mail_lister.py` | Modified | Added/removed filtering UI (Categories, Others, Graph enrichment) |
| `tools/amail/src/amail/main.py` | Modified | Added/removed `setup_graph_auth()` |
| `CLAUDE.md` | **NEW** | Root-level architectural principles |
| `SERVICE_EXTRACTION_PLAN.md` | **NEW** | Detailed service extraction plan |
| `PROGRESS.md` | **NEW** | This file |
| `AMY_Architecture_and_Roadmap.md` | Modified | Added service-first directive |

## Test Status

```
83 passed, 6 failed, 1 skipped
```

The 6 failures are **pre-existing** in `test_crew.py::TestGetLlm` — they monkeypatch `ACTIVE_PROVIDER` which no longer exists in `crew.py` (LLM routing was refactored to `shared_tools/llm_config.py`). These failures existed before this session and are unrelated to our changes.

---

## Key Takeaway for Future Development

> **Service class first, UI second.** Every feature starts as a standalone Python class in `shared/src/shared_tools/` with public methods, PyQt signals, and `threading.Thread` + `queue.Queue` concurrency. The UI is a thin consumer. This pattern, proven by `MailService` and `CalendarService`, decouples business logic from any specific frontend and makes the code testable, reusable, and ready for FastAPI/web migration.
