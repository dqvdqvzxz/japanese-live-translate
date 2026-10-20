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

# The last `stats` message the helper sent: what actually ARRIVED, per input.
# "buffers 0" = nothing arrived; "out 0" = arrived but unconvertible;
# "peak -120" with buffers = the OS delivered silence.
show_stats() {
  python3 - "$1" <<'PYS'
import json, sys
last = None
try:
    for line in open(sys.argv[1], errors="replace"):
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("type") == "stats":
            last = msg
        elif msg.get("type") in ("warning", "error"):
            print(f"    ! {msg.get('code')}: {msg.get('message')}")
except OSError:
    pass
if last is None:
    print("    (no stats message - run longer than 5 s, or this is an older helper)")
for name in ("app", "mic"):
    s = last.get(name) if last else None
    if s:
        fmt = s.get("format") or {}
        print(f"    {name}: buffers {s['buffers']}, out {s['output_frames']}, "
              f"peak {s['peak_dbfs']} dBFS, rejected {s['rejected']}, "
              f"format {fmt.get('rate')}Hz/{fmt.get('channels')}ch/{fmt.get('bits')}bit")
PYS
}

printf '%-48s %8s %10s  %s\n' SOURCE PEAK dBFS RESULT
for SRC in $SOURCES; do
  RAW="${TMPDIR:-/tmp}/jlt-diag.pcm"
  ERR="${TMPDIR:-/tmp}/jlt-diag.err"
  "$HELPER" --capture "$SRC" --rate 24000 --channels 1 --frame-ms 20 > "$RAW" 2>"$ERR" &
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
  show_stats "$ERR"
  rm -f "$RAW" "$ERR"
done

echo
# A mixer that emits faster than realtime produces audio no transcriber can
# follow, and the symptom - "it captures but there is no text" - looks
# identical to having no audio at all. Measuring the ratio catches it.
echo "Checking output rate (should be ~1.0x realtime)…"
RAW="${TMPDIR:-/tmp}/jlt-rate.pcm"
ERR="${TMPDIR:-/tmp}/jlt-rate.err"
"$HELPER" --capture "$(echo "$SOURCES" | head -1)" --mic --rate 24000 --channels 1 --frame-ms 20 > "$RAW" 2>"$ERR" &
PID=$!
sleep 5.5
kill -INT "$PID" 2>/dev/null || true
wait "$PID" 2>/dev/null || true
python3 - "$RAW" <<'PY2'
import sys
seconds = len(open(sys.argv[1], "rb").read()) / 2 / 24000
ratio = seconds / 5.5
verdict = "ok" if 0.8 <= ratio <= 1.25 else "WRONG — the mixer is off-rate"
print(f"  {seconds:.2f}s captured in 5.5s  =  {ratio:.2f}x realtime  [{verdict}]")
PY2
echo "With the microphone on (speak, or play sound near the mic):"
show_stats "$ERR"
rm -f "$RAW" "$ERR"

echo
echo "Pick the source marked AUDIO HERE in the app."
echo "If only the display row has audio, use 'Entire system audio'."
