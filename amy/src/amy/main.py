#!/usr/bin/env python
import sys
import warnings
import time
import subprocess

from amy.crew import Amy

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

def get_latest_email_id():
    applescript = """
    tell application "Microsoft Outlook"
        try
            set theInboxes to every mail folder whose name is "Inbox"
            repeat with theInbox in theInboxes
                if (count messages of theInbox) > 0 then
                    set theMessage to first message of theInbox
                    return id of theMessage as string
                end if
            end repeat
            return ""
        on error
            return ""
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
    except Exception:
        return ""

def run():
    """
    Run the crew in a loop, checking every 5 seconds for new emails.
    """
    print("Starting Outlook email monitor (checking every 5 seconds)...")
    last_seen_id = get_latest_email_id()
    print(f"Initial latest email ID: {last_seen_id}")

    while True:
        try:
            current_id = get_latest_email_id()
            if current_id and current_id != last_seen_id:
                print(f"\\n[!] New email detected (ID: {current_id}). Starting Crew...")
                Amy().crew().kickoff(inputs={})
                last_seen_id = current_id
                print("\\nWaiting for new emails...")
            time.sleep(5)
        except KeyboardInterrupt:
            print("\\nStopping email monitor.")
            break
        except Exception as e:
            print(f"An error occurred: {e}")
            time.sleep(5)

if __name__ == "__main__":
    run()
