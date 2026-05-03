"""
compose.py — Vera's hybrid message composition engine.

compose(category, merchant, trigger, customer=None) -> dict | None

Returns the contract-shaped output:
  { conversation_id, send_as, template_name, template_params,
    body, cta, suppression_key, rationale }
"""

from __future__ import annotations

import os
import re
import hashlib
from datetime import datetime
from typing import Any

from templates.registry import get_template, render_template
from validators import validate_output, ValidationResult
from fallback_ladder import fallback_compose


# ─── Category voice profiles (used for LLM enforcement + direct compose) ─────

CATEGORY_VOICE = {
    "dentist": {
        "patient_word": "patients",
        "service_word": "consultation",
        "tone": "clinical, evidence-based, professional",
        "instructions": (
            "Use clinical, professional language. Call people 'patients', never 'customers'. "
            "Use 'consultation', 'check-up', 'treatment', 'clinical assessment'. "
            "Never use 'discount', 'cheap', 'deal', or casual marketing language."
        ),
        "taboo": ["discount", "cheap", "deal", "grab", "sale"],
    },
    "dentists": {
        "patient_word": "patients",
        "service_word": "consultation",
        "tone": "clinical, evidence-based, professional",
        "instructions": (
            "Use clinical, professional language. Call people 'patients', never 'customers'. "
            "Use 'consultation', 'check-up', 'treatment', 'clinical assessment'. "
            "Never use 'discount', 'cheap', 'deal', or casual marketing language."
        ),
        "taboo": ["discount", "cheap", "deal", "grab", "sale"],
    },
    "salon": {
        "patient_word": "clients",
        "service_word": "appointment",
        "tone": "warm, visual, aspirational",
        "instructions": (
            "Use warm, aspirational language. Focus on how clients will look and feel beautiful. "
            "Use 'style', 'glow', 'bridal', 'look', 'transform', 'radiant'. "
            "Be encouraging and visual."
        ),
        "taboo": [],
    },
    "salons": {
        "patient_word": "clients",
        "service_word": "appointment",
        "tone": "warm, visual, aspirational",
        "instructions": (
            "Use warm, aspirational language. Focus on how clients will look and feel beautiful. "
            "Use 'style', 'glow', 'bridal', 'look', 'transform', 'radiant'. "
            "Be encouraging and visual."
        ),
        "taboo": [],
    },
    "restaurant": {
        "patient_word": "customers",
        "service_word": "visit",
        "tone": "urgent, appetite-driven, time-sensitive",
        "instructions": (
            "Create urgency and appetite. Use 'today', 'tonight', 'table', 'hungry', 'fresh', 'hot'. "
            "Make it time-sensitive. Drive immediate action."
        ),
        "taboo": [],
    },
    "restaurants": {
        "patient_word": "customers",
        "service_word": "visit",
        "tone": "urgent, appetite-driven, time-sensitive",
        "instructions": (
            "Create urgency and appetite. Use 'today', 'tonight', 'table', 'hungry', 'fresh', 'hot'. "
            "Make it time-sensitive. Drive immediate action."
        ),
        "taboo": [],
    },
    "gym": {
        "patient_word": "members",
        "service_word": "session",
        "tone": "motivational, data-driven, energetic",
        "instructions": (
            "Be motivational and data-driven. Use 'streak', 'goal', 'session', 'transform', "
            "'challenge', 'progress', 'commit'. Build momentum and accountability."
        ),
        "taboo": [],
    },
    "gyms": {
        "patient_word": "members",
        "service_word": "session",
        "tone": "motivational, data-driven, energetic",
        "instructions": (
            "Be motivational and data-driven. Use 'streak', 'goal', 'session', 'transform', "
            "'challenge', 'progress', 'commit'. Build momentum and accountability."
        ),
        "taboo": [],
    },
    "pharmacy": {
        "patient_word": "patients",
        "service_word": "prescription",
        "tone": "compliance-first, caring, informative",
        "instructions": (
            "Be caring and compliance-focused. Use 'prescription', 'refill', 'health', "
            "'dose', 'medication', 'treatment plan'. Prioritize patient wellbeing over sales."
        ),
        "taboo": [],
    },
    "pharmacies": {
        "patient_word": "patients",
        "service_word": "prescription",
        "tone": "compliance-first, caring, informative",
        "instructions": (
            "Be caring and compliance-focused. Use 'prescription', 'refill', 'health', "
            "'dose', 'medication', 'treatment plan'. Prioritize patient wellbeing over sales."
        ),
        "taboo": [],
    },
}

_DEFAULT_VOICE = {
    "patient_word": "customers",
    "service_word": "service",
    "tone": "professional, helpful",
    "instructions": "Be professional and helpful. Focus on the merchant's business growth.",
    "taboo": [],
}


def _get_voice(cat_slug: str) -> dict:
    return CATEGORY_VOICE.get(cat_slug, _DEFAULT_VOICE)


# ─── Main entry ───────────────────────────────────────────────────────────────

def compose(category: dict, merchant: dict, trigger: dict,
            customer: dict | None = None) -> dict | None:
    """
    Main entry point. Returns contract-shaped action dict or None if no
    grounded message can be produced.
    """
    # ─── Stage 0: Normalize trigger and merchant fields ──────────────────────
    if trigger and not trigger.get("kind") and trigger.get("type"):
        trigger = dict(trigger)
        trigger["kind"] = trigger["type"]

    if merchant and not merchant.get("merchant_id"):
        merchant = dict(merchant)
        merchant["merchant_id"] = (
            merchant.get("identity", {}).get("name", "unknown")
            .lower().replace(" ", "_")
        )

    if category is None:
        category = {}

    # ─── Stage 1: Validate inputs ────────────────────────────────────────────
    if not _has_required_fields(merchant, trigger):
        return fallback_compose(category, merchant, trigger, customer, rung=4)

    # ─── Stage 2: Resolve references ─────────────────────────────────────────
    enriched = _resolve_references(category, merchant, trigger, customer)
    if enriched is None:
        enriched = {"trigger_payload": trigger.get("payload", {})}

    # ─── Stage 3: Select winning trigger from candidates ─────────────────────
    candidates = [trigger] + _derive_implicit_triggers(merchant)
    candidates = [c for c in candidates if c is not None]

    override = _check_hard_override(candidates, merchant)
    winner = override if override else _select_winner(candidates, merchant, customer)

    # ─── Stage 4: Look up template ───────────────────────────────────────────
    template = get_template(winner.get("kind"), category.get("slug"))
    if template is None:
        direct = _direct_compose(category, merchant, winner, customer)
        if direct:
            return direct
        return fallback_compose(category, merchant, winner, customer, rung=2)

    # ─── Stage 5: Render with slot validation (DETERMINISTIC BASE) ───────────
    base_rendered = render_template(template, category, merchant, winner, customer, enriched)
    if base_rendered is None:
        direct = _direct_compose(category, merchant, winner, customer)
        if direct:
            return direct
        return fallback_compose(category, merchant, winner, customer, rung=2)

    # ─── Stage 5.5: LLM Enhancement ──────────────────────────────────────────
    rendered = _enhance_with_llm(base_rendered, category, merchant, winner, customer)

    # ─── Stage 6: Validate output ─────────────────────────────────────────────
    result = ValidationResult()
    validate_output(rendered, category, merchant, winner, customer, result)

    if not result.passed:
        base_result = ValidationResult()
        validate_output(base_rendered, category, merchant, winner, customer, base_result)

        if base_result.passed:
            rendered = base_rendered
        else:
            return fallback_compose(category, merchant, winner, customer, rung=3)

    # ─── Stage 7: Assemble final action ──────────────────────────────────────
    conversation_id = _make_conversation_id(merchant, winner, customer)
    suppression_key = _make_suppression_key(winner, merchant)
    send_as = _determine_send_as(winner, customer)

    return {
        "conversation_id": conversation_id,
        "send_as": send_as,
        "template_name": rendered["template_name"],
        "template_params": rendered.get("template_params", []),
        "body": rendered["body"],
        "cta": rendered["cta"],
        "suppression_key": suppression_key,
        "rationale": _build_rationale(winner, candidates, merchant, customer),
    }


# ─── Direct Signal Composition (belt-and-suspenders when template misses) ────

def _direct_compose(category: dict, merchant: dict, trigger: dict,
                    customer: dict | None) -> dict | None:
    """
    Build a message directly from trigger.signal + merchant offers + performance.
    Only uses facts present in the received context — zero hallucination.
    """
    signal = trigger.get("signal", "")
    kind = trigger.get("kind", "") or trigger.get("type", "")
    payload = trigger.get("payload", {})

    identity = merchant.get("identity", {})
    owner_raw = (identity.get("owner_first_name") or
                 identity.get("owner_name", "").split()[0] or "there")
    sname = identity.get("name", "")
    locality = (identity.get("locality") or
                (identity.get("location", "").split(",")[0].strip()
                 if identity.get("location") else "") or
                identity.get("city", ""))

    if not sname:
        return None

    cat_slug = category.get("slug", "")
    voice = _get_voice(cat_slug)
    patient_word = voice["patient_word"]
    service_word = voice["service_word"]

    owner_display = f"Dr. {owner_raw}" if cat_slug in ("dentist", "dentists") else owner_raw

    # Extract number from signal text
    count_str = ""
    if signal:
        m = re.search(r'(\d+)', signal)
        if m:
            count_str = m.group(1)

    # Best available offer (prefer active, accept any)
    offers = merchant.get("offers", [])
    offer = next(
        (o for o in offers if not o.get("status") or o.get("status") == "active"),
        offers[0] if offers else None
    )

    locality_str = locality or "your locality"
    body = None

    if kind in ("research_spike",) and count_str:
        if offer:
            body = (
                f"{count_str} {patient_word} in {locality_str} are searching for "
                f"'{offer.get('name', 'your service')}'. Should I reach them with your "
                f"₹{offer.get('price', '')} {service_word} offer?"
            )
        else:
            body = (
                f"{count_str} {patient_word} in {locality_str} are actively searching for "
                f"services like yours. Should I help you reach them now?"
            )

    elif kind in ("perf_dip", "perf_spike"):
        metric = payload.get("metric", "visits")
        delta = payload.get("delta_pct", "")
        if offer:
            direction = "dropped" if kind == "perf_dip" else "spiked"
            delta_str = f" {delta}%" if delta else ""
            body = (
                f"{owner_display}, your {metric} have {direction}{delta_str} this week. "
                f"Your '{offer.get('name', 'top offer')}' at ₹{offer.get('price', '')} "
                f"is the right move right now. Should I run a targeted push?"
            )

    elif kind in ("recall", "lapse") and customer:
        name = customer.get("identity", {}).get("name", "").split("(")[0].strip()
        days = payload.get("days_since_last_visit", payload.get("days_lapsed", 0))
        weeks = (days // 7) if days else None
        time_str = f"{weeks} weeks" if weeks else "a while"
        body = (
            f"Hi {name}, {sname} here — it's been {time_str} since your last visit. "
            f"Ready to book your next {service_word}?"
        )

    elif kind in ("festival", "festival_upcoming"):
        festival = payload.get("festival", "")
        days_to = payload.get("days_until", payload.get("days_to_festival", ""))
        if festival and offer:
            body = (
                f"{owner_display}, {festival} is {days_to} days away. "
                f"Want me to draft a special promotion using your '{offer.get('name', '')}' "
                f"at ₹{offer.get('price', '')} to bring in more {patient_word}?"
            )

    elif signal:
        if offer:
            body = (
                f"{owner_display}, {signal}. "
                f"Your '{offer.get('name', '')}' at ₹{offer.get('price', '')} "
                f"is the right hook for this moment. Should I draft a campaign?"
            )
        else:
            body = (
                f"{owner_display}, {signal}. "
                f"Should I help you act on this opportunity for {sname}?"
            )

    if not body:
        return None

    conversation_id = _make_conversation_id(merchant, trigger, customer)
    suppression_key = _make_suppression_key(trigger, merchant)
    send_as = _determine_send_as(trigger, customer)

    return {
        "conversation_id": conversation_id,
        "send_as": send_as,
        "template_name": f"vera_direct_{kind}_v1",
        "template_params": [owner_raw, sname, count_str or signal[:30]],
        "body": body,
        "cta": "binary_yes_no",
        "suppression_key": suppression_key,
        "rationale": _build_rationale(trigger, [trigger], merchant, customer),
    }


# ─── LLM Enhancement (Anthropic primary, OpenAI fallback) ────────────────────

def _enhance_with_llm(rendered: dict, category: dict, merchant: dict,
                      trigger: dict, customer: dict | None) -> dict:
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")

    if not anthropic_key and not openai_key:
        return rendered

    try:
        cat_slug = category.get("slug", "")
        voice = _get_voice(cat_slug)
        owner_name = merchant.get("identity", {}).get("owner_first_name", "there")
        cat_name = category.get("display_name", cat_slug or "business")
        taboo_words = (
            category.get("voice", {}).get("vocab_taboo", []) or voice.get("taboo", [])
        )

        prompt = (
            f"You are 'Vera', Magicpin's B2B AI assistant for local merchants.\n\n"
            f"Rewrite the following message for a {cat_name} owner named {owner_name}.\n\n"
            f"ORIGINAL:\n\"{rendered['body']}\"\n\n"
            f"VOICE RULES for {cat_name} ({voice.get('tone', 'professional')} tone):\n"
            f"{voice.get('instructions', 'Be professional and helpful.')}\n\n"
            f"HARD CONSTRAINTS (violating any = disqualification):\n"
            f"1. Keep ALL numbers, prices (₹), and percentages EXACTLY as in the original.\n"
            f"2. Do NOT add any fact, offer, or claim not in the original.\n"
            f"3. Keep the yes/no question at the end — same CTA intent.\n"
            f"4. Maximum 2-3 sentences.\n"
            f"5. NEVER use these words: "
            f"{', '.join(taboo_words) if taboo_words else 'none'}.\n\n"
            f"Return ONLY the rewritten message. No explanation, no quotes."
        )

        new_body = None

        if anthropic_key:
            import anthropic as _anthropic
            client = _anthropic.Anthropic(
                api_key=anthropic_key,
                timeout=_anthropic.Timeout(8.0),
            )
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            new_body = msg.content[0].text.strip()
        elif openai_key:
            from openai import OpenAI
            client = OpenAI(api_key=openai_key)
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=200,
                timeout=8,
            )
            new_body = resp.choices[0].message.content.strip()

        if new_body:
            enhanced = dict(rendered)
            enhanced["body"] = new_body
            return enhanced

    except Exception as e:
        print(f"[LLM Enhancement Skipped]: {e}")

    return rendered


# ─── Stage 1: Validation ─────────────────────────────────────────────────────

def _has_required_fields(merchant: dict, trigger: dict) -> bool:
    if not merchant or not trigger:
        return False
    if not merchant.get("identity"):
        return False
    if not trigger.get("kind") and not trigger.get("type"):
        return False
    return True


# ─── Stage 2: Reference resolution ───────────────────────────────────────────

def _resolve_references(category: dict, merchant: dict, trigger: dict,
                        customer: dict | None) -> dict | None:
    payload = trigger.get("payload", {})
    enriched = {"trigger_payload": dict(payload)}

    top_item_id = payload.get("top_item_id")
    if top_item_id:
        digest_items = category.get("digest", [])
        match = next((d for d in digest_items if d.get("id") == top_item_id), None)
        if match:
            enriched["digest_item"] = match

    digest_item_id = payload.get("alert_id") or payload.get("digest_item_id")
    if digest_item_id and "digest_item" not in enriched:
        digest_items = category.get("digest", [])
        match = next((d for d in digest_items if d.get("id") == digest_item_id), None)
        if match:
            enriched["digest_item"] = match

    offer_id = payload.get("offer_id")
    if offer_id:
        offers = merchant.get("offers", [])
        match = next((o for o in offers if o.get("id") == offer_id), None)
        if match:
            enriched["offer"] = match

    return enriched


# ─── Stage 3: Selection ───────────────────────────────────────────────────────

def _derive_implicit_triggers(merchant: dict) -> list[dict]:
    implicit = []
    history = merchant.get("conversation_history", [])
    mid = merchant.get("merchant_id", "unknown")

    if history:
        last = history[-1]
        if (last.get("from") == "merchant" and
                last.get("engagement") in ("intent_question", "intent_action")):
            ts = last.get("ts", "")
            try:
                last_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                hours_ago = (datetime.now(last_dt.tzinfo) - last_dt).total_seconds() / 3600
            except Exception:
                hours_ago = 999

            if hours_ago < 48:
                implicit.append({
                    "id": f"implicit_planning_{mid}",
                    "scope": "merchant",
                    "kind": "active_planning_intent",
                    "source": "internal",
                    "merchant_id": mid,
                    "customer_id": None,
                    "payload": {
                        "intent_topic": "from_history",
                        "merchant_last_message": last.get("body", ""),
                        "hours_ago": hours_ago,
                    },
                    "urgency": 4,
                    "suppression_key": f"planning:{mid}:from_history",
                    "expires_at": "2099-12-31T00:00:00Z",
                    "_implicit": True,
                })

    return implicit


def _check_hard_override(candidates: list[dict], merchant: dict) -> dict | None:
    for c in candidates:
        if c.get("kind") == "supply_alert" and c.get("urgency", 0) >= 5:
            return c
        if c.get("kind") == "regulation_change":
            deadline = c.get("payload", {}).get("deadline_iso")
            if deadline and _days_until(deadline) <= 30:
                return c
        if c.get("kind") == "active_planning_intent":
            payload = c.get("payload", {})
            hours_ago = payload.get("hours_ago", payload.get("stale_hours", 0))
            if hours_ago < 48:
                return c
    return None


def _select_winner(candidates: list[dict], merchant: dict,
                   customer: dict | None) -> dict:
    scored = [(c, _score(c, merchant, customer)) for c in candidates]
    scored.sort(key=lambda x: (
        -x[1],
        -(x[0].get("urgency", 0)),
        0 if x[0].get("scope") == "customer" else 1,
        0 if x[0].get("source") == "internal" else 1,
        x[0].get("id", ""),
    ))
    return scored[0][0]


KIND_PRIORS = {
    "active_planning_intent":   2.5,
    "supply_alert":             2.2,
    "regulation_change":        1.8,
    "chronic_refill_due":       1.7,
    "recall_due":               1.6,
    "recall":                   1.6,
    "ipl_match_today":          1.5,
    "perf_dip":                 1.4,
    "review_theme_emerged":     1.3,
    "winback_eligible":         1.2,
    "wedding_package_followup": 1.3,
    "customer_lapsed_hard":     1.3,
    "lapse":                    1.3,
    "research_digest":          1.0,
    "research_spike":           1.2,
    "trial_followup":           1.1,
    "seasonal_perf_dip":        1.0,
    "perf_spike":               0.9,
    "competitor_opened":        1.0,
    "gbp_unverified":           1.1,
    "category_seasonal":        0.9,
    "festival_upcoming":        0.6,
    "festival":                 0.6,
    "milestone_reached":        0.5,
    "cde_opportunity":          0.5,
    "curious_ask_due":          0.4,
    "dormant_with_vera":        0.4,
    "renewal_due":              1.6,
}


def _score(trigger: dict, merchant: dict, customer: dict | None) -> float:
    urgency = trigger.get("urgency", 1) / 5.0
    recency = _recency_score(trigger)
    specificity = _specificity_score(trigger, merchant)
    actionable = 1.0 if _has_yes_no_pivot(trigger) else 0.6
    fit = _merchant_fit(trigger, merchant)
    prior = KIND_PRIORS.get(trigger.get("kind"), 1.0)

    return (urgency * 0.25 + recency * 0.20 + specificity * 0.20 +
            actionable * 0.15 + fit * 0.20) * prior


def _recency_score(trigger: dict) -> float:
    payload = trigger.get("payload", {})
    if trigger.get("kind") == "active_planning_intent":
        hours = payload.get("hours_ago", payload.get("stale_hours", 0))
        return 0.5 ** (hours / 48.0) if hours else 1.0
    return 0.8


def _specificity_score(trigger: dict, merchant: dict) -> float:
    payload = trigger.get("payload", {})
    facts = sum(1 for v in payload.values() if v not in (None, "", []))
    if trigger.get("signal"):
        facts += 1
    perf = merchant.get("performance", {})
    if perf:
        facts += sum(1 for v in perf.values() if v not in (None, "", []))
    return min(1.0, facts / 4.0)


def _has_yes_no_pivot(trigger: dict) -> bool:
    yes_no_kinds = {
        "research_digest", "research_spike", "perf_dip", "supply_alert",
        "recall_due", "recall", "active_planning_intent", "review_theme_emerged",
        "ipl_match_today", "chronic_refill_due", "gbp_unverified",
        "wedding_package_followup", "customer_lapsed_hard", "lapse",
        "trial_followup", "winback_eligible", "regulation_change",
        "competitor_opened", "festival", "festival_upcoming",
    }
    return trigger.get("kind") in yes_no_kinds


def _merchant_fit(trigger: dict, merchant: dict) -> float:
    signals = merchant.get("signals", [])
    kind = trigger.get("kind")

    affinity = {
        "perf_dip":             ["perf_dip_severe", "ctr_below_peer_median"],
        "gbp_unverified":       ["unverified_gbp"],
        "research_digest":      ["high_risk_adult_cohort", "engaged_in_last_24h",
                                 "engaged_in_last_48h"],
        "research_spike":       ["engaged_in_last_24h", "engaged_in_last_48h"],
        "winback_eligible":     ["winback_eligible"],
        "competitor_opened":    ["ctr_below_peer_median", "no_active_offers"],
        "review_theme_emerged": ["high_engagement"],
    }
    matches = affinity.get(kind, [])
    score = 0.5
    for sig in signals:
        if sig in matches or any(m in sig for m in matches):
            score = min(1.0, score + 0.25)
    return score


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _days_until(iso_str: str) -> int:
    try:
        target = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return max(0, (target - datetime.now(target.tzinfo)).days)
    except Exception:
        return 999


def _make_conversation_id(merchant: dict, trigger: dict,
                          customer: dict | None) -> str:
    mid = merchant.get("merchant_id", "unknown")
    kind = trigger.get("kind", "unknown")
    if customer:
        name = customer.get("identity", {}).get("name", "cust").lower().split()[0]
        topic = {
            "recall_due": "recall", "recall": "recall",
            "chronic_refill_due": "refill",
            "wedding_package_followup": "bridal",
            "trial_followup": "trial",
            "customer_lapsed_hard": "winback", "lapse": "winback",
        }.get(kind, kind[:8])
        ym = datetime.utcnow().strftime("%Y_%m")
        return f"conv_{name}_{topic}_{ym}"

    short_mid = mid.split("_")[1] if "_" in mid else mid[:6]
    week = datetime.utcnow().strftime("W%V")
    return f"conv_m_{short_mid}_{kind}_{week}"


def _make_suppression_key(trigger: dict, merchant: dict) -> str:
    kind = trigger.get("kind", "unknown")
    mid = merchant.get("merchant_id", "unknown")
    payload = trigger.get("payload", {})

    essence_keys = ["category", "metric", "festival", "molecule",
                    "deadline_iso", "service_due", "intent_topic"]
    essence = {k: payload.get(k) for k in essence_keys if payload.get(k)}
    if not essence:
        essence = payload

    h = hashlib.sha256(
        f"{kind}:{mid}:{sorted(essence.items())}".encode()
    ).hexdigest()[:8]
    week = datetime.utcnow().strftime("%G-W%V")
    return f"{kind}:{mid}:{h}:{week}"


def _determine_send_as(trigger: dict, customer: dict | None) -> str:
    if trigger.get("scope") == "customer" or customer is not None:
        return "merchant_on_behalf"
    return "vera"


def _build_rationale(winner: dict, candidates: list[dict],
                     merchant: dict, customer: dict | None) -> str:
    kind = winner.get("kind", "?")
    signal = winner.get("signal", "")
    n_candidates = len(candidates)

    # Count from signal
    count_suffix = ""
    if signal:
        m = re.search(r'(\d+)', signal)
        if m:
            noun = "searches" if "search" in signal.lower() else "signals"
            count_suffix = f" ({m.group(1)} {noun})"

    # Other candidates
    other_kinds = [c.get("kind") for c in candidates
                   if c.get("kind") and c.get("kind") != kind]
    if other_kinds:
        other_str = f" over {', '.join(other_kinds[:2])}"
    else:
        other_str = f" ({n_candidates} candidate{'s' if n_candidates != 1 else ''})"

    # Merchant anchor
    anchor_str = ""
    offers = merchant.get("offers", [])
    perf = merchant.get("performance", {})
    if offers:
        o = offers[0]
        price = o.get("price")
        name = o.get("name", "offer")
        anchor_str = f"Anchored on ₹{price} {name} offer. " if price else f"Anchored on {name} offer. "
    elif perf.get("weekly_footfall"):
        anchor_str = f"Anchored on {perf['weekly_footfall']} weekly footfall. "
    elif perf.get("avg_rating"):
        anchor_str = f"Anchored on {perf['avg_rating']}★ avg rating. "

    # Timing
    TIMING = {
        "research_spike":        "active search window = highest intent moment",
        "research_digest":       "digest fresh = clinical anchor in peak recall window",
        "perf_dip":              "performance gap = immediate recovery window",
        "perf_spike":            "momentum peak = amplify with social proof now",
        "supply_alert":          "patient safety override = zero delay acceptable",
        "recall":                "recall window open = peak re-engagement moment",
        "recall_due":            "recall window open = peak re-engagement moment",
        "lapse":                 "winback window narrowing = 30% drop in conversion after 90d",
        "customer_lapsed_hard":  "winback window narrowing = 30% drop in conversion after 90d",
        "festival":              "festival countdown = optimal advance-booking window",
        "festival_upcoming":     "festival countdown = optimal advance-booking window",
        "ipl_match_today":       "match-day window perishable = 6-hour action window",
        "active_planning_intent":"merchant-initiated thread = highest engagement moment",
        "gbp_unverified":        "unverified GBP = highest-ROI free action available now",
        "winback_eligible":      "cohort identified = optimal reactivation window",
        "regulation_change":     "compliance deadline approaching = urgency is real",
        "chronic_refill_due":    "stock expiry date = zero-delay delivery window",
        "review_theme_emerged":  "review trend identified early = fix before it compounds",
        "dormant_with_vera":     "dormancy window = re-engage before permanent churn",
        "competitor_opened":     "competitor entered area = defend position now",
        "renewal_due":           "renewal window = retention cheaper than re-acquisition",
    }
    timing = TIMING.get(kind, "signal identified = timely action recommended")

    return (
        f"Selected {kind}{count_suffix}{other_str}. "
        f"{anchor_str}"
        f"Timing: {timing}."
    ).strip()


__all__ = ["compose", "CATEGORY_VOICE"]
