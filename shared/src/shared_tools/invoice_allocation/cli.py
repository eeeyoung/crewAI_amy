"""Invoice Allocation CLI — allocate invoice PDFs to project subfolders.

Usage:
    uv run python -m shared_tools.invoice_allocation.cli --dir /path/to/invoices
    uv run python -m shared_tools.invoice_allocation.cli --dir /path/to/invoices --dry-run
    uv run python -m shared_tools.invoice_allocation.cli --dir /path/to/invoices --yes
    uv run python -m shared_tools.invoice_allocation.cli --history
    uv run python -m shared_tools.invoice_allocation.cli --history --limit 20
    uv run python -m shared_tools.invoice_allocation.cli --undo 3
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# ── .env support ───────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv

    _env_path = Path(__file__).resolve().parent.parent.parent.parent.parent / ".env"
    load_dotenv(_env_path)
except ImportError:
    pass


def _print_header(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def _print_record(r: dict) -> None:
    """Pretty-print a single allocation record."""
    status_icon = {
        "moved": "✅",
        "confirmed": "✔️",
        "pending_confirmation": "⏳",
        "no_match": "➖",
        "skipped": "⏭️",
        "undone": "↩️",
        "error": "❌",
    }.get(r.get("status", ""), "  ")

    method = r.get("match_method", "")
    confidence = r.get("confidence", 0)
    target = r.get("target_project_name") or r.get("target_project_code") or "-"
    filename = r.get("filename", "")
    created = r.get("created_at", "")

    print(
        f"  {status_icon} {created[:19]} | {filename[:50]:50s} "
        f"→ {target[:25]:25s} | {method:12s} ({confidence:.2f})"
    )


# =============================================================================
# cmd_allocate — main allocation command
# =============================================================================


def cmd_allocate(args: argparse.Namespace) -> None:
    """Run a full allocation on a folder."""
    folder = Path(args.dir)
    if not folder.exists():
        print(f"ERROR: Folder not found: {args.dir}")
        sys.exit(1)

    from shared_tools.invoice_allocation.invoice_allocation_service import (
        InvoiceAllocationService,
        _scan_project_folders,
    )

    service = InvoiceAllocationService()
    service.start()

    # ── Dry-run mode ─────────────────────────────────────────────────
    if args.dry_run:
        _print_header(f"DRY RUN — {folder}")
        print("Scanning folder structure...\n")

        projects, control_folders = _scan_project_folders(folder)

        print(f"Project folders ({len(projects)}):")
        for p in projects:
            print(f"  📁 [{p['code']}] {p['name']}")

        print(f"\nControl folders ({len(control_folders)}):")
        for c in control_folders:
            print(f"  📂 [{c['prefix']}] {c['name']}")

        # Analyze root-level PDFs
        pdfs = sorted(
            p for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() == ".pdf"
        )
        print(f"\nRoot-level invoice PDFs ({len(pdfs)}):")

        auto_moved = 0
        pending = 0
        no_match = 0

        for pdf in pdfs:
            fname_match = service._match_by_filename(pdf.name, projects)
            text = service._extract_pdf_text(pdf)
            text_match = service._match_by_text(text or "", projects) if text else None
            best = service._combine_matches(fname_match, text_match)

            if best and best["score"] >= service.AUTO_MOVE_THRESHOLD:
                icon = "✅"
                auto_moved += 1
                status_text = "AUTO-MOVE"
            elif best and best["score"] > 0:
                icon = "⏳"
                pending += 1
                status_text = "PENDING"
            else:
                icon = "➖"
                no_match += 1
                status_text = "NO MATCH"

            # Determine method for display
            if best is None:
                method = "none"
            elif fname_match and text_match and fname_match.get("code") == text_match.get("code"):
                method = "combined"
            elif fname_match and (not text_match or fname_match.get("score", 0) >= text_match.get("score", 0)):
                method = "filename"
            elif text_match:
                method = "text"
            else:
                method = "none"

            target = best.get("full_name", "-") if best else "-"
            score = best.get("score", 0) if best else 0

            print(f"  {icon} [{status_text}] {pdf.name}")
            print(f"     → {target} | {method} ({score:.2f})")
            if text:
                preview = text[:120].replace('\n', ' ').replace('\r', '')
                print(f"     text: {preview}...")
            print()

        print(f"Summary: {auto_moved} auto-move, {pending} pending, {no_match} no match")
        print("(No files moved — dry run)")
        return

    # ── Live allocation ──────────────────────────────────────────────
    _print_header(f"ALLOCATING — {folder}")
    result = service.allocate_folder_sync(str(folder))

    projects = _scan_project_folders(folder)[0]  # re-scan for project paths

    print(f"\nPhase 1 complete:")
    print(f"  Total:     {result['total']} files")
    print(f"  Auto-moved:{result['moved']}")
    print(f"  Pending:   {len(result['pending'])}")
    print(f"  No match:  {len(result['no_match'])}")
    print(f"  Failed:    {result['failed']}")

    # Show auto-moved files with destinations
    if result.get("moved_items"):
        print(f"\n  Auto-moved:")
        for item in result["moved_items"]:
            target = item.get("target_project", item.get("suggested_project_full_name", "?"))
            confidence = item.get("confidence", 0)
            method = item.get("match_method", "?")
            print(f"    ✅ {item['filename']} → {target} ({method} {confidence:.2f})")

    # Show no-match files
    if result["no_match"]:
        print(f"\n  No match (left in root):")
        for item in result["no_match"]:
            print(f"    ➖ {item['filename']}")

    # ── Phase 2: Interactive confirmation for pending files ──────────
    pending = result["pending"]
    if not pending:
        print("\n✅ All done — nothing needs your input.")
        service.stop()
        return

    print(f"\n{'─' * 60}")
    print(f"  Phase 2: {len(pending)} file(s) need your decision")
    print(f"{'─' * 60}")

    confirmed = 0
    declined = 0

    for i, item in enumerate(pending, 1):
        print(f"\n[{i}/{len(pending)}] {item['filename']}")
        print(f"  Suggested: {item['suggested_project_full_name']} "
              f"(confidence: {item['confidence']:.2f})")
        if item.get("llm_reasoning"):
            print(f"  Reasoning: {item['llm_reasoning']}")

        if args.yes:
            answer = "y"
            print("  --yes: auto-confirming...")
        else:
            try:
                answer = input("  Move to this project? [y/N]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n  Interrupted.")
                break

        if answer in ("y", "yes"):
            move_result = service.confirm_move(
                item["original_path"],
                item["suggested_project_code"],
                projects,
            )
            if move_result and move_result["status"] == "confirmed":
                print(f"  ✔️  Moved → {move_result['target_project']}")
                confirmed += 1
            elif move_result and move_result["status"] == "skipped":
                print(f"  ⏭️  {move_result['reason']}")
            else:
                print(f"  ❌ Could not move (file missing?)")
        else:
            print(f"  ➖ Left in root")
            declined += 1

    # ── Summary ──────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    total_moved = result["moved"] + confirmed
    print(f"  Done: {total_moved} moved "
          f"({result['moved']} auto, {confirmed} confirmed)")
    if declined:
        print(f"        {declined} left in root (declined)")
    if result["no_match"]:
        print(f"        {len(result['no_match'])} left in root (no match)")
    if result["failed"]:
        print(f"        {result['failed']} failed")
    print(f"{'=' * 60}")

    service.stop()


# =============================================================================
# cmd_history
# =============================================================================


def cmd_history(args: argparse.Namespace) -> None:
    """Show allocation history."""
    from shared_tools.invoice_allocation.invoice_allocation_service import (
        InvoiceAllocationService,
    )

    service = InvoiceAllocationService()
    service.start()

    records = service.get_allocation_history(args.limit)

    _print_header(f"ALLOCATION HISTORY ({len(records)} records)")

    if not records:
        print("  No allocation records found.")
        return

    # Summary by run
    run_ids = sorted({r.get("run_id") for r in records if r.get("run_id")}, reverse=True)
    for rid in run_ids[:3]:  # show last 3 runs
        run_recs = [r for r in records if r.get("run_id") == rid]
        moved = sum(1 for r in run_recs if r["status"] in ("moved", "confirmed"))
        pending = sum(1 for r in run_recs if r["status"] == "pending_confirmation")
        no_match = sum(1 for r in run_recs if r["status"] == "no_match")
        print(f"\n─ Run #{rid} ({run_recs[0].get('created_at', '')[:19]}) — "
              f"{moved} moved, {pending} pending, {no_match} no match")
        for r in run_recs:
            _print_record(r)

    service.stop()


# =============================================================================
# cmd_undo
# =============================================================================


def cmd_undo(args: argparse.Namespace) -> None:
    """Undo an allocation (move file back)."""
    from shared_tools.invoice_allocation.invoice_allocation_service import (
        InvoiceAllocationService,
    )

    service = InvoiceAllocationService()
    service.start()

    ok = service.undo_allocation(args.undo)
    if ok:
        print(f"↩️  Record {args.undo} undone — file moved back.")
    else:
        print(f"❌ Record {args.undo} not found or file missing.")

    service.stop()


# =============================================================================
# main
# =============================================================================


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Allocate invoice PDFs to project subfolders",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --dir /path/to/invoices\n"
            "  %(prog)s --dir /path/to/invoices --dry-run\n"
            "  %(prog)s --dir /path/to/invoices --yes\n"
            "  %(prog)s --history\n"
            "  %(prog)s --undo 3\n"
        ),
    )
    parser.add_argument(
        "--dir", "-d", type=str,
        help="Folder path containing invoices to allocate",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Scan and match but don't move any files",
    )
    parser.add_argument(
        "--yes", "-y", action="store_true",
        help="Auto-confirm all pending items (non-interactive)",
    )
    parser.add_argument(
        "--history", action="store_true",
        help="Show allocation history",
    )
    parser.add_argument(
        "--limit", type=int, default=50,
        help="Number of history records to show (default: 50)",
    )
    parser.add_argument(
        "--undo", type=int, metavar="RECORD_ID",
        help="Undo an allocation (move file back) by record ID",
    )

    args = parser.parse_args()

    if args.undo:
        cmd_undo(args)
    elif args.history:
        cmd_history(args)
    elif args.dir:
        cmd_allocate(args)
    else:
        parser.print_help()
        print("\nERROR: --dir, --history, or --undo is required.")


if __name__ == "__main__":
    main()
