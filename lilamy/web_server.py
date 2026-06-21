"""lilAmy WebUI backend — FastAPI server with module registry.

Usage:
    uv run lilamy --web          # Start at http://127.0.0.1:8765
    LILAMY_PORT=9000 uv run lilamy --web
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from lilamy.modules.registry import get_enabled_modules

app = FastAPI(
    title="lilAmy Platform",
    description="Multi-agent construction admin platform API",
    version="0.1.0",
)

# CORS (allow all in dev; tighten in production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

# ── Static files ─────────────────────────────────────────────────────

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def serve_spa():
    """Serve the SPA shell."""
    return FileResponse(STATIC_DIR / "index.html")


# ── Module routes ────────────────────────────────────────────────────

for mod in get_enabled_modules():
    router_paths = [mod.get("router_path")]
    # Support additional routers per module (e.g., project routes under variations)
    for extra in mod.get("extra_routers", []):
        router_paths.append(extra)

    for router_path in router_paths:
        if router_path:
            import importlib
            mod_path, attr = router_path.rsplit(":", 1)
            module = importlib.import_module(mod_path)
            router = getattr(module, attr)
            app.include_router(router)

# ── Development / test tools (not in main sidebar) ──────────────────

import lilamy.modules.pdf_test_routes as pdf_test_routes
app.include_router(pdf_test_routes.router)


# ── Platform endpoints ──────────────────────────────────────────────

@app.get("/api/modules")
async def list_modules():
    """Return the list of modules for the sidebar."""
    return {"modules": get_enabled_modules()}


@app.get("/api/health")
async def health():
    return {"status": "ok", "platform": "lilAmy"}
