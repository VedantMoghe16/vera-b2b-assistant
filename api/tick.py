"""
api/tick.py — Periodic wake-up call from the judge.
"""

from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Request
from pydantic import BaseModel

from compose import compose

router = APIRouter()


class TriggerObject(BaseModel):
    trigger_id: str
    type: Optional[str] = None
    kind: Optional[str] = None
    merchant_id: Optional[str] = None
    signal: Optional[str] = None
    customer_id: Optional[str] = None


class TickBody(BaseModel):
    tick_id: Optional[str] = None
    ts: Optional[str] = None
    now: Optional[str] = None
    available_triggers: List[str] = []
    triggers: List[TriggerObject] = []


@router.post("/tick")
async def tick(body: TickBody, request: Request):
    contexts: dict = request.app.state.contexts
    conversations: dict = request.app.state.conversations

    # Initialize suppressions set if not present (belt-and-suspenders)
    if not hasattr(request.app.state, "suppressions"):
        request.app.state.suppressions = set()
    suppressions: set = request.app.state.suppressions

    actions = []

    print(f"[tick] available_triggers={body.available_triggers}")
    print(f"[tick] triggers={body.triggers}")
    print(f"[tick] stored context keys={list(contexts.keys())}")

    trigger_payloads = []

    for trg_obj in body.triggers:
        trigger_payloads.append(trg_obj.dict())

    for trg_id in body.available_triggers:
        trg = contexts.get(("trigger", trg_id), {}).get("payload")
        if trg:
            trigger_payloads.append(trg)

    print(f"[tick] trigger_payloads={trigger_payloads}")

    week = datetime.utcnow().strftime("%G-W%V")

    for trg in trigger_payloads:
        merchant_id = trg.get("merchant_id")
        print(f"[tick] looking up merchant_id={merchant_id}")

        if not merchant_id:
            print("[tick] SKIP: no merchant_id")
            continue

        merchant = contexts.get(("merchant", merchant_id), {}).get("payload")
        print(f"[tick] merchant found={merchant is not None}")

        if not merchant:
            print(f"[tick] SKIP: merchant not found for {merchant_id}")
            print(f"[tick] available merchant keys={[k for k in contexts.keys() if k[0]=='merchant']}")
            continue

        category_slug = (
            merchant.get("category_slug")
            or merchant.get("identity", {}).get("category")
        )
        print(f"[tick] category_slug={category_slug}")
        category = contexts.get(("category", category_slug), {}).get("payload") if category_slug else None
        print(f"[tick] category found={category is not None}")

        customer_id = trg.get("customer_id")
        customer = contexts.get(("customer", customer_id), {}).get("payload") if customer_id else None

        # ─── Suppression pre-check ────────────────────────────────────────────
        kind = trg.get("kind") or trg.get("type", "unknown")
        pre_key = f"{kind}:{merchant_id}:{week}"
        if pre_key in suppressions:
            print(f"[tick] SUPPRESSED (same week): {pre_key}")
            continue

        print(f"[tick] calling compose...")
        try:
            action_payload = compose(category or {}, merchant, trg, customer)
            print(f"[tick] compose result={action_payload}")
        except Exception as e:
            print(f"[tick] compose EXCEPTION: {e}")
            import traceback
            traceback.print_exc()
            continue

        if action_payload:
            # ─── Suppression post-check and registration ──────────────────────
            sup_key = action_payload.get("suppression_key", "")
            if sup_key and sup_key in suppressions:
                print(f"[tick] SUPPRESSED (exact key): {sup_key}")
                continue
            # Register both the full key and the simple week key
            if sup_key:
                suppressions.add(sup_key)
            suppressions.add(pre_key)

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
        else:
            print("[tick] compose returned None")

    print(f"[tick] final actions count={len(actions)}")
    return {"actions": actions}
