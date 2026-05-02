import time
from fastapi import APIRouter, Request

router = APIRouter()

@router.get("/healthz")
async def healthz(request: Request):
    """
    Liveness probe. The judge polls this every 60s.
    Must return the exact counts of loaded contexts to pass Phase 1 Warmup.
    """
    # Safely access in-memory state (initialized in server.py lifespan)
    contexts: dict = request.app.state.contexts
    start_time = request.app.state.start_time
    
    counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
    for (scope, _), _ in contexts.items():
        counts[scope] = counts.get(scope, 0) + 1
        
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - start_time),
        "contexts_loaded": counts
    }