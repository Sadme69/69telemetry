"""On-demand session processing.

Shared by both the CLI precompute script and the backend's on-demand processing.
Uses locks to prevent duplicate processing of the same session.
"""

from __future__ import annotations

import asyncio
import logging
import traceback

from services import storage
from services.f1_data import (
    _get_session_info_sync,
    _get_track_data_sync,
    _get_lap_data_sync,
    _get_race_results_sync,
    _get_driver_positions_by_time_sync,
    _get_driver_telemetry_sync,
)

logger = logging.getLogger(__name__)

# Locks to prevent duplicate processing of the same session
_locks: dict[str, asyncio.Lock] = {}
# Track active background tasks
_tasks: dict[str, asyncio.Task] = {}


def process_session_sync(
    year: int,
    round_num: int,
    session_type: str,
    skip_existing: bool = False,
    on_status: callable = None,
    process_telemetry: bool = True,
) -> bool:
    """Process and upload all data for a single session. Returns True if successful.

    on_status: optional callback(message: str) called with progress updates.
    """
    prefix = f"{year} R{round_num} {session_type}"
    base = f"sessions/{year}/{round_num}/{session_type}"

    if skip_existing and storage.exists(f"{base}/replay.json"):
        logger.info(f"[{prefix}] Already exists, skipping")
        return True

    def status(msg: str):
        logger.info(f"[{prefix}] {msg}")
        if on_status:
            on_status(msg)

    status("Loading session data from F1 API...")

    # Session info
    try:
        info = _get_session_info_sync(year, round_num, session_type)
        # storage.put_json(f"{base}/info.json", info) # DEFERRED
    except Exception as e:
        logger.error(f"[{prefix}] Failed to get session info: {e}")
        return False

    status("Processing track data...")

    # Track data
    track = None
    try:
        track = _get_track_data_sync(year, round_num, session_type)
        # storage.put_json(f"{base}/track.json", track) # DEFERRED
    except Exception as e:
        logger.warning(f"[{prefix}] No track data: {e}")

    status("Processing lap data...")

    # Lap data
    laps = None
    try:
        laps = _get_lap_data_sync(year, round_num, session_type)
        # storage.put_json(f"{base}/laps.json", laps) # DEFERRED
    except Exception as e:
        logger.warning(f"[{prefix}] No lap data: {e}")

    # Results
    results = None
    try:
        results = _get_race_results_sync(year, round_num, session_type)
        # storage.put_json(f"{base}/results.json", results) # DEFERRED
    except Exception as e:
        logger.warning(f"[{prefix}] No results: {e}")

    status("Building replay frames (this may take a minute)...")

    # Replay frames (the big one)
    try:
        frames = _get_driver_positions_by_time_sync(year, round_num, session_type)
        # Final gate: Save everything together once frames are ready
        if info: storage.put_json(f"{base}/info.json", info)
        if track: storage.put_json(f"{base}/track.json", track)
        if laps: storage.put_json(f"{base}/laps.json", laps)
        if results: storage.put_json(f"{base}/results.json", results)
        storage.put_json(f"{base}/replay.json", frames)
        logger.info(f"[{prefix}] Uploaded {len(frames)} replay frames and essential data")
    except Exception as e:
        logger.warning(f"[{prefix}] No replay data: {e}")

    if not process_telemetry:
        status("Processing complete (telemetry skipped)")
        return True

    status("Processing telemetry...")

    # Telemetry per driver (THIS IS THE SLOWEST PART)
    try:
        drivers = info.get("drivers", [])
        total_laps_set = set()
        if laps:
            for lap in laps:
                total_laps_set.add(lap["lap_number"])

        for drv in drivers:
            abbr = drv["abbreviation"]
            # Skip if already exists
            if storage.exists(f"{base}/telemetry/{abbr}.json"):
                continue
                
            drv_telemetry = {}
            # Limit telemetry processing to keep it within reasonable time
            # Only process up to 100 laps per driver (usually enough for a GP)
            processed_laps = 0
            for lap_num in sorted(total_laps_set):
                if processed_laps > 100:
                    break
                try:
                    tel = _get_driver_telemetry_sync(
                        year, round_num, session_type, abbr, lap_num
                    )
                    if tel:
                        drv_telemetry[str(lap_num)] = tel
                        processed_laps += 1
                except Exception:
                    continue
            if drv_telemetry:
                storage.put_json(f"{base}/telemetry/{abbr}.json", drv_telemetry)
        logger.info(f"[{prefix}] Uploaded telemetry for {len(drivers)} drivers")
    except Exception as e:
        logger.warning(f"[{prefix}] Telemetry upload issue: {e}")

    status("Processing complete")
    logger.info(f"[{prefix}] Done")
    return True


async def ensure_session_data(
    year: int,
    round_num: int,
    session_type: str,
    on_status: callable = None,
) -> bool:
    """Ensure session data exists, processing on-demand if needed.

    Uses per-session locks so concurrent requests wait rather than duplicate work.
    """
    base = f"sessions/{year}/{round_num}/{session_type}"
    key = f"{year}_{round_num}_{session_type}"

    # 1. Fast path: data already exists
    if storage.exists(f"{base}/replay.json"):
        return True

    # 2. Check if a task is already running
    if key in _tasks and not _tasks[key].done():
        try:
            # Wait for existing task but with timeout for HTTP
            await asyncio.wait_for(asyncio.shield(_tasks[key]), timeout=45.0)
            return storage.exists(f"{base}/replay.json")
        except asyncio.TimeoutError:
            return False

    # 3. Start new processing task
    if key not in _locks:
        _locks[key] = asyncio.Lock()

    async with _locks[key]:
        # Double check after lock
        if storage.exists(f"{base}/replay.json"):
            return True
        if key in _tasks and not _tasks[key].done():
            return False

        async def run_processing():
            try:
                # Primary processing (no telemetry)
                success = await asyncio.to_thread(
                    process_session_sync,
                    year,
                    round_num,
                    session_type,
                    on_status=None,
                    process_telemetry=False,
                )
                if success:
                    # Immediately start telemetry in background
                    asyncio.create_task(asyncio.to_thread(
                        process_session_sync,
                        year,
                        round_num,
                        session_type,
                        skip_existing=True,
                        process_telemetry=True,
                    ))
                return success
            except Exception as e:
                logger.error(f"Processing task failed for {key}: {e}")
                return False
            finally:
                # Cleanup task reference when done
                _tasks.pop(key, None)

        task = asyncio.create_task(run_processing())
        _tasks[key] = task

        try:
            # Wait for task with timeout
            await asyncio.wait_for(asyncio.shield(task), timeout=50.0)
            return storage.exists(f"{base}/replay.json")
        except asyncio.TimeoutError:
            logger.warning(f"Initial request for {key} timed out, processing continues...")
            return False


async def ensure_session_data_ws(
    year: int,
    round_num: int,
    session_type: str,
    send_status,
) -> bool:
    """Like ensure_session_data but sends WebSocket status updates during processing."""
    base = f"sessions/{year}/{round_num}/{session_type}"

    if storage.exists(f"{base}/replay.json"):
        return True

    key = f"{year}_{round_num}_{session_type}"
    if key not in _locks:
        _locks[key] = asyncio.Lock()

    # If another request is already processing, just wait
    if _locks[key].locked():
        await send_status("Waiting for session data (another request is processing)...")
        async with _locks[key]:
            return storage.exists(f"{base}/replay.json")

    async with _locks[key]:
        if storage.exists(f"{base}/replay.json"):
            return True

        await send_status("Session data not found — processing on demand...")

        # Use a queue to bridge sync callbacks to async WebSocket sends
        status_queue: asyncio.Queue = asyncio.Queue()

        def sync_status(msg: str):
            status_queue.put_nowait(msg)

        # Run processing in background thread
        loop = asyncio.get_event_loop()
        process_task = loop.run_in_executor(
            None,
            process_session_sync,
            year,
            round_num,
            session_type,
            False,
            sync_status,
            False, # process_telemetry=False for initial load
        )

        # Forward status messages while processing
        while not process_task.done():
            try:
                msg = await asyncio.wait_for(status_queue.get(), timeout=1.0)
                await send_status(msg)
            except asyncio.TimeoutError:
                pass

        # Drain remaining messages
        while not status_queue.empty():
            msg = status_queue.get_nowait()
            await send_status(msg)

        try:
            success = process_task.result()
            # Start telemetry in background
            if success:
                asyncio.create_task(asyncio.to_thread(
                    process_session_sync,
                    year,
                    round_num,
                    session_type,
                    skip_existing=True,
                    process_telemetry=True,
                ))
            return success
        except Exception as e:
            logger.error(f"On-demand processing failed for {key}: {e}")
            return False
