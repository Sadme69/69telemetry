import logging

from fastapi import APIRouter, Query, HTTPException
from services.storage import get_json

from services.process import ensure_session_data

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["laps"])


@router.get("/sessions/{year}/{round_num}/laps")
async def lap_data(
    year: int,
    round_num: int,
    type: str = Query("R", description="Session type"),
):
    # Ensure data exists first (processes on-demand if needed)
    await ensure_session_data(year, round_num, type)
    
    data = get_json(f"sessions/{year}/{round_num}/{type}/laps.json")
    if data is None:
        raise HTTPException(
            status_code=404,
            detail="Lap data not available for this session.",
        )
    return {"laps": data}
