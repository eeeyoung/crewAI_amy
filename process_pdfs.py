"""Vision-process image-only PDFs using Gemini Flash.

Usage:
    uv run python process_pdfs.py --data-dir D:/Projects/ClientName --phase 1
    uv run python process_pdfs.py --data-dir D:/Projects/ClientName --phase 2
    uv run python process_pdfs.py --data-dir D:/Projects/ClientName --all
    uv run python process_pdfs.py --data-dir D:/Projects/ClientName --dry-run

Phase 1 = key docs (contracts, specs, permits, reports) — ~60 PDFs
Phase 2 = drawings (architectural, structural, MEP) — ~400 PDFs
--all   = every PDF — ~738 PDFs (1-2 hours)
"""

import os
import sys
from pathlib import Path

# .env support
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass


def main():
    args = sys.argv[1:]
    data_dir = None
    phase = None
    dry_run = False
    verbose = False

    while args:
        a = args.pop(0)
        if a == "--data-dir" and args:
            data_dir = args.pop(0)
        elif a == "--phase" and args:
            phase = args.pop(0)
        elif a == "--all":
            phase = "all"
        elif a == "--dry-run":
            dry_run = True
        elif a in ("--verbose", "-v"):
            verbose = True
        elif a in ("--help", "-h"):
            print(__doc__)
            return
        else:
            print(f"Unknown: {a}")
            return

    data_dir = data_dir or os.environ.get("LILAMY_DATA_DIR")
    if not data_dir:
        print("ERROR: set --data-dir or LILAMY_DATA_DIR")
        return

    if not phase:
        phase = "1"
        print("Defaulting to --phase 1 (key documents). Use --all for everything.\n")

    os.environ["LILAMY_DATA_DIR"] = str(data_dir)

    # PyQt6 signals need a QApplication event loop to deliver from background
    # threads.  Create a minimal one — used only for signal routing.
    from PyQt6.QtWidgets import QApplication
    import sys as _sys
    _app = QApplication.instance() or QApplication(_sys.argv)

    from shared_tools.pdf_vision.pdf_vision_service import (
        PDFVisionService, PHASE1_FOLDER_KEYWORDS, PHASE2_FOLDER_KEYWORDS
    )
    from shared_tools.memory.file_registry import FileRegistry

    reg = FileRegistry(data_dir)
    reg.init_db()

    # Determine what would be processed
    if phase == "1":
        keywords = PHASE1_FOLDER_KEYWORDS
    elif phase == "2":
        keywords = PHASE2_FOLDER_KEYWORDS
    else:
        keywords = None

    svc = PDFVisionService(data_root=data_dir)

    # Count
    if keywords:
        rows = svc._filter_by_keywords(reg, keywords)
    else:
        rows = svc._all_metadata_only_pdfs(reg)

    total_pages_est = 0
    for r in rows:
        fp = Path(data_dir) / r["rel_path"]
        if fp.exists():
            try:
                import fitz
                doc = fitz.open(str(fp))
                total_pages_est += min(doc.page_count, 15)
                doc.close()
            except Exception:
                total_pages_est += 1

    est_time = total_pages_est * 2.5 / 60  # ~2.5s per page (render + API + rate limit)

    print(f"Phase {phase}: {len(rows)} PDFs, ~{total_pages_est} pages")
    print(f"Estimated time: {est_time:.0f} minutes")
    print(f"API cost: ~${total_pages_est * 0.0001:.2f} (Gemini Flash free tier)")
    print()

    if dry_run:
        print("[dry-run] — showing first 15 PDFs that would be processed:")
        for r in rows[:15]:
            print(f"  {r['rel_path']} ({r['file_size_bytes']} bytes)")
        if len(rows) > 15:
            print(f"  ... and {len(rows) - 15} more")
        return

    # ── Progress state ───────────────────────────────────────────
    progress_state = {
        "total": len(rows),
        "done": 0,
        "pages": 0,
        "chunks": 0,
        "failed": 0,
        "current_file": "",
    }

    def _sep():
        print(f"  {'─'*60}")

    def _on_progress(msg: str):
        # Suppress internal "Removed X old chunks" noise unless verbose
        if "Removed" in msg and "--verbose" not in sys.argv:
            return
        print(f"  {msg}")

    def _on_page(file_id: int, page_num: int, text: str):
        # Always show first page preview; show all pages only in --verbose
        if page_num == 0 or verbose:
            preview = text[:120].replace('\n', ' ').strip()
            print(f"    p{page_num + 1}: {preview}...")
        elif page_num == 1 and not verbose:
            print(f"    ... (use --verbose for all page previews)")

    def _on_file(file_id: int, pages: int):
        ps = progress_state
        ps["done"] += 1
        ps["pages"] += pages
        pct = ps["done"] / ps["total"] * 100
        print(f"    ✓ [{ps['done']}/{ps['total']}] {ps['current_file']}"
              f" — {pages}p ({pct:.0f}%)")

        # Every 10 files, print a summary separator
        if ps["done"] % 10 == 0:
            _sep()
            print(f"  Summary: {ps['done']}/{ps['total']} files, "
                  f"{ps['pages']} pages, ~{ps['done'] * 2.5 / 60:.1f} min elapsed")
            _sep()

    def _on_error(file_id: int, err: str):
        ps = progress_state
        ps["failed"] += 1
        print(f"    ✗ [{ps['current_file']}]: {err}")

    def _on_all(files: int, pages: int):
        ps = progress_state
        _sep()
        print(f"  COMPLETE: {files} files, {pages} pages processed")
        print(f"  Failed: {ps['failed']}")
        print(f"  Cache: {data_dir}\\.lilamy_vision_cache\\")
        _sep()

    # ── Wire signals ──────────────────────────────────────────────
    svc.progress.connect(_on_progress)
    svc.page_complete.connect(_on_page)
    svc.file_complete.connect(_on_file)
    svc.file_error.connect(_on_error)
    svc.all_complete.connect(_on_all)

    # ── Run ───────────────────────────────────────────────────────
    print(f"Starting Phase {phase} ({progress_state['total']} PDFs)...")
    print()

    # Hook into the internal loop to track current filename
    _original_process_one = svc._process_one_pdf
    def _tracked_process_one(file_id, file_path, mem_svc, reg):
        progress_state["current_file"] = file_path.name
        return _original_process_one(file_id, file_path, mem_svc, reg)
    svc._process_one_pdf = _tracked_process_one

    if phase == "1":
        svc.process_phase1()
    elif phase == "2":
        svc.process_phase2()
    else:
        svc.process_all()

    # Wait for completion.  QApplication.processEvents() is required so
    # pyqtSignal emissions from the background thread are delivered to
    # the connected slots in this (main) thread.
    import time
    from PyQt6.QtWidgets import QApplication
    try:
        while svc._thread and svc._thread.is_alive():
            QApplication.processEvents()  # deliver pending signals
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nCancelling...")
        svc.cancel()
        svc._thread.join(timeout=10)
        print("Cancelled.")


if __name__ == "__main__":
    main()
