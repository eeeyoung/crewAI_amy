"""HabitLearnerService — learns Amy's email replying patterns.

Service-first pattern: QObject + pyqtSignal + threading.Thread + queue.Queue.

The service produces behavioral profiles from (received, sent) email pairs
and exposes an ``infer(email)`` method that returns a ``BehavioralContext``
for the ReplyGeneratorCrew to consume at reply-generation time.

Pipeline stages:
    0. FETCH     — Pull emails from Outlook Inbox + Sent Items (9 months back)
    1. NORMALIZE — Extract structured messages from raw data
    2. MATCH     — Thread-match received messages to sent replies
    3. CLASSIFY  — LLM-classify each reply pair (intent, style features)
    4. BUILD     — Compute statistical sender profiles + style matrix

Usage:
    service = get_habit_service()
    service.build_profiles()           # Full pipeline
    ctx = service.infer(email_dict)     # Per-email behavioral predictions
"""

import hashlib
import json
import os
import queue
import re
import statistics
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from shared_tools.email_parser import (
    extract_domain,
    extract_sender_email,
    normalize_subject,
    parse_email_address,
    strip_html_to_text,
)


# ---------------------------------------------------------------------------
# BehavioralContext
# ---------------------------------------------------------------------------

@dataclass
class BehavioralContext:
    """Per-email behavioral predictions for the ReplyGeneratorCrew."""

    sender_profile: dict | None = None
    predicted_intent: str = "acknowledge"
    style_params: dict = field(default_factory=dict)
    matched_examples: list[dict] = field(default_factory=list)
    confidence: float = 0.0

    def to_injection_text(self) -> str:
        """Serialize to the text block injected into the reply agent's backstory."""
        if self.confidence < 0.2 and not self.sender_profile:
            return ""

        lines = []

        # ── Low-confidence guard ────────────────────────────────────
        if self.confidence < 0.4:
            lines.append(
                "NOTE: Limited behavioral data available for this sender/category. "
                "Fall back to the standard style blueprint for greeting, sign-off, "
                "and structure. The sender profile below may be incomplete."
            )
            lines.append("")

        # Sender profile section
        if self.sender_profile:
            sp = self.sender_profile
            tier_label = sp.get("tier_label", "unknown")
            reply_rate = sp.get("reply_rate", 0)
            avg_latency = sp.get("avg_latency_hours")
            greeting = sp.get("preferred_greeting", "N/A")
            signoff = sp.get("signoff_preference", "N/A")
            avg_words = sp.get("avg_reply_words")

            lines.append("SENDER PROFILE:")
            lines.append(
                f"- {sp.get('sender_name', sp.get('sender_email', 'Unknown'))} "
                f"({sp.get('sender_email', '')}) — Tier: {tier_label}"
            )
            lines.append(
                f"- Amy replies to this sender {reply_rate:.0%} of the time"
            )
            if avg_latency is not None:
                lines.append(f"- Typical reply latency: {avg_latency:.1f} hours")
            if avg_words is not None:
                lines.append(f"- Average reply length: {avg_words:.0f} words")
            if greeting:
                lines.append(f"- Preferred greeting: \"{greeting}\"")
            if signoff:
                lines.append(f"- Preferred sign-off: \"{signoff}\"")
            top_intent = sp.get("top_intent", "")
            if top_intent:
                lines.append(f"- Most common reply intent: {top_intent}")
            lines.append("")

        # Predicted intent section
        if self.predicted_intent:
            lines.append(f"PREDICTED INTENT: {self.predicted_intent}")
            lines.append("")

        # Style parameters section
        if self.style_params:
            sp = self.style_params
            lines.append("RECOMMENDED STYLE:")
            greeting = sp.get("greeting_style", "")
            if greeting:
                lines.append(f"- Greeting: \"{greeting}\"")
            signoff = sp.get("signoff", "")
            if signoff:
                lines.append(f"- Sign-off: \"{signoff}\"")
            formality = sp.get("formality")
            if formality is not None:
                formality_label = {1: "very casual", 2: "casual", 3: "neutral",
                                   4: "formal", 5: "very formal"}.get(round(formality), "neutral")
                lines.append(f"- Formality: {formality_label}")
            structure = sp.get("structure_type", "")
            if structure:
                lines.append(f"- Structure: {structure}")
            lines.append("")

        # Matched examples — include actual content for the LLM
        if self.matched_examples:
            lines.append(
                f"HISTORICAL EXAMPLES — Amy's actual replies to similar emails "
                f"({len(self.matched_examples)} found):"
            )
            lines.append("-" * 50)
            for i, ex in enumerate(self.matched_examples, 1):
                lines.append(f"Example {i}:")
                lines.append(f"  Original subject: \"{ex.get('received_subject', '')}\"")
                lines.append(f"  Intent: {ex.get('intent', 'unknown')}")
                reply_snippet = ex.get("reply_body_snippet", "")
                if reply_snippet:
                    lines.append(f"  Amy's reply: {reply_snippet}")
                lines.append("")
            lines.append("-" * 50)
            lines.append(
                "Match the tone, length, and structure of the examples above "
                "when drafting the reply."
            )
            lines.append("")

        lines.append(
            f"Overall confidence in these behavioral predictions: {self.confidence:.0%}"
        )

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# HabitLearnerService
# ---------------------------------------------------------------------------

# Singleton
_habit_service: "HabitLearnerService | None" = None


def get_habit_service() -> "HabitLearnerService":
    """Get or create the singleton HabitLearnerService instance."""
    global _habit_service
    if _habit_service is None:
        _habit_service = HabitLearnerService()
    return _habit_service


class HabitLearnerService(QObject):
    """Learns and infers Amy's email replying habits.

    Signals (emitted from background threads, auto-queued to main thread):
        build_started(total_stages)
        build_progress(current, total, stage_description)
        stage_complete(stage_name, summary_stats_dict)
        build_complete(final_summary_dict)
        build_error(error_string)
        message_parsed(parsed_message_dict)
        pair_matched(pair_dict)
        intent_classified(pair_id, intent_string, confidence_float)
        sender_updated(sender_email, profile_dict)
        fetch_progress(current, total, status_message)
    """

    # ── Signals ──────────────────────────────────────────────────────────
    build_started = pyqtSignal(int)
    build_progress = pyqtSignal(int, int, str)
    stage_complete = pyqtSignal(str, dict)
    build_complete = pyqtSignal(dict)
    build_error = pyqtSignal(str)
    message_parsed = pyqtSignal(dict)
    pair_matched = pyqtSignal(dict)
    intent_classified = pyqtSignal(int, str, float)
    sender_updated = pyqtSignal(str, dict)
    fetch_progress = pyqtSignal(int, int, str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)

        self._running = False
        self._profiles_loaded = False

        # Work queue for async operations
        self._work_queue: queue.Queue = queue.Queue()
        self._llm_semaphore = threading.Semaphore(1)

        # Cached profiles (loaded after build)
        self._sender_profiles: dict[str, dict] = {}
        self._style_matrix: dict[tuple, dict] = {}  # (tier, category) → params
        self._intent_priors_by_category: dict[str, dict] = {}
        self._intent_priors_by_tier: dict[str, dict] = {}

        # Ensure DB is initialized
        from shared_tools.habit_learner_db import init_db
        init_db()

        # Ensure mail_fetch directories exist
        from shared_tools.habit_learner_db import MAIL_FETCH_DIR
        MAIL_FETCH_DIR.mkdir(parents=True, exist_ok=True)
        (MAIL_FETCH_DIR / "inbox").mkdir(parents=True, exist_ok=True)
        (MAIL_FETCH_DIR / "sent").mkdir(parents=True, exist_ok=True)

    # ══════════════════════════════════════════════════════════════════════
    # Public API
    # ══════════════════════════════════════════════════════════════════════

    def build_profiles(self, start_stage: int = 0):
        """Run the learning pipeline in a background thread.

        Args:
            start_stage: Stage to start from (0=FETCH, 1=NORMALIZE, 2=MATCH, etc.)
        """
        t = threading.Thread(
            target=self._run_build_pipeline,
            args=(start_stage,),
            daemon=True,
            name="hl-build",
        )
        t.start()

    def fetch_from_outlook(self, months_back: int = 9):
        """Stage 0 only: Pull inbox + sent emails from Outlook.

        Saves JSON files to mail_fetch/inbox/ and mail_fetch/sent/,
        and inserts into raw_inbox / raw_sent tables.
        """
        t = threading.Thread(
            target=self._run_fetch,
            args=(months_back,),
            daemon=True,
            name="hl-fetch",
        )
        t.start()

    def load_profiles(self) -> bool:
        """Load pre-built profiles from DB into memory. Called after build or at startup."""
        try:
            from shared_tools.habit_learner_db import (
                get_all_sender_profiles,
                get_all_style_entries,
                get_intent_priors,
            )

            self._sender_profiles = {
                p["sender_email"]: p for p in get_all_sender_profiles()
            }

            self._style_matrix = {}
            for entry in get_all_style_entries():
                key = (entry["sender_tier"], entry["category"])
                self._style_matrix[key] = entry

            self._intent_priors_by_category = {}
            self._intent_priors_by_tier = {}
            for ip in get_intent_priors():
                target = (
                    self._intent_priors_by_category
                    if ip["dimension"] == "category"
                    else self._intent_priors_by_tier
                )
                if ip["dimension_value"] not in target:
                    target[ip["dimension_value"]] = {}
                target[ip["dimension_value"]][ip["intent"]] = ip["probability"]

            self._profiles_loaded = True
            return True
        except Exception as e:
            self.build_error.emit(f"Failed to load profiles: {e}")
            return False

    def infer(self, email: dict) -> BehavioralContext | None:
        """Fast inference from pre-built profiles. No LLM call.

        Args:
            email: Dict with keys: sender, subject, body, cc, category, urgency

        Returns:
            BehavioralContext or None if no profiles are loaded.
        """
        if not self._profiles_loaded:
            if not self.load_profiles():
                return None

        sender_raw = email.get("sender", "")
        sender_email = extract_sender_email(sender_raw)
        category = email.get("category", "General")
        sender_domain = extract_domain(sender_email)

        # 1. Look up sender profile
        profile = self._sender_profiles.get(sender_email)
        if not profile:
            # Try domain match
            for email_key, prof in self._sender_profiles.items():
                if prof.get("domain") == sender_domain:
                    profile = prof
                    break

        # 2. Get style params for (tier, category)
        tier = profile.get("tier", 3) if profile else 3
        style_params = self._style_matrix.get((tier, category))
        if not style_params:
            # Fallback: try same tier, any category
            for (t, c), params in self._style_matrix.items():
                if t == tier:
                    style_params = params
                    break

        # 3. Predict intent from priors
        predicted_intent = "acknowledge"
        if category in self._intent_priors_by_category:
            priors = self._intent_priors_by_category[category]
            predicted_intent = max(priors, key=priors.get)
        elif profile and profile.get("top_intent"):
            predicted_intent = profile["top_intent"]

        # 4. Select matched examples
        examples = self._select_examples(sender_email, category, k=3)

        # 5. Compute confidence
        # Confidence reflects how much data backs the predictions.
        # Key factors: sample size, exact-vs-domain match, style coverage.
        confidence = 0.15  # base — profiles are loaded

        if profile:
            # Was this an exact email match or a domain fallback?
            profile_email = profile.get("sender_email", "")
            is_exact_match = (profile_email.lower() == sender_email.lower())
            total_recv = profile.get("total_received", 0)

            if is_exact_match:
                confidence += 0.25
                # Sample bonus: scales from 0→0.35 as total_received goes 0→50
                sample_bonus = min(0.35, (total_recv / 50.0) * 0.35)
                confidence += sample_bonus
            else:
                # Domain-only match — weaker signal
                confidence += 0.10
                sample_bonus = min(0.15, (total_recv / 50.0) * 0.15)
                confidence += sample_bonus

        if style_params:
            # Bonus for having style data for this (tier, category)
            sample_count = style_params.get("sample_count", 0)
            style_bonus = min(0.15, (sample_count / 20.0) * 0.15)
            confidence += style_bonus

        if examples:
            confidence += 0.05

        confidence = min(confidence, 0.90)  # never claim 100% certainty

        return BehavioralContext(
            sender_profile=profile,
            predicted_intent=predicted_intent,
            style_params=style_params or {},
            matched_examples=examples,
            confidence=confidence,
        )

    # ── Sender classification for mail sorting ──────────────────────────

    # Auto-sender detection patterns
    _AUTO_LOCAL_PATTERNS = [
        'noreply', 'no-reply', 'no_reply', 'notification', 'notifications',
        'newsletter', 'mailer', 'mail-noreply', 'marketing', 'promotions',
        'donotreply', 'do-not-reply', 'automated', 'alert', 'alerts',
    ]

    _AUTO_DOMAIN_PATTERNS = [
        'mg.signonsite.com.au',
        'update.procore.com',
        'bounce.',
        'email.openai.com',
        'aumail.docusign.net',
    ]

    _AUTO_SENDER_MATCHES = [
        'fred@fireflies.ai',
        'dse@aumail.docusign.net',
    ]

    def classify_sender(self, sender_email: str) -> str:
        """Classify a sender as 'focused', 'ads_auto', or 'real_sender'.

        Uses learned sender profiles + pattern matching to determine
        whether an email is from a known engaged contact, an automated
        system/newsletter, or an unknown real person.

        Args:
            sender_email: Extracted email address (e.g. 'john@builder.com').

        Returns:
            'focused'    — Amy has replied to this sender before
            'ads_auto'   — Matches known auto-sender / newsletter patterns
            'real_sender' — Real person Amy hasn't engaged with yet
        """
        if not sender_email:
            return "real_sender"

        email_lower = sender_email.lower().strip()

        # Ensure profiles are loaded
        if not self._profiles_loaded:
            self.load_profiles()

        # 1. Check learned sender profiles (strongest signal)
        if email_lower in self._sender_profiles:
            return "focused"

        # 2. Check exact auto-sender matches
        for pattern in self._AUTO_SENDER_MATCHES:
            if pattern.lower() == email_lower:
                return "ads_auto"

        # 3. Check local-part patterns (before the @)
        local_part = email_lower.split("@")[0] if "@" in email_lower else email_lower
        for pattern in self._AUTO_LOCAL_PATTERNS:
            if pattern in local_part:
                return "ads_auto"

        # 4. Check domain patterns
        domain = email_lower.split("@")[-1] if "@" in email_lower else ""
        for pattern in self._AUTO_DOMAIN_PATTERNS:
            if pattern in domain:
                return "ads_auto"

        # 5. Check known sender domains (fuzzy match — but NOT public email providers)
        _public_domains = {
            'gmail.com', 'googlemail.com', 'yahoo.com', 'yahoo.com.au',
            'outlook.com', 'hotmail.com', 'live.com', 'live.com.au',
            'icloud.com', 'me.com', 'proton.me', 'pm.me',
            'aol.com', 'mail.com', 'yandex.com',
        }
        if domain and domain not in _public_domains:
            for known_email in self._sender_profiles:
                known_domain = known_email.split("@")[-1] if "@" in known_email else ""
                if known_domain and known_domain == domain:
                    return "focused"

        # 6. Default: real person, not yet engaged
        return "real_sender"

    def record_feedback(self, entry_id: str, generated_reply: str,
                        actual_reply: str | None, was_sent: bool):
        """Record what Amy actually did for online learning.

        If ``was_sent`` is True and ``actual_reply`` is provided, the sender's
        profile is updated with a moving-average of reply length and latency.
        """
        if not was_sent:
            return

        # This queues an async update — simple for now
        t = threading.Thread(
            target=self._do_record_feedback,
            args=(entry_id, generated_reply, actual_reply),
            daemon=True,
            name="hl-feedback",
        )
        t.start()

    def _do_record_feedback(self, entry_id: str, generated: str, actual: str | None):
        """Background: update sender profile with feedback signal."""
        # For now, track that we had a positive signal.
        # Full implementation would match entry_id to sender and update stats.
        pass

    def get_learning_summary(self) -> dict:
        """Return stats about what was learned."""
        from shared_tools.habit_learner_db import get_learning_summary
        return get_learning_summary()

    def get_unmatched_received(self, limit: int = 100) -> list[dict]:
        """Return received emails that had NO matching reply."""
        from shared_tools.habit_learner_db import get_unmatched_received
        return get_unmatched_received(limit)

    def get_sender_detail(self, sender_email: str) -> dict | None:
        """Full profile for one sender including example replies."""
        from shared_tools.habit_learner_db import (
            get_sender_profile,
            get_all_reply_pairs,
        )
        profile = get_sender_profile(sender_email)
        if not profile:
            return None

        # Find example pairs for this sender
        all_pairs = get_all_reply_pairs()
        examples = [
            {
                "received_subject": p.get("received_subject", ""),
                "reply_body": p.get("reply_body", "")[:300],
                "intent": p.get("intent", ""),
            }
            for p in all_pairs
            if p.get("received_sender") == sender_email
        ][:5]

        profile["examples"] = examples
        return profile

    # ══════════════════════════════════════════════════════════════════════
    # Internal: 5-stage build pipeline
    # ══════════════════════════════════════════════════════════════════════

    def _run_build_pipeline(self, start_stage: int = 0):
        """Execute stages sequentially starting from start_stage."""
        self._running = True
        session_id = None
        errors = []
        skipped_stages = []

        try:
            from shared_tools.habit_learner_db import start_learning_session

            session_id = start_learning_session()
            self.build_started.emit(5 - start_stage)

            if start_stage <= 0:
                self._emit_build_progress(0, 5, "Stage 0/5: Fetching from Outlook...")
                try:
                    stats0 = self._stage_fetch(months_back=9)
                    self.stage_complete.emit("FETCH", stats0)
                except Exception as e:
                    errors.append(f"Stage 0 FETCH failed: {e}")
                    self.build_error.emit(f"Fetch failed: {e}")
            else:
                skipped_stages.append("FETCH")

            if start_stage <= 1:
                self._emit_build_progress(1, 5, "Stage 1/5: Normalizing messages...")
                try:
                    stats1 = self._stage_normalize()
                    self.stage_complete.emit("NORMALIZE", stats1)
                except Exception as e:
                    errors.append(f"Stage 1 NORMALIZE failed: {e}")
                    self.build_error.emit(f"Normalize failed: {e}")
            else:
                skipped_stages.append("NORMALIZE")

            if start_stage <= 2:
                self._emit_build_progress(2, 5, "Stage 2/5: Matching replies...")
                try:
                    stats2 = self._stage_match()
                    self.stage_complete.emit("MATCH", stats2)
                except Exception as e:
                    errors.append(f"Stage 2 MATCH failed: {e}")
                    self.build_error.emit(f"Match failed: {e}")
            else:
                skipped_stages.append("MATCH")

            if start_stage <= 3:
                self._emit_build_progress(3, 5, "Stage 3/5: Classifying reply pairs...")
                try:
                    stats3 = self._stage_classify()
                    self.stage_complete.emit("CLASSIFY", stats3)
                except Exception as e:
                    errors.append(f"Stage 3 CLASSIFY failed: {e}")
                    self.build_error.emit(f"Classify failed: {e}")
            else:
                skipped_stages.append("CLASSIFY")

            if start_stage <= 4:
                self._emit_build_progress(4, 5, "Stage 4/5: Building sender profiles...")
                try:
                    stats4 = self._stage_build()
                    self.stage_complete.emit("BUILD", stats4)
                except Exception as e:
                    errors.append(f"Stage 4 BUILD failed: {e}")
                    self.build_error.emit(f"Build failed: {e}")
            else:
                skipped_stages.append("BUILD")

            # ── Load into memory ─────────────────────────────────────
            self.load_profiles()

            # ── Final summary ────────────────────────────────────────
            summary = self.get_learning_summary()
            summary["errors"] = errors

            # Update session
            if session_id:
                from shared_tools.habit_learner_db import (
                    complete_learning_session,
                    get_sent_message_count,
                    get_received_message_count,
                    get_pair_count,
                    get_unmatched_received_count,
                    get_sender_count,
                )
                complete_learning_session(session_id, {
                    "parsed_messages": get_sent_message_count() + get_received_message_count(),
                    "matched_pairs": get_pair_count(),
                    "unmatched_received": get_unmatched_received_count(),
                    "senders_discovered": get_sender_count(),
                    "errors": errors,
                })

            self.build_complete.emit(summary)

        except Exception as e:
            self.build_error.emit(f"Build pipeline failed: {e}")
        finally:
            self._running = False

    def _emit(self, signal, *args):
        """Emit only if still running."""
        if self._running:
            signal.emit(*args)

    def _emit_build_progress(self, current: int, total: int, msg: str):
        self._emit(self.build_progress, current, total, msg)

    # ── Stage 0: Fetch ───────────────────────────────────────────────────

    def _stage_fetch(self, months_back: int = 9) -> dict:
        """Fetch inbox + sent emails from Outlook, save as JSON + insert into DB.

        Uses date-based pagination: each batch fetches emails older than the
        previous batch's oldest email, so we progressively scan backward
        through the mailbox without re-fetching the same emails.
        """
        from datetime import datetime, timezone, timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(days=months_back * 30))
        cutoff_str = cutoff.strftime("%Y-%m-%dT00:00:00")

        from shared_tools.outlook_tool import fetch_inbox_emails, fetch_sent_emails
        from shared_tools.habit_learner_db import (
            MAIL_FETCH_DIR, insert_raw_inbox, insert_raw_sent,
            get_raw_inbox_count, get_raw_sent_count,
        )

        inbox_count = 0
        sent_count = 0
        batch_size = 200

        # ── COM retry helper ──────────────────────────────────────
        def _fetch_with_retry(fetcher, **kwargs):
            """Retry Outlook fetch with backoff on RPC exhaustion."""
            import time as _time
            last_err = None
            for attempt in range(4):
                try:
                    return fetcher(**kwargs)
                except Exception as e:
                    last_err = e
                    msg = str(e)
                    # RPC / shared-resource errors
                    if any(kw in msg.lower() for kw in
                           ('rpc', 'shared', '0x80010105', '0x800706bf',
                            'call was rejected', '被呼叫', '用完了')):
                        wait = (attempt + 1) * 3
                        self._emit(self.fetch_progress, 0, 1,
                                   f"Outlook resource busy, retrying in {wait}s...")
                        _time.sleep(wait)
                        continue
                    raise  # non-retryable
            raise last_err  # all retries exhausted

        # ── Fetch inbox with date-based pagination ─────────────────
        self._emit(self.fetch_progress, 0, 1, "Fetching inbox emails...")
        exclude_ids: set[str] = set()
        page_before = None  # None = start from newest

        while self._running:
            batch = _fetch_with_retry(
                fetch_inbox_emails,
                count=batch_size,
                max_body=10000,
                unread_only=False,
                received_after=cutoff_str,
                received_before=page_before,
                exclude_entry_ids=exclude_ids,
            )
            if not batch:
                break

            oldest_in_batch = None
            for em in batch:
                if not self._running:
                    break
                self._save_raw_inbox(em)
                exclude_ids.add(em.get("entry_id", ""))
                inbox_count += 1
                # Track the oldest timestamp in this batch for next page
                rt = em.get("received_time", "")
                if rt and (oldest_in_batch is None or rt < oldest_in_batch):
                    oldest_in_batch = rt

            self._emit(self.fetch_progress, inbox_count, inbox_count,
                       f"Fetched {inbox_count} inbox emails...")

            if len(batch) < batch_size:
                break  # exhausted

            # Next page: emails strictly older than the oldest in this batch
            if oldest_in_batch:
                page_before = oldest_in_batch
            else:
                break  # can't determine pagination boundary

        # ── Fetch sent items with date-based pagination ─────────────
        self._emit(self.fetch_progress, 0, 1, "Fetching sent emails...")
        exclude_ids.clear()
        page_before = None

        while self._running:
            batch = _fetch_with_retry(
                fetch_sent_emails,
                count=batch_size,
                max_body=10000,
                sent_after=cutoff_str,
                sent_before=page_before,
                exclude_entry_ids=exclude_ids,
            )
            if not batch:
                break

            oldest_in_batch = None
            for em in batch:
                if not self._running:
                    break
                self._save_raw_sent(em)
                exclude_ids.add(em.get("entry_id", ""))
                sent_count += 1
                st = em.get("sent_time", "")
                if st and (oldest_in_batch is None or st < oldest_in_batch):
                    oldest_in_batch = st

            self._emit(self.fetch_progress, sent_count, sent_count,
                       f"Fetched {sent_count} sent emails...")

            if len(batch) < batch_size:
                break

            if oldest_in_batch:
                page_before = oldest_in_batch
            else:
                break

        return {
            "inbox_fetched": inbox_count,
            "sent_fetched": sent_count,
            "cutoff_date": cutoff_str,
            "raw_inbox_total": get_raw_inbox_count(),
            "raw_sent_total": get_raw_sent_count(),
        }

    def _save_raw_inbox(self, email: dict):
        """Save a raw inbox email to JSON file + DB."""
        from shared_tools.habit_learner_db import MAIL_FETCH_DIR, insert_raw_inbox

        entry_id = email.get("entry_id", "")
        sender_raw = email.get("sender", "")
        sender_name, sender_email = parse_email_address(sender_raw)

        # Save JSON file for visualization
        json_path = ""
        try:
            import hashlib
            inbox_dir = MAIL_FETCH_DIR / "inbox"
            # Use MD5 hash of entry_id for a fixed-length unique filename.
            # Outlook EntryIDs are 140+ hex chars with the unique portion
            # at the END — simple truncation would collide across emails
            # from the same PST folder.
            safe_id = hashlib.md5(entry_id.encode()).hexdigest() if entry_id else "unknown"
            json_path = str(inbox_dir / f"{safe_id}.json")
            record = {
                "entry_id": entry_id,
                "subject": email.get("subject", ""),
                "sender_name": sender_name,
                "sender_email": sender_email,
                "received_time": email.get("received_time", ""),
                "body_plain": email.get("body", "")[:10000],
                "cc": email.get("cc", ""),
                "conversation_id": email.get("conversation_id", ""),
            }
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        insert_raw_inbox({
            "entry_id": entry_id,
            "subject": email.get("subject", ""),
            "sender_name": sender_name,
            "sender_email": sender_email,
            "recipients_to": email.get("recipients_to", "[]"),
            "recipients_cc": email.get("cc", "[]"),
            "body_plain": email.get("body", "")[:10000],
            "body_html": email.get("body_html", ""),
            "received_time": email.get("received_time", ""),
            "conversation_id": email.get("conversation_id", ""),
            "json_path": json_path,
        })

    def _save_raw_sent(self, email: dict):
        """Save a raw sent email to JSON file + DB."""
        from shared_tools.habit_learner_db import MAIL_FETCH_DIR, insert_raw_sent

        entry_id = email.get("entry_id", "")
        sender_raw = email.get("sender", "")
        sender_name, sender_email = parse_email_address(sender_raw)

        # Save JSON file for visualization
        json_path = ""
        try:
            import hashlib
            sent_dir = MAIL_FETCH_DIR / "sent"
            # Use MD5 hash for fixed-length unique filename (see _save_raw_inbox)
            safe_id = hashlib.md5(entry_id.encode()).hexdigest() if entry_id else "unknown"
            json_path = str(sent_dir / f"{safe_id}.json")
            record = {
                "entry_id": entry_id,
                "subject": email.get("subject", ""),
                "sender_name": sender_name,
                "sender_email": sender_email,
                "recipients_to": email.get("recipients_to", ""),
                "recipients_cc": email.get("recipients_cc", ""),
                "sent_time": email.get("sent_time", ""),
                "body_plain": email.get("body", "")[:10000],
                "conversation_id": email.get("conversation_id", ""),
            }
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        norm_subj = normalize_subject(email.get("subject", ""))

        insert_raw_sent({
            "entry_id": entry_id,
            "subject": email.get("subject", ""),
            "sender_name": sender_name,
            "sender_email": sender_email,
            "recipients_to": email.get("recipients_to", "[]"),
            "recipients_cc": email.get("recipients_cc", "[]"),
            "body_plain": email.get("body", "")[:10000],
            "body_html": email.get("body_html", ""),
            "sent_time": email.get("sent_time", ""),
            "conversation_id": email.get("conversation_id", ""),
            "thread_subject_norm": norm_subj,
            "json_path": json_path,
        })

    # ── Stage 1: Normalize ────────────────────────────────────────────────

    def _stage_normalize(self) -> dict:
        """Extract normalized messages from raw_inbox / raw_sent tables."""
        from shared_tools.habit_learner_db import (
            get_raw_inbox_emails, get_raw_sent_emails,
            insert_sent_message, insert_received_message,
        )
        import hashlib

        sent_count = 0
        received_count = 0

        # Normalize sent emails
        for raw in get_raw_sent_emails():
            if not self._running:
                break
            body = raw.get("body_plain", "") or strip_html_to_text(raw.get("body_html", ""))
            message_id = hashlib.md5(
                f"sent:{raw.get('entry_id', '')}".encode()
            ).hexdigest()

            msg = {
                "message_id": message_id,
                "sender_name": raw.get("sender_name", ""),
                "sender_email": raw.get("sender_email", ""),
                "recipients_to": raw.get("recipients_to", "[]"),
                "recipients_cc": raw.get("recipients_cc", "[]"),
                "subject": raw.get("subject", ""),
                "body_plain": body[:5000],
                "timestamp": raw.get("sent_time", ""),
                "thread_subject_norm": raw.get("thread_subject_norm", ""),
                "source_entry_id": raw.get("entry_id", ""),
                "conversation_id": raw.get("conversation_id", ""),
            }
            rid = insert_sent_message(msg)
            if rid:
                sent_count += 1
                self._emit(self.message_parsed, {**msg, "type": "sent", "id": rid})

        # Normalize inbox emails (received messages)
        for raw in get_raw_inbox_emails():
            if not self._running:
                break
            body = raw.get("body_plain", "") or strip_html_to_text(raw.get("body_html", ""))
            message_id = hashlib.md5(
                f"received:{raw.get('entry_id', '')}".encode()
            ).hexdigest()
            norm_subj = normalize_subject(raw.get("subject", ""))

            msg = {
                "message_id": message_id,
                "sender_name": raw.get("sender_name", ""),
                "sender_email": raw.get("sender_email", ""),
                "recipients_to": raw.get("recipients_to", "[]"),
                "recipients_cc": raw.get("recipients_cc", "[]"),
                "subject": raw.get("subject", ""),
                "body_plain": body[:5000],
                "timestamp": raw.get("received_time", ""),
                "thread_subject_norm": norm_subj,
                "source_entry_id": raw.get("entry_id", ""),
                "conversation_id": raw.get("conversation_id", ""),
            }
            rid = insert_received_message(msg)
            if rid:
                received_count += 1
                self._emit(self.message_parsed, {**msg, "type": "received", "id": rid})

        return {
            "sent_normalized": sent_count,
            "received_normalized": received_count,
        }

    # ── Stage 2: Match ────────────────────────────────────────────────────

    def _stage_match(self) -> dict:
        """Thread-match received messages to sent replies.

        How reply detection works:
        1. PRIMARY: Match by Outlook conversation_id (most reliable).
           Outlook groups all messages in the same thread under one
           conversation_id, regardless of subject changes.
        2. SECONDARY: Match by normalized subject + temporal proximity.
           Strips RE:/FW: prefixes, matches within a 7-day window,
           and requires the reply to be sent after the received email.

        After matching:
        - ``was_replied = 1`` → Amy replied to this received email
        - ``was_replied = 0`` → No matching sent reply was found
          (Amy received this email but either didn't reply, or her
           reply was outside the 9-month fetch window, or the reply
           was sent from a different device/account)
        """
        from shared_tools.habit_learner_db import (
            get_all_sent_messages, get_all_received_messages,
            insert_reply_pair, update_received_reply,
        )

        sent_msgs = get_all_sent_messages()
        received_msgs = get_all_received_messages()

        matched = 0
        matched_by_conversation = 0
        matched_by_subject = 0
        unmatched_sent = 0

        # Track which received messages have been claimed
        claimed_received_ids: set[int] = set()

        # ── Round 1: Match by conversation_id ────────────────────
        # Build received index by conversation_id
        received_by_conv: dict[str, list[dict]] = {}
        for rm in received_msgs:
            cid = (rm.get("conversation_id") or "").strip()
            if cid:
                received_by_conv.setdefault(cid, []).append(rm)

        for sm in sent_msgs:
            if not self._running:
                break
            cid = (sm.get("conversation_id") or "").strip()
            if not cid:
                continue

            candidates = received_by_conv.get(cid, [])
            best_match = None
            best_latency = float("inf")
            sent_ts = sm.get("timestamp", "")

            for rm in candidates:
                if rm["id"] in claimed_received_ids:
                    continue
                recv_ts = rm.get("timestamp", "")
                latency = self._compute_latency_hours(recv_ts, sent_ts)
                if latency is None:
                    continue
                if latency < -1:  # sent before received (clock skew margin)
                    continue
                if latency > 720:  # > 30 days — unlikely direct reply
                    continue
                if 0 <= latency < best_latency:
                    best_latency = latency
                    best_match = rm

            if best_match and best_latency < 720:
                pair_id = insert_reply_pair({
                    "received_id": best_match["id"],
                    "reply_id": sm["id"],
                    "latency_hours": max(0, best_latency),
                })
                if pair_id:
                    update_received_reply(best_match["id"], sm["id"], max(0, best_latency))
                    claimed_received_ids.add(best_match["id"])
                    matched += 1
                    matched_by_conversation += 1
                    self._emit(self.pair_matched, {
                        "pair_id": pair_id,
                        "received_subject": best_match.get("subject", ""),
                        "reply_subject": sm.get("subject", ""),
                        "latency_hours": max(0, best_latency),
                        "sender_email": best_match.get("sender_email", ""),
                        "match_method": "conversation_id",
                    })

        # ── Round 2: Match by normalized subject ─────────────────
        # Build received index by normalized subject (only unmatched ones)
        received_by_subject: dict[str, list[dict]] = {}
        for rm in received_msgs:
            if rm["id"] in claimed_received_ids:
                continue
            key = rm.get("thread_subject_norm", "").lower().strip()
            if key:
                received_by_subject.setdefault(key, []).append(rm)

        for sm in sent_msgs:
            if not self._running:
                break
            norm_subj = sm.get("thread_subject_norm", "").lower().strip()
            candidates = received_by_subject.get(norm_subj, [])

            best_match = None
            best_latency = float("inf")
            sent_ts = sm.get("timestamp", "")

            for rm in candidates:
                if rm["id"] in claimed_received_ids:
                    continue
                recv_ts = rm.get("timestamp", "")
                latency = self._compute_latency_hours(recv_ts, sent_ts)
                if latency is None:
                    continue
                if latency < -1:
                    continue
                if latency > 720:
                    continue
                if 0 <= latency < best_latency:
                    best_latency = latency
                    best_match = rm

            if best_match and best_latency < 720:
                pair_id = insert_reply_pair({
                    "received_id": best_match["id"],
                    "reply_id": sm["id"],
                    "latency_hours": max(0, best_latency),
                })
                if pair_id:
                    update_received_reply(best_match["id"], sm["id"], max(0, best_latency))
                    claimed_received_ids.add(best_match["id"])
                    matched += 1
                    matched_by_subject += 1
                    self._emit(self.pair_matched, {
                        "pair_id": pair_id,
                        "received_subject": best_match.get("subject", ""),
                        "reply_subject": sm.get("subject", ""),
                        "latency_hours": max(0, best_latency),
                        "sender_email": best_match.get("sender_email", ""),
                        "match_method": "subject",
                    })
            else:
                unmatched_sent += 1

        # Count unmatched received (Amy received but no reply found)
        unmatched_received = sum(
            1 for rm in received_msgs
            if rm["id"] not in claimed_received_ids
        )

        return {
            "total_sent": len(sent_msgs),
            "total_received": len(received_msgs),
            "matched_pairs": matched,
            "matched_by_conversation_id": matched_by_conversation,
            "matched_by_subject": matched_by_subject,
            "unmatched_sent": unmatched_sent,
            "unmatched_received": unmatched_received,
            "note": (
                "unmatched_received = emails Amy received but no reply was found "
                "(either she didn't reply, or the reply is outside the fetch window)"
            ),
        }

    def _compute_latency_hours(self, received_time: str, sent_time: str) -> float | None:
        """Compute latency in hours between received and sent times.

        Handles Outlook timestamps which look like:
            "2026-06-12 23:17:27.189000+00:00"  (space-separated, microsecond, tz)
            "2026-06-12T23:17:27+00:00"          (T-separated, no microsecond, tz)
            "2026-06-12 23:17:27"                (no tz)
        """
        from datetime import datetime, timezone

        def parse_ts(ts_str: str):
            if not ts_str:
                return None

            ts = ts_str.strip()

            # Normalize trailing timezone: +00:00 or +0000 or Z
            tz_offset = None
            if ts.endswith("Z"):
                ts = ts[:-1] + "+00:00"
            # Check for +HH:MM or -HH:MM at the end
            import re
            tz_m = re.search(r'([+-]\d{2}:\d{2})$', ts)
            if tz_m:
                tz_str = tz_m.group(1)
                ts = ts[:tz_m.start()].strip()
                hours, minutes = int(tz_str[1:3]), int(tz_str[4:6])
                if tz_str[0] == '-':
                    hours, minutes = -hours, -minutes
                tz_offset = timezone(__import__('datetime').timedelta(hours=hours, minutes=minutes))

            # Microsecond formats (most specific first)
            micro_formats = [
                "%Y-%m-%d %H:%M:%S.%f",      # 2026-06-12 23:17:27.189000
                "%Y-%m-%dT%H:%M:%S.%f",       # 2026-06-12T23:17:27.189000
            ]
            for fmt in micro_formats:
                try:
                    dt = datetime.strptime(ts, fmt)
                    if tz_offset:
                        dt = dt.replace(tzinfo=tz_offset)
                    return dt
                except ValueError:
                    continue

            # Non-microsecond formats
            basic_formats = [
                "%Y-%m-%d %H:%M:%S",          # 2026-06-12 23:17:27
                "%Y-%m-%dT%H:%M:%S",           # 2026-06-12T23:17:27
            ]
            for fmt in basic_formats:
                try:
                    dt = datetime.strptime(ts, fmt)
                    if tz_offset:
                        dt = dt.replace(tzinfo=tz_offset)
                    return dt
                except ValueError:
                    continue

            # Last resort: try fromisoformat (Python 3.7+)
            try:
                return datetime.fromisoformat(ts_str)
            except (ValueError, AttributeError):
                return None

        rt = parse_ts(received_time) if received_time else None
        st = parse_ts(sent_time) if sent_time else None

        if rt and st:
            # Make both naive or both aware for subtraction
            if rt.tzinfo and not st.tzinfo:
                st = st.replace(tzinfo=rt.tzinfo)
            elif st.tzinfo and not rt.tzinfo:
                rt = rt.replace(tzinfo=st.tzinfo)
            return (st - rt).total_seconds() / 3600.0
        return None

    # ── Stage 3: Classify ─────────────────────────────────────────────────

    def _stage_classify(self) -> dict:
        """LLM-classify each reply pair for intent and style features."""
        from shared_tools.habit_learner_db import (
            get_all_reply_pairs, get_unclassified_pairs,
            update_reply_pair_classification, get_pair_count,
        )
        from shared_tools.llm_config import get_llm

        pairs = get_unclassified_pairs()
        # Also classify any pairs that exist
        if not pairs:
            pairs = get_all_reply_pairs()
            pairs = [p for p in pairs if not p.get("intent")]

        total = len(pairs)
        classified = 0
        errors = 0

        for i, pair in enumerate(pairs):
            if not self._running:
                break

            self._emit_build_progress(
                i + 1, total,
                f"Classifying reply {i+1}/{total}: "
                f"{pair.get('received_subject', '')[:40]}..."
            )

            try:
                # Build classification prompt
                received_body = (pair.get("received_body", "") or "")[:500]
                reply_body = (pair.get("reply_body", "") or "")[:500]

                prompt = (
                    "Classify this email reply pair. Return ONLY valid JSON, "
                    "no markdown, no explanation.\n\n"
                    f"ORIGINAL EMAIL (received by Amy):\n"
                    f"From: {pair.get('received_sender', '')}\n"
                    f"Subject: {pair.get('received_subject', '')}\n"
                    f"Body: {received_body}\n\n"
                    f"AMY'S REPLY:\n"
                    f"Body: {reply_body}\n\n"
                    "Return JSON with these exact keys:\n"
                    '{"intent": "acknowledge|answer|ask_clarification|defer_redirect|'
                    "commit_to_action|decline|social|escalate|close_loop\",\n"
                    '"formality_level": 1-5,\n'
                    '"structure_type": "full_4part|brief_ack|defer|answer_only|cc_note",\n'
                    '"contains_question": true/false,\n'
                    '"contains_commitment": true/false,\n'
                    '"greeting_used": "Hi John," or null,\n'
                    '"signoff_used": "Kind regards," or null,\n'
                    '"confidence": 0.0-1.0\n'
                    '}'
                )

                with self._llm_semaphore:
                    llm = get_llm("fast")
                    # Use direct chat completion via the LLM
                    try:
                        response = llm.call(messages=[{"role": "user", "content": prompt}])
                    except AttributeError:
                        # Fallback: try the crewai LLM interface
                        from crewai import LLM
                        response_text = str(llm)
                        response = str(response_text)

                # Parse response
                result = self._parse_classification_response(
                    response if isinstance(response, str) else str(response)
                )

                if result:
                    # Extract text-based features directly from reply body
                    # (more reliable than LLM for mechanical features)
                    text_features = self._extract_text_features(reply_body)
                    result.update(text_features)

                    update_reply_pair_classification(pair["id"], result)
                    self._emit(self.intent_classified,
                               pair["id"],
                               result.get("intent", "unknown"),
                               result.get("confidence", 0.5))
                    classified += 1

            except Exception as e:
                errors += 1
                self._emit(self.build_error,
                           f"Classification error for pair {pair['id']}: {e}")

        return {
            "total_pairs": get_pair_count(),
            "classified": classified,
            "errors": errors,
        }

    def _parse_classification_response(self, raw: str) -> dict | None:
        """Parse LLM classification response into a dict."""
        if not raw:
            return None

        cleaned = raw.strip()
        # Extract JSON block if wrapped in markdown
        if "```" in cleaned:
            for part in cleaned.split("```"):
                part = part.strip()
                if part.startswith("{"):
                    cleaned = part
                    break

        # Try to find a JSON object
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start:end + 1]

        try:
            parsed = json.loads(cleaned)
            return {
                "intent": parsed.get("intent", ""),
                "formality_level": parsed.get("formality_level"),
                "greeting_used": parsed.get("greeting_used"),
                "signoff_used": parsed.get("signoff_used"),
                "structure_type": parsed.get("structure_type", ""),
                "contains_question": 1 if parsed.get("contains_question") else 0,
                "contains_commitment": 1 if parsed.get("contains_commitment") else 0,
                "confidence": parsed.get("confidence", 0.5),
            }
        except (json.JSONDecodeError, AttributeError):
            return None

    # ── Stage 4: Build profiles ───────────────────────────────────────────

    def _stage_build(self) -> dict:
        """Compute statistical profiles from labeled reply pairs."""
        from shared_tools.habit_learner_db import (
            get_all_reply_pairs, get_all_sender_profiles, get_sender_profile,
            upsert_sender_profile, upsert_style_entry, upsert_intent_prior,
            update_reply_pair_classification,
        )

        all_pairs = get_all_reply_pairs()

        # Backfill text features (greeting, signoff, word count) for all pairs
        # These are extracted deterministically from reply body — no LLM needed
        backfilled = 0
        for p in all_pairs:
            if not p.get("greeting_used") or p.get("reply_word_count", 0) == 0:
                reply_body = p.get("reply_body") or ""
                if reply_body:
                    features = self._extract_text_features(reply_body)
                    features["intent"] = p.get("intent")  # preserve LLM classification
                    features["formality_level"] = p.get("formality_level")
                    features["structure_type"] = p.get("structure_type")
                    features["contains_question"] = p.get("contains_question", 0)
                    features["contains_commitment"] = p.get("contains_commitment", 0)
                    features["confidence"] = p.get("classification_confidence")
                    update_reply_pair_classification(p["id"], features)
                    backfilled += 1
        if backfilled:
            # Re-fetch pairs with updated features
            all_pairs = get_all_reply_pairs()

        # Group by sender email
        sender_pairs: dict[str, list[dict]] = {}
        for p in all_pairs:
            se = p.get("received_sender", "")
            if se not in sender_pairs:
                sender_pairs[se] = []
            sender_pairs[se].append(p)

        # Build sender profiles
        sender_count = 0
        for sender_email, pairs in sender_pairs.items():
            if not self._running:
                break
            profile = self._compute_sender_profile(sender_email, pairs)
            upsert_sender_profile(profile)
            self._emit(self.sender_updated, sender_email, profile)
            sender_count += 1

        # Build style matrix (tier × category)
        for sender_email, pairs in sender_pairs.items():
            if not self._running:
                break
            profile = get_sender_profile(sender_email)
            tier = profile.get("tier", 3) if profile else 3

            # Group pairs by category (extracted from received subject/body)
            by_category: dict[str, list[dict]] = {}
            for p in pairs:
                cat = self._infer_category(p)
                by_category.setdefault(cat, []).append(p)

            for cat, cat_pairs in by_category.items():
                entry = self._compute_style_entry(tier, cat, cat_pairs)
                upsert_style_entry(entry)

        # Build intent priors by category
        intent_by_cat: dict[str, Counter] = {}
        for p in all_pairs:
            intent = p.get("intent", "")
            if not intent:
                continue
            cat = self._infer_category(p)
            intent_by_cat.setdefault(cat, Counter())[intent] += 1

        for cat, counter in intent_by_cat.items():
            total = sum(counter.values())
            for intent, cnt in counter.items():
                upsert_intent_prior("category", cat, intent,
                                    cnt / total if total > 0 else 0, cnt)

        # Build intent priors by sender tier
        # (already have profiles, can infer)
        intent_by_tier: dict[int, Counter] = {}
        for p in all_pairs:
            intent = p.get("intent", "")
            if not intent:
                continue
            se = p.get("received_sender", "")
            if se in sender_pairs:
                # Guess tier from sender email + pair count
                tier = self._guess_tier(se, len(sender_pairs[se]))
                intent_by_tier.setdefault(tier, Counter())[intent] += 1

        for tier, counter in intent_by_tier.items():
            total = sum(counter.values())
            for intent, cnt in counter.items():
                upsert_intent_prior("sender_tier", f"tier_{tier}", intent,
                                    cnt / total if total > 0 else 0, cnt)

        return {
            "senders_profiled": sender_count,
            "style_entries": sum(
                1 for s in sender_pairs.values()
                for _ in [self._infer_category(p) for p in s]
            ),
            "senders_discovered": sender_count,
        }

    def _compute_sender_profile(self, sender_email: str,
                                 pairs: list[dict]) -> dict:
        """Compute a sender profile from their reply pairs.

        Reply rate is computed from the full received_messages table
        (not just reply_pairs) so unreplied emails are counted correctly.
        """
        latencies = [p.get("latency_hours", 0) for p in pairs if p.get("latency_hours")]
        reply_words = [p.get("reply_word_count", 0) for p in pairs if p.get("reply_word_count")]
        intents = [p.get("intent", "") for p in pairs if p.get("intent")]
        greetings = [p.get("greeting_used", "") for p in pairs if p.get("greeting_used")]
        signoffs = [p.get("signoff_used", "") for p in pairs if p.get("signoff_used")]
        formalities = [p.get("formality_level", 0) for p in pairs if p.get("formality_level")]

        # ── Real received/replied counts from received_messages ─────
        from shared_tools.habit_learner_db import get_sender_received_stats
        stats = get_sender_received_stats(sender_email)
        total_received = stats["total_received"]
        total_replied = stats["total_replied"]
        # If no received_messages exist for this sender (e.g. pre-existing
        # pairs from before normalization was added), fall back to pairs count
        if total_received == 0:
            total_received = len(pairs)
            total_replied = len(pairs)
        reply_rate = (total_replied / total_received) if total_received > 0 else 0.0

        # Most common intent
        top_intent = max(set(intents), key=intents.count) if intents else ""

        # Most common greeting
        greeting_counts = Counter(g for g in greetings if g)
        preferred_greeting = greeting_counts.most_common(1)[0][0] if greeting_counts else ""

        # Most common signoff
        signoff_counts = Counter(s for s in signoffs if s)
        signoff_preference = signoff_counts.most_common(1)[0][0] if signoff_counts else ""

        tier = self._guess_tier(sender_email, total_received)
        domain = extract_domain(sender_email)

        return {
            "sender_email": sender_email,
            "sender_name": pairs[0].get("received_sender_name", ""),
            "domain": domain,
            "tier": tier,
            "tier_label": self._tier_label(tier),
            "total_received": total_received,
            "total_replied": total_replied,
            "reply_rate": reply_rate,
            "avg_latency_hours": statistics.mean(latencies) if latencies else None,
            "latency_std_hours": statistics.stdev(latencies) if len(latencies) > 1 else 0,
            "preferred_greeting": preferred_greeting,
            "avg_reply_words": statistics.mean(reply_words) if reply_words else None,
            "formality_level": statistics.mean(formalities) if formalities else None,
            "top_intent": top_intent,
            "signoff_preference": signoff_preference,
            "last_updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    def _compute_style_entry(self, tier: int, category: str,
                              pairs: list[dict]) -> dict:
        """Compute conditional style parameters for (tier, category)."""
        words = [p.get("reply_word_count", 0) for p in pairs if p.get("reply_word_count")]
        formalities = [p.get("formality_level", 0) for p in pairs if p.get("formality_level")]
        structures = [p.get("structure_type", "") for p in pairs if p.get("structure_type")]
        greetings = [p.get("greeting_used", "") for p in pairs if p.get("greeting_used")]
        signoffs = [p.get("signoff_used", "") for p in pairs if p.get("signoff_used")]

        greeting_counts = Counter(g for g in greetings if g)
        signoff_counts = Counter(s for s in signoffs if s)
        structure_counts = Counter(s for s in structures if s)

        # Representative example IDs
        example_ids = [p.get("id") for p in pairs[:3]]

        return {
            "sender_tier": tier,
            "category": category,
            "avg_words": statistics.mean(words) if words else None,
            "formality": statistics.mean(formalities) if formalities else None,
            "greeting_style": greeting_counts.most_common(1)[0][0] if greeting_counts else "",
            "signoff": signoff_counts.most_common(1)[0][0] if signoff_counts else "",
            "uses_bullet_points": 0.0,  # TODO: detect from body
            "structure_type": structure_counts.most_common(1)[0][0] if structure_counts else "",
            "sample_count": len(pairs),
            "examples_json": json.dumps(example_ids),
        }

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_text_features(reply_body: str) -> dict:
        """Extract greeting, signoff, word/paragraph count from reply text.
        Deterministic — no LLM needed. More reliable than LLM extraction."""
        if not reply_body:
            return {
                "greeting_used": None, "signoff_used": None,
                "reply_word_count": 0, "reply_paragraph_count": 0,
                "uses_bullet_points": 0,
            }

        import re

        lines = reply_body.strip().split('\n')

        # Extract greeting from first 2 lines
        greeting = None
        for line in lines[:2]:
            line = line.strip()
            # Match "Hi Name," "Dear Name," "Hello Name," "Name," etc.
            m = re.match(
                r'^(?:Hi|Dear|Hello|Hey|Good\s*(?:morning|afternoon|evening))\s+'
                r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s*,?\s*$',
                line, re.IGNORECASE
            )
            if m:
                greeting = line
                break

        # Extract signoff from last 2 lines
        signoff = None
        signoff_patterns = [
            r'^(?:Kind\s+regards|Best\s+regards|Warm\s+regards|Regards|Cheers|Thanks|Thank\s+you|Sincerely|Yours\s+(?:sincerely|truly|faithfully)|All\s+the\s+best|Best|Take\s+care|Talk\s+soon)[,.]?\s*$',
        ]
        for line in reversed(lines[-3:]):
            line = line.strip()
            for pat in signoff_patterns:
                if re.match(pat, line, re.IGNORECASE):
                    signoff = line
                    break
            if signoff:
                break

        # Word count
        words = reply_body.split()
        wc = len(words)

        # Paragraph count (separated by blank lines)
        paragraphs = re.split(r'\n\s*\n', reply_body.strip())
        pc = len([p for p in paragraphs if p.strip()])

        # Bullet points
        bullet_count = len(re.findall(r'^\s*[-•*#]\s', reply_body, re.MULTILINE))
        uses_bullets = 1 if bullet_count >= 2 else 0

        return {
            "greeting_used": greeting,
            "signoff_used": signoff,
            "reply_word_count": wc,
            "reply_paragraph_count": pc,
            "uses_bullet_points": uses_bullets,
        }

    def _guess_tier(self, sender_email: str, total_received: int) -> int:
        """Guess sender tier from domain and volume."""
        domain = (sender_email or "").lower().split("@")[-1] if "@" in (sender_email or "") else ""

        # Amy's own domain or known internal domains
        internal_domains = {"welink.com.au", "meritor.com.au"}
        if domain in internal_domains:
            return 1  # internal_team

        # Government / authorities
        if domain.endswith(".gov.au") or domain.endswith(".gov"):
            return 4  # authority

        # High volume external → client
        if total_received >= 10:
            return 2  # client

        # Moderate volume → vendor/subcontractor
        if total_received >= 3:
            return 3  # vendor

        return 5  # occasional

    @staticmethod
    def _tier_label(tier: int) -> str:
        return {1: "internal_team", 2: "client",
                3: "vendor", 4: "authority",
                5: "occasional", 6: "unknown"}.get(tier, "unknown")

    @staticmethod
    def _infer_category(pair: dict) -> str:
        """Infer a construction category from the pair's subject/body."""
        subject = (pair.get("received_subject", "") or "").lower()
        body = (pair.get("received_body", "") or "").lower()
        combined = subject + " " + body[:200]

        keywords = {
            "RFI": ["rfi", "request for information"],
            "Submittal": ["submittal", "submission", "approval", "drawing"],
            "Financial": ["invoice", "payment", "claim", "variation", "cost", "po ", "purchase order"],
            "Scheduling": ["program", "schedule", "deadline", "delay", "extension", "eot"],
            "Safety": ["safety", "swms", "incident", "hazard", "induction"],
            "Site Visit": ["site visit", "inspection", "walkthrough"],
            "Contract": ["contract", "agreement", "scope", "deed"],
            "Progress Claim": ["progress claim", "progress payment"],
            "HR": ["staff", "personnel", "leave", "recruitment"],
        }

        for cat, kws in keywords.items():
            for kw in kws:
                if kw in combined:
                    return cat
        return "General"

    def _select_examples(self, sender_email: str, category: str,
                          k: int = 3) -> list[dict]:
        """Select K most behaviorally similar historical replies."""
        from shared_tools.habit_learner_db import get_all_reply_pairs

        all_pairs = get_all_reply_pairs()
        if not all_pairs:
            return []

        # Score each pair
        scored = []
        for p in all_pairs:
            score = 0
            # Same sender email
            if p.get("received_sender") == sender_email:
                score += 5
            # Same domain
            sender_domain = extract_domain(sender_email)
            pair_domain = extract_domain(p.get("received_sender", ""))
            if sender_domain and pair_domain and sender_domain == pair_domain:
                score += 2
            # Same category (inferred)
            pair_cat = self._infer_category(p)
            if pair_cat == category:
                score += 3
            # Has classification
            if p.get("intent"):
                score += 1

            scored.append((score, p))

        # Sort by score descending, take top K
        scored.sort(key=lambda x: x[0], reverse=True)
        # Minimum score threshold: require at least same-domain (+2) + same-category (+3) = 5,
        # or exact sender match (+5) = 5.  Below this, examples are effectively random.
        min_score = 5
        top = [(s, p) for s, p in scored if s >= min_score][:k]

        return [
            {
                "pair_id": p["id"],
                "received_subject": p.get("received_subject", ""),
                "reply_body_snippet": (p.get("reply_body", "") or "")[:200],
                "intent": p.get("intent", ""),
                "score": score,
            }
            for score, p in top
        ]
