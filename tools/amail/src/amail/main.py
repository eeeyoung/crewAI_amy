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
        from shared_tools.graph.graph_service import GraphService
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


def build_habits():
    """Build Amy's email reply habit profiles from Outlook data.

    Usage:
        uv run build_habits              # Full pipeline (stages 0-4)
        uv run build_habits --stage 2    # Start from Stage 2 (matching)
        uv run build_habits --stage 3    # Start from Stage 3 (classify only)

    Stages:
        0 = FETCH (Outlook), 1 = NORMALIZE, 2 = MATCH, 3 = CLASSIFY, 4 = BUILD
    """
    import json
    import sys
    import time

    # Parse --stage N argument
    start_stage = 0
    args = sys.argv[1:]
    if '--stage' in args:
        idx = args.index('--stage')
        if idx + 1 < len(args):
            start_stage = int(args[idx + 1])

    from PyQt6.QtWidgets import QApplication
    from shared_tools.habit_learner.habit_learner_service import HabitLearnerService

    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)

    service = HabitLearnerService()

    # Console progress
    service.build_progress.connect(
        lambda c, t, s: print(f"[{c}/{t}] {s}")
    )
    service.stage_complete.connect(
        lambda name, stats: print(
            f"\n--- Stage {name} complete: {json.dumps(stats, indent=2)[:300]} ---"
        )
    )
    service.build_complete.connect(
        lambda summary: print(f"\n{'='*60}\nDone!\n{json.dumps(summary, indent=2)}")
    )
    service.build_error.connect(
        lambda err: print(f"  ⚠ Error: {err}")
    )
    service.fetch_progress.connect(
        lambda c, t, s: print(f"  Fetch [{c}] {s}")
    )

    stage_names = ["FETCH", "NORMALIZE", "MATCH", "CLASSIFY", "BUILD"]
    if start_stage > 0:
        print(f"Building Amy's reply habit profiles starting from Stage {start_stage} ({stage_names[start_stage]})...")
        print(f"Skipping: {' → '.join(stage_names[:start_stage])}")
        print()
    else:
        print("Building Amy's reply habit profiles...")
        print("Stages: FETCH → NORMALIZE → MATCH → CLASSIFY → BUILD")
        print()

    service.build_profiles(start_stage=start_stage)

    # Process events to allow signal delivery
    try:
        while service._running:
            app.processEvents()
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return

    # Show summary
    summary = service.get_learning_summary()
    print(f"\n{'='*60}")
    print("Learning Summary:")
    print(f"  Raw inbox emails:  {summary.get('raw_inbox_count', 0)}")
    print(f"  Raw sent emails:   {summary.get('raw_sent_count', 0)}")
    print(f"  Normalized sent:   {summary.get('sent_messages', 0)}")
    print(f"  Normalized recv:   {summary.get('received_messages', 0)}")
    print(f"  Matched pairs:     {summary.get('matched_pairs', 0)}")
    print(f"  Classified pairs:  {summary.get('classified_pairs', 0)}")
    print(f"  Reply rate:        {summary.get('reply_rate_pct', 0)}%")
    print(f"  Unmatched received:{summary.get('unmatched_received', 0)}")
    print(f"  Senders profiled:  {summary.get('senders_discovered', 0)}")
    print(f"  Tier distribution: {summary.get('tier_distribution', {})}")
    print(f"{'='*60}")


def view_habits():
    """Print learned reply habits: sender profiles, reply pairs, non-replied emails.

    Usage:
        uv run view_habits                  # Summary view
        uv run view_habits --senders        # All sender profiles
        uv run view_habits --pairs 20       # Last 20 classified reply pairs
        uv run view_habits --unreplied 30   # Top 30 emails Amy didn't reply to
        uv run view_habits --sender <email> # Detailed profile for one sender
    """
    import json
    import sys

    from shared_tools.habit_learner.habit_learner_db import (
        get_learning_summary, get_all_sender_profiles, get_all_reply_pairs,
        get_unmatched_received, get_sender_profile,
    )
    from shared_tools.core.email_parser import extract_sender_email

    args = sys.argv[1:]

    # ── Summary (default) ──────────────────────────────────────────
    if not args:
        summary = get_learning_summary()
        print(f"\n{'='*60}")
        print("  Habit Learner — Summary")
        print(f"{'='*60}")
        print(f"  Raw inbox emails:    {summary.get('raw_inbox_count', 0)}")
        print(f"  Raw sent emails:     {summary.get('raw_sent_count', 0)}")
        print(f"  Matched reply pairs: {summary.get('matched_pairs', 0)}")
        print(f"  Classified pairs:    {summary.get('classified_pairs', 0)}")
        print(f"  Reply rate:          {summary.get('reply_rate_pct', 0)}%")
        print(f"  Unmatched received:  {summary.get('unmatched_received', 0)}")
        print(f"  Senders profiled:    {summary.get('senders_discovered', 0)}")
        print(f"  Tier distribution:   {summary.get('tier_distribution', {})}")
        print(f"{'='*60}")
        print()
        print("Use --senders, --pairs N, --unreplied N, or --sender <email> for detail.")
        return

    # ── --senders ───────────────────────────────────────────────────
    if '--senders' in args:
        profiles = get_all_sender_profiles()
        if not profiles:
            print("No sender profiles yet. Run build_habits first.")
            return
        print(f"\n{'='*80}")
        print(f"  Sender Profiles ({len(profiles)} senders)")
        print(f"{'='*80}")
        for p in profiles[:50]:
            print(f"\n  {p.get('sender_name', p.get('sender_email', 'Unknown'))}")
            print(f"    Email: {p['sender_email']}")
            print(f"    Tier: {p.get('tier_label', '?')} ({p.get('tier', '?')})")
            print(f"    Reply rate: {p.get('reply_rate', 0):.0%}")
            if p.get('avg_latency_hours'):
                print(f"    Avg latency: {p['avg_latency_hours']:.1f}h")
            if p.get('preferred_greeting'):
                print(f"    Greeting: \"{p['preferred_greeting']}\"")
            if p.get('signoff_preference'):
                print(f"    Sign-off: \"{p['signoff_preference']}\"")
            if p.get('top_intent'):
                print(f"    Top intent: {p['top_intent']}")
            print(f"    Emails: {p.get('total_received', 0)} received, {p.get('total_replied', 0)} replied")
        return

    # ── --pairs N ───────────────────────────────────────────────────
    if '--pairs' in args:
        idx = args.index('--pairs')
        n = int(args[idx + 1]) if idx + 1 < len(args) else 10
        pairs = get_all_reply_pairs()
        if not pairs:
            print("No reply pairs yet. Run build_habits first.")
            return
        print(f"\n{'='*80}")
        print(f"  Reply Pairs (showing last {min(n, len(pairs))} of {len(pairs)} total)")
        print(f"{'='*80}")
        for p in pairs[-n:]:
            intent = p.get('intent', 'unclassified')
            confidence = p.get('classification_confidence', 0) or 0
            print(f"\n  [{p['id']}] {intent} (confidence: {confidence:.0%})")
            print(f"    Sender: {p.get('received_sender', '?')}")
            print(f"    Received: {p.get('received_subject', '')[:80]}")
            print(f"    Reply: {p.get('reply_subject', '')[:80]}")
            print(f"    Latency: {p.get('latency_hours', 0):.1f}h")
            print(f"    Structure: {p.get('structure_type', '?')}")
            if p.get('greeting_used'):
                print(f"    Greeting: \"{p['greeting_used']}\"")
            if p.get('signoff_used'):
                print(f"    Sign-off: \"{p['signoff_used']}\"")
            if p.get('reply_body'):
                print(f"    Reply body: {p['reply_body'][:200]}...")
        return

    # ── --unreplied N ───────────────────────────────────────────────
    if '--unreplied' in args:
        idx = args.index('--unreplied')
        n = int(args[idx + 1]) if idx + 1 < len(args) else 20
        unreplied = get_unmatched_received(n)
        if not unreplied:
            print("No unmatched received emails. Every email got a reply!")
            return
        print(f"\n{'='*80}")
        print(f"  Emails Amy Did NOT Reply To ({len(unreplied)} shown)")
        print(f"{'='*80}")
        for i, em in enumerate(unreplied):
            print(f"\n  [{i+1}] {em.get('sender_name', '')} <{em.get('sender_email', '')}>")
            print(f"    Subject: {em.get('subject', '')[:100]}")
            print(f"    Received: {em.get('timestamp', '')[:19]}")
            body = (em.get('body_plain', '') or '')[:150]
            if body:
                print(f"    Body: {body}...")
        return

    # ── --sender <email> ────────────────────────────────────────────
    if '--sender' in args:
        idx = args.index('--sender')
        email = args[idx + 1] if idx + 1 < len(args) else ""
        if not email:
            print("Usage: uv run view_habits --sender <email>")
            return
        from shared_tools.habit_learner.habit_learner_service import get_habit_service
        svc = get_habit_service()
        detail = svc.get_sender_detail(email)
        if not detail:
            print(f"No profile found for {email}")
            return
        print(f"\n{'='*80}")
        print(f"  Sender Detail: {detail.get('sender_name', email)} <{email}>")
        print(f"{'='*80}")
        print(f"  Tier: {detail.get('tier_label', '?')} (level {detail.get('tier', '?')})")
        print(f"  Domain: {detail.get('domain', '?')}")
        print(f"  Reply rate: {detail.get('reply_rate', 0):.0%}")
        if detail.get('avg_latency_hours'):
            print(f"  Avg latency: {detail['avg_latency_hours']:.1f}h")
        if detail.get('avg_reply_words'):
            print(f"  Avg reply length: {detail['avg_reply_words']:.0f} words")
        if detail.get('preferred_greeting'):
            print(f"  Preferred greeting: \"{detail['preferred_greeting']}\"")
        if detail.get('signoff_preference'):
            print(f"  Preferred sign-off: \"{detail['signoff_preference']}\"")
        if detail.get('top_intent'):
            print(f"  Top intent: {detail['top_intent']}")
        examples = detail.get('examples', [])
        if examples:
            print(f"\n  Example replies ({len(examples)}):")
            for ex in examples:
                print(f"    ─ {ex.get('received_subject', '')[:70]}")
                print(f"      Intent: {ex.get('intent', '?')}")
                print(f"      Reply: {ex.get('reply_body', '')[:150]}...")
        print(f"{'='*80}")
        return

    print(f"Unknown option. Use --senders, --pairs N, --unreplied N, or --sender <email>.")


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