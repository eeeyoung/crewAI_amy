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

    print("Fetching latest 10 emails from Outlook Inbox...")
    raw_emails = fetch_inbox_emails(count=10, max_body=10000)

    if not raw_emails:
        print("No emails found in Inbox. Exiting.")
        return

    print(f"Fetched {len(raw_emails)} emails. Launching workstation...")

    from amy.gui_viewer import show_triage_report
    show_triage_report(raw_emails)


def run():
    run_triage()


if __name__ == "__main__":
    run()
