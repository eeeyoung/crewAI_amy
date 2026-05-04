import os
import sqlite3

DB_PATH = "knowledge/fact_store.db"


def _get_conn():
    os.makedirs("knowledge", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _get_conn()
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS facts USING fts5(
            project, topic, detail, source_subject, source_sender,
            tokenize='porter unicode61'
        )
    """)
    conn.commit()
    conn.close()


def save_facts(facts: list[dict], source_subject: str = "", source_sender: str = ""):
    """Insert extracted facts into the FTS5 store."""
    conn = _get_conn()
    for f in facts:
        conn.execute(
            "INSERT INTO facts (project, topic, detail, source_subject, source_sender) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                f.get("project", ""),
                f.get("topic", ""),
                f.get("detail", ""),
                source_subject,
                source_sender,
            ),
        )
    conn.commit()
    conn.close()


STOP_WORDS = {
    "the", "and", "for", "are", "was", "not", "but", "you", "all", "can",
    "had", "her", "his", "its", "may", "who", "has", "did", "our", "any",
    "been", "were", "she", "him", "say", "get", "got", "put", "let", "set",
    "see", "use", "via", "per", "due", "yet", "too", "also", "very", "just",
    "into", "from", "have", "more", "some", "than", "them", "will", "what",
    "when", "than", "then", "here", "there", "this", "that", "with", "each",
    "over", "under", "after", "before", "would", "could", "should", "about",
    "does", "doing", "having", "being", "like", "need", "know", "make", "well",
    "back", "still", "much", "even", "take", "come", "want", "look", "find",
}

def search_facts(subject: str, content: str, category: str = "", limit: int = 5) -> list[dict]:
    """Search FTS5 for facts relevant to the given email. Returns list of fact dicts."""
    query_parts = []
    for text in (subject, content, category):
        words = [
            w.strip(".,;:!?()[]{}")
            for w in text.split()
            if len(w.strip(".,;:!?()[]{}")) > 2
            and w.strip(".,;:!?()[]{}").lower() not in STOP_WORDS
        ]
        query_parts.extend(words)

    if not query_parts:
        return []

    # Deduplicate while preserving order, then build FTS5 OR query
    seen = set()
    unique = []
    for w in query_parts:
        low = w.lower()
        if low not in seen:
            seen.add(low)
            unique.append(w)
    # Wrap each term in double quotes to prevent FTS5 syntax errors from
    # apostrophes, hyphens, and other special characters in email text.
    fts_query = " OR ".join(f'"{w}"' for w in unique[:50])

    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT project, topic, detail, rank FROM facts WHERE facts MATCH ? "
            "ORDER BY rank LIMIT ?",
            (fts_query, limit),
        ).fetchall()
        results = [dict(r) for r in rows]
    except sqlite3.OperationalError:
        results = []
    finally:
        conn.close()

    return results


def list_all_facts() -> list[dict]:
    """Return every fact in the store, most recent first."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT rowid, project, topic, detail, source_subject, source_sender "
            "FROM facts ORDER BY rowid DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


