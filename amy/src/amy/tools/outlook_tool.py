import subprocess
import platform
from crewai.tools import BaseTool
from pydantic import Field

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
