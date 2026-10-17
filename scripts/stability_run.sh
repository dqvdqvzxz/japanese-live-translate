#!/bin/sh
# Long-running stability check.
#
#   scripts/stability_run.sh [minutes]
#
# Starts a real session against the real capture helper and the real OpenAI
# socket, then samples resident memory every minute. It answers one question:
# does the process stay healthy and bounded over a long meeting?
#
# It deliberately runs against a quiet source. Silence is not forwarded to the
# API, so this costs essentially nothing — but that also means it exercises
# process lifetime, the event loop, the capture helper and the WebSocket
# keepalive path, NOT sustained transcription. It is a stability test, not a
# throughput test, and must not be reported as one.
set -eu

MINUTES="${1:-60}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${TMPDIR:-/tmp}/jlt-stability.csv"
LOG="${TMPDIR:-/tmp}/jlt-stability.log"
HANDSHAKE="${TMPDIR:-/tmp}/jlt-stability.json"
SOURCE="${SOURCE:-app:com.microsoft.teams2}"

cd "$ROOT/packages/backend"
JLT_CONFIG_DIR="${TMPDIR:-/tmp}/jlt-stability-config"
export JLT_CONFIG_DIR
rm -rf "$JLT_CONFIG_DIR"

# Not `uv run --env-file`: that option splits its value on whitespace to
# accept several files, so an absolute path containing a space — as this
# project's does — is silently truncated.
set -a
# shellcheck disable=SC1090
. "$ROOT/.env"
set +a
uv run python -m jlt > "$HANDSHAKE" 2> "$LOG" &
BACKEND_PID=$!
trap 'kill $BACKEND_PID 2>/dev/null || true' EXIT INT TERM
sleep 8

PORT=$(python3 -c "import json;print(json.load(open('$HANDSHAKE'))['port'])")
TOKEN=$(python3 -c "import json;print(json.load(open('$HANDSHAKE'))['token'])")

curl -sf -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"source_id\":\"$SOURCE\"}" "http://127.0.0.1:$PORT/api/session/start" > /dev/null
echo "session started on $SOURCE for ${MINUTES} minutes"

echo "minute,rss_kb,helpers,reconnects,finals,translated,failed" > "$OUT"
i=0
while [ "$i" -lt "$MINUTES" ]; do
  sleep 60
  i=$((i + 1))
  RSS=$(ps -o rss= -p "$BACKEND_PID" 2>/dev/null | tr -d ' ')
  [ -z "$RSS" ] && { echo "BACKEND DIED at minute $i" | tee -a "$OUT"; exit 1; }
  HELPERS=$(pgrep -f teams-audio-tap | wc -l | tr -d ' ')
  STATS=$(curl -sf -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$PORT/api/metrics" || echo '{}')
  echo "$STATS" | python3 -c "
import json,sys
d=json.load(sys.stdin) or {}
print(f\"$i,$RSS,$HELPERS,{d.get('reconnects',0)},{d.get('segments_final',0)},{d.get('segments_translated',0)},{d.get('segments_failed',0)}\")
" | tee -a "$OUT"
done

curl -sf -X POST -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$PORT/api/session/stop" \
  | python3 -m json.tool > "${TMPDIR:-/tmp}/jlt-stability-final.json"
sleep 3
echo "orphan helpers after stop: $(pgrep -f teams-audio-tap | wc -l | tr -d ' ')"
echo "results: $OUT"
