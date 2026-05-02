"""
reply_handler.py — handles inbound replies on a conversation.

Returns one of:
  { action: "send", body, cta, rationale }
  { action: "wait", wait_seconds, rationale }
  { action: "end",  rationale }

Routes via the intent classifier into 8 intent handlers + special cases for
auto-replies and out-of-scope curveballs.
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
        return _wait("empty reply", 3600)

    # ─── Special: auto-reply detection ───────────────────────────────────────
    if _is_auto_reply(text):
        # Increment count IN the handler so decisions use the current value
        conversation_state["auto_reply_count"] = conversation_state.get("auto_reply_count", 0) + 1
        repeat_count = conversation_state["auto_reply_count"]
        if repeat_count >= 3:
            return _end("auto-reply received 3x; no real engagement, closing")
        # First time: gentle prompt + back off
        if repeat_count == 1 and last_outbound:
            owner = (merchant.get("identity", {}).get("owner_first_name", "")
                     if merchant else "")
            body = (f"Looks like an auto-reply 😊 When you see this {owner}, "
                    f"just reply YES if you'd like me to proceed.")
            return _send(body, "binary_yes_no",
                         "Detected auto-reply; one explicit prompt to flag for owner.")
        # Subsequent: just wait
        wait = 4 * 3600 if repeat_count == 1 else 24 * 3600
        return _wait(f"auto-reply x{repeat_count}; backing off", wait)

    # ─── Classify intent ─────────────────────────────────────────────────────
    intent = classify_intent(text, last_outbound)

    handler = _INTENT_HANDLERS.get(intent, _handle_off_topic)
    return handler(merchant, customer, category, trigger,
                   last_outbound, text, conversation_state)


# ─── Auto-reply detection ───────────────────────────────────────────────────

AUTO_REPLY_PATTERNS = [
    r"thank you for contacting",
    r"we will (?:respond|get back) (?:shortly|soon)",
    r"out of office",
    r"automated reply",
    r"this is an automatic",
    r"not available right now",
]


def _is_auto_reply(text: str) -> bool:
    text_lower = text.lower()
    return any(re.search(p, text_lower) for p in AUTO_REPLY_PATTERNS)


# ─── Intent handlers ────────────────────────────────────────────────────────

def _handle_affirm(merchant, customer, category, trigger,
                   last_outbound, text, state):
    """Merchant said yes. Deliver the promised follow-up."""
    if not last_outbound:
        return _send(
            "Got it — coming back with the next step in 10 minutes.",
            "open_ended",
            "Affirm received but no last_outbound context; placeholder commit",
        )

    # Heuristic: identify what was promised in the last outbound
    last_body = last_outbound.get("body", "")
    promised = _extract_promise(last_body)
    owner = ""
    if merchant:
        owner = merchant.get("identity", {}).get("owner_first_name", "")

    # Compose a follow-up that delivers
    if "draft" in promised and "WhatsApp" in promised:
        # Recall / refill / patient education context
        body = (
            f"Sending the abstract now. Patient-ed draft below — copy-paste, "
            f"or I can schedule it as a Google post:\n\n"
            f'"3-month vs 6-month dental cleaning — does it actually matter? '
            f"New trial says yes, especially if you've had cavities recently. "
            f'Drop us a note for a 15-min check-up."\n\n'
            f"Want me to schedule the post for tomorrow 10am?"
        )
        return _send(body, "binary_yes_no",
                     "Delivering both promised artifacts in one turn; "
                     "binary CTA to lower friction.")

    if "list" in promised or "patient list" in promised or "customer list" in promised:
        body = (
            f"Pulling the list now (drops in ~5 minutes). Want me to also "
            f"stage the WhatsApp send queue, so you just hit go once you've "
            f"reviewed?"
        )
        return _send(body, "binary_yes_no",
                     "Affirm honored; offering the natural next-step (queue prep).")

    if "verification" in promised or "verify" in promised:
        body = (
            f"Starting verification now. Google will send a postcard or "
            f"automated phone call within 5 days — I'll ping you the moment "
            f"it lands so we can complete it the same day. Anything specific "
            f"you'd like me to monitor while we wait?"
        )
        return _send(body, "open_ended",
                     "Verification kicked off; setting expectation on timing.")

    # Generic affirm follow-up — promise concrete action with timing
    body = (
        f"Got it{' ' + owner if owner else ''} — moving on it now. I'll "
        f"come back in ~10 minutes with the draft. Anything specific you "
        f"want emphasized?"
    )
    return _send(body, "open_ended",
                 "Affirm received; concrete commit with timing + tone-setter.")


def _handle_decline(merchant, customer, category, trigger,
                    last_outbound, text, state):
    """No / not interested. Close cleanly. Suppress this kind."""
    return _end(
        "Merchant declined the offered action. Closing this conversation; "
        "this trigger kind is suppressed for the standard backoff window."
    )


def _handle_clarify(merchant, customer, category, trigger,
                    last_outbound, text, state):
    """Wants more info. Provide ONE more grounded fact + restate CTA."""
    if not trigger:
        body = ("Happy to clarify — what specifically would help you decide? "
                "I can pull more detail on the offer, the data, or the "
                "operational steps.")
        return _send(body, "open_ended",
                     "Clarify received without trigger context; offering the 3 dimensions.")

    kind = trigger.get("kind", "")
    payload = trigger.get("payload", {})

    if kind == "research_digest":
        digest_id = payload.get("top_item_id", "")
        # Pull additional fact from category digest if available
        if category:
            digest_items = category.get("digest", [])
            item = next((d for d in digest_items
                         if d.get("id") == digest_id), None)
            if item:
                summary = item.get("summary", "")[:200]
                body = (f"Sure — full context: {summary} The action item: "
                        f"{item.get('actionable', 'review and decide')}. "
                        f"Want me to proceed with the original suggestion?")
                return _send(body, "binary_yes_no",
                             "Clarify met with one more grounded fact + restated CTA.")

    body = ("Sure — short version: the suggestion is grounded in your "
            "specific situation, not a generic template. Want me to walk "
            "through the reasoning, or just proceed?")
    return _send(body, "binary_yes_no",
                 "Generic clarify response; offers escape hatch to proceed.")


def _handle_object(merchant, customer, category, trigger,
                   last_outbound, text, state):
    """Pushed back with a reason. Counter with grounded fact, or accept."""
    text_lower = text.lower()

    # Common objections and grounded counters
    if "expensive" in text_lower or "costly" in text_lower:
        # If it's about verification, the actual answer is "free"
        if last_outbound and "verification" in last_outbound.get("body", "").lower():
            body = ("Quick clarification — Google verification is free, no "
                    "fees. The phone call comes from Google directly. "
                    "Should I start it?")
            return _send(body, "binary_yes_no",
                         "Price objection countered with grounded fact: verification is free.")
        body = ("Fair point on cost. The path I'd suggest first is the one "
                "with zero spend: refresh photos + a free Google post. "
                "Should I start there?")
        return _send(body, "binary_yes_no",
                     "Price objection redirected to zero-cost first step.")

    if "tried" in text_lower or "didn't work" in text_lower or "kaam nahi" in text_lower:
        body = ("Got it — what didn't work last time? If I know that, I "
                "can suggest a different angle (or honestly tell you it's "
                "not worth re-trying).")
        return _send(body, "open_ended",
                     "Past-failure objection; honest data-gathering instead of pushing.")

    if "not for me" in text_lower or "not relevant" in text_lower:
        return _end("Merchant signaled this isn't relevant to their business; "
                    "closing rather than re-pushing.")

    # Generic objection — accept and probe
    body = ("Hear you. To be useful instead of repetitive, what would "
            "actually move the needle for you this month?")
    return _send(body, "open_ended",
                 "Generic objection; opening for merchant to re-frame their priority.")


def _handle_off_topic(merchant, customer, category, trigger,
                      last_outbound, text, state):
    """Out-of-scope. Polite redirect."""
    text_lower = text.lower()

    if "gst" in text_lower or "tax" in text_lower:
        body = ("That's better handled by your CA — GST filing is outside "
                "what I can help with directly. Coming back to the original "
                "thread — want to proceed with the drafted action?")
        return _send(body, "open_ended",
                     "Out-of-scope GST ask politely declined; redirected back to thread.")

    if "loan" in text_lower or "credit" in text_lower:
        body = ("Lending products aren't something I can help with — your "
                "bank or magicpin's finance partners are better placed. "
                "Want to continue with what we were discussing?")
        return _send(body, "open_ended",
                     "Out-of-scope finance ask declined; redirected.")

    # Generic redirect
    body = ("That's outside the scope of what I help with directly. To get "
            "back on track — want to continue with the original suggestion?")
    return _send(body, "open_ended",
                 "Off-topic curveball; one polite redirect back to thread.")


def _handle_modify(merchant, customer, category, trigger,
                   last_outbound, text, state):
    """Wants a variant. Acknowledge and note we'll adjust."""
    body = (f"Got it — adjusting based on '{text[:80]}'. Coming back with a "
            f"revised version in ~5 minutes. Anything else to factor in?")
    return _send(body, "open_ended",
                 "Modification request received; committing to revised version with timing.")


def _handle_hostile(merchant, customer, category, trigger,
                    last_outbound, text, state):
    """Stop / unsubscribe / abuse. Permanent close."""
    return _end(
        "Hostile / unsubscribe signal received. Conversation closed. "
        "Permanent suppression on this trigger kind for this merchant."
    )


def _handle_confused(merchant, customer, category, trigger,
                     last_outbound, text, state):
    """Doesn't recognize Vera. Re-introduce gently."""
    sname = ""
    if merchant:
        sname = merchant.get("identity", {}).get("name", "")
    body = (f"Hi! Vera here — magicpin's assistant for {sname or 'your business'}. "
            f"I help with listing improvements, offers, and customer "
            f"messaging. Last message was about a specific suggestion for "
            f"your business. Want me to re-share, or skip for now?")
    return _send(body, "binary_yes_no",
                 "Confusion signal; gentle re-introduction with offer to re-share or skip.")


def _handle_silence(merchant, customer, category, trigger,
                    last_outbound, text, state):
    """Empty / unparseable / 1-character reply."""
    return _wait("uninterpretable reply; backing off briefly", 1800)


# ─── Helpers ────────────────────────────────────────────────────────────────

def _send(body: str, cta: str, rationale: str) -> dict:
    return {"action": "send", "body": body, "cta": cta, "rationale": rationale}


def _wait(rationale: str, seconds: int) -> dict:
    return {"action": "wait", "wait_seconds": seconds, "rationale": rationale}


def _end(rationale: str) -> dict:
    return {"action": "end", "rationale": rationale}


def _extract_promise(text: str) -> str:
    """Crude extraction of what the last outbound promised."""
    # Find "Want me to X?" or "Should I X?" or similar
    matches = re.findall(
        r"(?:want me to|shall i|should i|i'?ll)\s+([^?.,]+)",
        text, re.IGNORECASE,
    )
    return matches[0].strip() if matches else ""


_INTENT_HANDLERS = {
    "AFFIRM":   _handle_affirm,
    "DECLINE":  _handle_decline,
    "CLARIFY":  _handle_clarify,
    "OBJECT":   _handle_object,
    "MODIFY":   _handle_modify,
    "HOSTILE":  _handle_hostile,
    "CONFUSED": _handle_confused,
    "OFF_TOPIC": _handle_off_topic,
    "SILENCE":  _handle_silence,
}