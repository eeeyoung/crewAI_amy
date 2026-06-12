#!/usr/bin/env python
import warnings

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")


def run_triage():
    """
    Launch the MailLister dialog first so the user can select up to 5 emails,
    then open the interactive GUI for triage and reply generation.
    """
    import os
    from amail.mail_knowledge import init_db
    init_db()

    from PyQt6.QtWidgets import QApplication, QDialog
    import sys

    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)

    processed_entry_ids: set[str] = set()

    # ── Optional Microsoft Graph enrichment ───────────────────────
    # Create GraphService if the Azure AD client ID is configured.
    # The client ID is public (used for device-code OAuth) — it can be
    # set in .env as GRAPH_CLIENT_ID or left hardcoded for convenience.
    # If omitted, MailLister works exactly as before.
    graph_svc = None
    graph_client_id = os.environ.get(
        "GRAPH_CLIENT_ID",
        "d92815c2-ccaa-451d-96ba-96fb35ad993c",  # Azure AD app registration
    )
    if graph_client_id:
        from shared_tools.graph_service import GraphService
        graph_svc = GraphService(client_id=graph_client_id)

    from amail.mail_lister import MailListerDialog
    dialog = MailListerDialog(processed_entry_ids, graph_service=graph_svc)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        print("No emails selected. Exiting.")
        return

    selected_emails = dialog.get_selected_emails()
    if not selected_emails:
        print("No emails selected. Exiting.")
        return

    print(f"Selected {len(selected_emails)} emails. Launching workstation...")

    from amail.gui_viewer import show_triage_report
    show_triage_report(selected_emails, processed_entry_ids)


def run():
    run_triage()


def extract_style():
    """
    Run the StyleLearnerCrew to extract writing style from the historical_emails folder.
    This generates the style_blueprint.md file.
    """
    from amail.crew import StyleLearnerCrew
    print("Extracting style blueprint from historical emails...")
    try:
        StyleLearnerCrew().crew().kickoff()
        print("\nSuccess! Style blueprint saved to knowledge/style_blueprint.md")
    except Exception as e:
        print(f"\nError extracting style: {e}")


def view_facts():
    """Print all facts in the knowledge store."""
    from amail.mail_knowledge import init_db, list_all_facts
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