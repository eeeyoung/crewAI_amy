import subprocess
import platform
import os
from crewai.tools import BaseTool
from pydantic import Field


def fetch_inbox_emails(count=100, max_body=4000, unread_only=False,
                      exclude_entry_ids: set = None,
                      received_after: str = None, received_before: str = None,
                      ascending: bool = False,
                      focused_only: bool = False):
    """Fetch emails from Outlook Inbox.

    Args:
        count: Max emails to return.
        max_body: Max body length in chars.
        unread_only: If True, only fetch unread emails.
        exclude_entry_ids: Set of EntryIDs to skip.
        received_after: ISO datetime string — only emails received AFTER this.
        received_before: ISO datetime string — only emails received BEFORE this.
        ascending: If True, sort oldest-first (for fetch_earlier).
        focused_only: If True, only fetch Focused inbox emails (skip Other tab).
                      Default False = fetch ALL inbox emails.
    """
    if exclude_entry_ids is None:
        exclude_entry_ids = set()
    if platform.system() == "Windows":
        return _fetch_inbox_emails_windows(count, max_body, unread_only, exclude_entry_ids,
                                           received_after, received_before, ascending,
                                           focused_only)
    elif platform.system() == "Darwin":
        return _fetch_inbox_emails_macos(count, max_body, unread_only, exclude_entry_ids,
                                         received_after, received_before, ascending)
    else:
        raise RuntimeError(f"This function is not supported on OS: {platform.system()}")

def _fetch_inbox_emails_windows(count=10, max_body=4000, unread_only=False,
                                exclude_entry_ids: set = None,
                                received_after: str = None, received_before: str = None,
                                ascending: bool = False,
                                focused_only: bool = False):
    """Fetch emails from Outlook Inbox.

    IMPORTANT: Callers must call ``pythoncom.CoInitialize()`` before calling
    this function (all service methods in mail_service.py and
    habit_learner_service.py already do).  This function does NOT manage
    COM apartments — it assumes the caller owns that lifecycle.
    """
    import win32com.client

    if exclude_entry_ids is None:
        exclude_entry_ids = set()

    outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
    inbox = outlook.GetDefaultFolder(6)  # 6 = olFolderInbox
    messages = inbox.Items

    # ── Build Outlook-native DASL Restrict filter ──────────────────
    # Push filtering into Outlook's SQL engine so we never materialize
    # COM objects for emails outside the target range.  This is the #1
    # fix for RPC channel exhaustion ("用完了所有的共享数据资源").
    restrict_parts = []
    if unread_only:
        restrict_parts.append("[UnRead] = True")
    if received_after:
        # DASL datetime format: yyyy-mm-ddThh:mm:ss (no timezone)
        restrict_parts.append(
            '"urn:schemas:httpmail:datereceived" >= \''
            + received_after[:19].replace(' ', 'T') + '\''
        )
    if received_before:
        restrict_parts.append(
            '"urn:schemas:httpmail:datereceived" <= \''
            + received_before[:19].replace(' ', 'T') + '\''
        )

    # ── Safety net: if NO filter is provided, add a default 30-day
    # window to prevent iterating the entire mailbox (10k+ items).
    # Callers that need all-time access must pass explicit date range.
    if not restrict_parts and not unread_only:
        from datetime import datetime, timedelta
        default_after = (datetime.utcnow() - timedelta(days=30)).strftime(
            '%Y-%m-%dT00:00:00'
        )
        restrict_parts.append(
            '"urn:schemas:httpmail:datereceived" >= \'' + default_after + '\''
        )

    # ── Apply Restrict ──────────────────────────────────────────────
    # DASL date filtering is the #1 fix for RPC exhaustion, but it can
    # silently return zero on non-Exchange mailboxes (PST, IMAP) or with
    # locale-mismatched date formats.  If it throws, we fall back to
    # iteration with a hard scan cap.  We DON'T try to detect silent
    # failures (Items.Count is itself expensive and unreliable on
    # restricted collections) — the 2000 cap + Python-side date check
    # is the safety net.
    if restrict_parts:
        dasl_filter = "@SQL=(" + " AND ".join(restrict_parts) + ")"
        try:
            messages = messages.Restrict(dasl_filter)
        except Exception:
            # DASL may fail on non-Exchange mailboxes; fall back to
            # simple unread-only restriction if applicable.  Date
            # filtering will happen Python-side with the 2000 cap.
            if unread_only:
                messages = messages.Restrict("[UnRead] = True")

    messages.Sort("[ReceivedTime]", not ascending)

    # Python-side date parsing for boundary precision around the DASL filter
    from datetime import datetime
    def _parse_dt(ts: str):
        if not ts:
            return None
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        return dt.replace(tzinfo=None)
    after_dt = _parse_dt(received_after)
    before_dt = _parse_dt(received_before)

    emails = []
    fetched = 0
    scanned = 0
    _MAX_SCAN = 2000  # hard safety cap — never iterate more than this

    for message in messages:
        scanned += 1
        if fetched >= count:
            break
        if scanned > _MAX_SCAN:
            break  # safety valve — don't burn RPC channels forever

        try:
            if message.Class != 43:  # 43 = olMail
                continue

            entry_id = getattr(message, "EntryID", "")
            if entry_id and entry_id in exclude_entry_ids:
                continue

            # Focused Inbox filter (must be Python-side — no DASL property)
            if focused_only:
                try:
                    is_focused = message.PropertyAccessor.GetProperty(
                        "http://schemas.microsoft.com/mapi/proptag/0x10820003"
                    )
                    if is_focused == 0:
                        continue
                except Exception:
                    pass

            # Python-side date boundary check (belt + suspenders around DASL)
            if after_dt or before_dt:
                try:
                    msg_time = message.ReceivedTime
                    msg_dt = datetime(
                        msg_time.year, msg_time.month, msg_time.day,
                        msg_time.hour, msg_time.minute, msg_time.second
                    )
                    if after_dt and msg_dt < after_dt:
                        continue
                    if before_dt and msg_dt > before_dt:
                        continue
                except Exception:
                    pass

            sender_name = getattr(message, "SenderName", "Unknown")
            sender_email = getattr(message, "SenderEmailAddress", "Unknown")
            if sender_email and sender_email.upper().startswith("/O="):
                try:
                    sender_email = message.Sender.PropertyAccessor.GetProperty(
                        "http://schemas.microsoft.com/mapi/proptag/0x39FE001E"
                    )
                except Exception:
                    try:
                        exch_user = message.Sender.GetExchangeUser()
                        if exch_user:
                            sender_email = exch_user.PrimarySmtpAddress
                    except Exception:
                        pass

            # Resolve CC addresses
            cc_str = ""
            try:
                cc_list = []
                for rec in message.Recipients:
                    if rec.Type == 2:  # olCC
                        rec_email = rec.Address
                        if rec_email and rec_email.upper().startswith("/O="):
                            try:
                                rec_email = rec.PropertyAccessor.GetProperty(
                                    "http://schemas.microsoft.com/mapi/proptag/0x39FE001E"
                                )
                            except Exception:
                                try:
                                    exch_user = rec.AddressEntry.GetExchangeUser()
                                    if exch_user:
                                        rec_email = exch_user.PrimarySmtpAddress
                                except Exception:
                                    pass
                        cc_list.append(f"{rec.Name} <{rec_email}>")
                cc_str = "; ".join(cc_list)
            except Exception:
                cc_str = getattr(message, "CC", "")

            conversation_id = ""
            try:
                conversation_id = str(getattr(message, "ConversationID", "") or "")
            except Exception:
                pass

            emails.append({
                "entry_id": entry_id,
                "subject": getattr(message, "Subject", "No Subject"),
                "sender": f"{sender_name} <{sender_email}>",
                "cc": cc_str,
                "received_time": str(getattr(message, "ReceivedTime", "Unknown Date")),
                "body": getattr(message, "Body", "")[:max_body],
                "conversation_id": conversation_id,
            })
            fetched += 1
        except Exception:
            continue
        finally:
            # Release COM reference each iteration so RPC channels
            # don't accumulate across the loop
            message = None

    # Explicitly release Outlook objects
    try:
        del messages
    except Exception:
        pass
    try:
        del inbox
    except Exception:
        pass
    try:
        del outlook
    except Exception:
        pass

    return emails

def _fetch_inbox_emails_macos(count=10, max_body=4000, unread_only=False,
                              exclude_entry_ids: set = None,
                              received_after: str = None, received_before: str = None,
                              ascending: bool = False):
    import json

    jxa_script = """
    function run(argv) {
        var count = parseInt(argv[0]) || 10;
        var maxBody = parseInt(argv[1]) || 4000;
        var unreadOnly = argv[2] === "true";

        var Outlook = Application("Microsoft Outlook");
        var msgs = [];
        try {
            var inboxes = Outlook.mailFolders.whose({name: "Inbox"})();
            for (var i = 0; i < inboxes.length; i++) {
                var inbox = inboxes[i];
                var messages = inbox.messages();
                for (var j = 0; j < messages.length; j++) {
                    if (msgs.length >= count) break;
                    var msg = messages[j];

                    if (unreadOnly && msg.isRead && msg.isRead()) {
                        continue;
                    }

                    var sender = msg.sender();
                    var senderName = sender ? (sender.name || "Unknown") : "Unknown";
                    var senderEmail = sender ? (sender.address || "Unknown") : "Unknown";

                    var ccStr = "";
                    try {
                        var ccList = msg.ccRecipients();
                        if (ccList) {
                            var ccNames = [];
                            for (var k = 0; k < ccList.length; k++) {
                                var rec = ccList[k];
                                if (rec && rec.emailAddress) {
                                    var addr = rec.emailAddress();
                                    ccNames.push((addr.name || "") + " <" + (addr.address || "") + ">");
                                }
                            }
                            ccStr = ccNames.join("; ");
                        }
                    } catch (e) {}

                    var body = msg.plainTextContent ? msg.plainTextContent() : "";
                    if (body && body.length > maxBody) {
                        body = body.substring(0, maxBody);
                    }

                    msgs.push({
                        subject: msg.subject ? msg.subject() : "No Subject",
                        sender: senderName + " <" + senderEmail + ">",
                        cc: ccStr,
                        received_time: msg.timeReceived ? msg.timeReceived().toString() : "Unknown Date",
                        body: body
                    });
                }
                if (msgs.length >= count) break;
            }
        } catch (e) {
            msgs.push({error: e.toString()});
        }
        return JSON.stringify(msgs);
    }
    """

    try:
        result = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", jxa_script, str(count), str(max_body), str(unread_only).lower()],
            capture_output=True,
            text=True,
            check=True
        )
        emails = json.loads(result.stdout.strip())

        if emails and "error" in emails[0]:
            print(f"JXA script error: {emails[0]['error']}")
            return []

        return emails
    except Exception as e:
        print(f"Error fetching emails on macOS: {e}")
        return []

def mark_email_as_read(entry_id: str) -> bool:
    """Mark an Outlook email as read by its EntryID. Returns True on success."""
    if platform.system() != "Windows":
        return False
    try:
        import pythoncom
        pythoncom.CoInitialize()
        import win32com.client
        outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        msg = outlook.GetItemFromID(entry_id)
        msg.UnRead = False
        msg.Save()
        return True
    except Exception as e:
        print(f"Error marking email as read: {e}")
        return False


def mark_email_as_unread(entry_id: str) -> bool:
    """Mark an Outlook email as unread by its EntryID. Returns True on success."""
    if platform.system() != "Windows":
        return False
    try:
        import pythoncom
        pythoncom.CoInitialize()
        import win32com.client
        outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        msg = outlook.GetItemFromID(entry_id)
        msg.UnRead = True
        msg.Save()
        return True
    except Exception as e:
        print(f"Error marking email as unread: {e}")
        return False


def mark_email_as_flagged(entry_id: str) -> bool:
    """Flag an Outlook email (set follow-up flag). Returns True on success."""
    if platform.system() != "Windows":
        return False
    try:
        import pythoncom
        pythoncom.CoInitialize()
        import win32com.client
        outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        msg = outlook.GetItemFromID(entry_id)
        msg.FlagStatus = 2  # olFlagMarked
        msg.Save()
        return True
    except Exception as e:
        print(f"Error flagging email: {e}")
        return False


def fetch_outlook_contacts() -> list[dict]:
    """Fetch contacts from the Global Address List and Contacts folder.

    Returns a list of dicts with 'name' and 'email' keys.
    Deduplicated by email (case-insensitive).
    Returns an empty list on failure or non-Windows platforms.
    """
    if platform.system() != "Windows":
        return []

    import win32com.client
    outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
    seen: dict[str, dict] = {}

    # Source 1 — Global Address List
    try:
        gal = outlook.GetGlobalAddressList()
        if gal and gal.AddressEntries:
            count = min(gal.AddressEntries.Count, 2000)
            for i in range(1, count + 1):
                try:
                    entry = gal.AddressEntries.Item(i)
                    name = getattr(entry, "Name", "") or ""
                    email = ""
                    try:
                        exch = entry.GetExchangeUser()
                        if exch:
                            email = exch.PrimarySmtpAddress or ""
                    except Exception:
                        pass
                    if not email:
                        addr = getattr(entry, "Address", "") or ""
                        if addr and not addr.upper().startswith("/O="):
                            email = addr
                    if name and email and email.lower() not in seen:
                        seen[email.lower()] = {"name": name, "email": email}
                except Exception:
                    continue
    except Exception:
        pass

    # Source 2 — Contacts folder (olFolderContacts = 10)
    try:
        contacts_folder = outlook.GetDefaultFolder(10)
        for item in contacts_folder.Items:
            try:
                full_name = getattr(item, "FullName", "") or ""
                for attr in ("Email1Address", "Email2Address", "Email3Address"):
                    email = getattr(item, attr, "") or ""
                    if email and email.lower() not in seen:
                        seen[email.lower()] = {"name": full_name, "email": email}
            except Exception:
                continue
    except Exception:
        pass

    results = sorted(seen.values(), key=lambda c: c["name"].lower())
    return results


def fetch_attachments_for_email(entry_id: str) -> list:
    """Return a list of attachment metadata dicts for the email identified by entry_id.
    Each dict has keys: index (1-based), filename, size (bytes).
    Inline/embedded images (those referenced via cid: in the HTML body) are excluded.
    Returns an empty list on failure or unsupported OS.
    """
    if platform.system() != "Windows":
        return []
    try:
        import win32com.client
        outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        msg = outlook.GetItemFromID(entry_id)

        html_body = getattr(msg, "HTMLBody", "") or ""
        html_body_lower = html_body.lower()

        PR_ATTACH_CONTENT_ID = "http://schemas.microsoft.com/mapi/proptag/0x3712001F"

        attachments = []
        for i in range(1, msg.Attachments.Count + 1):
            att = msg.Attachments.Item(i)

            try:
                content_id = att.PropertyAccessor.GetProperty(PR_ATTACH_CONTENT_ID)
                if content_id and str(content_id).strip():
                    cid = str(content_id).strip()
                    if f"cid:{cid}".lower() in html_body_lower:
                        continue
            except Exception:
                pass

            attachments.append({
                "index": i,
                "filename": att.FileName,
                "size": att.Size,
            })
        return attachments
    except Exception as e:
        print(f"Error fetching attachments: {e}")
        return []


def save_attachment(entry_id: str, attachment_index: int, save_dir: str) -> str:
    """Save a single attachment to save_dir. Returns the full saved path on success,
    or an error string starting with 'Error:' on failure.
    attachment_index is 1-based (matching Outlook COM).
    """
    if platform.system() != "Windows":
        return "Error: Attachment download is only supported on Windows."
    try:
        import win32com.client
        if not os.path.isdir(save_dir):
            os.makedirs(save_dir, exist_ok=True)
        outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        msg = outlook.GetItemFromID(entry_id)
        att = msg.Attachments.Item(attachment_index)
        save_path = os.path.join(save_dir, att.FileName)
        att.SaveAsFile(save_path)
        return save_path
    except Exception as e:
        return f"Error: {e}"


class OutlookReadTool(BaseTool):
    name: str = "outlook_read_tool"
    description: str = "Reads the first email from the default Microsoft Outlook account."

    def _run(self) -> str:
        current_os = platform.system()

        if current_os == "Windows":
            return self._run_windows()
        elif current_os == "Darwin":
            return self._run_macos()
        else:
            return f"Unsupported operating system: {current_os}"

    def _run_windows(self) -> str:
        try:
            import win32com.client
            outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
            inbox = outlook.GetDefaultFolder(6)
            messages = inbox.Items
            messages.Sort("[ReceivedTime]", True)

            if messages.Count > 0:
                message = messages.GetFirst()
                return f"ID: {message.EntryID}\nSender: {message.SenderEmailAddress}\nSubject: {message.Subject}\n\nContent: {message.Body}"
            else:
                return "No messages found in Inbox."
        except ImportError:
            return "Error: pywin32 not installed. Please install it to use this tool on Windows."
        except Exception as e:
            return f"Error accessing Outlook on Windows: {str(e)}"

    def _run_macos(self) -> str:
        applescript = """
        tell application "Microsoft Outlook"
            try
                set theInboxes to every mail folder whose name is "Inbox"
                set foundMessage to false
                repeat with theInbox in theInboxes
                    if (count messages of theInbox) > 0 then
                        set theMessage to first message of theInbox
                        set msgId to id of theMessage
                        set theSubject to subject of theMessage
                        set senderRecord to sender of theMessage
                        set theSender to address of senderRecord
                        set theContent to plain text content of theMessage
                        set foundMessage to true
                        return "ID: " & msgId & "\\nSender: " & theSender & "\\nSubject: " & theSubject & "\\n\\nContent: " & theContent
                    end if
                end repeat

                if not foundMessage then
                    return "No messages found in any Inbox."
                end if
            on error errMsg
                return "Error accessing Outlook: " & errMsg
            end try
        end tell
        """
        try:
            result = subprocess.run(
                ["osascript", "-e", applescript],
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            return f"Failed to execute AppleScript: {e.stderr}"
        except Exception as e:
            return f"An unexpected error occurred: {str(e)}"

class OutlookInboxBatchTool(BaseTool):
    name: str = "outlook_inbox_batch_tool"
    description: str = "Reads the latest 10 emails from the Microsoft Outlook Inbox folder."

    def _run(self) -> str:
        import json
        try:
            emails = fetch_inbox_emails(count=10, max_body=500, unread_only=False)

            if not emails:
                return json.dumps({"error": "No messages found in Inbox."})

            extracted_emails = []
            for email in emails:
                extracted_emails.append({
                    "Subject": email.get("subject", "No Subject"),
                    "Sender": email.get("sender", "Unknown"),
                    "CC": email.get("cc", ""),
                    "ReceivedTime": email.get("received_time", "Unknown Date"),
                    "BodySnippet": email.get("body", "")
                })

            return json.dumps(extracted_emails)
        except Exception as e:
            return json.dumps({"error": f"Error accessing Outlook Inbox: {str(e)}"})

class OutlookSendTool(BaseTool):
    name: str = "outlook_send_tool"
    description: str = "Sends an email using the Microsoft Outlook application."
    # Configurable signature — set these when instantiating the tool
    signature_html_path: str = ""
    signature_image_specs: list = Field(default_factory=list)  # [(relative_path, content_id), ...]

    def _run(self, recipient: str, subject: str, body: str, cc: str = "", is_html: bool = False) -> str:
        current_os = platform.system()

        if current_os == "Windows":
            try:
                import win32com.client
                outlook = win32com.client.Dispatch("Outlook.Application")
                mail = outlook.CreateItem(0)  # 0 = olMailItem

                mail.To = recipient
                if cc:
                    mail.CC = cc
                mail.Subject = subject

                if is_html:
                    for img_path, cid in self.signature_image_specs:
                        if os.path.exists(img_path):
                            abs_img_path = os.path.abspath(img_path)
                            attachment = mail.Attachments.Add(abs_img_path)
                            attachment.PropertyAccessor.SetProperty("http://schemas.microsoft.com/mapi/proptag/0x3712001F", cid)

                            file_uri = f"file:///{abs_img_path.replace(chr(92), '/')}"
                            body = body.replace(file_uri, f"cid:{cid}")
                            body = body.replace(abs_img_path, f"cid:{cid}")
                            body = body.replace(img_path, f"cid:{cid}")
                            body = body.replace(os.path.basename(img_path), f"cid:{cid}")

                    mail.HTMLBody = body
                else:
                    body_html = "".join(f"<p>{line}</p>" if line.strip() else "<br>" for line in body.split("\n"))

                    sig_path = self.signature_html_path
                    if sig_path and os.path.exists(sig_path):
                        with open(sig_path, "r", encoding="utf-8") as f:
                            signature_html = f.read()

                        combined_html = f'<div style="font-family: Calibri, sans-serif; font-size: 11pt;">{body_html}</div><br><br>{signature_html}'

                        for img_path, cid in self.signature_image_specs:
                            if os.path.exists(img_path):
                                abs_img_path = os.path.abspath(img_path)
                                attachment = mail.Attachments.Add(abs_img_path)
                                attachment.PropertyAccessor.SetProperty("http://schemas.microsoft.com/mapi/proptag/0x3712001F", cid)
                                combined_html = combined_html.replace(abs_img_path, f"cid:{cid}")
                                combined_html = combined_html.replace(img_path, f"cid:{cid}")
                                combined_html = combined_html.replace(os.path.basename(img_path), f"cid:{cid}")

                        mail.HTMLBody = combined_html
                    else:
                        _ = mail.GetInspector
                        mail.Body = body + "\n\n" + mail.Body

                mail.Send()
                return "Email successfully sent."
            except Exception as e:
                return f"Error sending email: {str(e)}"
        else:
            return f"This tool is currently only supported on Windows. Current OS: {current_os}"


# =============================================================================
# Outlook Calendar Functions
# =============================================================================

def create_calendar_event(
    subject: str,
    start_date: str,
    end_date: str,
    body: str = "",
    location: str = "",
    reminder_minutes: int = 15,
    categories: list[str] | None = None,
) -> str:
    """Create an Outlook calendar appointment.
    Returns the EntryID string of the created event, or an error string starting with 'Error:'.
    start_date and end_date should be ISO format strings, e.g. "2026-06-15T14:00:00".
    """
    if platform.system() != "Windows":
        return "Error: Calendar operations are only supported on Windows."

    try:
        import pythoncom
        pythoncom.CoInitialize()
        import win32com.client
        from datetime import datetime

        outlook = win32com.client.Dispatch("Outlook.Application")
        appointment = outlook.CreateItem(1)  # 1 = olAppointmentItem

        appointment.Subject = subject
        appointment.Start = datetime.fromisoformat(start_date)
        appointment.End = datetime.fromisoformat(end_date)
        appointment.Location = location
        appointment.Body = body
        appointment.ReminderMinutesBeforeStart = reminder_minutes

        if categories:
            appointment.Categories = ", ".join(categories)

        appointment.Save()
        return str(appointment.EntryID)

    except ImportError:
        return "Error: pywin32 not installed. Please install it to use this tool on Windows."
    except Exception as e:
        print(f"Error creating calendar event: {e}")
        return f"Error: {e}"


def get_calendar_events(start_date: str, end_date: str) -> list[dict]:
    """Fetch Outlook calendar events in a date range.
    start_date and end_date are ISO format strings, e.g. "2026-06-01T00:00:00".
    Returns a list of event dicts with keys: entry_id, subject, start, end,
    location, body, categories, duration_minutes, all_day_event, busy_status.
    """
    if platform.system() != "Windows":
        return []

    try:
        import win32com.client
        from datetime import datetime

        dt_start = datetime.fromisoformat(start_date)
        dt_end = datetime.fromisoformat(end_date)

        outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        calendar = outlook.GetDefaultFolder(9)  # 9 = olFolderCalendar
        items = calendar.Items
        items.Sort("[Start]")
        items.IncludeRecurrences = True

        events = []
        for item in items:
            try:
                if item.Class != 44:  # 44 = olAppointment
                    continue

                item_start = getattr(item, "Start", None)
                item_end = getattr(item, "End", None)

                if item_start is None:
                    continue

                # Filter by date range
                if item_end and item_end < dt_start:
                    continue
                if item_start > dt_end:
                    break  # Sorted by Start, so no more relevant items

                duration = int((item_end - item_start).total_seconds() / 60) if item_end and item_start else 0

                events.append({
                    "entry_id": getattr(item, "EntryID", ""),
                    "subject": getattr(item, "Subject", ""),
                    "start": item_start.isoformat() if item_start else "",
                    "end": item_end.isoformat() if item_end else "",
                    "location": getattr(item, "Location", ""),
                    "body": getattr(item, "Body", "")[:1000],
                    "categories": getattr(item, "Categories", ""),
                    "duration_minutes": duration,
                    "all_day_event": bool(getattr(item, "AllDayEvent", False)),
                    "busy_status": getattr(item, "BusyStatus", 0),
                })
            except Exception:
                continue

        return events

    except ImportError:
        return []
    except Exception as e:
        print(f"Error fetching calendar events: {e}")
        return []


def delete_calendar_event(event_entry_id: str) -> bool:
    """Delete an Outlook calendar event by EntryID. Returns True on success."""
    if platform.system() != "Windows":
        return False

    try:
        import win32com.client
        outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        appointment = outlook.GetItemFromID(event_entry_id)
        appointment.Delete()
        return True
    except Exception as e:
        print(f"Error deleting calendar event: {e}")
        return False


def update_calendar_event(event_entry_id: str, **kwargs) -> bool:
    """Update fields of an existing Outlook calendar event by EntryID.
    Allowed kwargs: Subject, Start, End, Body, Location,
    ReminderMinutesBeforeStart, Categories, AllDayEvent, BusyStatus.
    Start and End should be ISO format strings.
    Returns True on success.
    """
    if platform.system() != "Windows":
        return False

    try:
        import win32com.client
        from datetime import datetime

        outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        appointment = outlook.GetItemFromID(event_entry_id)

        for key, value in kwargs.items():
            if not hasattr(appointment, key):
                print(f"Warning: AppointmentItem has no attribute '{key}'")
                continue
            if key in ("Start", "End") and isinstance(value, str):
                value = datetime.fromisoformat(value)
            setattr(appointment, key, value)

        appointment.Save()
        return True
    except Exception as e:
        print(f"Error updating calendar event: {e}")
        return False
