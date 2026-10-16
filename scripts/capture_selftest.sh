#!/bin/sh
# Record a few seconds from the Swift helper and wrap it as a WAV so a human
# can listen and confirm the capture is real, audible, and the right source.
#
#   scripts/capture_selftest.sh app:com.microsoft.teams2 [seconds]
#
# This is the Phase 1 acceptance check that cannot be automated: only an ear
# can tell you the audio is Teams and not silence.
set -eu

SOURCE="${1:-}"
SECONDS_TO_RECORD="${2:-10}"
HELPER="$(dirname "$0")/../packages/audio-capture/.build/release/teams-audio-tap"
OUT="${TMPDIR:-/tmp}/jlt-capture-selftest.wav"
RAW="${TMPDIR:-/tmp}/jlt-capture-selftest.pcm"

if [ -z "$SOURCE" ]; then
  echo "Available sources:"
  "$HELPER" --list-sources | python3 -m json.tool
  echo
  echo "usage: $0 <source-id> [seconds]"
  exit 64
fi

echo "Capturing ${SECONDS_TO_RECORD}s from ${SOURCE}..."
"$HELPER" --capture "$SOURCE" --rate 24000 --channels 1 --frame-ms 20 > "$RAW" &
PID=$!
sleep "$SECONDS_TO_RECORD"
kill -INT "$PID" 2>/dev/null || true
wait "$PID" 2>/dev/null || true

BYTES=$(wc -c < "$RAW" | tr -d ' ')
echo "Captured ${BYTES} bytes ($(( BYTES / 48000 )) seconds at 24 kHz mono 16-bit)"
if [ "$BYTES" -eq 0 ]; then
  echo "FAIL: no audio was captured."
  exit 1
fi

# 24 kHz, mono, signed 16-bit little-endian — the exact format we send to the API.
python3 - "$RAW" "$OUT" <<'PY'
import sys, wave
raw, out = sys.argv[1], sys.argv[2]
data = open(raw, "rb").read()
with wave.open(out, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
    w.writeframes(data)
print(f"wrote {out}")
PY

echo
echo "Now listen to it. If it is silent, the wrong source was selected or the"
echo "application was not playing audio:"
echo "  afplay $OUT"
