#!/usr/bin/env python
"""lilAmy — multi-agent construction admin platform.

Usage:
    uv run lilamy              # Desktop GUI (AMail card UI)
    uv run lilamy --web        # WebUI server (FastAPI backend)
    uv run lilamy --amail      # Launch legacy AMail directly
"""

import sys
import os
import warnings

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")


def run_desktop():
    """Launch the desktop GUI with the AMail card-based module."""
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)

    from lilamy.gui_viewer import LilAmyWindow
    window = LilAmyWindow()
    window.show()
    app.exec()


def run_web():
    """Launch the FastAPI WebUI backend."""
    try:
        import uvicorn
        from lilamy.web_server import app
        host = os.environ.get("LILAMY_HOST", "127.0.0.1")
        port = int(os.environ.get("LILAMY_PORT", "8765"))
        print(f"lilAmy WebUI → http://{host}:{port}")
        uvicorn.run(app, host=host, port=port)
    except ImportError as e:
        print(f"WebUI dependencies missing: {e}")
        print("Install with: uv add fastapi uvicorn")
        sys.exit(1)


def run():
    """Main entry point."""
    if "--web" in sys.argv:
        run_web()
    elif "--amail" in sys.argv:
        from amail.main import run as amail_run
        amail_run()
    else:
        run_desktop()


if __name__ == "__main__":
    run()
