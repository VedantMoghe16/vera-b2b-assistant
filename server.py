"""
server.py — FastAPI application entry point for Vera.

Run with: uvicorn server:app --host 0.0.0.0 --port 8000
"""

import time
import threading
import os
from fastapi import FastAPI
from contextlib import asynccontextmanager
import httpx

from api.context import router as context_router
from api.tick import router as tick_router
from api.reply import router as reply_router
from api.healthz import router as healthz_router
from api.metadata import router as metadata_router


def start_keep_alive():
    """Pings own /v1/healthz every 4 minutes to prevent Render free tier sleep."""
    bot_url = os.environ.get("BOT_URL", "").rstrip("/")
    if not bot_url:
        print("[keep-alive] BOT_URL not set — skipping self-ping.")
        return

    def ping_loop():
        time.sleep(30)
        while True:
            try:
                resp = httpx.get(f"{bot_url}/v1/healthz", timeout=10)
                print(f"[keep-alive] Pinged healthz — status {resp.status_code}")
            except Exception as e:
                print(f"[keep-alive] Ping failed: {e}")
            time.sleep(240)

    thread = threading.Thread(target=ping_loop, daemon=True)
    thread.start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize shared in-memory state on startup."""
    app.state.contexts = {}         # (scope, context_id) -> {version, payload}
    app.state.conversations = {}    # conversation_id -> {turns, auto_reply_count, ...}
    app.state.suppressions = set()  # fired suppression_keys (weekly dedup)
    app.state.start_time = time.time()

    start_keep_alive()

    yield


app = FastAPI(
    title="Vera — B2B AI Assistant",
    version="2.0.0",
    lifespan=lifespan,
)

from fastapi.responses import RedirectResponse

@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/docs")

app.include_router(context_router, prefix="/v1")
app.include_router(tick_router, prefix="/v1")
app.include_router(reply_router, prefix="/v1")
app.include_router(healthz_router, prefix="/v1")
app.include_router(metadata_router, prefix="/v1")
