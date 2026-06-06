import sys
import json
import queue
import os
import re
import threading
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QTextEdit, QPushButton, QSplitter, QMessageBox, QFrame,
    QStackedLayout, QDialog, QFileDialog, QScrollArea, QCompleter
)
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal, QStringListModel, QSortFilterProxyModel, QRegularExpression
from PyQt6.QtGui import QFont, QShortcut, QKeySequence

from amail.crew import MessageFilterCrew, TriageSingleCrew, ReplyGeneratorCrew, WorkflowGeneratorCrew, FactExtractorCrew
from amail.fact_store import init_db, search_facts, save_facts
from shared_tools.outlook_tool import (
    OutlookSendTool, mark_email_as_read, mark_email_as_unread,
    fetch_attachments_for_email, save_attachment, fetch_outlook_contacts,
)
from shared_tools.ipc_bridge import (
    register_app, unregister_app, init_shared_db,
    push_categorized_email, pull_calendar_events,
)

_AMAIL_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
from amail.mail_lister import MailListerDialog


# =============================================================================
# Background Workers
# =============================================================================

# Shared semaphore: only one LLM call across all workers at a time,
# preventing concurrent API calls from overwhelming the GUI thread.
_llm_semaphore = threading.Semaphore(1)

class FilterWorker(QThread):
    """Filters emails one-by-one, stripping signatures and boilerplate.
    Emits (index, cleaned_body) when each email is filtered.
    Pushes filtered emails into the triage_queue.
    """
    filter_done = pyqtSignal(int, str)

    def __init__(self, filter_queue, triage_queue, skipped_indices, parent=None):
        super().__init__(parent)
        self.filter_queue = filter_queue
        self.triage_queue = triage_queue
        self.skipped_indices = skipped_indices
        self.running = True

    def run(self):
        while self.running:
            try:
                item = self.filter_queue.get(timeout=1)
            except queue.Empty:
                continue
            if item is None:
                break
            
            idx, email = item
            if idx in self.skipped_indices:
                continue
            try:
                with _llm_semaphore:
                    result = MessageFilterCrew().crew().kickoff(
                        inputs={
                            "email_body": email["body"],
                            "email_subject": email["subject"],
                            "email_sender": email["sender"],
                            "email_received_time": email.get("received_time", "Unknown"),
                        }
                    )
                cleaned = result.raw if hasattr(result, 'raw') else str(result)
            except Exception as e:
                cleaned = f"Error filtering: {str(e)}"

            if idx in self.skipped_indices:
                continue
            self.filter_done.emit(idx, cleaned)
            self.triage_queue.put((idx, email, cleaned))

    def stop(self):
        self.running = False


class TriageWorker(QThread):
    """Processes filtered emails one-by-one through the triage agent.
    Now also extracts calendar dates as part of triage."""
    category_ready = pyqtSignal(int, str, str, str, list)

    def __init__(self, triage_queue, reply_queue, workflow_queue, skipped_indices, parent=None):
        super().__init__(parent)
        self.triage_queue = triage_queue
        self.reply_queue = reply_queue
        self.workflow_queue = workflow_queue
        self.skipped_indices = skipped_indices
        self.running = True

    def run(self):
        while self.running:
            try:
                item = self.triage_queue.get(timeout=1)
            except queue.Empty:
                continue
            if item is None:
                break

            idx, email, filtered_body = item
            if idx in self.skipped_indices:
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
                with _llm_semaphore:
                    result = TriageSingleCrew().crew().kickoff(inputs=inputs)
                raw = result.raw if hasattr(result, 'raw') else str(result)

                try:
                    cleaned = raw.strip()
                    if cleaned.startswith("```"):
                        cleaned = cleaned.split("\n", 1)[-1]
                        cleaned = cleaned.rsplit("```", 1)[0]
                    parsed = json.loads(cleaned.strip())
                    category = parsed.get("category", "Uncategorized")
                    urgency = parsed.get("urgency", "")
                    extra_info = parsed.get("extra_info", "")
                    dates = parsed.get("dates", [])
                except (json.JSONDecodeError, AttributeError):
                    category = raw[:100]
                    urgency = ""
                    extra_info = "Could not parse structured output"
            except Exception as e:
                category = "Error"
                urgency = ""
                extra_info = str(e)

            if idx in self.skipped_indices:
                continue
            self.category_ready.emit(idx, category, urgency, extra_info, dates)
            self.reply_queue.put((idx, email, filtered_body, category, urgency, extra_info, dates))
            self.workflow_queue.put((idx, email, filtered_body, category, urgency, extra_info, dates))

    def stop(self):
        self.running = False
        self.triage_queue.put(None)


class ReplyWorker(QThread):
    """Picks categorized emails from the queue and generates drafts one-by-one."""
    reply_generated = pyqtSignal(int, str)

    def __init__(self, reply_queue, skipped_indices, parent=None):
        super().__init__(parent)
        self.reply_queue = reply_queue
        self.skipped_indices = skipped_indices
        self.running = True

    def run(self):
        while self.running:
            try:
                item = self.reply_queue.get(timeout=1)
            except queue.Empty:
                continue
            if item is None:
                break

            idx, email, filtered_body, category, urgency, extra_info, _dates = item
            if idx in self.skipped_indices:
                continue

            facts = search_facts(email["subject"], filtered_body, category)
            facts_text = ""
            if facts:
                facts_text = "\n".join(
                    f"- [{f['project']}] {f['topic']}: {f['detail']}"
                    for f in facts
                )

            # Inject relevant calendar context from ACalendar
            calendar_context = "No calendar data available."
            try:
                events = pull_calendar_events()
                if events:
                    lines = []
                    for ev in events[:10]:  # limit to 10 most relevant
                        start = ev.get("start_date", "TBC")[:10]
                        lines.append(
                            f"- {ev['description']}: {start} ({ev.get('date_type', '')})"
                        )
                    if lines:
                        calendar_context = "\n".join(lines)
            except Exception:
                pass

            inputs = {
                "email_subject": email["subject"],
                "email_content": filtered_body,
                "email_category": category,
                "email_urgency": urgency,
                "email_context": extra_info,
                "relevant_facts": facts_text or "No relevant stored facts found.",
                "relevant_schedule": calendar_context,
                "email_sender": email["sender"],
                "email_cc": email.get("cc", ""),
                "amy_name": os.environ.get("AMY_NAME", "Amy Chen"),
                "amy_email": os.environ.get("AMY_EMAIL", "amy@welink.com.au"),
            }

            try:
                with _llm_semaphore:
                    result = ReplyGeneratorCrew().crew().kickoff(inputs=inputs)
                draft_text = result.raw if hasattr(result, 'raw') else str(result)
            except Exception as e:
                draft_text = f"Error generating reply: {str(e)}"

            if idx in self.skipped_indices:
                continue
            self.reply_generated.emit(idx, draft_text)

    def stop(self):
        self.running = False
        self.reply_queue.put(None)


class WorkflowWorker(QThread):
    """Picks categorized emails from the queue and generates workflows one-by-one."""
    workflow_generated = pyqtSignal(int, str)

    def __init__(self, workflow_queue, skipped_indices, parent=None):
        super().__init__(parent)
        self.workflow_queue = workflow_queue
        self.skipped_indices = skipped_indices
        self.running = True

    def run(self):
        while self.running:
            try:
                item = self.workflow_queue.get(timeout=1)
            except queue.Empty:
                continue
            if item is None:
                break

            idx, email, filtered_body, category, urgency, extra_info, _dates = item
            if idx in self.skipped_indices:
                continue

            inputs = {
                "email_subject": email["subject"],
                "email_content": filtered_body,
                "email_category": category,
                "email_urgency": urgency,
                "email_context": extra_info,
            }

            try:
                with _llm_semaphore:
                    result = WorkflowGeneratorCrew().crew().kickoff(inputs=inputs)
                workflow_text = result.raw if hasattr(result, 'raw') else str(result)
            except Exception as e:
                workflow_text = f"Error generating workflow: {str(e)}"

            if idx in self.skipped_indices:
                continue
            self.workflow_generated.emit(idx, workflow_text)

    def stop(self):
        self.running = False
        self.workflow_queue.put(None)


class ContactFetchWorker(QThread):
    """Fetches Outlook contacts (GAL + Contacts folder) on a background thread."""
    contacts_loaded = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)

    def run(self):
        contacts = fetch_outlook_contacts()
        self.contacts_loaded.emit(contacts)

    def stop(self):
        pass


class RegenerateWorker(QThread):
    """Re-runs filter, triage, or reply for a single email depending on which stage failed."""
    filter_done = pyqtSignal(int, str)
    triage_done = pyqtSignal(int, str, str, str, list)
    reply_done = pyqtSignal(int, str)

    def __init__(self, idx, email, mode, filtered_body="", category="", urgency="", extra_info="", parent=None):
        super().__init__(parent)
        self.idx = idx
        self.email = email
        self.mode = mode  # "filter", "triage", or "reply"
        self.filtered_body = filtered_body
        self.category = category
        self.urgency = urgency
        self.extra_info = extra_info

    def run(self):
        if self.mode == "filter":
            self._run_filter()
            self._run_triage()
            self._run_reply()
        elif self.mode == "triage":
            self._run_triage()
            self._run_reply()
        elif self.mode == "reply":
            self._run_reply()

    def _run_filter(self):
        try:
            with _llm_semaphore:
                result = MessageFilterCrew().crew().kickoff(
                    inputs={"email_body": self.email["body"]}
                )
            self.filtered_body = result.raw if hasattr(result, 'raw') else str(result)
        except Exception as e:
            self.filtered_body = f"Error filtering: {str(e)}"
        self.filter_done.emit(self.idx, self.filtered_body)

    def _run_triage(self):
        inputs = {
            "email_subject": self.email["subject"],
            "email_sender": self.email["sender"],
            "email_content": self.filtered_body,
        }
        dates = []
        try:
            with _llm_semaphore:
                result = TriageSingleCrew().crew().kickoff(inputs=inputs)
            raw = result.raw if hasattr(result, 'raw') else str(result)
            try:
                cleaned = raw.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("\n", 1)[-1]
                    cleaned = cleaned.rsplit("```", 1)[0]
                parsed = json.loads(cleaned.strip())
                self.category = parsed.get("category", "Uncategorized")
                self.urgency = parsed.get("urgency", "")
                self.extra_info = parsed.get("extra_info", "")
                dates = parsed.get("dates", [])
            except (json.JSONDecodeError, AttributeError):
                self.category = raw[:100]
                self.urgency = ""
                self.extra_info = "Could not parse structured output"
        except Exception as e:
            self.category = "Error"
            self.urgency = ""
            self.extra_info = str(e)
        self.triage_done.emit(self.idx, self.category, self.urgency, self.extra_info, dates)

    def _run_reply(self):
        facts = search_facts(self.email["subject"], self.filtered_body, self.category)
        facts_text = ""
        if facts:
            facts_text = "\n".join(
                f"- [{f['project']}] {f['topic']}: {f['detail']}"
                for f in facts
            )

        inputs = {
            "email_subject": self.email["subject"],
            "email_content": self.filtered_body,
            "email_category": self.category,
            "email_urgency": self.urgency,
            "email_context": self.extra_info,
            "relevant_facts": facts_text or "No relevant stored facts found.",
            "email_sender": self.email["sender"],
            "email_cc": self.email.get("cc", ""),
            "amy_name": os.environ.get("AMY_NAME", "Amy Chen"),
            "amy_email": os.environ.get("AMY_EMAIL", "amy@welink.com.au"),
        }
        try:
            with _llm_semaphore:
                result = ReplyGeneratorCrew().crew().kickoff(inputs=inputs)
            draft_text = result.raw if hasattr(result, 'raw') else str(result)
        except Exception as e:
            draft_text = f"Error generating reply: {str(e)}"
        self.reply_done.emit(self.idx, draft_text)


class GrammarPolishWorker(QThread):
    """Polishes grammar of a reply draft in the background."""
    polish_done = pyqtSignal(int, str)

    def __init__(self, idx, draft_text, parent=None):
        super().__init__(parent)
        self.idx = idx
        self.draft_text = draft_text

    def run(self):
        from amail.crew import GrammarPolisherCrew
        try:
            with _llm_semaphore:
                result = GrammarPolisherCrew().crew().kickoff(
                    inputs={"draft_text": self.draft_text}
                )
            polished = result.raw if hasattr(result, 'raw') else str(result)
        except Exception as e:
            polished = f"Error polishing grammar: {str(e)}"
        self.polish_done.emit(self.idx, polished)


# =============================================================================
# Workflow Dialog
# =============================================================================

class WorkflowDialog(QDialog):
    def __init__(self, idx, email, st, parent=None):
        super().__init__(parent)
        self.idx = idx
        self.email = email
        self.st = st
        self.parent_window = parent
        
        self.setWindowTitle("Task Allocation Workflow")
        self.resize(800, 600)
        
        layout = QVBoxLayout(self)
        
        lbl_info = QLabel(f"<b>Workflow for:</b> {email['subject']}<br><b>Category:</b> {st['category']}")
        lbl_info.setStyleSheet("font-size: 14px; margin-bottom: 10px;")
        layout.addWidget(lbl_info)
        
        self.txt_workflow = QTextEdit()
        if st["workflow_status"] == "generating" or st["workflow_status"] == "pending":
            self.txt_workflow.setPlainText("⏳ Thinking...")
            self.txt_workflow.setEnabled(False)
        else:
            self.txt_workflow.setPlainText(st["workflow_text"])
        layout.addWidget(self.txt_workflow)
        
        btn_layout = QHBoxLayout()
        self.btn_regen = QPushButton("🔄 Regenerate (R)")
        self.btn_update = QPushButton("💾 Update Answer (4)")
        self.btn_proceed = QPushButton("▶️ Proceed (1)")
        
        btn_layout.addWidget(self.btn_regen)
        btn_layout.addWidget(self.btn_update)
        btn_layout.addWidget(self.btn_proceed)
        layout.addLayout(btn_layout)
        
        self.btn_regen.clicked.connect(self.on_regenerate)
        self.btn_update.clicked.connect(self.on_update)
        self.btn_proceed.clicked.connect(self.accept)

        # Shortcuts for workflow dialog
        QShortcut(QKeySequence("R"), self).activated.connect(self.on_regenerate)
        QShortcut(QKeySequence("4"), self).activated.connect(self.on_update)
        QShortcut(QKeySequence("1"), self).activated.connect(self.accept)
        
    def on_regenerate(self):
        if self.parent_window:
            self.parent_window.regenerate_workflow(self.idx)
        self.txt_workflow.setPlainText("⏳ Thinking...")
        self.txt_workflow.setEnabled(False)
        
    def on_update(self):
        updated_text = self.txt_workflow.toPlainText()
        if self.parent_window:
            self.parent_window.save_workflow_feedback(self.idx, updated_text)
        QMessageBox.information(self, "Success", "Workflow feedback saved for training!")


# =============================================================================
# Attachment Dialog
# =============================================================================

class AttachmentDialog(QDialog):
    """Lists attachments for the current email with Download / Download & Preview."""

    def __init__(self, entry_id, subject, download_dir, parent=None):
        super().__init__(parent)
        self.entry_id = entry_id
        self.download_dir = download_dir
        self.parent_window = parent

        self.setWindowTitle(f"Attachments — {subject}")
        self.resize(800, 420)

        root_layout = QVBoxLayout(self)

        # --- Download directory selector ---
        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("Download to:"))
        self.le_dir = QLineEdit(self.download_dir)
        self.le_dir.setReadOnly(True)
        dir_row.addWidget(self.le_dir, stretch=1)
        btn_browse = QPushButton("📁 Browse")
        btn_browse.clicked.connect(self._browse_dir)
        dir_row.addWidget(btn_browse)
        root_layout.addLayout(dir_row)

        # --- Scrollable attachment list ---
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.list_widget = QWidget()
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(4, 4, 4, 4)
        self.list_layout.setSpacing(6)
        scroll.setWidget(self.list_widget)
        root_layout.addWidget(scroll)

        # Populate
        self._populate()

    # ---- helpers ----

    def _browse_dir(self):
        chosen = QFileDialog.getExistingDirectory(self, "Select download folder", self.download_dir)
        if chosen:
            self.download_dir = chosen
            self.le_dir.setText(chosen)
            # Propagate back to the main window so it persists for the session
            if self.parent_window and hasattr(self.parent_window, "download_dir"):
                self.parent_window.download_dir = chosen

    def _populate(self):
        if not self.entry_id:
            lbl = QLabel("⚠️ No Entry ID — cannot read attachments.")
            lbl.setStyleSheet("color: #B31412; font-weight: bold; padding: 12px;")
            self.list_layout.addWidget(lbl)
            return

        attachments = fetch_attachments_for_email(self.entry_id)

        if not attachments:
            lbl = QLabel("📭 This email has no attachments.")
            lbl.setStyleSheet("color: #555; font-style: italic; padding: 12px;")
            self.list_layout.addWidget(lbl)
            return

        for att in attachments:
            row = QFrame()
            row.setStyleSheet(
                "QFrame { background-color: #FAFAF0; border: 1px solid #E6DEB1; border-radius: 6px; padding: 6px; }"
            )
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(8, 4, 8, 4)

            # File info
            size_kb = att['size'] / 1024
            if size_kb >= 1024:
                size_str = f"{size_kb / 1024:.1f} MB"
            else:
                size_str = f"{size_kb:.1f} KB"
            info_lbl = QLabel(f"📎 <b>{att['filename']}</b>  <span style='color:#888'>({size_str})</span>")
            info_lbl.setTextFormat(Qt.TextFormat.RichText)
            row_layout.addWidget(info_lbl, stretch=1)

            # Download button
            btn_dl = QPushButton("⬇️ Download")
            btn_dl.setMinimumWidth(100)
            btn_dl.setStyleSheet(
                "background-color: #0078D4; color: white; font-weight: bold; border-radius: 4px; padding: 4px 10px;"
            )
            btn_dl.clicked.connect(lambda checked, a=att: self._download(a))
            row_layout.addWidget(btn_dl)

            # Download & Preview button
            btn_dlp = QPushButton("👁️ Download & Preview")
            btn_dlp.setMinimumWidth(150)
            btn_dlp.setStyleSheet(
                "background-color: #107C10; color: white; font-weight: bold; border-radius: 4px; padding: 4px 10px;"
            )
            btn_dlp.clicked.connect(lambda checked, a=att: self._download_and_preview(a))
            row_layout.addWidget(btn_dlp)

            self.list_layout.addWidget(row)

        self.list_layout.addStretch()

    def _download(self, att_meta):
        save_dir = self.le_dir.text().strip()
        if not save_dir:
            QMessageBox.warning(self, "No directory", "Please select a download directory first.")
            return
        result = save_attachment(self.entry_id, att_meta["index"], save_dir)
        if result.startswith("Error:"):
            QMessageBox.warning(self, "Download failed", result)
        else:
            QMessageBox.information(self, "Downloaded", f"Saved to:\n{result}")

    def _download_and_preview(self, att_meta):
        save_dir = self.le_dir.text().strip()
        if not save_dir:
            QMessageBox.warning(self, "No directory", "Please select a download directory first.")
            return
        result = save_attachment(self.entry_id, att_meta["index"], save_dir)
        if result.startswith("Error:"):
            QMessageBox.warning(self, "Download failed", result)
            return
        # Open with system default "Open with" dialog (Windows)
        try:
            os.startfile(result)  # type: ignore[attr-defined]   # Windows only
        except AttributeError:
            # Fallback for non-Windows (shouldn't happen given save_attachment guard)
            import subprocess as _sp
            _sp.Popen(["xdg-open", result])
        except OSError as e:
            QMessageBox.warning(self, "Cannot open file", f"Failed to open:\n{e}")


# =============================================================================
# Contact Autocomplete Widgets
# =============================================================================

class _ContactFilterProxy(QSortFilterProxyModel):
    """Proxy that does case-insensitive substring matching against filter string."""

    def filterAcceptsRow(self, source_row, parent):
        if not self.filterRegularExpression().isValid():
            return True
        idx = self.sourceModel().index(source_row, self.filterKeyColumn(), parent)
        data = self.sourceModel().data(idx)
        if data is None:
            return False
        return self.filterRegularExpression().match(str(data)).hasMatch()


class ContactAutocomplete(QLineEdit):
    """A QLineEdit with QCompleter dropdown that filters against a contact list.

    Shows matching contacts after 2+ characters typed. Enter auto-fills the
    highlighted suggestion. Free-text entries are accepted as valid input.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._model = QStringListModel(self)
        self._proxy = _ContactFilterProxy(self)
        self._proxy.setSourceModel(self._model)
        self._proxy.setFilterKeyColumn(0)

        self._completer = QCompleter(self)
        self._completer.setModel(self._proxy)
        self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.popup().setMinimumWidth(350)
        self._completer.activated.connect(self._on_completion_activated)

        self.setCompleter(self._completer)
        self.textChanged.connect(self._on_text_changed)

    def _on_text_changed(self, text):
        t = text.strip()
        if len(t) >= 2:
            self._proxy.setFilterRegularExpression(
                QRegularExpression(re.escape(t), QRegularExpression.PatternOption.CaseInsensitiveOption)
            )
        else:
            self._completer.popup().hide()

    def _on_completion_activated(self, text):
        self.setText(text)

    def set_contacts(self, contacts: list[dict]):
        display_strings = [f"{c['name']} <{c['email']}>" for c in contacts]
        self._model.setStringList(display_strings)

    def get_text(self) -> str:
        return self.text().strip()


class CcRecipientRow(QWidget):
    """A single CC recipient row: autocomplete field + '-' remove button."""
    removed = pyqtSignal(QWidget)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.le_cc = ContactAutocomplete(self)
        layout.addWidget(self.le_cc, stretch=1)

        self.btn_clear = QPushButton("×", self)
        self.btn_clear.setFixedWidth(30)
        self.btn_clear.setFixedHeight(30)
        self.btn_clear.setToolTip("Clear this recipient")
        self.btn_clear.setStyleSheet(
            "background-color: #888888; color: white; font-weight: bold; "
            "border-radius: 4px; padding: 0px; font-size: 14px;"
        )
        self.btn_clear.clicked.connect(lambda: self.le_cc.setText(""))
        layout.addWidget(self.btn_clear)

        self.btn_remove = QPushButton("-", self)
        self.btn_remove.setFixedWidth(30)
        self.btn_remove.setFixedHeight(30)
        self.btn_remove.setToolTip("Remove this row")
        self.btn_remove.setStyleSheet(
            "background-color: #D83B01; color: white; font-weight: bold; "
            "border-radius: 4px; padding: 0px; font-size: 14px;"
        )
        self.btn_remove.clicked.connect(lambda: self.removed.emit(self))
        layout.addWidget(self.btn_remove)

    def set_text(self, text: str):
        self.le_cc.setText(text.strip())

    def get_text(self) -> str:
        return self.le_cc.get_text()

    def set_contacts(self, contacts: list[dict]):
        self.le_cc.set_contacts(contacts)

    def setEnabled(self, enabled: bool):
        self.le_cc.setEnabled(enabled)
        self.btn_clear.setEnabled(enabled)
        self.btn_remove.setEnabled(enabled)


class CcSection(QWidget):
    """Container for CC recipient rows with '+' add button and layout management."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[CcRecipientRow] = []
        self._contacts: list[dict] = []

        self._outer_layout = QVBoxLayout(self)
        self._outer_layout.setContentsMargins(0, 0, 0, 0)
        self._outer_layout.setSpacing(4)

        self._rows_layout = QVBoxLayout()
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(2)
        self._outer_layout.addLayout(self._rows_layout)

        self.btn_add = QPushButton("+ Add CC", self)
        self.btn_add.setFixedHeight(30)
        self.btn_add.setStyleSheet(
            "background-color: #107C10; color: white; font-weight: bold; "
            "border-radius: 4px; padding: 2px 10px; font-size: 12px;"
        )
        self.btn_add.clicked.connect(self.add_row)
        self._outer_layout.addWidget(self.btn_add)

    def set_contacts(self, contacts: list[dict]):
        self._contacts = contacts
        for row in self._rows:
            row.set_contacts(contacts)

    def add_row(self, initial_text: str = ""):
        row = CcRecipientRow(self)
        row.set_contacts(self._contacts)
        if initial_text:
            row.set_text(initial_text)
        row.removed.connect(self._remove_row)
        self._rows_layout.addWidget(row)
        self._rows.append(row)

    def _remove_row(self, row: CcRecipientRow):
        if row in self._rows:
            self._rows.remove(row)
            self._rows_layout.removeWidget(row)
            row.deleteLater()

    def clear_rows(self):
        for row in list(self._rows):
            self._rows_layout.removeWidget(row)
            row.deleteLater()
        self._rows.clear()

    def set_from_cc_string(self, cc_str: str):
        self.clear_rows()
        if not cc_str or not cc_str.strip():
            return
        parts = [p.strip() for p in cc_str.split(";") if p.strip()]
        for part in parts:
            self.add_row(initial_text=part)

    def get_cc_string(self) -> str:
        entries = [row.get_text() for row in self._rows if row.get_text()]
        return "; ".join(entries)

    def get_rows(self) -> list:
        return self._rows

    def setEnabled(self, enabled: bool):
        for row in self._rows:
            row.setEnabled(enabled)
        self.btn_add.setEnabled(enabled)


# =============================================================================
# Helpers
# =============================================================================

SIGNATURE_MARKERS = [
    "kind regards", "kind regards,", "best regards", "best regards,",
    "sincerely", "sincerely,", "yours sincerely", "yours sincerely,",
    "yours faithfully", "yours faithfully,", "warm regards", "warm regards,",
    "cheers", "cheers,", "thanks", "thanks,", "thank you", "thank you,",
    "regards", "regards,",
]


def _strip_signature(text: str) -> str:
    """Strip signature block and closing phrases from the end of an email body.

    Looks for lines that consist solely of a closing phrase (like 'Kind regards,')
    and truncates the text from that point.
    """
    lines = text.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip().lower().rstrip(",")
        if stripped in SIGNATURE_MARKERS or stripped.rstrip(",") in SIGNATURE_MARKERS:
            return "\n".join(lines[:i]).strip()
    return text.strip()


# =============================================================================
# Main Window
# =============================================================================

class TriageWindow(QMainWindow):
    def __init__(self, raw_emails, processed_entry_ids: set[str] = None):
        super().__init__()
        self.processed_entry_ids = processed_entry_ids if processed_entry_ids is not None else set()
        self.emails = []
        self.pending_emails: list[dict] = []
        self.max_active = 5
        self.state = {}
        self.current_index = 0
        self.email_index_counter = 0
        self.filter_queue = queue.Queue()
        self.triage_queue = queue.Queue()
        self.reply_queue = queue.Queue()
        self.workflow_queue = queue.Queue()
        self.skipped_indices = set()
        self.download_dir = os.path.abspath(".")  # default to program root
        self.contacts_cache: list[dict] = []

        self.init_ui()
        self.start_workers()

        # Navigation request poll timer (for "Open Email in AMail" from ACalendar)
        self.nav_poll_timer = QTimer()
        self.nav_poll_timer.setInterval(3000)
        self.nav_poll_timer.timeout.connect(self._poll_nav_requests)
        self.nav_poll_timer.start()

        # Queue the initial batch directly
        for email in raw_emails:
            idx = self._append_email(email)
            self.filter_queue.put((idx, email))

        if self.emails:
            self.load_email(0)
        self.update_ui_state()

    def _append_email(self, email: dict) -> int:
        """Append an email dict to self.emails, create state, return index."""
        idx = self.email_index_counter
        self.email_index_counter += 1
        self.emails.append(email)

        self.state[idx] = {
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

    def _count_active(self):
        """Return the number of emails currently in the processing pipeline."""
        return sum(
            1 for st in self.state.values()
            if st["send_status"] not in ("sent", "skipped")
        )

    def _all_emails_done(self):
        """Return True if every email (active + pending) has been processed."""
        if len(self.emails) == 0 and len(self.pending_emails) == 0:
            return True
        if len(self.emails) > 0:
            return all(
                st["send_status"] in ("sent", "skipped")
                for st in self.state.values()
            ) and len(self.pending_emails) == 0
        return False

    def init_ui(self):
        self.setWindowTitle("Interactive Triage & Auto-Reply Workstation")
        self.resize(1400, 800)
        self.setMinimumWidth(1000)
        
        self.setStyleSheet("""
            QMainWindow { background-color: #FFFDE7; }
            QWidget { font-family: Arial, sans-serif; color: #3E3E3E; }
            QSplitter { background-color: transparent; border: none; }
            QSplitter::handle { background-color: #FFFDE7; }
            QFrame { background-color: #FFFFFF; border-radius: 8px; border: 1px solid #E6DEB1; }
            QLabel { border: none; background-color: transparent; }
            QLineEdit, QTextEdit { 
                background-color: #FFFFFF; 
                border: 1px solid #E6DEB1; 
                border-radius: 4px; 
                padding: 4px; 
            }
            QPushButton {
                background-color: #FFF3CD;
                border: 1px solid #E6DEB1;
                border-radius: 4px;
                padding: 6px;
                font-weight: bold;
                color: #3E3E3E;
            }
            QPushButton:hover { background-color: #FFE8A1; }
            QPushButton:disabled { color: #A8A8A8; background-color: #FFF9E6; }
        """)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        self.empty_label = QLabel("No emails in queue.\nPress M to add more.")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet(
            "font-size: 22px; color: #A8A8A8; border: none; background: transparent; padding: 60px;"
        )
        self.empty_label.setVisible(False)
        main_layout.addWidget(self.empty_label)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self.splitter)

        # --- LEFT PANEL (Original Email) ---
        left_panel = QFrame()
        left_panel.setFrameShape(QFrame.Shape.StyledPanel)
        left_panel.setMinimumWidth(280)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(15, 15, 15, 15)

        header_font = QFont("Arial", 12, QFont.Weight.Bold)

        lbl_orig = QLabel("Original Email")
        lbl_orig.setFont(header_font)
        left_layout.addWidget(lbl_orig)

        # Subject
        self.lbl_orig_subject = QLabel("Subject: ")
        self.lbl_orig_subject.setWordWrap(True)
        self.lbl_orig_subject.setStyleSheet("font-weight: bold;")
        left_layout.addWidget(self.lbl_orig_subject)

        # Received Time
        self.lbl_orig_time = QLabel("Received: ")
        self.lbl_orig_time.setStyleSheet("color: #7A7A7A; font-size: 11px; font-style: italic;")
        left_layout.addWidget(self.lbl_orig_time)

        # Sender
        self.lbl_orig_sender = QLabel("Sender: ")
        left_layout.addWidget(self.lbl_orig_sender)

        # Original CC
        self.lbl_orig_cc = QLabel("CC: ")
        left_layout.addWidget(self.lbl_orig_cc)

        # Content area with overlay support
        left_layout.addWidget(QLabel("Content:"))

        # Container for content + overlay
        self.content_container = QWidget()
        content_stack = QStackedLayout(self.content_container)
        content_stack.setStackingMode(QStackedLayout.StackingMode.StackAll)

        self.txt_orig_content = QTextEdit()
        self.txt_orig_content.setReadOnly(True)
        self.txt_orig_content.setStyleSheet("background-color: #FCFBF4; color: #3E3E3E;")

        self.filter_overlay = QLabel("🔍 Thinking and filtering...")
        self.filter_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.filter_overlay.setStyleSheet(
            "background-color: rgba(255, 250, 205, 180); "
            "color: #3E3E3E; font-size: 20px; font-weight: bold;"
        )

        content_stack.addWidget(self.txt_orig_content)
        content_stack.addWidget(self.filter_overlay)

        left_layout.addWidget(self.content_container)

        # --- MIDDLE PANEL (Draft Reply) ---
        middle_panel = QFrame()
        middle_panel.setFrameShape(QFrame.Shape.StyledPanel)
        reply_layout = QVBoxLayout(middle_panel)
        reply_layout.setContentsMargins(15, 15, 15, 15)
        reply_layout.setSpacing(6)

        lbl_draft = QLabel("AI Draft Reply")
        lbl_draft.setFont(header_font)
        reply_layout.addWidget(lbl_draft)

        # Subject
        reply_layout.addWidget(QLabel("Subject:"))
        self.le_reply_subject = QLineEdit()
        reply_layout.addWidget(self.le_reply_subject)

        # Triage Info
        self.lbl_category = QLabel("Category: ⏳ Waiting...")
        self.lbl_category.setStyleSheet("color: #174EA6; background-color: #E8F0FE; padding: 6px; border-radius: 4px; font-weight: bold;")
        self.lbl_category.setWordWrap(True)
        reply_layout.addWidget(self.lbl_category)

        self.lbl_urgency = QLabel("Urgency: ⏳ Waiting...")
        self.lbl_urgency.setStyleSheet("color: #3C4043; background-color: #F8F9FA; padding: 6px; border-radius: 4px; font-weight: bold;")
        self.lbl_urgency.setWordWrap(True)
        reply_layout.addWidget(self.lbl_urgency)

        self.lbl_extra_info = QLabel("Extra Info: ⏳ Waiting...")
        self.lbl_extra_info.setStyleSheet("color: #3C4043; background-color: #F8F9FA; padding: 6px; border-radius: 4px; font-style: italic;")
        self.lbl_extra_info.setWordWrap(True)
        reply_layout.addWidget(self.lbl_extra_info)

        self.btn_workflow = QPushButton("📝 View/Edit Workflow (W)")
        self.btn_workflow.clicked.connect(self.open_workflow_dialog)
        self.btn_workflow.setStyleSheet("margin-top: 4px; margin-bottom: 4px;")
        reply_layout.addWidget(self.btn_workflow)

        # Receiver
        reply_layout.addWidget(QLabel("Receiver:"))
        receiver_row = QWidget()
        receiver_layout = QHBoxLayout(receiver_row)
        receiver_layout.setContentsMargins(0, 0, 0, 0)
        receiver_layout.setSpacing(4)
        self.le_reply_receiver = ContactAutocomplete()
        receiver_layout.addWidget(self.le_reply_receiver, stretch=1)
        self.btn_clear_receiver = QPushButton("×")
        self.btn_clear_receiver.setFixedWidth(30)
        self.btn_clear_receiver.setFixedHeight(30)
        self.btn_clear_receiver.setToolTip("Clear receiver")
        self.btn_clear_receiver.setStyleSheet(
            "background-color: #888888; color: white; font-weight: bold; "
            "border-radius: 4px; padding: 0px; font-size: 14px;"
        )
        self.btn_clear_receiver.clicked.connect(lambda: self.le_reply_receiver.setText(""))
        receiver_layout.addWidget(self.btn_clear_receiver)
        reply_layout.addWidget(receiver_row)

        # CC
        reply_layout.addWidget(QLabel("CC:"))
        self.cc_section = CcSection()
        reply_layout.addWidget(self.cc_section)

        # Content
        reply_layout.addWidget(QLabel("Content:"))
        self.txt_reply_content = QTextEdit()
        self.txt_reply_content.setMinimumHeight(200)
        reply_layout.addWidget(self.txt_reply_content, stretch=1)

        # -- Button column (right side of right panel) --
        btn_column = QWidget()
        btn_column.setMinimumWidth(120)
        btn_column.setStyleSheet("background-color: transparent; border: none;")
        btn_layout = QVBoxLayout(btn_column)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(5)

        # ── Top group: save / skip ──────────────────────────

        self.btn_save_reply = QPushButton("💾 Save Ex (4)")
        self.btn_save_reply.setMinimumHeight(34)
        self.btn_save_reply.setStyleSheet(
            "QPushButton { background-color: #107C10; color: white; font-weight: bold; "
            "border-radius: 4px; border: none; }"
            "QPushButton:hover { background-color: #0D652D; }"
            "QPushButton:disabled { background-color: #A8A8A8; }"
        )
        self.btn_save_reply.clicked.connect(self.save_reply_feedback)
        btn_layout.addWidget(self.btn_save_reply)

        self.btn_save_facts = QPushButton("📋 Facts (5)")
        self.btn_save_facts.setMinimumHeight(34)
        self.btn_save_facts.setStyleSheet(
            "QPushButton { background-color: #0F5B8C; color: white; font-weight: bold; "
            "border-radius: 4px; border: none; }"
            "QPushButton:hover { background-color: #0B456A; }"
            "QPushButton:disabled { background-color: #A8A8A8; }"
        )
        self.btn_save_facts.clicked.connect(self.save_key_facts)
        btn_layout.addWidget(self.btn_save_facts)

        self.btn_skip_read = QPushButton("⏭️ Skip+Read (2)")
        self.btn_skip_read.setMinimumHeight(34)
        self.btn_skip_read.setStyleSheet(
            "QPushButton { background-color: #6B8E23; color: white; font-weight: bold; "
            "border-radius: 4px; border: none; }"
            "QPushButton:hover { background-color: #557018; }"
            "QPushButton:disabled { background-color: #A8A8A8; }"
        )
        self.btn_skip_read.clicked.connect(self.skip_read)
        btn_layout.addWidget(self.btn_skip_read)

        self.btn_skip_unread = QPushButton("⏭️ Skip (3)")
        self.btn_skip_unread.setMinimumHeight(34)
        self.btn_skip_unread.setStyleSheet(
            "QPushButton { background-color: #8B4513; color: white; font-weight: bold; "
            "border-radius: 4px; border: none; }"
            "QPushButton:hover { background-color: #6B340E; }"
            "QPushButton:disabled { background-color: #A8A8A8; }"
        )
        self.btn_skip_unread.clicked.connect(self.skip_unread)
        btn_layout.addWidget(self.btn_skip_unread)

        # --- Separator ---
        sep1 = QLabel()
        sep1.setFixedHeight(1)
        sep1.setStyleSheet("background-color: #E6DEB1; border: none;")
        btn_layout.addWidget(sep1)

        # ── Middle group: utility ──────────────────────────

        self.btn_mails = QPushButton("📬 Mails (M)")
        self.btn_mails.setMinimumHeight(34)
        self.btn_mails.setStyleSheet(
            "QPushButton { background-color: #FFF3CD; color: #3E3E3E; font-weight: bold; "
            "border: 1px solid #E6DEB1; border-radius: 4px; }"
            "QPushButton:hover { background-color: #FFE8A1; }"
        )
        self.btn_mails.clicked.connect(self.open_mail_lister)
        btn_layout.addWidget(self.btn_mails)

        self.btn_attachment = QPushButton("📎 Attach (Q)")
        self.btn_attachment.setMinimumHeight(34)
        self.btn_attachment.setStyleSheet(
            "QPushButton { background-color: #FFF3CD; color: #3E3E3E; font-weight: bold; "
            "border: 1px solid #E6DEB1; border-radius: 4px; }"
            "QPushButton:hover { background-color: #FFE8A1; }"
        )
        self.btn_attachment.clicked.connect(self.open_attachment_dialog)
        btn_layout.addWidget(self.btn_attachment)

        btn_layout.addStretch()

        # --- Separator ---
        sep2 = QLabel()
        sep2.setFixedHeight(1)
        sep2.setStyleSheet("background-color: #E6DEB1; border: none;")
        btn_layout.addWidget(sep2)

        # ── Bottom group: reply actions (Regen → Polish → Send) ──

        self.btn_regenerate = QPushButton("🔄 Regen (R)")
        self.btn_regenerate.setMinimumHeight(34)
        self.btn_regenerate.setStyleSheet(
            "QPushButton { background-color: #D83B01; color: white; font-weight: bold; "
            "border-radius: 4px; border: none; }"
            "QPushButton:hover { background-color: #B02F01; }"
            "QPushButton:disabled { background-color: #A8A8A8; }"
        )
        self.btn_regenerate.clicked.connect(self.regenerate_current)
        btn_layout.addWidget(self.btn_regenerate)

        self.btn_grammar = QPushButton("✏️ Polish (G)")
        self.btn_grammar.setMinimumHeight(34)
        self.btn_grammar.setStyleSheet(
            "QPushButton { background-color: #5B5EA6; color: white; font-weight: bold; "
            "border-radius: 4px; border: none; }"
            "QPushButton:hover { background-color: #484B8C; }"
            "QPushButton:disabled { background-color: #A8A8A8; }"
        )
        self.btn_grammar.clicked.connect(self.grammar_polish)
        btn_layout.addWidget(self.btn_grammar)

        self.btn_send = QPushButton("📨 Send (1)")
        self.btn_send.setMinimumHeight(42)
        self.btn_send.setStyleSheet(
            "QPushButton { background-color: #0078D4; color: white; font-weight: bold; "
            "font-size: 12px; border-radius: 6px; border: none; }"
            "QPushButton:hover { background-color: #106EBE; }"
            "QPushButton:disabled { background-color: #A8A8A8; }"
        )
        self.btn_send.clicked.connect(self.send_email)
        btn_layout.addWidget(self.btn_send)

        # ── Navigation (horizontal, squeezed, counter as text) ──

        nav_frame = QFrame()
        nav_frame.setStyleSheet(
            "QFrame { background-color: #FFF9E6; border: 1px solid #E6DEB1; border-radius: 6px; }"
        )
        nav_layout = QHBoxLayout(nav_frame)
        nav_layout.setContentsMargins(2, 2, 2, 2)
        nav_layout.setSpacing(0)

        self.btn_prev = QPushButton("◀")
        self.btn_prev.setFixedSize(36, 28)
        self.btn_prev.setToolTip("Previous (A)")
        self.btn_prev.setStyleSheet(
            "QPushButton { background-color: transparent; color: #3E3E3E; font-weight: bold; "
            "border: none; border-radius: 4px; font-size: 14px; }"
            "QPushButton:hover { background-color: #FFE8A1; }"
            "QPushButton:disabled { color: #A8A8A8; }"
        )
        self.btn_prev.clicked.connect(self.prev_email)
        nav_layout.addWidget(self.btn_prev)

        self.lbl_counter = QLabel("0/0")
        self.lbl_counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_counter.setStyleSheet(
            "font-size: 11px; font-weight: bold; color: #3E3E3E; "
            "border: none; background: transparent; padding: 0px 4px;"
        )
        nav_layout.addWidget(self.lbl_counter)

        self.btn_next = QPushButton("▶")
        self.btn_next.setFixedSize(36, 28)
        self.btn_next.setToolTip("Next (D)")
        self.btn_next.setStyleSheet(
            "QPushButton { background-color: transparent; color: #3E3E3E; font-weight: bold; "
            "border: none; border-radius: 4px; font-size: 14px; }"
            "QPushButton:hover { background-color: #FFE8A1; }"
            "QPushButton:disabled { color: #A8A8A8; }"
        )
        self.btn_next.clicked.connect(self.next_email)
        nav_layout.addWidget(self.btn_next)

        btn_layout.addWidget(nav_frame)

        # --- Keyboard Shortcuts ---
        QShortcut(QKeySequence("A"), self).activated.connect(self.prev_email)
        QShortcut(QKeySequence("D"), self).activated.connect(self.next_email)
        QShortcut(QKeySequence("R"), self).activated.connect(self.regenerate_current)
        QShortcut(QKeySequence("W"), self).activated.connect(self.open_workflow_dialog)
        QShortcut(QKeySequence("1"), self).activated.connect(self.send_email)
        QShortcut(QKeySequence("2"), self).activated.connect(self.skip_read)
        QShortcut(QKeySequence("3"), self).activated.connect(self.skip_unread)
        QShortcut(QKeySequence("4"), self).activated.connect(self.save_reply_feedback)
        QShortcut(QKeySequence("5"), self).activated.connect(self.save_key_facts)
        QShortcut(QKeySequence("G"), self).activated.connect(self.grammar_polish)
        QShortcut(QKeySequence("Q"), self).activated.connect(self.open_attachment_dialog)
        QShortcut(QKeySequence("M"), self).activated.connect(self.open_mail_lister)

        # Three-column splitter: left (email) | middle (reply) | right (buttons)
        # Ratio 3 : 5 : 1 — initial sizes for 1400px window
        middle_panel.setMinimumWidth(350)
        self.splitter.addWidget(left_panel)
        self.splitter.addWidget(middle_panel)
        self.splitter.addWidget(btn_column)
        self.splitter.setSizes([467, 778, 155])
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, False)
        self.splitter.setCollapsible(2, False)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 5)
        self.splitter.setStretchFactor(2, 1)

    # -------------------------------------------------------------------------
    # Workers
    # -------------------------------------------------------------------------

    def start_workers(self):
        self.filter_worker = FilterWorker(self.filter_queue, self.triage_queue, self.skipped_indices)
        self.filter_worker.filter_done.connect(self.on_filter_done)
        self.filter_worker.start()

        self.triage_worker = TriageWorker(self.triage_queue, self.reply_queue, self.workflow_queue, self.skipped_indices)
        self.triage_worker.category_ready.connect(self.on_category_ready)
        self.triage_worker.start()

        self.reply_worker = ReplyWorker(self.reply_queue, self.skipped_indices)
        self.reply_worker.reply_generated.connect(self.on_reply_generated)
        self.reply_worker.start()

        self.workflow_worker = WorkflowWorker(self.workflow_queue, self.skipped_indices)
        self.workflow_worker.workflow_generated.connect(self.on_workflow_generated)
        self.workflow_worker.start()

        self.contact_worker = ContactFetchWorker()
        self.contact_worker.contacts_loaded.connect(self.on_contacts_loaded)
        self.contact_worker.start()

    # -------------------------------------------------------------------------
    # Signal Handlers
    # -------------------------------------------------------------------------

    def on_filter_done(self, idx, cleaned_body):
        if idx in self.skipped_indices:
            return
        self.state[idx]["filtered_body"] = cleaned_body
        self.state[idx]["filter_status"] = "done"
        self.state[idx]["category_status"] = "thinking"

        if idx == self.current_index:
            self.update_ui_state()

    def on_category_ready(self, idx, category, urgency, extra_info, dates=None):
        if idx in self.skipped_indices:
            return
        if dates is None:
            dates = []
        self.state[idx]["category"] = category
        self.state[idx]["urgency"] = urgency
        self.state[idx]["extra_info"] = extra_info
        self.state[idx]["dates"] = dates
        self.state[idx]["category_status"] = "done"
        self.state[idx]["reply_status"] = "generating"

        email = self.emails[idx]
        filtered_body = self.state[idx].get("filtered_body", email.get("body", ""))

        # Push categorized email to shared DB for ACalendar
        try:
            push_categorized_email({
                "email_entry_id": email.get("entry_id", ""),
                "email_subject": email.get("subject", ""),
                "email_sender": email.get("sender", ""),
                "email_body": filtered_body,
                "category": category,
                "urgency": urgency,
                "extra_info": extra_info,
            })
        except Exception:
            pass  # Non-critical; don't disrupt the triage workflow

        # Push calendar events directly — triage agent now extracts dates
        if dates:
            try:
                from shared_tools.ipc_bridge import push_calendar_events
                events_to_push = []
                for d in dates:
                    events_to_push.append({
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
                if events_to_push:
                    push_calendar_events(events_to_push)
            except Exception:
                pass  # Non-critical

        if idx == self.current_index:
            self.update_ui_state()

    def on_workflow_generated(self, idx, text):
        if idx in self.skipped_indices:
            return
        self.state[idx]["workflow_text"] = text
        self.state[idx]["workflow_status"] = "done"
        if idx == self.current_index:
            self.update_ui_state()

    def on_reply_generated(self, idx, text):
        if idx in self.skipped_indices:
            return
        import os
        # Only inject if it's the raw text from LLM (not already HTML or error)
        if not text.startswith("Error generating"):
            body_html = "".join(f"<p>{line}</p>" if line.strip() else "<br>" for line in text.split("\n"))
            sig_path = os.path.join(_AMAIL_ROOT, "knowledge/amy_signature.html")
            signature_html = ""
            if os.path.exists(sig_path):
                with open(sig_path, "r", encoding="utf-8") as f:
                    signature_html = f.read()
            full_html = f'<div style="font-family: Arial, sans-serif; font-size: 11pt;">{body_html}</div><br><br>{signature_html}'
            self.state[idx]["reply_text"] = full_html
        else:
            self.state[idx]["reply_text"] = text
            
        self.state[idx]["reply_status"] = "done"

        if idx == self.current_index:
            self.update_ui_state()

    def on_contacts_loaded(self, contacts: list[dict]):
        """Store fetched contacts and propagate to all autocomplete widgets."""
        self.contacts_cache = contacts
        self.le_reply_receiver.set_contacts(contacts)
        self.cc_section.set_contacts(contacts)

    def on_grammar_polished(self, idx, polished_text):
        if idx != self.current_index:
            return

        self.btn_grammar.setText("✏️ Polish (G)")
        self.btn_grammar.setEnabled(True)

        if polished_text.startswith("Error polishing"):
            QMessageBox.warning(self, "Grammar Polish Failed", polished_text)
            return

        body_html = "".join(
            f"<p>{line}</p>" if line.strip() else "<br>"
            for line in polished_text.split("\n")
        )
        sig_path = os.path.abspath("knowledge/amy_signature.html")
        signature_html = ""
        if os.path.exists(sig_path):
            with open(sig_path, "r", encoding="utf-8") as f:
                signature_html = f.read()
        full_html = f'<div style="font-family: Arial, sans-serif; font-size: 11pt;">{body_html}</div><br><br>{signature_html}'

        self.state[idx]["reply_text"] = full_html
        self.txt_reply_content.setHtml(full_html)

    # -------------------------------------------------------------------------
    # UI Updates
    # -------------------------------------------------------------------------

    def load_email(self, idx):
        if idx < 0 or idx >= len(self.emails):
            return

        # Save current draft edits before switching
        cur = self.state[self.current_index]
        if cur["reply_status"] == "done" and cur["send_status"] != "sent":
            cur["reply_text"] = self.txt_reply_content.toHtml()
            cur["reply_cc"] = self.cc_section.get_cc_string()

        self.current_index = idx
        email = self.emails[idx]

        # Left panel
        self.lbl_orig_subject.setText(f"Subject: {email['subject']}")
        self.lbl_orig_time.setText(f"Received: {email.get('received_time', 'Unknown')}")
        self.lbl_orig_sender.setText(f"Sender: {email['sender']}")
        self.lbl_orig_cc.setText(f"CC: {email.get('cc', '')}")
        # Show raw body initially; overlay will cover it if still filtering
        st = self.state[idx]
        if st["filter_status"] == "done":
            self.txt_orig_content.setPlainText(st["filtered_body"])
        else:
            self.txt_orig_content.setPlainText(email["body"])

        # Right panel — static fields
        self.le_reply_subject.setText(f"RE: {email['subject']}")
        self.le_reply_receiver.setText(email["sender"])
        
        # Right panel - editable fields loaded from state or default to email CC
        cc_str = st.get("reply_cc") or email.get("cc", "")
        self.cc_section.set_from_cc_string(cc_str)

        self.update_ui_state()

    def update_ui_state(self):
        if len(self.emails) == 0 or self._all_emails_done():
            self.empty_label.setVisible(True)
            self.splitter.setVisible(False)
            self.lbl_counter.setText("0/0")
            return
        self.empty_label.setVisible(False)
        self.splitter.setVisible(True)

        pending = len(self.pending_emails)
        if pending > 0:
            self.lbl_counter.setText(f"{self.current_index + 1}/{len(self.emails)}+{pending}")
        else:
            self.lbl_counter.setText(f"{self.current_index + 1}/{len(self.emails)}")
        st = self.state[self.current_index]

        # --- Attachment count on button ---
        if st["attachment_count"] is None:
            entry_id = self.emails[self.current_index].get("entry_id", "")
            if entry_id:
                st["attachment_count"] = len(fetch_attachments_for_email(entry_id))
            else:
                st["attachment_count"] = 0
        att_n = st["attachment_count"]
        if att_n > 0:
            self.btn_attachment.setText(f"📎 Attach ({att_n})")
            self.btn_attachment.setStyleSheet(
                "background-color: #5B5EA6; color: white; font-weight: bold; border-radius: 4px; padding: 4px 12px;"
            )
        else:
            self.btn_attachment.setText("📎 Attach (Q)")
            self.btn_attachment.setStyleSheet(
                "background-color: #9E9E9E; color: white; font-weight: bold; border-radius: 4px; padding: 4px 12px;"
            )

        # --- Left panel: filter overlay ---
        if st["filter_status"] == "filtering":
            self.filter_overlay.setVisible(True)
        else:
            self.filter_overlay.setVisible(False)
            self.txt_orig_content.setPlainText(st["filtered_body"])

        # --- Triage labels ---
        if st["category_status"] == "skipped":
            self.lbl_category.setText("Category: ⏭️ Skipped")
            self.lbl_urgency.setText("Urgency: ⏭️ Skipped")
            self.lbl_urgency.setStyleSheet("color: #8B4513; background-color: #FFF3E0; padding: 6px; border-radius: 4px; font-weight: bold;")
            self.lbl_extra_info.setText("Extra Info: ⏭️ Skipped")
        elif st["category_status"] == "pending":
            self.lbl_category.setText("Category: ⏳ Waiting for filter...")
            self.lbl_urgency.setText("Urgency: ⏳ Waiting for filter...")
            self.lbl_urgency.setStyleSheet("color: #3C4043; background-color: #F8F9FA; padding: 6px; border-radius: 4px; font-weight: bold;")
            self.lbl_extra_info.setText("Extra Info: ⏳ Waiting for filter...")
        elif st["category_status"] == "thinking":
            self.lbl_category.setText("Category: ⏳ Thinking...")
            self.lbl_urgency.setText("Urgency: ⏳ Thinking...")
            self.lbl_urgency.setStyleSheet("color: #3C4043; background-color: #F8F9FA; padding: 6px; border-radius: 4px; font-weight: bold;")
            self.lbl_extra_info.setText("Extra Info: ⏳ Thinking...")
        else:
            self.lbl_category.setText(f"Category: {st['category']}")
            
            # Dynamic Urgency Color
            urgency_text = st['urgency'].strip().upper()
            if any(w in urgency_text for w in ['HIGH', 'URGENT', 'ASAP', 'IMMEDIATE', 'CRITICAL']):
                u_color, u_bg = "#B31412", "#FCE8E6" # Red
            elif any(w in urgency_text for w in ['LOW', 'NORMAL', 'ROUTINE', 'STANDARD']):
                u_color, u_bg = "#0D652D", "#E6F4EA" # Green
            elif any(w in urgency_text for w in ['MEDIUM', 'MODERATE']):
                u_color, u_bg = "#E65100", "#FFF3E0" # Orange
            else:
                u_color, u_bg = "#3C4043", "#F8F9FA" # Default Grey
                
            self.lbl_urgency.setText(f"Urgency: {st['urgency']}")
            self.lbl_urgency.setStyleSheet(f"color: {u_color}; background-color: {u_bg}; padding: 6px; border-radius: 4px; font-weight: bold;")
            
            self.lbl_extra_info.setText(f"Extra Info: {st['extra_info']}")

        # --- Workflow Button State ---
        if st["category_status"] == "done":
            self.btn_workflow.setEnabled(True)
            if st["workflow_status"] == "generating":
                self.btn_workflow.setText("⏳ Generating Workflow...")
            else:
                self.btn_workflow.setText("📝 View/Edit Workflow (W)")
        else:
            self.btn_workflow.setEnabled(False)
            self.btn_workflow.setText("📝 View/Edit Workflow (W)")

        # --- Determine error state for regenerate ---
        has_filter_error = st["filtered_body"].startswith("Error filtering:")
        has_triage_error = st["category"] == "Error"
        has_reply_error = st["reply_text"].startswith("Error generating reply:")

        # --- Reply content & controls ---
        if st["send_status"] == "skipped":
            self.txt_reply_content.setPlainText("⏭️ Skipped")
            self.txt_reply_content.setEnabled(False)
            self.le_reply_receiver.setEnabled(False)
            self.btn_clear_receiver.setEnabled(False)
            self.cc_section.setEnabled(False)
            self.le_reply_subject.setEnabled(False)
            self.btn_send.setText("⏭️ Skipped")
            self.btn_send.setStyleSheet(
                "background-color: #6B8E23; color: white; font-weight: bold; border-radius: 4px;"
            )
            self.btn_send.setEnabled(False)
            self.btn_regenerate.setEnabled(False)
            self.btn_grammar.setEnabled(False)
            self.btn_save_reply.setEnabled(False)
            self.btn_save_facts.setEnabled(False)
            self.btn_skip_read.setEnabled(False)
            self.btn_skip_unread.setEnabled(False)
        elif st["send_status"] == "sent":
            self.txt_reply_content.setHtml(st["reply_text"])
            self.txt_reply_content.setEnabled(False)
            self.le_reply_receiver.setEnabled(False)
            self.btn_clear_receiver.setEnabled(False)
            self.cc_section.setEnabled(False)
            self.le_reply_subject.setEnabled(False)
            self.btn_send.setText("📨 Sent")
            self.btn_send.setStyleSheet(
                "background-color: #888888; color: white; font-weight: bold; border-radius: 4px;"
            )
            self.btn_send.setEnabled(False)
            self.btn_regenerate.setEnabled(False)
            self.btn_grammar.setEnabled(False)
            self.btn_save_reply.setEnabled(False)
            self.btn_save_facts.setEnabled(True)
            self.btn_skip_read.setEnabled(False)
            self.btn_skip_unread.setEnabled(False)
        elif st["reply_status"] == "done":
            if st["reply_text"].startswith("Error generating"):
                self.txt_reply_content.setPlainText(st["reply_text"])
            else:
                self.txt_reply_content.setHtml(st["reply_text"])
            self.txt_reply_content.setEnabled(True)
            self.le_reply_receiver.setEnabled(True)
            self.btn_clear_receiver.setEnabled(True)
            self.cc_section.setEnabled(True)
            self.le_reply_subject.setEnabled(True)
            self.btn_send.setText("📨 Send (1)")
            self.btn_send.setStyleSheet(
                "background-color: #0078D4; color: white; font-weight: bold; border-radius: 4px;"
            )
            self.btn_send.setEnabled(True)
            self.btn_regenerate.setEnabled(True)
            self.btn_grammar.setEnabled(True)
            self.btn_save_reply.setEnabled(True)
            self.btn_save_facts.setEnabled(True)
            self.btn_skip_read.setEnabled(True)
            self.btn_skip_unread.setEnabled(True)
        elif st["reply_status"] == "generating":
            self.txt_reply_content.setPlainText("⏳ Generating reply...\nPlease wait.")
            self.txt_reply_content.setEnabled(False)
            self.btn_send.setEnabled(False)
            self.btn_regenerate.setEnabled(False)
            self.btn_grammar.setEnabled(False)
            self.btn_save_reply.setEnabled(False)
            self.btn_save_facts.setEnabled(False)
            self.btn_skip_read.setEnabled(True)
            self.btn_skip_unread.setEnabled(True)
        else:
            # pending — waiting for earlier stages
            if st["filter_status"] == "filtering":
                self.txt_reply_content.setPlainText("⏳ Waiting for filter...")
            elif st["category_status"] == "thinking":
                self.txt_reply_content.setPlainText("⏳ Waiting for categorization...")
            else:
                self.txt_reply_content.setPlainText("⏳ Waiting...")
            self.txt_reply_content.setEnabled(False)
            self.btn_send.setEnabled(False)
            self.btn_save_reply.setEnabled(False)
            self.btn_save_facts.setEnabled(False)
            self.btn_regenerate.setEnabled(has_filter_error or has_triage_error)
            self.btn_grammar.setEnabled(False)
            self.btn_skip_read.setEnabled(True)
            self.btn_skip_unread.setEnabled(True)

        self.btn_prev.setEnabled(self.current_index > 0)
        self.btn_next.setEnabled(self.current_index < len(self.emails) - 1)

        # Save Key Facts is available as soon as the email is filtered
        self.btn_save_facts.setEnabled(
            st.get("filter_status") == "done" and st.get("send_status") != "skipped"
        )

    # -------------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------------

    def open_attachment_dialog(self):
        idx = self.current_index
        email = self.emails[idx]
        dialog = AttachmentDialog(
            entry_id=email.get("entry_id", ""),
            subject=email.get("subject", ""),
            download_dir=self.download_dir,
            parent=self,
        )
        dialog.exec()

    def open_workflow_dialog(self):
        idx = self.current_index
        dialog = WorkflowDialog(idx, self.emails[idx], self.state[idx], parent=self)
        dialog.exec()

    def regenerate_workflow(self, idx):
        self.state[idx]["workflow_status"] = "generating"
        self.state[idx]["workflow_text"] = ""
        st = self.state[idx]
        email = self.emails[idx]
        self.workflow_queue.put((idx, email, st["filtered_body"], st["category"], st["urgency"], st["extra_info"]))
        if idx == self.current_index:
            self.update_ui_state()

    def save_workflow_feedback(self, idx, text):
        import json
        import os
        self.state[idx]["workflow_text"] = text
        email = self.emails[idx]
        st = self.state[idx]
        
        os.makedirs("knowledge", exist_ok=True)
        with open(os.path.join(_AMAIL_ROOT, "knowledge/workflow_examples.jsonl"), "a", encoding="utf-8") as f:
            data = {
                "email_subject": email["subject"],
                "category": st["category"],
                "urgency": st["urgency"],
                "extra_info": st["extra_info"],
                "expected_workflow": text
            }
            f.write(json.dumps(data) + "\n")

    def save_reply_feedback(self):
        import json
        import os
        idx = self.current_index
        st = self.state[idx]
        email = self.emails[idx]
        
        reply_text = self.txt_reply_content.toPlainText() # use plain text to avoid pure HTML styling mess in prompt
        
        os.makedirs("knowledge", exist_ok=True)
        with open(os.path.join(_AMAIL_ROOT, "knowledge/reply_examples.jsonl"), "a", encoding="utf-8") as f:
            data = {
                "email_subject": email["subject"],
                "category": st["category"],
                "urgency": st["urgency"],
                "extra_info": st["extra_info"],
                "expected_reply": reply_text
            }
            f.write(json.dumps(data) + "\n")
        
        QMessageBox.information(self, "Success", "Reply saved as training example!")

    def save_key_facts(self):
        import json
        idx = self.current_index
        st = self.state[idx]
        email = self.emails[idx]

        # Visual feedback — show extraction in progress
        self.btn_save_facts.setText("⏳ Extracting Facts...")
        self.btn_save_facts.setEnabled(False)
        QApplication.processEvents()

        inputs = {
            "email_subject": email["subject"],
            "email_content": st["filtered_body"] or email["body"],
            "email_category": st["category"],
            "email_context": st["extra_info"],
        }

        try:
            result = FactExtractorCrew().crew().kickoff(inputs=inputs)
            raw = result.raw if hasattr(result, 'raw') else str(result)
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            facts = json.loads(cleaned)
            if not isinstance(facts, list):
                facts = []
        except Exception as e:
            self.btn_save_facts.setText("📋 Save Key Facts (5)")
            self.btn_save_facts.setEnabled(True)
            QMessageBox.warning(self, "Extraction Failed", f"Could not extract facts:\n{e}")
            return

        # Restore button
        self.btn_save_facts.setText("📋 Save Key Facts (5)")
        self.btn_save_facts.setEnabled(True)

        if not facts:
            QMessageBox.information(self, "No Facts", "No critical facts found in this email.")
            return

        save_facts(facts, email["subject"], email["sender"])
        QMessageBox.information(
            self, "Facts Saved",
            f"{len(facts)} fact(s) saved to the project knowledge base."
        )

    def prev_email(self):
        self.load_email(self.current_index - 1)

    def next_email(self):
        self.load_email(self.current_index + 1)

    def open_mail_lister(self):
        dialog = MailListerDialog(self.processed_entry_ids, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            emails = dialog.get_selected_emails()
            if emails:
                self.batch_queue_emails(emails)

    def batch_queue_emails(self, emails: list):
        for email in emails:
            eid = email.get("entry_id", "")
            if eid:
                self.processed_entry_ids.add(eid)
            self.pending_emails.append(email)
        self._promote_pending()

    def _promote_pending(self):
        """Move emails from pending to the active pipeline, up to max_active."""
        had_emails = len(self.emails) > 0

        while self.pending_emails and self._count_active() < self.max_active:
            email = self.pending_emails.pop(0)
            idx = self._append_email(email)
            self.filter_queue.put((idx, email))

        if not had_emails and self.emails:
            self.load_email(0)
        elif self.emails and self.current_index < len(self.emails):
            st = self.state.get(self.current_index)
            if st and st["send_status"] in ("sent", "skipped"):
                for i in range(len(self.emails)):
                    s = self.state.get(i)
                    if s and s["send_status"] not in ("sent", "skipped"):
                        self.load_email(i)
                        break

        self.update_ui_state()

    def regenerate_current(self):
        """Regenerate from the earliest failed stage for the current email."""
        idx = self.current_index
        st = self.state[idx]
        email = self.emails[idx]

        if st["filtered_body"].startswith("Error filtering:"):
            # Filter failed — re-run entire pipeline
            st["filter_status"] = "filtering"
            st["category_status"] = "pending"
            st["reply_status"] = "pending"
            st["filtered_body"] = ""
            st["category"] = ""
            st["reply_text"] = ""
            self.txt_orig_content.setPlainText(email["body"])
            self.update_ui_state()

            self._regen_worker = RegenerateWorker(idx, email, mode="filter")
            self._regen_worker.filter_done.connect(self.on_filter_done)
            self._regen_worker.triage_done.connect(self.on_category_ready)
            self._regen_worker.reply_done.connect(self.on_reply_generated)
            self._regen_worker.start()

        elif st["category"] == "Error":
            # Triage failed — re-run triage + reply
            st["category_status"] = "thinking"
            st["reply_status"] = "pending"
            st["category"] = ""
            st["reply_text"] = ""
            self.update_ui_state()

            self._regen_worker = RegenerateWorker(
                idx, email, mode="triage",
                filtered_body=st["filtered_body"]
            )
            self._regen_worker.triage_done.connect(self.on_category_ready)
            self._regen_worker.reply_done.connect(self.on_reply_generated)
            self._regen_worker.start()

        else:
            # Only reply failed or user wants a new draft
            st["reply_status"] = "generating"
            st["reply_text"] = ""
            self.update_ui_state()

            self._regen_worker = RegenerateWorker(
                idx, email, mode="reply",
                filtered_body=st["filtered_body"],
                category=st["category"], urgency=st["urgency"], extra_info=st["extra_info"]
            )
            self._regen_worker.reply_done.connect(self.on_reply_generated)
            self._regen_worker.start()

    def grammar_polish(self):
        idx = self.current_index
        st = self.state[idx]

        if st["reply_status"] != "done":
            return

        draft_text = self.txt_reply_content.toPlainText()
        if not draft_text.strip():
            return

        body_only = _strip_signature(draft_text)
        if not body_only.strip():
            return

        self.btn_grammar.setText("Polishing...")
        self.btn_grammar.setEnabled(False)
        QApplication.processEvents()

        self._grammar_worker = GrammarPolishWorker(idx, body_only)
        self._grammar_worker.polish_done.connect(self.on_grammar_polished)
        self._grammar_worker.start()

    def send_email(self):
        recipient = self.le_reply_receiver.text()
        cc_list = self.cc_section.get_cc_string()
        subject = self.le_reply_subject.text()
        body = self.txt_reply_content.toHtml()

        tool = OutlookSendTool(
            signature_html_path=os.path.join(_AMAIL_ROOT, "knowledge/amy_signature.html"),
            signature_image_specs=[
                (os.path.join(_AMAIL_ROOT, "knowledge/logo_meritor_welink.png"), "logo_meritor_welink.png"),
                (os.path.join(_AMAIL_ROOT, "knowledge/logo_hia_awards.png"), "logo_hia_awards.png"),
                (os.path.join(_AMAIL_ROOT, "knowledge/icon_instagram.png"), "icon_instagram.png"),
                (os.path.join(_AMAIL_ROOT, "knowledge/icon_facebook.png"), "icon_facebook.png"),
            ],
        )
        result = tool._run(recipient=recipient, subject=subject, body=body, cc=cc_list, is_html=True)

        if "successfully sent" in result.lower():
            # Mark the original email as read in Outlook
            entry_id = self.emails[self.current_index].get("entry_id", "")
            if entry_id:
                mark_email_as_read(entry_id)

            QMessageBox.information(self, "Success", "Email sent and marked as read!")
            self.state[self.current_index]["send_status"] = "sent"
            self.state[self.current_index]["reply_text"] = body

            # Block this EntryID for the session; promote from pending if room
            if entry_id:
                self.processed_entry_ids.add(entry_id)

            self._promote_pending()

            # Auto jump to next unsent
            for i in range(self.current_index + 1, len(self.emails)):
                if self.state[i]["send_status"] != "sent":
                    self.load_email(i)
                    break
        else:
            QMessageBox.warning(self, "Error", f"Failed to send email:\n{result}")

    def _skip_current(self, mark_read: bool):
        """Skip the current email, optionally marking it as read in Outlook.
        Immediately marks all agent sections as skipped and prevents workers
        from processing this index further."""
        idx = self.current_index
        entry_id = self.emails[idx].get("entry_id", "")

        if mark_read and entry_id:
            mark_email_as_read(entry_id)

        # Add to skipped set so workers abandon any in-flight work
        self.skipped_indices.add(idx)

        # Mark all agent sections as skipped
        st = self.state[idx]
        st["send_status"] = "skipped"
        st["filter_status"] = "skipped"
        st["category_status"] = "skipped"
        st["reply_status"] = "skipped"
        st["workflow_status"] = "skipped"
        st["filtered_body"] = st["filtered_body"] or "⏭️ Skipped"
        st["category"] = st["category"] or "⏭️ Skipped"
        st["urgency"] = st["urgency"] or "⏭️ Skipped"
        st["extra_info"] = st["extra_info"] or "⏭️ Skipped"
        st["reply_text"] = "⏭️ Skipped"
        st["workflow_text"] = st["workflow_text"] or "⏭️ Skipped"

        # Block this EntryID for the session; promote from pending if room
        if entry_id:
            self.processed_entry_ids.add(entry_id)

        self._promote_pending()

        # Auto jump to next unskipped/unsent email
        for i in range(idx + 1, len(self.emails)):
            if self.state[i]["send_status"] not in ("sent", "skipped"):
                self.load_email(i)
                return
        # If nothing ahead, stay on current
        self.update_ui_state()

    def skip_read(self):
        self._skip_current(mark_read=True)

    def skip_unread(self):
        self._skip_current(mark_read=False)

    def _poll_nav_requests(self):
        """Check if ACalendar has requested navigation to a specific email."""
        from pathlib import Path
        nav_path = Path.home() / ".crewai" / "nav_request.json"
        if not nav_path.exists():
            return
        try:
            with open(nav_path, "r") as f:
                request = json.load(f)
            target_entry_id = request.get("target_entry_id")
            if not target_entry_id:
                return
            for i, email in enumerate(self.emails):
                if email.get("entry_id") == target_entry_id:
                    self.load_email(i)
                    self.activateWindow()
                    self.raise_()
                    break
            nav_path.unlink(missing_ok=True)
        except Exception:
            try:
                nav_path.unlink(missing_ok=True)
            except Exception:
                pass

    def closeEvent(self, event):
        self.nav_poll_timer.stop()
        for worker_name in ('filter_worker', 'triage_worker', 'reply_worker', 'contact_worker'):
            worker = getattr(self, worker_name, None)
            if worker:
                worker.stop()
                worker.wait()
        super().closeEvent(event)


# =============================================================================
# Entry Point
# =============================================================================

def show_triage_report(raw_emails, processed_entry_ids: set[str] = None):
    """Launch the GUI with a list of raw email dicts and optional session blocklist."""
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)

    # Register with IPC bridge so ACalendar can detect AMail
    init_shared_db()
    register_app("amail")

    window = TriageWindow(raw_emails, processed_entry_ids)
    window.show()
    app.exec()

    unregister_app("amail")
