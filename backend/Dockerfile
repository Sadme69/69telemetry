FROM python:3.11-slim

WORKDIR /app

# Install system deps for numpy/pandas and libheif for HEIC support
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libheif-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create cache directory
RUN mkdir -p /data/fastf1-cache

EXPOSE 8000

ENV PORT=8000

# --- Memory tuning for small (<=512MB) containers ---
# glibc otherwise spins up to 8*nproc malloc arenas, which fragments RSS badly
# under multi-threaded numpy/pandas allocation churn. Capping arenas + an
# aggressive trim threshold keeps freed pages from lingering. We also pin the
# math libraries to a single thread: this service handles one heavy request at a
# time, so extra BLAS/OMP threads only add per-thread arenas and memory.
ENV MALLOC_ARENA_MAX=2 \
    MALLOC_TRIM_THRESHOLD_=100000 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    NUMEXPR_MAX_THREADS=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Single worker: each uvicorn worker is a full copy of the (FastF1/pandas) process.
CMD sh -c "cp -n /app/data/pit_loss.json /data/pit_loss.json 2>/dev/null; uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1 --no-access-log"
