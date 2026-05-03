"""
validators.py — output validators run before send.

Five checks run in order. If any fail, the caller is expected to fall down
one rung in the fallback ladder rather than patching in place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    passed: bool = True
    failures: list[str] = field(default_factory=list)

    def fail(self, reason: str):
        self.passed = False
        self.failures.append(reason)


def validate_output(rendered: dict, category: dict, merchant: dict,
                    trigger: dict, customer: dict | None,
                    result: ValidationResult) -> ValidationResult:
    """Run all validators; mutate result; return same."""
    body = rendered.get("body", "")
    cta = rendered.get("cta", "")

    if not _has_body(body):
        result.fail("empty_body")
        return result

    if not _valid_cta_value(cta):
        result.fail(f"invalid_cta_value: {cta}")

    if not _no_taboo_vocab(body, category):
        result.fail("taboo_vocab_present")

    if not _claims_traceable(body, trigger, merchant, category, customer):
        result.fail("ungrounded_claim_present")

    if not _length_reasonable(body):
        result.fail("length_unreasonable")

    if not _no_repeated_question(body):
        result.fail("multiple_questions")

    return result


def _has_body(body: str) -> bool:
    return bool(body and body.strip() and len(body.strip()) >= 30)


def _valid_cta_value(cta: str) -> bool:
    return cta in {"open_ended", "binary_yes_no", "multi_choice"}


def _no_taboo_vocab(body: str, category: dict) -> bool:
    """Check body against category.voice.vocab_taboo."""
    taboo = category.get("voice", {}).get("vocab_taboo", [])
    body_lower = body.lower()
    for t in taboo:
        t_clean = t.lower().split("(")[0].strip()
        if not t_clean:
            continue
        pattern = rf"\b{re.escape(t_clean)}\b"
        if re.search(pattern, body_lower):
            return False
    return True


def _claims_traceable(body: str, trigger: dict, merchant: dict,
                      category: dict, customer: dict | None) -> bool:
    """
    Numeric claims should trace to a context field OR be derivable via
    simple arithmetic (e.g., tier pricing: 149 - 24 = 125 is valid).
    """
    nums_in_body = [int(n) for n in re.findall(r"\d+", body)
                    if len(n) <= 6]

    # Build legitimate number pool — ONLY from grounded data fields.
    legitimate = set()

    # Trigger payload (the actual data)
    trigger_payload = trigger.get("payload", {})
    legitimate.update(
        int(n) for n in re.findall(r"\d+", str(trigger_payload)) if len(n) <= 6
    )
    # Trigger top-level signal field (e.g., "190 people searched...")
    signal_text = trigger.get("signal", "") or ""
    legitimate.update(
        int(n) for n in re.findall(r"\d+", signal_text) if len(n) <= 6
    )
    # Trigger top-level numeric fields (urgency, etc.)
    for field_name in ("urgency",):
        val = trigger.get(field_name)
        if isinstance(val, (int, float)):
            legitimate.add(int(val))

    # Merchant performance data
    legitimate.update(
        int(n) for n in re.findall(r"\d+", str(merchant.get("performance", {}))) if len(n) <= 6
    )
    # Merchant identity
    legitimate.update(
        int(n) for n in re.findall(r"\d+", str(merchant.get("identity", {}))) if len(n) <= 6
    )
    # Merchant offers (prices, IDs)
    legitimate.update(
        int(n) for n in re.findall(r"\d+", str(merchant.get("offers", []))) if len(n) <= 6
    )
    # Merchant signals
    legitimate.update(
        int(n) for n in re.findall(r"\d+", str(merchant.get("signals", []))) if len(n) <= 6
    )
    # Merchant customer_aggregate
    legitimate.update(
        int(n) for n in re.findall(r"\d+", str(merchant.get("customer_aggregate", {}))) if len(n) <= 6
    )
    # Category voice (operational numbers like "15-min check-up")
    legitimate.update(
        int(n) for n in re.findall(r"\d+", str(category.get("voice", {}))) if len(n) <= 6
    )
    legitimate.update(
        int(n) for n in re.findall(r"\d+", str(category.get("peer_stats", {}))) if len(n) <= 6
    )
    legitimate.update(
        int(n) for n in re.findall(r"\d+", str(category.get("digest", []))) if len(n) <= 6
    )
    legitimate.update(
        int(n) for n in re.findall(r"\d+", str(category.get("offer_catalog", []))) if len(n) <= 6
    )
    legitimate.update(
        int(n) for n in re.findall(r"\d+", str(category.get("seasonal_beats", []))) if len(n) <= 6
    )
    if customer:
        legitimate.update(
            int(n) for n in re.findall(r"\d+", str(customer)) if len(n) <= 6
        )

    # Small numbers and common formatting numerics are always safe
    SAFE_THRESHOLD = 30

    suspicious = []
    for n in nums_in_body:
        if n <= SAFE_THRESHOLD:
            continue
        if n in legitimate:
            continue
        if _is_derivable(n, legitimate):
            continue
        suspicious.append(n)

    # Allow up to 2 unmatched (tolerance for minor compositional math)
    return len(suspicious) <= 2


def _is_derivable(target: int, pool: set) -> bool:
    """Check if target = a±b for some a,b in pool, or a percentage multiple."""
    pool_list = list(pool)
    for a in pool_list:
        for b in pool_list:
            if a + b == target:  return True
            if a - b == target:  return True
            if b - a == target:  return True
    return False


def _length_reasonable(body: str) -> bool:
    if len(body) > 1500:
        return False
    sentences = re.split(r"[.!?]+\s+", body.strip())
    sentences = [s for s in sentences if len(s) > 3]
    return len(sentences) <= 15


def _no_repeated_question(body: str) -> bool:
    """No more than 2 question marks."""
    return body.count("?") <= 2
