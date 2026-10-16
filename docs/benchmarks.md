# Performance Benchmark Report

**No benchmark has been run yet. This file contains no measured numbers.**

It is a template with the exact procedure to follow. Rows are filled in only
from real runs; a target is not a result, and nothing here may be populated by
estimate. (Development rule 13 and §9 of the brief.)

## Targets (from the brief — these are goals, not claims)

| Metric | Target |
|---|---|
| Japanese partial latency | P50 < 1.5 s |
| Vietnamese translation after utterance end | P50 < 3 s |
| End to end | P95 < 8 s |
| Continuous stable operation | >= 60 min |

## What each metric means here

All three are measured from `time.monotonic()` inside the backend, so a clock
adjustment during a long meeting cannot corrupt them.

| Metric | Measured from | Measured to |
|---|---|---|
| `stt_latency_ms` | `input_audio_buffer.commit` sent | `...transcription.completed` received |
| `translation_latency_ms` | translation job enqueued | Responses body received |
| `e2e_latency_ms` | `input_audio_buffer.commit` sent | `segment.translated` published |

Partial latency cannot be measured end-to-end without a reference transcript
with known word onset times, so it is measured as *commit-independent*: the
interval from the first audio frame of a turn being appended to the first
`...transcription.delta` for that item.

## Procedure

1. `make verify-openai` — confirm models and the realtime URL.
2. Join a real Japanese Teams meeting, or play a recorded Japanese IT meeting
   through Teams so the audio path is identical to production.
3. Start a session and let it run for at least 60 minutes.
4. `curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:$PORT/api/metrics`
5. Paste the raw JSON into the Results section below, unedited.
6. Record the machine, macOS version, network, and audio source.

## Results

| Date | Machine | macOS | Duration | Partial P50 | Translation P50 | E2E P95 | Reconnects | Cost |
|---|---|---|---|---|---|---|---|---|
| _(not yet run)_ | | | | | | | | |

### Raw `/api/metrics` output

```
(not yet run)
```

## Resource usage

| Date | Idle CPU | Peak CPU | RSS after 60 min | Dropped frames |
|---|---|---|---|---|
| _(not yet run)_ | | | | |

Memory is checked for a leak by comparing RSS at minute 5 and minute 60 of the
same session; a bounded pipeline should show no sustained growth.
