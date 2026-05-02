"""
api/context.py — Receives incremental context pushes from the judge.

Must be idempotent and respect versioning.
"""

from datetime import datetime
from typing import Any, Dict
from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()


class ContextBody(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: Dict[str, Any]
    delivered_at: str


@router.post("/context")
async def push_context(body: ContextBody, request: Request):
    """
    Receives incremental context pushes from the judge.
    Must be idempotent and respect versioning.
    """
    # CRITICAL: access the shared dict initialized in server.py lifespan
    # This is a mutable dict stored on app.state — mutations are visible globally.
    contexts: dict = request.app.state.contexts

    key = (body.scope, body.context_id)
    cur = contexts.get(key)

    # Idempotency and version collision check
    if cur and cur["version"] >= body.version:
        return {
            "accepted": False,
            "reason": "stale_version",
            "current_version": cur["version"],
        }

    # Atomically replace/store the payload (in-place mutation of the shared dict)
    contexts[key] = {
        "version": body.version,
        "payload": body.payload,
    }

    return {
        "accepted": True,
        "ack_id": f"ack_{body.context_id}_v{body.version}",
        "stored_at": datetime.utcnow().isoformat() + "Z",
    }