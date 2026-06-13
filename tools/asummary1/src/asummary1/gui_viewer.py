"""GUI viewer for ASummary1 — two modes:
  1. 'Saved' — load all previously-saved summaries (up to 100) from DB
  2. 'New' — fetch Outlook inbox, summarize live, save to DB
"""

import json
import sqlite3
import sys
import threading

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QScrollArea, QFrame, QProgressBar, QMessageBox,
    QComboBox, QMenu, QDialog, QTextEdit, QDialogButtonBox, QLineEdit,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QAction, QKeySequence, QShortcut

from shared_tools.outlook_tool import fetch_inbox_emails, mark_email_as_read
from asummary1.crew import SummarizerCrew
from asummary1.reply_crew import ReplyCrew

from shared_tools.ipc_bridge import DB_PATH

# ── Styles ────────────────────────────────────────────────────────────

WINDOW_STYLE = """
    QMainWindow { background-color: #1E1E2E; }
    QLabel { color: #CDD6F4; }
    QPushButton { background-color: #45475A; color: #CDD6F4; border: 1px solid #585B70;
                  border-radius: 6px; padding: 8px 16px; font-weight: bold; }
    QPushButton:hover { background-color: #585B70; }
    QPushButton:disabled { background-color: #313244; color: #6C7086; }
    QPushButton[active="true"] { background-color: #89B4FA; color: #1E1E2E; }
    QScrollArea { border: none; background-color: #181825; }
    QProgressBar { border: 1px solid #45475A; border-radius: 4px; text-align: center;
                   background-color: #313244; }
    QProgressBar::chunk { background-color: #89B4FA; border-radius: 3px; }
    QComboBox { background-color: #313244; color: #CDD6F4; border: 1px solid #45475A;
                border-radius: 4px; padding: 4px 8px; }
"""

_llm_semaphore = threading.Semaphore(1)


# ── DB helpers ────────────────────────────────────────────────────────

def _get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _load_saved_summaries(limit: int = 100, status: str = 'active') -> list[dict]:
    if not DB_PATH.exists():
        return []
    conn = _get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS email_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email_entry_id TEXT UNIQUE NOT NULL,
                email_subject TEXT, email_sender TEXT, category TEXT,
                chinese_summary TEXT, assignee TEXT, todos_json TEXT,
                received_time TEXT, status TEXT DEFAULT 'active',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
        rows = conn.execute(
            """SELECT es.*, ce.email_body, ce.email_subject AS ce_subject,
                      ce.email_sender AS ce_sender
               FROM email_summaries es
               LEFT JOIN categorized_emails ce ON es.email_entry_id = ce.email_entry_id
               WHERE es.status = ?
               ORDER BY es.created_at DESC LIMIT ?""",
            (status, limit)
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _set_card_status(entry_id: str, status: str):
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE email_summaries SET status = ? WHERE email_entry_id = ?",
            (status, entry_id)
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


# ── Workers ───────────────────────────────────────────────────────────

class SummaryWorker(QThread):
    email_fetched = pyqtSignal(int)
    progress_update = pyqtSignal(int, int, str)
    summary_ready = pyqtSignal(int, dict, dict)
    all_done = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, count=20, unread_only=False, parent=None):
        super().__init__(parent)
        self.count = count
        self.unread_only = unread_only
        self.running = True

    def run(self):
        self.progress_update.emit(0, self.count, "Fetching from Outlook...")
        try:
            emails = fetch_inbox_emails(count=self.count, max_body=3000,
                                        unread_only=self.unread_only)
        except Exception as e:
            self.error_occurred.emit(f"Cannot read Outlook: {e}")
            return

        if not emails:
            self.error_occurred.emit("No emails found.")
            return
        if not self.running:
            return

        self.email_fetched.emit(len(emails))
        total = len(emails)

        for i, email in enumerate(emails):
            if not self.running:
                return
            subject = email.get("subject", "(No Subject)")[:60]
            self.progress_update.emit(i + 1, total,
                                      f"Summarizing [{i+1}/{total}]: {subject}...")

            summary = self._summarize(email)
            if not self.running:
                return
            self.summary_ready.emit(i, email, summary)
            self._save(email, summary)

        self.all_done.emit()

    def _summarize(self, email: dict) -> dict:
        inputs = {
            "email_subject": email.get("subject", ""),
            "email_sender": email.get("sender", ""),
            "email_content": email.get("body", ""),
            "email_category": "General",
        }
        try:
            with _llm_semaphore:
                result = SummarizerCrew().crew().kickoff(inputs=inputs)
            raw = result.raw if hasattr(result, 'raw') else str(result)
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0]
            if cleaned.startswith("{{") and cleaned.endswith("}}"):
                cleaned = cleaned[1:-1]
            parsed = json.loads(cleaned.strip())
            return {
                "chinese_summary": parsed.get("chinese_summary", ""),
                "assignee": parsed.get("assignee", ""),
                "todos": parsed.get("todos", []),
            }
        except json.JSONDecodeError:
            return {"chinese_summary": raw[:200], "assignee": "?", "todos": []}
        except Exception as e:
            return {"chinese_summary": f"Error: {e}", "assignee": "?", "todos": []}

    def _save(self, email, summary):
        conn = _get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS email_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email_entry_id TEXT UNIQUE NOT NULL,
                    email_subject TEXT, email_sender TEXT, category TEXT,
                    chinese_summary TEXT, assignee TEXT, todos_json TEXT,
                    received_time TEXT, status TEXT DEFAULT 'active',
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.execute(
                "INSERT OR REPLACE INTO email_summaries "
                "(email_entry_id, email_subject, email_sender, category, "
                "chinese_summary, assignee, todos_json, received_time, status)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (email.get("entry_id", ""), email.get("subject", ""),
                 email.get("sender", ""), "General",
                 summary["chinese_summary"], summary["assignee"],
                 json.dumps(summary["todos"], ensure_ascii=False),
                 email.get("received_time", ""), "active"),
            )
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()

    def stop(self):
        self.running = False


class LoadSavedWorker(QThread):
    loaded = pyqtSignal(list)
    error_occurred = pyqtSignal(str)

    def __init__(self, limit=100, status='active', parent=None):
        super().__init__(parent)
        self.limit = limit
        self.status = status

    def run(self):
        try:
            rows = _load_saved_summaries(self.limit, self.status)
            self.loaded.emit(rows)
        except Exception as e:
            self.error_occurred.emit(str(e))


# ── Outlook helper ───────────────────────────────────────────────────

def open_outlook_email(entry_id: str):
    """Open the original Outlook email by its EntryID and bring to front."""
    if not entry_id:
        return
    try:
        import win32com.client
        import win32gui
        outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        msg = outlook.GetItemFromID(entry_id)
        msg.Display()
        # Bring the opened inspector window to foreground
        try:
            hwnd = msg.GetInspector.Caption  # force inspector to exist
            # Find Outlook's top window and bring it to front
            def bring_outlook_foreground():
                try:
                    # Try to find the inspector window
                    hwnd = win32gui.FindWindow(None, msg.GetInspector.Caption)
                    if hwnd:
                        win32gui.SetForegroundWindow(hwnd)
                        win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
                except Exception:
                    pass
            import threading
            threading.Timer(0.3, bring_outlook_foreground).start()
        except Exception:
            pass
    except Exception as e:
        print(f"Error opening Outlook email: {e}")


def flag_and_mark_read(entry_id: str) -> bool:
    """Flag an email as task in Outlook AND mark it as read."""
    if not entry_id:
        return False
    ok = True
    try:
        import win32com.client
        outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        msg = outlook.GetItemFromID(entry_id)
        msg.MarkAsTask(0)  # 0 = olMarkToday
        msg.TaskSubject = msg.Subject
        msg.UnRead = False
        msg.Save()
    except Exception as e:
        print(f"Error flagging Outlook email: {e}")
        ok = False
    return ok


# ── Reply Worker & Dialog ──────────────────────────────────────────────

class ReplyWorker(QThread):
    reply_ready = pyqtSignal(str)
    reply_error = pyqtSignal(str)

    def __init__(self, email_data: dict, parent=None):
        super().__init__(parent)
        self.email_data = email_data

    def run(self):
        inputs = {
            "email_subject": self.email_data.get("subject") or self.email_data.get("email_subject", ""),
            "email_sender": self.email_data.get("sender") or self.email_data.get("email_sender", ""),
            "email_content": self.email_data.get("body") or self.email_data.get("email_body", ""),
        }
        try:
            with _llm_semaphore:
                result = ReplyCrew().crew().kickoff(inputs=inputs)
            text = result.raw if hasattr(result, 'raw') else str(result)
            self.reply_ready.emit(text.strip())
        except Exception as e:
            self.reply_error.emit(str(e))


class RefineWorker(QThread):
    refined = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, email_data: dict, current_draft: str, instructions: str, parent=None):
        super().__init__(parent)
        self.email_data = email_data
        self.current_draft = current_draft
        self.instructions = instructions

    def run(self):
        from shared_tools.llm_config import get_llm
        prompt = (
            f"ORIGINAL EMAIL SUBJECT: {self.email_data.get('subject', '')}\n"
            f"ORIGINAL EMAIL SENDER: {self.email_data.get('sender', '')}\n\n"
            f"CURRENT DRAFT REPLY:\n{self.current_draft}\n\n"
            f"EDIT INSTRUCTIONS: {self.instructions}\n\n"
            f"Revise the draft reply based on the edit instructions above. "
            f"Keep it as Amy Chen (30yr Australian PM+CA, direct, professional). "
            f"Output ONLY the revised reply text, no commentary."
        )
        try:
            with _llm_semaphore:
                llm = get_llm("fast")
                response = llm.call(prompt)
            self.refined.emit(response.strip())
        except Exception as e:
            self.error.emit(str(e))


class ReplyDialog(QDialog):
    def __init__(self, email_data: dict, parent=None):
        super().__init__(parent)
        self._subject = email_data.get("subject") or email_data.get("email_subject", "(No Subject)")
        self._sender = email_data.get("sender") or email_data.get("email_sender", "")
        self._body = email_data.get("body") or email_data.get("email_body", "") or email_data.get("email_content", "")

        self.setWindowTitle("📝 自动回复 — Amy Chen")
        self.setMinimumSize(700, 600)
        self.setStyleSheet(
            "QDialog { background-color: #1E1E2E; }"
            "QLabel { color: #CDD6F4; }"
            "QTextEdit { background-color: #313244; color: #CDD6F4; border: 1px solid #45475A;"
            "border-radius: 6px; padding: 8px; font-size: 12pt; }"
            "QLineEdit { background-color: #313244; color: #CDD6F4; border: 1px solid #45475A;"
            "border-radius: 6px; padding: 8px 12px; font-size: 11pt; }"
            "QPushButton { background-color: #45475A; color: #CDD6F4; border: 1px solid #585B70;"
            "border-radius: 6px; padding: 8px 16px; font-weight: bold; }"
            "QPushButton:hover { background-color: #585B70; }"
            "QPushButton:disabled { background-color: #313244; color: #6C7086; }"
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        lbl_subj = QLabel(f"<b>Reply to:</b> {self._subject}")
        lbl_subj.setWordWrap(True)
        layout.addWidget(lbl_subj)

        if self._sender:
            lbl_sender = QLabel(f"<b>From:</b> {self._sender}")
            lbl_sender.setStyleSheet("color: #A6ADC8;")
            layout.addWidget(lbl_sender)

        self.lbl_loading = QLabel("⏳ Amy is drafting a reply...")
        self.lbl_loading.setStyleSheet("color: #F9E2AF; font-size: 12pt;")
        layout.addWidget(self.lbl_loading)

        self.txt_reply = QTextEdit()
        self.txt_reply.setMinimumHeight(200)
        self.txt_reply.setVisible(False)
        layout.addWidget(self.txt_reply, stretch=1)

        # ── Refinement section (hidden until reply is ready) ──
        self.refine_widget = QWidget()
        self.refine_widget.setVisible(False)
        refine_layout = QHBoxLayout(self.refine_widget)
        refine_layout.setContentsMargins(0, 0, 0, 0)
        refine_layout.setSpacing(8)

        self.edit_instructions = QLineEdit()
        self.edit_instructions.setPlaceholderText("e.g. make it shorter, add a note about the deadline, be more formal...")
        refine_layout.addWidget(self.edit_instructions, stretch=1)

        self.btn_refine = QPushButton("✨ Refine")
        self.btn_refine.clicked.connect(self._do_refine)
        refine_layout.addWidget(self.btn_refine)

        layout.addWidget(self.refine_widget)

        # ── Button bar ──
        btn_layout = QHBoxLayout()

        self.btn_copy = QPushButton("📋 复制到剪贴板")
        self.btn_copy.setEnabled(False)
        self.btn_copy.clicked.connect(self._copy_to_clipboard)
        btn_layout.addWidget(self.btn_copy)

        self.btn_regenerate = QPushButton("🔄 重新生成")
        self.btn_regenerate.setEnabled(False)
        self.btn_regenerate.clicked.connect(self._regenerate)
        btn_layout.addWidget(self.btn_regenerate)

        btn_layout.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        self._start_worker()

    def _worker_data(self) -> dict:
        return {"subject": self._subject, "sender": self._sender, "email_body": self._body}

    def _start_worker(self):
        self.worker = ReplyWorker(self._worker_data())
        self.worker.reply_ready.connect(self._on_reply)
        self.worker.reply_error.connect(self._on_error)
        self.worker.start()

    def _on_reply(self, text):
        self.lbl_loading.setVisible(False)
        self.txt_reply.setVisible(True)
        self.txt_reply.setPlainText(text)
        self.refine_widget.setVisible(True)
        self.btn_copy.setEnabled(True)
        self.btn_regenerate.setEnabled(True)

    def _do_refine(self):
        instructions = self.edit_instructions.text().strip()
        if not instructions:
            return
        current = self.txt_reply.toPlainText()
        if not current.strip():
            return

        self.edit_instructions.setEnabled(False)
        self.btn_refine.setEnabled(False)
        self.btn_refine.setText("⏳ Refining...")
        self.lbl_loading.setText("⏳ Refining based on your instructions...")
        self.lbl_loading.setVisible(True)

        self.refine_worker = RefineWorker(
            {"subject": self._subject, "sender": self._sender},
            current, instructions
        )
        self.refine_worker.refined.connect(self._on_refined)
        self.refine_worker.error.connect(self._on_refine_error)
        self.refine_worker.start()

    def _on_refined(self, text):
        self.lbl_loading.setVisible(False)
        self.txt_reply.setPlainText(text)
        self.edit_instructions.setEnabled(True)
        self.edit_instructions.clear()
        self.btn_refine.setEnabled(True)
        self.btn_refine.setText("✨ Refine")

    def _on_refine_error(self, msg):
        self.lbl_loading.setText(f"❌ Refine error: {msg}")
        self.lbl_loading.setStyleSheet("color: #F38BA8; font-size: 12pt;")
        self.edit_instructions.setEnabled(True)
        self.btn_refine.setEnabled(True)
        self.btn_refine.setText("✨ Refine")

    def _on_error(self, msg):
        self.lbl_loading.setText(f"❌ Error: {msg}")
        self.lbl_loading.setStyleSheet("color: #F38BA8; font-size: 12pt;")

    def _copy_to_clipboard(self):
        text = self.txt_reply.toPlainText()
        QApplication.clipboard().setText(text)
        self.btn_copy.setText("✅ 已复制!")
        QTimer.singleShot(2000, lambda: self.btn_copy.setText("📋 复制到剪贴板"))

    def _regenerate(self):
        self.txt_reply.setVisible(False)
        self.refine_widget.setVisible(False)
        self.edit_instructions.clear()
        self.btn_copy.setEnabled(False)
        self.btn_regenerate.setEnabled(False)
        self.lbl_loading.setText("⏳ Regenerating...")
        self.lbl_loading.setVisible(True)
        self._start_worker()


# ── Clickable Card class ─────────────────────────────────────────────

class EmailCard(QFrame):
    """A card with multi-select support. Ctrl+Click to toggle selection."""
    removed = pyqtSignal(object)
    selected = pyqtSignal(object, bool)  # emits (self, is_ctrl_held)

    def __init__(self, entry_id: str, email_data: dict = None, parent=None):
        super().__init__(parent)
        self.entry_id = entry_id
        self.email_data = email_data or {}
        self._selected = False

        self.setObjectName("emailCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click: select | Ctrl+Click: multi | Ctrl+R: reply | Ctrl+F: flag | Backspace: remove | Dbl-click: Outlook")
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._update_style()
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def _update_style(self):
        if self._selected:
            self.setStyleSheet(
                "QFrame#emailCard { background-color: #3A3B50; border: 2px solid #89B4FA;"
                "border-radius: 8px; }"
            )
        else:
            self.setStyleSheet(
                "QFrame#emailCard { background-color: #313244; border: 1px solid #45475A;"
                "border-radius: 8px; }"
                "QFrame#emailCard:hover { background-color: #3A3B50; border-color: #89B4FA; }"
            )

    def set_selected(self, sel: bool):
        self._selected = sel
        self._update_style()

    def is_selected(self) -> bool:
        return self._selected

    def _show_context_menu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background-color: #313244; color: #CDD6F4; border: 1px solid #45475A;"
            "padding: 4px; }"
            "QMenu::item { padding: 6px 24px; }"
            "QMenu::item:selected { background-color: #45475A; }"
        )

        open_action = menu.addAction("📧 在 Outlook 中打开")
        reply_action = menu.addAction("📝 生成自动回复\tCtrl+R")
        flag_action = menu.addAction("🚩 移到 TO DO\tCtrl+F")
        menu.addSeparator()
        remove_action = menu.addAction("🗑 移除并标为已读\tBackspace")

        action = menu.exec(self.mapToGlobal(pos))
        if action == open_action:
            if self.entry_id:
                open_outlook_email(self.entry_id)
        elif action == reply_action:
            self._open_reply_dialog()
        elif action == flag_action:
            if self._flag_email():
                _set_card_status(self.entry_id, 'todo')
                self.removed.emit(self)
        elif action == remove_action:
            if self.entry_id:
                mark_email_as_read(self.entry_id)
            self.removed.emit(self)
            self.deleteLater()

    def open_reply(self):
        self._open_reply_dialog()

    def flag_email(self) -> bool:
        return self._flag_email()

    def _flag_email(self) -> bool:
        if self.entry_id:
            return flag_and_mark_read(self.entry_id)
        return False

    def _open_reply_dialog(self):
        dlg = ReplyDialog(self.email_data, self)
        dlg.exec()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            ctrl_held = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            self.selected.emit(self, ctrl_held)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.entry_id:
            open_outlook_email(self.entry_id)
        event.accept()


# ── Card builder ──────────────────────────────────────────────────────

def build_card(email_like: dict, todos=None, is_saved=False) -> EmailCard:
    entry_id = email_like.get("entry_id") or email_like.get("email_entry_id", "")

    card = EmailCard(entry_id, email_data=email_like)

    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(6)

    subject = email_like.get("subject") or email_like.get("email_subject", "(No Subject)")
    lbl = QLabel(f"📧 <b>{subject}</b>")
    lbl.setStyleSheet("color: #89B4FA; font-size: 13pt;")
    lbl.setWordWrap(True)
    layout.addWidget(lbl)

    sender = email_like.get("sender") or email_like.get("email_sender", "?")
    lbl_sender = QLabel(f"👤 {sender}")
    lbl_sender.setStyleSheet("color: #A6ADC8; font-size: 10pt;")
    lbl_sender.setWordWrap(True)
    layout.addWidget(lbl_sender)

    received = (email_like.get("received_time") or email_like.get("created_at") or "")
    if received:
        # Clean up common ISO formats: "2026-06-08 11:26:44.894000+00:00" -> "2026-06-08  11:26"
        cleaned = received.replace("T", " ")[:19]  # "2026-06-08 11:26:44"
        if len(cleaned) >= 16:
            cleaned = cleaned[:10] + "  " + cleaned[11:16]
        lbl_time = QLabel(f"🕐 {cleaned}")
        lbl_time.setStyleSheet("color: #A6ADC8; font-size: 10pt;")
        lbl_time.setWordWrap(True)
        layout.addWidget(lbl_time)

    cn = email_like.get("chinese_summary", "")
    if cn and cn != "N/A":
        lbl3 = QLabel(f"🇨🇳 <b>{cn}</b>")
        lbl3.setStyleSheet("color: #F9E2AF; font-size: 12pt;")
        lbl3.setWordWrap(True)
        layout.addWidget(lbl3)

    a = email_like.get("assignee", "")
    if a and a != "?":
        lbl4 = QLabel(f"👔 负责人: <b>{a}</b>")
        lbl4.setStyleSheet("color: #A6E3A1; font-size: 11pt;")
        lbl4.setWordWrap(True)
        layout.addWidget(lbl4)

    if todos is None and not is_saved:
        todos = email_like.get("todos", [])
    if todos:
        lines = "<br>".join(f"  • {t}" for t in todos)
        lbl5 = QLabel(f"✅ <b>待办事项:</b><br>{lines}")
        lbl5.setStyleSheet("color: #CBA6F7; font-size: 11pt;")
        lbl5.setWordWrap(True)
        layout.addWidget(lbl5)

    return card


# ── Main Window ───────────────────────────────────────────────────────

class SummaryWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ASummary1 — 邮件中文摘要")
        self.setMinimumSize(960, 750)
        self.setStyleSheet(WINDOW_STYLE)

        central = QWidget()
        self.setCentralWidget(central)
        ml = QVBoxLayout(central)
        ml.setContentsMargins(16, 16, 16, 16)
        ml.setSpacing(10)

        # ── Mode toggle bar ──
        bar = QWidget()
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(0, 0, 0, 0)

        self.btn_saved = QPushButton("📋 已保存")
        self.btn_saved.setMinimumHeight(36)
        self.btn_saved.setProperty("active", "true")
        self.btn_saved.clicked.connect(self.show_saved)

        self.btn_todo = QPushButton("🚩 TO DO")
        self.btn_todo.setMinimumHeight(36)
        self.btn_todo.clicked.connect(self.show_todo)

        self.btn_new = QPushButton("🔄 新邮件总结")
        self.btn_new.setMinimumHeight(36)
        self.btn_new.clicked.connect(self.show_new)

        bar_layout.addWidget(self.btn_saved)
        bar_layout.addWidget(self.btn_todo)
        bar_layout.addWidget(self.btn_new)

        self.count_combo = QComboBox()
        self.count_combo.addItems(["5", "10", "20", "30", "50", "100"])
        self.count_combo.setCurrentText("20")
        self.count_combo.setFixedWidth(70)

        self.btn_start = QPushButton("▶ 开始总结")
        self.btn_start.setMinimumHeight(36)
        self.btn_start.clicked.connect(self.start_summary)

        self.btn_unread = QPushButton("📖 仅未读")
        self.btn_unread.setMinimumHeight(36)
        self.btn_unread.clicked.connect(self.start_unread_only)

        bar_layout.addStretch()
        bar_layout.addWidget(QLabel("数量:"))
        bar_layout.addWidget(self.count_combo)
        bar_layout.addWidget(self.btn_start)
        bar_layout.addWidget(self.btn_unread)

        self._new_controls = [self.count_combo, self.btn_start, self.btn_unread]
        for w in self._new_controls:
            w.setVisible(False)

        ml.addWidget(bar)

        # ── Progress ──
        self.progress = QProgressBar()
        self.progress.setFixedHeight(6)
        self.progress.setVisible(False)
        ml.addWidget(self.progress)

        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color: #A6ADC8; font-size: 11pt;")
        ml.addWidget(self.lbl_status)

        # ── Scrollable card area ──
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(8, 8, 8, 8)
        self.cards_layout.setSpacing(8)
        self.cards_layout.addStretch()
        self.scroll.setWidget(self.cards_container)
        ml.addWidget(self.scroll, stretch=1)

        self.cards: list[EmailCard] = []
        self._selected_cards: set[EmailCard] = set()

        # Global shortcut: Ctrl+R = reply for selected card
        self.shortcut_ctrl_r = QShortcut(QKeySequence("Ctrl+R"), self)
        self.shortcut_ctrl_r.activated.connect(self._on_ctrl_r)

        # Global shortcut: Ctrl+F = flag selected cards in Outlook
        self.shortcut_ctrl_f = QShortcut(QKeySequence("Ctrl+F"), self)
        self.shortcut_ctrl_f.activated.connect(self._on_ctrl_f)

        # Global shortcuts
        self.shortcut_enter = QShortcut(QKeySequence("Return"), self)
        self.shortcut_enter.activated.connect(self._on_enter_open)

        self.shortcut_backspace = QShortcut(QKeySequence("Backspace"), self)
        self.shortcut_backspace.activated.connect(self._on_backspace)

        # Auto-load saved on open
        self.show_saved()

    # ── Mode Switching ──────────────────────────────────────────────

    def show_saved(self):
        self._set_active_tab("saved")
        for w in self._new_controls:
            w.setVisible(False)
        self._clear_cards()
        self.progress.setVisible(True)
        self.lbl_status.setText("Loading saved summaries...")

        self.load_worker = LoadSavedWorker(limit=100)
        self.load_worker.loaded.connect(self._on_saved_loaded)
        self.load_worker.error_occurred.connect(self._on_saved_error)
        self.load_worker.start()

    def show_new(self):
        self._set_active_tab("new")
        for w in self._new_controls:
            w.setVisible(True)
        self._clear_cards()
        self.lbl_status.setText("Ready — click '开始总结' or '仅未读'")

    def show_todo(self):
        self._set_active_tab("todo")
        for w in self._new_controls:
            w.setVisible(False)
        self._clear_cards()
        self.progress.setVisible(True)
        self.lbl_status.setText("Loading TO DO items...")

        self.load_worker = LoadSavedWorker(limit=100, status='todo')
        self.load_worker.loaded.connect(self._on_saved_loaded)
        self.load_worker.error_occurred.connect(self._on_saved_error)
        self.load_worker.start()

    def _set_active_tab(self, which):
        for btn, val in [(self.btn_saved, "saved"), (self.btn_todo, "todo"), (self.btn_new, "new")]:
            active = (val == which)
            btn.setProperty("active", "true" if active else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    # ── New summary actions ────────────────────────────────────────

    def start_summary(self):
        self._run_worker(int(self.count_combo.currentText()), unread_only=False)

    def start_unread_only(self):
        self._run_worker(int(self.count_combo.currentText()), unread_only=True)

    def _run_worker(self, count, unread_only):
        self._clear_cards()
        self.btn_start.setEnabled(False)
        self.btn_unread.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setMaximum(count)
        self.lbl_status.setText("Connecting to Outlook...")

        self.summary_worker = SummaryWorker(count=count, unread_only=unread_only)
        self.summary_worker.email_fetched.connect(self._on_email_fetched)
        self.summary_worker.progress_update.connect(self._on_progress)
        self.summary_worker.summary_ready.connect(self._on_summary_ready)
        self.summary_worker.all_done.connect(self._on_all_done)
        self.summary_worker.error_occurred.connect(self._on_error)
        self.summary_worker.start()

    # ── Slots: new summaries ───────────────────────────────────────

    def _on_email_fetched(self, total):
        self.progress.setMaximum(total)

    def _on_progress(self, cur, total, msg):
        self.progress.setValue(cur)
        self.lbl_status.setText(msg)

    def _on_summary_ready(self, idx, email, summary):
        # Merge summary into email so build_card can find chinese_summary, assignee, etc.
        merged = {**email, **summary}
        card = build_card(merged, todos=summary.get("todos", []), is_saved=False)
        card.removed.connect(self._on_card_removed)
        card.selected.connect(self._on_card_selected)
        self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)
        self.cards.append(card)

    def _on_all_done(self):
        self.btn_start.setEnabled(True)
        self.btn_unread.setEnabled(True)
        self.progress.setVisible(False)
        self.lbl_status.setText(f"✅ Done! {len(self.cards)} email(s) summarized and saved.")

    def _on_error(self, msg):
        self.btn_start.setEnabled(True)
        self.btn_unread.setEnabled(True)
        self.progress.setVisible(False)
        QMessageBox.warning(self, "Error", msg)

    # ── Slots: saved summaries ─────────────────────────────────────

    def _on_saved_loaded(self, rows):
        self.progress.setVisible(False)
        if not rows:
            self.lbl_status.setText("No saved summaries yet. Switch to '新邮件总结' to generate some.")
            return
        for row in rows:
            todos = []
            try:
                todos = json.loads(row.get("todos_json", "[]"))
            except Exception:
                pass
            card = build_card(row, todos=todos, is_saved=True)
            card.removed.connect(self._on_card_removed)
            card.selected.connect(self._on_card_selected)
            self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)
            self.cards.append(card)
        self.lbl_status.setText(f"📋 {len(rows)} saved summary(s) loaded.")

    def _on_saved_error(self, msg):
        self.progress.setVisible(False)
        QMessageBox.warning(self, "DB Error", msg)

    # ── Helpers ────────────────────────────────────────────────────

    def _on_card_selected(self, card, ctrl_held):
        if ctrl_held:
            # Ctrl+Click: toggle this card
            if card in self._selected_cards:
                self._selected_cards.discard(card)
                card.set_selected(False)
            else:
                self._selected_cards.add(card)
                card.set_selected(True)
        else:
            # Plain click: deselect all, select only this one
            for c in self._selected_cards:
                if c != card:
                    c.set_selected(False)
            self._selected_cards.clear()
            self._selected_cards.add(card)
            card.set_selected(True)
        self.lbl_status.setText(f"📋 {len(self.cards)} summaries | {len(self._selected_cards)} selected")

    def _on_ctrl_r(self):
        target = next(iter(self._selected_cards), None) if self._selected_cards else None
        if not target and self.cards:
            target = self.cards[0]
        if target:
            target.open_reply()

    def _on_ctrl_f(self):
        targets = list(self._selected_cards) if self._selected_cards else self.cards[:1]
        flagged = 0
        for card in targets:
            ok = card.flag_email()  # flags + marks as read in Outlook
            if ok:
                _set_card_status(card.entry_id, 'todo')
                card.removed.emit(card)
                flagged += 1
        self._selected_cards.clear()
        self.lbl_status.setText(f"🚩 {flagged} email(s) moved to TO DO — flagged + marked read in Outlook.")

    def _on_arrow_up(self):
        if not self.cards:
            return
        if not self._selected_cards:
            # Select last card
            self._select_single(self.cards[-1])
            return
        current = list(self._selected_cards)[0]
        idx = self.cards.index(current) if current in self.cards else -1
        if idx > 0:
            self._select_single(self.cards[idx - 1])

    def _on_arrow_down(self):
        if not self.cards:
            return
        if not self._selected_cards:
            self._select_single(self.cards[0])
            return
        current = list(self._selected_cards)[0]
        idx = self.cards.index(current) if current in self.cards else -1
        if idx >= 0 and idx < len(self.cards) - 1:
            self._select_single(self.cards[idx + 1])

    def _on_enter_open(self):
        targets = list(self._selected_cards) if self._selected_cards else (self.cards[:1] if self.cards else [])
        for card in targets:
            if card.entry_id:
                open_outlook_email(card.entry_id)

    def _select_single(self, card):
        for c in self._selected_cards:
            c.set_selected(False)
        self._selected_cards.clear()
        self._selected_cards.add(card)
        card.set_selected(True)
        self.scroll.ensureWidgetVisible(card)
        self.lbl_status.setText(f"📋 {len(self.cards)} summaries | {len(self._selected_cards)} selected")

    def _on_backspace(self):
        if not self._selected_cards:
            return
        cards_to_remove = list(self._selected_cards)
        for card in cards_to_remove:
            if card.entry_id:
                mark_email_as_read(card.entry_id)
            card.removed.emit(card)
        self._selected_cards.clear()
        self.lbl_status.setText(f"📋 {len(self.cards)} summaries | {len(cards_to_remove)} removed & marked read.")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Up:
            self._on_arrow_up()
        elif event.key() == Qt.Key.Key_Down:
            self._on_arrow_down()
        else:
            super().keyPressEvent(event)

    def _on_card_removed(self, card):
        self._selected_cards.discard(card)
        if card in self.cards:
            self.cards.remove(card)
        self.cards_layout.removeWidget(card)
        card.deleteLater()
        if not self.cards:
            self.lbl_status.setText("No summaries to show.")

    def _clear_cards(self):
        while self.cards_layout.count() > 1:
            item = self.cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.cards.clear()


# ── Entry point ───────────────────────────────────────────────────────

def show_summary_window():
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)
    win = SummaryWindow()
    win.show()
    app.exec()
