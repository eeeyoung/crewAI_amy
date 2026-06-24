"""Module registry for the lilAmy platform.

Each module provides:
  - id: unique string key
  - name: display name
  - icon: emoji icon for sidebar
  - description: one-line description
  - enabled: bool — whether the module is ready
  - router: FastAPI APIRouter (lazy-imported)
"""

MODULES = {
    "amail": {
        "id": "amail",
        "name": "AMail",
        "icon": "📧",
        "description": "Email triage, Chinese summary, smart reply",
        "enabled": True,
        "router_path": "lilamy.modules.amail_routes:router",
    },
    "todo": {
        "id": "todo",
        "name": "To-Do List",
        "icon": "📋",
        "description": "Action items, deadlines, progress tracking",
        "enabled": True,
        "router_path": "lilamy.modules.todo_routes:router",
    },
    "variations": {
        "id": "variations",
        "name": "Variations",
        "icon": "📝",
        "description": "Client variation workflow — Excel, PDF, submission",
        "enabled": True,
        "router_path": "lilamy.modules.variation_routes:router",
        "extra_routers": [
            "lilamy.modules.project_routes:router",
            "lilamy.modules.variation_agent_routes:router",
        ],
    },
    "invoice_allocation": {
        "id": "invoice_allocation",
        "name": "Invoice Allocation",
        "icon": "📄",
        "description": "Auto-allocate invoice PDFs to project folders",
        "enabled": True,
        "router_path": "lilamy.modules.invoice_allocation_routes:router",
    },
    "progress_claims": {
        "id": "progress_claims",
        "name": "Progress Claims",
        "icon": "💰",
        "description": "Cashflow tracking and monthly progress claim generation",
        "enabled": True,
        "router_path": "lilamy.modules.progress_claim_routes:router",
    },
    # DEPRECATED — superseded by the To-Do List module above
    "acalendar": {
        "id": "acalendar",
        "name": "ACalendar",
        "icon": "📅",
        "description": "Schedule tracking, deadlines, conflicts (deprecated)",
        "enabled": False,
    },
    "adocuments": {
        "id": "adocuments",
        "name": "ADocuments",
        "icon": "📄",
        "description": "Document search, RAG, specifications",
        "enabled": False,
    },
    "areport": {
        "id": "areport",
        "name": "AReport",
        "icon": "📊",
        "description": "Project reports, weekly digests, analytics",
        "enabled": False,
    },
}


def get_enabled_modules() -> list[dict]:
    """Return enabled modules for the sidebar."""
    return [m for m in MODULES.values() if m["enabled"]]


def get_module(module_id: str) -> dict | None:
    """Return a module by ID, or None."""
    return MODULES.get(module_id)
