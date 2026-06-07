#!/bin/sh
# Inject runtime config into the pre-built Next.js standalone bundle.
#
# NEXT_PUBLIC_* values are normally inlined at BUILD time. To let the backend URL
# be set at DEPLOY time instead, the Dockerfile bakes the literal placeholder
# __NEXT_PUBLIC_API_URL__ into the bundle (ARG NEXT_PUBLIC_API_URL=__NEXT_PUBLIC_API_URL__).
# This script swaps that placeholder for the real value from the runtime
# environment, then starts the server.
set -e

if [ -n "${NEXT_PUBLIC_API_URL:-}" ]; then
  echo "[entrypoint] Setting NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL"
  find /app/.next -type f \( -name "*.js" -o -name "*.html" -o -name "*.json" \) \
    -exec sed -i "s#__NEXT_PUBLIC_API_URL__#${NEXT_PUBLIC_API_URL}#g" {} + 2>/dev/null || true
else
  echo "[entrypoint] NEXT_PUBLIC_API_URL is not set; leaving bundle unchanged"
fi

exec "$@"
