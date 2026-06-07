# Memory optimization notes (512 MB Railway target)

This document explains the changes made to keep the backend under ~512 MB RAM on
Railway, and how to deploy/run it so it stays there.

## TL;DR — how to deploy under 512 MB

1. **Pre-generate session data offline** (locally or as a one-off job) instead of
   letting the live web instance call FastF1:

   ```bash
   cd backend
   pip install -r requirements.txt

   # Local storage (writes to backend/data/):
   python precompute.py 2025 --skip-existing

   # …or straight into Cloudflare R2 (recommended for Railway):
   STORAGE_MODE=r2 \
   R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... \
   R2_BUCKET_NAME=f1timingdata \
   python precompute.py 2025 --skip-existing
   ```

2. **Run the Railway service in serve-only mode** by disabling the in-process
   precompute loop. Set this env var on the service:

   ```
   AUTO_PRECOMPUTE=off
   ```

   With data already in storage and the loop off, the running container never
   imports FastF1/pandas and never builds replay frames — it just streams JSON.
   Expected RSS: ~50–80 MB idle, rising modestly while a replay is cached.

3. If you prefer the service to also fetch data itself, you can leave
   `AUTO_PRECOMPUTE=race+qual` (default). It is now memory-safe (one session in
   memory at a time, freed after each), but generating a single very long race
   in-process can still spike toward the limit. Offline precompute (steps 1–2) is
   the robust path for a hard 512 MB cap.

The Dockerfiles already set the memory-tuning env vars below; you don't need to
add them manually.

## What was changed and why

### 1. Bounded the loaded-session cache (the main fix)
`services/f1_data.py` used to cache every fully-loaded FastF1 `Session`
(telemetry + laps + weather + messages — hundreds of MB each) in a dict with **no
eviction**. The precompute loop processes session after session, so they piled up
until the container was killed.

- The cache is now hard-capped at **one** resident session (`_SESSION_CACHE_MAX = 1`).
  Loading a new session evicts any other first. This preserves the "load once,
  reuse across the ~6 derived extractions of a single session" win without
  accumulating across sessions.
- Added `evict_session()` and `clear_session_cache()` helpers.
- `services/process.py` now evicts the session in a `finally` block, so memory is
  released after every job — even if it fails partway.

### 2. Deferred the heavy imports to the paths that use them
Importing any router used to pull in FastF1 + pandas (~70 MB) and Pillow + libheif
(~20–30 MB) at startup, even on a deploy that only serves cached JSON.

- `services/process.py` imports `services.f1_data` lazily (inside
  `process_session_sync`).
- `routers/sync.py` imports Pillow / pillow-heif lazily (inside the photo-sync
  helper) and registers the HEIF opener only on first use.
- Result: a serving-only instance loads none of the heavy stack. (Verified: after
  `import services.process`, neither `fastf1` nor `pandas` is in `sys.modules`.)

### 3. Lowered peak memory while generating one session
In the replay-frame builder (`_get_driver_positions_by_time_sync`):
- The ~20 per-driver telemetry DataFrames are dropped immediately after they're
  converted to compact numpy arrays, so they don't coexist with the growing frame
  list.
- The two spots that materialized **every** telemetry timestamp into a Python list
  (millions of entries) just to compute a min/max now use a running reduction.
- `services/process.py` frees each artifact (`track`, `results`, `frames`,
  per-driver telemetry) right after it's written, instead of holding all of them.

### 4. Return freed memory to the OS
`services/memory.py` adds `release_memory()` = `gc.collect()` + glibc
`malloc_trim(0)`. CPython frees the Python objects, but glibc tends to keep the
arenas mapped, so RSS stays high without an explicit trim. Called after heavy
work and after cache evictions.

### 5. Capped the parsed-replay cache
`routers/replay.py` keeps at most `MAX_CACHED_REPLAYS = 2` parsed sessions, evicts
idle (client-less) ones oldest-first, shortens idle eviction from 5 min to 2 min,
and calls `release_memory()` on eviction.

### 6. Container/runtime tuning (both Dockerfiles)
```
MALLOC_ARENA_MAX=2          # glibc otherwise makes up to 8*nproc arenas → RSS fragmentation
MALLOC_TRIM_THRESHOLD_=100000
OMP_NUM_THREADS=1           # one heavy request at a time; extra BLAS/OMP threads = wasted arenas
OPENBLAS_NUM_THREADS=1
MKL_NUM_THREADS=1
NUMEXPR_NUM_THREADS=1
NUMEXPR_MAX_THREADS=1
PYTHONUNBUFFERED=1
PYTHONDONTWRITEBYTECODE=1
```
uvicorn now runs with `--workers 1` (each worker is a full copy of the process)
and `--no-access-log`.

## Behaviour is unchanged
No API shapes, JSON output, or replay logic were altered — only when things load,
how long they're kept, and when memory is returned to the OS. `services/r2_storage.py`
is unused dead code (superseded by the R2 backend in `services/storage.py`); it was
left in place but is never imported, so it costs nothing at runtime.

## Measured baseline (Python 3.12, import-time RSS)
| stack | RSS |
|---|---|
| interpreter | ~9 MB |
| + numpy | ~26 MB |
| + pandas | ~69 MB |
| + fastf1 | ~96 MB |

After these changes, that ~96 MB is only paid on the processing path, not at
startup for a serving instance.
