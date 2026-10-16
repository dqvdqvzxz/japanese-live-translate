# OpenAI API Verification Report

Verified on **2026-10-16** against the official documentation at
`https://developers.openai.com` (markdown source retrieved by appending `.md`
to each page URL).

This document records what was **verified from primary sources** versus what
remains **unverified** and must be confirmed against a live API project.
Nothing here is assumed or invented.

---

## 1. Models

| Model requested in spec | Exists? | Source |
|---|---|---|
| `gpt-live-transcribe` | **Yes** | [models/gpt-live-transcribe](https://developers.openai.com/api/docs/models/gpt-live-transcribe) |
| `gpt-5.4-mini` | **Yes** | [models/gpt-5.4-mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini) |

Both model names in the project brief are real and current. No substitution is
required.

### `gpt-live-transcribe`
- Low-latency streaming speech-to-text.
- Input modalities: audio, text. Output: text.
- Only snapshot/alias: `gpt-live-transcribe`.
- Supported endpoint: **`v1/realtime/transcription_sessions`** only.
  Explicitly *not* supported on `v1/audio/transcriptions`, `v1/live/sessions`,
  or plain `v1/realtime`.
- Supported feature flags: `streaming`.
- Rate limits (default): Build tier 2,000 RPM / 210,000 TPM.

### `gpt-5.4-mini`
- Default snapshot: `gpt-5.4-mini-2026-03-17`.
- 400,000 context window; 272,000 max input tokens; 128,000 max output tokens.
- Knowledge cutoff Aug 31, 2025.
- Supported endpoints include **`v1/responses`** (what we use) and
  `v1/chat/completions`, `v1/batch`.
- **`reasoning.effort` supports: `none` (default), `low`, `medium`, `high`, `xhigh`.**
  `none` being both supported *and* the default is important for us: translation
  is a latency-critical transformation task, so we send `reasoning: {"effort": "none"}`
  explicitly rather than relying on a default that could change.

---

## 2. Transcription transport and session shape

Verified from [guides/realtime-transcription](https://developers.openai.com/api/docs/guides/realtime-transcription).

Session configuration (quoted verbatim from the guide):

```json
{
  "type": "session.update",
  "session": {
    "type": "transcription",
    "audio": {
      "input": {
        "format": { "type": "audio/pcm", "rate": 24000 },
        "transcription": { "model": "gpt-live-transcribe" },
        "turn_detection": null
      }
    }
  }
}
```

Verified facts:

1. **Audio format** — `audio/pcm` at `rate: 24000`. The Realtime session
   reference states: *"The PCM audio format. Only a 24kHz sample rate is
   supported."* Other allowed format types are `audio/pcmu` and `audio/pcma`
   (telephony), which we do not use. PCM here means signed 16-bit
   little-endian ("PCM16"); the WebSocket guide describes its streams as
   "raw, mono PCM16 audio at 24 kHz".
2. **Turn detection must be off.** For `gpt-live-transcribe` transcription
   sessions, omit `audio.input.turn_detection` or set it to `null`. The guide
   states the model *"doesn't support `server_vad` or `semantic_vad`."*
   **Consequence: the application must implement client-side VAD.** This is a
   hard architectural requirement, not an optimization.
3. **Streaming events** — audio is appended with `input_audio_buffer.append`
   (`audio` field = base64 PCM) and a turn is closed with
   `input_audio_buffer.commit`.
4. **Transcript events**:
   - `conversation.item.input_audio_transcription.delta` → fields `item_id`,
     `content_index`, `delta`.
   - `conversation.item.input_audio_transcription.completed` → fields `item_id`,
     `content_index`, `transcript`.
5. **Ordering is NOT guaranteed.** Quoted: *"Ordering between completion events
   from different speech turns isn't guaranteed. Use `item_id` to match
   transcription events to committed input items."*
   **Consequence: the SegmentManager must assign its own monotonic sequence at
   commit time and reorder by it.** We cannot trust arrival order.
6. **Context hints** — `transcription.prompt` (free-form), `transcription.keywords`
   (array of literal terms), `transcription.languages` (array of codes),
   `transcription.delay`.
   - `gpt-live-transcribe` uses plural **`languages`**, not `language`. The docs
     state explicitly: *"Don't send both."* So Japanese preference is
     `"languages": ["ja"]`.
   - Keywords must not contain `<`, `>`, CR, or LF — the API rejects the
     session update. We sanitize glossary terms before sending.
   - A `session.update` may be sent mid-session to change these.
7. **`delay`** accepts `minimal`, `low`, `medium`, `high`, `xhigh`. We default
   to `low` ("low-latency live captions") and expose it in settings. The docs
   warn the real millisecond value varies by configuration and must be
   benchmarked, not assumed.
8. **No word timestamps, no speaker labels, no confidence scores** from
   `gpt-live-transcribe`. Our UI and data model therefore do not promise any of
   these. Wall-clock timestamps in our events are locally generated.

### Resolved empirically: the WebSocket URL for a transcription session

**Verified 2026-10-16 against a live API project.**
`wss://api.openai.com/v1/realtime?intent=transcription` accepted the
transcription `session.update` and the server replied `session.created`.
Reproduce with `make verify-openai`.

This was the one documentation gap. The transcription guide says to connect
"with WebSocket" but links to the generic connection guide without giving a
transcription-specific URL. What *is* documented:

- The Realtime WebSocket URL pattern is
  `wss://api.openai.com/v1/realtime?model=<model>` with header
  `Authorization: Bearer $OPENAI_API_KEY` (and optional
  `OpenAI-Safety-Identifier`). No `OpenAI-Beta` header appears in the current
  docs.
- The model page for `gpt-live-transcribe` names the supporting route as
  `v1/realtime/transcription_sessions`.

Because the docs do not show a single authoritative transcription WebSocket URL,
**we did not hardcode one on faith.** The design remains:

- `JLT_OPENAI_REALTIME_URL` overrides everything, for an operator who needs to
  pin a different endpoint.
- Otherwise candidates are tried in order and the first one accepted becomes
  sticky for the session, so reconnects do not re-probe.
- `scripts/verify_openai.py` resolves it against a real key.

The fallback machinery is kept rather than deleted now that the answer is
known: the project that works today is not necessarily every project, and an
endpoint that moves should produce a reconnect rather than an outage.

---

## 3. Responses API (translation lane)

Verified from [guides/text](https://developers.openai.com/api/docs/guides/text)
and [guides/reasoning](https://developers.openai.com/api/docs/guides/reasoning).

- Endpoint: `POST https://api.openai.com/v1/responses`.
- Request fields used: `model`, `input`, `instructions`, `reasoning: {effort}`,
  `max_output_tokens`, `store`.
- Reading output: the docs warn *"It is not safe to assume that the model's text
  output is present at `output[0].content[0].text`"* because `output` can also
  contain reasoning items and tool calls. The SDK exposes an aggregated
  `output_text`. **Our HTTP client therefore walks the whole `output` array and
  concatenates every `content[].text` whose `type == "output_text"`,** rather
  than indexing position 0.
- `store` defaults to true on the API; we set `store: false` so meeting content
  is not retained by OpenAI for later retrieval. See `docs/security.md`.

---

## 3b. Observed live behaviour (smoke test, 2026-10-16)

Measured by `scripts/smoke_stt.py`, which synthesises Japanese with the macOS
`Kyoko` voice, converts it to 24 kHz mono PCM16 and streams it through the
production provider. **Synthetic speech, n=2 — indicative, not a benchmark.**

| Observation | Value |
|---|---|
| First partial delta | 949 ms and 1057 ms after audio start |
| `commit` → `completed` | 682 ms and 685 ms |
| Sequence numbers | correct and in order |
| Duplicate finals | none |

Sentence 1 came back exactly right:
`来週のリリースは延期されません。` — including the negation.

### Finding: spoken identifiers do not survive as written identifiers

Sentence 2 was `チケットABC-1234のステータスを確認してください。`
The transcript was:

| Configuration | Transcript fragment |
|---|---|
| no hints | `チケットA B Cの千二百三十四の…` |
| `keywords: ["ABC-1234"]` + topic `prompt` | `チケットA B Cの1234の…` |

Keyword and prompt hints **helped but did not fix it**: the number changed from
kanji numerals to digits, while the letters stayed separated as `A B C`.

Be careful about what this does and does not show. The input was text-to-speech,
and `Kyoko` pronounces `ABC-1234` as separated letters followed by a number — so
the model arguably transcribed what it actually heard. A human saying the same
ticket id may well transcribe better. **This has not been tested with human
speech and must not be reported as a model defect.**

What it does establish is a product risk that the design must absorb: an
identifier spoken aloud reaches the translation stage as spaced letters and a
bare number, not as `ABC-1234`. Relying on the STT layer to preserve identifiers
is therefore not safe.

**Consequence for Phase 3:** identifier reconstruction belongs in the
translation stage, not the STT stage —
- the translation prompt is given the glossary's identifiers and instructed to
  restore them;
- a deterministic pre-pass normalises `A B C の 1234` / `A B C の千二百三十四`
  shapes back to `ABC-1234` when the glossary contains a matching identifier,
  because a deterministic rule beats asking a model to be careful;
- the golden dataset carries this exact sentence as a regression case.

## 4. Pricing (for the cost estimator and the benchmark report)

From [pricing](https://developers.openai.com/api/docs/pricing), standard tier:

| Item | Price |
|---|---|
| `gpt-live-transcribe` | **$0.017 / minute** of audio |
| `gpt-transcribe` (file) | $0.0045 / minute |
| `gpt-5.4-mini` input | $0.75 / 1M tokens |
| `gpt-5.4-mini` cached input | $0.075 / 1M tokens |
| `gpt-5.4-mini` output | $4.50 / 1M tokens |

A 60-minute meeting costs **$1.02 in STT alone**, which dominates. Translation
with `gpt-5.4-mini` at an estimated ~250 input + ~120 output tokens per segment
and ~400 segments/hour is roughly $0.08 + $0.22 ≈ **$0.30/hour**. Total order of
magnitude: **~$1.30 per hour of meeting**. These are projections from published
unit prices, not measured spend — `SessionManager` records actual usage and the
benchmark report records measured numbers.

Usage reporting: Realtime sessions report usage in session events; Responses
returns a `usage` object. We record both when present and never synthesize them.

---

## 5. Documented limitations carried into the design

| Limitation | Design response |
|---|---|
| No server-side VAD for `gpt-live-transcribe` | Client-side energy+hangover VAD in `AudioStreamManager`; only it may issue `commit` |
| Completion event ordering not guaranteed | Monotonic `seq` assigned locally at commit; `SegmentManager` reorders by `seq`, never by arrival |
| No timestamps / speaker labels / confidence | Not exposed in our schema or UI |
| `languages` is plural for this model | Config sends `languages: ["ja"]`; sending `language` would be rejected |
| Keyword charset restrictions | Glossary terms sanitized (`<`, `>`, CR, LF stripped) before `session.update` |
| Realtime transcription WS URL not pinned in docs | Resolved 2026-10-16 to `?intent=transcription`; fallback + override retained; see §2 |
| `gpt-5.4-mini` is a reasoning model | `reasoning.effort: "none"` sent explicitly for latency |
| Prompt length limit for `transcription.prompt` not published | Glossary prompt truncated to a conservative 2,000 characters |
| Spoken identifiers arrive as spaced letters + bare numbers (§3b) | Reconstructed at the translation stage, deterministically where the glossary allows |

---

## 6. How to re-verify

```bash
make verify-openai     # probes models, WS URL candidates, and a translation round-trip
```

It requires a real API key and reports failures loudly. It never writes
fabricated results.
