import subprocess
import platform
from crewai.tools import BaseTool
from pydantic import Field


def fetch_inbox_emails(count=10, max_body=4000):
    """Fetch the latest emails from Outlook Inbox directly via win32com.
    Returns a list of dicts with Subject, Sender, ReceivedTime, Body.
    This is a plain Python function, NOT a CrewAI tool.
    """
    if platform.system() != "Windows":
        raise RuntimeError("This function is only supported on Windows.")

    import win32com.client

    outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
    inbox = outlook.GetDefaultFolder(6)  # 6 = olFolderInbox
    messages = inbox.Items
    messages.Sort("[ReceivedTime]", True)

    emails = []
    fetched = 0

    for message in messages:
        if fetched >= count:
            break
        try:
            if message.Class != 43:  # 43 = olMail
                continue
            emails.append({
                "subject": getattr(message, "Subject", "No Subject"),
                "sender": getattr(message, "SenderEmailAddress", "Unknown"),
                "received_time": str(getattr(message, "ReceivedTime", "Unknown Date")),
                "body": getattr(message, "Body", "")[:max_body],
            })
            fetched += 1
        except Exception:
            continue

    return emails
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
        current_os = platform.system()
        
        if current_os == "Windows":
            return self._run_windows()
        else:
            return f"This tool is currently only supported on Windows. Current OS: {current_os}"

    def _run_windows(self) -> str:
        try:
            import win32com.client
            import json
            
            outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
            inbox_folder = outlook.GetDefaultFolder(6)  # 6 = olFolderInbox
            messages = inbox_folder.Items
            messages.Sort("[ReceivedTime]", True)  # Sort by newest first
            
            extracted_emails = []
            count = 0
            
            for message in messages:
                if count >= 10:
                    break
                
                try:
                    # Skip items that are not standard MailItems
                    if message.Class != 43:  # 43 = olMail
                        continue
                    
                    extracted_emails.append({
                        "Subject": getattr(message, "Subject", "No Subject"),
                        "Sender": getattr(message, "SenderEmailAddress", "Unknown"),
                        "ReceivedTime": str(getattr(message, "ReceivedTime", "Unknown Date")),
                        "BodySnippet": getattr(message, "Body", "")[:500]  # Limit body to save context window
                    })
                    count += 1
                except Exception as msg_e:
                    continue
                    
            if not extracted_emails:
                return json.dumps({"error": "No messages found in Inbox."})
                
            return json.dumps(extracted_emails)
            
        except ImportError:
            return json.dumps({"error": "pywin32 not installed."})
        except Exception as e:
            return json.dumps({"error": f"Error accessing Outlook Inbox: {str(e)}"})

class OutlookSendTool(BaseTool):
    name: str = "outlook_send_tool"
    description: str = "Sends an email using the Microsoft Outlook application."

    def _run(self, recipient: str, subject: str, body: str) -> str:
        current_os = platform.system()
        
        if current_os == "Windows":
            try:
                import win32com.client
                outlook = win32com.client.Dispatch("Outlook.Application")
                mail = outlook.CreateItem(0)  # 0 = olMailItem
                
                # Access GetInspector to generate the default signature in mail.Body
                _ = mail.GetInspector
                
                mail.To = recipient
                mail.Subject = subject
                
                # Prepend the generated body to the default signature
                mail.Body = body + "\n\n" + mail.Body
                
                mail.Send()
                return "Email successfully sent."
            except Exception as e:
                return f"Error sending email: {str(e)}"
        else:
            return f"This tool is currently only supported on Windows. Current OS: {current_os}"
