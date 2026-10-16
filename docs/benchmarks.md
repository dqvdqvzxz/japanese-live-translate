# Performance Benchmark Report

**The end-to-end benchmark has NOT been run.** It needs a real Teams meeting
with real Japanese speech, which has not happened yet.

Component measurements that *have* been taken are in §Component measurements
below. They are real, but they are not the end-to-end number and must not be
read as one. Rows are filled in only from runs that actually happened; a target
is not a result. (Development rule 13 and §9 of the brief.)

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

## Component measurements

Real numbers from real API calls, on an Apple Silicon Mac, macOS 25.6.0,
2026-10-16. **Each measures one stage in isolation. Neither is the end-to-end
latency a user would experience, because neither includes live capture, VAD
turn boundaries, or a real meeting's audio.**

### STT stage — `scripts/smoke_stt.py`

Synthesised Japanese (macOS `Kyoko`), streamed in real time through the
production provider. **n = 2. Indicative only.**

| Metric | Value |
|---|---|
| First partial delta after audio start | 949 ms, 1057 ms |
| `commit` → `completed` | 682 ms, 685 ms |
| Finals received | 2 of 2, in order, 0 duplicates |

### Translation stage — `make eval-translation`

All 18 golden cases, run serially with rolling context, exactly as the pipeline
calls the model. **n = 18.**

| Metric | Value |
|---|---|
| Model | `gpt-5.4-mini`, `reasoning.effort: none` |
| Latency P50 | **1096 ms** |
| Latency P95 | **1915 ms** |
| Cases passed | **18 / 18** |
| Tokens | 8,022 in / 290 out |

Against the brief's target of translation P50 under 3 s *after an utterance
ends*, the model call accounts for roughly 1.1 s. The remaining budget covers
VAD hangover, the STT final, and queueing — none of which is measured here.

Two defects were found by actually running this evaluation, and both are
recorded rather than smoothed over:

1. Two golden cases were mislabelled by me. `cond-03` has a negated subordinate
   clause with an affirmative main clause, which a whole-sentence marker check
   cannot represent; `term-01` labelled 失敗しました as negation, but it is
   grammatically affirmative and the correct Vietnamese "thất bại" carries no
   negation marker. Both labels were corrected, not loosened.
2. A genuine output defect: the model preserved numeric values but left the
   *units* in Japanese — `250ミリ秒`, `11月14日` — which a Vietnamese reader
   cannot read. The evaluator had not been checking for this. A
   `no_japanese_script` assertion was added, and the prompt now separates
   "keep the value exact" from "write the unit in Vietnamese". After the
   change: `250 milliseconds` and `15:00 ngày 14 tháng 11`.

## End-to-end results

| Date | Machine | macOS | Duration | Partial P50 | Translation P50 | E2E P95 | Reconnects | Cost |
|---|---|---|---|---|---|---|---|---|
| _(not yet run — needs a real meeting)_ | | | | | | | | |

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
