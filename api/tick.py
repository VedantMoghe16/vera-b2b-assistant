"""
api/tick.py — Periodic wake-up call from the judge.
"""

from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Request
from pydantic import BaseModel

from compose import compose

router = APIRouter()


class TriggerObject(BaseModel):
    trigger_id: str
    type: str
    merchant_id: str
    signal: Optional[str] = None
    customer_id: Optional[str] = None


class TickBody(BaseModel):
    tick_id: Optional[str] = None
    ts: Optional[str] = None
    now: Optional[str] = None
    available_triggers: List[str] = []          # judge sends trigger IDs
    triggers: List[TriggerObject] = []          # judge sends full objects


@router.post("/tick")
async def tick(body: TickBody, request: Request):
    contexts: dict = request.app.state.contexts
    conversations: dict = request.app.state.conversations
    actions = []

    # Build a unified list of trigger payloads from BOTH formats
    trigger_payloads = []

    # Format 1: full trigger objects sent directly
    for trg_obj in body.triggers:
        trigger_payloads.append(trg_obj.dict())

    # Format 2: trigger IDs — look up from stored context
    for trg_id in body.available_triggers:
        trg = contexts.get(("trigger", trg_id), {}).get("payload")
        if trg:
            trigger_payloads.append(trg)

    for trg in trigger_payloads:
        merchant_id = trg.get("merchant_id")
        if not merchant_id:
            continue

        merchant = contexts.get(("merchant", merchant_id), {}).get("payload")
        if not merchant:
            continue

        category_slug = (
            merchant.get("category_slug")
            or merchant.get("identity", {}).get("category")
        )
        category = contexts.get(("category", category_slug), {}).get("payload") if category_slug else None

        customer_id = trg.get("customer_id")
        customer = contexts.get(("customer", customer_id), {}).get("payload") if customer_id else None

        # category can be None — compose should handle it gracefully
        action_payload = compose(category, merchant, trg, customer)

        if action_payload:
            conv_id = action_payload["conversation_id"]
            conv_state = conversations.setdefault(conv_id, {
                "turns": [],
                "auto_reply_count": 0,
                "last_outbound": None,
                "trigger_context": None,
            })
            conv_state["trigger_context"] = trg
            conv_state["last_outbound"] = action_payload
            actions.append(action_payload)

    return {"actions": actions}