#!/bin/sh
# Which source is actually producing audio?
#
#   scripts/diagnose_audio.sh [seconds]
#
# Run this WHILE the meeting is playing sound. It captures from every
# plausible source in turn and reports the peak level of each, so the answer
# is a measurement rather than a guess about which process Teams uses.
set -eu

SECONDS_EACH="${1:-6}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Prefer the helper inside an installed app, so this tests exactly what the
# app will use.
for CANDIDATE in \
  "/Applications/Japanese Live Translator.app/Contents/Resources/teams-audio-tap" \
  "$ROOT/packages/desktop/src-tauri/target/release/bundle/macos/Japanese Live Translator.app/Contents/Resources/teams-audio-tap" \
  "$ROOT/packages/audio-capture/.build/release/teams-audio-tap"
do
  [ -x "$CANDIDATE" ] && HELPER="$CANDIDATE" && break
done
[ -n "${HELPER:-}" ] || { echo "No teams-audio-tap found. Install the app or run 'make capture-build'."; exit 1; }
echo "Using: $HELPER"
echo

SOURCES=$("$HELPER" --list-sources 2>/dev/null | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)['sources']
except Exception:
    sys.exit('Could not list sources — is Screen Recording permission granted?')
wanted = [
    s for s in data
    if s['kind'] == 'display'
    or any(k in (s.get('bundle_id') or '').lower()
           for k in ('teams', 'chrome', 'edgemac', 'safari', 'firefox'))
]
for s in wanted:
    print(s['id'])
")

[ -n "$SOURCES" ] || { echo "No Teams, browser or display sources found."; exit 1; }

printf '%-48s %8s %10s  %s\n' SOURCE PEAK dBFS RESULT
for SRC in $SOURCES; do
  RAW="${TMPDIR:-/tmp}/jlt-diag.pcm"
  "$HELPER" --capture "$SRC" --rate 24000 --channels 1 --frame-ms 20 > "$RAW" 2>/dev/null &
  PID=$!
  sleep "$SECONDS_EACH"
  kill -INT "$PID" 2>/dev/null || true
  wait "$PID" 2>/dev/null || true
  python3 - "$RAW" "$SRC" <<'PY'
import math, struct, sys
raw, src = sys.argv[1], sys.argv[2]
try:
    data = open(raw, "rb").read()
except OSError:
    data = b""
n = len(data) // 2
samples = struct.unpack(f"<{n}h", data[: n * 2]) if n else ()
peak = max((abs(x) for x in samples), default=0)
rms = math.sqrt(sum(x * x for x in samples) / n) if n else 0
db = 20 * math.log10(rms / 32768) if rms else -120.0
if n == 0:
    verdict = "NO FRAMES — capture produced nothing"
elif peak > 500:
    verdict = "*** AUDIO HERE ***"
elif peak > 0:
    verdict = "almost silent"
else:
    verdict = "digital silence"
print(f"{src:<48} {peak:8d} {db:10.1f}  {verdict}")
PY
  rm -f "$RAW"
done

echo
echo "Pick the source marked AUDIO HERE in the app."
echo "If only the display row has audio, use 'Entire system audio'."
