"""
fallback_ladder.py — graceful degradation when full grounding fails.

5 rungs, each producing a complete on-voice message:
  1. trigger_grounded    — full payload + full merchant
  2. signals_grounded    — merchant.signals[] only
  3. category_grounded   — category + locality only
  4. identity_grounded   — name + city + category only
  5. diagnostic          — nothing usable; ask one specific binary question

Returns a contract-shaped action dict, not None — fallback always succeeds.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional


def fallback_compose(category: dict, merchant: dict, trigger: dict,
                     customer: Optional[dict], rung: int = 2) -> Optional[dict]:
    """Climb down rungs from `rung` until one succeeds."""
    for r in range(rung, 6):
        result = _RUNG_HANDLERS[r](category, merchant, trigger, customer)
        if result:
            return result
    return None


def _rung_2_signals(category, merchant, trigger, customer):
    """Use merchant.signals[] to produce a specific message."""
    signals = merchant.get("signals", [])
    if not signals:
        return None

    owner = merchant.get("identity", {}).get("owner_first_name", "")
    sname = merchant.get("identity", {}).get("name", "")
    if not owner or not sname:
        return None

    # Find the highest-leverage signal to act on
    if "unverified_gbp" in signals:
        body = (
            f"{owner}, {sname} isn't verified on Google yet — that's the "
            f"single biggest free lift available right now. Verification "
            f"is one phone call from Google, ~5 days end-to-end. Should "
            f"I start the flow?"
        )
        return _wrap(merchant, trigger, body, "binary_yes_no",
                     "vera_fallback_unverified_v1", [owner, sname])

    if "renewal_due_soon" in str(signals):
        body = (
            f"{owner}, your subscription renewal is coming up soon. Want "
            f"me to share the renewal link, or hold off until you've "
            f"reviewed your usage?"
        )
        return _wrap(merchant, trigger, body, "binary_yes_no",
                     "vera_fallback_renewal_v1", [owner])

    if "no_active_offers" in signals:
        # Pull from category catalog
        catalog = category.get("offer_catalog", [])
        if catalog:
            offer = catalog[0]
            body = (
                f"{owner}, {sname} doesn't have an active offer right now. "
                f"Most peers in your category are running '{offer.get('title', 'a starter offer')}' "
                f"as a new-user hook. Want me to set it up on your GBP + "
                f"WhatsApp?"
            )
            return _wrap(merchant, trigger, body, "binary_yes_no",
                         "vera_fallback_no_offer_v1",
                         [owner, sname, offer.get("title", "")])

    return None


def _rung_3_category(category, merchant, trigger, customer):
    """Use category-level peer stats + locality + performance numbers."""
    locality = merchant.get("identity", {}).get("locality", "")
    owner = merchant.get("identity", {}).get("owner_first_name", "")
    sname = merchant.get("identity", {}).get("name", "")
    if not owner or not locality or not sname:
        return None

    peer_stats = category.get("peer_stats", {})
    digest = category.get("digest", [])
    if not digest:
        return None

    # Pick a "trend" or "tech" digest item — most actionable
    actionable_item = next(
        (d for d in digest
         if d.get("kind") in ("trend", "tech") and d.get("actionable")),
        digest[0] if digest else None,
    )
    if not actionable_item:
        return None

    title = actionable_item.get("title", "")
    actionable = actionable_item.get("actionable", "")

    # Add performance numbers for specificity anchors
    perf = merchant.get("performance", {})
    views = perf.get("views", 0)
    calls = perf.get("calls", 0)
    perf_str = ""
    if views or calls:
        perf_str = f" With {views} views and {calls} calls this month,"

    body = (
        f"{owner} — quick note from this week's {category.get('display_name', 'industry')} "
        f"digest. {title}. Practical angle for {sname} in {locality}: "
        f"{actionable}.{perf_str} want me to spec out the next step?"
    )
    return _wrap(merchant, trigger, body, "binary_yes_no",
                 "vera_fallback_category_v1",
                 [owner, sname, locality, title, actionable])


def _rung_4_identity(category, merchant, trigger, customer):
    """Only owner_first_name, name, city, category survive."""
    owner = merchant.get("identity", {}).get("owner_first_name", "")
    sname = merchant.get("identity", {}).get("name", "")
    city = merchant.get("identity", {}).get("city", "")
    if not (owner and sname and city):
        return None

    cat_name = category.get("display_name", "your category")

    body = (
        f"Hi {owner} — Vera here from magicpin. I'm noticing {cat_name} "
        f"in {city} are seeing strong growth in WhatsApp-based customer "
        f"reactivation right now (~3x retention vs walk-in only). Worth a "
        f"2-min walkthrough for {sname}?"
    )
    return _wrap(merchant, trigger, body, "binary_yes_no",
                 "vera_fallback_identity_v1", [owner, sname, city, cat_name])


def _rung_5_diagnostic(category, merchant, trigger, customer):
    """Nothing usable — ask one specific binary diagnostic."""
    owner = merchant.get("identity", {}).get("owner_first_name", "there")
    cat_name = category.get("display_name", "")

    body = (
        f"Hi {owner} — Vera here. To send something useful instead of "
        f"generic, one quick question: are most of your customers walk-in, "
        f"or do you also do home delivery / appointments via phone? Reply "
        f"A for walk-in mostly, B for mixed, C for delivery/appointment-led."
    )
    return _wrap(merchant, trigger, body, "multi_choice",
                 "vera_fallback_diagnostic_v1", [owner, cat_name])


def _rung_1_trigger(category, merchant, trigger, customer):
    """Rung 1: Full trigger + merchant grounding — try to produce a specific message
    directly from trigger payload when the normal template path fails."""
    kind = trigger.get("kind", "")
    payload = trigger.get("payload", {})
    owner = merchant.get("identity", {}).get("owner_first_name", "")
    sname = merchant.get("identity", {}).get("name", "")
    if not (owner and sname and kind):
        return None

    # Build a concise, grounded message from the raw trigger payload
    facts = []
    for k, v in payload.items():
        if v and k not in ("merchant_id", "customer_id", "id", "scope"):
            facts.append(f"{k.replace('_', ' ')}: {v}")
    if not facts:
        return None

    fact_str = "; ".join(facts[:3])  # cap at 3 facts for brevity
    body = (
        f"{owner}, a quick update on {kind.replace('_', ' ')} for {sname}. "
        f"{fact_str}. Want me to walk you through the next step?"
    )
    return _wrap(merchant, trigger, body, "binary_yes_no",
                 "vera_fallback_trigger_v1", [owner, sname, kind, fact_str])


_RUNG_HANDLERS = {
    1: _rung_1_trigger,
    2: _rung_2_signals,
    3: _rung_3_category,
    4: _rung_4_identity,
    5: _rung_5_diagnostic,
}


def _wrap(merchant: dict, trigger: dict, body: str, cta: str,
          template_name: str, params: list) -> dict:
    """Build the contract-shaped action dict for a fallback message."""
    mid = merchant.get("merchant_id", "unknown")
    short_mid = mid.split("_")[1] if "_" in mid else mid[:6]
    kind = trigger.get("kind", "fallback")
    week = datetime.utcnow().strftime("W%V")
    conv_id = f"conv_m_{short_mid}_{kind}_{week}_fb"
    sup_key = (f"fallback:{kind}:{mid}:"
               f"{hashlib.sha256(body.encode()).hexdigest()[:6]}")
    rationale = (f"Full-grounding compose unavailable; fell back to specific "
                 f"category/identity-level message. Maintains specificity "
                 f"without inventing merchant-specific facts.")
    return {
        "body": body,
        "cta": cta,
        "template_name": template_name,
        "template_params": [str(p) for p in params],
        "conversation_id": conv_id,
        "send_as": "vera",
        "suppression_key": sup_key,
        "rationale": rationale,
    }