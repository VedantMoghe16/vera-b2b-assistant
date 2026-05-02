"""
server.py — FastAPI application entry point for Vera.

Mounts the modular API routers and initializes shared state.
Run with: uvicorn server:app --host 0.0.0.0 --port 8000
"""

import time
from fastapi import FastAPI
from contextlib import asynccontextmanager

from api.context import router as context_router
from api.tick import router as tick_router
from api.reply import router as reply_router
from api.healthz import router as healthz_router
from api.metadata import router as metadata_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize shared in-memory state on startup."""
    app.state.contexts = {}        # Key: (scope, context_id) -> {version, payload}
    app.state.conversations = {}   # Key: conversation_id -> {turns, auto_reply_count, ...}
    app.state.start_time = time.time()
    yield
    # Shutdown: nothing to clean up for in-memory state


app = FastAPI(
    title="Vera — B2B AI Assistant",
    version="1.0.0",
    lifespan=lifespan,
)

from fastapi.responses import RedirectResponse

@app.get("/", include_in_schema=False)
async def root():
    """Redirect root to the interactive API docs."""
    return RedirectResponse(url="/docs")

# Mount all routers under the /v1 prefix
app.include_router(context_router, prefix="/v1")
app.include_router(tick_router, prefix="/v1")
app.include_router(reply_router, prefix="/v1")
app.include_router(healthz_router, prefix="/v1")
app.include_router(metadata_router, prefix="/v1")