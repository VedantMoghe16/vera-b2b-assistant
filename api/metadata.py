from fastapi import APIRouter

router = APIRouter()

@router.get("/metadata")
async def metadata():
    """
    Bot identity endpoint. The judges read the approach field, 
    so keep it descriptive of your deterministic + LLM architecture.
    """
    return {
        "team_name": "Vera Core Architects",
        "team_members": ["Lead Engineer"],
        "model": "gpt-4o-mini", # Update based on your actual model
        "approach": "Deterministic signal-to-noise framework with rigid LLM paraphrasing and validation fallbacks.",
        "contact_email": "team@example.com",
        "version": "1.0.0",
        "submitted_at": "2026-04-26T08:00:00Z"
    }