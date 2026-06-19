"""Email parsing utilities for the lilAmy platform.

Provides:
  - HTML-to-plain-text conversion using beautifulsoup4 (already a dependency)
  - Email address parsing ("Name <email>" → name, email)
  - Subject normalization (strip RE:/FW:/FWD: prefixes)
  - Datetime parsing for Outlook timestamps

Used by: HabitLearnerService (habit learning pipeline)
"""

import re
import html as _html
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------

def strip_html_to_text(html_content: str) -> str:
    """Convert MSO HTML email body to clean plain text.

    Uses beautifulsoup4 to parse the HTML, removes style/script/head/xml
    blocks, then extracts text with newline separation.  Handles the
    Word-generated HTML that Outlook PST exports produce.

    Args:
        html_content: Raw HTML string (MSO HTML or standard).

    Returns:
        Clean plain text with collapsed whitespace.
    """
    if not html_content:
        return ""

    # Fast path: if it doesn't look like HTML, return as-is
    if "<" not in html_content and ">" not in html_content:
        return html_content

    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html_content, "html.parser")

        # Remove non-content elements
        for tag in soup(["style", "script", "head", "xml", "meta", "link", "title"]):
            tag.decompose()

        # Remove HTML comments and CDATA
        from bs4 import Comment
        for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
            comment.extract()

        text = soup.get_text(separator="\n")

        # Decode HTML entities
        text = _html.unescape(text)

        # Collapse whitespace: keep paragraph breaks (double newlines),
        # remove excessive blank lines
        lines = [line.strip() for line in text.splitlines()]
        lines = [line for line in lines if line]  # remove empty lines
        return "\n".join(lines)

    except ImportError:
        # Fallback: basic regex-based stripping if beautifulsoup4 unavailable
        return _strip_html_regex(html_content)


def _strip_html_regex(html_content: str) -> str:
    """Fallback HTML stripper using regex. Less robust than beautifulsoup4."""
    # Remove style/script blocks
    text = re.sub(r"<style[^>]*>.*?</style>", "", html_content,
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text,
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)

    # Replace common block-level tags with newlines
    for tag in ["br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6"]:
        text = re.sub(rf"</?{tag}[^>]*>", "\n", text, flags=re.IGNORECASE)

    # Replace td with tab
    text = re.sub(r"</?td[^>]*>", "\t", text, flags=re.IGNORECASE)

    # Remove all remaining HTML tags
    text = re.sub(r"<[^>]+>", "", text)

    # Decode common entities
    text = _html.unescape(text)

    # Collapse whitespace
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Email address parsing
# ---------------------------------------------------------------------------

# Matches "Name <email>" or just "email"
_ADDRESS_RE = re.compile(
    r'^\s*(?:"?([^"<]*?)"?\s*)?<?([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})>?\s*$'
)

# Matches "Name <email>" patterns within larger strings (e.g. CC lists)
_ADDRESS_EXTRACT_RE = re.compile(
    r'([^<>,;]+?)\s*<([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})>'
)


def parse_email_address(raw: str) -> tuple[str, str]:
    """Parse a single 'Name <email>' or 'email' string.

    Args:
        raw: String like "Amy Chen <amy@welink.com.au>" or "amy@welink.com.au"

    Returns:
        (name, email) tuple. Name is "" if not present.
    """
    if not raw:
        return ("", "")

    raw = raw.strip()

    # Try "Name <email>" pattern first
    m = _ADDRESS_RE.match(raw)
    if m:
        name = (m.group(1) or "").strip().strip('"')
        email = (m.group(2) or "").strip()
        return (name, email)

    # Fallback: extract just the email
    simple = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', raw)
    if simple:
        return ("", simple.group(0))

    return (raw, "")


def parse_address_list(raw: str) -> list[dict]:
    """Parse a semicolon/comma-separated list of 'Name <email>' strings.

    Args:
        raw: "John <john@x.com>; Mary <mary@y.com>"

    Returns:
        [{"name": "John", "email": "john@x.com"}, ...]
    """
    if not raw:
        return []

    results = []
    for match in _ADDRESS_EXTRACT_RE.finditer(raw):
        results.append({
            "name": match.group(1).strip().strip('"'),
            "email": match.group(2).strip(),
        })

    # If regex didn't match, try splitting and parsing each piece
    if not results:
        for part in re.split(r'[;,]\s*', raw):
            part = part.strip()
            if part:
                name, email = parse_email_address(part)
                if email:
                    results.append({"name": name, "email": email})

    return results


def extract_domain(email: str) -> str:
    """Extract the domain part from an email address."""
    if "@" in email:
        return email.rsplit("@", 1)[-1].lower()
    return ""


def extract_sender_email(sender_raw: str) -> str:
    """Extract just the email address from a sender field like 'Name <email>'."""
    _, email = parse_email_address(sender_raw)
    return email


# ---------------------------------------------------------------------------
# Subject normalization
# ---------------------------------------------------------------------------

# Prefixes to strip for thread matching
_SUBJECT_PREFIX_RE = re.compile(
    r'^(?:RE|FW|FWD|AW|WG|答复|转发)\s*:\s*',
    re.IGNORECASE,
)

# Nested prefixes: "RE: RE: FW: Subject"
_SUBJECT_PREFIX_CHAIN_RE = re.compile(
    r'^(?:(?:RE|FW|FWD|AW|WG|答复|转发)\s*:\s*)+\s*',
    re.IGNORECASE,
)


def normalize_subject(subject: str) -> str:
    """Strip all RE:/FW:/FWD: prefixes for thread matching.

    "RE: RE: FW: ARCO - Forward Works" → "ARCO - Forward Works"
    """
    if not subject:
        return ""
    return _SUBJECT_PREFIX_CHAIN_RE.sub("", subject).strip()


def has_re_prefix(subject: str) -> bool:
    """Check if the subject line indicates this is a reply."""
    if not subject:
        return False
    return bool(_SUBJECT_PREFIX_RE.match(subject.strip()))


# ---------------------------------------------------------------------------
# Datetime parsing
# ---------------------------------------------------------------------------

def parse_email_datetime(raw: str) -> str | None:
    """Parse an Outlook email timestamp into ISO 8601 format.

    Handles formats like:
      - "2026-03-31 14:30:00+08:00"
      - "2026-03-31T14:30:00"
      - "2026-03-31 14:30:00"
      - "Mon, 31 Mar 2026 14:30:00 +0800"

    Returns ISO string or None if unparseable.
    """
    if not raw:
        return None

    raw = str(raw).strip()

    # Already ISO 8601
    if re.match(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}', raw):
        return raw.replace(" ", "T")

    # RFC 2822: "Mon, 31 Mar 2026 14:30:00 +0800"
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(raw).isoformat()
    except (ValueError, TypeError):
        pass

    # Simple "YYYY-MM-DD HH:MM:SS"
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").isoformat()
    except ValueError:
        pass

    return None


def compute_cutoff(months_back: int = 9) -> str:
    """Compute an ISO datetime string for `months_back` months ago from now.

    Returns:
        ISO 8601 string like "2025-09-13T00:00:00"
    """
    from datetime import timedelta
    # Approximate: subtract months_back * 30 days
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=months_back * 30)
    return cutoff.strftime("%Y-%m-%dT00:00:00")


# ---------------------------------------------------------------------------
# Email body extraction from PST thread files
# ---------------------------------------------------------------------------

# Pattern to detect thread boundaries in Outlook PST exports
_THREAD_BOUNDARY_RE = re.compile(
    r'<div[^>]*style[^>]*border-top[^>]*>.*?</div>',
    re.DOTALL | re.IGNORECASE,
)

# Pattern for forwarding headers: "From: ... Sent: ... To: ... Subject: ..."
_FWD_HEADER_RE = re.compile(
    r'<b>From:</b>\s*(.*?)<br>.*?<b>Sent:</b>\s*(.*?)<br>.*?'
    r'<b>To:</b>\s*(.*?)<br>.*?(?:<b>Cc:</b>\s*(.*?)<br>.*?)?'
    r'<b>Subject:</b>\s*(.*?)<br>',
    re.DOTALL | re.IGNORECASE,
)


def split_thread_messages(html_content: str) -> list[dict]:
    """Split an Outlook PST thread export into individual messages.

    Each .txt file typically contains a full conversation chain with the
    newest message at the top.  Messages are separated by Outlook-generated
    divider blocks containing "From:/Sent:/To:/Subject:" headers.

    Args:
        html_content: Raw MSO HTML from a PST export .txt file.

    Returns:
        List of dicts, each with: from_name, from_email, sent_time, to_list,
        cc_list, subject, body_html, body_plain.
    """
    if not html_content:
        return []

    # Strategy: find all the "From:/Sent:/To:/Subject:" header blocks
    # and split on them
    messages = []
    body_lower = html_content.lower()

    # Find positions of all forward/reply header markers
    # These typically appear as <b>From:</b> patterns
    header_positions = []
    for m in re.finditer(r'<b>\s*From\s*:\s*</b>', html_content, re.IGNORECASE):
        header_positions.append(m.start())

    if not header_positions:
        # Single message — the whole file is one email
        messages.append(_extract_single_message(html_content, is_first=True))
        return messages

    # First message: everything before the first "From:" header
    if header_positions[0] > 0:
        first_chunk = html_content[:header_positions[0]]
        msg = _extract_single_message(first_chunk, is_first=True)
        if msg:
            messages.append(msg)

    # Subsequent messages: between consecutive "From:" headers
    for i, pos in enumerate(header_positions):
        end_pos = header_positions[i + 1] if i + 1 < len(header_positions) else len(html_content)
        chunk = html_content[pos:end_pos]
        msg = _extract_single_message(chunk, is_first=False)
        if msg:
            messages.append(msg)

    return messages


def _extract_single_message(html_chunk: str, is_first: bool) -> dict | None:
    """Extract a single email message from an HTML chunk.

    Parses the MSO HTML to find From:/Sent:/To:/Cc:/Subject: headers
    and the message body.
    """
    if not html_chunk or len(html_chunk.strip()) < 50:
        return None

    # Try to extract structured headers
    from_name = ""
    from_email = ""
    sent_time = ""
    to_list = []
    cc_list = []
    subject = ""

    # Extract From:
    from_m = re.search(
        r'<b>\s*From\s*:\s*</b>\s*(.*?)(?:<br|</p|</div)',
        html_chunk, re.DOTALL | re.IGNORECASE,
    )
    if from_m:
        from_raw = strip_html_to_text(from_m.group(1)).strip()
        from_name, from_email = parse_email_address(from_raw)

    # Extract Sent:
    sent_m = re.search(
        r'<b>\s*Sent\s*:\s*</b>\s*(.*?)(?:<br|</p|</div)',
        html_chunk, re.DOTALL | re.IGNORECASE,
    )
    if sent_m:
        sent_time = strip_html_to_text(sent_m.group(1)).strip()

    # Extract To:
    to_m = re.search(
        r'<b>\s*To\s*:\s*</b>\s*(.*?)(?:<br|</p|</div)',
        html_chunk, re.DOTALL | re.IGNORECASE,
    )
    if to_m:
        to_raw = strip_html_to_text(to_m.group(1)).strip()
        to_list = parse_address_list(to_raw)

    # Extract Cc:
    cc_m = re.search(
        r'<b>\s*Cc\s*:\s*</b>\s*(.*?)(?:<br|</p|</div)',
        html_chunk, re.DOTALL | re.IGNORECASE,
    )
    if cc_m:
        cc_raw = strip_html_to_text(cc_m.group(1)).strip()
        cc_list = parse_address_list(cc_raw)

    # Extract Subject:
    subj_m = re.search(
        r'<b>\s*Subject\s*:\s*</b>\s*(.*?)(?:<br|</p|</div)',
        html_chunk, re.DOTALL | re.IGNORECASE,
    )
    if subj_m:
        subject = strip_html_to_text(subj_m.group(1)).strip()

    # Get body: everything after the header block
    body_html = html_chunk
    body_plain = strip_html_to_text(html_chunk)

    return {
        "from_name": from_name,
        "from_email": from_email,
        "sent_time": sent_time,
        "to_list": to_list,
        "cc_list": cc_list,
        "subject": subject,
        "body_html": body_html,
        "body_plain": body_plain,
    }
