# Build stage
FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libheif-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# Final stage
FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies (libheif is needed for runtime if using heic2any/etc)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libheif-dev && \
    rm -rf /var/lib/apt/lists/*

# Copy installed python packages from builder
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Memory optimizations for Railway/Small containers
ENV MALLOC_ARENA_MAX=2
ENV PYTHONUNBUFFERED=1
ENV PYTHONOPTIMIZE=1

COPY . .

# Create cache directory
RUN mkdir -p /data/fastf1-cache

EXPOSE 8000

ENV PORT=8000
CMD sh -c "cp -n /app/data/pit_loss.json /data/pit_loss.json 2>/dev/null; uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1 --no-access-log"
