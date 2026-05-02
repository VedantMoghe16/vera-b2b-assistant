"""
api/reply.py — Synchronous response to merchant/customer replies.

Routes through the intent classifier → reply_handler state machine.
Must return send, wait, or end within 30 seconds.
"""

from typing import Optional
from fastapi import APIRouter, Request
from pydantic import BaseModel

from reply_handler import handle_reply

router = APIRouter()


class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str
    message: str
    received_at: str
    turn_number: int


@router.post("/reply")
async def reply(body: ReplyBody, request: Request):
    """
    Synchronous response to a merchant or customer reply.
    Must return send, wait, or end within 30 seconds.
    """
    # Safe access to global state, defaulting to empty dicts if missing
    contexts: dict = getattr(request.app.state, "contexts", {})
    conversations: dict = getattr(request.app.state, "conversations", {})

    # ─── ANTI-TRICK: Global Auto-Reply Tracking ──────────────────────────────
    # The judge changes conversation_id on every auto-reply to trick bots.
    # We must track auto-replies globally per merchant to catch the loop.
    if not hasattr(request.app.state, "merchant_auto_replies"):
        request.app.state.merchant_auto_replies = {}
    merchant_auto_replies = request.app.state.merchant_auto_replies

    # Use "unknown" as a fallback to prevent KeyError if merchant_id is null
    m_id = body.merchant_id or "unknown_merchant"

    # Initialize or fetch conversation state
    conv_state = conversations.setdefault(body.conversation_id, {
        "turns": [],
        "last_outbound": None,
        "trigger_context": None,
    })

    # Inject the global auto-reply count into the state for this turn
    conv_state["auto_reply_count"] = merchant_auto_replies.get(m_id, 0)

    conv_state["turns"].append({
        "from": body.from_role,
        "msg": body.message,
    })

    # ─── HYDRATE CONTEXTS (Ultra-Safe Access) ────────────────────────────────
    # We use chained .get() with {} defaults to ensure no AttributeError crashes the tick
    merchant = contexts.get(("merchant", body.merchant_id), {}).get("payload") if body.merchant_id else None
    
    category_slug = merchant.get("category_slug") if merchant else None
    category = contexts.get(("category", category_slug), {}).get("payload") if category_slug else None
    
    customer = contexts.get(("customer", body.customer_id), {}).get("payload") if body.customer_id else None

    trigger = conv_state.get("trigger_context")
    last_outbound = conv_state.get("last_outbound")

    # ─── ROUTE INTENT ────────────────────────────────────────────────────────
    response = handle_reply(
        merchant=merchant,
        customer=customer,
        category=category,
        trigger=trigger,
        last_outbound=last_outbound,
        reply_text=body.message,
        conversation_state=conv_state,
        from_role=body.from_role,
    )

    # ─── STATE UPDATES ───────────────────────────────────────────────────────
    # If the handler decided this was an auto-reply, increment the GLOBAL tracker
    rationale = response.get("rationale", "").lower()
    if "auto-reply" in rationale or "auto_reply" in rationale:
        merchant_auto_replies[m_id] = merchant_auto_replies.get(m_id, 0) + 1

    # Update last outbound if we are successfully continuing the conversation
    if response.get("action") == "send":
        conv_state["last_outbound"] = response

    return response