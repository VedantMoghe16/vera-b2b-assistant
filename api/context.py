"""
api/context.py — Receives incremental context pushes from the judge.

Must be idempotent and respect versioning:
  same version  → no-op (accepted: false, stale_version)
  lower version → no-op (accepted: false, stale_version)
  higher version → replace stored payload
"""

from datetime import datetime
from typing import Any, Dict, Optional
from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()


class ContextBody(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: Dict[str, Any]
    delivered_at: Optional[str] = None  # judge may omit; not required internally


@router.post("/context")
async def push_context(body: ContextBody, request: Request):
    """
    Receives incremental context pushes from the judge.
    Scopes: merchant, category, customer, trigger.
    """
    contexts: dict = request.app.state.contexts

    key = (body.scope, body.context_id)
    cur = contexts.get(key)

    # Idempotency: reject same or older versions
    if cur and cur["version"] >= body.version:
        return {
            "accepted": False,
            "reason": "stale_version",
            "current_version": cur["version"],
        }

    contexts[key] = {
        "version": body.version,
        "payload": body.payload,
    }

    return {
        "accepted": True,
        "ack_id": f"ack_{body.context_id}_v{body.version}",
        "stored_at": datetime.utcnow().isoformat() + "Z",
    }
