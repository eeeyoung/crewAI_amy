import subprocess
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Type

class OutlookReplyToolInput(BaseModel):
    message_id: str = Field(..., description="The ID of the message to reply to.")
    reply_body: str = Field(..., description="The body of the reply message.")

class OutlookReplyTool(BaseTool):
    name: str = "outlook_reply_tool"
    description: str = "Replies to an email in Microsoft Outlook given its message ID and the reply content."
    args_schema: Type[BaseModel] = OutlookReplyToolInput

    def _run(self, message_id: str, reply_body: str) -> str:
        safe_reply_body = reply_body.replace('"', '\\\\"')
        applescript = f"""
        tell application "Microsoft Outlook"
            try
                -- Find the message by ID
                set targetMessage to missing value
                set theInboxes to every mail folder whose name is "Inbox"
                repeat with theInbox in theInboxes
                    try
                        set targetMessage to message id {message_id} of theInbox
                        exit repeat
                    end try
                end repeat
                
                if targetMessage is missing value then
                    return "Error: Could not find message with ID {message_id}"
                end if
                
                set theReply to reply to targetMessage
                set content of theReply to "{safe_reply_body}"
                send theReply
                return "Successfully replied to message ID {message_id}."
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
            return result.stdout.strip() or "Reply sent successfully."
        except subprocess.CalledProcessError as e:
            return f"Failed to execute AppleScript: {e.stderr}"
        except Exception as e:
            return f"An unexpected error occurred: {str(e)}"
