#!/usr/bin/env python
import sys
import warnings

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")


def run_triage():
    """
    Fetch emails directly, then launch the interactive GUI.
    Triage and reply generation happen in background threads inside the GUI.
    """
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
                "email_context": "Checking on schedule."
            }
        )
        print("\nTraining complete. Saved to my_style_training.pkl")
    except Exception as e:
        print(f"\nError during training: {e}")


if __name__ == "__main__":
    run()


# To do:
# 1. Left panel - make the email more readable when there is multiple dialogues (1st message and its sender, 2nd message and its sender, etc.)
# 2. Time flag
# 3. Shortcut for each button
# 4. Fetch attachments (Preview first)