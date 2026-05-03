"""
api/reply.py — Synchronous response to merchant/customer replies.

Routes through the intent classifier → reply_handler state machine.
Must return send, wait, or end within 30 seconds.

Field aliasing: judge may send body/sender/ts instead of message/from_role/received_at.
Both forms are accepted.
"""

from typing import Optional
from fastapi import APIRouter, Request
from pydantic import BaseModel, model_validator

from reply_handler import handle_reply

router = APIRouter()


class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None

    # Accept 'from_role' OR 'sender' (judge may use either)
    from_role: Optional[str] = None
    sender: Optional[str] = None

    # Accept 'message' OR 'body' (judge may use either)
    message: Optional[str] = None
    body: Optional[str] = None

    # Accept 'received_at' OR 'ts' (judge may use either)
    received_at: Optional[str] = None
    ts: Optional[str] = None

    turn_number: Optional[int] = 0

    @model_validator(mode="after")
    def normalize_aliases(self):
        """Collapse alias pairs into canonical names after parsing."""
        # message wins over body; body is the fallback
        if not self.message and self.body:
            self.message = self.body
        if not self.message:
            self.message = ""

        # from_role wins over sender
        if not self.from_role and self.sender:
            self.from_role = self.sender
        if not self.from_role:
            self.from_role = "merchant"

        # received_at wins over ts
        if not self.received_at and self.ts:
            self.received_at = self.ts

        return self


@router.post("/reply")
async def reply(body: ReplyBody, request: Request):
    """
    Synchronous response to a merchant or customer reply.
    Must return send, wait, or end within 30 seconds.
    """
    contexts: dict = getattr(request.app.state, "contexts", {})
    conversations: dict = getattr(request.app.state, "conversations", {})

    # ─── Anti-trick: track auto-replies globally per merchant ─────────────────
    # The judge rotates conversation_id on every auto-reply. We track globally
    # per merchant_id so we detect the loop even across different conv IDs.
    if not hasattr(request.app.state, "merchant_auto_replies"):
        request.app.state.merchant_auto_replies = {}
    merchant_auto_replies: dict = request.app.state.merchant_auto_replies

    m_id = body.merchant_id or "unknown_merchant"

    # Initialize or fetch conversation state
    conv_state = conversations.setdefault(body.conversation_id, {
        "turns": [],
        "last_outbound": None,
        "trigger_context": None,
        "auto_reply_count": 0,
    })

    # Seed conv_state with the cross-conversation auto-reply count so
    # handle_reply can detect the running total even with rotated conv IDs.
    conv_state["auto_reply_count"] = merchant_auto_replies.get(m_id, 0)

    conv_state["turns"].append({
        "from": body.from_role,
        "msg": body.message,
    })

    # ─── Hydrate contexts ─────────────────────────────────────────────────────
    merchant = (
        contexts.get(("merchant", body.merchant_id), {}).get("payload")
        if body.merchant_id else None
    )

    # Category slug: try root-level first, fall back to identity.category
    category_slug = None
    if merchant:
        category_slug = (
            merchant.get("category_slug")
            or merchant.get("identity", {}).get("category")
        )
    category = (
        contexts.get(("category", category_slug), {}).get("payload")
        if category_slug else None
    )

    customer = (
        contexts.get(("customer", body.customer_id), {}).get("payload")
        if body.customer_id else None
    )

    trigger = conv_state.get("trigger_context")
    last_outbound = conv_state.get("last_outbound")

    # ─── Route intent ─────────────────────────────────────────────────────────
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

    # ─── State updates ────────────────────────────────────────────────────────
    # Propagate auto-reply count back to global tracker
    rationale_lower = response.get("rationale", "").lower()
    if "auto-reply" in rationale_lower or "auto_reply" in rationale_lower:
        merchant_auto_replies[m_id] = merchant_auto_replies.get(m_id, 0) + 1

    # Store outbound message in conv state for next turn's context
    if response.get("action") == "send":
        conv_state["last_outbound"] = response

    return response
