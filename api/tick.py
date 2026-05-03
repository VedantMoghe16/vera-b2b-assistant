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

    # Initialize suppression and dedup sets if not present
    if not hasattr(request.app.state, "suppressions"):
        request.app.state.suppressions = set()
    suppressions: set = request.app.state.suppressions

    if not hasattr(request.app.state, "seen_tick_ids"):
        request.app.state.seen_tick_ids = set()
    seen_tick_ids: set = request.app.state.seen_tick_ids

    # ─── Tick-id dedup ───────────────────────────────────────────────────────
    # If the judge retries the same tick (network hiccup), return empty actions
    # so we don't fire duplicate messages to merchants.
    if body.tick_id:
        if body.tick_id in seen_tick_ids:
            print(f"[tick] DEDUP: already processed tick_id={body.tick_id}")
            return {"actions": []}
        seen_tick_ids.add(body.tick_id)

    actions = []

    print(f"[tick] tick_id={body.tick_id} available_triggers={body.available_triggers}")
    print(f"[tick] inline triggers={[t.trigger_id for t in body.triggers]}")
    print(f"[tick] stored context keys={list(contexts.keys())}")

    # Collect trigger payloads from both inline triggers and available_triggers IDs
    trigger_payloads = []

    for trg_obj in body.triggers:
        trigger_payloads.append(trg_obj.dict())

    for trg_id in body.available_triggers:
        trg = contexts.get(("trigger", trg_id), {}).get("payload")
        if trg:
            trigger_payloads.append(trg)
        else:
            print(f"[tick] SKIP available_trigger {trg_id}: not in context store")

    if not trigger_payloads:
        print("[tick] No trigger payloads — returning empty actions")
        return {"actions": []}

    print(f"[tick] Processing {len(trigger_payloads)} trigger(s)")

    week = datetime.utcnow().strftime("%G-W%V")

    for trg in trigger_payloads:
        merchant_id = trg.get("merchant_id")
        if not merchant_id:
            print("[tick] SKIP: no merchant_id in trigger")
            continue

        merchant = contexts.get(("merchant", merchant_id), {}).get("payload")
        if not merchant:
            print(f"[tick] SKIP: merchant {merchant_id} not in context store")
            print(f"[tick] available merchant keys={[k for k in contexts.keys() if k[0]=='merchant']}")
            continue

        # Category slug: root-level first, then identity.category
        category_slug = (
            merchant.get("category_slug")
            or merchant.get("identity", {}).get("category")
        )
        category = (
            contexts.get(("category", category_slug), {}).get("payload")
            if category_slug else None
        )
        if not category:
            print(f"[tick] category '{category_slug}' not found — composing without category context")

        customer_id = trg.get("customer_id")
        customer = (
            contexts.get(("customer", customer_id), {}).get("payload")
            if customer_id else None
        )

        # ─── Suppression pre-check ────────────────────────────────────────────
        kind = trg.get("kind") or trg.get("type", "unknown")
        pre_key = f"{kind}:{merchant_id}:{week}"
        if pre_key in suppressions:
            print(f"[tick] SUPPRESSED (weekly key): {pre_key}")
            continue

        print(f"[tick] Composing for merchant={merchant_id} kind={kind}")
        try:
            action_payload = compose(category or {}, merchant, trg, customer)
        except Exception as e:
            print(f"[tick] compose EXCEPTION: {e}")
            import traceback
            traceback.print_exc()
            continue

        if not action_payload:
            print("[tick] compose returned None — no grounded message possible")
            continue

        # ─── Suppression post-check and registration ──────────────────────────
        sup_key = action_payload.get("suppression_key", "")
        if sup_key and sup_key in suppressions:
            print(f"[tick] SUPPRESSED (exact key): {sup_key}")
            continue

        if sup_key:
            suppressions.add(sup_key)
        suppressions.add(pre_key)

        # Attach conversation state
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

    print(f"[tick] Returning {len(actions)} action(s)")
    return {"actions": actions}
