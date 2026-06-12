"""FileRegistry — SQLite relational core for the hybrid document store.

Part of the 4-layer architecture (Layer 1: Hybrid Data Repository).
Tracks every file in the project data root with content hashing for
incremental ingestion.  ChromaDB chunk metadata links back via ``file_id``.

Schema
------
  CREATE TABLE file_registry (
      file_id       INTEGER PRIMARY KEY AUTOINCREMENT,
      project       TEXT NOT NULL,          -- detected from top-level subdir
      rel_path      TEXT NOT NULL,          -- relative to data root
      file_name     TEXT NOT NULL,
      file_type     TEXT NOT NULL,          -- pdf, docx, xlsx, txt, eml, ...
      md5_hash      TEXT NOT NULL,          -- content hash for change detection
      file_size_bytes INTEGER NOT NULL,
      last_modified TEXT,                   -- ISO-8601 from filesystem
      chunk_count   INTEGER DEFAULT 0,      -- populated after embedding
      last_indexed_at TEXT,                 -- ISO-8601 timestamp
      status        TEXT DEFAULT 'new',     -- new | indexed | modified | error
      error_message TEXT,
      metadata_json TEXT DEFAULT '{}',      -- extensible JSON bag
      UNIQUE(project, rel_path)
  );
"""

import os
import hashlib
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path


class FileRegistry:
    """SQLite-backed file tracker for incremental document ingestion.

    Usage::

        reg = FileRegistry("/path/to/LILAMY_DATA_DIR")
        reg.init_db()
        file_id = reg.register("ARCO", "contracts/main.pdf", md5_hash, 1024)
        reg.mark_indexed(file_id, chunk_count=5)
        unchanged = reg.get_unchanged_files("ARCO")  # files whose hash matches
    """

    DB_FILENAME = ".lilamy_registry.db"

    def __init__(self, data_root: str | Path):
        self._data_root = Path(data_root)
        self._db_path = self._data_root / self.DB_FILENAME
        self._conn: sqlite3.Connection | None = None

    # ── lifecycle ──────────────────────────────────────────────────────

    @property
    def db_path(self) -> Path:
        return self._db_path

    def init_db(self) -> None:
        """Create schema if it doesn't exist."""
        self._ensure_connected()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA_SQL)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def _ensure_connected(self) -> None:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")

    # ── project CRUD ───────────────────────────────────────────────────

    def list_projects(self) -> list[str]:
        """Return all known project names, newest first."""
        self._ensure_connected()
        rows = self._conn.execute(
            "SELECT DISTINCT project FROM file_registry ORDER BY project"
        ).fetchall()
        return [r["project"] for r in rows]

    def project_stats(self, project: str) -> dict:
        """Return file count, total size, and chunk count for a project."""
        self._ensure_connected()
        row = self._conn.execute(
            """SELECT COUNT(*) AS file_count,
                      COALESCE(SUM(file_size_bytes), 0) AS total_bytes,
                      COALESCE(SUM(chunk_count), 0) AS total_chunks
               FROM file_registry WHERE project = ?""",
            (project,),
        ).fetchone()
        return {
            "project": project,
            "file_count": row["file_count"],
            "total_bytes": row["total_bytes"],
            "total_chunks": row["total_chunks"],
        }

    def stats(self) -> dict:
        """Global registry statistics."""
        self._ensure_connected()
        row = self._conn.execute(
            """SELECT COUNT(*) AS total_files,
                      COALESCE(SUM(file_size_bytes), 0) AS total_bytes,
                      COALESCE(SUM(chunk_count), 0) AS total_chunks
               FROM file_registry"""
        ).fetchone()
        return {
            "total_files": row["total_files"],
            "total_bytes": row["total_bytes"],
            "total_chunks": row["total_chunks"],
            "projects": self.list_projects(),
        }

    # ── file registration ──────────────────────────────────────────────

    def register(
        self,
        project: str,
        rel_path: str,
        md5_hash: str,
        file_size_bytes: int,
        file_type: str = "other",
        last_modified: str | None = None,
        metadata_json: str = "{}",
    ) -> int:
        """Insert or update a file record. Returns ``file_id``.

        If a record with the same (project, rel_path) already exists, its
        hash and metadata are updated and status is set to 'new' (meaning
        it needs re-indexing).  Otherwise a new row is inserted.
        """
        self._ensure_connected()
        now = datetime.now(timezone.utc).isoformat()
        existing = self._conn.execute(
            "SELECT file_id, md5_hash FROM file_registry WHERE project=? AND rel_path=?",
            (project, rel_path),
        ).fetchone()

        if existing:
            # File path known — has the content changed?
            if existing["md5_hash"] == md5_hash:
                # Unchanged — just touch last_indexed_at, keep status
                self._conn.execute(
                    "UPDATE file_registry SET last_indexed_at=? WHERE file_id=?",
                    (now, existing["file_id"]),
                )
                self._conn.commit()
                return existing["file_id"]
            else:
                # Content changed — reset to 'new'
                self._conn.execute(
                    """UPDATE file_registry
                       SET md5_hash=?, file_size_bytes=?, last_modified=?,
                           last_indexed_at=?, status='new', error_message=NULL
                       WHERE file_id=?""",
                    (md5_hash, file_size_bytes, last_modified, now, existing["file_id"]),
                )
                self._conn.commit()
                return existing["file_id"]

        # New file
        cur = self._conn.execute(
            """INSERT INTO file_registry
               (project, rel_path, file_name, file_type, md5_hash,
                file_size_bytes, last_modified, last_indexed_at, status, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)""",
            (
                project,
                rel_path,
                Path(rel_path).name,
                file_type,
                md5_hash,
                file_size_bytes,
                last_modified,
                now,
                metadata_json,
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    def mark_indexed(self, file_id: int, chunk_count: int = 0) -> None:
        """Mark a file as successfully indexed with its chunk count."""
        self._ensure_connected()
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """UPDATE file_registry
               SET status='indexed', chunk_count=?, last_indexed_at=?
               WHERE file_id=?""",
            (chunk_count, now, file_id),
        )
        self._conn.commit()

    def mark_error(self, file_id: int, error_message: str) -> None:
        """Record an ingestion error for this file."""
        self._ensure_connected()
        self._conn.execute(
            "UPDATE file_registry SET status='error', error_message=? WHERE file_id=?",
            (error_message, file_id),
        )
        self._conn.commit()

    # ── incremental queries ─────────────────────────────────────────────

    def get_files_to_index(self, project: str | None = None) -> list[sqlite3.Row]:
        """Return files whose status is NOT 'indexed' (new/modified/error)."""
        self._ensure_connected()
        if project:
            return self._conn.execute(
                "SELECT * FROM file_registry WHERE project=? AND status!='indexed'",
                (project,),
            ).fetchall()
        return self._conn.execute(
            "SELECT * FROM file_registry WHERE status!='indexed'"
        ).fetchall()

    def get_indexed_files(self, project: str | None = None) -> list[sqlite3.Row]:
        """Return successfully indexed files."""
        self._ensure_connected()
        if project:
            return self._conn.execute(
                "SELECT * FROM file_registry WHERE project=? AND status='indexed'",
                (project,),
            ).fetchall()
        return self._conn.execute(
            "SELECT * FROM file_registry WHERE status='indexed'"
        ).fetchall()

    def is_file_indexed(self, project: str, rel_path: str, md5_hash: str) -> bool:
        """True if this exact (project, path, hash) is already indexed."""
        self._ensure_connected()
        row = self._conn.execute(
            """SELECT 1 FROM file_registry
               WHERE project=? AND rel_path=? AND md5_hash=? AND status='indexed'""",
            (project, rel_path, md5_hash),
        ).fetchone()
        return row is not None

    def reset_project(self, project: str) -> int:
        """Mark all files in a project as 'new' for re-indexing. Returns count."""
        self._ensure_connected()
        cur = self._conn.execute(
            "UPDATE file_registry SET status='new' WHERE project=?",
            (project,),
        )
        self._conn.commit()
        return cur.rowcount

    def forget_missing_files(self, known_rel_paths: set[str], project: str) -> int:
        """Mark files as 'error' if they're in the registry but no longer on disk."""
        self._ensure_connected()
        rows = self._conn.execute(
            "SELECT file_id, rel_path FROM file_registry WHERE project=? AND status='indexed'",
            (project,),
        ).fetchall()
        count = 0
        for r in rows:
            if r["rel_path"] not in known_rel_paths:
                self._conn.execute(
                    "UPDATE file_registry SET status='error', error_message='File missing from disk' WHERE file_id=?",
                    (r["file_id"],),
                )
                count += 1
        self._conn.commit()
        return count


# ── SQL Schema ─────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS file_registry (
    file_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project         TEXT    NOT NULL,
    rel_path        TEXT    NOT NULL,
    file_name       TEXT    NOT NULL,
    file_type       TEXT    NOT NULL DEFAULT 'other',
    md5_hash        TEXT    NOT NULL,
    file_size_bytes INTEGER NOT NULL DEFAULT 0,
    last_modified   TEXT,
    chunk_count     INTEGER NOT NULL DEFAULT 0,
    last_indexed_at TEXT,
    status          TEXT    NOT NULL DEFAULT 'new',
    error_message   TEXT,
    metadata_json   TEXT    NOT NULL DEFAULT '{}',
    UNIQUE(project, rel_path)
);

CREATE INDEX IF NOT EXISTS idx_registry_project ON file_registry(project);
CREATE INDEX IF NOT EXISTS idx_registry_status  ON file_registry(status);
CREATE INDEX IF NOT EXISTS idx_registry_hash    ON file_registry(md5_hash);
"""
