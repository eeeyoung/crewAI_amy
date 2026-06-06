#!/usr/bin/env python
import warnings

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")


def run():
    """Launch the ACalendar GUI dashboard."""
    from PyQt6.QtWidgets import QApplication
    import sys

    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)

    from shared_tools.ipc_bridge import register_app, init_shared_db
    register_app("acalendar")
    init_shared_db()

    from acalendar.gui_viewer import show_calendar
    show_calendar()


if __name__ == "__main__":
    run()
