"""Subcontractor Database Edit Tool.

Manual CRUD for cleaning up learner data from the terminal.

Usage:
  uv run python -m shared_tools.subcontractor.edit vendor delete "Asphalt Driveway"
  uv run python -m shared_tools.subcontractor.edit vendor reclassify "Bushy" supplier --confidence high
  uv run python -m shared_tools.subcontractor.edit vendor rename "Bushy" "Bushby Plumbing"
  uv run python -m shared_tools.subcontractor.edit vendor add-trade "Bushy" "Stormwater"
  uv run python -m shared_tools.subcontractor.edit commitment set-value S05 420000
  uv run python -m shared_tools.subcontractor.edit commitment delete PO16808
  uv run python -m shared_tools.subcontractor.edit benchmark delete --trade "Civil Works"
  uv run python -m shared_tools.subcontractor.edit clause delete --subcontract S05 --number 3
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path


def _connect():
    db_path = Path(__file__).resolve().parent.parent.parent.parent.parent / "data" / "subcontractor.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _find_vendor(conn, name: str) -> dict | None:
    """Find a vendor by exact or substring match on company_name."""
    row = conn.execute(
        "SELECT * FROM vendors WHERE company_name = ?", (name,)
    ).fetchone()
    if row:
        return dict(row)
    rows = conn.execute(
        "SELECT * FROM vendors WHERE company_name LIKE ?", (f"%{name}%",)
    ).fetchall()
    if len(rows) == 1:
        return dict(rows[0])
    if len(rows) > 1:
        print(f"  Multiple matches for '{name}':")
        for r in rows:
            print(f"    {r['company_name']} [{r['vendor_type']}]")
        return None
    print(f"  No vendor found matching '{name}'")
    return None


def _find_commitment(conn, ref: str) -> dict | None:
    """Find a commitment by reference number."""
    row = conn.execute(
        "SELECT * FROM commitments WHERE reference_number = ?", (ref,)
    ).fetchone()
    if row:
        return dict(row)
    print(f"  No commitment found with ref '{ref}'")
    return None


# ═══════════════════════════════════════════════════════════════════════
# Vendor commands
# ═══════════════════════════════════════════════════════════════════════

def vendor_delete(args) -> None:
    conn = _connect()
    v = _find_vendor(conn, args.name)
    if not v:
        conn.close()
        return
    print(f"  Deleting: {v['company_name']} [{v['vendor_type']}]")
    conn.execute("DELETE FROM vendors WHERE entry_id = ?", (v["entry_id"],))
    conn.commit()
    conn.close()
    print("  Done.")


def vendor_reclassify(args) -> None:
    conn = _connect()
    v = _find_vendor(conn, args.name)
    if not v:
        conn.close()
        return
    print(f"  Reclassifying: {v['company_name']} → {args.type}")
    conn.execute(
        "UPDATE vendors SET vendor_type = ?, vendor_type_confidence = ?, updated_at = datetime('now') "
        "WHERE entry_id = ?",
        (args.type, args.confidence or "manual", v["entry_id"]),
    )
    conn.commit()
    conn.close()
    print("  Done.")


def vendor_rename(args) -> None:
    conn = _connect()
    v = _find_vendor(conn, args.old_name)
    if not v:
        conn.close()
        return
    print(f"  Renaming: {v['company_name']} → {args.new_name}")
    conn.execute(
        "UPDATE vendors SET company_name = ?, updated_at = datetime('now') WHERE entry_id = ?",
        (args.new_name, v["entry_id"]),
    )
    conn.commit()
    conn.close()
    print("  Done.")


def vendor_add_trade(args) -> None:
    conn = _connect()
    v = _find_vendor(conn, args.name)
    if not v:
        conn.close()
        return
    trades = json.loads(v["trade_categories"] or "[]")
    if args.trade not in trades:
        trades.append(args.trade)
        print(f"  Adding trade '{args.trade}' to {v['company_name']}")
        conn.execute(
            "UPDATE vendors SET trade_categories = ?, updated_at = datetime('now') WHERE entry_id = ?",
            (json.dumps(trades), v["entry_id"]),
        )
        conn.commit()
    else:
        print(f"  Trade '{args.trade}' already present on {v['company_name']}")
    conn.close()
    print("  Done.")


def vendor_remove_trade(args) -> None:
    conn = _connect()
    v = _find_vendor(conn, args.name)
    if not v:
        conn.close()
        return
    trades = json.loads(v["trade_categories"] or "[]")
    if args.trade in trades:
        trades.remove(args.trade)
        print(f"  Removing trade '{args.trade}' from {v['company_name']}")
        conn.execute(
            "UPDATE vendors SET trade_categories = ?, updated_at = datetime('now') WHERE entry_id = ?",
            (json.dumps(trades), v["entry_id"]),
        )
        conn.commit()
    else:
        print(f"  Trade '{args.trade}' not found on {v['company_name']}")
    conn.close()
    print("  Done.")


def vendor_show(args) -> None:
    conn = _connect()
    v = _find_vendor(conn, args.name)
    if not v:
        conn.close()
        return
    print(f"\n  Company:       {v['company_name']}")
    print(f"  Type:          {v['vendor_type']} ({v['vendor_type_confidence']})")
    print(f"  Trades:        {v['trade_categories']}")
    print(f"  ABN:           {v['abn'] or '-'}")
    print(f"  Contact:       {v['contact_name'] or '-'}  {v['contact_email'] or ''}  {v['contact_phone'] or ''}")
    print(f"  Address:       {v['address'] or '-'}")
    print(f"  Status:        {v['status']}")
    print(f"  Source:        {v['source']}")
    print()

    # Show linked commitments
    commits = conn.execute(
        "SELECT * FROM commitments WHERE vendor_entry_id = ?", (v["entry_id"],)
    ).fetchall()
    if commits:
        print(f"  Commitments ({len(commits)}):")
        for c in commits:
            print(f"    {c['reference_number']:10s}  {c['commitment_type']:15s}  {c['status']:10s}  ${c['commitment_value']:,.2f}")

    quotes = conn.execute(
        "SELECT * FROM quotes WHERE vendor_entry_id = ?", (v["entry_id"],)
    ).fetchall()
    if quotes:
        print(f"\n  Quotes ({len(quotes)}):")
        for q in quotes:
            status = "AWARDED" if q["is_awarded"] else "sample"
            print(f"    {q['quote_ref'] or 'unnamed':20s}  {q['trade_name']:20s}  {status:8s}  ${q['total_amount']:,.2f}")

    conn.close()
    print()


# ═══════════════════════════════════════════════════════════════════════
# Commitment commands
# ═══════════════════════════════════════════════════════════════════════

def commitment_set_value(args) -> None:
    conn = _connect()
    c = _find_commitment(conn, args.ref)
    if not c:
        conn.close()
        return
    print(f"  {c['reference_number']}: ${c['commitment_value']:,.2f} → ${args.value:,.2f}")
    conn.execute(
        "UPDATE commitments SET commitment_value = ?, updated_at = datetime('now') WHERE entry_id = ?",
        (args.value, c["entry_id"]),
    )
    conn.commit()
    conn.close()
    print("  Done.")


def commitment_set_status(args) -> None:
    conn = _connect()
    c = _find_commitment(conn, args.ref)
    if not c:
        conn.close()
        return
    print(f"  {c['reference_number']}: {c['status']} → {args.status}")
    conn.execute(
        "UPDATE commitments SET status = ?, updated_at = datetime('now') WHERE entry_id = ?",
        (args.status, c["entry_id"]),
    )
    conn.commit()
    conn.close()
    print("  Done.")


def commitment_delete(args) -> None:
    conn = _connect()
    c = _find_commitment(conn, args.ref)
    if not c:
        conn.close()
        return
    print(f"  Deleting: {c['reference_number']} — {c['title']}")
    conn.execute("DELETE FROM commitment_items WHERE commitment_entry_id = ?", (c["entry_id"],))
    conn.execute("DELETE FROM commitments WHERE entry_id = ?", (c["entry_id"],))
    conn.commit()
    conn.close()
    print("  Done.")


# ═══════════════════════════════════════════════════════════════════════
# Quote commands
# ═══════════════════════════════════════════════════════════════════════

def quote_delete(args) -> None:
    conn = _connect()
    # Find by vendor name
    row = conn.execute(
        "SELECT q.*, v.company_name FROM quotes q LEFT JOIN vendors v ON q.vendor_entry_id = v.entry_id "
        "WHERE q.entry_id = ? OR v.company_name LIKE ?",
        (args.id, f"%{args.id}%"),
    ).fetchone()
    if not row:
        print(f"  No quote found matching '{args.id}'")
        conn.close()
        return
    print(f"  Deleting: {row['company_name']} — {row['trade_name']} ${row['total_amount']:,.2f}")
    conn.execute("DELETE FROM quote_items WHERE quote_entry_id = ?", (row["entry_id"],))
    conn.execute("DELETE FROM quotes WHERE entry_id = ?", (row["entry_id"],))
    conn.commit()
    conn.close()
    print("  Done.")


# ═══════════════════════════════════════════════════════════════════════
# Knowledge base commands
# ═══════════════════════════════════════════════════════════════════════

def benchmark_delete(args) -> None:
    conn = _connect()
    if args.trade:
        conn.execute("DELETE FROM rate_benchmarks WHERE trade_name = ?", (args.trade,))
        conn.commit()
        print(f"  Deleted all benchmarks for trade '{args.trade}'")
    else:
        print("  Use --trade to specify which trade's benchmarks to delete")
    conn.close()


def clause_delete(args) -> None:
    conn = _connect()
    if args.subcontract and args.number:
        conn.execute(
            "DELETE FROM clause_library WHERE source_commitment_ref = ? AND clause_number = ?",
            (args.subcontract, args.number),
        )
        conn.commit()
        print(f"  Deleted clause #{args.number} from {args.subcontract}")
    elif args.subcontract:
        conn.execute(
            "DELETE FROM clause_library WHERE source_commitment_ref = ?",
            (args.subcontract,),
        )
        conn.commit()
        print(f"  Deleted all clauses from {args.subcontract}")
    else:
        print("  Use --subcontract and optionally --number")
    conn.close()


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Edit the Subcontractor learned database",
        prog="subcontractor-edit",
    )
    sub = parser.add_subparsers(dest="entity", required=True)

    # ── vendor ────────────────────────────────────────────────────────
    p = sub.add_parser("vendor", help="Edit vendors")
    vsub = p.add_subparsers(dest="action", required=True)

    sp = vsub.add_parser("delete", help="Remove a vendor")
    sp.add_argument("name")
    sp.set_defaults(func=vendor_delete)

    sp = vsub.add_parser("reclassify", help="Change vendor_type")
    sp.add_argument("name")
    sp.add_argument("type", choices=["subcontractor", "supplier"])
    sp.add_argument("--confidence", choices=["high", "medium", "low"])
    sp.set_defaults(func=vendor_reclassify)

    sp = vsub.add_parser("rename", help="Rename a vendor")
    sp.add_argument("old_name")
    sp.add_argument("new_name")
    sp.set_defaults(func=vendor_rename)

    sp = vsub.add_parser("add-trade", help="Add a trade to a vendor")
    sp.add_argument("name")
    sp.add_argument("trade")
    sp.set_defaults(func=vendor_add_trade)

    sp = vsub.add_parser("remove-trade", help="Remove a trade from a vendor")
    sp.add_argument("name")
    sp.add_argument("trade")
    sp.set_defaults(func=vendor_remove_trade)

    sp = vsub.add_parser("show", help="Show vendor details")
    sp.add_argument("name")
    sp.set_defaults(func=vendor_show)

    # ── commitment ────────────────────────────────────────────────────
    p = sub.add_parser("commitment", help="Edit commitments")
    csub = p.add_subparsers(dest="action", required=True)

    sp = csub.add_parser("set-value", help="Set commitment value")
    sp.add_argument("ref")
    sp.add_argument("value", type=float)
    sp.set_defaults(func=commitment_set_value)

    sp = csub.add_parser("set-status", help="Set commitment status")
    sp.add_argument("ref")
    sp.add_argument("status")
    sp.set_defaults(func=commitment_set_status)

    sp = csub.add_parser("delete", help="Delete a commitment")
    sp.add_argument("ref")
    sp.set_defaults(func=commitment_delete)

    # ── quote ─────────────────────────────────────────────────────────
    p = sub.add_parser("quote", help="Edit quotes")
    qsub = p.add_subparsers(dest="action", required=True)

    sp = qsub.add_parser("delete", help="Delete a quote by vendor name or entry_id")
    sp.add_argument("id")
    sp.set_defaults(func=quote_delete)

    # ── benchmark ─────────────────────────────────────────────────────
    p = sub.add_parser("benchmark", help="Edit rate benchmarks")
    bsub = p.add_subparsers(dest="action", required=True)

    sp = bsub.add_parser("delete", help="Delete benchmarks for a trade")
    sp.add_argument("--trade", required=True)
    sp.set_defaults(func=benchmark_delete)

    # ── clause ────────────────────────────────────────────────────────
    p = sub.add_parser("clause", help="Edit clause library")
    clsub = p.add_subparsers(dest="action", required=True)

    sp = clsub.add_parser("delete", help="Delete clauses")
    sp.add_argument("--subcontract", required=True)
    sp.add_argument("--number")
    sp.set_defaults(func=clause_delete)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
