import sys
import json
from datetime import datetime, date, timedelta
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QComboBox, QMessageBox, QFrame, QAbstractItemView, QDialog,
    QLineEdit, QFormLayout, QDialogButtonBox,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont

from shared_tools.core.ipc_bridge import (
    get_app_status,
)
from shared_tools.outlook.outlook_tool import OutlookSendTool
from shared_tools.calendar.calendar_service import CalendarService

# ── icons for date types ──
TYPE_ICONS = {
    "exact":       "✅ exact",       # ✅
    "approximate": "⏳ approx",      # ⏳
    "range":       "\U0001f4c5 range",   # 📅
    "deadline":    "⚡ deadline",     # ⚡
    "tbd":         "❓ tbd",          # ❓
}


# =============================================================================
# Background Workers
# =============================================================================

# =============================================================================
# Utility
# =============================================================================

def _detect_conflicts(events: list[dict]) -> list[dict]:
    """Compare pending events and detect date overlaps.
    Returns list of conflict dicts: {event_id_1, event_id_2, conflict_type}."""
    conflicts = []
    pending = [e for e in events if e.get("status") == "pending" and e.get("start_date")]
    for i in range(len(pending)):
        for j in range(i + 1, len(pending)):
            e1, e2 = pending[i], pending[j]
            s1 = e1["start_date"] or ""
            e1_end = e1.get("end_date") or s1
            s2 = e2["start_date"] or ""
            e2_end = e2.get("end_date") or s2

            if not s1 or not s2:
                continue

            # Check for overlap
            if s1 <= e2_end and s2 <= e1_end:
                conflicts.append({
                    "event_id_1": e1["id"],
                    "event_id_2": e2["id"],
                    "conflict_type": "overlap" if s1 != s2 else "same_day",
                })
    return conflicts


# =============================================================================
# Event Edit Dialog
# =============================================================================

DATE_TYPES = ["exact", "approximate", "range", "deadline", "tbd"]


class EventEditDialog(QDialog):
    """Dialog for viewing and editing a calendar event's details."""

    def __init__(self, event: dict, parent=None):
        super().__init__(parent)
        self.event = event
        self.parent_window = parent
        self._build_ui()
        self._populate()

    def _build_ui(self):
        self.setWindowTitle("Event Details")
        self.resize(500, 420)
        self.setMinimumWidth(450)

        # Match main window's warm palette
        self.setStyleSheet("""
            QDialog { background-color: #FFFDE7; }
            QLabel { border: none; background-color: transparent; color: #3E3E3E; }
            QLineEdit {
                background-color: #FFFFFF;
                border: 1px solid #E6DEB1;
                border-radius: 4px;
                padding: 5px 8px;
                color: #1A1A1A;
                font-size: 13px;
            }
            QLineEdit:focus {
                border: 2px solid #0078D4;
            }
            QComboBox {
                background-color: #FFFFFF;
                border: 1px solid #E6DEB1;
                border-radius: 4px;
                padding: 5px 8px;
                color: #1A1A1A;
                font-size: 13px;
            }
            QComboBox:hover { border-color: #C5B47A; }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
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
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        # ── Form fields ──
        form = QFormLayout()
        form.setSpacing(10)

        self.le_description = QLineEdit()
        self.le_description.setMinimumHeight(34)
        form.addRow("Description:", self.le_description)

        self.cb_type = QComboBox()
        self.cb_type.addItems(DATE_TYPES)
        self.cb_type.setMinimumHeight(34)
        form.addRow("Type:", self.cb_type)

        self.le_start_date = QLineEdit()
        self.le_start_date.setPlaceholderText("YYYY-MM-DD or ISO datetime")
        form.addRow("Start Date:", self.le_start_date)

        self.le_end_date = QLineEdit()
        self.le_end_date.setPlaceholderText("YYYY-MM-DD (optional)")
        form.addRow("End Date:", self.le_end_date)

        self.le_project = QLineEdit()
        self.le_project.setPlaceholderText("e.g., ARCO, Econolodge")
        form.addRow("Project:", self.le_project)

        layout.addLayout(form)

        # ── Source info (read-only) ──
        source = self.event.get("source_email_subject", "")
        if source:
            lbl_source = QLabel(f"📧 {source}")
            lbl_source.setStyleSheet("""
                color: #7A7A7A; font-style: italic; font-size: 11px;
                padding: 8px; background-color: #FFF9E6;
                border: 1px solid #E6DEB1; border-radius: 4px;
            """)
            lbl_source.setWordWrap(True)
            layout.addWidget(lbl_source)

        # ── Status ──
        status = self.event.get("status", "pending")
        outlook_id = self.event.get("outlook_event_id", "")
        status_text = f"Status: {status}"
        if outlook_id:
            status_text += f"  ⋮  Outlook: {outlook_id[:24]}..."
        self.lbl_status = QLabel(status_text)
        self.lbl_status.setStyleSheet("""
            color: #3E3E3E; font-size: 12px; font-weight: bold;
            padding: 6px 10px; background-color: #FFF9E6;
            border: 1px solid #E6DEB1; border-radius: 4px;
        """)
        layout.addWidget(self.lbl_status)

        layout.addStretch()

        # ── Buttons ──
        btn_layout = QHBoxLayout()

        self.btn_save = QPushButton("💾 Save")
        self.btn_save.setStyleSheet(
            "QPushButton { background-color: #0078D4; color: white; font-weight: bold;"
            "border-radius: 4px; padding: 8px 16px; border: none; }"
            "QPushButton:hover { background-color: #106EBE; }"
        )
        self.btn_save.clicked.connect(self._on_save)
        btn_layout.addWidget(self.btn_save)

        self.btn_outlook = QPushButton("📅 Push to Outlook")
        self.btn_outlook.setStyleSheet(
            "QPushButton { background-color: #107C10; color: white; font-weight: bold;"
            "border-radius: 4px; padding: 8px 16px; border: none; }"
            "QPushButton:hover { background-color: #0D652D; }"
        )
        self.btn_outlook.clicked.connect(self._on_push_outlook)
        btn_layout.addWidget(self.btn_outlook)

        self.btn_delete = QPushButton("🗑️ Delete")
        self.btn_delete.setStyleSheet(
            "QPushButton { background-color: #B31412; color: white; font-weight: bold;"
            "border-radius: 4px; padding: 8px 16px; border: none; }"
            "QPushButton:hover { background-color: #8B100E; }"
        )
        self.btn_delete.clicked.connect(self._on_delete)
        btn_layout.addWidget(self.btn_delete)

        btn_layout.addStretch()

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        btn_box.rejected.connect(self.reject)
        btn_layout.addWidget(btn_box)

        layout.addLayout(btn_layout)

    def _populate(self):
        self.le_description.setText(self.event.get("description", ""))
        self.le_start_date.setText(self.event.get("start_date", ""))
        self.le_end_date.setText(self.event.get("end_date") or "")
        self.le_project.setText(self.event.get("project") or "")

        dt = self.event.get("date_type", "tbd")
        idx = self.cb_type.findText(dt)
        self.cb_type.setCurrentIndex(max(idx, 0))

    def _on_save(self):
        """Persist edits to the shared DB."""
        try:
            update_calendar_event_db(
                self.event["id"],
                description=self.le_description.text().strip(),
                date_type=self.cb_type.currentText(),
                start_date=self.le_start_date.text().strip() or None,
                end_date=self.le_end_date.text().strip() or None,
                project=self.le_project.text().strip() or None,
            )
            self.lbl_status.setText("Status: saved ✓")
            self.lbl_status.setStyleSheet(
                "color: #0D652D; font-size: 12px; font-weight: bold;"
                "padding: 6px 10px; background-color: #E6F4EA;"
                "border: 1px solid #0D652D; border-radius: 4px;"
            )
            if self.parent_window:
                self.parent_window.load_events()
        except Exception as e:
            QMessageBox.warning(self, "Save Failed", str(e))

    def _on_push_outlook(self):
        """Create or update this event in Outlook Calendar."""
        desc = self.le_description.text().strip()
        start = self.le_start_date.text().strip()
        end = self.le_end_date.text().strip() or start
        proj = self.le_project.text().strip()

        if not start:
            QMessageBox.warning(self, "No Date", "Set a start date before pushing to Outlook.")
            return

        if "T" not in start:
            start = f"{start}T09:00:00"
        if "T" not in end:
            end = f"{end}T10:00:00"

        reply = QMessageBox.question(
            self, "Push to Outlook",
            f"Create Outlook appointment:\n\n{desc}\n{start[:10]}\nProject: {proj}\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        categories = [proj] if proj else None
        result = create_calendar_event(
            subject=desc, start_date=start, end_date=end,
            body=f"Source: {self.event.get('source_email_subject', '')}",
            categories=categories,
        )

        if result.startswith("Error:"):
            QMessageBox.warning(self, "Outlook Error", result)
            return

        update_calendar_event_db(self.event["id"], outlook_event_id=result, status="created")
        self.lbl_status.setText(f"Status: created  ⋮  Outlook: {result[:24]}...")
        self.lbl_status.setStyleSheet(
            "color: #0D652D; font-size: 12px; font-weight: bold;"
            "padding: 6px 10px; background-color: #E6F4EA;"
            "border: 1px solid #0D652D; border-radius: 4px;"
        )
        if self.parent_window:
            self.parent_window.load_events()

    def _on_delete(self):
        """Delete this event."""
        desc = self.event.get("description", "this event")
        reply = QMessageBox.question(
            self, "Delete Event",
            f"Delete '{desc}'?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        outlook_id = self.event.get("outlook_event_id", "")
        if outlook_id:
            reply2 = QMessageBox.question(
                self, "Delete from Outlook",
                "Also delete from Outlook Calendar?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply2 == QMessageBox.StandardButton.Yes:
                delete_calendar_event(outlook_id)

        update_calendar_event_db(self.event["id"], status="cancelled")
        if self.parent_window:
            self.parent_window.load_events()
        self.accept()


# =============================================================================
# Main Window
# =============================================================================

class CalendarWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.service = CalendarService(parent=self)
        self.projects: list[str] = []

        self.init_ui()
        self.load_events()
        self.start_polling()

    @property
    def events(self):
        return self.service._events

    @events.setter
    def events(self, value):
        self.service._events = value

    # ── UI Construction ────────────────────────────────────────────────

    def init_ui(self):
        self.setWindowTitle("ACalendar — Schedule Dashboard")
        self.resize(1400, 800)

        self.setStyleSheet("""
            QMainWindow { background-color: #FFFDE7; }
            QWidget { font-family: Arial, sans-serif; color: #3E3E3E; }
            QFrame { background-color: #FFFFFF; border-radius: 8px; border: 1px solid #E6DEB1; }
            QLabel { border: none; background-color: transparent; }
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
            QTableWidget {
                background-color: #FFFFFF;
                border: 1px solid #E6DEB1;
                border-radius: 4px;
                gridline-color: #F0EDD8;
                color: #1A1A1A;
            }
            QTableWidget::item { padding: 4px; color: #1A1A1A; }
            QTableWidget::item:selected { background-color: #FFF3CD; color: #1A1A1A; }
            QHeaderView::section {
                background-color: #FFF9E6;
                border: 1px solid #E6DEB1;
                padding: 6px;
                font-weight: bold;
                color: #3E3E3E;
            }
            QComboBox {
                background-color: #FFFFFF;
                border: 1px solid #E6DEB1;
                border-radius: 4px;
                padding: 4px 8px;
            }
        """)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(12)

        # --- Header ---
        header = QFrame()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(15, 10, 15, 10)

        lbl_title = QLabel("\U0001f4c5 ACalendar — Schedule Dashboard")
        lbl_title.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        header_layout.addWidget(lbl_title)
        header_layout.addStretch()

        self.amail_status_label = QLabel("AMail: • Checking...")
        self.amail_status_label.setStyleSheet(
            "font-size: 13px; font-weight: bold; color: #A8A8A8;"
        )
        header_layout.addWidget(self.amail_status_label)
        main_layout.addWidget(header)

        # --- Toolbar ---
        toolbar = QFrame()
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(15, 8, 15, 8)

        toolbar_layout.addWidget(QLabel("Job Filter:"))
        self.job_filter = QComboBox()
        self.job_filter.setMinimumWidth(120)
        self.job_filter.addItem("ALL")
        self.job_filter.currentTextChanged.connect(self.refresh_view)
        toolbar_layout.addWidget(self.job_filter)

        toolbar_layout.addWidget(QLabel("Date Type:"))
        self.type_filter = QComboBox()
        self.type_filter.setMinimumWidth(120)
        self.type_filter.addItem("ALL")
        for dt in ["exact", "approximate", "range", "deadline", "tbd"]:
            self.type_filter.addItem(dt)
        self.type_filter.currentTextChanged.connect(self.refresh_view)
        toolbar_layout.addWidget(self.type_filter)

        toolbar_layout.addStretch()

        self.btn_refresh = QPushButton("\U0001f504 Refresh from Mail")
        self.btn_refresh.setStyleSheet(
            "background-color: #0078D4; color: white; font-weight: bold; border-radius: 4px; padding: 6px 16px;"
        )
        self.btn_refresh.clicked.connect(self.on_refresh_from_mail)
        toolbar_layout.addWidget(self.btn_refresh)

        self.btn_digest = QPushButton("\U0001f4e7 Weekly Digest")
        self.btn_digest.setStyleSheet(
            "background-color: #107C10; color: white; font-weight: bold; border-radius: 4px; padding: 6px 16px;"
        )
        self.btn_digest.clicked.connect(self.on_weekly_digest)
        toolbar_layout.addWidget(self.btn_digest)

        main_layout.addWidget(toolbar)

        # --- Three table columns side-by-side ---
        tables_area = QHBoxLayout()
        tables_area.setSpacing(12)

        self._create_section(tables_area, "\U0001f4c6 UPCOMING\n(next 7 days)", "upcoming")
        self._create_section(tables_area, "\U0001f4c5 THIS MONTH", "month")
        self._create_section(tables_area, "❓ TBC / UNCONFIRMED", "tbc")

        main_layout.addLayout(tables_area, stretch=1)

        # --- Bottom action bar ---
        actions = QFrame()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(15, 8, 15, 8)

        self.btn_open_email = QPushButton("\U0001f4e8 Open Email in AMail")
        self.btn_open_email.setStyleSheet(
            "background-color: #5B5EA6; color: white; font-weight: bold; border-radius: 4px; padding: 6px 16px;"
        )
        self.btn_open_email.clicked.connect(self.on_open_in_amail)
        actions_layout.addWidget(self.btn_open_email)

        actions_layout.addStretch()

        self.lbl_status = QLabel("Ready")
        self.lbl_status.setStyleSheet("color: #888; font-style: italic;")
        actions_layout.addWidget(self.lbl_status)

        main_layout.addWidget(actions)

    def _create_section(self, parent_layout, title: str, key: str):
        """Create a labeled QTableWidget column and store it."""
        col = QVBoxLayout()
        col.setSpacing(6)

        lbl = QLabel(title)
        lbl.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        col.addWidget(lbl)

        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["Date", "Description", "Project", "Type"])
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.setShowGrid(True)
        table.setAlternatingRowColors(True)
        table.setStyleSheet(table.styleSheet() +
            "QTableWidget { alternate-background-color: #FFFDF5; }")

        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        table.setColumnWidth(0, 140)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        table.setColumnWidth(2, 100)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        table.setColumnWidth(3, 120)

        # Double-click opens edit dialog
        table.cellDoubleClicked.connect(
            lambda row, col, t=table: self._on_event_double_clicked(t, row)
        )

        col.addWidget(table)
        parent_layout.addLayout(col, stretch=1)
        setattr(self, f"table_{key}", table)

    # ── Data Loading ───────────────────────────────────────────────────

    def load_events(self):
        """Reload all events from the shared database."""
        self.service.load_events()
        self._refresh_project_filter()
        self.refresh_view()
        self.lbl_status.setText(f"{len(self.events)} event(s) loaded")

    def _refresh_project_filter(self):
        """Rebuild the project filter combo with unique projects from events."""
        current = self.job_filter.currentText()
        projects = sorted(set(
            (e.get("project") or "").strip() for e in self.events
            if (e.get("project") or "").strip()
        ))
        self.job_filter.blockSignals(True)
        self.job_filter.clear()
        self.job_filter.addItem("ALL")
        for p in projects:
            self.job_filter.addItem(p)
        idx = self.job_filter.findText(current)
        self.job_filter.setCurrentIndex(max(idx, 0))
        self.job_filter.blockSignals(False)

    def refresh_view(self):
        """Filter and repopulate all table sections."""
        job_filter = self.job_filter.currentText()
        type_filter = self.type_filter.currentText()
        today = date.today()
        week_end = today + timedelta(days=7)
        # Calculate month boundaries
        month_start = today.replace(day=1)

        filtered = self.events
        if job_filter and job_filter != "ALL":
            filtered = [e for e in filtered if e.get("project") == job_filter]
        if type_filter and type_filter != "ALL":
            filtered = [e for e in filtered if e.get("date_type") == type_filter]

        # Categorize
        upcoming = []
        month_events = []
        tbc_events = []

        for e in filtered:
            dt = e.get("date_type", "")
            start = e.get("start_date") or ""
            if dt == "tbd" or not start:
                tbc_events.append(e)
            elif start:
                try:
                    sd = date.fromisoformat(start[:10])
                    if sd <= week_end:
                        upcoming.append(e)
                    elif sd.month == today.month and sd.year == today.year:
                        month_events.append(e)
                    else:
                        month_events.append(e)  # beyond current month goes here too
                except ValueError:
                    tbc_events.append(e)
            else:
                month_events.append(e)

        self._populate_table(self.table_upcoming, upcoming)
        self._populate_table(self.table_month, month_events)
        self._populate_table(self.table_tbc, tbc_events)

    def _populate_table(self, table: QTableWidget, events: list[dict]):
        """Fill a table widget with event rows."""
        table.setRowCount(0)
        for e in events:
            row = table.rowCount()
            table.insertRow(row)
            table.setRowHeight(row, 28)

            # Date column
            start = e.get("start_date") or ""
            end = e.get("end_date") or ""
            if end and end != start:
                date_text = f"{start[:10] or '?'} – {end[:10] or '?'}"
            elif start:
                date_text = start[:10]
            else:
                date_text = "TBC"
            table.setItem(row, 0, QTableWidgetItem(date_text))

            # Description
            desc = e.get("description", "")
            table.setItem(row, 1, QTableWidgetItem(desc))

            # Project
            proj = e.get("project", "")
            table.setItem(row, 2, QTableWidgetItem(proj))

            # Type with icon
            dt = e.get("date_type", "tbd")
            icon_text = TYPE_ICONS.get(dt, dt)
            table.setItem(row, 3, QTableWidgetItem(icon_text))

            # Store event id as hidden data on the first column
            table.item(row, 0).setData(Qt.ItemDataRole.UserRole, e.get("id"))

        # Check for conflicts and mark them
        conflicts = _detect_conflicts(events)
        if conflicts:
            conflict_event_ids = set()
            for c in conflicts:
                conflict_event_ids.add(c["event_id_1"])
                conflict_event_ids.add(c["event_id_2"])
            # Highlight conflicting rows with a red tint
            for row in range(table.rowCount()):
                eid = table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                if eid in conflict_event_ids:
                    for col in range(table.columnCount()):
                        item = table.item(row, col)
                        if item:
                            item.setForeground(Qt.GlobalColor.red)

    def _get_selected_event(self) -> dict | None:
        """Return the event dict for the currently selected row across all tables."""
        for key in ("upcoming", "month", "tbc"):
            table = getattr(self, f"table_{key}")
            rows = table.selectionModel().selectedRows()
            if rows:
                eid = table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
                for e in self.events:
                    if e.get("id") == eid:
                        return e
        return None

    # ── Actions ─────────────────────────────────────────────────────────

    def _on_event_double_clicked(self, table: QTableWidget, row: int):
        """Open the edit dialog for the double-clicked event."""
        eid = table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        event = next((e for e in self.events if e.get("id") == eid), None)
        if event:
            dialog = EventEditDialog(event, parent=self)
            dialog.exec()

    def on_refresh_from_mail(self):
        """Pull latest calendar events from AMail's triage output."""
        results = self.service.pull_new_emails()
        self.load_events()
        if results:
            self.lbl_status.setText(f"Processed {len(results)} new email(s). Events updated.")
        else:
            self.lbl_status.setText("No new emails from AMail.")

    def on_open_in_amail(self):
        """Request AMail to navigate to the source email of the selected event."""
        event = self._get_selected_event()
        if not event:
            QMessageBox.warning(self, "No Selection", "Please select an event first.")
            return
        source_entry_id = event.get("source_email_entry_id", "")
        if not source_entry_id:
            QMessageBox.warning(self, "No Source", "This event has no linked source email.")
            return
        amail_running = get_app_status("amail")
        if not amail_running:
            QMessageBox.information(self, "AMail Not Running",
                                    "AMail is not currently running.\n"
                                    "Please launch AMail to navigate to the source email.")
            return
        CalendarService.navigate_to_amail(source_entry_id)
        self.lbl_status.setText("Navigation request sent to AMail.")
        QMessageBox.information(self, "Request Sent",
                                "Navigation request sent to AMail.\n"
                                "AMail will jump to the source email momentarily.")

    def on_weekly_digest(self):
        """Compose and send a weekly digest email of upcoming events."""
        try:
            today = date.today()
            week_end = today + timedelta(days=7)

            upcoming = [
                e for e in self.events
                if (e.get("start_date") or "") and (e.get("start_date") or "")[:10] <= week_end.isoformat()
                and e.get("status") != "cancelled"
            ]

            if not upcoming:
                QMessageBox.information(self, "No Events",
                                        "No events in the next 7 days.")
                return

            # Group by project
            by_project: dict[str, list] = {}
            for e in upcoming:
                proj = e.get("project") or "Other"
                by_project.setdefault(proj, []).append(e)

            # Build HTML body
            lines = ["<h2>Weekly Schedule Digest</h2>",
                      f"<p><b>Week of {today.isoformat()}</b></p><hr>"]
            for proj, events in sorted(by_project.items()):
                lines.append(f"<h3>{proj}</h3><ul>")
                for e in sorted(events, key=lambda x: (x.get("start_date") or "")):
                    start = (e.get("start_date") or "TBC")[:10]
                    dt = e.get("date_type") or ""
                    icon = TYPE_ICONS.get(dt, "").split(" ")[0]
                    lines.append(
                        f"<li>{icon} <b>{start}</b> — "
                        f"{e.get('description') or ''} ({dt})</li>"
                    )
                lines.append("</ul>")

            body = "\n".join(lines)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not build digest:\n{e}")
            return

        # Group by project
        by_project: dict[str, list] = {}
        for e in upcoming:
            proj = e.get("project", "Other")
            by_project.setdefault(proj, []).append(e)

        # Build HTML body
        lines = ["<h2>Weekly Schedule Digest</h2>",
                  f"<p><b>Week of {today.isoformat()}</b></p><hr>"]
        for proj, events in sorted(by_project.items()):
            lines.append(f"<h3>{proj}</h3><ul>")
            for e in sorted(events, key=lambda x: x.get("start_date", "")):
                start = e.get("start_date", "TBC")[:10]
                dt = e.get("date_type", "")
                icon = TYPE_ICONS.get(dt, "").split(" ")[0]
                lines.append(
                    f"<li>{icon} <b>{start}</b> — "
                    f"{e.get('description', '')} ({dt})</li>"
                )
            lines.append("</ul>")

        body = "\n".join(lines)

        import os
        recipient = os.environ.get("AMY_EMAIL", "")
        if not recipient:
            QMessageBox.warning(self, "No Recipient",
                                "Set AMY_EMAIL in your .env to send digests.")
            return
        ok = self.service.send_weekly_digest(recipient)
        if ok:
            QMessageBox.information(self, "Sent", "Weekly digest sent!")
            self.lbl_status.setText("Weekly digest sent.")
        else:
            QMessageBox.warning(self, "Error", "Failed to send digest.")

    # ── Polling ─────────────────────────────────────────────────────────

    def start_polling(self):
        """Start background timers for AMail status and triage polling."""
        self.amail_poll_timer = QTimer(self)
        self.amail_poll_timer.setInterval(5000)
        self.amail_poll_timer.timeout.connect(self._poll_amail_status)
        self.amail_poll_timer.start()

        self.triage_poll_timer = QTimer(self)
        self.triage_poll_timer.setInterval(30000)
        self.triage_poll_timer.timeout.connect(self._poll_new_triage)
        self.triage_poll_timer.start()

        # Initial checks
        self._poll_amail_status()
        self._poll_new_triage()

    def _poll_amail_status(self):
        """Update the AMail status indicator."""
        status = get_app_status("amail")
        if status:
            self.amail_status_label.setText("AMail: \U0001f7e2 Running")
            self.amail_status_label.setStyleSheet(
                "font-size: 13px; font-weight: bold; color: #0D652D;"
            )
        else:
            self.amail_status_label.setText("AMail: \U0001f534 Stopped")
            self.amail_status_label.setStyleSheet(
                "font-size: 13px; font-weight: bold; color: #B31412;"
            )

    def _poll_new_triage(self):
        """Check for new categorized emails from AMail."""
        results = pull_new_categorized_emails()
        if results:
            self.lbl_status.setText(
                f"{len(results)} new email(s) from AMail — click Refresh"
            )

    # ── Cleanup ─────────────────────────────────────────────────────────

    def closeEvent(self, event):
        from shared_tools.core.ipc_bridge import unregister_app

        self.amail_poll_timer.stop()
        self.triage_poll_timer.stop()

        unregister_app("acalendar")
        super().closeEvent(event)


# =============================================================================
# Entry Point
# =============================================================================

def show_calendar():
    """Launch the ACalendar GUI."""
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)

    window = CalendarWindow()
    window.show()
    app.exec()
