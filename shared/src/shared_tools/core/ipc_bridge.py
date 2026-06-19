"""Inter-App Communication bridge for the lilAmy platform.

Provides:
  - Lock-file presence detection so agents can see each other's running status.
  - Shared SQLite database at <LILAMY_DATA_DIR>/mail_history.db for data exchange.

No networking — everything goes through the filesystem.

Data directory resolution (in order):
  1. LILAMY_DATA_DIR env var (if set)
  2. <project_root>/data/ (default — gitignored)
"""
import json
import os
import platform
import sqlite3
import time
from pathlib import Path


def _resolve_data_dir() -> Path:
    """Resolve the data directory from env var or fall back to project_root/data."""
    if "LILAMY_DATA_DIR" in os.environ:
        return Path(os.environ["LILAMY_DATA_DIR"])
    # ipc_bridge.py is at shared/src/shared_tools/core/ipc_bridge.py → 5 levels up to project root
    project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
    return project_root / "data"


CREWAI_DIR = _resolve_data_dir()
DB_PATH = CREWAI_DIR / "mail_history.db"


# =============================================================================
# Helpers
# =============================================================================

def _get_connection() -> sqlite3.Connection:
    """Get a new SQLite connection to the shared database."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _is_pid_alive(pid: int) -> bool:
    """Check whether a process with the given PID is still running."""
    if platform.system() == "Windows":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x0400, False, pid)  # PROCESS_QUERY_INFORMATION
            if not handle:
                return False
            kernel32.CloseHandle(handle)
            return True
        except Exception:
            return True   # fallback: trust the lock file
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


# =============================================================================
# Presence — lock files
# =============================================================================

def register_app(app_name: str) -> dict:
    """Write a lock file with PID, timestamp, and status. Returns lock info dict."""
    CREWAI_DIR.mkdir(parents=True, exist_ok=True)
    lock_info = {
        "app_name": app_name,
        "pid": os.getpid(),
        "timestamp": time.time(),
        "status": "running",
    }
    lock_path = CREWAI_DIR / f"{app_name}.lock"
    with open(lock_path, "w") as f:
        json.dump(lock_info, f)
    return lock_info


def unregister_app(app_name: str) -> None:
    """Remove the lock file on clean shutdown."""
    lock_path = CREWAI_DIR / f"{app_name}.lock"
    try:
        lock_path.unlink(missing_ok=True)
    except Exception:
        pass


def get_app_status(app_name: str) -> dict | None:
    """Read another app's lock file. Returns None if not running or stale lock."""
    lock_path = CREWAI_DIR / f"{app_name}.lock"
    if not lock_path.exists():
        return None
    try:
        with open(lock_path, "r") as f:
            info = json.load(f)
    except Exception:
        return None

    pid = info.get("pid")
    if pid and not _is_pid_alive(pid):
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            pass
        return None
    return info


# =============================================================================
# Shared Database
# =============================================================================

def init_shared_db() -> None:
    """Create tables if they don't exist. Idempotent, safe to call on every startup."""
    CREWAI_DIR.mkdir(parents=True, exist_ok=True)
    conn = _get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS categorized_emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email_entry_id TEXT UNIQUE NOT NULL,
                email_subject TEXT,
                email_sender TEXT,
                email_body TEXT,
                category TEXT,
                urgency TEXT,
                extra_info TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                consumed_by_acalendar INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS calendar_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_email_entry_id TEXT,
                source_email_subject TEXT,
                source_email_sender TEXT,
                description TEXT NOT NULL,
                date_type TEXT NOT NULL,
                start_date TEXT,
                end_date TEXT,
                confidence REAL DEFAULT 1.0,
                project TEXT,
                outlook_event_id TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS conflicts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id_1 INTEGER REFERENCES calendar_events(id),
                event_id_2 INTEGER REFERENCES calendar_events(id),
                conflict_type TEXT,
                resolved INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS weekly_digests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_start TEXT NOT NULL,
                events_json TEXT,
                sent_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS processed_emails (
                entry_id TEXT PRIMARY KEY,
                subject TEXT,
                sender TEXT,
                received_time TEXT,
                body TEXT,
                category TEXT,
                urgency TEXT,
                chinese_summary TEXT,
                assignee TEXT,
                todos_json TEXT,
                deadlines_json TEXT,
                reply_draft TEXT,
                status TEXT DEFAULT 'active',
                processed_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_processed_emails_status
                ON processed_emails(status);
            CREATE INDEX IF NOT EXISTS idx_processed_emails_received
                ON processed_emails(received_time DESC);

            CREATE TABLE IF NOT EXISTS todo_items (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id        TEXT UNIQUE NOT NULL,
                source_email_id TEXT,
                description     TEXT NOT NULL,
                category        TEXT DEFAULT 'General',
                urgency         TEXT DEFAULT 'low',
                assignee        TEXT DEFAULT '',
                status          TEXT DEFAULT 'pending',
                deadline_date   TEXT,
                deadline_time   TEXT,
                deadline_type   TEXT DEFAULT 'tbd',
                project         TEXT DEFAULT '',
                created_at      TEXT DEFAULT (datetime('now')),
                updated_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_todo_items_status
                ON todo_items(status);
            CREATE INDEX IF NOT EXISTS idx_todo_items_source
                ON todo_items(source_email_id);
            CREATE INDEX IF NOT EXISTS idx_todo_items_deadline
                ON todo_items(deadline_date);
        """)
        conn.commit()

        # ── Migrations: add columns/tables that may not exist in older DBs ──
        _migrate_add_column(conn, "processed_emails", "deadlines_json", "TEXT")
        _migrate_add_column(conn, "todo_items", "deadline_time", "TEXT")

    finally:
        conn.close()


def _migrate_add_column(conn, table: str, column: str, col_type: str) -> bool:
    """Add a column to a table if it doesn't already exist. Returns True if added."""
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        conn.commit()
        return True
    return False


# =============================================================================
# Categorized Emails — written by AMail, read by ACalendar
# =============================================================================

def push_categorized_email(email_data: dict) -> int | None:
    """AMail calls this after categorizing an email. Inserts into categorized_emails. Returns row id."""
    conn = _get_connection()
    try:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO categorized_emails
               (email_entry_id, email_subject, email_sender, email_body,
                category, urgency, extra_info)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                email_data.get("email_entry_id", ""),
                email_data.get("email_subject", ""),
                email_data.get("email_sender", ""),
                email_data.get("email_body", ""),
                email_data.get("category", ""),
                email_data.get("urgency", ""),
                email_data.get("extra_info", ""),
            ),
        )
        conn.commit()
        return cursor.lastrowid
    except Exception as e:
        print(f"Error pushing categorized email: {e}")
        return None
    finally:
        conn.close()


def pull_new_categorized_emails() -> list[dict]:
    """ACalendar calls this. Returns unconsumed categorized emails (consumed_by_acalendar=0)."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM categorized_emails WHERE consumed_by_acalendar = 0 ORDER BY created_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"Error pulling categorized emails: {e}")
        return []
    finally:
        conn.close()


def mark_email_consumed(email_entry_id: str) -> None:
    """ACalendar calls this after processing a categorized email."""
    conn = _get_connection()
    try:
        conn.execute(
            "UPDATE categorized_emails SET consumed_by_acalendar = 1 WHERE email_entry_id = ?",
            (email_entry_id,),
        )
        conn.commit()
    except Exception as e:
        print(f"Error marking email consumed: {e}")
    finally:
        conn.close()


# =============================================================================
# Calendar Events — written by ACalendar, read by AMail
# =============================================================================

def push_calendar_events(events: list[dict]) -> list[int]:
    """ACalendar calls this after extracting dates. Inserts rows into calendar_events.
    Returns list of row ids for the inserted rows."""
    conn = _get_connection()
    ids = []
    try:
        for event in events:
            cursor = conn.execute(
                """INSERT INTO calendar_events
                   (source_email_entry_id, source_email_subject, source_email_sender,
                    description, date_type, start_date, end_date, confidence, project, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.get("source_email_entry_id", ""),
                    event.get("source_email_subject", ""),
                    event.get("source_email_sender", ""),
                    event.get("description", ""),
                    event.get("date_type", ""),
                    event.get("start_date"),
                    event.get("end_date"),
                    event.get("confidence", 1.0),
                    event.get("project", ""),
                    event.get("status", "pending"),
                ),
            )
            ids.append(cursor.lastrowid)
        conn.commit()
    except Exception as e:
        print(f"Error pushing calendar events: {e}")
    finally:
        conn.close()
    return ids


def pull_calendar_events(
    project: str | None = None,
    date_type: str | None = None,
    status: str | None = None,
) -> list[dict]:
    """AMail calls this when composing replies. Returns filtered events."""
    conn = _get_connection()
    try:
        query = "SELECT * FROM calendar_events WHERE 1=1"
        params: list = []
        if project:
            query += " AND project = ?"
            params.append(project)
        if date_type:
            query += " AND date_type = ?"
            params.append(date_type)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY start_date ASC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"Error pulling calendar events: {e}")
        return []
    finally:
        conn.close()


def update_calendar_event_db(event_id: int, **kwargs) -> bool:
    """Update fields of a calendar event in the shared DB (not Outlook).
    Automatically sets updated_at to now."""
    conn = _get_connection()
    try:
        set_clause = ", ".join(f"{k} = ?" for k in kwargs.keys())
        values = list(kwargs.values()) + [event_id]
        conn.execute(
            f"UPDATE calendar_events SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
            values,
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error updating calendar event in DB: {e}")
        return False
    finally:
        conn.close()


# =============================================================================
# Conflicts
# =============================================================================

def push_conflict(event_id_1: int, event_id_2: int, conflict_type: str) -> int | None:
    """ACalendar calls this when a conflict is detected. Returns row id."""
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO conflicts (event_id_1, event_id_2, conflict_type) VALUES (?, ?, ?)",
            (event_id_1, event_id_2, conflict_type),
        )
        conn.commit()
        return cursor.lastrowid
    except Exception as e:
        print(f"Error pushing conflict: {e}")
        return None
    finally:
        conn.close()


def pull_conflicts(resolved: bool = False) -> list[dict]:
    """Pull conflicts, optionally filtered by resolved status."""
    conn = _get_connection()
    try:
        resolved_int = 1 if resolved else 0
        rows = conn.execute(
            "SELECT * FROM conflicts WHERE resolved = ? ORDER BY id ASC",
            (resolved_int,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"Error pulling conflicts: {e}")
        return []
    finally:
        conn.close()


def resolve_conflict(conflict_id: int) -> bool:
    """Mark a conflict as resolved."""
    conn = _get_connection()
    try:
        conn.execute("UPDATE conflicts SET resolved = 1 WHERE id = ?", (conflict_id,))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error resolving conflict: {e}")
        return False
    finally:
        conn.close()


# =============================================================================
# Processed Emails — the unified AMail store
# =============================================================================

def upsert_processed_email(email_data: dict) -> bool:
    """Insert or update a processed email in the unified store.
    Used by MailService after single-pass summarization.
    Returns True on success."""
    conn = _get_connection()
    try:
        conn.execute(
            """INSERT INTO processed_emails
               (entry_id, subject, sender, received_time, body, category, urgency,
                chinese_summary, assignee, todos_json, deadlines_json, reply_draft, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(entry_id) DO UPDATE SET
                subject = excluded.subject,
                sender = excluded.sender,
                received_time = excluded.received_time,
                body = excluded.body,
                category = excluded.category,
                urgency = excluded.urgency,
                chinese_summary = excluded.chinese_summary,
                assignee = excluded.assignee,
                todos_json = excluded.todos_json,
                deadlines_json = excluded.deadlines_json,
                reply_draft = excluded.reply_draft,
                status = excluded.status,
                updated_at = datetime('now')""",
            (
                email_data.get("entry_id", ""),
                email_data.get("subject", ""),
                email_data.get("sender", ""),
                email_data.get("received_time", ""),
                email_data.get("body", ""),
                email_data.get("category", ""),
                email_data.get("urgency", ""),
                email_data.get("chinese_summary", ""),
                email_data.get("assignee", ""),
                email_data.get("todos_json", "[]"),
                email_data.get("deadlines_json", "[]"),
                email_data.get("reply_draft", ""),
                email_data.get("status", "active"),
            ),
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error upserting processed email: {e}")
        return False
    finally:
        conn.close()


def get_processed_emails(status: str = "active", limit: int = 0) -> list[dict]:
    """Return processed emails from the unified store, newest first.
    Falls back to categorized_emails for body if not stored locally.
    Set limit=0 for unlimited."""
    conn = _get_connection()
    try:
        if limit > 0:
            rows = conn.execute(
                """SELECT pe.*,
                          COALESCE(pe.body, ce.email_body, '') AS body
                   FROM processed_emails pe
                   LEFT JOIN categorized_emails ce
                     ON pe.entry_id = ce.email_entry_id
                   WHERE pe.status = ?
                   ORDER BY pe.received_time DESC
                   LIMIT ?""",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT pe.*,
                          COALESCE(pe.body, ce.email_body, '') AS body
                   FROM processed_emails pe
                   LEFT JOIN categorized_emails ce
                     ON pe.entry_id = ce.email_entry_id
                   WHERE pe.status = ?
                   ORDER BY pe.received_time DESC""",
                (status,),
            ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            # Ensure body is set from the COALESCE
            if not d.get("body"):
                d["body"] = r["body"] if r["body"] else ""
            results.append(d)
        return results
    except Exception as e:
        print(f"Error reading processed emails: {e}")
        return []
    finally:
        conn.close()


def get_processed_entry_ids() -> set[str]:
    """Return the set of active EntryIDs (for dedup).
    Removed emails are excluded so they can be re-fetched."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT entry_id FROM processed_emails WHERE status = 'active'"
        ).fetchall()
        return {r["entry_id"] for r in rows}
    except Exception:
        return set()
    finally:
        conn.close()


def get_latest_received_time() -> str | None:
    """Return the received_time of the newest ACTIVE processed email, or None.
    Excludes removed emails so re-fetching can fill gaps after removals."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT received_time FROM processed_emails WHERE status = 'active' ORDER BY received_time DESC LIMIT 1"
        ).fetchone()
        return row["received_time"] if row else None
    except Exception:
        return None
    finally:
        conn.close()


def get_earliest_received_time() -> str | None:
    """Return the received_time of the oldest processed email, or None."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT received_time FROM processed_emails WHERE status = 'active' ORDER BY received_time ASC LIMIT 1"
        ).fetchone()
        return row["received_time"] if row else None
    except Exception:
        return None
    finally:
        conn.close()


def get_active_entry_ids_in_range(since: str, until: str) -> set[str]:
    """Return the set of active processed email EntryIDs in a date range."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT entry_id FROM processed_emails WHERE status = 'active' AND received_time >= ? AND received_time <= ?",
            (since, until),
        ).fetchall()
        return {r["entry_id"] for r in rows}
    except Exception:
        return set()
    finally:
        conn.close()


def remove_processed_email(entry_id: str) -> bool:
    """Soft-delete: mark status = 'removed'."""
    conn = _get_connection()
    try:
        conn.execute(
            "UPDATE processed_emails SET status = 'removed', updated_at = datetime('now') WHERE entry_id = ?",
            (entry_id,),
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def get_processed_email(entry_id: str) -> dict | None:
    """Return a single processed email by EntryID, or None."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM processed_emails WHERE entry_id = ?", (entry_id,)
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


# =============================================================================
# To-Do Items — unified task/action-item store
# =============================================================================

def upsert_todo_item(data: dict) -> bool:
    """Insert or replace a to-do item by entry_id (UUID). Returns True on success."""
    conn = _get_connection()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO todo_items
               (entry_id, source_email_id, description, category, urgency, assignee,
                status, deadline_date, deadline_time, deadline_type, project, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                data.get("entry_id", ""),
                data.get("source_email_id"),
                data.get("description", ""),
                data.get("category", "General"),
                data.get("urgency", "low"),
                data.get("assignee", ""),
                data.get("status", "pending"),
                data.get("deadline_date"),
                data.get("deadline_time"),
                data.get("deadline_type", "tbd"),
                data.get("project", ""),
            ),
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error upserting todo item: {e}")
        return False
    finally:
        conn.close()


def get_todo_items(status: str | None = None, limit: int = 0) -> list[dict]:
    """Return to-do items, newest first. Optional status filter (pending/done/cancelled).
    When status is None (All), excludes cancelled items (trash).
    Set status='cancelled' to see trash. Set limit=0 for unlimited."""
    conn = _get_connection()
    try:
        if status:
            if limit > 0:
                rows = conn.execute(
                    "SELECT * FROM todo_items WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM todo_items WHERE status = ? ORDER BY created_at DESC",
                    (status,),
                ).fetchall()
        else:
            # "All" = pending + done only (exclude cancelled/trash)
            if limit > 0:
                rows = conn.execute(
                    "SELECT * FROM todo_items WHERE status != 'cancelled' ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM todo_items WHERE status != 'cancelled' ORDER BY created_at DESC"
                ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"Error reading todo items: {e}")
        return []
    finally:
        conn.close()


def get_todo_item(entry_id: str) -> dict | None:
    """Return a single to-do item by its UUID entry_id, or None."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM todo_items WHERE entry_id = ?", (entry_id,)
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def update_todo_item(entry_id: str, **fields) -> bool:
    """Update fields of a to-do item by entry_id.
    Automatically sets updated_at to now. Returns True on success."""
    if not fields:
        return False
    conn = _get_connection()
    try:
        set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
        values = list(fields.values()) + [entry_id]
        conn.execute(
            f"UPDATE todo_items SET {set_clause}, updated_at = datetime('now') WHERE entry_id = ?",
            values,
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error updating todo item: {e}")
        return False
    finally:
        conn.close()


def delete_todo_item(entry_id: str) -> bool:
    """Soft-delete a to-do item: set status = 'cancelled' (moves to trash)."""
    conn = _get_connection()
    try:
        conn.execute(
            "UPDATE todo_items SET status = 'cancelled', updated_at = datetime('now') WHERE entry_id = ?",
            (entry_id,),
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def hard_delete_todo_item(entry_id: str) -> bool:
    """Permanently delete a to-do item row from the database."""
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM todo_items WHERE entry_id = ?", (entry_id,))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def restore_todo_item(entry_id: str) -> bool:
    """Restore a cancelled to-do item back to pending status."""
    conn = _get_connection()
    try:
        conn.execute(
            "UPDATE todo_items SET status = 'pending', updated_at = datetime('now') WHERE entry_id = ?",
            (entry_id,),
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()

