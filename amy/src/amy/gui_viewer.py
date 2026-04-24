import sys
import json
import queue
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QTextEdit, QPushButton, QSplitter, QMessageBox, QFrame,
    QStackedLayout
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont

from amy.crew import MessageFilterCrew, TriageSingleCrew, ReplyGeneratorCrew
from amy.tools.outlook_tool import OutlookSendTool


# =============================================================================
# Background Workers
# =============================================================================

class FilterWorker(QThread):
    """Filters emails one-by-one, stripping signatures and boilerplate.
    Emits (index, cleaned_body) when each email is filtered.
    Pushes filtered emails into the triage_queue.
    """
    filter_done = pyqtSignal(int, str)

    def __init__(self, emails, triage_queue, parent=None):
        super().__init__(parent)
        self.emails = emails
        self.triage_queue = triage_queue
        self.running = True

    def run(self):
        for idx, email in enumerate(self.emails):
            if not self.running:
                break

            try:
                result = MessageFilterCrew().crew().kickoff(
                    inputs={"email_body": email["body"]}
                )
                cleaned = result.raw if hasattr(result, 'raw') else str(result)
            except Exception as e:
                cleaned = f"Error filtering: {str(e)}"

            self.filter_done.emit(idx, cleaned)
            self.triage_queue.put((idx, email, cleaned))

    def stop(self):
        self.running = False


class TriageWorker(QThread):
    """Processes filtered emails one-by-one through the triage agent."""
    category_ready = pyqtSignal(int, str, str)

    def __init__(self, triage_queue, reply_queue, parent=None):
        super().__init__(parent)
        self.triage_queue = triage_queue
        self.reply_queue = reply_queue
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

            inputs = {
                "email_subject": email["subject"],
                "email_sender": email["sender"],
                "email_content": filtered_body,
            }

            category = "Uncategorized"
            extra_info = ""

            try:
                result = TriageSingleCrew().crew().kickoff(inputs=inputs)
                raw = result.raw if hasattr(result, 'raw') else str(result)

                try:
                    cleaned = raw.strip()
                    if cleaned.startswith("```"):
                        cleaned = cleaned.split("\n", 1)[-1]
                        cleaned = cleaned.rsplit("```", 1)[0]
                    parsed = json.loads(cleaned.strip())
                    category = parsed.get("category", "Uncategorized")
                    extra_info = parsed.get("extra_info", "")
                except (json.JSONDecodeError, AttributeError):
                    category = raw[:100]
                    extra_info = "Could not parse structured output"
            except Exception as e:
                category = "Error"
                extra_info = str(e)

            self.category_ready.emit(idx, category, extra_info)
            self.reply_queue.put((idx, email, filtered_body, category, extra_info))

    def stop(self):
        self.running = False
        self.triage_queue.put(None)


class ReplyWorker(QThread):
    """Picks categorized emails from the queue and generates drafts one-by-one."""
    reply_generated = pyqtSignal(int, str)

    def __init__(self, reply_queue, parent=None):
        super().__init__(parent)
        self.reply_queue = reply_queue
        self.running = True

    def run(self):
        while self.running:
            try:
                item = self.reply_queue.get(timeout=1)
            except queue.Empty:
                continue
            if item is None:
                break

            idx, email, filtered_body, category, extra_info = item

            inputs = {
                "email_subject": email["subject"],
                "email_content": filtered_body,
                "email_category": category,
                "email_context": extra_info,
            }

            try:
                result = ReplyGeneratorCrew().crew().kickoff(inputs=inputs)
                draft_text = result.raw if hasattr(result, 'raw') else str(result)
            except Exception as e:
                draft_text = f"Error generating reply: {str(e)}"

            self.reply_generated.emit(idx, draft_text)

    def stop(self):
        self.running = False
        self.reply_queue.put(None)


class RegenerateWorker(QThread):
    """Re-runs filter, triage, or reply for a single email depending on which stage failed."""
    filter_done = pyqtSignal(int, str)
    triage_done = pyqtSignal(int, str, str)
    reply_done = pyqtSignal(int, str)

    def __init__(self, idx, email, mode, filtered_body="", category="", extra_info="", parent=None):
        super().__init__(parent)
        self.idx = idx
        self.email = email
        self.mode = mode  # "filter", "triage", or "reply"
        self.filtered_body = filtered_body
        self.category = category
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
        try:
            result = TriageSingleCrew().crew().kickoff(inputs=inputs)
            raw = result.raw if hasattr(result, 'raw') else str(result)
            try:
                cleaned = raw.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("\n", 1)[-1]
                    cleaned = cleaned.rsplit("```", 1)[0]
                parsed = json.loads(cleaned.strip())
                self.category = parsed.get("category", "Uncategorized")
                self.extra_info = parsed.get("extra_info", "")
            except (json.JSONDecodeError, AttributeError):
                self.category = raw[:100]
                self.extra_info = "Could not parse structured output"
        except Exception as e:
            self.category = "Error"
            self.extra_info = str(e)
        self.triage_done.emit(self.idx, self.category, self.extra_info)

    def _run_reply(self):
        inputs = {
            "email_subject": self.email["subject"],
            "email_content": self.filtered_body,
            "email_category": self.category,
            "email_context": self.extra_info,
        }
        try:
            result = ReplyGeneratorCrew().crew().kickoff(inputs=inputs)
            draft_text = result.raw if hasattr(result, 'raw') else str(result)
        except Exception as e:
            draft_text = f"Error generating reply: {str(e)}"
        self.reply_done.emit(self.idx, draft_text)


# =============================================================================
# Main Window
# =============================================================================

class TriageWindow(QMainWindow):
    def __init__(self, raw_emails):
        super().__init__()
        self.emails = raw_emails
        self.current_index = 0

        # Per-email state
        self.state = {}
        for i in range(len(self.emails)):
            self.state[i] = {
                "filtered_body": "",
                "filter_status": "filtering",     # filtering → done
                "category": "",
                "extra_info": "",
                "category_status": "pending",      # pending → thinking → done
                "reply_text": "",
                "reply_status": "pending",          # pending → generating → done
                "send_status": "unsent",            # unsent → sent
            }

        self.init_ui()

        if self.emails:
            self.load_email(0)
            self.start_workers()

    def init_ui(self):
        self.setWindowTitle("Interactive Triage & Auto-Reply Workstation")
        self.resize(1400, 800)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # --- LEFT PANEL (Original Email) ---
        left_panel = QFrame()
        left_panel.setFrameShape(QFrame.Shape.StyledPanel)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(15, 15, 15, 15)

        header_font = QFont("Segoe UI", 12, QFont.Weight.Bold)

        lbl_orig = QLabel("Original Email")
        lbl_orig.setFont(header_font)
        left_layout.addWidget(lbl_orig)

        # Subject
        self.lbl_orig_subject = QLabel("Subject: ")
        self.lbl_orig_subject.setWordWrap(True)
        self.lbl_orig_subject.setStyleSheet("font-weight: bold;")
        left_layout.addWidget(self.lbl_orig_subject)

        # Sender
        self.lbl_orig_sender = QLabel("Sender: ")
        left_layout.addWidget(self.lbl_orig_sender)

        # Content area with overlay support
        left_layout.addWidget(QLabel("Content:"))

        # Container for content + overlay
        self.content_container = QWidget()
        content_stack = QStackedLayout(self.content_container)
        content_stack.setStackingMode(QStackedLayout.StackingMode.StackAll)

        self.txt_orig_content = QTextEdit()
        self.txt_orig_content.setReadOnly(True)
        self.txt_orig_content.setStyleSheet("background-color: #f5f5f5; color: #1a1a1a;")

        self.filter_overlay = QLabel("🔍 Thinking and filtering...")
        self.filter_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.filter_overlay.setStyleSheet(
            "background-color: rgba(50, 50, 50, 180); "
            "color: white; font-size: 20px; font-weight: bold;"
        )

        content_stack.addWidget(self.txt_orig_content)
        content_stack.addWidget(self.filter_overlay)

        left_layout.addWidget(self.content_container)

        # --- RIGHT PANEL (Draft Reply) ---
        right_panel = QFrame()
        right_panel.setFrameShape(QFrame.Shape.StyledPanel)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(15, 15, 15, 15)

        lbl_draft = QLabel("AI Draft Reply")
        lbl_draft.setFont(header_font)
        right_layout.addWidget(lbl_draft)

        # Subject
        right_layout.addWidget(QLabel("Subject:"))
        self.le_reply_subject = QLineEdit()
        right_layout.addWidget(self.le_reply_subject)

        # Category
        self.lbl_category = QLabel("Category: ⏳ Waiting...")
        self.lbl_category.setStyleSheet("color: #005A9E; font-weight: bold;")
        self.lbl_category.setWordWrap(True)
        right_layout.addWidget(self.lbl_category)

        # Receiver
        right_layout.addWidget(QLabel("Receiver:"))
        self.le_reply_receiver = QLineEdit()
        right_layout.addWidget(self.le_reply_receiver)

        # Content
        right_layout.addWidget(QLabel("Content:"))
        self.txt_reply_content = QTextEdit()
        right_layout.addWidget(self.txt_reply_content)

        # --- BOTTOM CONTROLS ---
        controls_layout = QHBoxLayout()

        self.btn_prev = QPushButton("< Prev")
        self.btn_prev.setMinimumWidth(100)
        self.btn_prev.clicked.connect(self.prev_email)
        controls_layout.addWidget(self.btn_prev)

        self.lbl_counter = QLabel("0 / 0")
        self.lbl_counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_counter.setMinimumWidth(100)
        controls_layout.addWidget(self.lbl_counter)

        self.btn_next = QPushButton("Next >")
        self.btn_next.setMinimumWidth(100)
        self.btn_next.clicked.connect(self.next_email)
        controls_layout.addWidget(self.btn_next)

        controls_layout.addStretch()

        self.btn_regenerate = QPushButton("🔄 Regenerate")
        self.btn_regenerate.setMinimumWidth(130)
        self.btn_regenerate.setMinimumHeight(35)
        self.btn_regenerate.setStyleSheet(
            "background-color: #D83B01; color: white; font-weight: bold; border-radius: 4px;"
        )
        self.btn_regenerate.clicked.connect(self.regenerate_current)
        controls_layout.addWidget(self.btn_regenerate)

        self.btn_send = QPushButton("Confirm and Send")
        self.btn_send.setMinimumWidth(150)
        self.btn_send.setMinimumHeight(35)
        self.btn_send.setStyleSheet(
            "background-color: #0078D4; color: white; font-weight: bold; border-radius: 4px;"
        )
        self.btn_send.clicked.connect(self.send_email)
        controls_layout.addWidget(self.btn_send)

        right_layout.addLayout(controls_layout)

        # Add to splitter
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([600, 800])

    # -------------------------------------------------------------------------
    # Workers
    # -------------------------------------------------------------------------

    def start_workers(self):
        self.triage_queue = queue.Queue()
        self.reply_queue = queue.Queue()

        self.filter_worker = FilterWorker(self.emails, self.triage_queue)
        self.filter_worker.filter_done.connect(self.on_filter_done)
        self.filter_worker.start()

        self.triage_worker = TriageWorker(self.triage_queue, self.reply_queue)
        self.triage_worker.category_ready.connect(self.on_category_ready)
        self.triage_worker.start()

        self.reply_worker = ReplyWorker(self.reply_queue)
        self.reply_worker.reply_generated.connect(self.on_reply_generated)
        self.reply_worker.start()

    # -------------------------------------------------------------------------
    # Signal Handlers
    # -------------------------------------------------------------------------

    def on_filter_done(self, idx, cleaned_body):
        self.state[idx]["filtered_body"] = cleaned_body
        self.state[idx]["filter_status"] = "done"
        self.state[idx]["category_status"] = "thinking"

        if idx == self.current_index:
            self.update_ui_state()

    def on_category_ready(self, idx, category, extra_info):
        self.state[idx]["category"] = category
        self.state[idx]["extra_info"] = extra_info
        self.state[idx]["category_status"] = "done"
        self.state[idx]["reply_status"] = "generating"

        if idx == self.current_index:
            self.update_ui_state()

    def on_reply_generated(self, idx, text):
        self.state[idx]["reply_text"] = text
        self.state[idx]["reply_status"] = "done"

        if idx == self.current_index:
            self.update_ui_state()

    # -------------------------------------------------------------------------
    # UI Updates
    # -------------------------------------------------------------------------

    def load_email(self, idx):
        if idx < 0 or idx >= len(self.emails):
            return

        # Save current draft edits before switching
        cur = self.state[self.current_index]
        if cur["reply_status"] == "done" and cur["send_status"] != "sent":
            cur["reply_text"] = self.txt_reply_content.toPlainText()

        self.current_index = idx
        email = self.emails[idx]

        # Left panel
        self.lbl_orig_subject.setText(f"Subject: {email['subject']}")
        self.lbl_orig_sender.setText(f"Sender: {email['sender']}")

        # Show raw body initially; overlay will cover it if still filtering
        st = self.state[idx]
        if st["filter_status"] == "done":
            self.txt_orig_content.setPlainText(st["filtered_body"])
        else:
            self.txt_orig_content.setPlainText(email["body"])

        # Right panel — static fields
        self.le_reply_subject.setText(f"RE: {email['subject']}")
        self.le_reply_receiver.setText(email["sender"])

        self.update_ui_state()

    def update_ui_state(self):
        self.lbl_counter.setText(f"{self.current_index + 1} / {len(self.emails)}")
        st = self.state[self.current_index]

        # --- Left panel: filter overlay ---
        if st["filter_status"] == "filtering":
            self.filter_overlay.setVisible(True)
        else:
            self.filter_overlay.setVisible(False)
            self.txt_orig_content.setPlainText(st["filtered_body"])

        # --- Category label ---
        if st["category_status"] == "pending":
            self.lbl_category.setText("Category: ⏳ Waiting for filter...")
        elif st["category_status"] == "thinking":
            self.lbl_category.setText("Category: ⏳ Thinking...")
        else:
            self.lbl_category.setText(f"Category: {st['category']} | {st['extra_info']}")

        # --- Determine error state for regenerate ---
        has_filter_error = st["filtered_body"].startswith("Error filtering:")
        has_triage_error = st["category"] == "Error"
        has_reply_error = st["reply_text"].startswith("Error generating reply:")

        # --- Reply content & controls ---
        if st["send_status"] == "sent":
            self.txt_reply_content.setPlainText(st["reply_text"])
            self.txt_reply_content.setEnabled(False)
            self.le_reply_receiver.setEnabled(False)
            self.le_reply_subject.setEnabled(False)
            self.btn_send.setText("Already Sent")
            self.btn_send.setStyleSheet(
                "background-color: #888888; color: white; font-weight: bold; border-radius: 4px;"
            )
            self.btn_send.setEnabled(False)
            self.btn_regenerate.setEnabled(False)
        elif st["reply_status"] == "done":
            self.txt_reply_content.setPlainText(st["reply_text"])
            self.txt_reply_content.setEnabled(True)
            self.le_reply_receiver.setEnabled(True)
            self.le_reply_subject.setEnabled(True)
            self.btn_send.setText("Confirm and Send")
            self.btn_send.setStyleSheet(
                "background-color: #0078D4; color: white; font-weight: bold; border-radius: 4px;"
            )
            self.btn_send.setEnabled(True)
            self.btn_regenerate.setEnabled(True)
        elif st["reply_status"] == "generating":
            self.txt_reply_content.setPlainText("⏳ Generating reply...\nPlease wait.")
            self.txt_reply_content.setEnabled(False)
            self.btn_send.setEnabled(False)
            self.btn_regenerate.setEnabled(False)
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
            self.btn_regenerate.setEnabled(has_filter_error or has_triage_error)

        self.btn_prev.setEnabled(self.current_index > 0)
        self.btn_next.setEnabled(self.current_index < len(self.emails) - 1)

    # -------------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------------

    def prev_email(self):
        self.load_email(self.current_index - 1)

    def next_email(self):
        self.load_email(self.current_index + 1)

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
                category=st["category"], extra_info=st["extra_info"]
            )
            self._regen_worker.reply_done.connect(self.on_reply_generated)
            self._regen_worker.start()

    def send_email(self):
        recipient = self.le_reply_receiver.text()
        subject = self.le_reply_subject.text()
        body = self.txt_reply_content.toPlainText()

        tool = OutlookSendTool()
        result = tool._run(recipient=recipient, subject=subject, body=body)

        if "successfully sent" in result.lower():
            QMessageBox.information(self, "Success", "Email sent successfully!")
            self.state[self.current_index]["send_status"] = "sent"
            self.state[self.current_index]["reply_text"] = body
            self.update_ui_state()

            # Auto jump to next unsent
            for i in range(self.current_index + 1, len(self.emails)):
                if self.state[i]["send_status"] != "sent":
                    self.load_email(i)
                    break
        else:
            QMessageBox.warning(self, "Error", f"Failed to send email:\n{result}")

    def closeEvent(self, event):
        for worker_name in ('filter_worker', 'triage_worker', 'reply_worker'):
            worker = getattr(self, worker_name, None)
            if worker:
                worker.stop()
                worker.wait()
        super().closeEvent(event)


# =============================================================================
# Entry Point
# =============================================================================

def show_triage_report(raw_emails):
    """Launch the GUI with a list of raw email dicts."""
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)

    window = TriageWindow(raw_emails)
    window.show()
    app.exec()
