"""Quick diagnostic to check what Outlook's Body property actually returns."""
import win32com.client

outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
inbox = outlook.GetDefaultFolder(6)
messages = inbox.Items
messages.Sort("[ReceivedTime]", True)

# Grab the first MailItem
for msg in messages:
    if msg.Class == 43:
        print(f"Subject: {msg.Subject}")
        print(f"Sender:  {msg.SenderEmailAddress}")
        print(f"Body length: {len(msg.Body)} characters")
        print("=" * 60)
        print("FULL BODY (first 3000 chars):")
        print("=" * 60)
        print(msg.Body[:3000])
        print("=" * 60)
        print(f"\n(Total body was {len(msg.Body)} chars, shown first 3000)")
        break
