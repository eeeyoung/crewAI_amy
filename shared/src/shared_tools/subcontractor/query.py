"""Subcontractor Database Query Tool.

Query the learned subcontractor database from the terminal.

Usage:
  uv run python -m shared_tools.subcontractor.query vendors
  uv run python -m shared_tools.subcontractor.query vendors --type subcontractor
  uv run python -m shared_tools.subcontractor.query commitments --type subcontract
  uv run python -m shared_tools.subcontractor.query benchmarks --trade Hydraulics
  uv run python -m shared_tools.subcontractor.query clauses --compare
  uv run python -m shared_tools.subcontractor.query summary
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def _connect():
    """Connect to the subcontractor database."""
    db_path = Path(__file__).resolve().parent.parent.parent.parent.parent / "data" / "subcontractor.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


# ═══════════════════════════════════════════════════════════════════════
# Output helpers
# ═══════════════════════════════════════════════════════════════════════

def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    """Print a formatted table."""
    if not rows:
        print("  (no results)")
        return
    # Compute column widths
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(str(cell)))
    # Header
    sep = "  " + "  ".join("─" * (w + 2) for w in widths)
    header_line = "  " + "  ".join(f" {headers[i]:{widths[i]}} " for i in range(len(headers)))
    print(header_line)
    print(sep)
    # Rows
    for row in rows:
        line = "  " + "  ".join(f" {str(row[i]):{widths[i]}} " for i in range(min(len(row), len(widths))))
        print(line)
    print()


def _fmt_money(v: float) -> str:
    if v == 0:
        return "$0"
    if v >= 1_000_000:
        return f"${v/1_000_000:,.2f}M"
    if v >= 1_000:
        return f"${v:,.0f}"
    return f"${v:,.2f}"


# ═══════════════════════════════════════════════════════════════════════
# Commands
# ═══════════════════════════════════════════════════════════════════════

def cmd_vendors(args) -> None:
    conn = _connect()
    query = "SELECT * FROM vendors WHERE 1=1"
    params: list = []
    if args.type:
        query += " AND vendor_type = ?"
        params.append(args.type)
    if args.trade:
        query += " AND trade_categories LIKE ?"
        params.append(f"%{args.trade}%")
    if args.confidence:
        query += " AND vendor_type_confidence = ?"
        params.append(args.confidence)
    query += " ORDER BY vendor_type, company_name"
    rows = conn.execute(query, params).fetchall()
    conn.close()

    sub_rows = [r for r in rows if r["vendor_type"] == "subcontractor"]
    sup_rows = [r for r in rows if r["vendor_type"] == "supplier"]

    if sub_rows:
        print(f"\n=== SUBCONTRACTORS ({len(sub_rows)}) ===\n")
        _print_table(
            ["COMPANY", "CONF", "TRADES"],
            [[r["company_name"], r["vendor_type_confidence"],
              json.dumps(json.loads(r["trade_categories"])) if r["trade_categories"] else "[]"]
             for r in sub_rows],
        )

    if sup_rows:
        print(f"=== SUPPLIERS ({len(sup_rows)}) ===\n")
        _print_table(
            ["COMPANY", "CONF", "TRADES"],
            [[r["company_name"], r["vendor_type_confidence"],
              json.dumps(json.loads(r["trade_categories"])) if r["trade_categories"] else "[]"]
             for r in sup_rows],
        )

    print(f"Total: {len(rows)} vendors ({len(sub_rows)} subcontractors, {len(sup_rows)} suppliers)")


def cmd_commitments(args) -> None:
    conn = _connect()
    query = """SELECT c.reference_number, c.commitment_type, c.title,
                      c.commitment_value, c.status, v.company_name as vendor_name
               FROM commitments c
               LEFT JOIN vendors v ON c.vendor_entry_id = v.entry_id
               WHERE 1=1"""
    params: list = []
    if args.type == "po":
        query += " AND c.commitment_type = 'purchase_order'"
    elif args.type == "subcontract":
        query += " AND c.commitment_type = 'subcontract'"
    if args.vendor:
        query += " AND v.company_name LIKE ?"
        params.append(f"%{args.vendor}%")
    if args.project:
        query += " AND c.project_entry_id = ?"
        params.append(args.project)
    query += " ORDER BY c.commitment_type, c.reference_number"
    rows = conn.execute(query, params).fetchall()
    conn.close()

    po_rows = [r for r in rows if r["commitment_type"] == "purchase_order"]
    sub_rows = [r for r in rows if r["commitment_type"] == "subcontract"]

    if po_rows:
        print(f"\n=== PURCHASE ORDERS ({len(po_rows)}) ===\n")
        _print_table(
            ["REF", "VENDOR", "VALUE", "TITLE", "STATUS"],
            [[r["reference_number"], r["vendor_name"] or "?",
              _fmt_money(r["commitment_value"] or 0), r["title"][:50], r["status"]]
             for r in po_rows],
        )

    if sub_rows:
        print(f"=== SUBCONTRACTS ({len(sub_rows)}) ===\n")
        _print_table(
            ["REF", "VENDOR", "VALUE", "TITLE", "STATUS"],
            [[r["reference_number"], r["vendor_name"] or "?",
              _fmt_money(r["commitment_value"] or 0), r["title"][:50], r["status"]]
             for r in sub_rows],
        )

    print(f"Total: {len(rows)} commitments ({len(po_rows)} POs, {len(sub_rows)} Subcontracts)")


def cmd_quotes(args) -> None:
    conn = _connect()
    query = """SELECT q.trade_name, q.total_amount, q.is_awarded, q.quote_ref,
                      q.date_submitted, v.company_name as vendor_name,
                      (SELECT COUNT(*) FROM quote_items qi WHERE qi.quote_entry_id = q.entry_id) as item_count
               FROM quotes q
               LEFT JOIN vendors v ON q.vendor_entry_id = v.entry_id
               WHERE 1=1"""
    params: list = []
    if args.awarded:
        query += " AND q.is_awarded = 1"
    if args.trade:
        query += " AND q.trade_name = ?"
        params.append(args.trade)
    if args.vendor:
        query += " AND v.company_name LIKE ?"
        params.append(f"%{args.vendor}%")
    query += " ORDER BY q.is_awarded DESC, q.total_amount DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()

    awarded = [r for r in rows if r["is_awarded"]]
    samples = [r for r in rows if not r["is_awarded"]]

    if awarded:
        print(f"\n=== AWARDED QUOTES ({len(awarded)}) ===\n")
        _print_table(
            ["VENDOR", "TRADE", "VALUE", "ITEMS", "REF"],
            [[r["vendor_name"] or "?", r["trade_name"], _fmt_money(r["total_amount"] or 0),
              str(r["item_count"]), r["quote_ref"] or ""]
             for r in awarded],
        )

    if samples:
        print(f"=== UNAWARDED SAMPLES ({len(samples)}) ===\n")
        _print_table(
            ["VENDOR", "TRADE", "VALUE", "ITEMS", "REF"],
            [[r["vendor_name"] or "?", r["trade_name"], _fmt_money(r["total_amount"] or 0),
              str(r["item_count"]), r["quote_ref"] or ""]
             for r in samples],
        )

    # Show line items if --items flag
    if args.items and rows:
        print("=== LINE ITEMS ===\n")
        for r in rows:
            items = conn.execute(
                "SELECT * FROM quote_items WHERE quote_entry_id = ? ORDER BY sort_order",
                (r["entry_id"],)
            ).fetchall() if hasattr(r, "entry_id") else []
        conn.close()
        return

    total_value = sum(r["total_amount"] or 0 for r in rows)
    print(f"Total: {len(rows)} quotes ({len(awarded)} awarded, {len(samples)} samples), {_fmt_money(total_value)} total")


def cmd_benchmarks(args) -> None:
    conn = _get_conn()
    query = "SELECT * FROM rate_benchmarks WHERE 1=1"
    params: list = []
    if args.trade:
        query += " AND trade_name = ?"
        params.append(args.trade)
    query += " ORDER BY trade_name, sample_count DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()

    if args.compare:
        # Show one row per trade (highest sample_count)
        seen = set()
        top = []
        for r in rows:
            if r["trade_name"] not in seen:
                top.append(r)
                seen.add(r["trade_name"])
        rows = top

    print(f"\n=== RATE BENCHMARKS ({len(rows)} entries) ===\n")
    _print_table(
        ["TRADE", "SCOPE", "UNIT", "AVG", "MIN", "MAX", "N"],
        [[r["trade_name"], r["scope_keyword"][:40], r["unit"],
          _fmt_money(r["avg_rate"]), _fmt_money(r["min_rate"]),
          _fmt_money(r["max_rate"]), str(r["sample_count"])]
         for r in rows],
    )


def cmd_clauses(args) -> None:
    conn = _get_conn()
    if args.compare:
        # Show clauses that differ across subcontracts
        query = """SELECT clause_number, clause_title,
                          COUNT(DISTINCT source_commitment_ref) as sub_count,
                          GROUP_CONCAT(DISTINCT source_commitment_ref) as subs
                   FROM clause_library
                   GROUP BY clause_number
                   HAVING sub_count >= 2
                   ORDER BY CAST(clause_number AS INTEGER)"""
        rows = conn.execute(query).fetchall()
        conn.close()
        print(f"\n=== CLAUSE COMPARISON ({len(rows)} clauses appear in 2+ subcontracts) ===\n")
        _print_table(
            ["#", "TITLE", "SUBS", "APPEARS IN"],
            [[r["clause_number"], r["clause_title"][:50],
              str(r["sub_count"]), r["subs"]]
             for r in rows],
        )
    elif args.subcontract:
        query = """SELECT * FROM clause_library WHERE source_commitment_ref = ?
                   ORDER BY CAST(clause_number AS INTEGER)"""
        rows = conn.execute(query, (args.subcontract,)).fetchall()
        conn.close()
        print(f"\n=== CLAUSES — {args.subcontract} ({len(rows)} clauses) ===\n")
        for r in rows:
            print(f"  {r['clause_number']}. {r['clause_title']}")
            print(f"     {r['clause_text'][:200]}...")
            print()
    elif args.number:
        query = """SELECT cl.*, COUNT(*) OVER (PARTITION BY cl.clause_number) as dup_count
                   FROM clause_library cl WHERE cl.clause_number = ?
                   ORDER BY cl.source_commitment_ref"""
        rows = conn.execute(query, (args.number,)).fetchall()
        conn.close()
        print(f"\n=== CLAUSE #{args.number} ({len(rows)} versions) ===\n")
        for r in rows:
            print(f"  [{r['source_commitment_ref']}] {r['clause_title']}")
            print(f"  {r['clause_text'][:300]}")
            print()
    else:
        # Summary
        query = """SELECT source_commitment_ref, COUNT(*) as cnt,
                          MIN(CAST(clause_number AS INTEGER)) as min_c,
                          MAX(CAST(clause_number AS INTEGER)) as max_c
                   FROM clause_library GROUP BY source_commitment_ref
                   ORDER BY source_commitment_ref"""
        rows = conn.execute(query).fetchall()
        conn.close()
        print(f"\n=== CLAUSE LIBRARY ({len(rows)} subcontracts) ===\n")
        _print_table(
            ["SUBCONTRACT", "CLAUSES", "RANGE"],
            [[r["source_commitment_ref"], str(r["cnt"]),
              f"#{r['min_c']}–#{r['max_c']}"]
             for r in rows],
        )


def cmd_competitive(args) -> None:
    conn = _get_conn()
    query = "SELECT * FROM competitive_sets WHERE 1=1"
    params: list = []
    if args.trade:
        query += " AND trade_name = ?"
        params.append(args.trade)
    query += " ORDER BY quote_count DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()

    print(f"\n=== COMPETITIVE SETS ({len(rows)} trades) ===\n")
    table_rows = []
    for r in rows:
        vendor_ids = json.loads(r["vendor_entry_ids"]) if r["vendor_entry_ids"] else []
        vendor_names = []
        for vid in vendor_ids:
            vconn = _get_conn()
            v = vconn.execute("SELECT company_name FROM vendors WHERE entry_id = ?", (vid,)).fetchone()
            vconn.close()
            vendor_names.append(v["company_name"] if v else vid[:8])
        table_rows.append([
            r["trade_name"], str(r["quote_count"]),
            ", ".join(vendor_names)[:80],
            "★" if r["awarded_vendor_entry_id"] else "",
        ])

    _print_table(
        ["TRADE", "BIDDERS", "VENDORS", "AWARDED"],
        table_rows,
    )


def cmd_summary(args) -> None:
    conn = _get_conn()
    print()
    print("=" * 62)
    print("  SUBCONTRACTOR DATABASE — LEARNED KNOWLEDGE SUMMARY")
    print("=" * 62)
    print()

    # Vendors
    v_total = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]
    v_sub = conn.execute("SELECT COUNT(*) FROM vendors WHERE vendor_type='subcontractor'").fetchone()[0]
    v_sup = conn.execute("SELECT COUNT(*) FROM vendors WHERE vendor_type='supplier'").fetchone()[0]
    print(f"  Vendors:           {v_total} ({v_sub} subcontractors, {v_sup} suppliers)")

    # Quotes
    q_total = conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
    q_awarded = conn.execute("SELECT COUNT(*) FROM quotes WHERE is_awarded=1").fetchone()[0]
    qi_total = conn.execute("SELECT COUNT(*) FROM quote_items").fetchone()[0]
    print(f"  Quotes:            {q_total} ({q_awarded} awarded) with {qi_total} line items")

    # Commitments
    c_po = conn.execute("SELECT COUNT(*) FROM commitments WHERE commitment_type='purchase_order'").fetchone()[0]
    c_sub = conn.execute("SELECT COUNT(*) FROM commitments WHERE commitment_type='subcontract'").fetchone()[0]
    print(f"  Commitments:       {c_po} POs + {c_sub} Subcontracts")

    # Knowledge base
    rb = conn.execute("SELECT COUNT(*) FROM rate_benchmarks").fetchone()[0]
    rb_trades = conn.execute("SELECT COUNT(DISTINCT trade_name) FROM rate_benchmarks").fetchone()[0]
    print(f"  Rate benchmarks:   {rb} entries across {rb_trades} trades")

    cl = conn.execute("SELECT COUNT(*) FROM clause_library").fetchone()[0]
    cl_subs = conn.execute("SELECT COUNT(DISTINCT source_commitment_ref) FROM clause_library").fetchone()[0]
    print(f"  Clause library:    {cl} clauses from {cl_subs} subcontracts")

    cs = conn.execute("SELECT COUNT(*) FROM competitive_sets").fetchone()[0]
    print(f"  Competitive sets:  {cs} trades with 2+ bidders")

    # Top trades by vendor count
    print()
    print("  Top trades by vendor count:")
    for r in conn.execute("""
        SELECT trade_name, quote_count FROM competitive_sets
        ORDER BY quote_count DESC LIMIT 5
    """).fetchall():
        print(f"    {r['trade_name']:30s} {r['quote_count']} bidders")

    # Top subcontractors by value
    print()
    print("  Top commitments by value:")
    for r in conn.execute("""
        SELECT c.reference_number, c.commitment_value, v.company_name
        FROM commitments c LEFT JOIN vendors v ON c.vendor_entry_id = v.entry_id
        ORDER BY c.commitment_value DESC LIMIT 5
    """).fetchall():
        print(f"    {r['reference_number']:10s} {r['company_name'] or '?':30s} {_fmt_money(r['commitment_value'] or 0)}")

    conn.close()
    print()
    print("=" * 62)


def _get_conn():
    """Get a connection with all safety pragmas."""
    db_path = Path(__file__).resolve().parent.parent.parent.parent.parent / "data" / "subcontractor.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Query the Subcontractor learned database",
        prog="subcontractor-query",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── vendors ───────────────────────────────────────────────────────
    p = sub.add_parser("vendors", help="List all vendors")
    p.add_argument("--type", choices=["subcontractor", "supplier"])
    p.add_argument("--trade")
    p.add_argument("--confidence", choices=["high", "medium", "low"])
    p.set_defaults(func=cmd_vendors)

    # ── commitments ───────────────────────────────────────────────────
    p = sub.add_parser("commitments", help="List POs and Subcontracts")
    p.add_argument("--type", dest="type", choices=["po", "subcontract"])
    p.add_argument("--vendor")
    p.add_argument("--project")
    p.set_defaults(func=cmd_commitments)

    # ── quotes ────────────────────────────────────────────────────────
    p = sub.add_parser("quotes", help="List AI-extracted quotes")
    p.add_argument("--awarded", action="store_true")
    p.add_argument("--trade")
    p.add_argument("--vendor")
    p.add_argument("--items", action="store_true", help="Show line items")
    p.set_defaults(func=cmd_quotes)

    # ── benchmarks ────────────────────────────────────────────────────
    p = sub.add_parser("benchmarks", help="Rate benchmark intelligence")
    p.add_argument("--trade")
    p.add_argument("--compare", action="store_true", help="One row per trade")
    p.set_defaults(func=cmd_benchmarks)

    # ── clauses ───────────────────────────────────────────────────────
    p = sub.add_parser("clauses", help="Subcontract clause library")
    p.add_argument("--subcontract", help="e.g., S05")
    p.add_argument("--number", help="Clause number to compare across subs")
    p.add_argument("--compare", action="store_true", help="Show clauses shared across subs")
    p.set_defaults(func=cmd_clauses)

    # ── competitive ───────────────────────────────────────────────────
    p = sub.add_parser("competitive", help="Competitive bidding sets")
    p.add_argument("--trade")
    p.set_defaults(func=cmd_competitive)

    # ── summary ───────────────────────────────────────────────────────
    p = sub.add_parser("summary", help="One-page overview")
    p.set_defaults(func=cmd_summary)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
