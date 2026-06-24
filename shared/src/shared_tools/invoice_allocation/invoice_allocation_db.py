"""Dedicated database for the Invoice Allocation module.

All allocation data lives in <LILAMY_DATA_DIR>/invoice_allocation.db — completely
separate from mail_history.db.

Tables: allocation_runs, allocation_records
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
DB_PATH = DATA_DIR / "invoice_allocation.db"


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


def init_invoice_allocation_db() -> None:
    """Create tables if they don't exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = _get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS allocation_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folder_path TEXT NOT NULL,
                total_files INTEGER NOT NULL DEFAULT 0,
                moved_count INTEGER NOT NULL DEFAULT 0,
                flagged_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                projects_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'in_progress',
                started_at TEXT DEFAULT (datetime('now')),
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS allocation_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES allocation_runs(id),
                filename TEXT NOT NULL,
                original_path TEXT NOT NULL,
                moved_to_path TEXT,
                target_project_code TEXT,
                target_project_name TEXT,
                match_method TEXT,
                confidence REAL DEFAULT 0.0,
                llm_reasoning TEXT,
                status TEXT NOT NULL DEFAULT 'moved',
                error_message TEXT,
                md5_hash TEXT,
                file_size_bytes INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                confirmed_at TEXT,
                undone_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_alloc_records_run
                ON allocation_records(run_id);
            CREATE INDEX IF NOT EXISTS idx_alloc_records_status
                ON allocation_records(status);
            CREATE INDEX IF NOT EXISTS idx_alloc_records_filename
                ON allocation_records(filename);
        """)
        conn.commit()
    finally:
        conn.close()


# =============================================================================
# Run CRUD
# =============================================================================


def create_run(folder_path: str, total_files: int, projects: list[str]) -> int:
    """Create a new allocation run record. Returns the run_id."""
    conn = _get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO allocation_runs (folder_path, total_files, projects_json, status) "
            "VALUES (?, ?, ?, 'in_progress')",
            (str(folder_path), total_files, json.dumps(projects)),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def finalize_run(
    run_id: int,
    moved_count: int = 0,
    pending_count: int = 0,
    no_match_count: int = 0,
    failed_count: int = 0,
    status: str = "completed",
) -> None:
    """Mark a run as completed with final counts."""
    conn = _get_connection()
    try:
        conn.execute(
            "UPDATE allocation_runs SET "
            "moved_count = ?, flagged_count = ?, failed_count = ?, "
            "status = ?, completed_at = datetime('now') "
            "WHERE id = ?",
            (moved_count, pending_count, failed_count, status, run_id),
        )
        conn.commit()
    finally:
        conn.close()


# =============================================================================
# Record CRUD
# =============================================================================


def record_allocation(
    run_id: int,
    filename: str,
    original_path: str,
    moved_to_path: str,
    target_project_code: str | None,
    target_project_name: str | None,
    match_method: str,
    confidence: float,
    status: str = "moved",
    llm_reasoning: str | None = None,
    md5_hash: str | None = None,
    file_size_bytes: int = 0,
) -> int:
    """Record a successful allocation move. Returns the record_id."""
    conn = _get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO allocation_records "
            "(run_id, filename, original_path, moved_to_path, target_project_code, "
            "target_project_name, match_method, confidence, status, llm_reasoning, "
            "md5_hash, file_size_bytes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id, filename, original_path, moved_to_path,
                target_project_code, target_project_name,
                match_method, confidence, status, llm_reasoning,
                md5_hash, file_size_bytes,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def record_error(
    run_id: int,
    filename: str,
    original_path: str,
    error_message: str,
    md5_hash: str | None = None,
) -> int:
    """Record a file that could not be processed. Returns the record_id."""
    conn = _get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO allocation_records "
            "(run_id, filename, original_path, match_method, confidence, status, "
            "error_message, md5_hash) "
            "VALUES (?, ?, ?, 'error', 0.0, 'error', ?, ?)",
            (run_id, filename, original_path, error_message, md5_hash),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def record_skip(
    run_id: int,
    filename: str,
    original_path: str,
    reason: str,
) -> int:
    """Record a file that was skipped. Returns the record_id."""
    conn = _get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO allocation_records "
            "(run_id, filename, original_path, match_method, confidence, status, "
            "error_message) "
            "VALUES (?, ?, ?, 'skip', 0.0, 'skipped', ?)",
            (run_id, filename, original_path, reason),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def record_pending(
    run_id: int,
    filename: str,
    original_path: str,
    target_project_code: str | None,
    target_project_name: str | None,
    match_method: str,
    confidence: float,
    llm_reasoning: str | None = None,
    md5_hash: str | None = None,
) -> int:
    """Record a low-confidence match awaiting user confirmation.

    The file stays at original_path until user confirms or declines.
    Returns the record_id.
    """
    conn = _get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO allocation_records "
            "(run_id, filename, original_path, moved_to_path, target_project_code, "
            "target_project_name, match_method, confidence, status, llm_reasoning, "
            "md5_hash) "
            "VALUES (?, ?, ?, NULL, ?, ?, ?, ?, 'pending_confirmation', ?, ?)",
            (
                run_id, filename, original_path,
                target_project_code, target_project_name,
                match_method, confidence, llm_reasoning, md5_hash,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def confirm_pending_move(
    record_id: int,
    moved_to_path: str,
    file_size_bytes: int = 0,
) -> bool:
    """Confirm a pending allocation — update record with move destination."""
    conn = _get_connection()
    try:
        cur = conn.execute(
            "UPDATE allocation_records SET "
            "status = 'confirmed', moved_to_path = ?, "
            "file_size_bytes = ?, confirmed_at = datetime('now') "
            "WHERE id = ? AND status = 'pending_confirmation'",
            (moved_to_path, file_size_bytes, record_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def record_no_match(
    run_id: int,
    filename: str,
    original_path: str,
) -> int:
    """Record a file with zero confidence — left in place, no action taken."""
    conn = _get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO allocation_records "
            "(run_id, filename, original_path, match_method, confidence, status) "
            "VALUES (?, ?, ?, 'none', 0.0, 'no_match')",
            (run_id, filename, original_path),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_history(limit: int = 50) -> list[dict]:
    """Return recent allocation records, newest first."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM allocation_records ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_record(record_id: int) -> dict | None:
    """Return a single allocation record by id."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM allocation_records WHERE id = ?", (record_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def confirm_allocation(record_id: int) -> bool:
    """Mark a flagged allocation as confirmed by user."""
    conn = _get_connection()
    try:
        cur = conn.execute(
            "UPDATE allocation_records SET status = 'confirmed', "
            "confirmed_at = datetime('now') WHERE id = ?",
            (record_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def set_record_status(
    record_id: int, status: str, undone_at: bool = False
) -> bool:
    """Update a record's status. Sets undone_at timestamp if undone=True."""
    conn = _get_connection()
    try:
        if undone_at:
            cur = conn.execute(
                "UPDATE allocation_records SET status = ?, "
                "undone_at = datetime('now') WHERE id = ?",
                (status, record_id),
            )
        else:
            cur = conn.execute(
                "UPDATE allocation_records SET status = ? WHERE id = ?",
                (status, record_id),
            )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_run_summary(run_id: int) -> dict | None:
    """Return summary of a single allocation run."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM allocation_runs WHERE id = ?", (run_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_run_records(run_id: int) -> list[dict]:
    """Return all records for a given run."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM allocation_records WHERE run_id = ? "
            "ORDER BY created_at",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
