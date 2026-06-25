"""Dedicated database for the Progress Claim module.

All progress-claim data lives in <LILAMY_DATA_DIR>/progress_claims.db —
completely separate from mail_history.db and variations.db.

Tables:
    projects              — project header (name, client, job#, contract amount, cashflow_path)
    cashflow_work_items   — one row per cashflow line item (section, description, cost)
    cashflow_months       — month columns detected from the cashflow header
    cashflow_progress     — per-item per-month % complete + computed amount
    progress_claims       — generated claim header (number, month, totals, retention)
    progress_claim_items  — per-claim per-item detail (cumulative %, claimed, balance)
"""
import os
import sqlite3
import uuid
from pathlib import Path


# =============================================================================
# Path / connection helpers
# =============================================================================


def _resolve_data_dir() -> Path:
    if "LILAMY_DATA_DIR" in os.environ:
        return Path(os.environ["LILAMY_DATA_DIR"])
    project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
    return project_root / "data"


DATA_DIR = _resolve_data_dir()
DB_PATH = DATA_DIR / "progress_claims.db"


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migrate_add_column(conn, table: str, column: str, col_type: str) -> bool:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        conn.commit()
        return True
    return False


def _new_entry_id() -> str:
    return str(uuid.uuid4())


# =============================================================================
# Init + Migration
# =============================================================================


def init_progress_claim_db() -> None:
    """Create all tables if they don't exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = _get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                job_number TEXT DEFAULT '',
                location TEXT DEFAULT '',
                client TEXT DEFAULT '',
                client_contact TEXT DEFAULT '',
                superintendent TEXT DEFAULT '',
                company_name TEXT DEFAULT 'Welink Construction',
                company_abn TEXT DEFAULT '',
                company_address TEXT DEFAULT '',
                base_contract_amount REAL DEFAULT 0,
                cashflow_path TEXT,
                source_type TEXT DEFAULT 'new',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_pc_projects_name
                ON projects(name);

            CREATE TABLE IF NOT EXISTS cashflow_work_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_entry_id TEXT NOT NULL REFERENCES projects(entry_id),
                section TEXT NOT NULL,
                section_label TEXT DEFAULT '',
                item_number INTEGER DEFAULT 0,
                description TEXT NOT NULL DEFAULT '',
                cost REAL DEFAULT 0,
                sort_order INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_pc_work_items_project
                ON cashflow_work_items(project_entry_id);
            CREATE INDEX IF NOT EXISTS idx_pc_work_items_section
                ON cashflow_work_items(project_entry_id, section);

            CREATE TABLE IF NOT EXISTS cashflow_sections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_entry_id TEXT NOT NULL REFERENCES projects(entry_id),
                section_code TEXT NOT NULL,
                section_label TEXT NOT NULL DEFAULT '',
                claimable INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(project_entry_id, section_code)
            );

            CREATE INDEX IF NOT EXISTS idx_pc_sections_project
                ON cashflow_sections(project_entry_id);

            CREATE TABLE IF NOT EXISTS cashflow_months (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_entry_id TEXT NOT NULL REFERENCES projects(entry_id),
                month_key TEXT NOT NULL,
                month_label TEXT NOT NULL,
                month_index INTEGER NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(project_entry_id, month_key)
            );

            CREATE INDEX IF NOT EXISTS idx_pc_months_project
                ON cashflow_months(project_entry_id);

            CREATE TABLE IF NOT EXISTS cashflow_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                work_item_id INTEGER NOT NULL REFERENCES cashflow_work_items(id),
                month_id INTEGER NOT NULL REFERENCES cashflow_months(id),
                percentage REAL DEFAULT 0,
                amount REAL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(work_item_id, month_id)
            );

            CREATE INDEX IF NOT EXISTS idx_pc_progress_item
                ON cashflow_progress(work_item_id);
            CREATE INDEX IF NOT EXISTS idx_pc_progress_month
                ON cashflow_progress(month_id);

            CREATE TABLE IF NOT EXISTS progress_claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id TEXT UNIQUE NOT NULL,
                project_entry_id TEXT NOT NULL REFERENCES projects(entry_id),
                claim_number INTEGER NOT NULL,
                claim_month TEXT NOT NULL,
                claim_date TEXT,
                rev_number INTEGER DEFAULT 1,
                status TEXT DEFAULT 'draft',
                retention_percentage REAL DEFAULT 10,
                retention_max_percentage REAL DEFAULT 5,
                gross_claim REAL DEFAULT 0,
                less_previous_claims REAL DEFAULT 0,
                retention_amount REAL DEFAULT 0,
                total_retention_held REAL DEFAULT 0,
                net_claim REAL DEFAULT 0,
                gst_amount REAL DEFAULT 0,
                total_including_gst REAL DEFAULT 0,
                section_a_total REAL DEFAULT 0,
                section_b_total REAL DEFAULT 0,
                section_c_total REAL DEFAULT 0,
                section_d_total REAL DEFAULT 0,
                section_e_total REAL DEFAULT 0,
                cumulative_claimed REAL DEFAULT 0,
                excel_path TEXT,
                pdf_path TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(project_entry_id, claim_number)
            );

            CREATE INDEX IF NOT EXISTS idx_pc_claims_project
                ON progress_claims(project_entry_id);
            CREATE INDEX IF NOT EXISTS idx_pc_claims_month
                ON progress_claims(project_entry_id, claim_month);

            CREATE TABLE IF NOT EXISTS progress_claim_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_entry_id TEXT NOT NULL REFERENCES progress_claims(entry_id),
                work_item_id INTEGER,
                section TEXT NOT NULL,
                item_number INTEGER NOT NULL DEFAULT 0,
                description TEXT NOT NULL DEFAULT '',
                cost REAL DEFAULT 0,
                cumulative_percentage REAL DEFAULT 0,
                total_claimed REAL DEFAULT 0,
                previously_claimed REAL DEFAULT 0,
                current_claim REAL DEFAULT 0,
                balance_remaining REAL DEFAULT 0,
                sort_order INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_pc_claim_items_claim
                ON progress_claim_items(claim_entry_id);
        """)
        conn.commit()

        # Migrations for new columns
        _migrate_add_column(conn, "projects", "site_address", "TEXT DEFAULT ''")
        _migrate_add_column(conn, "progress_claims", "total_retention_held", "REAL DEFAULT 0")
        _migrate_add_column(conn, "progress_claims", "section_totals_json", "TEXT DEFAULT ''")
    finally:
        conn.close()


# =============================================================================
# Projects
# =============================================================================


def upsert_project(data: dict) -> bool:
    conn = _get_connection()
    try:
        entry_id = data.get("entry_id") or _new_entry_id()
        conn.execute(
            """INSERT INTO projects
               (entry_id, name, job_number, location, site_address, client,
                client_contact, superintendent, company_name, company_abn,
                company_address, base_contract_amount, cashflow_path,
                source_type, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(entry_id) DO UPDATE SET
                name = excluded.name, job_number = excluded.job_number,
                location = excluded.location, site_address = excluded.site_address,
                client = excluded.client,
                client_contact = excluded.client_contact,
                superintendent = excluded.superintendent,
                company_name = excluded.company_name, company_abn = excluded.company_abn,
                company_address = excluded.company_address,
                base_contract_amount = excluded.base_contract_amount,
                cashflow_path = excluded.cashflow_path,
                source_type = excluded.source_type, updated_at = datetime('now')""",
            (entry_id, data.get("name", ""), data.get("job_number", ""),
             data.get("location", ""), data.get("site_address", ""),
             data.get("client", ""),
             data.get("client_contact", ""), data.get("superintendent", ""),
             data.get("company_name", "Welink Construction"),
             data.get("company_abn", ""), data.get("company_address", ""),
             data.get("base_contract_amount", 0), data.get("cashflow_path", ""),
             data.get("source_type", "new")),
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"[progress_claim_db] Error upserting project: {e}")
        return False
    finally:
        conn.close()


def get_project(entry_id: str) -> dict | None:
    conn = _get_connection()
    try:
        row = conn.execute("SELECT * FROM projects WHERE entry_id = ?", (entry_id,)).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def get_projects() -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[progress_claim_db] Error reading projects: {e}")
        return []
    finally:
        conn.close()


def update_project(entry_id: str, **fields) -> bool:
    if not fields:
        return False
    conn = _get_connection()
    try:
        set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
        values = list(fields.values()) + [entry_id]
        conn.execute(f"UPDATE projects SET {set_clause}, updated_at = datetime('now') WHERE entry_id = ?", values)
        conn.commit()
        return True
    except Exception as e:
        print(f"[progress_claim_db] Error updating project: {e}")
        return False
    finally:
        conn.close()


def delete_project(entry_id: str) -> bool:
    """Delete a project and all its cashflow + claim data (cascading)."""
    conn = _get_connection()
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        # Delete claims + claim items first
        claim_ids = [r[0] for r in conn.execute(
            "SELECT entry_id FROM progress_claims WHERE project_entry_id = ?", (entry_id,)).fetchall()]
        for cid in claim_ids:
            conn.execute("DELETE FROM progress_claim_items WHERE claim_entry_id = ?", (cid,))
        conn.execute("DELETE FROM progress_claims WHERE project_entry_id = ?", (entry_id,))
        # Delete cashflow progress + work items + months
        item_ids = [r[0] for r in conn.execute(
            "SELECT id FROM cashflow_work_items WHERE project_entry_id = ?", (entry_id,)).fetchall()]
        for iid in item_ids:
            conn.execute("DELETE FROM cashflow_progress WHERE work_item_id = ?", (iid,))
        conn.execute("DELETE FROM cashflow_work_items WHERE project_entry_id = ?", (entry_id,))
        conn.execute("DELETE FROM cashflow_months WHERE project_entry_id = ?", (entry_id,))
        conn.execute("DELETE FROM projects WHERE entry_id = ?", (entry_id,))
        conn.commit()
        return True
    except Exception as e:
        print(f"[progress_claim_db] Error deleting project: {e}")
        return False
    finally:
        conn.close()


# =============================================================================
# Cashflow: work items
# =============================================================================


def clear_cashflow_for_project(project_entry_id: str) -> None:
    """Remove all existing cashflow data for a project (used before re-import)."""
    conn = _get_connection()
    try:
        item_ids = [r[0] for r in conn.execute(
            "SELECT id FROM cashflow_work_items WHERE project_entry_id = ?", (project_entry_id,)).fetchall()]
        for iid in item_ids:
            conn.execute("DELETE FROM cashflow_progress WHERE work_item_id = ?", (iid,))
        conn.execute("DELETE FROM cashflow_work_items WHERE project_entry_id = ?", (project_entry_id,))
        conn.execute("DELETE FROM cashflow_months WHERE project_entry_id = ?", (project_entry_id,))
        conn.commit()
    except Exception as e:
        print(f"[progress_claim_db] Error clearing cashflow: {e}")
    finally:
        conn.close()


def upsert_work_item(data: dict) -> int | None:
    """Insert a work item. Returns its row id, or None on failure."""
    conn = _get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO cashflow_work_items
               (project_entry_id, section, section_label, item_number, description,
                cost, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (data.get("project_entry_id", ""), data.get("section", ""),
             data.get("section_label", ""), data.get("item_number", 0),
             data.get("description", ""), data.get("cost", 0),
             data.get("sort_order", 0)),
        )
        conn.commit()
        return cur.lastrowid
    except Exception as e:
        print(f"[progress_claim_db] Error upserting work item: {e}")
        return None
    finally:
        conn.close()


def get_work_items(project_entry_id: str) -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM cashflow_work_items WHERE project_entry_id = ? ORDER BY sort_order, id",
            (project_entry_id,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[progress_claim_db] Error reading work items: {e}")
        return []
    finally:
        conn.close()


def get_work_items_by_section(project_entry_id: str) -> dict[str, list[dict]]:
    """Group work items by section code, preserving order."""
    items = get_work_items(project_entry_id)
    grouped: dict[str, list[dict]] = {}
    for it in items:
        grouped.setdefault(it["section"], []).append(it)
    return grouped


# =============================================================================
# Cashflow: sections (freeform, add/remove/rename)
# =============================================================================


def clear_sections_for_project(project_entry_id: str) -> None:
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM cashflow_sections WHERE project_entry_id = ?", (project_entry_id,))
        conn.commit()
    except Exception as e:
        print(f"[progress_claim_db] Error clearing sections: {e}")
    finally:
        conn.close()


def upsert_section(data: dict) -> int | None:
    conn = _get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO cashflow_sections
               (project_entry_id, section_code, section_label, claimable, sort_order)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(project_entry_id, section_code) DO UPDATE SET
                section_label = excluded.section_label,
                claimable = excluded.claimable,
                sort_order = excluded.sort_order""",
            (data.get("project_entry_id", ""), data.get("section_code", ""),
             data.get("section_label", ""), data.get("claimable", 1),
             data.get("sort_order", 0)),
        )
        row = conn.execute(
            "SELECT id FROM cashflow_sections WHERE project_entry_id = ? AND section_code = ?",
            (data.get("project_entry_id", ""), data.get("section_code", ""))).fetchone()
        conn.commit()
        return row["id"] if row else cur.lastrowid
    except Exception as e:
        print(f"[progress_claim_db] Error upserting section: {e}")
        return None
    finally:
        conn.close()


def get_sections(project_entry_id: str) -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM cashflow_sections WHERE project_entry_id = ? ORDER BY sort_order, id",
            (project_entry_id,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[progress_claim_db] Error reading sections: {e}")
        return []
    finally:
        conn.close()


def get_section(project_entry_id: str, section_code: str) -> dict | None:
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM cashflow_sections WHERE project_entry_id = ? AND section_code = ?",
            (project_entry_id, section_code)).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def update_section(project_entry_id: str, section_code: str, **fields) -> bool:
    allowed = {"section_label", "claimable", "sort_order", "section_code"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return False
    conn = _get_connection()
    try:
        set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
        values = list(fields.values()) + [project_entry_id, section_code]
        conn.execute(
            f"UPDATE cashflow_sections SET {set_clause} WHERE project_entry_id = ? AND section_code = ?",
            values)
        conn.commit()
        return True
    except Exception as e:
        print(f"[progress_claim_db] Error updating section: {e}")
        return False
    finally:
        conn.close()


def delete_section(project_entry_id: str, section_code: str) -> bool:
    """Delete a section and all its work items + progress."""
    conn = _get_connection()
    try:
        item_ids = [r[0] for r in conn.execute(
            "SELECT id FROM cashflow_work_items WHERE project_entry_id = ? AND section = ?",
            (project_entry_id, section_code)).fetchall()]
        for iid in item_ids:
            conn.execute("DELETE FROM cashflow_progress WHERE work_item_id = ?", (iid,))
        conn.execute("DELETE FROM cashflow_work_items WHERE project_entry_id = ? AND section = ?",
                     (project_entry_id, section_code))
        conn.execute("DELETE FROM cashflow_sections WHERE project_entry_id = ? AND section_code = ?",
                     (project_entry_id, section_code))
        conn.commit()
        return True
    except Exception as e:
        print(f"[progress_claim_db] Error deleting section: {e}")
        return False
    finally:
        conn.close()


def next_section_code(project_entry_id: str) -> str:
    """Next available single-letter section code (A, B, ..., Z, then AA, AB...)."""
    conn = _get_connection()
    try:
        used = {r[0] for r in conn.execute(
            "SELECT section_code FROM cashflow_sections WHERE project_entry_id = ?",
            (project_entry_id,)).fetchall()}
    finally:
        conn.close()
    # single letters first
    for i in range(26):
        code = chr(ord("A") + i)
        if code not in used:
            return code
    # double letters
    for i in range(26):
        for j in range(26):
            code = chr(ord("A") + i) + chr(ord("A") + j)
            if code not in used:
                return code
    return "ZZ"


def get_work_item(item_id: int) -> dict | None:
    conn = _get_connection()
    try:
        row = conn.execute("SELECT * FROM cashflow_work_items WHERE id = ?", (item_id,)).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def update_work_item(item_id: int, **fields) -> bool:
    """Patch a work item's fields (e.g. description, cost)."""
    if not fields:
        return False
    allowed = {"description", "cost", "section", "section_label", "item_number", "sort_order"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return False
    conn = _get_connection()
    try:
        set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
        values = list(fields.values()) + [item_id]
        conn.execute(f"UPDATE cashflow_work_items SET {set_clause}, updated_at = datetime('now') WHERE id = ?", values)
        conn.commit()
        return True
    except Exception as e:
        print(f"[progress_claim_db] Error updating work item: {e}")
        return False
    finally:
        conn.close()


def delete_work_item(item_id: int) -> bool:
    """Delete a work item and all its progress records."""
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM cashflow_progress WHERE work_item_id = ?", (item_id,))
        conn.execute("DELETE FROM cashflow_work_items WHERE id = ?", (item_id,))
        conn.commit()
        return True
    except Exception as e:
        print(f"[progress_claim_db] Error deleting work item: {e}")
        return False
    finally:
        conn.close()


# =============================================================================
# Cashflow: months
# =============================================================================


def upsert_month(data: dict) -> int | None:
    conn = _get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO cashflow_months
               (project_entry_id, month_key, month_label, month_index)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(project_entry_id, month_key) DO UPDATE SET
                month_label = excluded.month_label,
                month_index = excluded.month_index""",
            (data.get("project_entry_id", ""), data.get("month_key", ""),
             data.get("month_label", ""), data.get("month_index", 0)),
        )
        # Resolve the actual id (whether inserted or existing)
        row = conn.execute(
            "SELECT id FROM cashflow_months WHERE project_entry_id = ? AND month_key = ?",
            (data.get("project_entry_id", ""), data.get("month_key", ""))).fetchone()
        conn.commit()
        return row["id"] if row else cur.lastrowid
    except Exception as e:
        print(f"[progress_claim_db] Error upserting month: {e}")
        return None
    finally:
        conn.close()


def get_months(project_entry_id: str) -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM cashflow_months WHERE project_entry_id = ? ORDER BY month_index",
            (project_entry_id,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[progress_claim_db] Error reading months: {e}")
        return []
    finally:
        conn.close()


def get_month(month_id: int) -> dict | None:
    conn = _get_connection()
    try:
        row = conn.execute("SELECT * FROM cashflow_months WHERE id = ?", (month_id,)).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def delete_month(month_id: int) -> bool:
    """Delete a month column and all progress records for it."""
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM cashflow_progress WHERE month_id = ?", (month_id,))
        conn.execute("DELETE FROM cashflow_months WHERE id = ?", (month_id,))
        conn.commit()
        return True
    except Exception as e:
        print(f"[progress_claim_db] Error deleting month: {e}")
        return False
    finally:
        conn.close()


# =============================================================================
# Cashflow: progress (per item / per month)
# =============================================================================


def set_progress(work_item_id: int, month_id: int, percentage: float) -> bool:
    """Upsert a progress record. Amount is left to the caller/service to compute."""
    conn = _get_connection()
    try:
        conn.execute(
            """INSERT INTO cashflow_progress
               (work_item_id, month_id, percentage, amount, updated_at)
               VALUES (?, ?, ?, 0, datetime('now'))
               ON CONFLICT(work_item_id, month_id) DO UPDATE SET
                percentage = excluded.percentage, updated_at = datetime('now')""",
            (work_item_id, month_id, percentage),
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"[progress_claim_db] Error setting progress: {e}")
        return False
    finally:
        conn.close()


def bulk_set_progress(records: list[dict]) -> bool:
    """records: [{work_item_id, month_id, percentage, amount}]"""
    if not records:
        return True
    conn = _get_connection()
    try:
        conn.executemany(
            """INSERT INTO cashflow_progress
               (work_item_id, month_id, percentage, amount, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'))
               ON CONFLICT(work_item_id, month_id) DO UPDATE SET
                percentage = excluded.percentage,
                amount = excluded.amount,
                updated_at = datetime('now')""",
            [(r["work_item_id"], r["month_id"], r.get("percentage", 0), r.get("amount", 0))
             for r in records],
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"[progress_claim_db] Error bulk setting progress: {e}")
        return False
    finally:
        conn.close()


def get_progress_for_item(work_item_id: int) -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM cashflow_progress WHERE work_item_id = ? ORDER BY month_id",
            (work_item_id,)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def get_all_progress(project_entry_id: str) -> list[dict]:
    """All progress records for a project, joined with month index for ordering."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            """SELECT p.*, m.month_index, m.month_key
               FROM cashflow_progress p
               JOIN cashflow_work_items w ON w.id = p.work_item_id
               JOIN cashflow_months m ON m.id = p.month_id
               WHERE w.project_entry_id = ?
               ORDER BY w.sort_order, m.month_index""",
            (project_entry_id,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[progress_claim_db] Error reading all progress: {e}")
        return []
    finally:
        conn.close()


# =============================================================================
# Progress claims
# =============================================================================


def upsert_claim(data: dict) -> str | None:
    """Insert or update a claim. Returns entry_id."""
    conn = _get_connection()
    try:
        entry_id = data.get("entry_id") or _new_entry_id()
        conn.execute(
            """INSERT INTO progress_claims
               (entry_id, project_entry_id, claim_number, claim_month, claim_date,
                rev_number, status, retention_percentage, retention_max_percentage,
                gross_claim, less_previous_claims, retention_amount, total_retention_held,
                net_claim, gst_amount, total_including_gst, section_a_total,
                section_b_total, section_c_total, section_d_total, section_e_total,
                cumulative_claimed, excel_path, pdf_path, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(entry_id) DO UPDATE SET
                claim_month = excluded.claim_month, claim_date = excluded.claim_date,
                rev_number = excluded.rev_number, status = excluded.status,
                retention_percentage = excluded.retention_percentage,
                retention_max_percentage = excluded.retention_max_percentage,
                gross_claim = excluded.gross_claim,
                less_previous_claims = excluded.less_previous_claims,
                retention_amount = excluded.retention_amount,
                total_retention_held = excluded.total_retention_held,
                net_claim = excluded.net_claim, gst_amount = excluded.gst_amount,
                total_including_gst = excluded.total_including_gst,
                section_a_total = excluded.section_a_total,
                section_b_total = excluded.section_b_total,
                section_c_total = excluded.section_c_total,
                section_d_total = excluded.section_d_total,
                section_e_total = excluded.section_e_total,
                cumulative_claimed = excluded.cumulative_claimed,
                excel_path = excluded.excel_path, pdf_path = excluded.pdf_path,
                updated_at = datetime('now')""",
            (entry_id, data.get("project_entry_id", ""), data.get("claim_number", 1),
             data.get("claim_month", ""), data.get("claim_date"),
             data.get("rev_number", 1), data.get("status", "draft"),
             data.get("retention_percentage", 10), data.get("retention_max_percentage", 5),
             data.get("gross_claim", 0), data.get("less_previous_claims", 0),
             data.get("retention_amount", 0), data.get("total_retention_held", 0),
             data.get("net_claim", 0),
             data.get("gst_amount", 0), data.get("total_including_gst", 0),
             data.get("section_a_total", 0), data.get("section_b_total", 0),
             data.get("section_c_total", 0), data.get("section_d_total", 0),
             data.get("section_e_total", 0), data.get("cumulative_claimed", 0),
             data.get("excel_path"), data.get("pdf_path")),
        )
        conn.commit()
        return entry_id
    except Exception as e:
        print(f"[progress_claim_db] Error upserting claim: {e}")
        return None
    finally:
        conn.close()


def get_claim(entry_id: str) -> dict | None:
    conn = _get_connection()
    try:
        row = conn.execute("SELECT * FROM progress_claims WHERE entry_id = ?", (entry_id,)).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def get_claims(project_entry_id: str) -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM progress_claims WHERE project_entry_id = ? ORDER BY claim_number DESC",
            (project_entry_id,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[progress_claim_db] Error reading claims: {e}")
        return []
    finally:
        conn.close()


def get_latest_claim(project_entry_id: str) -> dict | None:
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM progress_claims WHERE project_entry_id = ? ORDER BY claim_number DESC LIMIT 1",
            (project_entry_id,)).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def get_previous_claim(project_entry_id: str, before_claim_number: int) -> dict | None:
    """The most recent claim with number < before_claim_number (i.e. the prior claim)."""
    conn = _get_connection()
    try:
        row = conn.execute(
            """SELECT * FROM progress_claims
               WHERE project_entry_id = ? AND claim_number < ?
               ORDER BY claim_number DESC LIMIT 1""",
            (project_entry_id, before_claim_number)).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def update_claim(entry_id: str, **fields) -> bool:
    if not fields:
        return False
    conn = _get_connection()
    try:
        set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
        values = list(fields.values()) + [entry_id]
        conn.execute(f"UPDATE progress_claims SET {set_clause}, updated_at = datetime('now') WHERE entry_id = ?", values)
        conn.commit()
        return True
    except Exception as e:
        print(f"[progress_claim_db] Error updating claim: {e}")
        return False
    finally:
        conn.close()


def delete_claim(entry_id: str) -> bool:
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM progress_claim_items WHERE claim_entry_id = ?", (entry_id,))
        conn.execute("DELETE FROM progress_claims WHERE entry_id = ?", (entry_id,))
        conn.commit()
        return True
    except Exception as e:
        print(f"[progress_claim_db] Error deleting claim: {e}")
        return False
    finally:
        conn.close()


def next_claim_number(project_entry_id: str) -> int:
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT MAX(claim_number) as max_no FROM progress_claims WHERE project_entry_id = ?",
            (project_entry_id,)).fetchone()
        return (row["max_no"] or 0) + 1
    except Exception:
        return 1
    finally:
        conn.close()


# =============================================================================
# Progress claim items
# =============================================================================


def clear_claim_items(claim_entry_id: str) -> bool:
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM progress_claim_items WHERE claim_entry_id = ?", (claim_entry_id,))
        conn.commit()
        return True
    except Exception as e:
        print(f"[progress_claim_db] Error clearing claim items: {e}")
        return False
    finally:
        conn.close()


def bulk_insert_claim_items(items: list[dict]) -> bool:
    if not items:
        return True
    conn = _get_connection()
    try:
        conn.executemany(
            """INSERT INTO progress_claim_items
               (claim_entry_id, work_item_id, section, item_number, description, cost,
                cumulative_percentage, total_claimed, previously_claimed, current_claim,
                balance_remaining, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(i.get("claim_entry_id", ""), i.get("work_item_id"),
              i.get("section", ""), i.get("item_number", 0),
              i.get("description", ""), i.get("cost", 0),
              i.get("cumulative_percentage", 0), i.get("total_claimed", 0),
              i.get("previously_claimed", 0), i.get("current_claim", 0),
              i.get("balance_remaining", 0), i.get("sort_order", 0))
             for i in items],
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"[progress_claim_db] Error inserting claim items: {e}")
        return False
    finally:
        conn.close()


def get_claim_items(claim_entry_id: str) -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM progress_claim_items WHERE claim_entry_id = ? ORDER BY sort_order, id",
            (claim_entry_id,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[progress_claim_db] Error reading claim items: {e}")
        return []
    finally:
        conn.close()


def get_claim_item(item_id: int) -> dict | None:
    conn = _get_connection()
    try:
        row = conn.execute("SELECT * FROM progress_claim_items WHERE id = ?", (item_id,)).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def update_claim_item(item_id: int, **fields) -> bool:
    """Patch a claim item's fields (e.g. previously_claimed override)."""
    if not fields:
        return False
    allowed = {"description", "cost", "cumulative_percentage", "total_claimed",
               "previously_claimed", "current_claim", "balance_remaining"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return False
    conn = _get_connection()
    try:
        set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
        values = list(fields.values()) + [item_id]
        conn.execute(f"UPDATE progress_claim_items SET {set_clause} WHERE id = ?", values)
        conn.commit()
        return True
    except Exception as e:
        print(f"[progress_claim_db] Error updating claim item: {e}")
        return False
    finally:
        conn.close()
