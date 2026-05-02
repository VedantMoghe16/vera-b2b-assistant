"""
classifier/lexicon.py — deterministic intent classification.

Keyword + phrase + structural rules. No LLM dependency for the core path.
LLM tiebreaker is in classifier/tiebreaker.py for ambiguous cases.

Returns one of:
  AFFIRM, DECLINE, CLARIFY, MODIFY, OBJECT, OFF_TOPIC, HOSTILE,
  CONFUSED, SILENCE
"""

from __future__ import annotations

import re
from typing import Optional


INTENT_LEXICON = {
    "HOSTILE": {
        "exact": ["stop", "spam", "unsubscribe", "remove", "block",
                  "lawsuit", "harassment"],
        "phrase": ["stop messaging", "don't message", "dont message",
                   "leave me alone", "report you", "this is harassment",
                   "delete my number", "stop sending"],
        "weight": 1.0, "override": True,
    },
    "AFFIRM": {
        "exact": ["y", "yes", "yeah", "yep", "ok", "okay", "haan", "ji",
                  "sure", "pls", "please", "go", "send", "confirm",
                  "kar do", "thik hai", "theek hai"],
        "phrase": ["go ahead", "please do", "yes please", "do it",
                   "sounds good", "let's do it", "lets do it",
                   "kar do", "bhej do", "haan kar do",
                   "yes send", "yes pull", "yes draft"],
        "weight": 1.0,
    },
    "DECLINE": {
        "exact": ["no", "nope", "nahi", "skip", "pass", "later"],
        "phrase": ["not now", "not interested", "maybe later",
                   "not right now", "abhi nahi", "baad mein",
                   "no thanks", "no thank you", "not today",
                   "not this time", "not for me"],
        "weight": 1.0,
    },
    "CLARIFY": {
        "exact": ["what", "how", "why", "when", "where",
                  "details", "explain", "kya", "kaise", "kyun"],
        "phrase": ["what does", "what do you mean", "tell me more",
                   "more info", "more information", "how does", "why this",
                   "kaise karega", "matlab kya", "what's that"],
        "structural": "ends_with_question_mark",
        "weight": 0.85,
    },
    "MODIFY": {
        "exact": ["but", "instead", "rather"],
        "phrase": ["can it be", "can we change", "what about",
                   "what if", "instead of", "kya yeh ho sakta",
                   "send to", "but change", "but make"],
        "weight": 0.85,
    },
    "OBJECT": {
        "exact": ["expensive", "costly"],
        "phrase": ["too expensive", "too costly", "won't work",
                   "wont work", "already tried", "doesn't work",
                   "didn't work", "didnt work", "kaam nahi karega",
                   "didn't help", "tried before", "already done",
                   "didn't work last time"],
        "weight": 0.9,
    },
    "CONFUSED": {
        "exact": ["who", "huh"],
        "phrase": ["who is this", "who are you", "what is this",
                   "wrong number", "kaun ho", "kaun hai",
                   "i don't know", "do i know you"],
        "structural": "punctuation_only",
        "weight": 0.95,
    },
    "OFF_TOPIC": {
        "phrase": ["weather", "cricket", "modi", "election",
                   "movie", "share market", "stock market",
                   "gst filing", "tax filing", "loan", "credit card"],
        "weight": 0.7,
    },
}


def classify_intent(text: str, last_outbound: Optional[dict] = None) -> str:
    """
    Score each intent class. Return the highest-scoring one.
    HOSTILE is a hard override.
    """
    if not text or len(text.strip()) == 0:
        return "SILENCE"

    text_normalized = text.lower().strip()

    # Special: pure punctuation or single character
    if _is_punctuation_only(text_normalized) or len(text_normalized) < 2:
        return "CONFUSED"

    # HOSTILE override
    hostile = INTENT_LEXICON["HOSTILE"]
    if _matches(text_normalized, hostile):
        return "HOSTILE"

    # Score all intents
    scores: dict[str, float] = {}
    for intent, rules in INTENT_LEXICON.items():
        if intent == "HOSTILE":
            continue
        scores[intent] = _score_intent(text_normalized, rules)

    # Tiebreak: prioritize specific over generic when scores are close
    PRIORITY = ["DECLINE", "OBJECT", "MODIFY", "CLARIFY", "AFFIRM",
                "CONFUSED", "OFF_TOPIC"]

    # If AFFIRM is highest but DECLINE is also significant, DECLINE wins
    # ("yes but maybe later" → DECLINE)
    if scores.get("AFFIRM", 0) > 0.4 and scores.get("DECLINE", 0) > 0.4:
        return "DECLINE"

    # If OBJECT is significant alongside CLARIFY ("why would I want that"),
    # OBJECT wins
    if scores.get("OBJECT", 0) > 0.4 and scores.get("CLARIFY", 0) > 0.4:
        return "OBJECT"

    # Pick highest score; tiebreak by PRIORITY list order
    max_score = max(scores.values()) if scores else 0
    if max_score < 0.3:
        # Nothing matched well — likely off-topic or confused
        if last_outbound:
            return "OFF_TOPIC"
        return "CONFUSED"

    candidates = [k for k, v in scores.items() if v >= max_score - 0.05]
    for p in PRIORITY:
        if p in candidates:
            return p
    return candidates[0]


def _matches(text: str, rules: dict) -> bool:
    """Quick boolean: does any rule match?"""
    for ex in rules.get("exact", []):
        if re.search(rf"\b{re.escape(ex)}\b", text):
            return True
    for ph in rules.get("phrase", []):
        if ph in text:
            return True
    return False


def _score_intent(text: str, rules: dict) -> float:
    """Score 0.0-1.0 for one intent."""
    score = 0.0
    weight = rules.get("weight", 1.0)

    # Tokenize for exact-word matches
    tokens = set(re.findall(r"\b\w+\b", text))

    for ex in rules.get("exact", []):
        if " " in ex:
            if ex in text:
                score += 0.4 * weight
        else:
            if ex in tokens:
                score += 0.4 * weight

    for ph in rules.get("phrase", []):
        if ph in text:
            score += 0.6 * weight

    struct = rules.get("structural")
    if struct == "ends_with_question_mark" and text.rstrip().endswith("?"):
        score += 0.3 * weight
    elif struct == "punctuation_only" and _is_punctuation_only(text):
        score += 0.5 * weight

    return min(1.0, score)


def _is_punctuation_only(text: str) -> bool:
    return all(not c.isalnum() for c in text.strip())