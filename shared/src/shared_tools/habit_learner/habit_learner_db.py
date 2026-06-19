"""Habit Learner database layer.

Provides the SQLite schema (9 tables) and CRUD functions for Amy's
email reply habit learning system.

Database location: ``<LILAMY_DATA_DIR>/habit_learner.db``

Tables:
    raw_inbox        — raw received emails from Outlook fetch
    raw_sent         — raw sent emails from Outlook fetch
    sent_messages    — normalized sent messages (Amy's replies)
    received_messages— normalized received messages (incoming)
    reply_pairs      — matched (received → reply) pairs with LLM-classified features
    sender_profiles  — aggregated per-sender behavioral profiles
    style_matrix     — conditional style parameters (tier × category)
    intent_priors    — P(intent | dimension_value) conditional probabilities
    learning_sessions— audit trail of build runs
"""

import json
import os
import sqlite3
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Database path resolution
# ---------------------------------------------------------------------------

def _resolve_data_dir() -> Path:
    """Resolve the data directory (same logic as ipc_bridge.py)."""
    if "LILAMY_DATA_DIR" in os.environ:
        return Path(os.environ["LILAMY_DATA_DIR"])
    # habit_learner_db.py is at shared/src/shared_tools/habit_learner/ → 5 levels up to root
    project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
    return project_root / "data"


DB_PATH = _resolve_data_dir() / "habit_learner.db"

# Directory for JSON file storage (browsable by humans)
MAIL_FETCH_DIR = _resolve_data_dir() / "mail_fetch"


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def _get_connection() -> sqlite3.Connection:
    """Get a new SQLite connection with WAL mode and row factory."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ---------------------------------------------------------------------------
# Schema initialization
# ---------------------------------------------------------------------------

def init_db():
    """Create all habit learner tables if they don't exist. Idempotent."""
    conn = _get_connection()
    try:
        conn.executescript("""
        -- Raw fetched emails (from Outlook)
        CREATE TABLE IF NOT EXISTS raw_inbox (
            entry_id TEXT PRIMARY KEY,
            subject TEXT,
            sender_name TEXT,
            sender_email TEXT,
            recipients_to TEXT DEFAULT '[]',
            recipients_cc TEXT DEFAULT '[]',
            body_plain TEXT,
            body_html TEXT,
            received_time TEXT,
            conversation_id TEXT,
            has_attachment INTEGER DEFAULT 0,
            json_path TEXT
        );

        CREATE TABLE IF NOT EXISTS raw_sent (
            entry_id TEXT PRIMARY KEY,
            subject TEXT,
            sender_name TEXT,
            sender_email TEXT,
            recipients_to TEXT DEFAULT '[]',
            recipients_cc TEXT DEFAULT '[]',
            body_plain TEXT,
            body_html TEXT,
            sent_time TEXT,
            conversation_id TEXT,
            thread_subject_norm TEXT,
            json_path TEXT
        );

        -- Normalized messages
        CREATE TABLE IF NOT EXISTS sent_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT UNIQUE,
            sender_name TEXT,
            sender_email TEXT,
            recipients_to TEXT DEFAULT '[]',
            recipients_cc TEXT DEFAULT '[]',
            subject TEXT,
            body_plain TEXT,
            timestamp TEXT,
            thread_subject_norm TEXT,
            source_entry_id TEXT,
            conversation_id TEXT
        );

        CREATE TABLE IF NOT EXISTS received_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT UNIQUE,
            sender_name TEXT,
            sender_email TEXT,
            recipients_to TEXT DEFAULT '[]',
            recipients_cc TEXT DEFAULT '[]',
            subject TEXT,
            body_plain TEXT,
            timestamp TEXT,
            thread_subject_norm TEXT,
            source_entry_id TEXT,
            conversation_id TEXT,
            matched_reply_id INTEGER REFERENCES sent_messages(id),
            reply_latency_hours REAL,
            was_replied INTEGER NOT NULL DEFAULT 0
        );

        -- Matched reply pairs with LLM-classified features
        CREATE TABLE IF NOT EXISTS reply_pairs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            received_id INTEGER NOT NULL REFERENCES received_messages(id),
            reply_id INTEGER NOT NULL REFERENCES sent_messages(id),
            latency_hours REAL NOT NULL,
            intent TEXT,
            formality_level INTEGER,
            greeting_used TEXT,
            signoff_used TEXT,
            reply_word_count INTEGER,
            reply_paragraph_count INTEGER,
            uses_bullet_points INTEGER DEFAULT 0,
            contains_question INTEGER DEFAULT 0,
            contains_commitment INTEGER DEFAULT 0,
            structure_type TEXT,
            classification_confidence REAL
        );

        -- Aggregated sender behavioral profiles
        CREATE TABLE IF NOT EXISTS sender_profiles (
            sender_email TEXT PRIMARY KEY,
            sender_name TEXT,
            domain TEXT,
            tier INTEGER DEFAULT 3,
            tier_label TEXT DEFAULT 'unknown',
            total_received INTEGER DEFAULT 0,
            total_replied INTEGER DEFAULT 0,
            reply_rate REAL DEFAULT 0.0,
            avg_latency_hours REAL,
            latency_std_hours REAL,
            preferred_greeting TEXT,
            avg_reply_words REAL,
            formality_level REAL,
            top_intent TEXT,
            signoff_preference TEXT,
            last_updated TEXT
        );

        -- Conditional style matrix (tier × category)
        CREATE TABLE IF NOT EXISTS style_matrix (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_tier INTEGER NOT NULL,
            category TEXT NOT NULL,
            avg_words REAL,
            formality REAL,
            greeting_style TEXT,
            signoff TEXT,
            uses_bullet_points REAL,
            structure_type TEXT,
            sample_count INTEGER DEFAULT 1,
            examples_json TEXT DEFAULT '[]'
        );

        -- Intent priors: P(intent | dimension_value)
        CREATE TABLE IF NOT EXISTS intent_priors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dimension TEXT NOT NULL,
            dimension_value TEXT NOT NULL,
            intent TEXT NOT NULL,
            probability REAL NOT NULL,
            sample_count INTEGER DEFAULT 0
        );

        -- Audit trail
        CREATE TABLE IF NOT EXISTS learning_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT,
            completed_at TEXT,
            total_files INTEGER DEFAULT 0,
            parsed_messages INTEGER DEFAULT 0,
            matched_pairs INTEGER DEFAULT 0,
            unmatched_sent INTEGER DEFAULT 0,
            unmatched_received INTEGER DEFAULT 0,
            senders_discovered INTEGER DEFAULT 0,
            errors_json TEXT DEFAULT '[]'
        );

        -- Indexes for common query patterns
        CREATE INDEX IF NOT EXISTS idx_raw_inbox_time
            ON raw_inbox(received_time);
        CREATE INDEX IF NOT EXISTS idx_raw_sent_time
            ON raw_sent(sent_time);
        CREATE INDEX IF NOT EXISTS idx_received_norm_subj
            ON received_messages(thread_subject_norm);
        CREATE INDEX IF NOT EXISTS idx_sent_norm_subj
            ON sent_messages(thread_subject_norm);
        CREATE INDEX IF NOT EXISTS idx_received_sender
            ON received_messages(sender_email);
        CREATE INDEX IF NOT EXISTS idx_reply_pairs_intent
            ON reply_pairs(intent);
        CREATE INDEX IF NOT EXISTS idx_sender_profiles_tier
            ON sender_profiles(tier);
        CREATE INDEX IF NOT EXISTS idx_style_matrix_tier_cat
            ON style_matrix(sender_tier, category);
        CREATE INDEX IF NOT EXISTS idx_intent_priors_dim
            ON intent_priors(dimension, dimension_value);
        """)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Raw email CRUD (Stage 0 — Fetch)
# ---------------------------------------------------------------------------

def insert_raw_inbox(email: dict) -> bool:
    """Insert or replace a raw inbox email. Returns True on success."""
    conn = _get_connection()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO raw_inbox
                (entry_id, subject, sender_name, sender_email,
                 recipients_to, recipients_cc, body_plain, body_html,
                 received_time, conversation_id, has_attachment, json_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            email.get("entry_id", ""),
            email.get("subject", ""),
            email.get("sender_name", ""),
            email.get("sender_email", ""),
            email.get("recipients_to", "[]"),
            email.get("recipients_cc", "[]"),
            email.get("body_plain", ""),
            email.get("body_html", ""),
            email.get("received_time", ""),
            email.get("conversation_id", ""),
            email.get("has_attachment", 0),
            email.get("json_path", ""),
        ))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error inserting raw inbox email: {e}")
        return False
    finally:
        conn.close()


def insert_raw_sent(email: dict) -> bool:
    """Insert or replace a raw sent email. Returns True on success."""
    conn = _get_connection()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO raw_sent
                (entry_id, subject, sender_name, sender_email,
                 recipients_to, recipients_cc, body_plain, body_html,
                 sent_time, conversation_id, thread_subject_norm, json_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            email.get("entry_id", ""),
            email.get("subject", ""),
            email.get("sender_name", ""),
            email.get("sender_email", ""),
            email.get("recipients_to", "[]"),
            email.get("recipients_cc", "[]"),
            email.get("body_plain", ""),
            email.get("body_html", ""),
            email.get("sent_time", ""),
            email.get("conversation_id", ""),
            email.get("thread_subject_norm", ""),
            email.get("json_path", ""),
        ))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error inserting raw sent email: {e}")
        return False
    finally:
        conn.close()


def get_raw_inbox_count() -> int:
    """Return total count of raw inbox emails."""
    conn = _get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) FROM raw_inbox").fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def get_raw_sent_count() -> int:
    """Return total count of raw sent emails."""
    conn = _get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) FROM raw_sent").fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def get_raw_inbox_emails(limit: int = 0) -> list[dict]:
    """Get all raw inbox emails. limit=0 means unlimited."""
    conn = _get_connection()
    try:
        query = "SELECT * FROM raw_inbox ORDER BY received_time DESC"
        if limit > 0:
            query += f" LIMIT {limit}"
        return [dict(row) for row in conn.execute(query).fetchall()]
    finally:
        conn.close()


def get_raw_sent_emails(limit: int = 0) -> list[dict]:
    """Get all raw sent emails. limit=0 means unlimited."""
    conn = _get_connection()
    try:
        query = "SELECT * FROM raw_sent ORDER BY sent_time DESC"
        if limit > 0:
            query += f" LIMIT {limit}"
        return [dict(row) for row in conn.execute(query).fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Normalized message CRUD (Stage 1 — Normalize)
# ---------------------------------------------------------------------------

def insert_sent_message(msg: dict) -> int | None:
    """Insert a normalized sent message. Returns row id or None."""
    conn = _get_connection()
    try:
        cursor = conn.execute("""
            INSERT OR IGNORE INTO sent_messages
                (message_id, sender_name, sender_email, recipients_to,
                 recipients_cc, subject, body_plain, timestamp,
                 thread_subject_norm, source_entry_id, conversation_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            msg.get("message_id", ""),
            msg.get("sender_name", ""),
            msg.get("sender_email", ""),
            msg.get("recipients_to", "[]"),
            msg.get("recipients_cc", "[]"),
            msg.get("subject", ""),
            msg.get("body_plain", ""),
            msg.get("timestamp", ""),
            msg.get("thread_subject_norm", ""),
            msg.get("source_entry_id", ""),
            msg.get("conversation_id", ""),
        ))
        conn.commit()
        return cursor.lastrowid
    except Exception as e:
        print(f"Error inserting sent message: {e}")
        return None
    finally:
        conn.close()


def insert_received_message(msg: dict) -> int | None:
    """Insert a normalized received message. Returns row id or None."""
    conn = _get_connection()
    try:
        cursor = conn.execute("""
            INSERT OR IGNORE INTO received_messages
                (message_id, sender_name, sender_email, recipients_to,
                 recipients_cc, subject, body_plain, timestamp,
                 thread_subject_norm, source_entry_id, conversation_id,
                 matched_reply_id, reply_latency_hours, was_replied)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            msg.get("message_id", ""),
            msg.get("sender_name", ""),
            msg.get("sender_email", ""),
            msg.get("recipients_to", "[]"),
            msg.get("recipients_cc", "[]"),
            msg.get("subject", ""),
            msg.get("body_plain", ""),
            msg.get("timestamp", ""),
            msg.get("thread_subject_norm", ""),
            msg.get("source_entry_id", ""),
            msg.get("conversation_id", ""),
            msg.get("matched_reply_id"),
            msg.get("reply_latency_hours"),
            msg.get("was_replied", 0),
        ))
        conn.commit()
        return cursor.lastrowid
    except Exception as e:
        print(f"Error inserting received message: {e}")
        return None
    finally:
        conn.close()


def get_all_sent_messages() -> list[dict]:
    """Get all normalized sent messages."""
    conn = _get_connection()
    try:
        return [dict(row) for row in
                conn.execute("SELECT * FROM sent_messages ORDER BY timestamp DESC").fetchall()]
    finally:
        conn.close()


def get_all_received_messages() -> list[dict]:
    """Get all normalized received messages."""
    conn = _get_connection()
    try:
        return [dict(row) for row in
                conn.execute("SELECT * FROM received_messages ORDER BY timestamp DESC").fetchall()]
    finally:
        conn.close()


def get_sent_message_count() -> int:
    conn = _get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM sent_messages").fetchone()[0]
    finally:
        conn.close()


def get_received_message_count() -> int:
    conn = _get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM received_messages").fetchone()[0]
    finally:
        conn.close()


def update_received_reply(received_id: int, reply_id: int, latency_hours: float) -> bool:
    """Mark a received message as replied, linking to its sent reply."""
    conn = _get_connection()
    try:
        conn.execute("""
            UPDATE received_messages
            SET matched_reply_id = ?, reply_latency_hours = ?, was_replied = 1
            WHERE id = ?
        """, (reply_id, latency_hours, received_id))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error updating received reply: {e}")
        return False
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Reply pair CRUD (Stage 2 — Match & Stage 3 — Classify)
# ---------------------------------------------------------------------------

def insert_reply_pair(pair: dict) -> int | None:
    """Insert a matched reply pair. Skips if (received_id, reply_id) already exists."""
    conn = _get_connection()
    try:
        # Check for existing pair to avoid duplicates on re-run
        existing = conn.execute(
            "SELECT id FROM reply_pairs WHERE received_id = ? AND reply_id = ?",
            (pair.get("received_id"), pair.get("reply_id"))
        ).fetchone()
        if existing:
            return existing["id"]

        cursor = conn.execute("""
            INSERT INTO reply_pairs
                (received_id, reply_id, latency_hours, intent, formality_level,
                 greeting_used, signoff_used, reply_word_count, reply_paragraph_count,
                 uses_bullet_points, contains_question, contains_commitment,
                 structure_type, classification_confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            pair.get("received_id"),
            pair.get("reply_id"),
            pair.get("latency_hours", 0),
            pair.get("intent"),
            pair.get("formality_level"),
            pair.get("greeting_used"),
            pair.get("signoff_used"),
            pair.get("reply_word_count"),
            pair.get("reply_paragraph_count"),
            pair.get("uses_bullet_points", 0),
            pair.get("contains_question", 0),
            pair.get("contains_commitment", 0),
            pair.get("structure_type"),
            pair.get("classification_confidence"),
        ))
        conn.commit()
        return cursor.lastrowid
    except Exception as e:
        print(f"Error inserting reply pair: {e}")
        return None
    finally:
        conn.close()


def update_reply_pair_classification(pair_id: int, classification: dict) -> bool:
    """Update a reply pair with LLM classification + text-extracted features."""
    conn = _get_connection()
    try:
        conn.execute("""
            UPDATE reply_pairs
            SET intent = ?, formality_level = ?, greeting_used = ?, signoff_used = ?,
                structure_type = ?, contains_question = ?, contains_commitment = ?,
                classification_confidence = ?,
                reply_word_count = ?, reply_paragraph_count = ?,
                uses_bullet_points = ?
            WHERE id = ?
        """, (
            classification.get("intent"),
            classification.get("formality_level"),
            classification.get("greeting_used"),
            classification.get("signoff_used"),
            classification.get("structure_type"),
            classification.get("contains_question", 0),
            classification.get("contains_commitment", 0),
            classification.get("confidence"),
            classification.get("reply_word_count", 0),
            classification.get("reply_paragraph_count", 0),
            classification.get("uses_bullet_points", 0),
            pair_id,
        ))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error updating reply pair classification: {e}")
        return False
    finally:
        conn.close()


def get_all_reply_pairs() -> list[dict]:
    """Get all reply pairs with joined message info."""
    conn = _get_connection()
    try:
        rows = conn.execute("""
            SELECT
                rp.*,
                rm.subject AS received_subject, rm.sender_email AS received_sender,
                rm.timestamp AS received_time, rm.body_plain AS received_body,
                sm.subject AS reply_subject, sm.body_plain AS reply_body,
                sm.timestamp AS reply_time
            FROM reply_pairs rp
            JOIN received_messages rm ON rp.received_id = rm.id
            JOIN sent_messages sm ON rp.reply_id = sm.id
            ORDER BY rp.id
        """).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_unclassified_pairs() -> list[dict]:
    """Get reply pairs that haven't been classified yet."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM reply_pairs WHERE intent IS NULL"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_pair_count() -> int:
    conn = _get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM reply_pairs").fetchone()[0]
    finally:
        conn.close()


def get_classified_pair_count() -> int:
    conn = _get_connection()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM reply_pairs WHERE intent IS NOT NULL"
        ).fetchone()[0]
    finally:
        conn.close()


def get_unmatched_received_count() -> int:
    """Count received messages that have no matching reply."""
    conn = _get_connection()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM received_messages WHERE was_replied = 0"
        ).fetchone()[0]
    finally:
        conn.close()


def get_unmatched_received(limit: int = 100) -> list[dict]:
    """Get received messages that never got a reply."""
    conn = _get_connection()
    try:
        query = """
            SELECT * FROM received_messages
            WHERE was_replied = 0
            ORDER BY timestamp DESC
        """
        if limit > 0:
            query += f" LIMIT {limit}"
        return [dict(row) for row in conn.execute(query).fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Sender profile CRUD (Stage 4 — Build)
# ---------------------------------------------------------------------------

def upsert_sender_profile(profile: dict) -> bool:
    """Insert or update a sender profile."""
    conn = _get_connection()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO sender_profiles
                (sender_email, sender_name, domain, tier, tier_label,
                 total_received, total_replied, reply_rate,
                 avg_latency_hours, latency_std_hours,
                 preferred_greeting, avg_reply_words, formality_level,
                 top_intent, signoff_preference, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            profile.get("sender_email", ""),
            profile.get("sender_name", ""),
            profile.get("domain", ""),
            profile.get("tier", 3),
            profile.get("tier_label", "unknown"),
            profile.get("total_received", 0),
            profile.get("total_replied", 0),
            profile.get("reply_rate", 0.0),
            profile.get("avg_latency_hours"),
            profile.get("latency_std_hours"),
            profile.get("preferred_greeting"),
            profile.get("avg_reply_words"),
            profile.get("formality_level"),
            profile.get("top_intent"),
            profile.get("signoff_preference"),
            profile.get("last_updated", ""),
        ))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error upserting sender profile: {e}")
        return False
    finally:
        conn.close()


def get_sender_profile(sender_email: str) -> dict | None:
    """Get a single sender's profile by email."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM sender_profiles WHERE sender_email = ?",
            (sender_email,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_all_sender_profiles() -> list[dict]:
    """Get all sender profiles, sorted by reply_rate descending."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM sender_profiles ORDER BY total_received DESC"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_sender_count() -> int:
    conn = _get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM sender_profiles").fetchone()[0]
    finally:
        conn.close()


def get_sender_received_stats(sender_email: str) -> dict:
    """Get real received/replied counts for a sender from received_messages.

    Returns {"total_received": int, "total_replied": int}.
    This is the ground truth for reply_rate — unlike reply_pairs which only
    contains matched pairs.
    """
    conn = _get_connection()
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM received_messages WHERE sender_email = ?",
            (sender_email,)
        ).fetchone()[0]
        replied = conn.execute(
            "SELECT COUNT(*) FROM received_messages WHERE sender_email = ? AND was_replied = 1",
            (sender_email,)
        ).fetchone()[0]
        return {"total_received": total, "total_replied": replied}
    finally:
        conn.close()
        conn.close()


# ---------------------------------------------------------------------------
# Style matrix CRUD (Stage 4 — Build)
# ---------------------------------------------------------------------------

def upsert_style_entry(entry: dict) -> bool:
    """Insert or update a style matrix entry for a (tier, category) pair."""
    conn = _get_connection()
    try:
        # Check if entry exists
        existing = conn.execute(
            "SELECT id FROM style_matrix WHERE sender_tier = ? AND category = ?",
            (entry.get("sender_tier"), entry.get("category"))
        ).fetchone()
        if existing:
            conn.execute("""
                UPDATE style_matrix
                SET avg_words = ?, formality = ?, greeting_style = ?, signoff = ?,
                    uses_bullet_points = ?, structure_type = ?,
                    sample_count = ?, examples_json = ?
                WHERE id = ?
            """, (
                entry.get("avg_words"),
                entry.get("formality"),
                entry.get("greeting_style"),
                entry.get("signoff"),
                entry.get("uses_bullet_points"),
                entry.get("structure_type"),
                entry.get("sample_count", 1),
                entry.get("examples_json", "[]"),
                existing["id"],
            ))
        else:
            conn.execute("""
                INSERT INTO style_matrix
                    (sender_tier, category, avg_words, formality, greeting_style,
                     signoff, uses_bullet_points, structure_type,
                     sample_count, examples_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                entry.get("sender_tier"),
                entry.get("category"),
                entry.get("avg_words"),
                entry.get("formality"),
                entry.get("greeting_style"),
                entry.get("signoff"),
                entry.get("uses_bullet_points"),
                entry.get("structure_type"),
                entry.get("sample_count", 1),
                entry.get("examples_json", "[]"),
            ))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error upserting style entry: {e}")
        return False
    finally:
        conn.close()


def get_style_for_context(sender_tier: int, category: str) -> dict | None:
    """Get the conditional style parameters for a (tier, category) combo."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM style_matrix WHERE sender_tier = ? AND category = ?",
            (sender_tier, category)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_all_style_entries() -> list[dict]:
    conn = _get_connection()
    try:
        return [dict(row) for row in
                conn.execute("SELECT * FROM style_matrix ORDER BY sender_tier, category").fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Intent priors CRUD (Stage 4 — Build)
# ---------------------------------------------------------------------------

def upsert_intent_prior(dimension: str, dimension_value: str,
                         intent: str, probability: float,
                         sample_count: int = 0) -> bool:
    """Insert or update an intent prior probability."""
    conn = _get_connection()
    try:
        existing = conn.execute(
            """SELECT id FROM intent_priors
               WHERE dimension = ? AND dimension_value = ? AND intent = ?""",
            (dimension, dimension_value, intent)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE intent_priors SET probability = ?, sample_count = ? WHERE id = ?",
                (probability, sample_count, existing["id"])
            )
        else:
            conn.execute(
                """INSERT INTO intent_priors (dimension, dimension_value, intent, probability, sample_count)
                   VALUES (?, ?, ?, ?, ?)""",
                (dimension, dimension_value, intent, probability, sample_count)
            )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error upserting intent prior: {e}")
        return False
    finally:
        conn.close()


def get_intent_priors(dimension: str = None) -> list[dict]:
    """Get intent priors, optionally filtered by dimension."""
    conn = _get_connection()
    try:
        if dimension:
            rows = conn.execute(
                "SELECT * FROM intent_priors WHERE dimension = ? ORDER BY probability DESC",
                (dimension,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM intent_priors ORDER BY dimension, probability DESC"
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Learning session CRUD
# ---------------------------------------------------------------------------

def start_learning_session() -> int:
    """Create a new learning session record. Returns session id."""
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO learning_sessions (started_at) VALUES (?)",
            (time.strftime("%Y-%m-%dT%H:%M:%S"),)
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def complete_learning_session(session_id: int, stats: dict) -> bool:
    """Mark a learning session as complete with final stats."""
    conn = _get_connection()
    try:
        conn.execute("""
            UPDATE learning_sessions
            SET completed_at = ?, total_files = ?, parsed_messages = ?,
                matched_pairs = ?, unmatched_sent = ?, unmatched_received = ?,
                senders_discovered = ?, errors_json = ?
            WHERE id = ?
        """, (
            time.strftime("%Y-%m-%dT%H:%M:%S"),
            stats.get("total_files", 0),
            stats.get("parsed_messages", 0),
            stats.get("matched_pairs", 0),
            stats.get("unmatched_sent", 0),
            stats.get("unmatched_received", 0),
            stats.get("senders_discovered", 0),
            json.dumps(stats.get("errors", [])),
            session_id,
        ))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error completing learning session: {e}")
        return False
    finally:
        conn.close()


def get_last_learning_session() -> dict | None:
    """Get the most recent learning session."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM learning_sessions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Bulk operations
# ---------------------------------------------------------------------------

def clear_all_data():
    """Delete all learning data. Use with caution."""
    conn = _get_connection()
    try:
        tables = [
            "reply_pairs", "received_messages", "sent_messages",
            "raw_sent", "raw_inbox",
            "sender_profiles", "style_matrix", "intent_priors",
            "learning_sessions",
        ]
        for table in tables:
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()


def get_learning_summary() -> dict:
    """Return a summary dict of all learning data."""
    conn = _get_connection()
    try:
        raw_inbox_n = conn.execute("SELECT COUNT(*) FROM raw_inbox").fetchone()[0]
        raw_sent_n = conn.execute("SELECT COUNT(*) FROM raw_sent").fetchone()[0]
        sent_n = conn.execute("SELECT COUNT(*) FROM sent_messages").fetchone()[0]
        received_n = conn.execute("SELECT COUNT(*) FROM received_messages").fetchone()[0]
        pairs_n = conn.execute("SELECT COUNT(*) FROM reply_pairs").fetchone()[0]
        classified_n = conn.execute(
            "SELECT COUNT(*) FROM reply_pairs WHERE intent IS NOT NULL"
        ).fetchone()[0]
        senders_n = conn.execute("SELECT COUNT(*) FROM sender_profiles").fetchone()[0]
        unmatched_n = conn.execute(
            "SELECT COUNT(*) FROM received_messages WHERE was_replied = 0"
        ).fetchone()[0]

        # Reply rate
        reply_rate = (pairs_n / received_n * 100) if received_n > 0 else 0

        # Tier distribution
        tier_rows = conn.execute(
            "SELECT tier_label, COUNT(*) as cnt FROM sender_profiles GROUP BY tier_label"
        ).fetchall()
        tier_dist = {row["tier_label"]: row["cnt"] for row in tier_rows}

        return {
            "raw_inbox_count": raw_inbox_n,
            "raw_sent_count": raw_sent_n,
            "sent_messages": sent_n,
            "received_messages": received_n,
            "matched_pairs": pairs_n,
            "classified_pairs": classified_n,
            "unmatched_received": unmatched_n,
            "reply_rate_pct": round(reply_rate, 1),
            "senders_discovered": senders_n,
            "tier_distribution": tier_dist,
        }
    finally:
        conn.close()
