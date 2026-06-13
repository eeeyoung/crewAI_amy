"""lilAmy desktop GUI — AMail module with card list + detail panel.

Left panel: scrollable summary cards (Chinese summary, category, urgency,
assignee, todos at a glance).
Right panel: full email body + metadata + AI draft reply.

Uses MailService (service-first pattern). GUI is a thin signal consumer.
"""

import json
import threading

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QScrollArea, QFrame, QProgressBar,
    QTextEdit, QSplitter, QLineEdit,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut

from shared_tools.mail_service import MailService

# ── Styles ────────────────────────────────────────────────────────────

STYLE = """
    QMainWindow { background-color: #1E1E2E; }
    QLabel { color: #CDD6F4; }
    QPushButton { background-color: #45475A; color: #CDD6F4; border: 1px solid #585B70;
                  border-radius: 6px; padding: 8px 16px; font-weight: bold; }
    QPushButton:hover { background-color: #585B70; }
    QPushButton:disabled { background-color: #313244; color: #6C7086; }
    QScrollArea { border: none; background-color: #181825; }
    QProgressBar { border: 1px solid #45475A; border-radius: 4px; text-align: center;
                   background-color: #313244; }
    QProgressBar::chunk { background-color: #89B4FA; border-radius: 3px; }
    QTextEdit { background-color: #313244; color: #CDD6F4; border: 1px solid #45475A;
                border-radius: 6px; padding: 8px; font-size: 11pt; }
    QLineEdit { background-color: #313244; color: #CDD6F4; border: 1px solid #45475A;
                border-radius: 6px; padding: 8px 12px; font-size: 11pt; }
    QSplitter::handle { background-color: #45475A; width: 2px; }
"""

CARD_BASE = """
    QFrame#emailCard { background-color: #313244; border: 1px solid #45475A;
                       border-radius: 8px; }
    QFrame#emailCard:hover { background-color: #3A3B50; border-color: #89B4FA; }
"""

CARD_SELECTED = """
    QFrame#emailCard { background-color: #3A3B50; border: 2px solid #89B4FA;
                       border-radius: 8px; }
"""


# ── Email Card ────────────────────────────────────────────────────────

class EmailCard(QFrame):
    """Single conclusion block in the card list."""

    def __init__(self, email_data: dict, parent=None):
        super().__init__(parent)
        self.email_data = email_data
        self.entry_id = email_data.get("entry_id", "")
        self._selected = False

        self.setObjectName("emailCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(CARD_BASE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)

        # Chinese summary (primary)
        cn = email_data.get("chinese_summary", "")
        if not cn:
            cn = email_data.get("subject", "(No Subject)")[:60]
        lbl = QLabel(f"<b>{cn}</b>")
        lbl.setStyleSheet("color: #F9E2AF; font-size: 12pt;")
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        # Subject
        subj = email_data.get("subject", "(No Subject)")[:60]
        lbl2 = QLabel(f"📧 {subj}")
        lbl2.setStyleSheet("color: #89B4FA; font-size: 10pt;")
        lbl2.setWordWrap(True)
        layout.addWidget(lbl2)

        # Meta: category + urgency + assignee
        meta = QHBoxLayout()
        meta.setSpacing(8)

        cat = email_data.get("category", "General")
        c = QLabel(f"📂 {cat}")
        c.setStyleSheet("color: #A6ADC8; font-size: 9pt;")
        meta.addWidget(c)

        urg = email_data.get("urgency", "low")
        urg_color = {"critical": "#F38BA8", "high": "#FAB387", "medium": "#F9E2AF", "low": "#A6E3A1"}
        u = QLabel(f"⚠️ {urg}")
        u.setStyleSheet(f"color: {urg_color.get(urg, '#A6ADC8')}; font-size: 9pt; font-weight: bold;")
        meta.addWidget(u)

        a = email_data.get("assignee", "")
        if a:
            al = QLabel(f"👔 {a}")
            al.setStyleSheet("color: #A6E3A1; font-size: 9pt;")
            meta.addWidget(al)
        meta.addStretch()
        layout.addLayout(meta)

        # Todos
        todos = email_data.get("todos_json", "[]")
        if isinstance(todos, str):
            try:
                todos = json.loads(todos)
            except Exception:
                todos = []
        if todos:
            t = QLabel(f"✅ {' · '.join(todos[:2])}")
            t.setStyleSheet("color: #CBA6F7; font-size: 9pt;")
            t.setWordWrap(True)
            layout.addWidget(t)

        # Time
        received = email_data.get("received_time", "")[:16]
        if received:
            r = QLabel(f"🕐 {received}")
            r.setStyleSheet("color: #6C7086; font-size: 8pt;")
            layout.addWidget(r)

    def set_selected(self, sel: bool):
        self._selected = sel
        self.setStyleSheet(CARD_SELECTED if sel else CARD_BASE)

    def mouseDoubleClickEvent(self, event):
        if self.entry_id:
            try:
                import win32com.client
                outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
                outlook.GetItemFromID(self.entry_id).Display()
            except Exception:
                pass
        event.accept()


# ── Detail Panel ───────────────────────────────────────────────────────

class DetailPanel(QWidget):
    """Right panel: full email body + metadata + AI reply section."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._email: dict = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.lbl_subject = QLabel("Select an email to view details")
        self.lbl_subject.setStyleSheet("color: #89B4FA; font-size: 14pt; font-weight: bold;")
        self.lbl_subject.setWordWrap(True)
        layout.addWidget(self.lbl_subject)

        self.lbl_sender = QLabel("")
        self.lbl_sender.setStyleSheet("color: #A6ADC8; font-size: 11pt;")
        layout.addWidget(self.lbl_sender)

        self.lbl_meta = QLabel("")
        self.lbl_meta.setStyleSheet("color: #CDD6F4; font-size: 10pt;")
        layout.addWidget(self.lbl_meta)

        self.lbl_assignee = QLabel("")
        self.lbl_assignee.setStyleSheet("color: #A6E3A1; font-size: 11pt; font-weight: bold;")
        layout.addWidget(self.lbl_assignee)

        self.lbl_todos = QLabel("")
        self.lbl_todos.setStyleSheet("color: #CBA6F7; font-size: 10pt;")
        self.lbl_todos.setWordWrap(True)
        layout.addWidget(self.lbl_todos)

        sec = QLabel("EMAIL BODY")
        sec.setStyleSheet("color: #6C7086; font-size: 9pt;")
        layout.addWidget(sec)

        self.txt_body = QTextEdit()
        self.txt_body.setReadOnly(True)
        self.txt_body.setMinimumHeight(150)
        layout.addWidget(self.txt_body, stretch=1)

        sec2 = QLabel("DRAFT REPLY")
        sec2.setStyleSheet("color: #6C7086; font-size: 9pt;")
        layout.addWidget(sec2)

        self.txt_reply = QTextEdit()
        self.txt_reply.setMinimumHeight(100)
        self.txt_reply.setPlaceholderText("Click 'Draft Reply' to generate...")
        layout.addWidget(self.txt_reply)

        # Buttons
        btn = QHBoxLayout()
        btn.setSpacing(8)

        self.btn_draft = QPushButton("✏️ Draft Reply")
        self.btn_draft.clicked.connect(self._draft_reply)
        btn.addWidget(self.btn_draft)

        self.btn_refine = QPushButton("✨ Refine")
        self.btn_refine.setEnabled(False)
        self.btn_refine.clicked.connect(self._refine_reply)
        btn.addWidget(self.btn_refine)

        self.btn_copy = QPushButton("📋 Copy")
        self.btn_copy.setEnabled(False)
        self.btn_copy.clicked.connect(self._copy_reply)
        btn.addWidget(self.btn_copy)

        btn.addStretch()
        self.btn_remove = QPushButton("🗑 Remove")
        self.btn_remove.clicked.connect(self._remove)
        btn.addWidget(self.btn_remove)
        layout.addLayout(btn)

        self.edit_instr = QLineEdit()
        self.edit_instr.setPlaceholderText("Edit: make it shorter, more formal, add deadline...")
        self.edit_instr.setVisible(False)
        layout.addWidget(self.edit_instr)

    # ── Public ──────────────────────────────────────────────────────

    def show_email(self, data: dict):
        self._email = data
        self.lbl_subject.setText(data.get("subject", "(No Subject)"))
        self.lbl_sender.setText(f"From: {data.get('sender', 'Unknown')}")
        self.lbl_meta.setText(
            f"📂 {data.get('category', 'General')}  |  "
            f"⚠️ {data.get('urgency', 'low')}  |  "
            f"{data.get('received_time', '')[:16]}"
        )
        a = data.get("assignee", "")
        self.lbl_assignee.setText(f"👔 {a}" if a else "")

        todos = data.get("todos_json", "[]")
        if isinstance(todos, str):
            try:
                todos = json.loads(todos)
            except Exception:
                todos = []
        self.lbl_todos.setText("✅ " + " · ".join(todos) if todos else "")

        body = data.get("body") or data.get("email_body", "(body not available)")
        self.txt_body.setPlainText(body)
        self.txt_reply.clear()
        draft = data.get("reply_draft", "")
        if draft:
            self.txt_reply.setPlainText(draft)
            self.btn_copy.setEnabled(True)
        self.btn_refine.setEnabled(False)
        self.edit_instr.setVisible(False)

    def clear(self):
        self._email = {}
        self.lbl_subject.setText("Select an email to view details")
        self.lbl_sender.setText("")
        self.lbl_meta.setText("")
        self.lbl_assignee.setText("")
        self.lbl_todos.setText("")
        self.txt_body.clear()
        self.txt_reply.clear()
        self.btn_draft.setEnabled(True)
        self.btn_refine.setEnabled(False)
        self.btn_copy.setEnabled(False)
        self.edit_instr.setVisible(False)

    # ── Reply actions ───────────────────────────────────────────────

    def _draft_reply(self):
        if not self._email:
            return
        self.btn_draft.setEnabled(False)
        self.btn_draft.setText("⏳ Drafting...")
        threading.Thread(target=self._do_draft, daemon=True).start()

    def _do_draft(self):
        from amail.crew import ReplyGeneratorCrew
        em = self._email
        inputs = {
            "email_subject": em.get("subject", ""),
            "email_sender": em.get("sender", ""),
            "email_content": em.get("body") or em.get("email_body", ""),
        }
        try:
            result = ReplyGeneratorCrew().crew().kickoff(inputs=inputs)
            draft = (result.raw if hasattr(result, "raw") else str(result)).strip()
            self.txt_reply.setPlainText(draft)
            self.btn_copy.setEnabled(True)
            self.btn_refine.setEnabled(True)
            self.edit_instr.setVisible(True)
        except Exception as e:
            self.txt_reply.setPlainText(f"Error: {e}")
        finally:
            self.btn_draft.setEnabled(True)
            self.btn_draft.setText("✏️ Draft Reply")

    def _refine_reply(self):
        instr = self.edit_instr.text().strip()
        if not instr or not self.txt_reply.toPlainText().strip():
            return
        self.btn_refine.setEnabled(False)
        self.btn_refine.setText("⏳ Refining...")
        threading.Thread(target=self._do_refine, args=(instr,), daemon=True).start()

    def _do_refine(self, instr: str):
        from shared_tools.llm_config import get_llm
        em = self._email
        prompt = (
            f"ORIGINAL EMAIL SUBJECT: {em.get('subject', '')}\n"
            f"ORIGINAL EMAIL SENDER: {em.get('sender', '')}\n\n"
            f"CURRENT DRAFT REPLY:\n{self.txt_reply.toPlainText()}\n\n"
            f"EDIT INSTRUCTIONS: {instr}\n\n"
            f"Revise the draft reply based on the edit instructions. "
            f"Keep it professional and direct. Output ONLY the revised text."
        )
        try:
            resp = get_llm("fast").call(prompt)
            self.txt_reply.setPlainText(resp.strip())
        except Exception as e:
            self.txt_reply.setPlainText(f"Refine error: {e}")
        finally:
            self.btn_refine.setEnabled(True)
            self.btn_refine.setText("✨ Refine")
            self.edit_instr.clear()

    def _copy_reply(self):
        text = self.txt_reply.toPlainText().strip()
        if text:
            QApplication.clipboard().setText(text)
            self.btn_copy.setText("✅ Copied!")
            QTimer.singleShot(2000, lambda: self.btn_copy.setText("📋 Copy"))

    def _remove(self):
        if self._email:
            from shared_tools.ipc_bridge import remove_processed_email
            remove_processed_email(self._email.get("entry_id", ""))
            self.clear()


# ── Main Window ───────────────────────────────────────────────────────

class LilAmyWindow(QMainWindow):
    """lilAmy platform — AMail module: card list + detail panel."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("lilAmy — AMail")
        self.setMinimumSize(1100, 750)
        self.setStyleSheet(STYLE)

        c = QWidget()
        self.setCentralWidget(c)
        root = QVBoxLayout(c)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # Top bar
        top = QHBoxLayout()
        top.setSpacing(8)
        t = QLabel("<b>lilAmy</b> — AMail")
        t.setStyleSheet("color: #CDD6F4; font-size: 14pt;")
        top.addWidget(t)
        top.addStretch()
        self.lbl_count = QLabel("📧 0 emails")
        self.lbl_count.setStyleSheet("color: #A6ADC8; font-size: 10pt;")
        top.addWidget(self.lbl_count)
        self.btn_refresh = QPushButton("🔄 Refresh")
        self.btn_refresh.clicked.connect(self._manual_refresh)
        top.addWidget(self.btn_refresh)
        root.addLayout(top)

        self.progress = QProgressBar()
        self.progress.setFixedHeight(4)
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        # Splitter: cards | detail
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 4, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.cards_widget = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_widget)
        self.cards_layout.setContentsMargins(4, 4, 4, 4)
        self.cards_layout.setSpacing(6)
        self.cards_layout.addStretch()
        self.scroll.setWidget(self.cards_widget)
        ll.addWidget(self.scroll)
        splitter.addWidget(left)

        self.detail = DetailPanel()
        splitter.addWidget(self.detail)
        splitter.setSizes([420, 680])
        root.addWidget(splitter, stretch=1)

        self.lbl_status = QLabel("Starting...")
        self.lbl_status.setStyleSheet("color: #6C7086; font-size: 9pt;")
        root.addWidget(self.lbl_status)

        # Service
        self.service = MailService(auto_refresh=True)
        self.service.emails_loaded.connect(self._on_loaded)
        self.service.summary_ready.connect(self._on_summary)
        self.service.new_emails_arrived.connect(self._on_new)
        self.service.refresh_progress.connect(self._on_progress)
        self.service.refresh_error.connect(self._on_error)

        self.cards: list[EmailCard] = []
        self._selected: EmailCard | None = None

        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(self._manual_refresh)

        # Go
        self.service.start()
        self.service.load_emails_from_db()
        QTimer.singleShot(500, lambda: self.service.refresh_inbox(count=30))

    # ── Signal handlers ─────────────────────────────────────────────

    def _on_loaded(self, emails: list):
        for em in emails:
            self._add_card(em, prepend=False)
        self._update_count()
        self.lbl_status.setText(f"Loaded {len(emails)} emails from local store")

    def _on_summary(self, entry: dict):
        self._add_card(entry, prepend=True)
        self._update_count()

    def _on_new(self, count: int):
        self.progress.setVisible(False)
        self.btn_refresh.setEnabled(True)
        self.lbl_status.setText(f"Refresh done — {count} new" if count else "No new emails")

    def _on_progress(self, cur: int, total: int, msg: str):
        self.progress.setVisible(True)
        self.progress.setMaximum(total)
        self.progress.setValue(cur)
        self.lbl_status.setText(msg)

    def _on_error(self, msg: str):
        self.lbl_status.setText(f"Error: {msg}")
        self.btn_refresh.setEnabled(True)

    # ── Cards ───────────────────────────────────────────────────────

    def _add_card(self, data: dict, prepend: bool = True):
        card = EmailCard(data)
        card.mousePressEvent = lambda e, c=card: self._select_card(c, e)  # type: ignore[assignment]
        idx = 0 if prepend else self.cards_layout.count() - 1
        self.cards_layout.insertWidget(idx, card)
        if prepend:
            self.cards.insert(0, card)
        else:
            self.cards.append(card)

    def _select_card(self, card: EmailCard, event):
        if self._selected:
            self._selected.set_selected(False)
        self._selected = card
        card.set_selected(True)
        data = card.email_data
        if not data.get("body") and not data.get("email_body"):
            self._fetch_body(card)
        else:
            self.detail.show_email(data)

    def _fetch_body(self, card: EmailCard):
        try:
            import win32com.client
            outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
            msg = outlook.GetItemFromID(card.entry_id)
            body = getattr(msg, "Body", "")[:5000]
            card.email_data["body"] = body
            card.email_data["email_body"] = body
        except Exception:
            card.email_data["body"] = "(body not available)"
        self.detail.show_email(card.email_data)

    def _update_count(self):
        self.lbl_count.setText(f"📧 {len(self.cards)} emails")

    def _manual_refresh(self):
        self.btn_refresh.setEnabled(False)
        self.service.refresh_inbox(count=30)
