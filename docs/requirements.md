# Requirements — Japanese Live Translator

## 1. Purpose

A macOS desktop application that shows live Japanese subtitles and Vietnamese
translations for Microsoft Teams meetings, using only the OpenAI API for
inference. No local Whisper, no local LLM, no DeepSeek.

Primary user: a Vietnamese-speaking IT professional attending Japanese-language
Teams meetings, on a low-resource Mac.

## 2. Scope

### In scope
- System audio capture of Teams (desktop app and browser tabs) on macOS.
- Realtime Japanese transcription via OpenAI `gpt-live-transcribe`.
- Japanese → Vietnamese translation via OpenAI `gpt-5.4-mini` (Responses API).
- Always-on-top subtitle window with session controls, settings, history.
- Sessions lasting at least 60 minutes.

### Out of scope (v1)
- Windows/Linux.
- Speaker diarization (the chosen STT model does not provide it).
- Word-level timestamps or confidence scores (not provided by the model).
- Recording or exporting audio.
- Translating the user's own microphone speech (mic is excluded by default).

## 3. Functional requirements

### FR-1 Audio capture
- FR-1.1 Capture system/application audio on macOS using ScreenCaptureKit.
- FR-1.2 Support Teams Desktop (`com.microsoft.teams2`, `com.microsoft.teams`)
  and Teams in a browser (Chrome, Edge, Safari, Firefox bundle IDs).
- FR-1.3 Work when output is routed to headphones (ScreenCaptureKit taps the
  app's audio graph, not the speaker, so headphone routing is irrelevant).
- FR-1.4 Exclude microphone input by default; it is never captured in v1.
- FR-1.5 Emit mono 24 kHz signed 16-bit little-endian PCM — the only format the
  STT model accepts.
- FR-1.6 Never block the audio render callback. Conversion and IPC happen off
  the realtime thread.
- FR-1.7 Release the capture stream, the helper process, and all buffers on stop.
- FR-1.8 If the target application is not running or has no audio, report this
  as an explicit status rather than silently producing silence.

### FR-2 Realtime STT
- FR-2.1 Maintain one persistent WebSocket transcription session per capture session.
- FR-2.2 Configure `languages: ["ja"]` as the language preference.
- FR-2.3 Surface `...transcription.delta` events as partial Japanese text immediately.
- FR-2.4 Surface `...transcription.completed` events as final Japanese text.
- FR-2.5 Reconnect automatically with exponential backoff after a network drop,
  resuming audio streaming without restarting capture.
- FR-2.6 Never emit the same final segment twice, including across reconnects.
- FR-2.7 Preserve segment ordering using a locally assigned monotonic sequence
  number, because the API does not guarantee completion-event ordering.
- FR-2.8 Implement client-side VAD and issue `input_audio_buffer.commit` at
  end of speech, because this model supports no server VAD.

### FR-3 Segment management
- FR-3.1 Track each segment through `partial → final → translating → translated`.
- FR-3.2 Merge consecutive short final segments (below a configurable character
  threshold, within a configurable time window) into one translation unit.
- FR-3.3 Do not translate a segment that looks like an incomplete utterance
  (no sentence terminator and short) unless it is flushed by a timeout.
- FR-3.4 Enqueue at most one translation job per segment; deduplicate by
  content hash.
- FR-3.5 Keep a stable source→translation mapping so the UI can always pair
  Japanese text with its Vietnamese text.

### FR-4 Translation
- FR-4.1 Translate Japanese to Vietnamese preserving negation, conditionals,
  numbers, dates, and technical meaning.
- FR-4.2 Pass through ticket IDs and identifiers (e.g. `ABC-1234`, `#4821`)
  unchanged.
- FR-4.3 Apply a user-configurable IT glossary (JA term → VI term).
- FR-4.4 Supply the previous N translated segments as context, without
  re-translating them or including them in the output.
- FR-4.5 Issue translation requests concurrently (bounded), with retries on
  transient failures, and a per-request timeout.
- FR-4.6 Emit translations to the UI in source order even if they complete out
  of order.

### FR-5 Desktop UI
- FR-5.1 Japanese partial text appears as soon as deltas arrive.
- FR-5.2 Finalized Japanese replaces the partial in place.
- FR-5.3 Vietnamese appears directly beneath its Japanese source.
- FR-5.4 Start / Stop / Pause / Resume controls.
- FR-5.5 Always-on-top toggle.
- FR-5.6 Session history view (in-memory by default).
- FR-5.7 Settings: audio source, STT delay, glossary, font size, theme,
  context size, persistence opt-in.
- FR-5.8 Visible connection and capture status at all times.
- FR-5.9 Dark mode, following the system by default.
- FR-5.10 Configurable subtitle font size.

### FR-6 Security
See `docs/security.md`. Summary: API key only in macOS Keychain, never sent to
the renderer; backend bound to `127.0.0.1` with a per-run bearer token; no raw
meeting content in logs; no audio or transcript persistence by default; capture
state always explicit; capture never auto-starts.

## 4. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-1 | Japanese partial latency P50 < 1.5 s (target) |
| NFR-2 | Vietnamese translation P50 < 3 s after utterance end (target) |
| NFR-3 | End-to-end P95 < 8 s (target) |
| NFR-4 | Stable for >= 60 minutes continuous operation |
| NFR-5 | Bounded memory: all queues have fixed capacity and drop/merge policies |
| NFR-6 | Idle CPU attributable to the app < 10% of one core on Apple Silicon |
| NFR-7 | Clean shutdown: no orphaned helper process, no leaked sockets |

NFR-1..3 are **targets**. Measured values go in `docs/benchmarks.md` and are
only filled in from real runs.

## 5. Acceptance criteria per phase

| Phase | Acceptance |
|---|---|
| 0 | Repo builds; lint + type check + tests run green; event schema fixed; OpenAI facts verified against docs |
| 1 | Swift helper captures Teams audio, emits 24 kHz mono PCM16, survives 30 min, releases resources |
| 2 | STT provider produces ordered, de-duplicated JA segments against a mock and a live key; reconnect covered by tests |
| 3 | Segment manager + translation queue produce ordered VI output; golden dataset passes |
| 4 | UI renders live subtitles with all controls |
| 5 | Full pipeline runs end to end with correct lifecycle and cleanup |
| 6 | Measured benchmarks recorded; app packaged; docs complete |

## 6. Assumptions

- The user has an OpenAI API project with access to `gpt-live-transcribe` and
  `gpt-5.4-mini`.
- macOS 13+ (ScreenCaptureKit audio capture requires macOS 13.0; per-application
  audio capture used here targets macOS 13+ APIs).
- The user grants Screen Recording permission, which macOS requires even for
  audio-only ScreenCaptureKit capture.
