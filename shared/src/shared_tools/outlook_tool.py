import subprocess
import platform
import os
from crewai.tools import BaseTool
from pydantic import Field


def fetch_inbox_emails(count=10, max_body=4000, unread_only=False, exclude_entry_ids: set = None):
    """Fetch the latest emails from Outlook Inbox directly.
    Returns a list of dicts with subject, sender, cc, received_time, body.
    Skips emails whose EntryID is in exclude_entry_ids (session blocklist).
    Only returns emails from the "Focused" inbox (skips "Other" tab).
    This is a plain Python function, NOT a CrewAI tool.
    """
    if exclude_entry_ids is None:
        exclude_entry_ids = set()
    if platform.system() == "Windows":
        return _fetch_inbox_emails_windows(count, max_body, unread_only, exclude_entry_ids)
    elif platform.system() == "Darwin":
        return _fetch_inbox_emails_macos(count, max_body, unread_only, exclude_entry_ids)
    else:
        raise RuntimeError(f"This function is not supported on OS: {platform.system()}")

def _fetch_inbox_emails_windows(count=10, max_body=4000, unread_only=False, exclude_entry_ids: set = None):
    import win32com.client

    if exclude_entry_ids is None:
        exclude_entry_ids = set()

    outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
    inbox = outlook.GetDefaultFolder(6)  # 6 = olFolderInbox
    messages = inbox.Items
    if unread_only:
        messages = messages.Restrict("[UnRead] = True")
    messages.Sort("[ReceivedTime]", True)

    emails = []
    fetched = 0

    for message in messages:
        if fetched >= count:
            break
        try:
            if message.Class != 43:  # 43 = olMail
                continue

            # Skip if EntryID is in the session blocklist
            entry_id = getattr(message, "EntryID", "")
            if entry_id and entry_id in exclude_entry_ids:
                continue

            # Skip emails from the "Other" tab (Focused Inbox only)
            try:
                is_focused = message.PropertyAccessor.GetProperty(
                    "http://schemas.microsoft.com/mapi/proptag/0x10820003"
                )
                if is_focused == 0:  # 0 = Other, 1 = Focused
                    continue
            except Exception:
                pass  # Property not available; include the email

            sender_name = getattr(message, "SenderName", "Unknown")
            sender_email = getattr(message, "SenderEmailAddress", "Unknown")
            if sender_email and sender_email.upper().startswith("/O="):
                try:
                    sender_email = message.Sender.PropertyAccessor.GetProperty("http://schemas.microsoft.com/mapi/proptag/0x39FE001E")
                except Exception:
                    try:
                        exch_user = message.Sender.GetExchangeUser()
                        if exch_user: sender_email = exch_user.PrimarySmtpAddress
                    except Exception:
                        pass

            # Resolve CC addresses
            cc_list = []
            try:
                for rec in message.Recipients:
                    if rec.Type == 2:  # 2 = olCC
                        rec_email = rec.Address
                        if rec_email and rec_email.upper().startswith("/O="):
                            try:
                                rec_email = rec.PropertyAccessor.GetProperty("http://schemas.microsoft.com/mapi/proptag/0x39FE001E")
                            except Exception:
                                try:
                                    exch_user = rec.AddressEntry.GetExchangeUser()
                                    if exch_user: rec_email = exch_user.PrimarySmtpAddress
                                except Exception:
                                    pass
                        cc_list.append(f"{rec.Name} <{rec_email}>")
                cc_str = "; ".join(cc_list)
            except Exception:
                cc_str = getattr(message, "CC", "")

            emails.append({
                "entry_id": entry_id,
                "subject": getattr(message, "Subject", "No Subject"),
                "sender": f"{sender_name} <{sender_email}>",
                "cc": cc_str,
                "received_time": str(getattr(message, "ReceivedTime", "Unknown Date")),
                "body": getattr(message, "Body", "")[:max_body],
            })
            fetched += 1
        except Exception:
            continue

    return emails

def _fetch_inbox_emails_macos(count=10, max_body=4000, unread_only=False, exclude_entry_ids: set = None):
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
        import win32com.client
        outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        msg = outlook.GetItemFromID(entry_id)
        msg.UnRead = True
        msg.Save()
        return True
    except Exception as e:
        print(f"Error marking email as unread: {e}")
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

                    mail.HTMLBody = body
                else:
                    body_html = "".join(f"<p>{line}</p>" if line.strip() else "<br>" for line in body.split("\n"))

                    sig_path = self.signature_html_path
                    if sig_path and os.path.exists(sig_path):
                        with open(sig_path, "r", encoding="utf-8") as f:
                            signature_html = f.read()

                        mail.HTMLBody = f'<div style="font-family: Calibri, sans-serif; font-size: 11pt;">{body_html}</div><br><br>{signature_html}'

                        for img_path, cid in self.signature_image_specs:
                            if os.path.exists(img_path):
                                abs_img_path = os.path.abspath(img_path)
                                attachment = mail.Attachments.Add(abs_img_path)
                                attachment.PropertyAccessor.SetProperty("http://schemas.microsoft.com/mapi/proptag/0x3712001F", cid)
                    else:
                        _ = mail.GetInspector
                        mail.Body = body + "\n\n" + mail.Body

                mail.Send()
                return "Email successfully sent."
            except Exception as e:
                return f"Error sending email: {str(e)}"
        else:
            return f"This tool is currently only supported on Windows. Current OS: {current_os}"
