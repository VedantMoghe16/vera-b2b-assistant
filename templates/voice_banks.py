"""
templates/voice_banks.py — Category-specific vocabulary and tone styling.
"""

VOICE_BANKS = {
    "dentists": {
        "salutation": ["Dr. {owner_name}", "Doc"],
        "transitions": ["Worth a look —", "Quick clinical note —", "Heads up —"],
        "evidence_lead": ["{source} ({date}) shows", "{source} flagged"],
        "cta_verbs": ["pull the patient list", "draft the recall", "queue the post"],
        "taboo": ["guaranteed", "100% safe", "miracle", "best in city", "completely cure"]
    },
    "salons": {
        "salutation": ["Hi {owner_name}", "{salon_name} team"],
        "transitions": ["Spotted —", "Quick styling check —", "Bridal note —"],
        "evidence_lead": ["Searches for {topic} are up {delta} this week"],
        "cta_verbs": ["draft the GBP post", "queue an Insta carousel", "send the offer"],
        "taboo": ["permanent results", "instant transformation", "miracle"]
    },
    "restaurants": {
        "salutation": ["Hi {owner_name}", "{restaurant_name} team"],
        "transitions": ["Quick one —", "Operator note:", "Heads up —"],
        "evidence_lead": ["IPL {match} tonight", "Covers dropped {delta} last week"],
        "cta_verbs": ["push the combo", "set the rate card", "block the slot"],
        "taboo": ["guaranteed packed house", "viral guarantee", "best food"]
    },
    "gyms": {
        "salutation": ["Hi {owner_name}", "Coach"],
        "transitions": ["Quick check —", "Schedule note —", "Retention angle —"],
        "evidence_lead": ["Your {time} slot is running at {capacity}%", "Weekday churn is {delta}"],
        "cta_verbs": ["block a 7am class", "queue the renewal nudge", "reach out"],
        "taboo": ["shred in 7 days", "fastest results", "miracle transformation"]
    },
    "pharmacies": {
        "salutation": ["Hi {owner_name}", "{pharmacy_name} desk"],
        "transitions": ["Heads up —", "Quick check —", "Compliance note —"],
        "evidence_lead": ["{regulator} update:", "Batch {batch_id} flagged"],
        "cta_verbs": ["pull the customer list", "set up the WhatsApp reminder", "audit the register"],
        "taboo": ["miracle cure", "guaranteed result", "best price"]
    }
}