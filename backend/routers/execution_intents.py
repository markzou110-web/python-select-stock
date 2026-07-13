from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from core.db import get_db_engine
from core.execution_intents import build_execution_attribution, get_execution_intent, list_execution_intents, transition_execution_intent


router = APIRouter(prefix="/api/execution-intents", tags=["execution-intents"])


@router.get("")
def get_intents(state: str | None = None, limit: int = 100):
    normalized = str(state).upper() if state else None
    return {"items": list_execution_intents(get_db_engine(), state=normalized, limit=limit)}


@router.get("/summary/attribution")
def get_intent_attribution(days: int = 30):
    return build_execution_attribution(get_db_engine(), days=days)


@router.get("/{intent_id}")
def get_intent(intent_id: str):
    result = get_execution_intent(get_db_engine(), intent_id)
    if not result:
        raise HTTPException(status_code=404, detail="Execution intent not found")
    return result


@router.post("/{intent_id}/transition")
def transition_intent(intent_id: str, payload: Dict[str, Any]):
    result = transition_execution_intent(get_db_engine(), intent_id, str(payload.get("target") or ""), payload)
    if result.get("error") == "intent_not_found":
        raise HTTPException(status_code=404, detail="Execution intent not found")
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result)
    return result
