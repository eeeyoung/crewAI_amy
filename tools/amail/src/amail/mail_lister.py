from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QAbstractItemView,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QShortcut, QKeySequence, QCursor

from shared_tools.outlook_tool import fetch_inbox_emails


class DragSelectTable(QTableWidget):
    """QTableWidget with click-and-drag checkbox selection + auto-scroll."""

    selection_changed = pyqtSignal()

    def __init__(self, rows: int = 0, columns: int = 4, max_selection: int = 100, parent=None):
        super().__init__(rows, columns, parent)
        self.max_selection = max_selection
        self._drag_active = False
        self._drag_state = Qt.CheckState.Unchecked
        self._last_drag_row = -1
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setInterval(40)
        self._scroll_timer.timeout.connect(self._do_autoscroll)
        self._scroll_direction = 0
        self.setMouseTracking(True)

    def _count_checked(self):
        count = 0
        for row in range(self.rowCount()):
            item = self.item(row, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                count += 1
        return count

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.pos())
            if item is not None:
                cb_item = self.item(item.row(), 0)
                if cb_item is not None:
                    new_state = Qt.CheckState.Unchecked if cb_item.checkState() == Qt.CheckState.Checked else Qt.CheckState.Checked
                    if new_state == Qt.CheckState.Checked and self._count_checked() >= self.max_selection:
                        super().mousePressEvent(event)
                        return
                    cb_item.setCheckState(new_state)
                    self._drag_state = new_state
                    self._drag_active = True
                    self._last_drag_row = item.row()
                    self.selection_changed.emit()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_active:
            item = self.itemAt(event.pos())
            if item is not None:
                row = item.row()
                if row != self._last_drag_row:
                    if self._drag_state == Qt.CheckState.Checked:
                        already = self._count_checked()
                        if already >= self.max_selection:
                            self._last_drag_row = row
                            return

                    start = min(self._last_drag_row, row)
                    end = max(self._last_drag_row, row)
                    for r in range(start, end + 1):
                        cb_item = self.item(r, 0)
                        if cb_item is not None and cb_item.checkState() != self._drag_state:
                            cb_item.setCheckState(self._drag_state)
                    self._last_drag_row = row
                    self.selection_changed.emit()

            viewport = self.viewport()
            pos_y = event.pos().y()
            if pos_y < 30:
                self._scroll_direction = -1
                if not self._scroll_timer.isActive():
                    self._scroll_timer.start()
            elif pos_y > viewport.height() - 30:
                self._scroll_direction = 1
                if not self._scroll_timer.isActive():
                    self._scroll_timer.start()
            else:
                self._scroll_timer.stop()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_active:
            self._drag_active = False
            self._scroll_timer.stop()
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        if self._drag_active:
            self._scroll_timer.stop()
        super().leaveEvent(event)

    def _do_autoscroll(self):
        if not self._drag_active:
            self._scroll_timer.stop()
            return

        scrollbar = self.verticalScrollBar()
        new_val = scrollbar.value() + self._scroll_direction * 20
        new_val = max(scrollbar.minimum(), min(scrollbar.maximum(), new_val))
        scrollbar.setValue(new_val)

        cursor_pos = self.viewport().mapFromGlobal(QCursor.pos())
        item = self.itemAt(cursor_pos)
        if item is not None:
            row = item.row()
            if row != self._last_drag_row:
                if self._drag_state == Qt.CheckState.Checked:
                    if self._count_checked() >= self.max_selection:
                        self._last_drag_row = row
                        return
                start = min(self._last_drag_row, row)
                end = max(self._last_drag_row, row)
                for r in range(start, end + 1):
                    cb_item = self.item(r, 0)
                    if cb_item is not None and cb_item.checkState() != self._drag_state:
                        cb_item.setCheckState(self._drag_state)
                self._last_drag_row = row
                self.selection_changed.emit()


class FetchMailWorker(QThread):
    mail_fetched = pyqtSignal(list)

    def __init__(self, exclude_entry_ids: set, parent=None):
        super().__init__(parent)
        self.exclude_entry_ids = exclude_entry_ids
        self.running = True

    def run(self):
        try:
            emails = fetch_inbox_emails(
                count=100, max_body=5000, unread_only=True,
                exclude_entry_ids=self.exclude_entry_ids
            )
            if self.running:
                self.mail_fetched.emit(emails)
        except Exception:
            if self.running:
                self.mail_fetched.emit([])

    def stop(self):
        self.running = False


class MailListerDialog(QDialog):
    def __init__(self, processed_entry_ids: set, parent=None,
                 graph_service=None):               # ← optional GraphService
        super().__init__(parent)
        self.processed_entry_ids = processed_entry_ids
        self.displayed_entry_ids: set[str] = set()
        self.max_selection = 100
        self.selected_emails: list[dict] = []
        self._emails_data: list[dict] = []
        self._fetching = False
        self._suppress_item_changed = False
        self._active_workers: list[FetchMailWorker] = []
        self._monitor_timer = None
        self._graph_service = graph_service         # may be None
        self._graph_enriched = False

        # Wire Graph enrichment signal (detachable — just remove this block)
        if self._graph_service:
            self._graph_service.enrichment_complete.connect(
                self._on_graph_enrichment_complete
            )

        self._build_ui()
        self._trigger_fetch()
        self._start_monitor()

    def _build_ui(self):
        self.setWindowTitle("Unread Emails — Select up to 100")
        self.resize(900, 600)
        self.setMinimumSize(600, 400)

        self.setStyleSheet("""
            QDialog { background-color: #FFFDE7; }
            QLabel { border: none; background-color: transparent; color: #3E3E3E; }
            QPushButton {
                background-color: #FFF3CD;
                border: 1px solid #E6DEB1;
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: bold;
                color: #3E3E3E;
            }
            QPushButton:hover { background-color: #FFE8A1; }
            QPushButton:disabled { color: #A8A8A8; background-color: #FFF9E6; }
            QTableWidget {
                background-color: #FFFFFF;
                border: 1px solid #E6DEB1;
                border-radius: 4px;
                gridline-color: #F0EDD8;
                color: #1A1A1A;
            }
            QTableWidget::item { padding: 4px; color: #1A1A1A; }
            QTableWidget::item:selected { background-color: #FFF3CD; color: #1A1A1A; }
            QTableWidget::indicator {
                border: 2px solid #1A1A1A;
                width: 18px;
                height: 18px;
                border-radius: 3px;
                background-color: #FFFFFF;
            }
            QTableWidget::indicator:checked {
                background-color: #0078D4;
                border: 2px solid #1A1A1A;
            }
            QHeaderView::section {
                background-color: #FFF9E6;
                border: 1px solid #E6DEB1;
                padding: 6px;
                font-weight: bold;
                color: #3E3E3E;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # Header
        lbl_header = QLabel("Unread Emails — Select up to 100")
        lbl_header.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        lbl_header.setStyleSheet("color: #3E3E3E;")
        layout.addWidget(lbl_header)

        # Toolbar row
        toolbar = QHBoxLayout()
        self.btn_refresh = QPushButton("Refresh (F5)")
        self.btn_refresh.setMinimumWidth(110)
        self.btn_refresh.clicked.connect(self._trigger_fetch)
        toolbar.addWidget(self.btn_refresh)

        self.btn_select_all = QPushButton("Select All")
        self.btn_select_all.setMinimumWidth(90)
        self.btn_select_all.clicked.connect(self._select_all)
        toolbar.addWidget(self.btn_select_all)

        self.btn_unselect_all = QPushButton("Unselect All")
        self.btn_unselect_all.setMinimumWidth(90)
        self.btn_unselect_all.clicked.connect(self._unselect_all)
        toolbar.addWidget(self.btn_unselect_all)

        toolbar.addStretch()

        self.lbl_status = QLabel("Fetching...")
        self.lbl_status.setStyleSheet("color: #7A7A7A; font-size: 12px;")
        toolbar.addWidget(self.lbl_status)

        self.lbl_new_arrivals = QLabel("")
        self.lbl_new_arrivals.setStyleSheet(
            "color: #D83B01; font-weight: bold; font-size: 12px; "
            "background-color: #FFF3CD; padding: 4px 10px; border-radius: 4px;"
        )
        toolbar.addWidget(self.lbl_new_arrivals)

        layout.addLayout(toolbar)

        # Table
        self.table = DragSelectTable(0, 4, max_selection=self.max_selection)
        self.table.setHorizontalHeaderLabels(["", "Sender", "Subject", "Received"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(True)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet(
            self.table.styleSheet() +
            "QTableWidget { alternate-background-color: #FFFDF5; }"
        )

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 50)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(1, 200)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(3, 220)

        self.table.itemChanged.connect(self._on_item_changed)
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.selection_changed.connect(self._on_drag_selection_changed)
        layout.addWidget(self.table, stretch=1)

        # Bottom bar
        bottom = QHBoxLayout()

        self.btn_process = QPushButton("Process Selected (Enter)")
        self.btn_process.setMinimumHeight(40)
        self.btn_process.setStyleSheet(
            "background-color: #0078D4; color: white; font-weight: bold; "
            "font-size: 13px; border-radius: 4px; padding: 8px 20px;"
        )
        self.btn_process.setEnabled(False)
        self.btn_process.clicked.connect(self._process_selected)
        bottom.addWidget(self.btn_process)

        # ── Graph API sign-in (optional, detachable) ──────────────
        self.btn_graph = QPushButton("🔑 Sign in with Microsoft")
        self.btn_graph.setMinimumHeight(40)
        self.btn_graph.setStyleSheet(
            "background-color: #E8F0FE; color: #1a73e8; font-weight: bold; "
            "font-size: 12px; border-radius: 4px; padding: 6px 12px;"
        )
        self.btn_graph.clicked.connect(self._on_graph_sign_in)
        self.btn_graph.setVisible(self._graph_service is not None)
        bottom.addWidget(self.btn_graph)

        self._lbl_graph_status = QLabel()
        self._lbl_graph_status.setStyleSheet("color: #666; font-size: 11px;")
        self._lbl_graph_status.setVisible(False)
        bottom.addWidget(self._lbl_graph_status)

        bottom.addStretch()

        self.lbl_selection = QLabel("Select up to 100 emails to process")
        self.lbl_selection.setStyleSheet("color: #A8A8A8; font-size: 12px;")
        bottom.addWidget(self.lbl_selection)

        layout.addLayout(bottom)

        # Keyboard shortcuts
        QShortcut(QKeySequence("Return"), self).activated.connect(self._process_selected)
        QShortcut(QKeySequence("Enter"), self).activated.connect(self._process_selected)
        QShortcut(QKeySequence("Refresh"), self).activated.connect(self._trigger_fetch)
        QShortcut(QKeySequence("Escape"), self).activated.connect(self.reject)

    def _trigger_fetch(self):
        if self._fetching:
            return
        self._fetching = True
        self.lbl_status.setText("Fetching...")
        self.btn_refresh.setEnabled(False)

        worker = FetchMailWorker(self.processed_entry_ids, parent=self)
        worker.mail_fetched.connect(self._on_fetch_complete)
        worker.finished.connect(worker.deleteLater)
        worker.start()
        self._active_workers.append(worker)

    def _on_fetch_complete(self, emails):
        self._fetching = False
        self.btn_refresh.setEnabled(True)

        self.table.setRowCount(0)
        self._emails_data.clear()
        self.displayed_entry_ids.clear()

        for email in emails:
            email.setdefault("is_focused", True)  # default: focused
            self._add_email_row(email)

        n = len(emails)
        self.lbl_status.setText(f"{n} unread email(s)")
        self._update_process_button()

        # ── Background Graph enrichment (detachable) ──────────────
        if self._graph_service and self._graph_service.is_authenticated:
            self._graph_service.enrich_async(emails)

    # ── Graph API handlers (detachable) ───────────────────────────

    def _on_graph_sign_in(self):
        """Open the device-code sign-in dialog."""
        if not self._graph_service:
            return
        from amail.graph_dialog import GraphSignInDialog
        dialog = GraphSignInDialog(self._graph_service, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._lbl_graph_status.setText("✓ Connected")
            self._lbl_graph_status.setVisible(True)
            self.btn_graph.setVisible(False)
            # Re-fetch to enrich current emails
            self._trigger_fetch()
        else:
            self._lbl_graph_status.setText("Sign-in cancelled")
            self._lbl_graph_status.setVisible(True)

    def _on_graph_enrichment_complete(self, results: dict[str, bool]):
        """Update email data with Focused/Other classification from Graph."""
        self._graph_enriched = True
        focused_count = 0
        other_count = 0
        for email in self._emails_data:
            eid = email.get("entry_id", "")
            if eid in results:
                email["is_focused"] = results[eid]
            if email.get("is_focused", True):
                focused_count += 1
            else:
                other_count += 1
        self._lbl_graph_status.setText(
            f"✓ {focused_count} Focused · {other_count} Other"
        )
        self._lbl_graph_status.setVisible(True)

    # ── Email table ───────────────────────────────────────────────

    def _add_email_row(self, email):
        eid = email.get("entry_id", "")
        self.displayed_entry_ids.add(eid)
        self._emails_data.append(email)

        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setRowHeight(row, 32)

        # Checkbox column
        cb_item = QTableWidgetItem()
        cb_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        cb_item.setCheckState(Qt.CheckState.Unchecked)
        cb_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(row, 0, cb_item)

        # Sender column — strip email address for display
        sender_raw = email.get("sender", "")
        sender_display = sender_raw.split("<")[0].strip() if "<" in sender_raw else sender_raw
        self.table.setItem(row, 1, QTableWidgetItem(sender_display))

        # Subject column
        self.table.setItem(row, 2, QTableWidgetItem(email.get("subject", "")))

        # Received time column
        self.table.setItem(row, 3, QTableWidgetItem(email.get("received_time", "")))

    def _on_cell_clicked(self, row, col):
        if col == 0:
            return  # Qt's built-in ItemIsUserCheckable already toggled it; itemChanged handles validation

        cb_item = self.table.item(row, 0)
        if cb_item is None:
            return

        new_state = Qt.CheckState.Unchecked if cb_item.checkState() == Qt.CheckState.Checked else Qt.CheckState.Checked
        if new_state == Qt.CheckState.Checked and self._count_checked() >= self.max_selection:
            QMessageBox.warning(self, "Selection Limit", f"You can select up to {self.max_selection} emails at a time.")
            return
        cb_item.setCheckState(new_state)

    def _on_item_changed(self, item):
        if self._suppress_item_changed or item.column() != 0:
            return

        if item.checkState() == Qt.CheckState.Checked and self._count_checked() > self.max_selection:
            self._suppress_item_changed = True
            item.setCheckState(Qt.CheckState.Unchecked)
            self._suppress_item_changed = False
            if not self.table._drag_active:
                QMessageBox.warning(self, "Selection Limit", f"You can select up to {self.max_selection} emails at a time.")
            return

        self._update_process_button()
        self._update_selection_label()

    def _on_drag_selection_changed(self):
        self._update_process_button()
        self._update_selection_label()

    def _count_checked(self):
        return self.table._count_checked()

    def _update_process_button(self):
        checked = self._count_checked()
        self.btn_process.setEnabled(checked > 0)
        if checked > 0:
            self.btn_process.setText(f"Process Selected ({checked}) (Enter)")
        else:
            self.btn_process.setText("Process Selected (Enter)")

    def _select_all(self):
        self._suppress_item_changed = True
        for row in range(self.table.rowCount()):
            cb_item = self.table.item(row, 0)
            if cb_item is not None and cb_item.checkState() == Qt.CheckState.Unchecked:
                if self._count_checked() >= self.max_selection:
                    break
                cb_item.setCheckState(Qt.CheckState.Checked)
        self._suppress_item_changed = False
        self._update_process_button()
        self._update_selection_label()

    def _unselect_all(self):
        self._suppress_item_changed = True
        for row in range(self.table.rowCount()):
            cb_item = self.table.item(row, 0)
            if cb_item is not None and cb_item.checkState() == Qt.CheckState.Checked:
                cb_item.setCheckState(Qt.CheckState.Unchecked)
        self._suppress_item_changed = False
        self._update_process_button()
        self._update_selection_label()

    def _update_selection_label(self):
        checked = self._count_checked()
        remaining = self.max_selection - checked
        if checked == 0:
            self.lbl_selection.setText(f"Select up to {self.max_selection} emails to process")
        else:
            self.lbl_selection.setText(f"{checked} selected — {remaining} remaining")

    def _start_monitor(self):
        self._monitor_timer = QTimer(self)
        self._monitor_timer.setInterval(10000)
        self._monitor_timer.timeout.connect(self._poll_for_new)
        self._monitor_timer.start()

    def _poll_for_new(self):
        if self._fetching:
            return
        self._fetching = True

        combined_exclude = self.processed_entry_ids | self.displayed_entry_ids
        worker = FetchMailWorker(combined_exclude, parent=self)
        worker.mail_fetched.connect(self._on_poll_result)
        worker.finished.connect(worker.deleteLater)
        worker.start()
        self._active_workers.append(worker)

    def _on_poll_result(self, emails):
        self._fetching = False

        if not emails:
            return

        new_count = 0
        for email in emails:
            eid = email.get("entry_id", "")
            if eid and eid not in self.displayed_entry_ids:
                self._add_email_row(email)
                new_count += 1

        if new_count > 0:
            total = len(self._emails_data)
            self.lbl_status.setText(f"{total} unread email(s)")
            self.lbl_new_arrivals.setText(f"{new_count} new email(s) arrived")
            QTimer.singleShot(5000, lambda: self.lbl_new_arrivals.setText(""))

    def _process_selected(self):
        checked = self._count_checked()
        if checked == 0:
            return

        self.selected_emails = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                email = self._emails_data[row]
                eid = email.get("entry_id", "")
                if eid:
                    self.processed_entry_ids.add(eid)
                self.selected_emails.append(email)

        self.accept()

    def get_selected_emails(self):
        return self.selected_emails

    def closeEvent(self, event):
        if self._monitor_timer:
            self._monitor_timer.stop()
        for worker in self._active_workers:
            worker.stop()
        for worker in self._active_workers:
            worker.wait(3000)
        super().closeEvent(event)
