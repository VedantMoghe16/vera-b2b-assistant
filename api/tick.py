"""
api/tick.py — Periodic wake-up call from the judge.

Evaluates available triggers and decides if an outbound message is warranted.
"""

from typing import List
from fastapi import APIRouter, Request
from pydantic import BaseModel

from compose import compose

router = APIRouter()


class TickBody(BaseModel):
    now: str
    available_triggers: List[str] = []


@router.post("/tick")
async def tick(body: TickBody, request: Request):
    """
    Periodic wake-up call from the judge.
    Evaluates available triggers and decides if an outbound message is warranted.
    """
    contexts: dict = request.app.state.contexts
    conversations: dict = request.app.state.conversations
    actions = []

    for trg_id in body.available_triggers:
        trg = contexts.get(("trigger", trg_id), {}).get("payload")
        if not trg:
            continue

        # Hydrate the 4 contexts — safe access throughout
        merchant_id = trg.get("merchant_id")
        merchant = contexts.get(("merchant", merchant_id), {}).get("payload") if merchant_id else None

        # Guard: merchant can be None if context hasn't been pushed yet
        if not merchant:
            continue

        category_slug = merchant.get("category_slug")
        category = contexts.get(("category", category_slug), {}).get("payload") if category_slug else None

        customer_id = trg.get("customer_id")
        customer = contexts.get(("customer", customer_id), {}).get("payload") if customer_id else None

        if not category:
            continue

        # Run the composition engine (Selection -> Render -> LLM -> Validate -> Fallback)
        action_payload = compose(category, merchant, trg, customer)

        if action_payload:
            # Save the outgoing trigger context so the reply handler knows what we sent
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