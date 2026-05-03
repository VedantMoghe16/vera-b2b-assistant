from fastapi import APIRouter

router = APIRouter()

@router.get("/metadata")
async def metadata():
    """
    Bot identity endpoint. Judges read the approach field.
    Required fields: name, version, model, capabilities, category_support.
    """
    return {
        "name": "Vera",
        "team_name": "Vera Core Architects",
        "model": "claude-sonnet-4-6",
        "version": "2.0.0",
        "approach": (
            "Signal-grounded composition engine with 7-stage pipeline: "
            "(1) trigger normalization + kind aliasing (recall→recall_due, lapse→customer_lapsed_hard, festival→festival_upcoming), "
            "(2) reference resolution (digest items, offers, customers), "
            "(3) multi-candidate scoring with KIND_PRIORS + urgency + recency + specificity, "
            "(4) category×kind template lookup with plural/singular slug normalization, "
            "(5) deterministic slot rendering — all numbers traced to received context, zero hallucination, "
            "(5.5) Anthropic claude-sonnet-4-6 paraphrase with per-category voice enforcement "
            "(falls back to OpenAI, then to deterministic base if both unavailable or timeout), "
            "(6) taboo-word + numeric-traceability + CTA validation, "
            "(7) weekly suppression dedup (pre-check before compose + post-check after). "
            "Category voices: dentist=clinical/patients/consultation, salon=warm/clients/glow, "
            "restaurant=urgent/customers/tonight, gym=motivational/members/streak, pharmacy=compliance/patients/prescription. "
            "5-rung fallback ladder: signal+offer → merchant signals → category digest → identity → diagnostic. "
            "Rationale always names: signal selected, merchant anchor (offer price), timing reason."
        ),
        "capabilities": [
            "research_spike", "research_digest",
            "perf_dip", "perf_spike",
            "recall_due", "recall",
            "customer_lapsed_hard", "lapse",
            "festival_upcoming", "festival",
            "supply_alert", "chronic_refill_due",
            "gbp_unverified", "regulation_change",
            "winback_eligible", "active_planning_intent",
            "review_theme_emerged", "ipl_match_today",
            "wedding_package_followup", "trial_followup",
            "dormant_with_vera", "milestone_reached",
            "category_seasonal", "renewal_due",
            "competitor_opened", "cde_opportunity",
            "seasonal_perf_dip",
        ],
        "category_support": [
            "dentist", "dentists",
            "salon", "salons",
            "restaurant", "restaurants",
            "gym", "gyms",
            "pharmacy", "pharmacies",
        ],
        "reply_intents_supported": [
            "AFFIRM", "DECLINE", "CLARIFY", "OBJECT",
            "MODIFY", "HOSTILE", "CONFUSED", "OFF_TOPIC",
            "SILENCE", "AUTO_REPLY_OOO",
        ],
        "submitted_at": "2026-05-03T00:00:00Z",
    }
