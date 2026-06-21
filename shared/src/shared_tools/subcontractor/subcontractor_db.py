"""Dedicated database for the Subcontractor Management module.

All subcontractor data lives in <LILAMY_DATA_DIR>/subcontractor.db — completely
separate from variations.db and mail_history.db.

Tables: projects, vendors, commitments, commitment_items, quotes, quote_items

Key design rules:
  - vendor_type ('supplier' | 'subcontractor') drives the instrument
  - Subcontractor + PO ≥ $100K → must upgrade to Subcontract
  - Deterministic money calculation — NEVER LLM
"""

import os
import sqlite3
from datetime import datetime
from pathlib import Path


def _resolve_data_dir() -> Path:
    if "LILAMY_DATA_DIR" in os.environ:
        return Path(os.environ["LILAMY_DATA_DIR"])
    project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
    return project_root / "data"


DATA_DIR = _resolve_data_dir()
DB_PATH = DATA_DIR / "subcontractor.db"


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    # ── Safety pragmas ───────────────────────────────────────────
    conn.execute("PRAGMA journal_mode=WAL")          # Write-ahead log: readers don't block writers
    conn.execute("PRAGMA foreign_keys=ON")            # Enforce FK constraints
    conn.execute("PRAGMA busy_timeout=5000")          # Wait 5s on lock instead of crashing
    conn.execute("PRAGMA synchronous=NORMAL")         # Safe with WAL, 10-50x faster than FULL
    conn.execute("PRAGMA cache_size=-4000")           # Limit memory to ~4MB
    conn.execute("PRAGMA temp_store=MEMORY")          # Temp tables in memory (not disk)
    return conn


def _migrate_add_column(conn, table: str, column: str, col_type: str) -> bool:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        conn.commit()
        return True
    return False


# =============================================================================
# Init + Migration
# =============================================================================


def init_subcontractor_db() -> None:
    """Create tables if they don't exist."""
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
                head_contract_sum REAL DEFAULT 0,
                contract_type TEXT DEFAULT 'AS 4000-1997',
                client_name TEXT DEFAULT '',
                company_name TEXT DEFAULT 'Welink Construction',
                start_date TEXT,
                pc_date TEXT,
                retention_pct REAL DEFAULT 5,
                status TEXT DEFAULT 'active',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_sub_projects_name
                ON projects(name);

            CREATE TABLE IF NOT EXISTS vendors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id TEXT UNIQUE NOT NULL,
                vendor_type TEXT NOT NULL DEFAULT 'subcontractor',
                company_name TEXT NOT NULL DEFAULT '',
                trading_name TEXT DEFAULT '',
                abn TEXT DEFAULT '',
                contact_name TEXT DEFAULT '',
                contact_email TEXT DEFAULT '',
                contact_phone TEXT DEFAULT '',
                address TEXT DEFAULT '',
                trade_categories TEXT DEFAULT '[]',
                prequalification_status TEXT DEFAULT 'pending',
                insurance_expiry TEXT,
                safety_rating TEXT DEFAULT '',
                performance_score REAL DEFAULT 0,
                notes TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_sub_vendors_type
                ON vendors(vendor_type);
            CREATE INDEX IF NOT EXISTS idx_sub_vendors_name
                ON vendors(company_name);
            CREATE INDEX IF NOT EXISTS idx_sub_vendors_status
                ON vendors(status);

            CREATE TABLE IF NOT EXISTS commitments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id TEXT UNIQUE NOT NULL,
                project_entry_id TEXT DEFAULT '',
                vendor_entry_id TEXT DEFAULT '',
                commitment_type TEXT NOT NULL DEFAULT 'purchase_order',
                reference_number TEXT DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                description TEXT DEFAULT '',
                commitment_value REAL DEFAULT 0,
                retention_pct REAL DEFAULT 0,
                retention_limit REAL DEFAULT 0,
                start_date TEXT,
                end_date TEXT,
                defects_liability_end TEXT,
                delivery_date TEXT,
                goods_receipt_date TEXT,
                delivery_instructions TEXT DEFAULT '',
                attention TEXT DEFAULT '',
                special_instructions TEXT DEFAULT '',
                revision INTEGER DEFAULT 1,
                approved_by TEXT DEFAULT 'ACHEN',
                status TEXT DEFAULT 'draft',
                securities_held REAL DEFAULT 0,
                insurance_verified INTEGER DEFAULT 0,
                upgraded_from_entry_id TEXT,
                upgraded_at TEXT,
                document_path TEXT,
                pdf_path TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_sub_commitments_type
                ON commitments(commitment_type);
            CREATE INDEX IF NOT EXISTS idx_sub_commitments_project
                ON commitments(project_entry_id);
            CREATE INDEX IF NOT EXISTS idx_sub_commitments_vendor
                ON commitments(vendor_entry_id);
            CREATE INDEX IF NOT EXISTS idx_sub_commitments_status
                ON commitments(status);

            CREATE TABLE IF NOT EXISTS commitment_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                commitment_entry_id TEXT NOT NULL
                    REFERENCES commitments(entry_id) ON DELETE CASCADE,
                item_number INTEGER NOT NULL DEFAULT 1,
                description TEXT DEFAULT '',
                qty REAL DEFAULT 0,
                unit TEXT DEFAULT 'item',
                rate REAL DEFAULT 0,
                amount REAL DEFAULT 0,
                wbs_code TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                sort_order INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_sub_citems_commitment
                ON commitment_items(commitment_entry_id);

            CREATE TABLE IF NOT EXISTS quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id TEXT UNIQUE NOT NULL,
                project_entry_id TEXT DEFAULT '',
                vendor_entry_id TEXT DEFAULT '',
                trade_name TEXT DEFAULT '',
                quote_ref TEXT DEFAULT '',
                total_amount REAL DEFAULT 0,
                date_submitted TEXT,
                is_awarded INTEGER DEFAULT 0,
                commitment_entry_id TEXT,
                source_file_path TEXT,
                ai_extracted INTEGER DEFAULT 0,
                notes TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_sub_quotes_project
                ON quotes(project_entry_id);
            CREATE INDEX IF NOT EXISTS idx_sub_quotes_vendor
                ON quotes(vendor_entry_id);
            CREATE INDEX IF NOT EXISTS idx_sub_quotes_trade
                ON quotes(trade_name);
            CREATE INDEX IF NOT EXISTS idx_sub_quotes_awarded
                ON quotes(is_awarded);

            CREATE TABLE IF NOT EXISTS quote_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quote_entry_id TEXT NOT NULL
                    REFERENCES quotes(entry_id) ON DELETE CASCADE,
                item_number INTEGER NOT NULL DEFAULT 1,
                description TEXT DEFAULT '',
                qty REAL DEFAULT 0,
                unit TEXT DEFAULT 'item',
                rate REAL DEFAULT 0,
                amount REAL DEFAULT 0,
                notes TEXT DEFAULT '',
                sort_order INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_sub_qitems_quote
                ON quote_items(quote_entry_id);

            -- ── Learner Knowledge Tables ───────────────────────────

            CREATE TABLE IF NOT EXISTS rate_benchmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id TEXT UNIQUE NOT NULL,
                trade_name TEXT NOT NULL DEFAULT '',
                scope_keyword TEXT DEFAULT '',
                unit TEXT DEFAULT 'item',
                min_rate REAL DEFAULT 0,
                max_rate REAL DEFAULT 0,
                avg_rate REAL DEFAULT 0,
                median_rate REAL DEFAULT 0,
                sample_count INTEGER DEFAULT 0,
                project_entry_id TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_sub_benchmarks_trade
                ON rate_benchmarks(trade_name);

            CREATE TABLE IF NOT EXISTS clause_library (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id TEXT UNIQUE NOT NULL,
                clause_number TEXT DEFAULT '',
                clause_title TEXT DEFAULT '',
                clause_text TEXT DEFAULT '',
                source_type TEXT DEFAULT 'subcontract',
                source_doc_path TEXT DEFAULT '',
                source_commitment_ref TEXT DEFAULT '',
                project_entry_id TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_sub_clauses_ref
                ON clause_library(source_commitment_ref);
            CREATE INDEX IF NOT EXISTS idx_sub_clauses_number
                ON clause_library(clause_number);

            CREATE TABLE IF NOT EXISTS competitive_sets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id TEXT UNIQUE NOT NULL,
                trade_name TEXT NOT NULL DEFAULT '',
                vendor_entry_ids TEXT DEFAULT '[]',
                project_entry_id TEXT DEFAULT '',
                quote_count INTEGER DEFAULT 0,
                awarded_vendor_entry_id TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_sub_compsets_trade
                ON competitive_sets(trade_name);
        """)
        conn.commit()

        # Run migrations for any columns added after initial deploy
        _migrate_add_column(conn, "commitments", "upgraded_from_entry_id", "TEXT")
        _migrate_add_column(conn, "commitments", "upgraded_at", "TEXT")
        _migrate_add_column(conn, "commitments", "delivery_instructions", "TEXT DEFAULT ''")
        _migrate_add_column(conn, "commitments", "attention", "TEXT DEFAULT ''")
        _migrate_add_column(conn, "commitments", "special_instructions", "TEXT DEFAULT ''")
        _migrate_add_column(conn, "commitments", "revision", "INTEGER DEFAULT 1")
        _migrate_add_column(conn, "commitments", "approved_by", "TEXT DEFAULT 'ACHEN'")
        _migrate_add_column(conn, "vendors", "trading_name", "TEXT DEFAULT ''")
        _migrate_add_column(conn, "quotes", "commitment_entry_id", "TEXT")
        # Learner support columns
        _migrate_add_column(conn, "vendors", "source", "TEXT DEFAULT 'manual'")
        _migrate_add_column(conn, "vendors", "learned_from_path", "TEXT")
        _migrate_add_column(conn, "vendors", "vendor_type_confidence", "TEXT")
        _migrate_add_column(conn, "quotes", "source", "TEXT DEFAULT 'manual'")
        _migrate_add_column(conn, "commitments", "source", "TEXT DEFAULT 'manual'")

    finally:
        conn.close()


# =============================================================================
# Projects
# =============================================================================


def upsert_project(data: dict) -> bool:
    conn = _get_connection()
    try:
        conn.execute(
            """INSERT INTO projects
               (entry_id, name, job_number, location, head_contract_sum,
                contract_type, client_name, company_name, start_date, pc_date,
                retention_pct, status, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(entry_id) DO UPDATE SET
                name = excluded.name, job_number = excluded.job_number,
                location = excluded.location,
                head_contract_sum = excluded.head_contract_sum,
                contract_type = excluded.contract_type,
                client_name = excluded.client_name,
                company_name = excluded.company_name,
                start_date = excluded.start_date, pc_date = excluded.pc_date,
                retention_pct = excluded.retention_pct,
                status = excluded.status, updated_at = datetime('now')""",
            (data.get("entry_id", ""), data.get("name", ""),
             data.get("job_number", ""), data.get("location", ""),
             data.get("head_contract_sum", 0), data.get("contract_type", "AS 4000-1997"),
             data.get("client_name", ""), data.get("company_name", "Welink Construction"),
             data.get("start_date"), data.get("pc_date"),
             data.get("retention_pct", 5), data.get("status", "active")),
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error upserting project: {e}")
        return False
    finally:
        conn.close()


def get_project(entry_id: str) -> dict | None:
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM projects WHERE entry_id = ?", (entry_id,)
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def get_projects() -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM projects ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"Error reading projects: {e}")
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
        conn.execute(
            f"UPDATE projects SET {set_clause}, updated_at = datetime('now') "
            f"WHERE entry_id = ?",
            values,
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error updating project: {e}")
        return False
    finally:
        conn.close()


def delete_project(entry_id: str) -> bool:
    conn = _get_connection()
    try:
        conn.execute(
            "DELETE FROM commitment_items WHERE commitment_entry_id IN "
            "(SELECT entry_id FROM commitments WHERE project_entry_id = ?)",
            (entry_id,),
        )
        conn.execute(
            "DELETE FROM quote_items WHERE quote_entry_id IN "
            "(SELECT entry_id FROM quotes WHERE project_entry_id = ?)",
            (entry_id,),
        )
        conn.execute("DELETE FROM commitments WHERE project_entry_id = ?", (entry_id,))
        conn.execute("DELETE FROM quotes WHERE project_entry_id = ?", (entry_id,))
        conn.execute("DELETE FROM projects WHERE entry_id = ?", (entry_id,))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error deleting project: {e}")
        return False
    finally:
        conn.close()


# =============================================================================
# Vendors
# =============================================================================


def upsert_vendor(data: dict) -> bool:
    conn = _get_connection()
    try:
        conn.execute(
            """INSERT INTO vendors
               (entry_id, vendor_type, company_name, trading_name, abn,
                contact_name, contact_email, contact_phone, address,
                trade_categories, prequalification_status, insurance_expiry,
                safety_rating, performance_score, notes, status,
                source, learned_from_path, vendor_type_confidence,
                updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, datetime('now'))
               ON CONFLICT(entry_id) DO UPDATE SET
                vendor_type = excluded.vendor_type,
                company_name = excluded.company_name,
                trading_name = excluded.trading_name,
                abn = excluded.abn,
                contact_name = excluded.contact_name,
                contact_email = excluded.contact_email,
                contact_phone = excluded.contact_phone,
                address = excluded.address,
                trade_categories = excluded.trade_categories,
                prequalification_status = excluded.prequalification_status,
                insurance_expiry = excluded.insurance_expiry,
                safety_rating = excluded.safety_rating,
                performance_score = excluded.performance_score,
                notes = excluded.notes,
                status = excluded.status,
                source = COALESCE(excluded.source, vendors.source),
                learned_from_path = COALESCE(excluded.learned_from_path, vendors.learned_from_path),
                vendor_type_confidence = COALESCE(excluded.vendor_type_confidence, vendors.vendor_type_confidence),
                updated_at = datetime('now')""",
            (data.get("entry_id", ""), data.get("vendor_type", "subcontractor"),
             data.get("company_name", ""), data.get("trading_name", ""),
             data.get("abn", ""), data.get("contact_name", ""),
             data.get("contact_email", ""), data.get("contact_phone", ""),
             data.get("address", ""), data.get("trade_categories", "[]"),
             data.get("prequalification_status", "pending"),
             data.get("insurance_expiry"), data.get("safety_rating", ""),
             data.get("performance_score", 0), data.get("notes", ""),
             data.get("status", "active"),
             data.get("source", "manual"),
             data.get("learned_from_path"),
             data.get("vendor_type_confidence")),
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error upserting vendor: {e}")
        return False
    finally:
        conn.close()


def get_vendor(entry_id: str) -> dict | None:
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM vendors WHERE entry_id = ?", (entry_id,)
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def get_vendors(vendor_type: str | None = None, trade: str | None = None,
                status: str = "active") -> list[dict]:
    conn = _get_connection()
    try:
        query = "SELECT * FROM vendors WHERE status = ?"
        params: list = [status]
        if vendor_type:
            query += " AND vendor_type = ?"
            params.append(vendor_type)
        if trade:
            query += " AND trade_categories LIKE ?"
            params.append(f"%{trade}%")
        query += " ORDER BY company_name"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"Error reading vendors: {e}")
        return []
    finally:
        conn.close()


def update_vendor(entry_id: str, **fields) -> bool:
    if not fields:
        return False
    conn = _get_connection()
    try:
        set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
        values = list(fields.values()) + [entry_id]
        conn.execute(
            f"UPDATE vendors SET {set_clause}, updated_at = datetime('now') "
            f"WHERE entry_id = ?",
            values,
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error updating vendor: {e}")
        return False
    finally:
        conn.close()


def delete_vendor(entry_id: str) -> bool:
    conn = _get_connection()
    try:
        conn.execute(
            "UPDATE vendors SET status = 'inactive', updated_at = datetime('now') "
            "WHERE entry_id = ?",
            (entry_id,),
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


# =============================================================================
# Commitments (POs + Subcontracts)
# =============================================================================


def upsert_commitment(data: dict) -> bool:
    conn = _get_connection()
    try:
        conn.execute(
            """INSERT INTO commitments
               (entry_id, project_entry_id, vendor_entry_id, commitment_type,
                reference_number, title, description, commitment_value,
                retention_pct, retention_limit, start_date, end_date,
                defects_liability_end, delivery_date, goods_receipt_date,
                delivery_instructions, attention, special_instructions,
                revision, approved_by,
                status, securities_held, insurance_verified,
                upgraded_from_entry_id, upgraded_at,
                document_path, pdf_path,
                source, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(entry_id) DO UPDATE SET
                project_entry_id = excluded.project_entry_id,
                vendor_entry_id = excluded.vendor_entry_id,
                commitment_type = excluded.commitment_type,
                reference_number = excluded.reference_number,
                title = excluded.title,
                description = excluded.description,
                commitment_value = excluded.commitment_value,
                retention_pct = excluded.retention_pct,
                retention_limit = excluded.retention_limit,
                start_date = excluded.start_date,
                end_date = excluded.end_date,
                defects_liability_end = excluded.defects_liability_end,
                delivery_date = excluded.delivery_date,
                goods_receipt_date = excluded.goods_receipt_date,
                delivery_instructions = excluded.delivery_instructions,
                attention = excluded.attention,
                special_instructions = excluded.special_instructions,
                revision = excluded.revision,
                approved_by = excluded.approved_by,
                status = excluded.status,
                securities_held = excluded.securities_held,
                insurance_verified = excluded.insurance_verified,
                upgraded_from_entry_id = excluded.upgraded_from_entry_id,
                upgraded_at = excluded.upgraded_at,
                document_path = excluded.document_path,
                pdf_path = excluded.pdf_path,
                source = COALESCE(excluded.source, commitments.source),
                updated_at = datetime('now')""",
            (data.get("entry_id", ""), data.get("project_entry_id", ""),
             data.get("vendor_entry_id", ""),
             data.get("commitment_type", "purchase_order"),
             data.get("reference_number", ""), data.get("title", ""),
             data.get("description", ""), data.get("commitment_value", 0),
             data.get("retention_pct", 0), data.get("retention_limit", 0),
             data.get("start_date"), data.get("end_date"),
             data.get("defects_liability_end"),
             data.get("delivery_date"), data.get("goods_receipt_date"),
             data.get("delivery_instructions", ""),
             data.get("attention", ""),
             data.get("special_instructions", ""),
             data.get("revision", 1),
             data.get("approved_by", "ACHEN"),
             data.get("status", "draft"), data.get("securities_held", 0),
             data.get("insurance_verified", 0),
             data.get("upgraded_from_entry_id"),
             data.get("upgraded_at"),
             data.get("document_path"), data.get("pdf_path"),
             data.get("source", "manual"),
             datetime.now().isoformat()),
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error upserting commitment: {e}")
        return False
    finally:
        conn.close()


def get_commitment(entry_id: str) -> dict | None:
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM commitments WHERE entry_id = ?", (entry_id,)
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def get_commitments(project_entry_id: str | None = None,
                    commitment_type: str | None = None,
                    vendor_entry_id: str | None = None,
                    status: str | None = None) -> list[dict]:
    conn = _get_connection()
    try:
        query = "SELECT * FROM commitments WHERE 1=1"
        params: list = []
        if project_entry_id:
            query += " AND project_entry_id = ?"
            params.append(project_entry_id)
        if commitment_type:
            query += " AND commitment_type = ?"
            params.append(commitment_type)
        if vendor_entry_id:
            query += " AND vendor_entry_id = ?"
            params.append(vendor_entry_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"Error reading commitments: {e}")
        return []
    finally:
        conn.close()


def update_commitment(entry_id: str, **fields) -> bool:
    if not fields:
        return False
    conn = _get_connection()
    try:
        set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
        values = list(fields.values()) + [entry_id]
        conn.execute(
            f"UPDATE commitments SET {set_clause}, updated_at = datetime('now') "
            f"WHERE entry_id = ?",
            values,
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error updating commitment: {e}")
        return False
    finally:
        conn.close()


def delete_commitment(entry_id: str) -> bool:
    conn = _get_connection()
    try:
        conn.execute(
            "DELETE FROM commitment_items WHERE commitment_entry_id = ?",
            (entry_id,),
        )
        conn.execute("DELETE FROM commitments WHERE entry_id = ?", (entry_id,))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def get_next_po_number(project_entry_id: str) -> int:
    """Return the next PO number for a project."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM commitments "
            "WHERE project_entry_id = ? AND commitment_type = 'purchase_order'",
            (project_entry_id,),
        ).fetchone()
        # Start from the project's first PO number (approximate)
        base = 16808  # ARCO's first PO number
        return base + (row["cnt"] if row else 0)
    except Exception:
        return 1
    finally:
        conn.close()


def get_next_subcontract_number(project_entry_id: str) -> int:
    """Return the next Subcontract number for a project (S01, S02, ...)."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM commitments "
            "WHERE project_entry_id = ? AND commitment_type = 'subcontract'",
            (project_entry_id,),
        ).fetchone()
        return (row["cnt"] if row else 0) + 1
    except Exception:
        return 1
    finally:
        conn.close()


# =============================================================================
# Commitment Items (line items)
# =============================================================================


def upsert_commitment_item(data: dict) -> int | None:
    conn = _get_connection()
    try:
        item_id = data.get("id")
        if item_id:
            updatable = ["item_number", "description", "qty", "unit",
                         "rate", "amount", "wbs_code", "notes", "sort_order"]
            set_parts = []
            values = []
            for col in updatable:
                if col in data:
                    set_parts.append(f"{col} = ?")
                    values.append(data[col])
            if not set_parts:
                return item_id
            values.append(item_id)
            conn.execute(
                f"UPDATE commitment_items SET {', '.join(set_parts)} WHERE id = ?",
                values,
            )
            conn.commit()
            return item_id
        else:
            cursor = conn.execute(
                """INSERT INTO commitment_items
                   (commitment_entry_id, item_number, description, qty, unit,
                    rate, amount, wbs_code, notes, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (data.get("commitment_entry_id", ""), data.get("item_number", 1),
                 data.get("description", ""), data.get("qty", 0),
                 data.get("unit", "item"), data.get("rate", 0),
                 data.get("amount", 0), data.get("wbs_code", ""),
                 data.get("notes", ""), data.get("sort_order", 0)),
            )
            conn.commit()
            return cursor.lastrowid
    except Exception as e:
        print(f"Error upserting commitment item: {e}")
        return None
    finally:
        conn.close()


def get_commitment_items(commitment_entry_id: str) -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM commitment_items WHERE commitment_entry_id = ? "
            "ORDER BY sort_order, item_number",
            (commitment_entry_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"Error reading commitment items: {e}")
        return []
    finally:
        conn.close()


def delete_commitment_item(item_id: int) -> bool:
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM commitment_items WHERE id = ?", (item_id,))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def reorder_commitment_items(commitment_entry_id: str, item_ids: list[int]) -> bool:
    conn = _get_connection()
    try:
        for idx, item_id in enumerate(item_ids):
            conn.execute(
                "UPDATE commitment_items SET sort_order = ? "
                "WHERE id = ? AND commitment_entry_id = ?",
                (idx, item_id, commitment_entry_id),
            )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error reordering items: {e}")
        return False
    finally:
        conn.close()


# =============================================================================
# Quotes
# =============================================================================


def upsert_quote(data: dict) -> bool:
    conn = _get_connection()
    try:
        conn.execute(
            """INSERT INTO quotes
               (entry_id, project_entry_id, vendor_entry_id, trade_name,
                quote_ref, total_amount, date_submitted, is_awarded,
                commitment_entry_id, source_file_path, ai_extracted, notes,
                source, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(entry_id) DO UPDATE SET
                project_entry_id = excluded.project_entry_id,
                vendor_entry_id = excluded.vendor_entry_id,
                trade_name = excluded.trade_name,
                quote_ref = excluded.quote_ref,
                total_amount = excluded.total_amount,
                date_submitted = excluded.date_submitted,
                is_awarded = excluded.is_awarded,
                commitment_entry_id = excluded.commitment_entry_id,
                source_file_path = excluded.source_file_path,
                ai_extracted = excluded.ai_extracted,
                notes = excluded.notes,
                source = COALESCE(excluded.source, quotes.source),
                updated_at = datetime('now')""",
            (data.get("entry_id", ""), data.get("project_entry_id", ""),
             data.get("vendor_entry_id", ""), data.get("trade_name", ""),
             data.get("quote_ref", ""), data.get("total_amount", 0),
             data.get("date_submitted"), data.get("is_awarded", 0),
             data.get("commitment_entry_id"), data.get("source_file_path", ""),
             data.get("ai_extracted", 0), data.get("notes", ""),
             data.get("source", "manual")),
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error upserting quote: {e}")
        return False
    finally:
        conn.close()


def get_quote(entry_id: str) -> dict | None:
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM quotes WHERE entry_id = ?", (entry_id,)
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def get_quotes(project_entry_id: str | None = None,
               trade_name: str | None = None,
               vendor_entry_id: str | None = None,
               is_awarded: int | None = None) -> list[dict]:
    conn = _get_connection()
    try:
        query = "SELECT * FROM quotes WHERE 1=1"
        params: list = []
        if project_entry_id:
            query += " AND project_entry_id = ?"
            params.append(project_entry_id)
        if trade_name:
            query += " AND trade_name = ?"
            params.append(trade_name)
        if vendor_entry_id:
            query += " AND vendor_entry_id = ?"
            params.append(vendor_entry_id)
        if is_awarded is not None:
            query += " AND is_awarded = ?"
            params.append(is_awarded)
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"Error reading quotes: {e}")
        return []
    finally:
        conn.close()


def update_quote(entry_id: str, **fields) -> bool:
    if not fields:
        return False
    conn = _get_connection()
    try:
        set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
        values = list(fields.values()) + [entry_id]
        conn.execute(
            f"UPDATE quotes SET {set_clause}, updated_at = datetime('now') "
            f"WHERE entry_id = ?",
            values,
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error updating quote: {e}")
        return False
    finally:
        conn.close()


def delete_quote(entry_id: str) -> bool:
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM quote_items WHERE quote_entry_id = ?", (entry_id,))
        conn.execute("DELETE FROM quotes WHERE entry_id = ?", (entry_id,))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


# =============================================================================
# Quote Items
# =============================================================================


def upsert_quote_item(data: dict) -> int | None:
    conn = _get_connection()
    try:
        item_id = data.get("id")
        if item_id:
            updatable = ["item_number", "description", "qty", "unit",
                         "rate", "amount", "notes", "sort_order"]
            set_parts = []
            values = []
            for col in updatable:
                if col in data:
                    set_parts.append(f"{col} = ?")
                    values.append(data[col])
            if not set_parts:
                return item_id
            values.append(item_id)
            conn.execute(
                f"UPDATE quote_items SET {', '.join(set_parts)} WHERE id = ?",
                values,
            )
            conn.commit()
            return item_id
        else:
            cursor = conn.execute(
                """INSERT INTO quote_items
                   (quote_entry_id, item_number, description, qty, unit,
                    rate, amount, notes, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (data.get("quote_entry_id", ""), data.get("item_number", 1),
                 data.get("description", ""), data.get("qty", 0),
                 data.get("unit", "item"), data.get("rate", 0),
                 data.get("amount", 0), data.get("notes", ""),
                 data.get("sort_order", 0)),
            )
            conn.commit()
            return cursor.lastrowid
    except Exception as e:
        print(f"Error upserting quote item: {e}")
        return None
    finally:
        conn.close()


def get_quote_items(quote_entry_id: str) -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM quote_items WHERE quote_entry_id = ? "
            "ORDER BY sort_order, item_number",
            (quote_entry_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"Error reading quote items: {e}")
        return []
    finally:
        conn.close()


def delete_quote_item(item_id: int) -> bool:
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM quote_items WHERE id = ?", (item_id,))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


# =============================================================================
# Cross-entity queries
# =============================================================================


def get_commitment_with_items(entry_id: str) -> dict | None:
    """Return a commitment with its line items, vendor name, and project name."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT c.*, v.company_name as vendor_name, v.vendor_type, "
            "v.address as vendor_address, v.abn as vendor_abn, "
            "v.contact_name as vendor_contact, v.contact_email as vendor_contact_email, "
            "v.contact_phone as vendor_contact_phone, "
            "p.name as project_name, p.job_number as project_code, "
            "p.location as project_location "
            "FROM commitments c "
            "LEFT JOIN vendors v ON c.vendor_entry_id = v.entry_id "
            "LEFT JOIN projects p ON c.project_entry_id = p.entry_id "
            "WHERE c.entry_id = ?",
            (entry_id,),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        items = conn.execute(
            "SELECT * FROM commitment_items WHERE commitment_entry_id = ? "
            "ORDER BY sort_order, item_number",
            (entry_id,),
        ).fetchall()
        result["items"] = [dict(r) for r in items]
        return result
    except Exception as e:
        print(f"Error getting commitment with items: {e}")
        return None
    finally:
        conn.close()


def get_quote_with_items(entry_id: str) -> dict | None:
    """Return a quote with its line items."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT q.*, v.company_name as vendor_name, v.vendor_type "
            "FROM quotes q "
            "LEFT JOIN vendors v ON q.vendor_entry_id = v.entry_id "
            "WHERE q.entry_id = ?",
            (entry_id,),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        items = conn.execute(
            "SELECT * FROM quote_items WHERE quote_entry_id = ? "
            "ORDER BY sort_order, item_number",
            (entry_id,),
        ).fetchall()
        result["items"] = [dict(r) for r in items]
        return result
    except Exception as e:
        print(f"Error getting quote with items: {e}")
        return None
    finally:
        conn.close()


def get_vendor_summary(entry_id: str) -> dict | None:
    """Return a vendor with its commitment and quote history."""
    conn = _get_connection()
    try:
        vendor = conn.execute(
            "SELECT * FROM vendors WHERE entry_id = ?", (entry_id,)
        ).fetchone()
        if not vendor:
            return None
        result = dict(vendor)

        commitments = conn.execute(
            "SELECT * FROM commitments WHERE vendor_entry_id = ? "
            "ORDER BY created_at DESC",
            (entry_id,),
        ).fetchall()
        result["commitments"] = [dict(r) for r in commitments]

        quotes = conn.execute(
            "SELECT * FROM quotes WHERE vendor_entry_id = ? "
            "ORDER BY created_at DESC",
            (entry_id,),
        ).fetchall()
        result["quotes"] = [dict(r) for r in quotes]

        return result
    except Exception as e:
        print(f"Error getting vendor summary: {e}")
        return None
    finally:
        conn.close()


# =============================================================================
# Rate Benchmarks (learner knowledge table)
# =============================================================================


def upsert_rate_benchmark(data: dict) -> bool:
    conn = _get_connection()
    try:
        conn.execute(
            """INSERT INTO rate_benchmarks
               (entry_id, trade_name, scope_keyword, unit,
                min_rate, max_rate, avg_rate, median_rate,
                sample_count, project_entry_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(entry_id) DO UPDATE SET
                trade_name = excluded.trade_name,
                scope_keyword = excluded.scope_keyword,
                unit = excluded.unit,
                min_rate = excluded.min_rate,
                max_rate = excluded.max_rate,
                avg_rate = excluded.avg_rate,
                median_rate = excluded.median_rate,
                sample_count = excluded.sample_count,
                project_entry_id = excluded.project_entry_id""",
            (data.get("entry_id", ""), data.get("trade_name", ""),
             data.get("scope_keyword", ""), data.get("unit", "item"),
             data.get("min_rate", 0), data.get("max_rate", 0),
             data.get("avg_rate", 0), data.get("median_rate", 0),
             data.get("sample_count", 0), data.get("project_entry_id", "")),
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error upserting rate benchmark: {e}")
        return False
    finally:
        conn.close()


def get_rate_benchmarks(trade_name: str | None = None) -> list[dict]:
    conn = _get_connection()
    try:
        query = "SELECT * FROM rate_benchmarks WHERE 1=1"
        params: list = []
        if trade_name:
            query += " AND trade_name = ?"
            params.append(trade_name)
        query += " ORDER BY trade_name, scope_keyword"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"Error reading rate benchmarks: {e}")
        return []
    finally:
        conn.close()


def clear_rate_benchmarks(project_entry_id: str = "") -> bool:
    conn = _get_connection()
    try:
        if project_entry_id:
            conn.execute("DELETE FROM rate_benchmarks WHERE project_entry_id = ?",
                        (project_entry_id,))
        else:
            conn.execute("DELETE FROM rate_benchmarks")
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


# =============================================================================
# Clause Library (learner knowledge table)
# =============================================================================


def upsert_clause(data: dict) -> bool:
    conn = _get_connection()
    try:
        conn.execute(
            """INSERT INTO clause_library
               (entry_id, clause_number, clause_title, clause_text,
                source_type, source_doc_path, source_commitment_ref,
                project_entry_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(entry_id) DO UPDATE SET
                clause_number = excluded.clause_number,
                clause_title = excluded.clause_title,
                clause_text = excluded.clause_text,
                source_type = excluded.source_type,
                source_doc_path = excluded.source_doc_path,
                source_commitment_ref = excluded.source_commitment_ref,
                project_entry_id = excluded.project_entry_id""",
            (data.get("entry_id", ""), data.get("clause_number", ""),
             data.get("clause_title", ""), data.get("clause_text", ""),
             data.get("source_type", "subcontract"),
             data.get("source_doc_path", ""),
             data.get("source_commitment_ref", ""),
             data.get("project_entry_id", "")),
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error upserting clause: {e}")
        return False
    finally:
        conn.close()


def get_clauses(source_commitment_ref: str | None = None,
                clause_number: str | None = None) -> list[dict]:
    conn = _get_connection()
    try:
        query = "SELECT * FROM clause_library WHERE 1=1"
        params: list = []
        if source_commitment_ref:
            query += " AND source_commitment_ref = ?"
            params.append(source_commitment_ref)
        if clause_number:
            query += " AND clause_number = ?"
            params.append(clause_number)
        query += " ORDER BY source_commitment_ref, CAST(clause_number AS INTEGER)"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"Error reading clauses: {e}")
        return []
    finally:
        conn.close()


def clear_clause_library(project_entry_id: str = "") -> bool:
    conn = _get_connection()
    try:
        if project_entry_id:
            conn.execute("DELETE FROM clause_library WHERE project_entry_id = ?",
                        (project_entry_id,))
        else:
            conn.execute("DELETE FROM clause_library")
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


# =============================================================================
# Competitive Sets (learner knowledge table)
# =============================================================================


def upsert_competitive_set(data: dict) -> bool:
    conn = _get_connection()
    try:
        conn.execute(
            """INSERT INTO competitive_sets
               (entry_id, trade_name, vendor_entry_ids, project_entry_id,
                quote_count, awarded_vendor_entry_id)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(entry_id) DO UPDATE SET
                trade_name = excluded.trade_name,
                vendor_entry_ids = excluded.vendor_entry_ids,
                project_entry_id = excluded.project_entry_id,
                quote_count = excluded.quote_count,
                awarded_vendor_entry_id = excluded.awarded_vendor_entry_id""",
            (data.get("entry_id", ""), data.get("trade_name", ""),
             data.get("vendor_entry_ids", "[]"),
             data.get("project_entry_id", ""),
             data.get("quote_count", 0),
             data.get("awarded_vendor_entry_id", "")),
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error upserting competitive set: {e}")
        return False
    finally:
        conn.close()


def get_competitive_sets(trade_name: str | None = None) -> list[dict]:
    conn = _get_connection()
    try:
        query = "SELECT * FROM competitive_sets WHERE 1=1"
        params: list = []
        if trade_name:
            query += " AND trade_name = ?"
            params.append(trade_name)
        query += " ORDER BY trade_name"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"Error reading competitive sets: {e}")
        return []
    finally:
        conn.close()


def clear_competitive_sets(project_entry_id: str = "") -> bool:
    conn = _get_connection()
    try:
        if project_entry_id:
            conn.execute("DELETE FROM competitive_sets WHERE project_entry_id = ?",
                        (project_entry_id,))
        else:
            conn.execute("DELETE FROM competitive_sets")
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()
