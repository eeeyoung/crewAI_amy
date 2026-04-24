import sys
import json
import queue
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QTextEdit, QPushButton, QSplitter, QMessageBox, QFrame
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont

from amy.crew import TriageSingleCrew, ReplyGeneratorCrew
from amy.tools.outlook_tool import OutlookSendTool


class TriageWorker(QThread):
    """Processes emails one-by-one through the triage agent.
    Emits (index, category, extra_info) when each email is classified.
    Pushes classified emails into the reply_queue for the ReplyWorker.
    """
    category_ready = pyqtSignal(int, str, str)

    def __init__(self, emails, reply_queue, parent=None):
        super().__init__(parent)
        self.emails = emails
        self.reply_queue = reply_queue
        self.running = True

    def run(self):
        for idx, email in enumerate(self.emails):
            if not self.running:
                break

            inputs = {
                "email_subject": email["subject"],
                "email_sender": email["sender"],
                "email_content": email["body"],
            }

            category = "Uncategorized"
            extra_info = ""

            try:
                crew_instance = TriageSingleCrew().crew()
                result = crew_instance.kickoff(inputs=inputs)
                raw = result.raw if hasattr(result, 'raw') else str(result)

                # Try to parse JSON from the result
                try:
                    # Strip markdown code fences if present
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
            # Queue this email for the reply worker
            self.reply_queue.put((idx, email, category, extra_info))

    def stop(self):
        self.running = False


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

            if item is None:  # Poison pill to stop
                break

            idx, email, category, extra_info = item

            inputs = {
                "email_subject": email["subject"],
                "email_content": email["body"],
                "email_category": category,
                "email_context": extra_info,
            }

            try:
                crew_instance = ReplyGeneratorCrew().crew()
                result = crew_instance.kickoff(inputs=inputs)
                draft_text = result.raw if hasattr(result, 'raw') else str(result)
            except Exception as e:
                draft_text = f"Error generating reply: {str(e)}"

            self.reply_generated.emit(idx, draft_text)

    def stop(self):
        self.running = False
        self.reply_queue.put(None)  # Poison pill


class RegenerateWorker(QThread):
    """Re-runs triage or reply for a single email depending on which stage failed."""
    triage_done = pyqtSignal(int, str, str)
    reply_done = pyqtSignal(int, str)

    def __init__(self, idx, email, mode, category="", extra_info="", parent=None):
        super().__init__(parent)
        self.idx = idx
        self.email = email
        self.mode = mode  # "triage" or "reply"
        self.category = category
        self.extra_info = extra_info

    def run(self):
        if self.mode == "triage":
            inputs = {
                "email_subject": self.email["subject"],
                "email_sender": self.email["sender"],
                "email_content": self.email["body"],
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

            self.triage_done.emit(self.idx, category, extra_info)

            # Now also generate the reply
            self.category = category
            self.extra_info = extra_info
            self._run_reply()

        elif self.mode == "reply":
            self._run_reply()

    def _run_reply(self):
        inputs = {
            "email_subject": self.email["subject"],
            "email_content": self.email["body"],
            "email_category": self.category,
            "email_context": self.extra_info,
        }
        try:
            result = ReplyGeneratorCrew().crew().kickoff(inputs=inputs)
            draft_text = result.raw if hasattr(result, 'raw') else str(result)
        except Exception as e:
            draft_text = f"Error generating reply: {str(e)}"
        self.reply_done.emit(self.idx, draft_text)


class TriageWindow(QMainWindow):
    def __init__(self, raw_emails):
        super().__init__()
        self.emails = raw_emails
        self.current_index = 0

        # Per-email state
        self.state = {}
        for i in range(len(self.emails)):
            self.state[i] = {
                "category": "",
                "extra_info": "",
                "category_status": "thinking",   # thinking → done
                "reply_text": "",
                "reply_status": "pending",        # pending → generating → done
                "send_status": "unsent",          # unsent → sent
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

        # Content
        left_layout.addWidget(QLabel("Content:"))
        self.txt_orig_content = QTextEdit()
        self.txt_orig_content.setReadOnly(True)
        self.txt_orig_content.setStyleSheet("background-color: #f5f5f5; color: #1a1a1a;")
        left_layout.addWidget(self.txt_orig_content)

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
        self.lbl_category = QLabel("Category: ⏳ Thinking...")
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

    def start_workers(self):
        self.reply_queue = queue.Queue()

        # Mark all as "generating" for reply status once triage finishes
        self.triage_worker = TriageWorker(self.emails, self.reply_queue)
        self.triage_worker.category_ready.connect(self.on_category_ready)
        self.triage_worker.start()

        self.reply_worker = ReplyWorker(self.reply_queue)
        self.reply_worker.reply_generated.connect(self.on_reply_generated)
        self.reply_worker.start()

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
        self.txt_orig_content.setPlainText(email["body"])

        # Right panel — static fields
        self.le_reply_subject.setText(f"RE: {email['subject']}")
        self.le_reply_receiver.setText(email["sender"])

        self.update_ui_state()

    def update_ui_state(self):
        self.lbl_counter.setText(f"{self.current_index + 1} / {len(self.emails)}")
        st = self.state[self.current_index]

        # --- Category label ---
        if st["category_status"] == "thinking":
            self.lbl_category.setText("Category: ⏳ Thinking...")
        else:
            self.lbl_category.setText(f"Category: {st['category']} | {st['extra_info']}")

        # --- Reply content & controls ---
        # Determine if regenerate should be available
        has_error = (
            st["category"] == "Error"
            or st["reply_text"].startswith("Error generating reply:")
        )

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
            self.btn_regenerate.setEnabled(True)  # Always allow regenerating a completed draft
        elif st["reply_status"] == "generating":
            self.txt_reply_content.setPlainText("⏳ Generating reply...\nPlease wait.")
            self.txt_reply_content.setEnabled(False)
            self.btn_send.setEnabled(False)
            self.btn_regenerate.setEnabled(False)
        else:
            # pending — waiting for categorization first
            self.txt_reply_content.setPlainText("⏳ Waiting for categorization...")
            self.txt_reply_content.setEnabled(False)
            self.btn_send.setEnabled(False)
            self.btn_regenerate.setEnabled(has_error)

        self.btn_prev.setEnabled(self.current_index > 0)
        self.btn_next.setEnabled(self.current_index < len(self.emails) - 1)

    def prev_email(self):
        self.load_email(self.current_index - 1)

    def next_email(self):
        self.load_email(self.current_index + 1)

    def regenerate_current(self):
        """Regenerate triage or reply for the current email based on which stage failed."""
        idx = self.current_index
        st = self.state[idx]
        email = self.emails[idx]

        if st["category"] == "Error":
            # Triage failed — re-run triage, which will also chain into reply
            st["category_status"] = "thinking"
            st["reply_status"] = "pending"
            st["reply_text"] = ""
            self.update_ui_state()

            self._regen_worker = RegenerateWorker(idx, email, mode="triage")
            self._regen_worker.triage_done.connect(self.on_category_ready)
            self._regen_worker.reply_done.connect(self.on_reply_generated)
            self._regen_worker.start()
        else:
            # Triage was fine, only reply needs regeneration
            st["reply_status"] = "generating"
            st["reply_text"] = ""
            self.update_ui_state()

            self._regen_worker = RegenerateWorker(
                idx, email, mode="reply",
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
        if hasattr(self, 'triage_worker'):
            self.triage_worker.stop()
            self.triage_worker.wait()
        if hasattr(self, 'reply_worker'):
            self.reply_worker.stop()
            self.reply_worker.wait()
        super().closeEvent(event)


def show_triage_report(raw_emails):
    """Launch the GUI with a list of raw email dicts."""
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)

    window = TriageWindow(raw_emails)
    window.show()
    app.exec()
