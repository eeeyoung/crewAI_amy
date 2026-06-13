#!/usr/bin/env python
"""ASummary — reads AMail-processed emails from the shared database and
generates Chinese summaries, assignees, and todo items.

Usage:
    uv run asummary              # Process all un-summarized emails
    uv run asummary --list       # List already-summarized emails
    uv run asummary --all        # Re-process ALL emails (including already-done ones)
"""

import json
import sqlite3
import sys
import warnings

# Force UTF-8 on Windows so emojis and Chinese render correctly
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

from shared_tools.ipc_bridge import DB_PATH  # noqa: E402 — import after path setup


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_summary_table(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS email_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_entry_id TEXT UNIQUE NOT NULL,
            email_subject TEXT,
            email_sender TEXT,
            category TEXT,
            chinese_summary TEXT,
            assignee TEXT,
            todos_json TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def get_unsummarized_emails() -> list[dict]:
    """Fetch categorized emails that haven't been summarized yet."""
    conn = _get_connection()
    _ensure_summary_table(conn)
    try:
        rows = conn.execute("""
            SELECT ce.* FROM categorized_emails ce
            LEFT JOIN email_summaries es ON ce.email_entry_id = es.email_entry_id
            WHERE es.email_entry_id IS NULL
            ORDER BY ce.created_at DESC
        """).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"Error reading from shared DB: {e}")
        return []
    finally:
        conn.close()


def get_all_summaries() -> list[dict]:
    """Fetch all existing summaries."""
    conn = _get_connection()
    _ensure_summary_table(conn)
    try:
        rows = conn.execute(
            "SELECT * FROM email_summaries ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"Error reading summaries: {e}")
        return []
    finally:
        conn.close()


def save_summary(email_entry_id: str, email_subject: str, email_sender: str,
                 category: str, chinese_summary: str, assignee: str, todos: list):
    """Store a generated summary in the database."""
    conn = _get_connection()
    _ensure_summary_table(conn)
    try:
        conn.execute(
            """INSERT OR REPLACE INTO email_summaries
               (email_entry_id, email_subject, email_sender, category,
                chinese_summary, assignee, todos_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (email_entry_id, email_subject, email_sender, category,
             chinese_summary, assignee, json.dumps(todos, ensure_ascii=False)),
        )
        conn.commit()
    except Exception as e:
        print(f"Error saving summary: {e}")
    finally:
        conn.close()


def summarize_email(email: dict) -> dict | None:
    """Run the LLM summarizer on a single email. Returns the summary dict or None."""
    from asummary.crew import SummarizerCrew

    inputs = {
        "email_subject": email.get("email_subject", ""),
        "email_sender": email.get("email_sender", ""),
        "email_content": email.get("email_body", ""),
        "email_category": email.get("category", "General"),
    }

    try:
        result = SummarizerCrew().crew().kickoff(inputs=inputs)
        raw = result.raw if hasattr(result, 'raw') else str(result)

        # Parse JSON from the output
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
            cleaned = cleaned.rsplit("```", 1)[0]
        # Handle {{...}} (LLM returning template delimiters literally)
        if cleaned.startswith("{{") and cleaned.endswith("}}"):
            cleaned = cleaned[1:-1]
        parsed = json.loads(cleaned.strip())
        return {
            "chinese_summary": parsed.get("chinese_summary", ""),
            "assignee": parsed.get("assignee", ""),
            "todos": parsed.get("todos", []),
        }
    except json.JSONDecodeError:
        return {
            "chinese_summary": raw[:200],
            "assignee": "Unknown",
            "todos": [],
        }
    except Exception as e:
        print(f"  ⚠️  Error: {e}")
        return None


def format_output(email: dict, summary: dict) -> str:
    """Format a single email summary for terminal display."""
    subject = email.get("email_subject", "(No Subject)")[:60]
    sender = email.get("email_sender", "Unknown")
    category = email.get("category", "General")

    todos = summary.get("todos", [])
    todo_lines = "\n".join(f"       • {t}" for t in todos) if todos else "       (none)"

    return (
        f"┌─ 📧 {subject}\n"
        f"├─ 👤 {sender}  |  📂 {category}\n"
        f"├─ 🇨🇳 {summary.get('chinese_summary', 'N/A')}\n"
        f"├─ 👔 负责人: {summary.get('assignee', 'N/A')}\n"
        f"└─ ✅ 待办:\n{todo_lines}"
    )


def run():
    """Main entry point. Use --list / --latest / --all for CLI options."""
    print("🔍 ASummary — reading emails processed by AMail...\n")

    # Ensure shared DB exists (auto-init if missing)
    if not DB_PATH.exists():
        from shared_tools.ipc_bridge import init_shared_db
        init_shared_db()
        print(f"📦 Initialized shared database at {DB_PATH}\n")

    # Handle CLI flags
    if "--list" in sys.argv:
        summaries = get_all_summaries()
        if not summaries:
            print("No summaries found. Run without --list to generate some.")
            return
        print(f"📋 {len(summaries)} summarized email(s):\n")
        for s in summaries:
            todos = json.loads(s.get("todos_json", "[]"))
            todo_str = ", ".join(todos) if todos else "(none)"
            print(f"  📧 {s['email_subject'][:60]}")
            print(f"     🇨🇳 {s['chinese_summary']}")
            print(f"     👔 {s['assignee']}  |  ✅ {todo_str}")
            print()
        return

    # How many emails to process? Default: 5, or --latest N, or --all
    limit = 5
    if "--all" in sys.argv:
        limit = None  # unlimited
    elif "--latest" in sys.argv:
        try:
            idx = sys.argv.index("--latest")
            limit = int(sys.argv[idx + 1])
        except (ValueError, IndexError):
            print("Usage: uv run asummary --latest <N>")
            return

    # Fetch emails to process
    emails = get_unsummarized_emails()
    if not emails:
        print("✅ No new emails to summarize. All AMail-processed emails are done!")
        return

    if limit:
        emails = emails[:limit]

    print(f"📬 Processing {len(emails)} email(s) (out of {len(get_unsummarized_emails())} total).\n")

    for i, email in enumerate(emails, 1):
        subject = email.get("email_subject", "(No Subject)")[:50]
        print(f"[{i}/{len(emails)}] Summarizing: {subject}...")
        summary = summarize_email(email)

        if summary:
            save_summary(
                email_entry_id=email.get("email_entry_id", ""),
                email_subject=email.get("email_subject", ""),
                email_sender=email.get("email_sender", ""),
                category=email.get("category", ""),
                chinese_summary=summary["chinese_summary"],
                assignee=summary["assignee"],
                todos=summary["todos"],
            )
            print(format_output(email, summary))
        else:
            print(f"  ❌ Failed to summarize.\n")

    print(f"✅ Done! {len(emails)} email(s) summarized.")


if __name__ == "__main__":
    run()
