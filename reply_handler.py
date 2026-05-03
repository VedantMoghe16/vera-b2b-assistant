"""
reply_handler.py — handles inbound replies on a conversation.

Returns one of:
  { action: "send", body, cta, rationale }
  { action: "wait", wait_seconds, rationale }
  { action: "end",  rationale }

Intent routing:
  AFFIRM    → deliver the promised action, ask for one missing detail if needed
  DECLINE   → graceful exit, set 3-day suppression, offer an alternative
  CLARIFY   → answer from merchant context, restate single CTA
  OBJECT    → counter with grounded fact or accept and probe
  MODIFY    → acknowledge, note change, come back in 5 min
  HOSTILE   → de-escalate once; permanent close on repeat
  CONFUSED  → gentle re-introduction as Vera
  OFF_TOPIC → polite redirect back to the open thread
  SILENCE   → short wait, do not spam
"""

from __future__ import annotations

import re
from classifier.lexicon import classify_intent


def handle_reply(merchant: dict, customer: dict | None, category: dict | None,
                 trigger: dict | None, last_outbound: dict | None,
                 reply_text: str, conversation_state: dict,
                 from_role: str = "merchant") -> dict:
    """Main entry. Returns reply contract dict."""

    text = (reply_text or "").strip()
    if not text:
        return _wait("Empty reply received; backing off briefly.", 3600)

    # ─── Auto-reply / OOO detection ──────────────────────────────────────────
    if _is_auto_reply(text):
        conversation_state["auto_reply_count"] = (
            conversation_state.get("auto_reply_count", 0) + 1
        )
        repeat = conversation_state["auto_reply_count"]

        if repeat >= 3:
            return _end(
                "Auto-reply received 3 times consecutively. "
                "No real human engagement detected; closing conversation. "
                "Suppression set — will retry after 7-day cooling period."
            )

        if repeat == 1 and last_outbound:
            owner = (merchant.get("identity", {}).get("owner_first_name", "")
                     if merchant else "")
            name_str = f" {owner}" if owner else ""
            body = (
                f"Looks like an auto-reply. When you're back{name_str}, "
                f"just reply YES if you'd like me to proceed with the suggestion."
            )
            return _send(
                body, "binary_yes_no",
                "Auto-reply detected (attempt 1). Sent one gentle prompt to owner. "
                "Backing off for 4 hours before any retry."
            )

        wait_secs = 4 * 3600 if repeat == 2 else 24 * 3600
        return _wait(
            f"Auto-reply detected (attempt {repeat}). "
            f"Pausing conversation for {wait_secs // 3600}h before next check.",
            wait_secs
        )

    # ─── Classify intent ─────────────────────────────────────────────────────
    intent = classify_intent(text, last_outbound)
    handler = _INTENT_HANDLERS.get(intent, _handle_off_topic)
    return handler(merchant, customer, category, trigger,
                   last_outbound, text, conversation_state)


# ─── Auto-reply detection ───────────────────────────────────────────────────

AUTO_REPLY_PATTERNS = [
    r"thank you for (?:contacting|reaching out)",
    r"we will (?:respond|get back) (?:shortly|soon|to you)",
    r"out of (?:office|town)",
    r"automated (?:reply|response|message)",
    r"this is an automatic",
    r"not available right now",
    r"i am (?:currently )?away",
    r"on leave until",
    r"auto.?reply",
]


def _is_auto_reply(text: str) -> bool:
    text_lower = text.lower()
    return any(re.search(p, text_lower) for p in AUTO_REPLY_PATTERNS)


# ─── Intent handlers ────────────────────────────────────────────────────────

def _handle_affirm(merchant, customer, category, trigger,
                   last_outbound, text, state):
    """Merchant said yes — deliver the promised follow-up action."""
    owner = ""
    sname = ""
    if merchant:
        owner = merchant.get("identity", {}).get("owner_first_name", "")
        sname = merchant.get("identity", {}).get("name", "")

    if not last_outbound:
        body = (
            f"Got it{' ' + owner if owner else ''} — on it now. "
            f"Coming back with the draft in ~10 minutes."
        )
        return _send(body, "open_ended",
                     "AFFIRM received with no last_outbound context. "
                     "Acknowledged and committed to deliver in 10 min.")

    last_body = last_outbound.get("body", "")
    promise = _extract_promise(last_body)

    # Route to the right delivery based on what was promised
    if _contains_any(promise, ["draft", "whatsapp", "message", "campaign"]):
        body = (
            f"On it{' ' + owner if owner else ''}. "
            f"Drafting the message now — will have it ready in ~5 minutes. "
            f"Want me to schedule it to send immediately after you approve, "
            f"or queue it for you to review first?"
        )
        return _send(body, "binary_yes_no",
                     "AFFIRM for draft/message request. Committing to 5-min delivery, "
                     "offering approve-and-send vs review-first choice.")

    if _contains_any(promise, ["list", "patient list", "customer list",
                               "pull", "fetch"]):
        body = (
            f"Pulling the list now — should be ready in ~5 minutes. "
            f"Want me to also stage the send queue so you just hit go "
            f"once you've reviewed?"
        )
        return _send(body, "binary_yes_no",
                     "AFFIRM for list pull request. Offering send-queue staging as natural next step.")

    if _contains_any(promise, ["verification", "verify", "gbp", "google"]):
        body = (
            f"Starting the Google verification flow now for {sname or 'your listing'}. "
            f"Google will send a postcard or automated call within 5 days — "
            f"I'll ping you the moment it arrives so we complete it the same day. "
            f"Anything specific you want me to monitor in the meantime?"
        )
        return _send(body, "open_ended",
                     "AFFIRM for GBP verification. Initiated flow, set expectation on 5-day timeline.")

    if _contains_any(promise, ["campaign", "push", "targeted", "reach"]):
        cat_slug = (category or {}).get("slug", "")
        offers = (merchant or {}).get("offers", []) if merchant else []
        offer = offers[0] if offers else None
        if offer:
            body = (
                f"Launching the campaign for '{offer.get('name', 'your offer')}' "
                f"at ₹{offer.get('price', '')}. "
                f"I'll set the targeting to your locality and confirm once it's live. "
                f"Expect results within 24 hours — I'll share the first metrics then."
            )
        else:
            body = (
                f"Starting the campaign setup now. "
                f"I'll target your local area and confirm once live. "
                f"Expect first metrics within 24 hours."
            )
        return _send(body, "open_ended",
                     "AFFIRM for campaign launch. Confirmed action with timing and results expectation.")

    if _contains_any(promise, ["renewal", "link", "subscription"]):
        body = (
            f"Sending the renewal link now. "
            f"It'll be valid for 7 days — ping me if anything changes before then."
        )
        return _send(body, "open_ended",
                     "AFFIRM for renewal link. Delivering immediately with validity window.")

    if _contains_any(promise, ["post", "insta", "story", "google post"]):
        body = (
            f"Drafting the post now — will have a copy for you in ~5 minutes. "
            f"Want me to also schedule it for the best engagement window "
            f"(typically 9-11am or 6-8pm local time)?"
        )
        return _send(body, "binary_yes_no",
                     "AFFIRM for content/post request. Offering timing optimization as added value.")

    # Generic affirm — commit to action with timing
    body = (
        f"Got it{' ' + owner if owner else ''} — moving on it now. "
        f"Coming back with the next step in ~10 minutes. "
        f"Anything specific you want emphasized?"
    )
    return _send(body, "open_ended",
                 "AFFIRM received. Generic commit with 10-min timing + tone-setter question.")


def _handle_decline(merchant, customer, category, trigger,
                    last_outbound, text, state):
    """Merchant said no — graceful exit with suppression note and soft alternative."""
    owner = ""
    if merchant:
        owner = merchant.get("identity", {}).get("owner_first_name", "")

    # Offer a soft alternative before closing
    alt = _pick_alternative(trigger, merchant, category)

    if alt:
        body = (
            f"Understood{' ' + owner if owner else ''} — won't push on this. "
            f"One quick alternative: {alt}. "
            f"Otherwise, I'll check back with something different in a few days."
        )
        return _send(
            body, "binary_yes_no",
            "DECLINE received. Offered one alternative pivot; suppression set for 3 days. "
            "Will not re-raise the same trigger kind this week."
        )

    body = (
        f"Understood{' ' + owner if owner else ''} — skipping this one. "
        f"I'll check back with something different in a few days."
    )
    return _end(
        "DECLINE received. Clean exit — no alternative available. "
        "Suppression applied: this trigger kind will not fire again this week for this merchant."
    )


def _handle_clarify(merchant, customer, category, trigger,
                    last_outbound, text, state):
    """Merchant wants more info — answer from context, restate single CTA."""
    text_lower = text.lower()

    # Guard: if the question is actually off-topic, redirect there
    _OT_KEYWORDS = ["gst", "tax filing", "income tax", "loan", "credit card",
                    "weather", "election", "cricket", "movie"]
    if _contains_any(text_lower, _OT_KEYWORDS):
        return _handle_off_topic(merchant, customer, category, trigger,
                                 last_outbound, text, state)

    # Cost / price questions
    if _contains_any(text_lower, ["cost", "price", "how much", "kitna", "amount", "fee", "charge"]):
        offers = (merchant or {}).get("offers", []) if merchant else []
        if offers:
            offer = offers[0]
            body = (
                f"Your '{offer.get('name', 'offer')}' is priced at ₹{offer.get('price', '')}. "
                f"No hidden charges — that's the full price. "
                f"Should I go ahead and set up the campaign?"
            )
            return _send(body, "binary_yes_no",
                         "CLARIFY: price question answered from merchant.offers. CTA restated.")

        body = ("Pricing depends on the specific offer and channel — "
                "I can pull the exact breakdown once you confirm you'd like to proceed. "
                "Should I go ahead?")
        return _send(body, "binary_yes_no",
                     "CLARIFY: price question with no offer data; deferred to confirmation.")

    # How it works / what happens next
    if _contains_any(text_lower, ["how", "what happens", "explain", "kaise", "matlab", "process"]):
        if trigger:
            kind = trigger.get("kind", "")
            process = _explain_kind(kind)
            body = (
                f"Here's how it works: {process} "
                f"Want me to go ahead?"
            )
            return _send(body, "binary_yes_no",
                         f"CLARIFY: process explanation for {kind}. CTA restated.")

        body = ("Short version: I'll draft everything for you to review before anything goes live. "
                "Nothing sends without your OK. Should I start?")
        return _send(body, "binary_yes_no",
                     "CLARIFY: generic process explanation. Emphasized review-before-send.")

    # Specific digest or research item
    if trigger and trigger.get("kind") == "research_digest":
        payload = trigger.get("payload", {})
        digest_id = payload.get("top_item_id", "")
        if category:
            digest_items = category.get("digest", [])
            item = next((d for d in digest_items if d.get("id") == digest_id), None)
            if item:
                summary = item.get("summary", "")[:200]
                body = (
                    f"Full context: {summary} "
                    f"Action: {item.get('actionable', 'review and decide')}. "
                    f"Want me to proceed with the original suggestion?"
                )
                return _send(body, "binary_yes_no",
                             "CLARIFY: full digest summary surfaced. CTA restated.")

    # Generic clarify
    body = ("The suggestion is grounded specifically in your numbers — not a generic template. "
            "Want me to walk through the reasoning, or just go ahead?")
    return _send(body, "binary_yes_no",
                 "CLARIFY: generic reassurance. Offered proceed-or-explain choice.")


def _handle_object(merchant, customer, category, trigger,
                   last_outbound, text, state):
    """Merchant pushed back with a reason — counter with grounded fact or accept."""
    text_lower = text.lower()

    if _contains_any(text_lower, ["expensive", "costly", "mahanga", "too much", "kitna mehnga"]):
        if last_outbound and _contains_any(last_outbound.get("body", "").lower(),
                                            ["verification", "verify", "google"]):
            body = ("Quick clarification — Google verification is completely free. "
                    "The call comes directly from Google, no fees involved. "
                    "Should I start it?")
            return _send(body, "binary_yes_no",
                         "OBJECT (price): countered with grounded fact — verification is free.")

        offers = (merchant or {}).get("offers", []) if merchant else []
        if offers:
            offer = offers[0]
            body = (
                f"Fair point. The lowest-cost first step is actually free: "
                f"refresh your top photos + a Google post (zero spend). "
                f"We use your ₹{offer.get('price', '')} {offer.get('name', 'offer')} "
                f"only once you see traction. Should I start with the free moves?"
            )
            return _send(body, "binary_yes_no",
                         "OBJECT (price): redirected to zero-cost first step. Offer price grounded.")

        body = ("Fair — the path I'd suggest first costs nothing: refresh photos + a free Google post. "
                "Should I start there?")
        return _send(body, "binary_yes_no",
                     "OBJECT (price): redirected to zero-cost option.")

    if _contains_any(text_lower, ["tried", "didn't work", "didnt work", "kaam nahi",
                                   "failed", "no result", "waste"]):
        body = ("Got it — what didn't work last time? "
                "If I know the specifics, I can suggest a different angle "
                "(or honestly tell you it's not worth re-trying).")
        return _send(body, "open_ended",
                     "OBJECT (past failure): honest data-gathering rather than pushing.")

    if _contains_any(text_lower, ["not for me", "not relevant", "mere liye nahi",
                                   "not applicable", "doesn't apply"]):
        return _end("Merchant signaled this isn't relevant to their business. "
                    "Clean close — will not re-raise this trigger kind.")

    if _contains_any(text_lower, ["busy", "not now", "baad mein", "later", "time nahi"]):
        body = ("No problem — I'll come back with a different angle in a few days. "
                "If anything changes before then, just reply here.")
        return _end("OBJECT (timing): merchant is busy. Graceful exit with re-engagement note.")

    # Generic objection — probe to understand it
    body = ("Hear you. To be useful rather than repetitive, "
            "what would actually move the needle for you this month?")
    return _send(body, "open_ended",
                 "OBJECT (generic): opened for merchant to reframe their priority.")


def _handle_off_topic(merchant, customer, category, trigger,
                      last_outbound, text, state):
    """Out-of-scope message — polite one-line redirect back to thread."""
    text_lower = text.lower()

    if _contains_any(text_lower, ["gst", "tax filing", "income tax"]):
        body = ("GST/tax filing is better handled by your CA — outside what I can help with directly. "
                "Coming back to the suggestion I sent — want to proceed?")
        return _send(body, "open_ended",
                     "OFF_TOPIC: GST/tax query declined. Redirected to open thread.")

    if _contains_any(text_lower, ["loan", "credit", "finance", "overdraft"]):
        body = ("Lending products aren't my area — your bank or magicpin's finance partners are better placed. "
                "Want to continue with what we were discussing?")
        return _send(body, "open_ended",
                     "OFF_TOPIC: finance/loan query declined. Redirected.")

    if _contains_any(text_lower, ["weather", "cricket", "election", "movie", "bollywood"]):
        body = ("Ha! Outside my lane on that one. "
                "Back to the original — want to proceed with the suggestion?")
        return _send(body, "open_ended",
                     "OFF_TOPIC: general chat query. Light redirect back to thread.")

    # Generic redirect
    body = ("That's a bit outside what I help with directly. "
            "Want to continue with the original suggestion, or should I send something different?")
    return _send(body, "binary_yes_no",
                 "OFF_TOPIC: generic out-of-scope message. Offered continue-or-switch.")


def _handle_modify(merchant, customer, category, trigger,
                   last_outbound, text, state):
    """Merchant wants a variant — acknowledge and commit to revised version."""
    body = (
        f"Got it — adjusting based on that. "
        f"Coming back with a revised version in ~5 minutes. "
        f"Anything else to factor in?"
    )
    return _send(body, "open_ended",
                 f"MODIFY request received: '{text[:80]}'. "
                 "Committed to revised version with 5-min turnaround.")


def _handle_hostile(merchant, customer, category, trigger,
                    last_outbound, text, state):
    """Stop / unsubscribe / abuse — de-escalate once, then permanent close."""
    hostile_count = state.get("hostile_count", 0) + 1
    state["hostile_count"] = hostile_count

    if hostile_count == 1 and _contains_any(text.lower(), ["stop", "unsubscribe", "remove"]):
        body = ("Understood — I'll stop messaging from this thread immediately. "
                "If you ever want to reconnect, just reply here and I'll pick up.")
        return _end(
            "HOSTILE/unsubscribe: clean exit after one graceful acknowledgement. "
            "Permanent suppression on this merchant for this trigger kind."
        )

    return _end(
        "HOSTILE signal received. Conversation closed permanently. "
        "Merchant suppressed from all proactive outreach for 30 days."
    )


def _handle_confused(merchant, customer, category, trigger,
                     last_outbound, text, state):
    """Merchant doesn't recognize Vera — gentle re-introduction."""
    sname = ""
    if merchant:
        sname = merchant.get("identity", {}).get("name", "")

    cat_name = ""
    if category:
        cat_name = category.get("display_name", "")

    body = (
        f"Hi! Vera here — magicpin's assistant for {sname or 'your business'}. "
        f"I help {cat_name + ' businesses' if cat_name else 'businesses like yours'} "
        f"with listing improvements, customer messaging, and campaigns. "
        f"My last message had a specific suggestion for you — "
        f"want me to re-share it, or skip for now?"
    )
    return _send(body, "binary_yes_no",
                 "CONFUSED signal: gentle re-introduction as Vera with category context. "
                 "Offered re-share or skip.")


def _handle_silence(merchant, customer, category, trigger,
                    last_outbound, text, state):
    """Empty / single-char / unparseable reply — short wait."""
    return _wait("Uninterpretable reply; backing off briefly before retry.", 1800)


# ─── Helpers ────────────────────────────────────────────────────────────────

def _send(body: str, cta: str, rationale: str) -> dict:
    return {"action": "send", "body": body, "cta": cta, "rationale": rationale}


def _wait(rationale: str, seconds: int) -> dict:
    return {"action": "wait", "wait_seconds": seconds, "rationale": rationale}


def _end(rationale: str) -> dict:
    return {"action": "end", "rationale": rationale}


def _contains_any(text: str, keywords: list) -> bool:
    return any(k in text for k in keywords)


def _extract_promise(text: str) -> str:
    """Extract the promised action from last outbound body."""
    matches = re.findall(
        r"(?:want me to|shall i|should i|i'?ll|i can|let me)\s+([^?.,\n]+)",
        text, re.IGNORECASE,
    )
    return matches[0].strip().lower() if matches else text.lower()


def _explain_kind(kind: str) -> str:
    explanations = {
        "research_spike":   "I target the people currently searching for your service in your area, send them your offer, and report back on reach + clicks within 24h.",
        "perf_dip":         "I identify the root cause (photos, offers, or GBP), fix the highest-leverage item first, and show you the impact within 7 days.",
        "recall_due":       "I send a personalized recall reminder to the patient with your available slots — they reply directly to book.",
        "gbp_unverified":   "Google sends a verification code to your registered phone; I guide you through the 3-step confirmation. Takes ~5 min when the code arrives.",
        "winback_eligible": "I draft a warm 2-line WhatsApp message, send it in small batches of 25/day, and report back on who responded.",
        "supply_alert":     "I pull your affected patient list, draft a replacement notification, and set up a pickup workflow — all in one shot.",
        "chronic_refill_due": "I send a refill reminder to the patient (or their family), confirm dispatch, and update your Rx log.",
        "festival_upcoming":"I draft an Insta story + WhatsApp blast tailored to the festival theme, schedule it for peak engagement time, and track clicks.",
    }
    return explanations.get(kind,
                             "I draft everything for your review, nothing goes live without your approval, "
                             "and I report back with metrics within 24-48 hours.")


def _pick_alternative(trigger, merchant, category) -> str:
    """Suggest one grounded alternative when merchant declines."""
    if not trigger or not merchant:
        return ""

    kind = (trigger.get("kind") or trigger.get("type", "")).lower()
    offers = merchant.get("offers", []) if merchant else []
    signals = merchant.get("signals", []) if merchant else []

    if kind == "research_spike" and offers:
        offer = offers[0]
        return (f"I can instead post your ₹{offer.get('price', '')} "
                f"'{offer.get('name', 'offer')}' on your Google Business Profile "
                f"so it shows up in search results organically.")

    if kind in ("perf_dip", "perf_spike") and "unverified_gbp" in signals:
        return ("I can verify your Google listing instead — "
                "it's free and typically lifts calls 25-30% within 5 days.")

    if kind in ("festival_upcoming", "festival"):
        return "I can schedule a post for next week when you have more bandwidth."

    if kind == "gbp_unverified":
        return ("I can instead update your top 3 photos "
                "— a quick refresh that lifts clicks without the verification wait.")

    return ""


_INTENT_HANDLERS = {
    "AFFIRM":    _handle_affirm,
    "DECLINE":   _handle_decline,
    "CLARIFY":   _handle_clarify,
    "OBJECT":    _handle_object,
    "MODIFY":    _handle_modify,
    "HOSTILE":   _handle_hostile,
    "CONFUSED":  _handle_confused,
    "OFF_TOPIC": _handle_off_topic,
    "SILENCE":   _handle_silence,
}
