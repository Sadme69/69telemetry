import logging
import time
from copy import deepcopy
from datetime import datetime, timezone

from fastapi import APIRouter, Query, HTTPException, Response, status
from services.storage import get_json, put_json
from services.process import ensure_session_data

...

@router.get("/sessions/{year}/{round_num}")
async def get_session(
    year: int,
    round_num: int,
    response: Response,
    type: str = Query("R", description="Session type: R, Q, S, FP1, FP2, FP3, SQ"),
):
    # 1. Try to get full data from storage first
    data = get_json(f"sessions/{year}/{round_num}/{type}/info.json")
    if data is not None:
        return data

    # 2. Trigger on-demand processing (this may take up to 60s)
    # available will be False if it times out OR if it genuinely fails
    available = await ensure_session_data(year, round_num, type)
    if available:
        data = get_json(f"sessions/{year}/{round_num}/{type}/info.json")
        if data is not None:
            return data

    # Check if it's actually processing (lock exists)
    from services.process import _locks
    lock_key = f"{year}_{round_num}_{type}"
    if lock_key in _locks and _locks[lock_key].locked():
        response.status_code = status.HTTP_202_ACCEPTED
        return {"status": "processing", "message": "Session data is being prepared..."}

    # 3. Last resort: minimal fallback from schedule (only for live/very recent)
    schedule = get_json(f"seasons/{year}/schedule.json")
    if schedule:
...

        events = schedule.get("events", [])
        if 0 < round_num <= len(events):
            evt = events[round_num - 1]
            session_type_labels = {
                "R": "Race", "Q": "Qualifying", "S": "Sprint",
                "SQ": "Sprint Qualifying", "FP1": "Practice 1",
                "FP2": "Practice 2", "FP3": "Practice 3",
            }
            return {
                "year": year,
                "round_number": round_num,
                "event_name": evt.get("event_name", f"Round {round_num}"),
                "circuit": evt.get("location", ""),
                "country": evt.get("country", ""),
                "session_type": session_type_labels.get(type, type),
                "drivers": [],
            }

    raise HTTPException(
        status_code=404,
        detail=f"Session data not available for {year} Round {round_num} ({type}).",
    )
