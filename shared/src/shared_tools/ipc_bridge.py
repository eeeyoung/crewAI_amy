"""Inter-App Communication bridge for the lilAmy platform.

Provides:
  - Lock-file presence detection so agents can see each other's running status.
  - Shared SQLite database at ~/.crewai/shared_data.db for data exchange.

No networking — everything goes through the filesystem.
"""
import json
import os
import platform
import sqlite3
import time
from pathlib import Path

CREWAI_DIR = Path.home() / ".crewai"
DB_PATH = CREWAI_DIR / "shared_data.db"


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
        """)
        conn.commit()
    finally:
        conn.close()


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
