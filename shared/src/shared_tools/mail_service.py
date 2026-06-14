"""MailService — standalone AMail pipeline orchestrator.

Extracted from ``gui_viewer.py``.  Owns all business logic: Outlook COM
access, CrewAI pipeline invocations, state management, IPC bridge, and
fact-store operations.  Communicates with the GUI layer via PyQt signals.

Uses plain ``threading.Thread`` + ``queue.Queue`` internally — no
dependency on PyQt6 beyond the signal mechanism.
"""

import os
import json
import queue
import threading
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

_AMAIL_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "tools", "amail")
)
_KNOWLEDGE_DIR = os.path.join(_AMAIL_ROOT, "knowledge")


# ---------------------------------------------------------------------------
# MailService
# ---------------------------------------------------------------------------

class MailService(QObject):
    """Orchestrates the full AMail pipeline.

    Usage from the GUI::

        service = MailService()
        service.start()

        # connect signals
        service.filter_done.connect(gui.on_filter_done)
        service.category_ready.connect(gui.on_category_ready)
        ...

        # submit emails for processing
        indices = service.submit_emails(email_list)

        # user actions
        service.send_email(idx, recipient, cc, subject, body_html)
        service.skip_email(idx, mark_read=True)
    """

    # ---- signals (emitted from background threads, auto-queued to main) ----

    # Legacy 6-stage pipeline signals
    filter_done = pyqtSignal(int, str)              # idx, cleaned_body
    category_ready = pyqtSignal(int, str, str, str, list)  # idx, cat, urg, extra, dates
    reply_generated = pyqtSignal(int, str)          # idx, html_body
    workflow_generated = pyqtSignal(int, str)       # idx, workflow_text
    contacts_loaded = pyqtSignal(list)              # [{"name":..., "email":...}, ...]
    grammar_polished = pyqtSignal(int, str)         # idx, polished_text

    # Unified pipeline signals
    emails_loaded = pyqtSignal(list)                # initial load from DB (fast)
    new_emails_arrived = pyqtSignal(int)            # count of new emails after refresh
    summary_ready = pyqtSignal(dict)                # single email summarized {entry_id, ...}
    refresh_progress = pyqtSignal(int, int, str)    # current, total, status message
    refresh_error = pyqtSignal(str)                 # error message
    email_removed = pyqtSignal(str)                 # entry_id of removed email
    email_body_updated = pyqtSignal(str)            # entry_id of email with updated body
    sync_complete = pyqtSignal(int, int, int)       # added, removed, updated counts

    def __init__(self, parent: QObject | None = None, auto_refresh: bool = False):
        super().__init__(parent)

        # ---- state ----
        self._emails: list[dict] = []
        self._pending_emails: list[dict] = []
        self._state: dict[int, dict] = {}
        self._index_counter = 0
        self._max_active = 5
        self._processed_entry_ids: set[str] = set()
        self._skipped_indices: set[int] = set()

        # ---- fetch progress (thread-safe, polled by WebUI) ----
        self._fetch_status_lock = threading.Lock()
        self._fetch_status: dict = {
            "phase": "idle",       # idle | fetching | summarizing | complete | error
            "total": 0,
            "done": 0,
            "current": "",         # current email subject
            "message": "",
            "error": "",
            "added": 0,            # emails successfully added
        }

        # ---- queues ----
        self._filter_queue: queue.Queue = queue.Queue()
        self._triage_queue: queue.Queue = queue.Queue()
        self._reply_queue: queue.Queue = queue.Queue()
        self._workflow_queue: queue.Queue = queue.Queue()

        # ---- semaphore & threads ----
        self._llm_semaphore = threading.Semaphore(1)
        self._threads: list[threading.Thread] = []
        self._running = False
        self._contacts_cache: list[dict] = []

        # ---- auto-refresh ----
        self._auto_refresh = auto_refresh
        self._refresh_timer: object | None = None  # QTimer, set in start()
        self._latest_received_time: str | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        """Initialise DB, register IPC, launch pipeline threads."""
        if self._running:
            return
        self._running = True

        from amail.mail_knowledge import init_db
        from shared_tools.ipc_bridge import init_shared_db, register_app

        init_db()
        init_shared_db()
        register_app("amail")

        self._threads = [
            threading.Thread(target=self._run_filter_loop, daemon=True, name="svc-filter"),
            threading.Thread(target=self._run_triage_loop, daemon=True, name="svc-triage"),
            threading.Thread(target=self._run_reply_loop, daemon=True, name="svc-reply"),
            threading.Thread(target=self._run_workflow_loop, daemon=True, name="svc-workflow"),
        ]
        for t in self._threads:
            t.start()

        self._start_auto_refresh()

    def stop(self):
        """Stop all pipeline threads, auto-refresh timer, and unregister IPC."""
        self._running = False
        self._stop_auto_refresh()
        for q in (self._filter_queue, self._triage_queue,
                  self._reply_queue, self._workflow_queue):
            q.put(None)  # poison pill
        for t in self._threads:
            t.join(timeout=3)
        from shared_tools.ipc_bridge import unregister_app
        unregister_app("amail")

    # ------------------------------------------------------------------
    # Unified pipeline — single-pass summarization + auto-refresh
    # ------------------------------------------------------------------

    def load_emails_from_db(self, status: str = "active", limit: int = 100) -> list[dict]:
        """Load processed emails from the shared DB (fast, no Outlook call).
        Emits ``emails_loaded`` with the result list."""
        from shared_tools.ipc_bridge import get_processed_emails, get_latest_received_time
        emails = get_processed_emails(status=status, limit=limit)
        self._latest_received_time = get_latest_received_time()
        # Prime the session dedup set
        from shared_tools.ipc_bridge import get_processed_entry_ids
        self._processed_entry_ids = get_processed_entry_ids()
        self.emails_loaded.emit(emails)
        return emails

    def refresh_inbox(self, count: int = 50):
        """Fetch new emails from Outlook and run the unified summarizer.
        Runs in a background thread. Emits ``refresh_progress``, ``summary_ready``,
        and ``new_emails_arrived`` when complete."""
        t = threading.Thread(
            target=self._do_refresh, args=(count,),
            daemon=True, name="svc-refresh"
        )
        t.start()

    def _do_refresh(self, count: int):
        """Background: fetch Outlook → dedup → summarize → persist."""
        if not self._running:
            return

        import pythoncom; pythoncom.CoInitialize()
        from shared_tools.outlook_tool import fetch_inbox_emails
        from shared_tools.ipc_bridge import (
            get_processed_entry_ids, get_latest_received_time,
            upsert_processed_email,
        )

        def _emit(signal, *args):
            """Emit only if still running (prevents shutdown crashes)."""
            if self._running:
                signal.emit(*args)

        # 1. Load dedup set from DB
        try:
            existing_ids = get_processed_entry_ids()
            self._processed_entry_ids |= existing_ids
            self._latest_received_time = get_latest_received_time() or self._latest_received_time
        except Exception:
            pass

        # 2. Fetch from Outlook
        self._update_fetch_status(phase="fetching", total=0, done=0,
                                  added=0, current="", message="Connecting to Outlook...")
        _emit(self.refresh_progress, 0, count, "Fetching from Outlook...")
        print(f"[FETCH] _do_refresh: fetching count={count}, exclude_ids={len(self._processed_entry_ids)}, latest_received={self._latest_received_time}")
        try:
            raw_emails = fetch_inbox_emails(
                count=count, max_body=4000, unread_only=False,
                exclude_entry_ids=self._processed_entry_ids,
            )
        except Exception as e:
            _emit(self.refresh_error, f"Outlook fetch failed: {e}")
            self._update_fetch_status(phase="error", error=str(e))
            print(f"[FETCH] _do_refresh ERROR: {e}")
            return

        print(f"[FETCH] _do_refresh: Outlook returned {len(raw_emails)} emails")
        if not raw_emails:
            _emit(self.new_emails_arrived, 0)
            self._update_fetch_status(phase="complete", total=0, done=0,
                                      added=0, message="No new emails found")
            return

        # 3. Filter: skip emails older than latest stored
        new_emails = []
        for em in raw_emails:
            eid = em.get("entry_id", "")
            received = em.get("received_time", "")
            if eid in self._processed_entry_ids:
                continue
            if self._latest_received_time and received <= self._latest_received_time:
                continue
            new_emails.append(em)

        print(f"[FETCH] _do_refresh: after filtering: {len(new_emails)} new emails (out of {len(raw_emails)} raw)")
        total = len(new_emails)
        if total == 0:
            _emit(self.new_emails_arrived, 0)
            self._update_fetch_status(phase="complete", total=0, done=0,
                                      added=0, message="No new emails (all already processed)")
            return

        # 4. Summarize each new email (single LLM pass)
        summarized = 0
        for i, em in enumerate(new_emails):
            if not self._running:
                return
            subj = em.get('subject', '')[:60]
            _emit(self.refresh_progress, i + 1, total,
                  f"Summarizing [{i+1}/{total}]: {subj}...")
            print(f"[FETCH] Summarizing [{i+1}/{total}]: {subj}")
            try:
                result = self._summarize_single(em)
                if result:
                    print(f"[FETCH]   → OK: category={result.get('category')}, urgency={result.get('urgency')}, todos={len(result.get('todos',[]))}, deadlines={len(result.get('deadlines',[]))}")
                    entry = {
                        "entry_id": em.get("entry_id", ""),
                        "subject": em.get("subject", ""),
                        "sender": em.get("sender", ""),
                        "received_time": em.get("received_time", ""),
                        "body": em.get("body", ""),
                        "category": result.get("category", "General"),
                        "urgency": result.get("urgency", "low"),
                        "chinese_summary": result.get("chinese_summary", ""),
                        "assignee": result.get("assignee", ""),
                        "todos_json": json.dumps(result.get("todos", []), ensure_ascii=False),
                        "deadlines_json": json.dumps(result.get("deadlines", []), ensure_ascii=False),
                        "status": "active",
                    }
                    upsert_processed_email(entry)
                    self._processed_entry_ids.add(em.get("entry_id", ""))
                    if received := em.get("received_time", ""):
                        if not self._latest_received_time or received > self._latest_received_time:
                            self._latest_received_time = received
                    _emit(self.summary_ready, entry)
                    summarized += 1
                else:
                    print(f"[FETCH]   → FAILED (summarizer returned None)")
            except Exception as e:
                _emit(self.refresh_error, f"Summarize failed for {em.get('subject', 'Unknown')[:40]}: {e}")
                print(f"[FETCH]   → ERROR: {e}")

        print(f"[FETCH] _do_refresh COMPLETE: {summarized} summarized of {total} new")
        self._update_fetch_status(phase="complete", added=summarized,
                                  message=f"{summarized} new email(s) processed")
        _emit(self.new_emails_arrived, summarized)

    def _summarize_single(self, email: dict) -> dict | None:
        """Run the unified summarizer on a single email.
        Returns a dict with chinese_summary, category, urgency, assignee, todos, deadlines
        or None on failure."""
        from amail.crew import UnifiedSummarizerCrew
        inputs = {
            "email_subject": email.get("subject", ""),
            "email_sender": email.get("sender", ""),
            "email_content": email.get("body", ""),
        }
        with self._llm_semaphore:
            result = UnifiedSummarizerCrew().crew().kickoff(inputs=inputs)
        raw = result.raw if hasattr(result, 'raw') else str(result)

        # Parse JSON from the output (same pattern as ASummary)
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
            cleaned = cleaned.rsplit("```", 1)[0]
        if cleaned.startswith("{{") and cleaned.endswith("}}"):
            cleaned = cleaned[1:-1]
        try:
            return json.loads(cleaned.strip())
        except json.JSONDecodeError:
            return {
                "chinese_summary": raw[:200],
                "category": "General",
                "urgency": "low",
                "assignee": "Unknown",
                "todos": [],
                "deadlines": [],
            }

    def remove_email(self, entry_id: str):
        """Soft-delete an email from the processed store."""
        from shared_tools.ipc_bridge import remove_processed_email
        remove_processed_email(entry_id)
        self._processed_entry_ids.discard(entry_id)

    # ── Fetch modes ────────────────────────────────────────────────────

    def fetch_earlier(self, count: int = 30):
        """Fetch UNREAD emails OLDER than the earliest stored email.
        Fills backward gaps in the timeline."""
        t = threading.Thread(target=self._do_fetch_earlier, args=(count,),
                            daemon=True, name="svc-fetch-earlier")
        t.start()

    def _do_fetch_earlier(self, count: int):
        if not self._running:
            return
        import pythoncom; pythoncom.CoInitialize()
        from shared_tools.outlook_tool import fetch_inbox_emails
        from shared_tools.ipc_bridge import (
            get_earliest_received_time, get_processed_entry_ids,
            upsert_processed_email,
        )

        def _emit(sig, *args):
            if self._running:
                sig.emit(*args)

        # Prime dedup + earliest watermark
        existing_ids = get_processed_entry_ids()
        self._processed_entry_ids |= existing_ids
        earliest = get_earliest_received_time()

        try:
            if earliest is None:
                # Empty storage — fetch oldest unread emails first (ascending)
                _emit(self.refresh_progress, 0, count,
                      "Storage empty — fetching oldest unread emails first...")
                raw_emails = fetch_inbox_emails(
                    count=count, max_body=4000, unread_only=True,
                    exclude_entry_ids=self._processed_entry_ids,
                    ascending=True,  # oldest first
                )
            else:
                _emit(self.refresh_progress, 0, count,
                      f"Fetching unread emails older than {earliest[:19]}...")
                raw_emails = fetch_inbox_emails(
                    count=count, max_body=4000, unread_only=True,
                    exclude_entry_ids=self._processed_entry_ids,
                    received_before=earliest,  # older than earliest stored
                )
        except Exception as e:
            _emit(self.refresh_error, f"Fetch earlier failed: {e}")
            return

        if not raw_emails:
            _emit(self.new_emails_arrived, 0)
            return

        summarized = self._summarize_batch(raw_emails, _emit)
        self._update_fetch_status(phase="complete", added=summarized,
                                  message=f"{summarized} earlier email(s) processed")
        _emit(self.new_emails_arrived, summarized)

    def fetch_latest(self, count: int = 30):
        """Fetch UNREAD emails NEWER than the latest stored email.
        Same as refresh_inbox but keeps the name semantic clear."""
        self.refresh_inbox(count=count)

    def sync_inbox(self, from_date: str = None, to_date: str = None):
        """Fill gaps between from_date and to_date. Uses storage range if not provided."""
        t = threading.Thread(target=self._do_sync, args=(from_date, to_date),
                           daemon=True, name="svc-sync")
        t.start()

    def _do_sync(self, from_date: str = None, to_date: str = None):
        """Fill gaps: find unread Outlook emails in the date range
        that aren't in storage yet, and add them."""
        if not self._running:
            return
        import pythoncom
        pythoncom.CoInitialize()
        from shared_tools.outlook_tool import fetch_inbox_emails
        from shared_tools.ipc_bridge import (
            get_earliest_received_time, get_latest_received_time,
            get_processed_entry_ids, get_active_entry_ids_in_range,
            upsert_processed_email, get_processed_emails,
        )

        def _emit(sig, *args):
            if not self._running:
                return
            try:
                sig.emit(*args)
            except Exception:
                pass

        added = 0
        body_updated = 0

        # 1. Determine date range: use custom if provided, else storage range
        print("[SYNC] Step 1/2: Loading storage state...")
        existing_ids = get_processed_entry_ids()
        self._processed_entry_ids |= existing_ids
        active_count = len(get_processed_emails(status="active", limit=0))
        removed_count = len(get_processed_emails(status="removed", limit=0))
        print(f"[SYNC]   Active: {active_count}, Removed: {removed_count}")

        if from_date and to_date:
            earliest = from_date
            latest = to_date
            print(f"[SYNC]   Custom range: {earliest[:19]} → {latest[:19]}")
        else:
            earliest = get_earliest_received_time()
            latest = get_latest_received_time()
            print(f"[SYNC]   Storage range: {earliest[:19] if earliest else 'N/A'} → {latest[:19] if latest else 'N/A'}")

        # 2. Fill gaps
        if earliest and latest:
            print("[SYNC] Step 2/2: Filling gaps...")
            try:
                storage_ids = get_active_entry_ids_in_range(earliest, latest) if not from_date else get_processed_entry_ids()
                print(f"[SYNC]   Active IDs in range: {len(storage_ids)}")
                gap_emails = fetch_inbox_emails(
                    count=200, max_body=4000, unread_only=True,
                    exclude_entry_ids=storage_ids,
                    received_after=earliest, received_before=latest,
                )
                print(f"[SYNC]   Unread emails found in range: {len(gap_emails)}")
                if gap_emails:
                    added += self._summarize_batch(gap_emails, _emit)
                    print(f"[SYNC]   Added {added} new emails to storage")
                else:
                    print("[SYNC]   No new emails to add — storage is up to date")
            except Exception as e:
                print(f"[SYNC]   ERROR: {e}")
        else:
            print("[SYNC] Step 2/2: Skipped — no date range to fill")

        # 3. Update bodies
        print("[SYNC] Updating email bodies...")
        all_active = get_processed_emails(status="active", limit=0)
        try:
            import win32com.client
            outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
            for i, em in enumerate(all_active):
                if not self._running:
                    break
                if not em.get("body"):
                    try:
                        msg = outlook.GetItemFromID(em["entry_id"])
                        body = getattr(msg, "Body", "")[:5000]
                        if body:
                            em["body"] = body
                            upsert_processed_email(em)
                            _emit(self.email_body_updated, em["entry_id"])
                            body_updated += 1
                    except Exception:
                        pass
        except Exception:
            pass

        _emit(self.sync_complete, added, 0, body_updated)

    def get_fetch_status(self) -> dict:
        """Thread-safe read of current fetch progress. Polled by WebUI."""
        with self._fetch_status_lock:
            return dict(self._fetch_status)

    def _update_fetch_status(self, **kwargs):
        """Thread-safe update of fetch progress."""
        with self._fetch_status_lock:
            self._fetch_status.update(kwargs)

    def _summarize_batch(self, emails: list[dict], _emit) -> int:
        """Summarize a batch of emails and persist. Returns count added."""
        from shared_tools.ipc_bridge import upsert_processed_email
        summarized = 0
        total = len(emails)

        self._update_fetch_status(phase="summarizing", total=total, done=0,
                                  added=0, current="", message="")

        for i, em in enumerate(emails):
            if not self._running:
                break
            subject = em.get('subject', '')[:50]
            _emit(self.refresh_progress, i + 1, total,
                  f"Summarizing [{i+1}/{total}]: {subject}...")
            self._update_fetch_status(
                done=i + 1,
                current=subject,
                message=f"Summarizing [{i+1}/{total}]"
            )
            try:
                result = self._summarize_single(em)
                if result:
                    entry = {
                        "entry_id": em.get("entry_id", ""),
                        "subject": em.get("subject", ""),
                        "sender": em.get("sender", ""),
                        "received_time": em.get("received_time", ""),
                        "body": em.get("body", ""),
                        "category": result.get("category", "General"),
                        "urgency": result.get("urgency", "low"),
                        "chinese_summary": result.get("chinese_summary", ""),
                        "assignee": result.get("assignee", ""),
                        "todos_json": json.dumps(result.get("todos", []), ensure_ascii=False),
                        "deadlines_json": json.dumps(result.get("deadlines", []), ensure_ascii=False),
                        "status": "active",
                    }
                    upsert_processed_email(entry)
                    self._processed_entry_ids.add(em.get("entry_id", ""))
                    if received := em.get("received_time", ""):
                        if not self._latest_received_time or received > self._latest_received_time:
                            self._latest_received_time = received
                    _emit(self.summary_ready, entry)
                    summarized += 1
            except Exception as e:
                _emit(self.refresh_error,
                      f"Summarize failed for {em.get('subject', 'Unknown')[:40]}: {e}")
        self._update_fetch_status(added=summarized)
        return summarized

    # ── auto-refresh timer ─────────────────────────────────────────────

    def _start_auto_refresh(self):
        """Start the 10-minute refresh timer (requires a running QApplication)."""
        if not self._auto_refresh:
            return
        try:
            from PyQt6.QtCore import QTimer
            self._refresh_timer = QTimer()
            self._refresh_timer.timeout.connect(lambda: self.refresh_inbox(count=50))
            self._refresh_timer.start(600_000)  # 10 minutes
        except Exception:
            pass  # no QApplication — timer won't work (CLI mode)

    def _stop_auto_refresh(self):
        """Stop the refresh timer if active."""
        if self._refresh_timer is not None:
            try:
                self._refresh_timer.stop()
            except Exception:
                pass
            self._refresh_timer = None

    # ------------------------------------------------------------------
    # Email submission & state access
    # ------------------------------------------------------------------

    def submit_emails(self, emails: list[dict]) -> list[int]:
        """Add *emails* to the processing pipeline.  Returns assigned indices."""
        indices = []
        for email in emails:
            eid = email.get("entry_id", "")
            if eid:
                self._processed_entry_ids.add(eid)
            idx = self._append_email(email)
            indices.append(idx)
        self._promote_pending()
        return indices

    def get_state(self, idx: int) -> dict | None:
        """Return the mutable processing-state dict for *idx*.
        Changes made by the caller are reflected in the service."""
        return self._state.get(idx)

    def get_email(self, idx: int) -> dict | None:
        """Return the mutable raw-email dict for *idx*."""
        if 0 <= idx < len(self._emails):
            return self._emails[idx]
        return None

    def email_count(self) -> int:
        return len(self._emails)

    def active_count(self) -> int:
        return sum(
            1 for st in self._state.values()
            if st.get("send_status") not in ("sent", "skipped")
        )

    def pending_count(self) -> int:
        return len(self._pending_emails)

    def all_done(self) -> bool:
        if not self._emails and not self._pending_emails:
            return True
        return all(
            st.get("send_status") in ("sent", "skipped")
            for st in self._state.values()
        ) and not self._pending_emails

    @property
    def processed_entry_ids(self) -> set[str]:
        return self._processed_entry_ids

    @processed_entry_ids.setter
    def processed_entry_ids(self, value: set[str]):
        self._processed_entry_ids = value

    # ------------------------------------------------------------------
    # User actions (called from GUI)
    # ------------------------------------------------------------------

    def send_email(self, idx: int, recipient: str, cc: str,
                   subject: str, body_html: str) -> bool:
        """Send reply via Outlook and mark original as read."""
        from shared_tools.outlook_tool import (
            OutlookSendTool, mark_email_as_read,
        )
        sig_path = os.path.join(_KNOWLEDGE_DIR, "amy_signature.html")
        img_specs = [
            (os.path.join(_KNOWLEDGE_DIR, "logo_meritor_welink.png"), "logo_meritor_welink.png"),
            (os.path.join(_KNOWLEDGE_DIR, "logo_hia_awards.png"), "logo_hia_awards.png"),
            (os.path.join(_KNOWLEDGE_DIR, "icon_instagram.png"), "icon_instagram.png"),
            (os.path.join(_KNOWLEDGE_DIR, "icon_facebook.png"), "icon_facebook.png"),
        ]
        tool = OutlookSendTool(
            signature_html_path=sig_path if os.path.exists(sig_path) else "",
            signature_image_specs=[(p, c) for p, c in img_specs if os.path.exists(p)],
        )
        result = tool._run(
            recipient=recipient, subject=subject, body=body_html,
            cc=cc, is_html=True,
        )
        if "successfully sent" in result.lower():
            entry_id = self._emails[idx].get("entry_id", "")
            if entry_id:
                mark_email_as_read(entry_id)
                self._processed_entry_ids.add(entry_id)
            self._state[idx]["send_status"] = "sent"
            self._state[idx]["reply_text"] = body_html
            self._promote_pending()
            return True
        return False

    def skip_email(self, idx: int, mark_read: bool = False):
        """Skip an email, optionally marking it as read in Outlook."""
        entry_id = self._emails[idx].get("entry_id", "")
        if mark_read and entry_id:
            from shared_tools.outlook_tool import mark_email_as_read
            mark_email_as_read(entry_id)
        if entry_id:
            self._processed_entry_ids.add(entry_id)

        self._skipped_indices.add(idx)
        st = self._state[idx]
        for key in ("send_status", "filter_status", "category_status",
                     "reply_status", "workflow_status"):
            st[key] = "skipped"
        st["filtered_body"] = st.get("filtered_body") or "Skipped"
        st["category"] = st.get("category") or "Skipped"
        st["urgency"] = st.get("urgency") or "Skipped"
        st["extra_info"] = st.get("extra_info") or "Skipped"
        st["reply_text"] = "Skipped"
        st["workflow_text"] = st.get("workflow_text") or "Skipped"
        self._promote_pending()

    def regenerate(self, idx: int):
        """Re-run from the earliest failed stage for *idx*."""
        st = self._state[idx]
        email = self._emails[idx]

        if st.get("filtered_body", "").startswith("Error filtering"):
            st["filter_status"] = "filtering"
            st["category_status"] = "pending"
            st["reply_status"] = "pending"
            st["filtered_body"] = ""
            st["category"] = ""
            st["reply_text"] = ""
            self._filter_queue.put((idx, email))

        elif st.get("category") == "Error":
            st["category_status"] = "thinking"
            st["reply_status"] = "pending"
            st["category"] = ""
            st["reply_text"] = ""
            self._triage_queue.put((
                idx, email, st.get("filtered_body", email.get("body", "")),
            ))
            # also re-enqueue for reply after triage
            self._reply_queue.put((
                idx, email, st.get("filtered_body", email.get("body", "")),
                st["category"], st["urgency"], st["extra_info"], st.get("dates", []),
            ))

        else:
            st["reply_status"] = "generating"
            st["reply_text"] = ""
            self._reply_queue.put((
                idx, email, st.get("filtered_body", email.get("body", "")),
                st["category"], st["urgency"], st["extra_info"], st.get("dates", []),
            ))

    def polish_grammar(self, idx: int, draft_text: str):
        """Start async grammar-polish for the given draft text."""
        t = threading.Thread(
            target=self._run_grammar_polish,
            args=(idx, draft_text),
            daemon=True,
            name="svc-grammar",
        )
        t.start()

    def extract_facts(self, idx: int) -> list[dict]:
        """Run FactExtractorCrew and save results.  Returns extracted facts."""
        from amail.crew import FactExtractorCrew
        st = self._state[idx]
        email = self._emails[idx]
        inputs = {
            "email_subject": email["subject"],
            "email_content": st.get("filtered_body") or email["body"],
            "email_category": st.get("category", ""),
            "email_context": st.get("extra_info", ""),
        }
        result = FactExtractorCrew().crew().kickoff(inputs=inputs)
        raw = result.raw if hasattr(result, "raw") else str(result)
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        facts = json.loads(cleaned)
        if not isinstance(facts, list):
            facts = []
        if facts:
            from amail.mail_knowledge import save_facts
            save_facts(facts, email.get("subject", ""), email.get("sender", ""))
        return facts

    def save_reply_example(self, idx: int, text: str):
        """Save the current reply as a training example."""
        st = self._state[idx]
        email = self._emails[idx]
        os.makedirs(_KNOWLEDGE_DIR, exist_ok=True)
        path = os.path.join(_KNOWLEDGE_DIR, "reply_examples.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "email_subject": email.get("subject", ""),
                "category": st.get("category", ""),
                "urgency": st.get("urgency", ""),
                "extra_info": st.get("extra_info", ""),
                "expected_reply": text,
            }) + "\n")

    def save_workflow_example(self, idx: int, text: str):
        """Save the current workflow as a training example."""
        st = self._state[idx]
        email = self._emails[idx]
        os.makedirs(_KNOWLEDGE_DIR, exist_ok=True)
        path = os.path.join(_KNOWLEDGE_DIR, "workflow_examples.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "email_subject": email.get("subject", ""),
                "category": st.get("category", ""),
                "urgency": st.get("urgency", ""),
                "extra_info": st.get("extra_info", ""),
                "expected_workflow": text,
            }) + "\n")

    def get_attachments(self, idx: int) -> list[dict]:
        """Return attachment metadata for the given email index."""
        entry_id = self._emails[idx].get("entry_id", "")
        if not entry_id:
            return []
        from shared_tools.outlook_tool import fetch_attachments_for_email
        return fetch_attachments_for_email(entry_id)

    def save_attachment(self, idx: int, att_index: int, save_dir: str) -> str:
        entry_id = self._emails[idx].get("entry_id", "")
        from shared_tools.outlook_tool import save_attachment
        return save_attachment(entry_id, att_index, save_dir)

    def fetch_contacts_async(self):
        """Start async contact fetch; results via ``contacts_loaded`` signal."""
        t = threading.Thread(target=self._run_contact_fetch, daemon=True, name="svc-contacts")
        t.start()

    def check_nav_request(self) -> str | None:
        """Check if ACalendar requested navigation to a specific email.
        Returns the target ``EntryID`` or ``None``."""
        from shared_tools.ipc_bridge import CREWAI_DIR
        nav_path = CREWAI_DIR / "nav_request.json"
        if not nav_path.exists():
            return None
        try:
            data = json.loads(nav_path.read_text(encoding="utf-8"))
            target = data.get("target_entry_id")
            nav_path.unlink(missing_ok=True)
            return target
        except Exception:
            try:
                nav_path.unlink(missing_ok=True)
            except Exception:
                pass
            return None

    # ------------------------------------------------------------------
    # Internal: pipeline thread loops
    # ------------------------------------------------------------------

    def _run_filter_loop(self):
        while self._running:
            try:
                item = self._filter_queue.get(timeout=1)
            except queue.Empty:
                continue
            if item is None:
                break
            idx, email = item
            if idx in self._skipped_indices:
                continue
            try:
                with self._llm_semaphore:
                    from amail.crew import MessageFilterCrew
                    result = MessageFilterCrew().crew().kickoff(inputs={
                        "email_body": email["body"],
                        "email_subject": email["subject"],
                        "email_sender": email["sender"],
                        "email_received_time": email.get("received_time", "Unknown"),
                    })
                cleaned = result.raw if hasattr(result, "raw") else str(result)
            except Exception as e:
                cleaned = f"Error filtering: {e}"

            if idx in self._skipped_indices:
                continue
            self._state[idx]["filtered_body"] = cleaned
            self._state[idx]["filter_status"] = "done"
            self._state[idx]["category_status"] = "thinking"
            self.filter_done.emit(idx, cleaned)
            self._triage_queue.put((idx, email, cleaned))

    def _run_triage_loop(self):
        while self._running:
            try:
                item = self._triage_queue.get(timeout=1)
            except queue.Empty:
                continue
            if item is None:
                break

            if len(item) == 3:
                idx, email, filtered_body = item
            else:
                idx, email, filtered_body, *_ = item

            if idx in self._skipped_indices:
                continue

            inputs = {
                "email_subject": email["subject"],
                "email_sender": email["sender"],
                "email_content": filtered_body,
            }
            category = "Uncategorized"
            urgency = ""
            extra_info = ""
            dates = []

            try:
                with self._llm_semaphore:
                    from amail.crew import TriageSingleCrew
                    result = TriageSingleCrew().crew().kickoff(inputs=inputs)
                raw = result.raw if hasattr(result, "raw") else str(result)
                try:
                    cleaned = raw.strip()
                    if cleaned.startswith("```"):
                        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0]
                    parsed = json.loads(cleaned.strip())
                    category = parsed.get("category", "Uncategorized")
                    urgency = parsed.get("urgency", "")
                    extra_info = parsed.get("extra_info", "")
                    dates = parsed.get("dates", [])
                except (json.JSONDecodeError, AttributeError):
                    category = raw[:100]
                    extra_info = "Could not parse structured output"
            except Exception as e:
                category = "Error"
                extra_info = str(e)

            if idx in self._skipped_indices:
                continue

            self._state[idx].update({
                "category": category, "urgency": urgency,
                "extra_info": extra_info, "dates": dates,
                "category_status": "done", "reply_status": "generating",
            })
            self.category_ready.emit(idx, category, urgency, extra_info, dates)

            # Push to IPC for ACalendar
            try:
                from shared_tools.ipc_bridge import push_categorized_email, push_calendar_events
                push_categorized_email({
                    "email_entry_id": email.get("entry_id", ""),
                    "email_subject": email.get("subject", ""),
                    "email_sender": email.get("sender", ""),
                    "email_body": filtered_body,
                    "category": category, "urgency": urgency,
                    "extra_info": extra_info,
                })
                if dates:
                    evs = []
                    for d in dates:
                        evs.append({
                            "source_email_entry_id": email.get("entry_id", ""),
                            "source_email_subject": email.get("subject", ""),
                            "source_email_sender": email.get("sender", ""),
                            "description": d.get("description", ""),
                            "date_type": d.get("date_type", "tbd"),
                            "start_date": d.get("start_date"),
                            "end_date": d.get("end_date"),
                            "confidence": d.get("confidence", 0.5),
                            "project": d.get("project", ""),
                            "status": "pending",
                        })
                    if evs:
                        push_calendar_events(evs)
            except Exception:
                pass

            self._reply_queue.put((
                idx, email, filtered_body, category, urgency, extra_info, dates,
            ))
            self._workflow_queue.put((
                idx, email, filtered_body, category, urgency, extra_info, dates,
            ))

    def _run_reply_loop(self):
        while self._running:
            try:
                item = self._reply_queue.get(timeout=1)
            except queue.Empty:
                continue
            if item is None:
                break

            idx, email, filtered_body, category, urgency, extra_info, dates = item
            if idx in self._skipped_indices:
                continue

            print(f"\n{'═'*70}")
            print(f"📧 REPLY AGENT — Desktop (email #{idx})")
            print(f"{'═'*70}")
            print(f"  Email: {email.get('subject', '(no subject)')[:80]}")
            print(f"  Sender: {email.get('sender', 'unknown')}")
            print(f"  Category: {category}")
            print(f"  Urgency: {urgency}")

            facts = self._search_facts(email["subject"], filtered_body, category)
            facts_text = "\n".join(
                f"- [{f['project']}] {f['topic']}: {f['detail']}" for f in facts
            ) if facts else "No relevant stored facts found."

            cal_ctx = "No calendar data available."
            try:
                from shared_tools.ipc_bridge import pull_calendar_events
                evs = pull_calendar_events()
                if evs:
                    lines = [
                        f"- {ev['description']}: {ev.get('start_date', 'TBC')[:10]}"
                        for ev in evs[:10]
                    ]
                    cal_ctx = "\n".join(lines)
            except Exception:
                pass

            # Behavioral context from Habit Learner (graceful degradation)
            behavioral_text = ""
            print(f"\n  ── Habit Learner ──────────────────────────────────────")
            try:
                from shared_tools.habit_learner_service import get_habit_service
                habit_svc = get_habit_service()
                sender_raw = email.get("sender", "")
                from shared_tools.email_parser import extract_sender_email
                sender_email = extract_sender_email(sender_raw)
                print(f"  Sender email: {sender_email or '(not extractable)'}")

                ctx = habit_svc.infer(email)
                if ctx:
                    print(f"  Profile found:  {'YES' if ctx.sender_profile else 'NO'}")
                    if ctx.sender_profile:
                        sp = ctx.sender_profile
                        print(f"    Tier:         {sp.get('tier_label', '?')} (level {sp.get('tier', '?')})")
                        print(f"    Reply rate:   {sp.get('reply_rate', 0):.0%}")
                        print(f"    Top intent:   {sp.get('top_intent', 'N/A')}")
                        print(f"    Greeting:     \"{sp.get('preferred_greeting', 'N/A')}\"")
                        print(f"    Sign-off:     \"{sp.get('signoff_preference', 'N/A')}\"")
                    print(f"  Predicted intent: {ctx.predicted_intent}")
                    print(f"  Confidence:  {ctx.confidence:.0%}")
                    behavioral_text = ctx.to_injection_text()
                    if behavioral_text:
                        for line in behavioral_text.split("\n")[:6]:
                            print(f"  | {line}")
                        if len(behavioral_text.split("\n")) > 6:
                            print(f"  | ... ({len(behavioral_text.split(chr(10)))} lines total)")
                else:
                    print(f"  Result: No profiles loaded — infer() returned None")
            except Exception as e:
                print(f"  Habit Learner: skipped ({e})")

            inputs = {
                "email_subject": email["subject"],
                "email_content": filtered_body,
                "email_category": category,
                "email_urgency": urgency,
                "email_context": extra_info,
                "relevant_facts": facts_text,
                "relevant_schedule": cal_ctx,
                "behavioral_context": behavioral_text,
                "email_sender": email["sender"],
                "email_cc": email.get("cc", ""),
                "amy_name": os.environ.get("AMY_NAME", "Amy Chen"),
                "amy_email": os.environ.get("AMY_EMAIL", "amy@welink.com.au"),
            }

            try:
                with self._llm_semaphore:
                    from amail.crew import ReplyGeneratorCrew
                    print(f"\n  ── LLM Call ─────────────────────────────────────────")
                    print(f"  behavioral_context: {len(behavioral_text)} chars")
                    print(f"  relevant_facts: {len(facts_text)} chars")
                    print(f"  Calling ReplyGeneratorCrew.kickoff()...")
                    result = ReplyGeneratorCrew().crew().kickoff(inputs=inputs)
                draft = result.raw if hasattr(result, "raw") else str(result)
                print(f"  ✅ LLM call complete — draft: {len(draft)} chars")
                print(f"  Draft preview: {draft[:200]}...")
                print(f"{'═'*70}\n")
            except Exception as e:
                draft = f"Error generating reply: {e}"
                print(f"  ❌ LLM call failed: {e}")
                print(f"{'═'*70}\n")

            if idx in self._skipped_indices:
                continue

            if not draft.startswith("Error generating"):
                body_html = "".join(
                    f"<p>{line}</p>" if line.strip() else "<br>"
                    for line in draft.split("\n")
                )
                sig_path = os.path.join(_KNOWLEDGE_DIR, "amy_signature.html")
                if os.path.exists(sig_path):
                    with open(sig_path, "r", encoding="utf-8") as f:
                        sig = f.read()
                    body_html = (
                        f'<div style="font-family: Arial, sans-serif; font-size: 11pt;">'
                        f'{body_html}</div><br><br>{sig}'
                    )
                self._state[idx]["reply_text"] = body_html
            else:
                self._state[idx]["reply_text"] = draft

            self._state[idx]["reply_status"] = "done"
            self.reply_generated.emit(idx, self._state[idx]["reply_text"])

    def _run_workflow_loop(self):
        while self._running:
            try:
                item = self._workflow_queue.get(timeout=1)
            except queue.Empty:
                continue
            if item is None:
                break

            idx, email, filtered_body, category, urgency, extra_info, _dates = item
            if idx in self._skipped_indices:
                continue

            inputs = {
                "email_subject": email["subject"],
                "email_content": filtered_body,
                "email_category": category,
                "email_urgency": urgency,
                "email_context": extra_info,
            }
            try:
                with self._llm_semaphore:
                    from amail.crew import WorkflowGeneratorCrew
                    result = WorkflowGeneratorCrew().crew().kickoff(inputs=inputs)
                text = result.raw if hasattr(result, "raw") else str(result)
            except Exception as e:
                text = f"Error generating workflow: {e}"

            if idx in self._skipped_indices:
                continue
            self._state[idx]["workflow_text"] = text
            self._state[idx]["workflow_status"] = "done"
            self.workflow_generated.emit(idx, text)

    def _run_grammar_polish(self, idx: int, draft_text: str):
        try:
            with self._llm_semaphore:
                from amail.crew import GrammarPolisherCrew
                result = GrammarPolisherCrew().crew().kickoff(
                    inputs={"draft_text": draft_text}
                )
            polished = result.raw if hasattr(result, "raw") else str(result)
        except Exception as e:
            polished = f"Error polishing grammar: {e}"
        self.grammar_polished.emit(idx, polished)

    def _run_contact_fetch(self):
        from shared_tools.outlook_tool import fetch_outlook_contacts
        contacts = fetch_outlook_contacts()
        self._contacts_cache = contacts
        self.contacts_loaded.emit(contacts)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _append_email(self, email: dict) -> int:
        idx = self._index_counter
        self._index_counter += 1
        self._emails.append(email)
        self._state[idx] = {
            "filtered_body": "",
            "filter_status": "filtering",
            "category": "",
            "urgency": "",
            "extra_info": "",
            "category_status": "pending",
            "reply_cc": email.get("cc", ""),
            "reply_text": "",
            "reply_status": "pending",
            "workflow_text": "",
            "workflow_status": "pending",
            "send_status": "unsent",
            "attachment_count": None,
        }
        return idx

    def _promote_pending(self):
        while self._pending_emails and self.active_count() < self._max_active:
            email = self._pending_emails.pop(0)
            idx = self._append_email(email)
            self._filter_queue.put((idx, email))

    @staticmethod
    def _search_facts(subject: str, content: str, category: str = "") -> list[dict]:
        from amail.mail_knowledge import search_facts
        return search_facts(subject, content, category)
