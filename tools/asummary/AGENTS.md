# AGENTS.md — ASummary (DEPRECATED)

> ⚠️ **This tool is deprecated.** ASummary1 (`tools/asummary1/`) is its successor with identical `crew.py` and YAML configs PLUS a PyQt6 GUI, ReplyCrew, and Outlook direct-fetch capability. ASummary exists only for reference.

## Migration

| ASummary | ASummary1 |
|---|---|
| CLI only | GUI (default) + CLI (`--cli` flag) |
| Reads from AMail DB only | Reads from AMail DB OR fetches Outlook directly |
| No reply generation | ReplyCrew + refine dialog |
| Terminal output | Card-based GUI with keyboard shortcuts |

## If you need to modify the summarizer

Edit the files in `tools/asummary1/`, not here. The `crew.py`, `agents.yaml`, and `tasks.yaml` are byte-for-byte identical between the two — ASummary1 is the canonical copy.
