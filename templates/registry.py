"""
templates/registry.py — Vera's category × trigger-kind template matrix.

Each template is a render function that takes (category, merchant, trigger,
customer, enriched) and returns a fully-composed message dict, OR None if
required slots can't be grounded.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable, Optional


# ─── Category voice mini-map (for template-level vocabulary) ─────────────────

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


# ─── Public API ─────────────────────────────────────────────────────────────

TemplateFn = Callable[[dict, dict, dict, Optional[dict], dict], Optional[dict]]

# Kind aliases — judge sends these short names, map to canonical kinds
_KIND_ALIASES = {
    "recall":    "recall_due",
    "lapse":     "customer_lapsed_hard",
    "festival":  "festival_upcoming",
    "perf_dip":  "perf_dip",   # already in registry as _all_
}


def get_template(kind: str, category_slug: str) -> Optional[TemplateFn]:
    """Return the template function for (kind, category) or None.

    Tries in order:
      1. Exact (kind, category_slug)
      2. Exact (kind, "_all_")
      3. Alias resolution then same two lookups
      4. Plural/singular normalization then same two lookups
    """
    if not kind:
        return None

    # Direct lookups
    fn = _REGISTRY.get((kind, category_slug)) or _REGISTRY.get((kind, "_all_"))
    if fn:
        return fn

    # Alias resolution
    canonical = _KIND_ALIASES.get(kind)
    if canonical and canonical != kind:
        fn = _REGISTRY.get((canonical, category_slug)) or _REGISTRY.get((canonical, "_all_"))
        if fn:
            return fn

    # Normalize plural ↔ singular for category slug
    if category_slug:
        alt_slug = (category_slug[:-1] if category_slug.endswith("s")
                    else category_slug + "s")
        fn = _REGISTRY.get((kind, alt_slug)) or _REGISTRY.get((canonical or kind, alt_slug))
        if fn:
            return fn

    return None


def render_template(template: TemplateFn, category: dict, merchant: dict,
                    trigger: dict, customer: Optional[dict],
                    enriched: dict) -> Optional[dict]:
    """Invoke the template, returning {body, cta, template_name, template_params}."""
    try:
        return template(category, merchant, trigger, customer, enriched)
    except (KeyError, IndexError, TypeError, AttributeError):
        return None


# ─── Helpers used across templates ──────────────────────────────────────────

def _owner_name(merchant: dict, prefix: str = "") -> str:
    name = merchant.get("identity", {}).get("owner_first_name", "")
    if not name:
        return prefix.strip() or "there"
    if prefix:
        return f"{prefix} {name}"
    return name


def _customer_name(customer: dict) -> str:
    raw = customer.get("identity", {}).get("name", "")
    return raw.split("(")[0].strip()


def _get_offer(merchant: dict, predicate: Callable[[dict], bool] = None,
               status: str = "active") -> Optional[dict]:
    """Get first offer matching predicate. If status-filtered finds nothing, try any offer."""
    candidates = []
    for o in merchant.get("offers", []):
        if o.get("status") is not None and o.get("status") != status:
            continue
        if predicate is None or predicate(o):
            candidates.append(o)
    if candidates:
        return candidates[0]
    # Fallback: any offer regardless of status
    for o in merchant.get("offers", []):
        if predicate is None or predicate(o):
            return o
    return None


def _get_any_offer(merchant: dict) -> Optional[dict]:
    """Get first available offer regardless of status."""
    offers = merchant.get("offers", [])
    return offers[0] if offers else None


def _category_offer(category: dict, offer_id: str) -> Optional[dict]:
    for o in category.get("offer_catalog", []):
        if o.get("id") == offer_id:
            return o
    return None


def _format_pct(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return f"{abs(int(value * 100))}%" if abs(value) < 1 else f"{int(value)}%"
    return str(value)


def _safe(value, default=""):
    return value if value not in (None, "", []) else default


# ─── Template implementations ───────────────────────────────────────────────

# == DENTISTS ================================================================

def _t_research_digest__dentists(category, merchant, trigger, customer, enriched):
    digest = enriched.get("digest_item")
    if not digest:
        return None
    cohort_count = (merchant.get("customer_aggregate", {})
                    .get("high_risk_adult_count"))
    if not cohort_count:
        return None

    owner = _owner_name(merchant, "Dr.")
    source = _safe(digest.get("source"), "JIDA")
    trial_n = digest.get("trial_n")
    summary = _safe(digest.get("summary"), "")
    pct = _extract_first_pct(summary) or "significant reduction in"

    body = (
        f"{owner}, {source} just landed and one item is relevant to your "
        f"high-risk adult cohort. The {trial_n}-patient trial shows "
        f"3-month fluoride recall cuts caries recurrence {pct} better than "
        f"6-month. You have {cohort_count} flagged in your charts. "
        f"Want me to draft a recall WhatsApp + pull the patient list?"
    )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "template_name": "vera_research_digest_dentists_v1",
        "template_params": [owner, source, str(trial_n),
                            str(cohort_count), pct],
    }


def _t_recall_due__dentists(category, merchant, trigger, customer, enriched):
    if not customer:
        return None
    name = _customer_name(customer)
    payload = trigger.get("payload", {})
    slots = payload.get("available_slots", [])
    if len(slots) < 2:
        return None

    lang = customer.get("identity", {}).get("language_pref", "en")
    is_hi_en = "hi" in lang

    offer = _get_offer(merchant,
                       lambda o: "cleaning" in o.get("title", "").lower() or
                                 "check" in o.get("name", "").lower())
    price_str = ""
    if offer:
        price = offer.get("price", offer.get("price", ""))
        if price:
            price_str = f"₹{price} cleaning"

    months = _months_since(customer.get("relationship", {}).get("last_visit"))
    s1, s2 = slots[0]["label"], slots[1]["label"]

    if is_hi_en:
        body = (
            f"Hi {name}, Dr. {merchant['identity'].get('owner_first_name', '')}'s "
            f"clinic here 🦷 It's been {months} months since your last visit. "
            f"6-month recall due hai. Aapke liye 2 slots ready: {s1} ya {s2}. "
        )
        if price_str:
            body += f"{price_str} + complimentary fluoride. "
        body += "Reply 1 for first, 2 for second, ya different time bata dijiye."
    else:
        body = (
            f"Hi {name}, Dr. {merchant['identity'].get('owner_first_name', '')}'s "
            f"clinic here. It's been {months} months since your last cleaning, "
            f"so your 6-month recall is now due. Two slots open: {s1} or {s2}. "
        )
        if price_str:
            body += f"{price_str} + complimentary fluoride. "
        body += "Reply 1 for the first, 2 for the second, or share a different time."

    return {
        "body": body,
        "cta": "multi_choice",
        "template_name": "merchant_recall_dentists_v1",
        "template_params": [name, str(months), s1, s2, price_str],
    }


def _t_competitor_opened__dentists(category, merchant, trigger, customer, enriched):
    payload = trigger.get("payload", {})
    competitor = payload.get("competitor_name")
    distance = payload.get("distance_km")
    their_offer = payload.get("their_offer", "")
    if not competitor:
        return None

    owner = _owner_name(merchant, "Dr.")
    locality = merchant.get("identity", {}).get("locality", "your area")

    own_offer = _get_offer(merchant,
                           lambda o: "cleaning" in o.get("title", "").lower() or
                                     "check" in o.get("name", "").lower())
    counter = ""
    if own_offer:
        counter = f"You're at {own_offer.get('title', own_offer.get('name', ''))} — close on price"

    body = (
        f"{owner}, heads up — {competitor} opened {distance}km away in "
        f"{locality} with {their_offer}. {counter}. Two ways to defend: "
        f"add a free fluoride add-on (free, ~₹0 cost to you), or push your "
        f"deep-cleaning bundle. Want me to draft a Google post emphasising "
        f"your verified-since-{merchant['identity'].get('established_year', 'date')} "
        f"track record?"
    )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "template_name": "vera_competitor_dentists_v1",
        "template_params": [owner, competitor, str(distance), their_offer, locality],
    }


# == RESEARCH SPIKE (all categories) =========================================

def _t_research_spike__all(category, merchant, trigger, customer, enriched):
    """Outbound demand spike — count from signal, map to best offer, single yes/no."""
    signal = trigger.get("signal", "")
    payload = trigger.get("payload", {})

    # Extract search count: signal text first, then payload fields
    count = None
    if signal:
        m = re.search(r'(\d+)', signal)
        if m:
            count = m.group(1)
    if not count:
        raw = payload.get("search_count") or payload.get("count") or payload.get("volume")
        if raw:
            count = str(raw)

    if not count:
        return None

    identity = merchant.get("identity", {})
    locality = (identity.get("locality") or
                (identity.get("location", "").split(",")[0].strip()
                 if identity.get("location") else "") or
                identity.get("city", ""))

    cat_slug = category.get("slug", "")
    voice = _cat_voice(cat_slug)
    patient_word = voice["patient_word"]
    service_word = voice["service_word"]

    locality_str = locality or "your locality"

    # Best offer — prefer one with "active" status or any if status unset
    offer = _get_any_offer(merchant)

    if offer:
        offer_name = offer.get("name", "")
        offer_price = offer.get("price", "")
        body = (
            f"{count} {patient_word} in {locality_str} are searching for "
            f"a {offer_name}. Should I reach them with your "
            f"₹{offer_price} {service_word} offer?"
        )
        params = [count, patient_word, locality_str, offer_name, str(offer_price)]
    else:
        body = (
            f"{count} {patient_word} in {locality_str} are actively searching for "
            f"services like yours right now. Should I help you reach them?"
        )
        params = [count, patient_word, locality_str]

    return {
        "body": body,
        "cta": "binary_yes_no",
        "template_name": "vera_research_spike_v1",
        "template_params": params,
    }


# == SALONS ==================================================================

def _t_wedding_package_followup__salons(category, merchant, trigger, customer, enriched):
    if not customer:
        return None
    name = _customer_name(customer)
    payload = trigger.get("payload", {})
    days_to_wedding = payload.get("days_to_wedding")
    if not days_to_wedding:
        return None

    owner = merchant.get("identity", {}).get("owner_first_name", "")
    sname = merchant.get("identity", {}).get("name", "the salon")
    locality = merchant.get("identity", {}).get("locality", "")

    body = (
        f"Hi {name} 💍 {owner} from {sname} {locality}. {days_to_wedding} "
        f"days to your wedding — perfect window to start the 30-day skin-prep "
        f"program before serious bridal bookings stack up. ₹2,499 covers "
        f"4 sessions + a take-home kit. Want me to block your preferred "
        f"Saturday slot for the first session next week?"
    )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "template_name": "merchant_bridal_followup_salons_v1",
        "template_params": [name, owner, sname, str(days_to_wedding)],
    }


def _t_curious_ask_due__salons(category, merchant, trigger, customer, enriched):
    owner = merchant.get("identity", {}).get("owner_first_name", "")
    sname = merchant.get("identity", {}).get("name", "the salon")
    locality = merchant.get("identity", {}).get("locality", "")
    if not owner:
        return None

    views = merchant.get("performance", {}).get("views", 0)
    loc_str = f" in {locality}" if locality else ""

    body = (
        f"Hi {owner}! Quick check — what service has been most asked about "
        f"this week at {sname}{loc_str}? With {views} profile views this month, "
        f"I'll turn the answer into a Google post plus a 4-line WhatsApp reply "
        f"you can reuse when clients ask about pricing. Should take 5 minutes."
    )
    return {
        "body": body,
        "cta": "open_ended",
        "template_name": "vera_curious_ask_salons_v1",
        "template_params": [owner, sname, locality],
    }


def _t_research_digest__salons(category, merchant, trigger, customer, enriched):
    digest = enriched.get("digest_item")
    if not digest:
        return None
    owner = merchant.get("identity", {}).get("owner_first_name", "")
    title = digest.get("title", "")
    actionable = digest.get("actionable", "")
    if not actionable:
        return None

    body = (
        f"Hi {owner} — quick spot from this week's industry feed: {title}. "
        f"Practical angle for {merchant.get('identity', {}).get('name', 'your salon')}: "
        f"{actionable}. Want me to draft a quick Insta carousel framing this "
        f"as your USP versus walk-in shops?"
    )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "template_name": "vera_research_digest_salons_v1",
        "template_params": [owner, title, actionable],
    }


# == RESTAURANTS =============================================================

def _t_ipl_match_today__restaurants(category, merchant, trigger, customer, enriched):
    payload = trigger.get("payload", {})
    match = payload.get("match", "")
    venue = payload.get("venue", "")
    match_time = payload.get("match_time_iso", "")
    is_weeknight = payload.get("is_weeknight", False)
    if not match:
        return None

    owner = merchant.get("identity", {}).get("owner_first_name", "")
    sname = merchant.get("identity", {}).get("name", "")
    locality = merchant.get("identity", {}).get("locality", "")
    if not owner:
        return None
    time_str = _format_match_time(match_time)

    bogo = _get_offer(merchant,
                      lambda o: "bogo" in o.get("title", "").lower() or
                                "buy 1" in o.get("title", "").lower())

    loc_str = f" in {locality}" if locality else ""
    if not is_weeknight:
        body = (
            f"Quick heads-up {owner} — {match} at {venue} tonight, {time_str}. "
            f"Important: Saturday IPL matches typically shift -12% restaurant "
            f"covers (people watch at home). For {sname}{loc_str}, skip the match-night promo today; "
            f"instead push your "
        )
        if bogo:
            body += f"{bogo.get('title', bogo.get('name', 'BOGO offer'))} (already active) "
        else:
            body += "BOGO pizza "
        body += (f"as a delivery-only Saturday special. Want me to draft the "
                 f"Swiggy banner + an Insta story? Live in 10 min.")
    else:
        body = (
            f"{owner}, {match} at {venue} tonight, {time_str} — weeknight IPL "
            f"is your strongest bracket (+18% covers vs Saturday avg). For {sname}{loc_str}, want "
            f"me to push your match-night combo on WhatsApp + queue an Insta "
            f"story? Live in 10 min."
        )

    return {
        "body": body,
        "cta": "binary_yes_no",
        "template_name": "vera_ipl_match_restaurants_v1",
        "template_params": [owner, match, venue, time_str,
                            "weeknight" if is_weeknight else "saturday", sname, locality],
    }


def _t_active_planning_intent__restaurants(category, merchant, trigger, customer, enriched):
    payload = trigger.get("payload", {})
    topic = payload.get("intent_topic", "")
    last_msg = payload.get("merchant_last_message", "")
    owner = merchant.get("identity", {}).get("owner_first_name", "")
    if not owner or not topic:
        return None

    locality = merchant.get("identity", {}).get("locality", "")
    sname = merchant.get("identity", {}).get("name", "")

    if "corp" in topic.lower() or "thali" in topic.lower():
        thali_offer = _get_offer(merchant,
                                 lambda o: "thali" in o.get("title", "").lower() or
                                           "thali" in o.get("name", "").lower())
        retail_price = 149
        if thali_offer:
            for token in (thali_offer.get("title", "") or thali_offer.get("name", "")).split():
                if token.startswith("₹"):
                    try:
                        retail_price = int(token[1:].replace(",", ""))
                    except Exception:
                        pass

        body = (
            f"{owner}, here's a starter you can edit:\n\n"
            f"{sname} Corporate Thali — for offices in {locality}\n"
            f"• 10 thalis @ ₹{retail_price - 24} each (₹24 off retail) + free delivery\n"
            f"• 25 thalis @ ₹{retail_price - 34} each + 2 free filter coffees\n"
            f"• 50+: ₹{retail_price - 44} each + 1 free dosa platter\n"
            f"• WhatsApp the day before by 5pm; deliver 12:30-1pm\n\n"
            f"Want me to draft a 3-line WhatsApp message to send their "
            f"facilities managers?"
        )
        return {
            "body": body,
            "cta": "binary_yes_no",
            "template_name": "vera_planning_corp_thali_v1",
            "template_params": [owner, sname, locality, str(retail_price)],
        }

    body = (
        f"{owner}, picking up on '{topic}'. To make this concrete, I need "
        f"one input: what's your current avg order value at {sname}? "
        f"I'll structure a proposal around that anchor."
    )
    return {
        "body": body,
        "cta": "open_ended",
        "template_name": "vera_planning_generic_v1",
        "template_params": [owner, topic, sname],
    }


def _t_review_theme_emerged__restaurants(category, merchant, trigger, customer, enriched):
    payload = trigger.get("payload", {})
    theme = payload.get("theme", "")
    occurrences = payload.get("occurrences_30d", 0)
    quote = payload.get("common_quote", "")
    owner = merchant.get("identity", {}).get("owner_first_name", "")
    if not theme or not owner:
        return None

    if "delivery_late" in theme:
        body = (
            f"{owner}, flagging early — '{theme.replace('_', ' ')}' showed "
            f"up in {occurrences} reviews this month (rising trend). One "
            f"customer said: \"{quote}\". Two operational fixes that work: "
            f"cap concurrent delivery orders during peak, or batch-route "
            f"orders within 1km. Want me to draft a Swiggy support ticket "
            f"to investigate driver assignment patterns?"
        )
    else:
        body = (
            f"{owner}, theme to flag: '{theme.replace('_', ' ')}' came up "
            f"{occurrences} times in the last 30 days of reviews. Quote: "
            f"\"{quote}\". Want me to draft a polite WhatsApp to the next "
            f"5 reviewers asking for specifics? Helps surface the actual "
            f"operational issue."
        )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "template_name": "vera_review_theme_restaurants_v1",
        "template_params": [owner, theme, str(occurrences), quote],
    }


# == GYMS ====================================================================

def _t_seasonal_perf_dip__gyms(category, merchant, trigger, customer, enriched):
    payload = trigger.get("payload", {})
    metric = payload.get("metric", "views")
    delta_pct = payload.get("delta_pct", 0)
    delta_str = _format_pct(delta_pct)
    if not delta_str:
        return None

    owner = merchant.get("identity", {}).get("owner_first_name", "")
    if not owner:
        return None

    members = (merchant.get("customer_aggregate", {})
               .get("total_active_members"))
    if not members:
        return None

    body = (
        f"{owner}, {metric} are off {delta_str} this week — wanted to flag "
        f"this is the textbook April-June acquisition lull (every metro gym "
        f"runs -25 to -35% in this stretch, not a problem on your end). "
        f"My recommendation: hold ad spend back this month and redeploy in "
        f"Sept-Oct when conversion roughly doubles. Best use of this window "
        f"is locking in retention with your {members} active members. Want "
        f"me to put together a 'summer streak challenge' you can launch this "
        f"week to keep them engaged through the dip?"
    )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "template_name": "vera_seasonal_dip_gyms_v1",
        "template_params": [owner, metric, delta_str, str(members)],
    }


def _t_customer_lapsed_hard__gyms(category, merchant, trigger, customer, enriched):
    if not customer:
        return None
    name = _customer_name(customer)
    payload = trigger.get("payload", {})
    days = payload.get("days_since_last_visit", 0)
    focus = payload.get("previous_focus", "fitness")

    weeks = days // 7
    owner = merchant.get("identity", {}).get("owner_first_name", "")
    sname = merchant.get("identity", {}).get("name", "the gym")

    class_pitch = "an evening HIIT class (45 min, 6:30pm Tue/Thu)"
    if "weight_loss" in focus:
        class_pitch = ("a Tue/Thu evening HIIT class (45 min, 6:30pm) — "
                       "fits weight-loss goals well")
    elif "strength" in focus:
        class_pitch = "a strength block on Mon/Wed/Fri (60 min, 7am or 7pm)"

    body = (
        f"Hi {name} 👋 {owner} from {sname}. About {weeks} weeks since we "
        f"last saw you — happens to most folks, no judgment from our end. "
        f"Quick update: we just rolled out {class_pitch}. Want me to hold "
        f"a free trial spot for you next Tuesday? Reply YES — zero "
        f"commitment, no auto-charge."
    )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "template_name": "merchant_winback_gyms_v1",
        "template_params": [name, owner, sname, str(weeks), focus],
    }


def _t_active_planning_intent__gyms(category, merchant, trigger, customer, enriched):
    payload = trigger.get("payload", {})
    topic = payload.get("intent_topic", "")
    owner = merchant.get("identity", {}).get("owner_first_name", "")
    if not topic or not owner:
        return None

    if "kids_yoga" in topic.lower():
        sname = merchant.get("identity", {}).get("name", "your gym")
        locality = merchant.get("identity", {}).get("locality", "")
        loc_str = f" in {locality}" if locality else ""
        body = (
            f"Great timing {owner} — kids yoga summer camps peak in May. "
            f"Suggested structure for {sname}{loc_str}: 4-week program, 3 classes/week, ages "
            f"7-12, ₹2,499 per child (early-bird ₹1,999 if booked by 10 "
            f"May). Want me to draft the GBP post + an Instagram carousel "
            f"with the 3 main poses? Live in 15 minutes."
        )
        return {
            "body": body,
            "cta": "binary_yes_no",
            "template_name": "vera_planning_kids_yoga_v1",
            "template_params": [owner, topic, sname, locality],
        }

    body = (
        f"{owner}, picking up on '{topic}'. To structure this, what's your "
        f"target age group + price band? I'll come back with a draft program "
        f"+ marketing copy."
    )
    return {
        "body": body,
        "cta": "open_ended",
        "template_name": "vera_planning_generic_gyms_v1",
        "template_params": [owner, topic],
    }


# == PHARMACIES ==============================================================

def _t_supply_alert__pharmacies(category, merchant, trigger, customer, enriched):
    payload = trigger.get("payload", {})
    molecule = payload.get("molecule", "")
    batches = payload.get("affected_batches", [])
    manufacturer = payload.get("manufacturer", "")
    if not molecule or not batches:
        return None

    owner = merchant.get("identity", {}).get("owner_first_name", "")
    if not owner:
        return None

    chronic_count = (merchant.get("customer_aggregate", {})
                     .get("chronic_rx_count", 0))
    affected_estimate = max(1, chronic_count // 11)
    batches_str = ", ".join(batches[:2])

    body = (
        f"{owner}, urgent: voluntary recall on {len(batches)} {molecule} "
        f"batches ({batches_str}) by {manufacturer} — sub-potency, no "
        f"safety risk, but patients should be informed for replacement. "
        f"Pulled your repeat-Rx list: ~{affected_estimate} of your "
        f"{chronic_count} chronic-Rx patients were dispensed these batches "
        f"in the last 90 days. Want me to draft their WhatsApp note + the "
        f"replacement-pickup workflow?"
    )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "template_name": "vera_supply_alert_pharmacies_v1",
        "template_params": [owner, molecule, batches_str, manufacturer,
                            str(affected_estimate), str(chronic_count)],
    }


def _t_chronic_refill_due__pharmacies(category, merchant, trigger, customer, enriched):
    if not customer:
        return None
    payload = trigger.get("payload", {})
    molecules = payload.get("molecule_list", [])
    runs_out = payload.get("stock_runs_out_iso", "")
    if not molecules or not runs_out:
        return None

    name = customer.get("identity", {}).get("name", "")
    is_senior = customer.get("identity", {}).get("senior_citizen")
    is_via_son = "via_son" in customer.get("preferences", {}).get("channel", "")
    lang = customer.get("identity", {}).get("language_pref", "en")
    is_hi = "hi" in lang and "en" not in lang

    sname = merchant.get("identity", {}).get("name", "")
    locality = merchant.get("identity", {}).get("locality", "")

    runs_out_str = _format_date(runs_out)
    molecules_str = ", ".join(molecules)

    senior_offer = _get_offer(merchant,
                              lambda o: "senior" in o.get("title", "").lower())
    delivery_offer = _get_offer(merchant,
                                lambda o: "delivery" in o.get("title", "").lower())

    if is_via_son or is_senior:
        prefix = "Namaste — "
        if is_hi:
            body = (
                f"{prefix}{sname} {locality} yahan. {name} ji ki monthly "
                f"medicines ({molecules_str}) {runs_out_str} ko khatam hongi. "
                f"Same dose, same brand pack ready hai. "
            )
            if senior_offer:
                body += "Senior 15% discount applied. "
            if delivery_offer:
                body += "Free home delivery to saved address by 5pm tomorrow. "
            body += "Reply CONFIRM to dispatch, or call if any change in dosage."
        else:
            body = (
                f"{prefix}{sname} {locality} here. {name}'s 3 monthly "
                f"medicines ({molecules_str}) run out on {runs_out_str}. "
                f"Same dose, same brand ready to dispatch. "
            )
            if senior_offer:
                body += "Senior 15% discount applied. "
            if delivery_offer:
                body += "Free home delivery to saved address by 5pm tomorrow. "
            body += "Reply CONFIRM to dispatch, or call if any dosage change."
    else:
        body = (
            f"Hi {name}, {sname} here. Your monthly Rx ({molecules_str}) "
            f"runs out {runs_out_str}. Same brand ready, "
        )
        if delivery_offer:
            body += "free delivery to saved address. "
        body += "Reply CONFIRM to dispatch tomorrow morning."

    return {
        "body": body,
        "cta": "binary_yes_no",
        "template_name": "merchant_refill_pharmacies_v1",
        "template_params": [name, sname, locality, molecules_str, runs_out_str],
    }


def _t_gbp_unverified__pharmacies(category, merchant, trigger, customer, enriched):
    owner = merchant.get("identity", {}).get("owner_first_name", "")
    sname = merchant.get("identity", {}).get("name", "")
    locality = merchant.get("identity", {}).get("locality", "")
    if not owner:
        return None

    views = merchant.get("performance", {}).get("views", 0)
    calls = merchant.get("performance", {}).get("calls", 0)

    body = (
        f"{owner} — {sname} isn't verified on Google yet. In {locality}, "
        f"verified pharmacies in your peer set get roughly 30% more calls "
        f"than unverified ones. Right now you're at {views} views and {calls} calls/month — "
        f"verification is one phone call from Google "
        f"and takes ~5 days end-to-end. Should I start the flow?"
    )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "template_name": "vera_gbp_unverified_pharmacies_v1",
        "template_params": [owner, sname, locality, str(views), str(calls)],
    }


# == ALL CATEGORIES (generic) ================================================

def _t_perf_dip__all(category, merchant, trigger, customer, enriched):
    payload = trigger.get("payload", {})
    metric = payload.get("metric", "calls")
    delta = payload.get("delta_pct", 0)
    delta_str = _format_pct(delta)
    baseline = payload.get("vs_baseline", 0)

    owner = merchant.get("identity", {}).get("owner_first_name", "")
    sname = merchant.get("identity", {}).get("name", "")
    locality = merchant.get("identity", {}).get("locality", "")
    if not owner:
        return None

    cur_value = merchant.get("performance", {}).get(metric, 0)
    signals = merchant.get("signals", [])
    fix = ""
    if "unverified_gbp" in signals:
        fix = ("Your GBP isn't verified — verifying typically lifts calls "
               "25-30% in 5 days")
    elif "no_active_offers" in signals:
        fix = ("You don't have an active offer — even a basic ₹99 entry "
               "offer typically lifts CTR within a week")
    elif "stale_posts" in str(signals):
        fix = ("Your last Google post is 22+ days old — fresh posts lift "
               "impressions ~15% within 7 days")
    else:
        fix = ("Three things historically work: refresh your top photos, "
               "publish a Google post, and add a time-bounded offer")

    loc_str = f" in {locality}" if locality else ""
    views = merchant.get("performance", {}).get("views", 0)
    if delta == 0 or delta_str == "0%":
        body = (
            f"{owner}, {metric} have stalled at {cur_value} this week "
            f"for {sname}{loc_str} (baseline was {baseline}, {views} profile views this month). "
            f"{fix}. Want me to start "
            f"with the highest-leverage fix today?"
        )
    else:
        body = (
            f"{owner}, {metric} dropped {delta_str} week-on-week "
            f"(from {baseline} to {cur_value}) for {sname}{loc_str}. {fix}. Want me to start "
            f"with the highest-leverage fix today?"
        )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "template_name": "vera_perf_dip_v1",
        "template_params": [owner, metric, delta_str, str(baseline),
                            str(cur_value), fix[:60]],
    }


def _t_renewal_due__all(category, merchant, trigger, customer, enriched):
    payload = trigger.get("payload", {})
    days = payload.get("days_remaining", 0)
    plan = payload.get("plan", "Pro")
    amount = payload.get("renewal_amount", 0)

    owner = merchant.get("identity", {}).get("owner_first_name", "")
    if not owner:
        return None

    perf = merchant.get("performance", {})
    delta_calls = perf.get("delta_7d", {}).get("calls_pct", 0)

    body = (
        f"{owner}, your {plan} subscription renews in {days} days "
        f"(₹{amount}). Quick context: "
    )
    if delta_calls < -0.20:
        body += (f"calls are down {abs(int(delta_calls*100))}% this week, "
                 f"so Pro features (verified-merchant boost, GBP optimizer) "
                 f"will pay for themselves quickly. ")
    else:
        body += "no urgency from me, just a heads-up. "
    body += "Want me to send the renewal link, or hold off?"

    return {
        "body": body,
        "cta": "binary_yes_no",
        "template_name": "vera_renewal_due_v1",
        "template_params": [owner, plan, str(days), str(amount)],
    }


def _t_active_planning_intent__all(category, merchant, trigger, customer, enriched):
    payload = trigger.get("payload", {})
    topic = payload.get("intent_topic", "")
    owner = merchant.get("identity", {}).get("owner_first_name", "")
    if not topic or not owner:
        return None
    body = (
        f"{owner}, continuing on '{topic}'. To draft something concrete, "
        f"I need one piece of info from you: which segment is this aimed "
        f"at? Once I know, I'll come back with a complete proposal in "
        f"~10 minutes."
    )
    return {
        "body": body,
        "cta": "open_ended",
        "template_name": "vera_planning_generic_v1",
        "template_params": [owner, topic],
    }


def _t_regulation_change__all(category, merchant, trigger, customer, enriched):
    payload = trigger.get("payload", {})
    rule_id = payload.get("rule_id", "")
    deadline = payload.get("deadline_iso", "")
    summary = payload.get("summary", "")
    authority = payload.get("authority", "")
    if not (rule_id and deadline and summary):
        return None

    owner = merchant.get("identity", {}).get("owner_first_name", "")
    if not owner:
        return None

    try:
        from datetime import datetime as dt
        dl = dt.fromisoformat(deadline.replace("Z", "+00:00"))
        days_left = max(0, (dl - dt.now(dl.tzinfo)).days)
    except Exception:
        days_left = 30
        dl = None

    deadline_str = dl.strftime("%-d %b %Y") if dl else deadline
    body = (
        f"{owner}, compliance flag — {authority or 'regulator'} circular "
        f"{rule_id}: {summary}. Deadline {deadline_str} "
        f"({days_left} days left). I can pull a 1-page checklist of what "
        f"to verify in your records before the date — should I send it?"
    )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "template_name": "vera_regulation_change_v1",
        "template_params": [owner, rule_id, summary[:80], str(days_left), authority],
    }


def _t_festival_upcoming__all(category, merchant, trigger, customer, enriched):
    payload = trigger.get("payload", {})
    festival = payload.get("festival", "")
    days_to = payload.get("days_until", payload.get("days_to_festival", 0))
    if not festival:
        return None

    owner = merchant.get("identity", {}).get("owner_first_name", "")
    sname = merchant.get("identity", {}).get("name", "")
    locality = merchant.get("identity", {}).get("locality", "")
    if not owner:
        return None

    views = merchant.get("performance", {}).get("views", 0)
    cat_slug = category.get("slug", "")
    angle_by_category = {
        "salons":      f"booking pre-{festival} hair + skin appointments now — salons in {locality} typically see 35% more bookings 2 weeks before",
        "salon":       f"booking pre-{festival} hair + skin appointments now — salons in {locality} typically see 35% more bookings 2 weeks before",
        "restaurants": f"a sweets-and-thali combo for {festival} family orders — restaurants in {locality} see 40% more delivery orders during festival week",
        "restaurant":  f"a sweets-and-thali combo for {festival} family orders — restaurants in {locality} see 40% more delivery orders during festival week",
        "dentists":    f"pre-{festival} cleaning slots so smiles look sharp in photos — 3-week window is ideal",
        "dentist":     f"pre-{festival} cleaning slots so smiles look sharp in photos — 3-week window is ideal",
        "gyms":        f"a 7-day '{festival} prep' challenge for members — gyms in {locality} see 20% more sign-ups with festival hooks",
        "gym":         f"a 7-day '{festival} prep' challenge for members — gyms in {locality} see 20% more sign-ups with festival hooks",
        "pharmacies":  f"stock check on diabetic-friendly sweets and antacids ahead of {festival} — pharmacies in {locality} typically see 25% uptick",
        "pharmacy":    f"stock check on diabetic-friendly sweets and antacids ahead of {festival} — pharmacies in {locality} typically see 25% uptick",
    }
    angle = angle_by_category.get(cat_slug, f"a {festival}-themed promotion for {locality}")

    body = (
        f"{owner}, flagging {festival} ({days_to} days out). For {sname} in {locality}, "
        f"the highest-leverage move is {angle}. With {views} profile views this month, "
        f"I can draft an Insta story plus a 3-line WhatsApp blast you can send this weekend — want me to?"
    )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "template_name": "vera_festival_v1",
        "template_params": [owner, festival, str(days_to), sname, angle, locality],
    }


def _t_milestone_reached__all(category, merchant, trigger, customer, enriched):
    payload = trigger.get("payload", {})
    milestone = payload.get("milestone", "")
    value = payload.get("value", "")
    if not milestone:
        return None

    owner = merchant.get("identity", {}).get("owner_first_name", "")
    sname = merchant.get("identity", {}).get("name", "")
    locality = merchant.get("identity", {}).get("locality", "")
    if not owner:
        return None

    loc_str = f" in {locality}" if locality else ""
    views = merchant.get("performance", {}).get("views", 0)
    body = (
        f"{owner}, milestone alert — {sname}{loc_str} just crossed {value} {milestone}. "
        f"With {views} monthly profile views, two ways this compounds: pin it on your "
        f"GBP profile (signals trust to first-time browsers), or post it as a thank-you "
        f"carousel on Insta (your last carousel got 4x story engagement). Which one?"
    )
    return {
        "body": body,
        "cta": "multi_choice",
        "template_name": "vera_milestone_v1",
        "template_params": [owner, sname, str(value), milestone, locality],
    }


def _t_trial_followup__all(category, merchant, trigger, customer, enriched):
    if not customer:
        return None
    payload = trigger.get("payload", {})
    days_since = payload.get("days_since_trial", 0)
    program = payload.get("program_name", "")
    if not days_since:
        return None

    name = customer.get("identity", {}).get("name", "").split("(")[0].strip()
    owner = merchant.get("identity", {}).get("owner_first_name", "")
    sname = merchant.get("identity", {}).get("name", "")
    cat_slug = category.get("slug", "")

    program_pitch = program or {
        "salons": "the 4-session skin-prep package",
        "salon": "the 4-session skin-prep package",
        "gyms": "the 8-week starter program",
        "gym": "the 8-week starter program",
        "dentists": "ongoing cleaning + check-up",
        "dentist": "ongoing cleaning + check-up",
    }.get(cat_slug, "the full program")

    body = (
        f"Hi {name}! It's been {days_since} days since your trial at "
        f"{sname} — wanted to check in. If you're considering next steps, "
        f"{program_pitch} starts at a reduced first-month rate for trial "
        f"members. Want me to share the details, or should I check back "
        f"in another week?"
    )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "template_name": "merchant_trial_followup_v1",
        "template_params": [name, sname, str(days_since), program_pitch],
    }


def _t_perf_spike__all(category, merchant, trigger, customer, enriched):
    payload = trigger.get("payload", {})
    metric = payload.get("metric", "views")
    delta = payload.get("delta_pct", 0)
    delta_str = f"+{int(delta * 100)}%" if isinstance(delta, float) else f"+{delta}%"

    owner = merchant.get("identity", {}).get("owner_first_name", "")
    sname = merchant.get("identity", {}).get("name", "")
    locality = merchant.get("identity", {}).get("locality", "")
    if not owner:
        return None

    cur_value = merchant.get("performance", {}).get(metric, "")
    calls = merchant.get("performance", {}).get("calls", 0)
    loc_str = f" in {locality}" if locality else ""
    if delta == 0 or delta_str == "+0%":
        body = (
            f"{owner}, {metric} holding steady at {cur_value} this week "
            f"for {sname}{loc_str} ({calls} calls this month). Good foundation — the compounding move: "
            f"capture this in a 3-line WhatsApp testimonial-ask to your last 10 happy customers. "
            f"Their reviews this week feed next week's growth. Want me to "
            f"draft the message?"
        )
    else:
        body = (
            f"{owner}, nice — {metric} up {delta_str} week-on-week (now at "
            f"{cur_value}) for {sname}{loc_str}. The compounding move: capture this momentum in a "
            f"3-line WhatsApp testimonial-ask to your last 10 happy customers. "
            f"Their reviews this week feed next week's growth. Want me to "
            f"draft the message?"
        )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "template_name": "vera_perf_spike_v1",
        "template_params": [owner, metric, delta_str, str(cur_value), sname, locality],
    }


def _t_winback_eligible__all(category, merchant, trigger, customer, enriched):
    payload = trigger.get("payload", {})
    cohort_size = payload.get("eligible_count", 0)
    last_active_days = payload.get("avg_last_active_days", 0)

    owner = merchant.get("identity", {}).get("owner_first_name", "")
    sname = merchant.get("identity", {}).get("name", "")
    if not owner or not cohort_size:
        return None

    cat_slug = category.get("slug", "")
    voice = _cat_voice(cat_slug)
    patient_word = voice["patient_word"]

    body = (
        f"{owner}, ~{cohort_size} {patient_word} haven't been back to {sname} in "
        f"{last_active_days}+ days. Industry baseline for a warm-tone "
        f"winback message is 8-12% reactivation. Want me to draft a "
        f"single WhatsApp template you can personalize per {patient_word[:-1] if patient_word.endswith('s') else patient_word} + send "
        f"in batches of 25/day?"
    )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "template_name": "vera_winback_eligible_v1",
        "template_params": [owner, sname, str(cohort_size), str(last_active_days)],
    }


def _t_dormant_with_vera__all(category, merchant, trigger, customer, enriched):
    payload = trigger.get("payload", {})
    days_dormant = (payload.get("days_since_last_merchant_message") or
                    payload.get("days_since_last_engagement") or 30)
    last_topic = payload.get("last_topic", "")

    owner = merchant.get("identity", {}).get("owner_first_name", "")
    sname = merchant.get("identity", {}).get("name", "")
    locality = merchant.get("identity", {}).get("locality", "")
    if not owner:
        return None

    perf = merchant.get("performance", {})
    views = perf.get("views", 0)
    calls = perf.get("calls", 0)
    lapsed = merchant.get("customer_aggregate", {}).get("lapsed_90d_plus", 0)

    topic_anchor = ""
    if last_topic:
        topic_clean = last_topic.replace("_", " ")
        topic_anchor = f" Last we spoke about {topic_clean}."

    loc_str = f" in {locality}" if locality else ""
    body = (
        f"Hi {owner} — Vera here. About {days_dormant} days since we last "
        f"connected.{topic_anchor} Quick update on {sname}{loc_str}: "
        f"{views} profile views and {calls} calls this month"
    )
    if lapsed:
        body += f", and {lapsed} customers haven't been back in 90+ days"
    body += (
        f". I've got 3 specific moves peers in your category are using to "
        f"convert these numbers — want me to send them over?"
    )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "template_name": "vera_dormant_v1",
        "template_params": [owner, sname, str(days_dormant), last_topic,
                            locality, str(views), str(calls)],
    }


def _t_cde_opportunity__dentists(category, merchant, trigger, customer, enriched):
    payload = trigger.get("payload", {})
    program = payload.get("program_name", "")
    credits = payload.get("credit_hours", 0)
    deadline = payload.get("registration_deadline", "")
    if not program:
        return None

    owner = merchant.get("identity", {}).get("owner_first_name", "")
    if not owner:
        return None

    body = (
        f"Dr. {owner}, CDE worth flagging — {program} ({credits} credit "
        f"hours toward your annual requirement). Registration closes "
        f"{deadline}. Want me to drop the registration link + add the "
        f"session to your calendar?"
    )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "template_name": "vera_cde_dentists_v1",
        "template_params": [owner, program, str(credits), deadline],
    }


def _t_category_seasonal__all(category, merchant, trigger, customer, enriched):
    payload = trigger.get("payload", {})
    beat = payload.get("seasonal_beat", "")
    advisory = payload.get("advisory", "")
    if not beat:
        return None

    owner = merchant.get("identity", {}).get("owner_first_name", "")
    sname = merchant.get("identity", {}).get("name", "")
    if not owner:
        return None

    cat_slug = category.get("slug", "")
    angle = {
        "pharmacies":  "ORS sachets, sunscreen, hydration salts",
        "pharmacy":    "ORS sachets, sunscreen, hydration salts",
        "restaurants": "lighter menu — buttermilk, salads, cooling specials",
        "restaurant":  "lighter menu — buttermilk, salads, cooling specials",
        "salons":      "scalp/skin treatments for heat damage",
        "salon":       "scalp/skin treatments for heat damage",
        "gyms":        "shift outdoor classes earlier; hydration protocol",
        "gym":         "shift outdoor classes earlier; hydration protocol",
        "dentists":    "post-mango-season cleaning push",
        "dentist":     "post-mango-season cleaning push",
    }.get(cat_slug, "category-appropriate prep")

    body = (
        f"{owner}, seasonal note — {beat}. {advisory}. For {sname}, the "
        f"highest-leverage angle is {angle}. Want me to draft the "
        f"announcement + queue it on your GBP and WhatsApp?"
    )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "template_name": "vera_category_seasonal_v1",
        "template_params": [owner, beat, advisory, sname, angle],
    }


# ─── Mini helpers for templates ─────────────────────────────────────────────

def _months_since(iso_date: str) -> int:
    if not iso_date:
        return 6
    try:
        d = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        delta = datetime.now(d.tzinfo) - d
        return max(1, delta.days // 30)
    except Exception:
        return 6


def _format_match_time(iso: str) -> str:
    if not iso:
        return "evening"
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return d.strftime("%-I:%M%p").lower()
    except Exception:
        return "tonight"


def _format_date(iso: str) -> str:
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return d.strftime("%-d %b")
    except Exception:
        return iso


def _extract_first_pct(text: str) -> Optional[str]:
    m = re.search(r"(\d+(?:\.\d+)?%)", text)
    return m.group(1) if m else None


# ─── Registry ───────────────────────────────────────────────────────────────

_REGISTRY: dict[tuple[str, str], TemplateFn] = {
    # Dentists (singular and plural slug both covered via get_template normalization)
    ("research_digest",       "dentists"):    _t_research_digest__dentists,
    ("recall_due",            "dentists"):    _t_recall_due__dentists,
    ("competitor_opened",     "dentists"):    _t_competitor_opened__dentists,
    ("cde_opportunity",       "dentists"):    _t_cde_opportunity__dentists,

    # Salons
    ("wedding_package_followup", "salons"):   _t_wedding_package_followup__salons,
    ("curious_ask_due",          "salons"):   _t_curious_ask_due__salons,
    ("research_digest",          "salons"):   _t_research_digest__salons,

    # Restaurants
    ("ipl_match_today",          "restaurants"): _t_ipl_match_today__restaurants,
    ("active_planning_intent",   "restaurants"): _t_active_planning_intent__restaurants,
    ("review_theme_emerged",     "restaurants"): _t_review_theme_emerged__restaurants,

    # Gyms
    ("seasonal_perf_dip",        "gyms"):     _t_seasonal_perf_dip__gyms,
    ("customer_lapsed_hard",     "gyms"):     _t_customer_lapsed_hard__gyms,
    ("active_planning_intent",   "gyms"):     _t_active_planning_intent__gyms,

    # Pharmacies
    ("supply_alert",             "pharmacies"):_t_supply_alert__pharmacies,
    ("chronic_refill_due",       "pharmacies"):_t_chronic_refill_due__pharmacies,
    ("gbp_unverified",           "pharmacies"):_t_gbp_unverified__pharmacies,

    # Generic (any category)
    ("research_spike",           "_all_"):    _t_research_spike__all,
    ("perf_dip",                 "_all_"):    _t_perf_dip__all,
    ("renewal_due",              "_all_"):    _t_renewal_due__all,
    ("active_planning_intent",   "_all_"):    _t_active_planning_intent__all,
    ("regulation_change",        "_all_"):    _t_regulation_change__all,
    ("festival_upcoming",        "_all_"):    _t_festival_upcoming__all,
    ("milestone_reached",        "_all_"):    _t_milestone_reached__all,
    ("trial_followup",           "_all_"):    _t_trial_followup__all,
    ("perf_spike",               "_all_"):    _t_perf_spike__all,
    ("winback_eligible",         "_all_"):    _t_winback_eligible__all,
    ("dormant_with_vera",        "_all_"):    _t_dormant_with_vera__all,
    ("category_seasonal",        "_all_"):    _t_category_seasonal__all,
}
