"""On-demand session processing.

Shared by both the CLI precompute script and the backend's on-demand processing.
Uses locks to prevent duplicate processing of the same session.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import traceback

from services import storage
from services.memory import release_memory

logger = logging.getLogger(__name__)

# Locks to prevent duplicate processing of the same session
_locks: dict[str, asyncio.Lock] = {}

# Global processing gate: only one session may be *processed* at a time.
#
# Processing a session loads a full FastF1 session (telemetry + laps + weather +
# messages — hundreds of MB) and then builds the replay frame list. Two of those
# at once would blow the 512MB budget. It would ALSO thrash the single-slot
# session cache: with two jobs running, each one evicts the other's loaded
# session and is then forced to reload it on its next extraction step (and again
# on every per-lap telemetry call), churning near-endlessly.
#
# Serializing processing keeps memory bounded and eliminates the thrash. Bump
# PROCESS_CONCURRENCY (and _SESSION_CACHE_MAX in f1_data.py) only on a bigger
# instance with RAM to spare.
_PROCESS_CONCURRENCY = max(1, int(os.environ.get("PROCESS_CONCURRENCY", "1")))
_process_gate = threading.BoundedSemaphore(_PROCESS_CONCURRENCY)


def process_session_sync(
    year: int,
    round_num: int,
    session_type: str,
    skip_existing: bool = False,
    on_status: callable = None,
) -> bool:
    """Process and upload all data for a single session. Returns True if successful.

    on_status: optional callback(message: str) called with progress updates.
    """
    prefix = f"{year} R{round_num} {session_type}"
    base = f"sessions/{year}/{round_num}/{session_type}"

    if skip_existing and storage.exists(f"{base}/replay.json"):
        logger.info(f"[{prefix}] Already exists, skipping")
        return True

    # Imported lazily: this pulls in FastF1 + pandas (~70MB). Keeping it out of the
    # module top level means a serving-only instance (data already precomputed)
    # never loads them at all.
    from services.f1_data import (
        _get_session_info_sync,
        _get_track_data_sync,
        _get_lap_data_sync,
        _get_race_results_sync,
        _get_driver_positions_by_time_sync,
        _get_driver_telemetry_sync,
        evict_session,
    )

    def status(msg: str):
        logger.info(f"[{prefix}] {msg}")
        if on_status:
            on_status(msg)

    # Serialize heavy processing. If another session is currently being processed,
    # wait for it to finish instead of running concurrently (which would thrash
    # the session cache and risk OOM).
    if not _process_gate.acquire(blocking=False):
        status("Waiting for another session to finish processing...")
        _process_gate.acquire()

    try:
        status("Loading session data from F1 API...")

        # Session info
        try:
            info = _get_session_info_sync(year, round_num, session_type)
            storage.put_json(f"{base}/info.json", info)
        except Exception as e:
            logger.error(f"[{prefix}] Failed to get session info: {e}")
            return False

        status("Processing track data...")

        # Track data
        try:
            track = _get_track_data_sync(year, round_num, session_type)
            storage.put_json(f"{base}/track.json", track)
            del track
        except Exception as e:
            logger.warning(f"[{prefix}] No track data: {e}")

        status("Processing lap data...")

        # Lap data
        laps = None
        try:
            laps = _get_lap_data_sync(year, round_num, session_type)
            storage.put_json(f"{base}/laps.json", laps)
        except Exception as e:
            logger.warning(f"[{prefix}] No lap data: {e}")

        # Results
        try:
            results = _get_race_results_sync(year, round_num, session_type)
            storage.put_json(f"{base}/results.json", results)
            del results
        except Exception as e:
            logger.warning(f"[{prefix}] No results: {e}")

        status("Building replay frames (this may take a minute)...")

        # Replay frames (the big one) — free it the moment it's written out so the
        # large frame list doesn't coexist with the telemetry pass below.
        try:
            frames = _get_driver_positions_by_time_sync(year, round_num, session_type)
            storage.put_json(f"{base}/replay.json", frames)
            logger.info(f"[{prefix}] Uploaded {len(frames)} replay frames")
            del frames
            release_memory()
        except Exception as e:
            logger.warning(f"[{prefix}] No replay data: {e}")

        status("Processing telemetry...")

        # Telemetry per driver — write each driver out and drop it before the next.
        try:
            drivers = info.get("drivers", [])
            total_laps_set = set()
            if laps:
                for lap in laps:
                    total_laps_set.add(lap["lap_number"])

            for drv in drivers:
                abbr = drv["abbreviation"]
                drv_telemetry = {}
                for lap_num in sorted(total_laps_set):
                    try:
                        tel = _get_driver_telemetry_sync(
                            year, round_num, session_type, abbr, lap_num
                        )
                        if tel:
                            drv_telemetry[str(lap_num)] = tel
                    except Exception:
                        continue
                if drv_telemetry:
                    storage.put_json(f"{base}/telemetry/{abbr}.json", drv_telemetry)
                drv_telemetry = None
            logger.info(f"[{prefix}] Uploaded telemetry for {len(drivers)} drivers")
        except Exception as e:
            logger.warning(f"[{prefix}] Telemetry upload issue: {e}")

        status("Processing complete")
        logger.info(f"[{prefix}] Done")
        return True
    finally:
        # Always release the (large) loaded session, even on failure, so memory
        # never accumulates across the precompute loop / CLI batch.
        try:
            evict_session(year, round_num, session_type)
        except Exception:
            pass
        release_memory()
        _process_gate.release()


async def ensure_session_data(
    year: int,
    round_num: int,
    session_type: str,
    on_status: callable = None,
) -> bool:
    """Ensure session data exists, processing on-demand if needed.

    Uses per-session locks so concurrent requests wait rather than duplicate work.
    on_status: optional async callback(message: str) for progress updates.
    """
    base = f"sessions/{year}/{round_num}/{session_type}"

    # Fast path: data already exists
    if storage.exists(f"{base}/replay.json"):
        return True

    # Get or create lock for this session
    key = f"{year}_{round_num}_{session_type}"
    if key not in _locks:
        _locks[key] = asyncio.Lock()

    async with _locks[key]:
        # Double-check after acquiring lock (another request may have finished)
        if storage.exists(f"{base}/replay.json"):
            return True

        # Wrap sync callback for async on_status
        status_messages = []

        def sync_status(msg: str):
            status_messages.append(msg)

        # Run processing in a thread
        try:
            success = await asyncio.to_thread(
                process_session_sync,
                year,
                round_num,
                session_type,
                on_status=sync_status,
            )
            return success
        except Exception as e:
            logger.error(f"On-demand processing failed for {key}: {e}")
            traceback.print_exc()
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
            return success
        except Exception as e:
            logger.error(f"On-demand processing failed for {key}: {e}")
            return False
