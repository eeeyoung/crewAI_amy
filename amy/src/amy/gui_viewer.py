import sys
import json
import queue
import os
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QTextEdit, QPushButton, QSplitter, QMessageBox, QFrame,
    QStackedLayout, QDialog
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont

from amy.crew import MessageFilterCrew, TriageSingleCrew, ReplyGeneratorCrew, WorkflowGeneratorCrew
from amy.tools.outlook_tool import OutlookSendTool, mark_email_as_read, mark_email_as_unread


# =============================================================================
# Background Workers
# =============================================================================

class FilterWorker(QThread):
    """Filters emails one-by-one, stripping signatures and boilerplate.
    Emits (index, cleaned_body) when each email is filtered.
    Pushes filtered emails into the triage_queue.
    """
    filter_done = pyqtSignal(int, str)

    def __init__(self, filter_queue, triage_queue, parent=None):
        super().__init__(parent)
        self.filter_queue = filter_queue
        self.triage_queue = triage_queue
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
    category_ready = pyqtSignal(int, str, str, str)

    def __init__(self, triage_queue, reply_queue, workflow_queue, parent=None):
        super().__init__(parent)
        self.triage_queue = triage_queue
        self.reply_queue = reply_queue
        self.workflow_queue = workflow_queue
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
            urgency = ""
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
                    urgency = parsed.get("urgency", "")
                    extra_info = parsed.get("extra_info", "")
                except (json.JSONDecodeError, AttributeError):
                    category = raw[:100]
                    urgency = ""
                    extra_info = "Could not parse structured output"
            except Exception as e:
                category = "Error"
                urgency = ""
                extra_info = str(e)

            self.category_ready.emit(idx, category, urgency, extra_info)
            self.reply_queue.put((idx, email, filtered_body, category, urgency, extra_info))
            self.workflow_queue.put((idx, email, filtered_body, category, urgency, extra_info))

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

            idx, email, filtered_body, category, urgency, extra_info = item

            inputs = {
                "email_subject": email["subject"],
                "email_content": filtered_body,
                "email_category": category,
                "email_urgency": urgency,
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


class WorkflowWorker(QThread):
    """Picks categorized emails from the queue and generates workflows one-by-one."""
    workflow_generated = pyqtSignal(int, str)

    def __init__(self, workflow_queue, parent=None):
        super().__init__(parent)
        self.workflow_queue = workflow_queue
        self.running = True

    def run(self):
        while self.running:
            try:
                item = self.workflow_queue.get(timeout=1)
            except queue.Empty:
                continue
            if item is None:
                break

            idx, email, filtered_body, category, urgency, extra_info = item

            inputs = {
                "email_subject": email["subject"],
                "email_content": filtered_body,
                "email_category": category,
                "email_urgency": urgency,
                "email_context": extra_info,
            }

            try:
                result = WorkflowGeneratorCrew().crew().kickoff(inputs=inputs)
                workflow_text = result.raw if hasattr(result, 'raw') else str(result)
            except Exception as e:
                workflow_text = f"Error generating workflow: {str(e)}"

            self.workflow_generated.emit(idx, workflow_text)

    def stop(self):
        self.running = False
        self.workflow_queue.put(None)


class RegenerateWorker(QThread):
    """Re-runs filter, triage, or reply for a single email depending on which stage failed."""
    filter_done = pyqtSignal(int, str)
    triage_done = pyqtSignal(int, str, str, str)
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
                self.urgency = parsed.get("urgency", "")
                self.extra_info = parsed.get("extra_info", "")
            except (json.JSONDecodeError, AttributeError):
                self.category = raw[:100]
                self.urgency = ""
                self.extra_info = "Could not parse structured output"
        except Exception as e:
            self.category = "Error"
            self.urgency = ""
            self.extra_info = str(e)
        self.triage_done.emit(self.idx, self.category, self.urgency, self.extra_info)

    def _run_reply(self):
        inputs = {
            "email_subject": self.email["subject"],
            "email_content": self.filtered_body,
            "email_category": self.category,
            "email_urgency": self.urgency,
            "email_context": self.extra_info,
        }
        try:
            result = ReplyGeneratorCrew().crew().kickoff(inputs=inputs)
            draft_text = result.raw if hasattr(result, 'raw') else str(result)
        except Exception as e:
            draft_text = f"Error generating reply: {str(e)}"
        self.reply_done.emit(self.idx, draft_text)


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
        self.btn_regen = QPushButton("🔄 Regenerate")
        self.btn_update = QPushButton("💾 Update Answer")
        self.btn_proceed = QPushButton("▶️ Proceed")
        
        btn_layout.addWidget(self.btn_regen)
        btn_layout.addWidget(self.btn_update)
        btn_layout.addWidget(self.btn_proceed)
        layout.addLayout(btn_layout)
        
        self.btn_regen.clicked.connect(self.on_regenerate)
        self.btn_update.clicked.connect(self.on_update)
        self.btn_proceed.clicked.connect(self.accept)
        
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
# Main Window
# =============================================================================

class TriageWindow(QMainWindow):
    def __init__(self, raw_emails):
        super().__init__()
        self.all_unread = raw_emails
        self.emails = []
        self.state = {}
        self.current_index = 0
        self.filter_queue = queue.Queue()
        self.triage_queue = queue.Queue()
        self.reply_queue = queue.Queue()
        self.workflow_queue = queue.Queue()

        self.init_ui()
        self.start_workers()

        # Load first 3 emails (or less if fewer are available)
        initial_count = min(5, len(self.all_unread))
        for _ in range(initial_count):
            self._load_next_from_backlog()

        if self.emails:
            self.load_email(0)

    def _load_next_from_backlog(self):
        if not self.all_unread:
            return
        
        email = self.all_unread.pop(0)
        idx = len(self.emails)
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
        }
        
        self.filter_queue.put((idx, email))
        self.update_ui_state()

    def init_ui(self):
        self.setWindowTitle("Interactive Triage & Auto-Reply Workstation")
        self.resize(1200, 800)
        
        self.setStyleSheet("""
            QMainWindow { background-color: #FFFDE7; }
            QWidget { font-family: 'Segoe UI', Arial, sans-serif; color: #3E3E3E; }
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

        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # --- LEFT PANEL (Original Email) ---
        left_panel = QFrame()
        left_panel.setFrameShape(QFrame.Shape.StyledPanel)
        left_panel.setMaximumWidth(750)
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

        # Triage Info (Category, Urgency, Extra Info)
        self.lbl_category = QLabel("Category: ⏳ Waiting...")
        self.lbl_category.setStyleSheet("color: #174EA6; background-color: #E8F0FE; padding: 6px; border-radius: 4px; font-weight: bold;")
        self.lbl_category.setWordWrap(True)
        right_layout.addWidget(self.lbl_category)

        self.lbl_urgency = QLabel("Urgency: ⏳ Waiting...")
        self.lbl_urgency.setStyleSheet("color: #3C4043; background-color: #F8F9FA; padding: 6px; border-radius: 4px; font-weight: bold;")
        self.lbl_urgency.setWordWrap(True)
        right_layout.addWidget(self.lbl_urgency)

        self.lbl_extra_info = QLabel("Extra Info: ⏳ Waiting...")
        self.lbl_extra_info.setStyleSheet("color: #3C4043; background-color: #F8F9FA; padding: 6px; border-radius: 4px; font-style: italic;")
        self.lbl_extra_info.setWordWrap(True)
        right_layout.addWidget(self.lbl_extra_info)

        self.btn_workflow = QPushButton("📝 View/Edit Workflow")
        self.btn_workflow.clicked.connect(self.open_workflow_dialog)
        self.btn_workflow.setStyleSheet("margin-top: 10px; margin-bottom: 10px;")
        right_layout.addWidget(self.btn_workflow)

        # Receiver
        right_layout.addWidget(QLabel("Receiver:"))
        self.le_reply_receiver = QLineEdit()
        right_layout.addWidget(self.le_reply_receiver)

        # CC
        right_layout.addWidget(QLabel("CC:"))
        self.le_reply_cc = QLineEdit()
        right_layout.addWidget(self.le_reply_cc)

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

        self.btn_send = QPushButton("Send && Mark as Read")
        self.btn_send.setMinimumWidth(150)
        self.btn_send.setMinimumHeight(35)
        self.btn_send.setStyleSheet(
            "background-color: #0078D4; color: white; font-weight: bold; border-radius: 4px;"
        )
        self.btn_send.clicked.connect(self.send_email)
        controls_layout.addWidget(self.btn_send)

        # Stacked Skip buttons (occupy the space of one button)
        skip_container = QWidget()
        skip_layout = QVBoxLayout(skip_container)
        skip_layout.setContentsMargins(0, 0, 0, 0)
        skip_layout.setSpacing(2)

        self.btn_skip_read = QPushButton("Skip with READ")
        self.btn_skip_read.setMinimumHeight(16)
        self.btn_skip_read.setStyleSheet(
            "background-color: #6B8E23; color: white; font-weight: bold; border-radius: 3px; font-size: 10px; padding: 2px 8px;"
        )
        self.btn_skip_read.clicked.connect(self.skip_read)
        skip_layout.addWidget(self.btn_skip_read)

        self.btn_skip_unread = QPushButton("Skip with UNREAD")
        self.btn_skip_unread.setMinimumHeight(16)
        self.btn_skip_unread.setStyleSheet(
            "background-color: #8B4513; color: white; font-weight: bold; border-radius: 3px; font-size: 10px; padding: 2px 8px;"
        )
        self.btn_skip_unread.clicked.connect(self.skip_unread)
        skip_layout.addWidget(self.btn_skip_unread)

        controls_layout.addWidget(skip_container)

        self.btn_save_reply = QPushButton("💾 Save as Example")
        self.btn_save_reply.setMinimumHeight(35)
        self.btn_save_reply.setStyleSheet(
            "background-color: #107C10; color: white; font-weight: bold; border-radius: 4px;"
        )
        self.btn_save_reply.clicked.connect(self.save_reply_feedback)
        controls_layout.addWidget(self.btn_save_reply)

        right_layout.addLayout(controls_layout)

        # Add to splitter
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([600, 800])

    # -------------------------------------------------------------------------
    # Workers
    # -------------------------------------------------------------------------

    def start_workers(self):
        self.filter_worker = FilterWorker(self.filter_queue, self.triage_queue)
        self.filter_worker.filter_done.connect(self.on_filter_done)
        self.filter_worker.start()

        self.triage_worker = TriageWorker(self.triage_queue, self.reply_queue, self.workflow_queue)
        self.triage_worker.category_ready.connect(self.on_category_ready)
        self.triage_worker.start()

        self.reply_worker = ReplyWorker(self.reply_queue)
        self.reply_worker.reply_generated.connect(self.on_reply_generated)
        self.reply_worker.start()

        self.workflow_worker = WorkflowWorker(self.workflow_queue)
        self.workflow_worker.workflow_generated.connect(self.on_workflow_generated)
        self.workflow_worker.start()

    # -------------------------------------------------------------------------
    # Signal Handlers
    # -------------------------------------------------------------------------

    def on_filter_done(self, idx, cleaned_body):
        self.state[idx]["filtered_body"] = cleaned_body
        self.state[idx]["filter_status"] = "done"
        self.state[idx]["category_status"] = "thinking"

        if idx == self.current_index:
            self.update_ui_state()

    def on_category_ready(self, idx, category, urgency, extra_info):
        self.state[idx]["category"] = category
        self.state[idx]["urgency"] = urgency
        self.state[idx]["extra_info"] = extra_info
        self.state[idx]["category_status"] = "done"
        self.state[idx]["reply_status"] = "generating"

        if idx == self.current_index:
            self.update_ui_state()

    def on_workflow_generated(self, idx, text):
        self.state[idx]["workflow_text"] = text
        self.state[idx]["workflow_status"] = "done"
        if idx == self.current_index:
            self.update_ui_state()

    def on_reply_generated(self, idx, text):
        import os
        # Only inject if it's the raw text from LLM (not already HTML or error)
        if not text.startswith("Error generating"):
            body_html = "".join(f"<p>{line}</p>" if line.strip() else "<br>" for line in text.split("\n"))
            sig_path = os.path.abspath("knowledge/amy_signature.html")
            signature_html = ""
            if os.path.exists(sig_path):
                with open(sig_path, "r", encoding="utf-8") as f:
                    signature_html = f.read()
            full_html = f'<div style="font-family: Calibri, sans-serif; font-size: 11pt;">{body_html}</div><br><br>{signature_html}'
            self.state[idx]["reply_text"] = full_html
        else:
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
            cur["reply_text"] = self.txt_reply_content.toHtml()
            cur["reply_cc"] = self.le_reply_cc.text()

        self.current_index = idx
        email = self.emails[idx]

        # Left panel
        self.lbl_orig_subject.setText(f"Subject: {email['subject']}")
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
        self.le_reply_cc.setText(st.get("reply_cc") or email.get("cc", ""))

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

        # --- Triage labels ---
        if st["category_status"] == "pending":
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
                self.btn_workflow.setText("📝 View/Edit Workflow")
        else:
            self.btn_workflow.setEnabled(False)
            self.btn_workflow.setText("📝 View/Edit Workflow")

        # --- Determine error state for regenerate ---
        has_filter_error = st["filtered_body"].startswith("Error filtering:")
        has_triage_error = st["category"] == "Error"
        has_reply_error = st["reply_text"].startswith("Error generating reply:")

        # --- Reply content & controls ---
        if st["send_status"] == "skipped":
            self.txt_reply_content.setPlainText("⏭️ Skipped")
            self.txt_reply_content.setEnabled(False)
            self.le_reply_receiver.setEnabled(False)
            self.le_reply_cc.setEnabled(False)
            self.le_reply_subject.setEnabled(False)
            self.btn_send.setText("Skipped")
            self.btn_send.setStyleSheet(
                "background-color: #6B8E23; color: white; font-weight: bold; border-radius: 4px;"
            )
            self.btn_send.setEnabled(False)
            self.btn_regenerate.setEnabled(False)
            self.btn_save_reply.setEnabled(False)
            self.btn_skip_read.setEnabled(False)
            self.btn_skip_unread.setEnabled(False)
        elif st["send_status"] == "sent":
            self.txt_reply_content.setHtml(st["reply_text"])
            self.txt_reply_content.setEnabled(False)
            self.le_reply_receiver.setEnabled(False)
            self.le_reply_cc.setEnabled(False)
            self.le_reply_subject.setEnabled(False)
            self.btn_send.setText("Already Sent")
            self.btn_send.setStyleSheet(
                "background-color: #888888; color: white; font-weight: bold; border-radius: 4px;"
            )
            self.btn_send.setEnabled(False)
            self.btn_regenerate.setEnabled(False)
            self.btn_save_reply.setEnabled(False)
            self.btn_skip_read.setEnabled(False)
            self.btn_skip_unread.setEnabled(False)
        elif st["reply_status"] == "done":
            if st["reply_text"].startswith("Error generating"):
                self.txt_reply_content.setPlainText(st["reply_text"])
            else:
                self.txt_reply_content.setHtml(st["reply_text"])
            self.txt_reply_content.setEnabled(True)
            self.le_reply_receiver.setEnabled(True)
            self.le_reply_cc.setEnabled(True)
            self.le_reply_subject.setEnabled(True)
            self.btn_send.setText("Send && Mark as Read")
            self.btn_send.setStyleSheet(
                "background-color: #0078D4; color: white; font-weight: bold; border-radius: 4px;"
            )
            self.btn_send.setEnabled(True)
            self.btn_regenerate.setEnabled(True)
            self.btn_save_reply.setEnabled(True)
            self.btn_skip_read.setEnabled(True)
            self.btn_skip_unread.setEnabled(True)
        elif st["reply_status"] == "generating":
            self.txt_reply_content.setPlainText("⏳ Generating reply...\nPlease wait.")
            self.txt_reply_content.setEnabled(False)
            self.btn_send.setEnabled(False)
            self.btn_regenerate.setEnabled(False)
            self.btn_save_reply.setEnabled(False)
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
            self.btn_regenerate.setEnabled(has_filter_error or has_triage_error)
            self.btn_skip_read.setEnabled(True)
            self.btn_skip_unread.setEnabled(True)

        self.btn_prev.setEnabled(self.current_index > 0)
        self.btn_next.setEnabled(self.current_index < len(self.emails) - 1)

    # -------------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------------

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
        with open("knowledge/workflow_examples.jsonl", "a", encoding="utf-8") as f:
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
        with open("knowledge/reply_examples.jsonl", "a", encoding="utf-8") as f:
            data = {
                "email_subject": email["subject"],
                "category": st["category"],
                "urgency": st["urgency"],
                "extra_info": st["extra_info"],
                "expected_reply": reply_text
            }
            f.write(json.dumps(data) + "\n")
        
        QMessageBox.information(self, "Success", "Reply saved as training example!")

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
                category=st["category"], urgency=st["urgency"], extra_info=st["extra_info"]
            )
            self._regen_worker.reply_done.connect(self.on_reply_generated)
            self._regen_worker.start()

    def send_email(self):
        recipient = self.le_reply_receiver.text()
        cc_list = self.le_reply_cc.text()
        subject = self.le_reply_subject.text()
        body = self.txt_reply_content.toHtml()

        tool = OutlookSendTool()
        result = tool._run(recipient=recipient, subject=subject, body=body, cc=cc_list, is_html=True)

        if "successfully sent" in result.lower():
            # Mark the original email as read in Outlook
            entry_id = self.emails[self.current_index].get("entry_id", "")
            if entry_id:
                mark_email_as_read(entry_id)

            QMessageBox.information(self, "Success", "Email sent and marked as read!")
            self.state[self.current_index]["send_status"] = "sent"
            self.state[self.current_index]["reply_text"] = body
            
            # Lazily load a new email from the backlog
            if self.all_unread:
                self._load_next_from_backlog()
            
            self.update_ui_state()

            # Auto jump to next unsent
            for i in range(self.current_index + 1, len(self.emails)):
                if self.state[i]["send_status"] != "sent":
                    self.load_email(i)
                    break
        else:
            QMessageBox.warning(self, "Error", f"Failed to send email:\n{result}")

    def _skip_current(self, mark_read: bool):
        """Skip the current email, optionally marking it as read in Outlook."""
        idx = self.current_index
        entry_id = self.emails[idx].get("entry_id", "")

        if mark_read and entry_id:
            mark_email_as_read(entry_id)

        self.state[idx]["send_status"] = "skipped"

        # Lazily load a new email from the backlog
        if self.all_unread:
            self._load_next_from_backlog()

        self.update_ui_state()

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
