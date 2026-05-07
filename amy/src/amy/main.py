#!/usr/bin/env python
import sys
import warnings

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")


def run_triage():
    """
    Fetch emails directly, then launch the interactive GUI.
    Triage and reply generation happen in background threads inside the GUI.
    """
    from amy.fact_store import init_db
    init_db()

    from amy.tools.outlook_tool import fetch_inbox_emails

    print("Fetching up to 50 unread emails from Outlook Inbox...")
    raw_emails = fetch_inbox_emails(count=50, max_body=30000, unread_only=True)

    if not raw_emails:
        print("No emails found in Inbox. Exiting.")
        return

    print(f"Fetched {len(raw_emails)} emails. Launching workstation...")

    from amy.gui_viewer import show_triage_report
    show_triage_report(raw_emails)


def run():
    run_triage()


def extract_style():
    """
    Run the StyleLearnerCrew to extract writing style from the historical_emails folder.
    This generates the style_blueprint.md file.
    """
    from amy.crew import StyleLearnerCrew
    print("Extracting style blueprint from historical emails...")
    try:
        StyleLearnerCrew().crew().kickoff()
        print("\nSuccess! Style blueprint saved to knowledge/style_blueprint.md")
    except Exception as e:
        print(f"\nError extracting style: {e}")


def train():
    """
    Run CrewAI's training loop on the ReplyGeneratorCrew.
    Provide the number of iterations as a command line argument, e.g., 'uv run train 2'.
    """
    from amy.crew import ReplyGeneratorCrew
    n_iterations = 1
    if len(sys.argv) > 1:
        try:
            n_iterations = int(sys.argv[1])
        except ValueError:
            pass

    print(f"Starting training loop with {n_iterations} iterations...")
    try:
        ReplyGeneratorCrew().crew().train(
            n_iterations=n_iterations,
            filename='my_style_training.pkl',
            inputs={
                "email_subject": "Test Inquiry",
                "email_content": "Hi Amy, what is the status of the concrete pour?",
                "email_category": "RFI",
                "email_context": "Checking on schedule.",
                "relevant_facts": "No relevant stored facts found."
            }
        )
        print("\nTraining complete. Saved to my_style_training.pkl")
    except Exception as e:
        print(f"\nError during training: {e}")


def view_facts():
    """Print all facts in the knowledge store."""
    from amy.fact_store import init_db, list_all_facts
    init_db()
    facts = list_all_facts()
    if not facts:
        print("No facts stored yet. Use 'Save Key Facts' in the GUI to add some.")
        return
    print(f"\n{'='*80}")
    print(f"  Fact Store — {len(facts)} entries")
    print(f"{'='*80}\n")
    for f in facts:
        print(f"  [{f['project']}] {f['topic']}")
        print(f"  {f['detail']}")
        if f.get("source_subject"):
            print(f"  Source: {f['source_subject']}")
        print()


def test():
    """Run the test suite via pytest."""
    import pytest
    import sys
    args = ["tests/", "-v"]
    args.extend(sys.argv[1:])
    sys.exit(pytest.main(args))


if __name__ == "__main__":
    run()