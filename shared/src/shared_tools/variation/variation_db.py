"""Dedicated database for the Variations module.

All variation data lives in <LILAMY_DATA_DIR>/variations.db — completely
separate from mail_history.db. Only the PUSH function bridges DB → xlsx.

Tables: projects, variations, variation_items, variation_templates
"""
import json
import os
import sqlite3
from pathlib import Path


def _resolve_data_dir() -> Path:
    if "LILAMY_DATA_DIR" in os.environ:
        return Path(os.environ["LILAMY_DATA_DIR"])
    project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
    return project_root / "data"


DATA_DIR = _resolve_data_dir()
DB_PATH = DATA_DIR / "variations.db"


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


# =============================================================================
# Init + Migration
# =============================================================================


def init_variation_db() -> None:
    """Create tables if they don't exist. Migrate data from mail_history.db on first run."""
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
                base_contract_amount REAL DEFAULT 0,
                company_name TEXT DEFAULT 'Welink Construction',
                xlsx_path TEXT,
                source_type TEXT DEFAULT 'new',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_var_projects_name
                ON projects(name);

            CREATE TABLE IF NOT EXISTS variations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id TEXT UNIQUE NOT NULL,
                project_entry_id TEXT DEFAULT '',
                project_name TEXT NOT NULL DEFAULT '',
                project_location TEXT DEFAULT '',
                job_number TEXT DEFAULT '',
                base_contract_amount REAL DEFAULT 0,
                vo_number INTEGER,
                vo_title TEXT DEFAULT '',
                vo_type TEXT DEFAULT 'Head Contract VO',
                is_estimate INTEGER DEFAULT 0,
                date_issued TEXT,
                site_instruction_ref TEXT DEFAULT '',
                status TEXT DEFAULT 'draft',
                source_email_entry_id TEXT,
                bank_approved REAL DEFAULT 0,
                client_approved REAL DEFAULT 0,
                approved_value REAL DEFAULT 0,
                not_approved_value REAL DEFAULT 0,
                approval_type TEXT DEFAULT 'client',
                sort_order INTEGER DEFAULT 0,
                excel_path TEXT,
                pdf_path TEXT,
                onedrive_path TEXT,
                submitted_at TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_var_variations_status
                ON variations(status);
            CREATE INDEX IF NOT EXISTS idx_var_variations_project
                ON variations(project_entry_id);

            CREATE TABLE IF NOT EXISTS variation_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                variation_entry_id TEXT NOT NULL REFERENCES variations(entry_id),
                item_number INTEGER NOT NULL DEFAULT 1,
                description TEXT DEFAULT '',
                qty REAL DEFAULT 0,
                unit TEXT DEFAULT 'item',
                rate REAL DEFAULT 0,
                cost REAL DEFAULT 0,
                credit REAL DEFAULT 0,
                sort_order INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_var_items_variation
                ON variation_items(variation_entry_id);

            CREATE TABLE IF NOT EXISTS variation_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_name TEXT,
                template_path TEXT,
                mapping_json TEXT,
                is_default INTEGER DEFAULT 0
            );
        """)
        conn.commit()

        # Migrations for new columns
        _migrate_add_column(conn, "variations", "approved_value", "REAL DEFAULT 0")
        _migrate_add_column(conn, "variations", "not_approved_value", "REAL DEFAULT 0")
        _migrate_add_column(conn, "variations", "approval_type", "TEXT DEFAULT 'client'")
        _migrate_add_column(conn, "variations", "sort_order", "INTEGER DEFAULT 0")

        # Migrate existing data from mail_history.db → variations.db
        _migrate_from_shared_db(conn)

    finally:
        conn.close()


def _migrate_from_shared_db(conn) -> None:
    """Copy variation data from mail_history.db if it exists there and variations.db is empty."""
    from shared_tools.core.ipc_bridge import CREWAI_DIR as SHARED_DATA_DIR, DB_PATH as SHARED_DB_PATH

    shared_db = SHARED_DB_PATH
    if not shared_db.exists():
        return

    # Check if variations.db already has data
    row = conn.execute("SELECT COUNT(*) as cnt FROM projects").fetchone()
    if row["cnt"] > 0:
        return  # Already migrated

    # Check if shared db has variation tables
    try:
        sconn = sqlite3.connect(str(shared_db))
        sconn.row_factory = sqlite3.Row
        tables = [r[0] for r in sconn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('projects', 'variations', 'variation_items', 'variation_templates')"
        ).fetchall()]

        if "projects" not in tables:
            sconn.close()
            return  # No variation data to migrate

        # Copy projects
        for row in sconn.execute("SELECT * FROM projects").fetchall():
            d = dict(row)
            conn.execute(
                """INSERT OR IGNORE INTO projects
                   (entry_id, name, job_number, location, base_contract_amount,
                    company_name, xlsx_path, source_type, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (d.get("entry_id"), d.get("name", ""), d.get("job_number", ""),
                 d.get("location", ""), d.get("base_contract_amount", 0),
                 d.get("company_name", "Welink Construction"), d.get("xlsx_path", ""),
                 d.get("source_type", "new"), d.get("created_at"), d.get("updated_at")),
            )

        # Copy variations
        for row in sconn.execute("SELECT * FROM variations").fetchall():
            d = dict(row)
            conn.execute(
                """INSERT OR IGNORE INTO variations
                   (entry_id, project_entry_id, project_name, project_location,
                    job_number, base_contract_amount, vo_number, vo_title, vo_type,
                    is_estimate, date_issued, site_instruction_ref, status,
                    source_email_entry_id, bank_approved, client_approved,
                    excel_path, pdf_path, onedrive_path, submitted_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (d.get("entry_id"), d.get("project_entry_id", ""), d.get("project_name", ""),
                 d.get("project_location", ""), d.get("job_number", ""),
                 d.get("base_contract_amount", 0), d.get("vo_number"), d.get("vo_title", ""),
                 d.get("vo_type", "Head Contract VO"), d.get("is_estimate", 0),
                 d.get("date_issued"), d.get("site_instruction_ref", ""), d.get("status", "draft"),
                 d.get("source_email_entry_id"), d.get("bank_approved", 0),
                 d.get("client_approved", 0), d.get("excel_path"), d.get("pdf_path"),
                 d.get("onedrive_path"), d.get("submitted_at"), d.get("created_at"),
                 d.get("updated_at")),
            )

        # Copy variation_items
        for row in sconn.execute("SELECT * FROM variation_items").fetchall():
            d = dict(row)
            conn.execute(
                """INSERT OR IGNORE INTO variation_items
                   (id, variation_entry_id, item_number, description, qty, unit, rate, cost, credit, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (d.get("id"), d.get("variation_entry_id", ""), d.get("item_number", 1),
                 d.get("description", ""), d.get("qty", 0), d.get("unit", "item"),
                 d.get("rate", 0), d.get("cost", 0), d.get("credit", 0), d.get("sort_order", 0)),
            )

        # Copy variation_templates
        for row in sconn.execute("SELECT * FROM variation_templates").fetchall():
            d = dict(row)
            conn.execute(
                """INSERT OR IGNORE INTO variation_templates
                   (id, project_name, template_path, mapping_json, is_default)
                   VALUES (?, ?, ?, ?, ?)""",
                (d.get("id"), d.get("project_name", ""), d.get("template_path", ""),
                 d.get("mapping_json", "{}"), d.get("is_default", 0)),
            )

        conn.commit()

        # Drop variation tables from shared DB
        sconn.execute("DROP TABLE IF EXISTS variation_items")
        sconn.execute("DROP TABLE IF EXISTS variations")
        sconn.execute("DROP TABLE IF EXISTS variation_templates")
        sconn.execute("DROP TABLE IF EXISTS projects")
        sconn.commit()

        print("[variation_db] Migrated variation data from mail_history.db → variations.db")

        sconn.close()
    except Exception as e:
        print(f"[variation_db] Migration skipped: {e}")


# =============================================================================
# Projects
# =============================================================================


def upsert_project(data: dict) -> bool:
    conn = _get_connection()
    try:
        conn.execute(
            """INSERT INTO projects
               (entry_id, name, job_number, location, base_contract_amount,
                company_name, xlsx_path, source_type, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(entry_id) DO UPDATE SET
                name = excluded.name, job_number = excluded.job_number,
                location = excluded.location, base_contract_amount = excluded.base_contract_amount,
                company_name = excluded.company_name, xlsx_path = excluded.xlsx_path,
                source_type = excluded.source_type, updated_at = datetime('now')""",
            (data.get("entry_id", ""), data.get("name", ""), data.get("job_number", ""),
             data.get("location", ""), data.get("base_contract_amount", 0),
             data.get("company_name", "Welink Construction"), data.get("xlsx_path", ""),
             data.get("source_type", "new")),
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
        conn.execute(f"UPDATE projects SET {set_clause}, updated_at = datetime('now') WHERE entry_id = ?", values)
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
        conn.execute("DELETE FROM variation_items WHERE variation_entry_id IN (SELECT entry_id FROM variations WHERE project_entry_id = ?)", (entry_id,))
        conn.execute("DELETE FROM variations WHERE project_entry_id = ?", (entry_id,))
        conn.execute("DELETE FROM projects WHERE entry_id = ?", (entry_id,))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error deleting project: {e}")
        return False
    finally:
        conn.close()


def get_project_vo_count(entry_id: str) -> int:
    conn = _get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM variations WHERE project_entry_id = ?", (entry_id,)).fetchone()
        return row["cnt"] if row else 0
    except Exception:
        return 0
    finally:
        conn.close()


# =============================================================================
# Variations
# =============================================================================


def upsert_variation(data: dict) -> bool:
    conn = _get_connection()
    try:
        conn.execute(
            """INSERT INTO variations
               (entry_id, project_entry_id, project_name, project_location, job_number,
                base_contract_amount, vo_number, vo_title, vo_type, is_estimate,
                date_issued, site_instruction_ref, status, source_email_entry_id,
                bank_approved, client_approved, approved_value, not_approved_value, approval_type, sort_order,
                excel_path, pdf_path, onedrive_path, submitted_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(entry_id) DO UPDATE SET
                project_entry_id = excluded.project_entry_id,
                project_name = excluded.project_name,
                project_location = excluded.project_location,
                job_number = excluded.job_number,
                base_contract_amount = excluded.base_contract_amount,
                vo_number = excluded.vo_number, vo_title = excluded.vo_title,
                vo_type = excluded.vo_type, is_estimate = excluded.is_estimate,
                date_issued = excluded.date_issued, site_instruction_ref = excluded.site_instruction_ref,
                status = excluded.status, source_email_entry_id = excluded.source_email_entry_id,
                bank_approved = excluded.bank_approved, client_approved = excluded.client_approved,
                approved_value = excluded.approved_value, not_approved_value = excluded.not_approved_value,
                approval_type = excluded.approval_type,
                sort_order = excluded.sort_order,
                excel_path = excluded.excel_path, pdf_path = excluded.pdf_path,
                onedrive_path = excluded.onedrive_path, submitted_at = excluded.submitted_at,
                updated_at = datetime('now')""",
            (data.get("entry_id", ""), data.get("project_entry_id", ""),
             data.get("project_name", ""), data.get("project_location", ""),
             data.get("job_number", ""), data.get("base_contract_amount", 0),
             data.get("vo_number"), data.get("vo_title", ""),
             data.get("vo_type", "Head Contract VO"), data.get("is_estimate", 0),
             data.get("date_issued"), data.get("site_instruction_ref", ""),
             data.get("status", "draft"), data.get("source_email_entry_id"),
             data.get("bank_approved", 0), data.get("client_approved", 0),
             data.get("approved_value", 0), data.get("not_approved_value", 0),
             data.get("approval_type", "client"),
             data.get("sort_order", 0),
             data.get("excel_path"), data.get("pdf_path"),
             data.get("onedrive_path"), data.get("submitted_at")),
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error upserting variation: {e}")
        return False
    finally:
        conn.close()


def get_variation(entry_id: str) -> dict | None:
    conn = _get_connection()
    try:
        row = conn.execute("SELECT * FROM variations WHERE entry_id = ?", (entry_id,)).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def get_variations(project_entry_id: str | None = None, project: str | None = None,
                   status: str | None = None, limit: int = 0) -> list[dict]:
    conn = _get_connection()
    try:
        query = "SELECT * FROM variations WHERE 1=1"
        params: list = []
        if project_entry_id:
            query += " AND project_entry_id = ?"
            params.append(project_entry_id)
        if project:
            query += " AND project_name = ?"
            params.append(project)
        if status:
            query += " AND status = ?"
            params.append(status)
        else:
            query += " AND status != 'void'"
        query += " ORDER BY sort_order DESC, vo_number DESC"
        if limit > 0:
            query += " LIMIT ?"
            params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"Error reading variations: {e}")
        return []
    finally:
        conn.close()


def update_variation(entry_id: str, **fields) -> bool:
    if not fields:
        return False
    conn = _get_connection()
    try:
        set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
        values = list(fields.values()) + [entry_id]
        conn.execute(f"UPDATE variations SET {set_clause}, updated_at = datetime('now') WHERE entry_id = ?", values)
        conn.commit()
        return True
    except Exception as e:
        print(f"Error updating variation: {e}")
        return False
    finally:
        conn.close()


def delete_variation(entry_id: str) -> bool:
    conn = _get_connection()
    try:
        conn.execute("UPDATE variations SET status = 'void', updated_at = datetime('now') WHERE entry_id = ?", (entry_id,))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def restore_variation(entry_id: str) -> bool:
    conn = _get_connection()
    try:
        conn.execute("UPDATE variations SET status = 'draft', updated_at = datetime('now') WHERE entry_id = ?", (entry_id,))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def reorder_variations(ordered_ids: list[str]) -> bool:
    """Update sort_order for a list of variation entry_ids based on list position."""
    conn = _get_connection()
    try:
        n = len(ordered_ids)
        for idx, entry_id in enumerate(ordered_ids):
            # First in list → highest sort_order (appears at top with DESC)
            sort_order = n - 1 - idx
            conn.execute(
                "UPDATE variations SET sort_order = ? WHERE entry_id = ?",
                (sort_order, entry_id),
            )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error reordering variations: {e}")
        return False
    finally:
        conn.close()


def hard_delete_variation(entry_id: str) -> bool:
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM variation_items WHERE variation_entry_id = ?", (entry_id,))
        conn.execute("DELETE FROM variations WHERE entry_id = ?", (entry_id,))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


# =============================================================================
# Variation Items
# =============================================================================


def upsert_variation_item(data: dict) -> int | None:
    """Insert or update a variation line item.

    For UPDATE: only sets fields that are present in `data` — does NOT
    overwrite unprovided fields with defaults. This is critical for PATCH
    semantics: sending {rate: 500, cost: 1000} must not reset qty to 0.
    """
    conn = _get_connection()
    try:
        item_id = data.get("id")
        if item_id:
            # Build dynamic SET clause — only update fields present in data
            updatable = ["item_number", "description", "qty", "unit",
                         "rate", "cost", "credit", "sort_order"]
            set_parts = []
            values = []
            for col in updatable:
                if col in data:
                    set_parts.append(f"{col} = ?")
                    values.append(data[col])
            if not set_parts:
                return item_id  # nothing to update
            values.append(item_id)
            conn.execute(
                f"UPDATE variation_items SET {', '.join(set_parts)} WHERE id = ?",
                values,
            )
            conn.commit()
            return item_id
        else:
            cursor = conn.execute(
                """INSERT INTO variation_items
                   (variation_entry_id, item_number, description, qty, unit, rate, cost, credit, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (data.get("variation_entry_id", ""), data.get("item_number", 1),
                 data.get("description", ""), data.get("qty", 0), data.get("unit", "item"),
                 data.get("rate", 0), data.get("cost", 0), data.get("credit", 0),
                 data.get("sort_order", 0)),
            )
            conn.commit()
            return cursor.lastrowid
    except Exception as e:
        print(f"Error upserting variation item: {e}")
        return None
    finally:
        conn.close()


def get_variation_items(variation_entry_id: str) -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM variation_items WHERE variation_entry_id = ? ORDER BY sort_order, item_number",
            (variation_entry_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"Error reading variation items: {e}")
        return []
    finally:
        conn.close()


def delete_variation_item(item_id: int) -> bool:
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM variation_items WHERE id = ?", (item_id,))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def reorder_variation_items(variation_entry_id: str, item_ids: list[int]) -> bool:
    conn = _get_connection()
    try:
        for idx, item_id in enumerate(item_ids):
            conn.execute(
                "UPDATE variation_items SET sort_order = ? WHERE id = ? AND variation_entry_id = ?",
                (idx, item_id, variation_entry_id),
            )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error reordering items: {e}")
        return False
    finally:
        conn.close()


# =============================================================================
# Variation Templates
# =============================================================================


def get_template_mapping(project_name: str | None = None) -> dict | None:
    conn = _get_connection()
    try:
        if project_name:
            row = conn.execute("SELECT * FROM variation_templates WHERE project_name = ? LIMIT 1", (project_name,)).fetchone()
        else:
            row = conn.execute("SELECT * FROM variation_templates WHERE is_default = 1 LIMIT 1").fetchone()
        if row:
            d = dict(row)
            if d.get("mapping_json"):
                d["mapping"] = json.loads(d["mapping_json"])
            return d
        return None
    except Exception as e:
        print(f"Error reading template mapping: {e}")
        return None
    finally:
        conn.close()


def upsert_template_mapping(data: dict) -> bool:
    conn = _get_connection()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO variation_templates
               (id, project_name, template_path, mapping_json, is_default)
               VALUES (?, ?, ?, ?, ?)""",
            (data.get("id"), data.get("project_name", ""), data.get("template_path", ""),
             data.get("mapping_json", "{}"), data.get("is_default", 0)),
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error upserting template mapping: {e}")
        return False
    finally:
        conn.close()
