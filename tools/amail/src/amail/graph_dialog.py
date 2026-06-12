"""GraphSignInDialog — thin PyQt6 UI for Microsoft Graph device-code auth.

This is the ONLY UI dependency of GraphService.  The dialog is a simple
wrapper that:
    1. Calls ``GraphService.authenticate()``
    2. Displays the device code and verification URL
    3. Shows a Copy Code button and a spinner
    4. Closes when ``auth_complete`` or ``auth_failed`` fires

To detach Graph integration from AMail, remove this file and the wiring
in ``mail_lister.py`` — GraphService itself has zero UI dependency.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QProgressBar, QApplication,
)
from PyQt6.QtCore import Qt, pyqtSignal


class GraphSignInDialog(QDialog):
    """Modal device-code sign-in dialog.

    Usage::

        dialog = GraphSignInDialog(graph_service, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # graph_service is now authenticated
            svc.enrich_async(emails)
    """

    sign_in_complete = pyqtSignal()
    sign_in_failed = pyqtSignal(str)

    def __init__(self, graph_service, parent=None):
        super().__init__(parent)
        self._service = graph_service
        self._code = ""
        self._build_ui()
        self._wire_signals()
        self.setWindowTitle("Microsoft Graph — Sign In")
        self.setMinimumWidth(480)
        self.setModal(True)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # ── Instructions ──────────────────────────────────────────
        self._label_info = QLabel(
            "To enrich emails with Focused/Other classification,\n"
            "sign in to your Microsoft account using the code below."
        )
        self._label_info.setWordWrap(True)
        layout.addWidget(self._label_info)

        # ── Device code ───────────────────────────────────────────
        code_layout = QHBoxLayout()
        self._code_display = QLineEdit()
        self._code_display.setReadOnly(True)
        self._code_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._code_display.setStyleSheet(
            "QLineEdit { font-size: 22px; font-weight: bold; letter-spacing: 6px; "
            "padding: 8px; background: #f0f0f0; border: 2px solid #0078d4; "
            "border-radius: 4px; }"
        )
        code_layout.addWidget(self._code_display)

        self._btn_copy = QPushButton("📋 Copy")
        self._btn_copy.setFixedWidth(80)
        self._btn_copy.clicked.connect(self._copy_code)
        code_layout.addWidget(self._btn_copy)
        layout.addLayout(code_layout)

        # ── Verification URL ──────────────────────────────────────
        url_layout = QHBoxLayout()
        self._url_label = QLabel()
        self._url_label.setWordWrap(True)
        self._url_label.setOpenExternalLinks(True)
        self._url_label.setStyleSheet("font-size: 11px; color: #555;")
        url_layout.addWidget(self._url_label)
        layout.addLayout(url_layout)

        # ── Spinner ───────────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminate
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        # ── Status ────────────────────────────────────────────────
        self._label_status = QLabel("Waiting for authentication...")
        self._label_status.setStyleSheet("color: #666; font-style: italic;")
        layout.addWidget(self._label_status)

        # ── Cancel ────────────────────────────────────────────────
        self._btn_cancel = QPushButton("Cancel")
        self._btn_cancel.clicked.connect(self._on_cancel)
        layout.addWidget(self._btn_cancel)

    def _wire_signals(self):
        self._service.auth_pending.connect(self._on_auth_pending)
        self._service.auth_complete.connect(self._on_auth_complete)
        self._service.auth_failed.connect(self._on_auth_failed)

    # ── Slots ─────────────────────────────────────────────────────

    def _on_auth_pending(self, device_code, verification_url, message):
        self._code = device_code
        self._code_display.setText(device_code)
        self._url_label.setText(
            f'<a href="{verification_url}">{verification_url}</a>  —  '
            f"enter the code above"
        )
        self._progress.setVisible(True)
        self._label_status.setText("Open the link and enter the code shown above...")

    def _on_auth_complete(self):
        self._label_status.setText("✓ Authenticated successfully!")
        self._progress.setVisible(False)
        self.sign_in_complete.emit()
        self.accept()

    def _on_auth_failed(self, error_message):
        self._label_status.setText(f"✗ {error_message}")
        self._progress.setVisible(False)
        self.sign_in_failed.emit(error_message)

    def _copy_code(self):
        if self._code:
            QApplication.clipboard().setText(self._code)
            self._label_status.setText("Code copied! Paste it on the sign-in page.")

    def _on_cancel(self):
        self._service.cancel_auth()
        self.reject()

    def showEvent(self, event):
        super().showEvent(event)
        # Start auth flow when dialog becomes visible
        self._service.authenticate()
