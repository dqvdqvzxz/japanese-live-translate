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

### Full pipeline — real audio through the real tap, 2026-10-17

Captured system audio (`display:1`) through the Swift helper, VAD, live
`gpt-live-transcribe` and live `gpt-5.4-mini`. **n = 6 utterances, 78 s.**

| Metric | Measured | Target |
|---|---|---|
| `stt_latency_ms` (commit → final) | p50 **637 ms**, p95 837 ms | — |
| `translation_latency_ms` | p50 **1250 ms**, p95 1332 ms | p50 < 3 s ✅ |
| `e2e_latency_ms` (commit → Vietnamese on screen) | p50 **4120 ms**, p95 **4843 ms** | p95 < 8 s ✅ |
| Utterances translated | 6 of 6, 0 failed | — |
| Orphan processes after stop | 0 | 0 |
| Cost | $0.0243 for 78 s | — |

**The content of this run is not a valid Japanese test.** `display:1` captures
all system audio, and unrelated Vietnamese speech was playing on the machine at
the time, so what was transcribed was that, not the Japanese TTS that was also
being played. The *timing* is a genuine measurement of the whole pipeline; the
*transcription quality* says nothing about Japanese.

#### Where the end-to-end time actually goes

e2e p50 of 4120 ms against stt 637 ms plus translation 1250 ms leaves roughly
**2.2 s unaccounted**, and it is not mysterious: short finals are held by the
segment manager. Anything under `min_translate_chars` (8) waits up to
`incomplete_flush_ms` (2500 ms) for a continuation before being released. Four
of the six utterances were very short, so the hold dominated.

That is the policy working as designed — it exists so two-word interjections
are not translated alone — but it means **segmentation, not the API, is the
largest term in end-to-end latency for short utterances.** Tuning
`incomplete_flush_ms` is the highest-leverage knob, and it is a direct
latency-versus-quality trade. This had not been obvious before measuring.

### 60-minute continuous operation — `make stability MINUTES=60`

Real capture helper, real OpenAI transcription socket, Teams as the source,
2026-10-17. Raw samples in [stability-60min.csv](stability-60min.csv).

| Metric | Result | Target |
|---|---|---|
| Duration | **3609.7 s (60.2 min)**, backend never died | ≥ 60 min ✅ |
| Resident memory, minute 5 → minute 60 | 17,024 KB → **5,472 KB** (−11.5 MB) | no growth ✅ |
| Peak resident memory | 18.1 MB | — |
| Capture helper processes | exactly 1, for the whole hour | 1 |
| Orphan processes after stop | **0** | 0 |
| Reconnects | 1, recovered automatically | — |
| Cost | ~$0 — silence is not forwarded | — |

Memory did not merely stay flat, it fell: the peak is startup allocation that
Python returns afterwards. There is no leak over an hour.

#### The run found something worth knowing

At minute 39 the OpenAI connection dropped on its own —
`ConnectionClosedError` after about 38.6 minutes of an idle session — and the
provider reconnected **1.2 seconds later** without intervention. Nothing in the
session was lost and the helper was untouched.

This is the first evidence that **OpenAI drops an idle realtime transcription
session**, which makes automatic reconnection a requirement for long meetings
rather than defensive programming. A quiet stretch in a two-hour meeting would
otherwise silently end transcription. It also validates the reconnect path
against the real service rather than only against a local test server.

#### Scope of this test

It ran against a **quiet** source, so no audio was sent and no transcription
happened. It measures process lifetime, memory, the capture helper, the event
loop and the realtime socket over an hour. It is **not** a test of sustained
transcription throughput, and the latency figures above come from the separate
runs, not from this one.

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
