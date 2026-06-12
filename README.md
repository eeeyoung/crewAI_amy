# lilAmy — Multi-Agent Desktop Workspace

A CrewAI-powered desktop platform with multiple AI agents for construction project management.

## Agents

| Agent | Command | Description |
|-------|---------|-------------|
| **AMail** | `uv run amail` | Email triage, categorization, and auto-reply |
| **ACalendar** | `uv run acalendar` | Date extraction from emails and calendar scheduling |

## Development

```bash
uv sync          # Install all dependencies
uv run amail     # Launch email assistant
uv run acalendar # Launch calendar assistant
```

## Build (.exe)

```bash
# AMail
uv run pyinstaller --onefile --windowed \
    --name "AMail" \
    --add-data "tools/amail/knowledge;knowledge" \
    --add-data "tools/amail/src/amail/config;config" \
    --hidden-import=amail \
    tools/amail/src/amail/main.py

# ACalendar
uv run pyinstaller --onefile --windowed \
    --name "ACalendar" \
    --add-data "tools/acalendar/src/acalendar/config;config" \
    --hidden-import=acalendar \
    tools/acalendar/src/acalendar/main.py
```

Output executables appear in `dist/`.