import subprocess
import platform
from crewai.tools import BaseTool
from pydantic import Field


def fetch_inbox_emails(count=10, max_body=4000, unread_only=False):
    """Fetch the latest emails from Outlook Inbox directly.
    Returns a list of dicts with subject, sender, cc, received_time, body.
    This is a plain Python function, NOT a CrewAI tool.
    """
    if platform.system() == "Windows":
        return _fetch_inbox_emails_windows(count, max_body, unread_only)
    elif platform.system() == "Darwin":
        return _fetch_inbox_emails_macos(count, max_body, unread_only)
    else:
        raise RuntimeError(f"This function is not supported on OS: {platform.system()}")

def _fetch_inbox_emails_windows(count=10, max_body=4000, unread_only=False):
    import win32com.client

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
            sender_name = getattr(message, "SenderName", "Unknown")
            sender_email = getattr(message, "SenderEmailAddress", "Unknown")
            if sender_email and sender_email.upper().startswith("/O="):
                try:
                    # Using PR_SMTP_ADDRESS property tag
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

def _fetch_inbox_emails_macos(count=10, max_body=4000, unread_only=False):
    import subprocess
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
            inbox = outlook.GetDefaultFolder(6)  # 6 = olFolderInbox
            messages = inbox.Items
            messages.Sort("[ReceivedTime]", True)  # Sort by newest first
            
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
                -- Find all folders named "Inbox" and look for the one with messages
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

    def _run(self, recipient: str, subject: str, body: str, cc: str = "", is_html: bool = False) -> str:
        current_os = platform.system()
        
        if current_os == "Windows":
            try:
                import win32com.client
                import os
                outlook = win32com.client.Dispatch("Outlook.Application")
                mail = outlook.CreateItem(0)  # 0 = olMailItem
                
                mail.To = recipient
                if cc:
                    mail.CC = cc
                mail.Subject = subject
                
                if is_html:
                    image_files = [
                        ("knowledge/logo_meritor_welink.png", "logo_meritor_welink.png"),
                        ("knowledge/logo_hia_awards.png", "logo_hia_awards.png"),
                        ("knowledge/icon_instagram.png", "icon_instagram.png"),
                        ("knowledge/icon_facebook.png", "icon_facebook.png")
                    ]
                    for img_path, cid in image_files:
                        if os.path.exists(img_path):
                            abs_img_path = os.path.abspath(img_path)
                            attachment = mail.Attachments.Add(abs_img_path)
                            # Set PR_ATTACH_CONTENT_ID
                            attachment.PropertyAccessor.SetProperty("http://schemas.microsoft.com/mapi/proptag/0x3712001F", cid)
                            
                            # Replace occurrences of absolute path, file URI, and relative path with CID
                            file_uri = f"file:///{abs_img_path.replace(chr(92), '/')}"
                            body = body.replace(file_uri, f"cid:{cid}")
                            body = body.replace(abs_img_path, f"cid:{cid}")
                            body = body.replace(img_path, f"cid:{cid}")
                    
                    mail.HTMLBody = body
                else:
                    # Convert the plain text draft into basic HTML paragraphs
                    body_html = "".join(f"<p>{line}</p>" if line.strip() else "<br>" for line in body.split("\n"))
                    
                    # Check for our custom HTML signature
                    sig_path = "knowledge/amy_signature.html"
                    if os.path.exists(sig_path):
                        with open(sig_path, "r", encoding="utf-8") as f:
                            signature_html = f.read()
                        
                        mail.HTMLBody = f'<div style="font-family: Calibri, sans-serif; font-size: 11pt;">{body_html}</div><br><br>{signature_html}'
                        
                        # Attach signature images and set their Content-ID
                        image_files = [
                            ("knowledge/logo_meritor_welink.png", "logo_meritor_welink.png"),
                            ("knowledge/logo_hia_awards.png", "logo_hia_awards.png"),
                            ("knowledge/icon_instagram.png", "icon_instagram.png"),
                            ("knowledge/icon_facebook.png", "icon_facebook.png")
                        ]
                        for img_path, cid in image_files:
                            if os.path.exists(img_path):
                                abs_img_path = os.path.abspath(img_path)
                                attachment = mail.Attachments.Add(abs_img_path)
                                # Set PR_ATTACH_CONTENT_ID
                                attachment.PropertyAccessor.SetProperty("http://schemas.microsoft.com/mapi/proptag/0x3712001F", cid)
                    else:
                        # Fallback to plain text with default outlook signature if html signature is missing
                        _ = mail.GetInspector
                        mail.Body = body + "\n\n" + mail.Body
                
                mail.Send()
                return "Email successfully sent."
            except Exception as e:
                return f"Error sending email: {str(e)}"
        else:
            return f"This tool is currently only supported on Windows. Current OS: {current_os}"
