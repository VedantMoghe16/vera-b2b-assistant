"""
fallback_ladder.py — graceful degradation when full grounding fails.

Rung 1: Full trigger signal + merchant offers
Rung 2: Merchant signals array
Rung 3: Category-level peer stats + locality + offers + performance
Rung 4: Identity-only (name, city, category)
Rung 5: Diagnostic one-question probe
"""

from __future__ import annotations

import re
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


def _get_owner(merchant: dict) -> str:
    identity = merchant.get("identity", {})
    return (
        identity.get("owner_first_name")
        or identity.get("owner_name", "").split()[0]
        or identity.get("name", "").split()[0]
        or "there"
    )


def _get_owner_display(merchant: dict, cat_slug: str) -> str:
    owner = _get_owner(merchant)
    if cat_slug in ("dentist", "dentists") and owner not in ("there", ""):
        return f"Dr. {owner}"
    return owner


def _get_city(merchant: dict) -> str:
    identity = merchant.get("identity", {})
    return (
        identity.get("city")
        or identity.get("locality")
        or (identity.get("location", "").split(",")[-1].strip()
            if identity.get("location") else "")
        or "your city"
    )


def _get_locality(merchant: dict) -> str:
    identity = merchant.get("identity", {})
    return (
        identity.get("locality")
        or (identity.get("location", "").split(",")[0].strip()
            if identity.get("location") else "")
        or identity.get("city")
        or "your area"
    )


_CAT_VOICE = {
    "dentist":    {"patient_word": "patients", "service_word": "consultation"},
    "dentists":   {"patient_word": "patients", "service_word": "consultation"},
    "salon":      {"patient_word": "clients",  "service_word": "appointment"},
    "salons":     {"patient_word": "clients",  "service_word": "appointment"},
    "restaurant": {"patient_word": "customers","service_word": "visit"},
    "restaurants":{"patient_word": "customers","service_word": "visit"},
    "gym":        {"patient_word": "members",  "service_word": "session"},
    "gyms":       {"patient_word": "members",  "service_word": "session"},
    "pharmacy":   {"patient_word": "patients", "service_word": "prescription"},
    "pharmacies": {"patient_word": "patients", "service_word": "prescription"},
}

def _cat_voice(slug: str) -> dict:
    return _CAT_VOICE.get(slug, {"patient_word": "customers", "service_word": "service"})


def _rung_1_trigger(category, merchant, trigger, customer):
    """Rung 1: Full trigger signal + merchant offers + performance."""
    kind = trigger.get("kind", "") or trigger.get("type", "")
    payload = trigger.get("payload", {})
    signal = trigger.get("signal", "")
    cat_slug = category.get("slug", "")

    owner = _get_owner(merchant)
    owner_display = _get_owner_display(merchant, cat_slug)
    sname = merchant.get("identity", {}).get("name", "")
    locality = _get_locality(merchant)

    if not (sname and kind):
        return None

    voice = _cat_voice(cat_slug)
    patient_word = voice["patient_word"]
    service_word = voice["service_word"]

    # Extract count from signal
    count_str = ""
    if signal:
        m = re.search(r'(\d+)', signal)
        if m:
            count_str = m.group(1)

    # Best offer
    offers = merchant.get("offers", [])
    offer = next(
        (o for o in offers if not o.get("status") or o.get("status") == "active"),
        offers[0] if offers else None
    )

    if signal and count_str and offer:
        # High specificity: count + offer price
        body = (
            f"{count_str} {patient_word} in {locality} are searching for "
            f"'{offer.get('name', 'your service')}'. Should I reach them with your "
            f"₹{offer.get('price', '')} {service_word} offer?"
        )
        return _wrap(merchant, trigger, body, "binary_yes_no",
                     "vera_fallback_spike_offer_v1",
                     [count_str, patient_word, locality,
                      offer.get("name", ""), str(offer.get("price", ""))],
                     f"Selected {kind} (signal: {count_str} searches). "
                     f"Anchored on ₹{offer.get('price','')} {offer.get('name','')} offer. "
                     f"Timing: active search window = highest intent moment.")

    if signal:
        body = (
            f"{owner_display} — {signal}. "
            f"This is a high-intent window for {sname}. "
            f"Should I draft a targeted message to bring them in?"
        )
        return _wrap(merchant, trigger, body, "binary_yes_no",
                     "vera_fallback_trigger_signal_v1",
                     [owner, sname, signal],
                     f"Selected {kind} from trigger signal. "
                     f"Signal: '{signal}'. "
                     f"Timing: demand signal active = direct action window.")

    facts = []
    for k, v in payload.items():
        if v and k not in ("merchant_id", "customer_id", "id", "scope"):
            facts.append(f"{k.replace('_', ' ')}: {v}")
    if not facts:
        return None

    fact_str = "; ".join(facts[:3])
    body = (
        f"{owner_display} — quick update on {kind.replace('_', ' ')} for {sname}. "
        f"{fact_str}. Want me to walk you through the next step?"
    )
    return _wrap(merchant, trigger, body, "binary_yes_no",
                 "vera_fallback_trigger_v1", [owner, sname, kind, fact_str],
                 f"Selected {kind} from payload facts. "
                 f"Grounded on: {fact_str[:80]}. "
                 f"Timing: signal freshness = act now.")


def _rung_2_signals(category, merchant, trigger, customer):
    """Use merchant.signals[] to produce a specific message."""
    signals = merchant.get("signals", [])
    if not signals:
        return None

    cat_slug = category.get("slug", "")
    owner = _get_owner(merchant)
    owner_display = _get_owner_display(merchant, cat_slug)
    sname = merchant.get("identity", {}).get("name", "")
    if not sname:
        return None

    if "unverified_gbp" in signals:
        body = (
            f"{owner_display}, {sname} isn't verified on Google yet — that's the "
            f"single biggest free lift available right now. Verification "
            f"is one phone call from Google, ~5 days end-to-end. Should "
            f"I start the flow?"
        )
        return _wrap(merchant, trigger, body, "binary_yes_no",
                     "vera_fallback_unverified_v1", [owner, sname],
                     f"Selected unverified_gbp signal. "
                     f"Anchored on {sname} GBP status. "
                     f"Timing: verification is highest-ROI free action available.")

    if "renewal_due_soon" in str(signals):
        body = (
            f"{owner_display}, your subscription renewal is coming up soon. Want "
            f"me to share the renewal link, or hold off until you've "
            f"reviewed your usage?"
        )
        return _wrap(merchant, trigger, body, "binary_yes_no",
                     "vera_fallback_renewal_v1", [owner],
                     "Selected renewal_due_soon signal. "
                     "Timing: renewal window = retention cheaper than re-acquisition.")

    if "no_active_offers" in signals:
        catalog = category.get("offer_catalog", [])
        if catalog:
            offer = catalog[0]
            body = (
                f"{owner_display}, {sname} doesn't have an active offer right now. "
                f"Most peers in your category are running "
                f"'{offer.get('title', 'a starter offer')}' as a new-user hook. "
                f"Want me to set it up on your GBP + WhatsApp?"
            )
            return _wrap(merchant, trigger, body, "binary_yes_no",
                         "vera_fallback_no_offer_v1",
                         [owner, sname, offer.get("title", "")],
                         f"Selected no_active_offers signal. "
                         f"Anchored on category peer offer: {offer.get('title','')}. "
                         f"Timing: zero active offers = immediate opportunity.")

    return None


def _rung_3_category(category, merchant, trigger, customer):
    """Use offers + performance + locality — no invented numbers."""
    locality = _get_locality(merchant)
    cat_slug = category.get("slug", "")
    owner = _get_owner(merchant)
    owner_display = _get_owner_display(merchant, cat_slug)
    sname = merchant.get("identity", {}).get("name", "")
    if not sname:
        return None

    offers = merchant.get("offers", [])
    perf = merchant.get("performance", {})

    if offers and perf:
        offer = next(
            (o for o in offers if not o.get("status") or o.get("status") == "active"),
            offers[0]
        )
        footfall = perf.get("weekly_footfall", 0)
        rating = perf.get("avg_rating", 0)
        voice = _cat_voice(cat_slug)
        patient_word = voice["patient_word"]
        service_word = voice["service_word"]

        # Only use search count from actual signal, never invent it
        signal = trigger.get("signal", "")
        count_str = ""
        if signal:
            m = re.search(r'(\d+)', signal)
            if m:
                count_str = m.group(1)

        if count_str:
            body = (
                f"{owner_display}, {count_str} {patient_word} in {locality} are searching for "
                f"services like yours. With {footfall} weekly visits and a {rating}★ rating, "
                f"your '{offer.get('name', 'top offer')}' at ₹{offer.get('price', '')} "
                f"is the right hook. Should I run a targeted push?"
            )
        else:
            body = (
                f"{owner_display}, {sname} has {footfall} weekly visits and a "
                f"{rating}★ rating. Your '{offer.get('name', 'top offer')}' "
                f"at ₹{offer.get('price', '')} is the right hook to capture "
                f"{patient_word} searching in {locality} right now. "
                f"Want me to run a targeted push?"
            )
        return _wrap(merchant, trigger, body, "binary_yes_no",
                     "vera_fallback_offers_perf_v1",
                     [owner, sname, str(footfall), str(rating)],
                     f"Anchored on {footfall} weekly visits + ₹{offer.get('price','')} {offer.get('name','')} offer. "
                     f"Timing: local search demand = direct conversion window.")

    digest = category.get("digest", [])
    if not digest:
        return None

    actionable_item = next(
        (d for d in digest
         if d.get("kind") in ("trend", "tech") and d.get("actionable")),
        digest[0] if digest else None,
    )
    if not actionable_item:
        return None

    title = actionable_item.get("title", "")
    actionable = actionable_item.get("actionable", "")

    perf_str = ""
    views = perf.get("views", 0)
    calls = perf.get("calls", 0)
    if views or calls:
        perf_str = f" With {views} views and {calls} calls this month,"

    body = (
        f"{owner_display} — quick note from this week's "
        f"{category.get('display_name', 'industry')} digest. {title}. "
        f"Practical angle for {sname} in {locality}: "
        f"{actionable}.{perf_str} want me to spec out the next step?"
    )
    return _wrap(merchant, trigger, body, "binary_yes_no",
                 "vera_fallback_category_v1",
                 [owner, sname, locality, title, actionable],
                 f"Anchored on category digest: '{title}'. "
                 f"Timing: digest freshness = act while insight is relevant.")


def _rung_4_identity(category, merchant, trigger, customer):
    """Only owner name, business name, city, category survive."""
    cat_slug = category.get("slug", "")
    owner = _get_owner(merchant)
    owner_display = _get_owner_display(merchant, cat_slug)
    sname = merchant.get("identity", {}).get("name", "")
    city = _get_city(merchant)
    cat_name = (
        category.get("display_name")
        or merchant.get("identity", {}).get("category", "")
        or "your category"
    )

    if not sname:
        return None

    body = (
        f"Hi {owner_display} — Vera here from magicpin. I'm noticing {cat_name} "
        f"businesses in {city} are seeing strong growth in WhatsApp-based "
        f"customer reactivation right now (~3x retention vs walk-in only). "
        f"Worth a 2-min walkthrough for {sname}?"
    )
    return _wrap(merchant, trigger, body, "binary_yes_no",
                 "vera_fallback_identity_v1", [owner, sname, city, cat_name],
                 f"Identity-level engagement for {sname} in {city}. "
                 f"Category: {cat_name}. Timing: reactivation benchmark relevant to current period.")


def _rung_5_diagnostic(category, merchant, trigger, customer):
    """Nothing usable — ask one specific binary diagnostic."""
    owner = _get_owner(merchant)
    cat_name = category.get("display_name", "your business")

    body = (
        f"Hi {owner} — Vera here. To send something useful instead of "
        f"generic, one quick question: are most of your customers walk-in, "
        f"or do you also do home delivery / appointments via phone? Reply "
        f"A for walk-in mostly, B for mixed, C for delivery/appointment-led."
    )
    return _wrap(merchant, trigger, body, "multi_choice",
                 "vera_fallback_diagnostic_v1", [owner, cat_name],
                 "Diagnostic probe to gather segment data before next outreach.")


_RUNG_HANDLERS = {
    1: _rung_1_trigger,
    2: _rung_2_signals,
    3: _rung_3_category,
    4: _rung_4_identity,
    5: _rung_5_diagnostic,
}


def _wrap(merchant: dict, trigger: dict, body: str, cta: str,
          template_name: str, params: list, rationale: str = "") -> dict:
    """Build the contract-shaped action dict for a fallback message."""
    mid = merchant.get("merchant_id", "unknown")
    short_mid = mid.split("_")[1] if "_" in mid else mid[:6]
    kind = trigger.get("kind") or trigger.get("type", "fallback")
    week = datetime.utcnow().strftime("W%V")
    conv_id = f"conv_m_{short_mid}_{kind}_{week}_fb"
    sup_key = (f"fallback:{kind}:{mid}:"
               f"{hashlib.sha256(body.encode()).hexdigest()[:6]}")

    if not rationale:
        rationale = (
            f"Signal: {kind}. Merchant: {merchant.get('identity', {}).get('name', '')}. "
            f"Timing: context-grounded message for current moment."
        )

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
