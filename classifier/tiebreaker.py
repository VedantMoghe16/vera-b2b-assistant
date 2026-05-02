"""
classifier/tiebreaker.py — LLM fallback for ambiguous intent classification.
Runs ONLY when the deterministic lexicon cannot confidently pick a single intent.
"""
import os
import json
from typing import Optional

TIEBREAKER_PROMPT = """You are classifying a merchant's reply to Vera, an AI assistant.
The merchant's last reply: "{reply_text}"
Vera's previous message summary: "{last_question_summary}"
Vera's CTA was: "{last_cta}"

Classify the reply into EXACTLY ONE of these two candidates:
1. {intent_a}
2. {intent_b}

Definitions:
- AFFIRM: merchant agrees / wants Vera to proceed
- DECLINE: merchant says no / not interested
- CLARIFY: merchant wants more info before deciding
- MODIFY: merchant wants a variant of what was offered
- OBJECT: merchant pushed back with a reason (e.g., price, timing)
- OFF_TOPIC: reply is unrelated to the original question
- HOSTILE: stop/abuse/legal threat/unsubscribe
- CONFUSED: merchant doesn't recognize Vera or the context

Output ONLY this JSON, no other text:
{{"intent": "<one of the two candidates>", "confidence": <0.0-1.0>}}"""

def classify_tiebreaker(text: str, intent_a: str, intent_b: str, last_outbound: Optional[dict]) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        # Fallback to the first intent if we can't call the LLM
        return intent_a

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        
        last_summary = last_outbound.get("body", "")[:100] + "..." if last_outbound else "None"
        last_cta = last_outbound.get("cta", "None") if last_outbound else "None"

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            temperature=0.0, # Must be 0 for determinism
            messages=[{
                "role": "user", 
                "content": TIEBREAKER_PROMPT.format(
                    reply_text=text,
                    last_question_summary=last_summary,
                    last_cta=last_cta,
                    intent_a=intent_a,
                    intent_b=intent_b
                )
            }],
            timeout=5
        )
        
        parsed = json.loads(response.choices[0].message.content)
        chosen_intent = parsed.get("intent", intent_a)
        
        # Security: Prevent hallucinated intents
        if chosen_intent not in [intent_a, intent_b]:
            return intent_a
            
        return chosen_intent
        
    except Exception as e:
        print(f"[Tiebreaker Failed] {e}")
        return intent_a