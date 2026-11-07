#!/usr/bin/env bash
# Serve the detector locally and expose it through a Cloudflare quick tunnel.
#
# Prerequisites:
#   pip install -r requirements.txt
#   python -m tools.prepare_hadith
#   python -m tools.build_index --corpus-dir data/prepared --output-dir hadith_engine/_data
#   cloudflared installed (https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/)
#
# Usage:
#   tools/serve_public.sh
#   PORT=8020 PYTHON=.venv/bin/python CLOUDFLARED=/usr/local/bin/cloudflared tools/serve_public.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8020}"
CLOUDFLARED="${CLOUDFLARED:-cloudflared}"
PYTHON="${PYTHON:-python3}"
LOG_DIR="${LOG_DIR:-${TMPDIR:-/tmp}/quran-hadith-tunnel}"

if ! command -v "$CLOUDFLARED" >/dev/null 2>&1; then
  echo "cloudflared not found. Set CLOUDFLARED=/path/to/cloudflared." >&2
  exit 1
fi
if ! "$PYTHON" -c 'import uvicorn, fastapi' >/dev/null 2>&1; then
  echo "$PYTHON cannot import uvicorn/fastapi. Set PYTHON=/path/to/venv/bin/python." >&2
  exit 1
fi

mkdir -p "$LOG_DIR"
cd "$ROOT"

"$PYTHON" -m uvicorn app:app --host "$HOST" --port "$PORT" >"$LOG_DIR/uvicorn.log" 2>&1 &
APP_PID=$!
TUNNEL_PID=""

cleanup() {
  kill "$APP_PID" ${TUNNEL_PID:+"$TUNNEL_PID"} 2>/dev/null || true
}
trap cleanup EXIT INT TERM

for _ in $(seq 1 120); do
  curl -sf --max-time 2 "http://$HOST:$PORT/health" >/dev/null 2>&1 && break
  if ! kill -0 "$APP_PID" 2>/dev/null; then
    echo "uvicorn exited; see $LOG_DIR/uvicorn.log" >&2
    exit 1
  fi
  sleep 1
done
if ! curl -sf --max-time 2 "http://$HOST:$PORT/health" >/dev/null 2>&1; then
  echo "uvicorn did not become healthy; see $LOG_DIR/uvicorn.log" >&2
  exit 1
fi

"$CLOUDFLARED" tunnel --url "http://$HOST:$PORT" --no-autoupdate \
  >"$LOG_DIR/cloudflared.log" 2>&1 &
TUNNEL_PID=$!

URL=""
for _ in $(seq 1 120); do
  URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG_DIR/cloudflared.log" | head -1 || true)"
  [ -n "$URL" ] && break
  if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
    echo "cloudflared exited; see $LOG_DIR/cloudflared.log" >&2
    exit 1
  fi
  sleep 1
done
if [ -z "$URL" ]; then
  echo "cloudflared did not produce a URL; see $LOG_DIR/cloudflared.log" >&2
  exit 1
fi

echo "Public URL: $URL"
echo "Health:     $URL/health"
echo "Logs:       $LOG_DIR"
wait "$TUNNEL_PID"
