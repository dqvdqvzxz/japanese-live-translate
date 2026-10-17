# Japanese Live Translator

Live Japanese → Vietnamese subtitles for Microsoft Teams on macOS.
All inference runs on the OpenAI API. **No local Whisper, no local LLM, no
DeepSeek.**

Developed by **VuongDQ**.

This file describes what the project *is*. The brief it started from is kept
verbatim in [docs/original-brief.md](docs/original-brief.md); where the two
disagree, this one is current and the differences are explained in
§"What changed from the brief".

---

## 1. What it does

1. Captures the audio of a chosen macOS application — Microsoft Teams desktop,
   a browser running Teams, or the whole system.
2. Streams it to OpenAI for Japanese transcription.
3. Shows Japanese as it is spoken, and Vietnamese beneath each finished
   sentence.
4. Keeps a history of meetings, with per-meeting cost, an optional AI summary
   with action items, and transcripts saved to a folder the user picks.

Runs for at least an hour without leaking memory, and recovers on its own when
the connection drops.

## 2. Architecture

```
Tauri 2 shell (Rust)      Keychain, backend supervision, window, native dialogs
  └─ React + TypeScript   subtitles, controls, settings, history
        │ ws://127.0.0.1:<port>/ws   (bearer token, loopback only)
  └─ Python 3.12 backend  all business logic and all secrets
        └─ Swift helper   ScreenCaptureKit → 24 kHz mono PCM16 on stdout
```

Three processes, deliberately:

- **Swift** because ScreenCaptureKit has no Python binding and an audio tap
  must not block.
- **Python** because it holds the logic and the API key, and is the only
  process that talks to OpenAI.
- **The renderer is a pure view.** It never sees the API key.

Components: `AudioCaptureService`, `AudioStreamManager`,
`OpenAIRealtimeSTTProvider`, `TranscriptManager`, `SegmentManager`,
`TranslationQueue`, `OpenAITranslationProvider`, `SummaryProvider`,
`SessionManager`, `EventDispatcher`, `WebSocketGateway`, `SettingsManager`,
`HistoryManager`. Collaborators are injected as protocol-typed constructor
arguments so every one can be tested against fakes.

Full detail: [docs/architecture.md](docs/architecture.md).

## 3. Models and endpoints

Verified against the official documentation and then against a live project —
see [docs/openai-verification.md](docs/openai-verification.md).

| Purpose | Model | Endpoint |
|---|---|---|
| Transcription | `gpt-live-transcribe` | `wss://api.openai.com/v1/realtime?intent=transcription` |
| Translation | `gpt-5.4-mini` | `POST /v1/responses`, `reasoning.effort: none` |
| Summary | `gpt-5.4-mini` | `POST /v1/responses`, `reasoning.effort: low` |

Audio is **24 kHz mono signed 16-bit little-endian**, the only format the
transcription model accepts.

### Constraints the API imposes

1. **No server-side VAD.** `gpt-live-transcribe` supports neither `server_vad`
   nor `semantic_vad`, and emits a final transcript only in response to
   `input_audio_buffer.commit`. Client-side VAD is therefore mandatory — not an
   optimisation. Without it there are no finals and so no translations at all.
2. **Completion events are not ordered.** A sequence number is assigned
   locally at commit time and everything downstream orders by it. `item_id`
   joins events to a turn; it never orders them.
3. **Language is `languages: ["ja"]`,** plural. The singular field is rejected,
   and sending both is an error.
4. **The session must be acknowledged before audio is sent.** Streaming
   immediately after `session.update` races the configuration, and the first
   turn of a session comes back transcribed under the server's defaults.
5. **Idle sessions are dropped.** Observed once after ~38.6 minutes of
   silence, recovered in 1.2 s. Reconnection is load-bearing for long
   meetings, not a safety net.

## 4. Behaviour worth knowing

**Segmentation, not the API, dominates latency for short utterances.** Short
finals are held up to `incomplete_flush_ms` waiting to merge into a whole
sentence, because translating a two-word interjection alone produces poor
Vietnamese. Measured end-to-end p50 of 4.1 s breaks down as ~0.6 s STT, ~1.2 s
translation, and ~2.2 s of that hold.

**Deduplication is by transcript `seq`, never by content.** Japanese meetings
are full of genuine repeats; three people saying はい is three things that
were said.

**Spoken identifiers do not survive transcription.** `ABC-1234` came back as
`A B Cの千二百三十四`; keyword hints improved the number but never the
letters. Identifiers declared in the glossary are therefore reconstructed
deterministically before translation — and only ones the user declared, so the
repair can never invent a ticket number.

**Partials are droppable, finals are not.** Every queue policy follows this.

## 5. Privacy

- Audio and text go to the **OpenAI API and nowhere else**. No developer
  server, no analytics, no telemetry.
- Requests are sent with `store: false`.
- **No audio file is ever written.**
- Transcripts are not saved unless the user turns it on *and* picks a folder
  through the macOS system picker. Each meeting is then one Markdown file,
  mode 0600, named after the recording.
- The API key lives in the macOS Keychain. It reaches the backend through its
  environment on spawn — never argv, which `ps` exposes — and never reaches
  the renderer, which only learns `api_key_present`.
- Capture never starts without an explicit press, and the state is always
  visible.
- The in-app notice says the destination is the OpenAI API. It does **not**
  claim data is never sent to a third party, because that would be false and
  other meeting participants may rely on it.

Threat model and residual risks: [docs/security.md](docs/security.md).

## 6. Using and building

```bash
make doctor     # what toolchains are present
make setup      # install everything
make test       # 386 backend + 29 frontend tests
make lint       # ruff, mypy --strict, eslint, tsc, clippy, rustfmt
make dev        # run the app
make release    # ad-hoc signed, shareable .dmg in dist/
```

Live checks, each needing `OPENAI_API_KEY` in `.env`:

```bash
make verify-openai     # models, endpoints, a translation round-trip
make smoke-stt         # Japanese speech through the real pipeline
make eval-translation  # the golden dataset against the live model
make stability MINUTES=60
```

Installing a shared copy: [docs/sharing.md](docs/sharing.md).
Building and signing: [docs/installation.md](docs/installation.md).

## 7. Measured, not assumed

Every figure below came from a command that ran. Nothing here is estimated;
`docs/benchmarks.md` holds the raw output and the scope of each run.

| | |
|---|---|
| Tests | 386 backend, 29 frontend |
| Golden translation dataset | 18/18 against the live model |
| STT latency (commit → final) | p50 637 ms, p95 837 ms |
| Translation latency | p50 1250 ms, p95 1332 ms |
| End to end | p50 4120 ms, p95 4843 ms |
| 60-minute run | 60.2 min, RSS 17.0 → 5.5 MB, 0 orphans, 1 auto-recovered reconnect |
| Teams audio capture | peak 25053, −23.7 dBFS |
| Browser audio capture | peak 8898, −15.1 dBFS (Chrome) |
| Capture with headphone output | peak 7525, −33.2 dBFS |
| Release artifact | 24.4 MB app, 21 MB dmg |

### Still unverified

- A clean Japanese end-to-end run. The measured pipeline run captured
  unrelated system audio, so the *timings* are real and the *transcription
  quality* says nothing about Japanese.
- Window dragging by hand — the capability it needed is fixed and verified in
  the generated ACL, but a native drag cannot be automated.
- Intel Macs and Windows. The macOS build is Apple Silicon only; Windows
  would need the ScreenCaptureKit helper rewritten against WASAPI loopback.
- Code signing and notarization, which need a paid Apple Developer ID.

## 8. What changed from the brief

| Brief said | What happened |
|---|---|
| Verify the models exist | Both are real; no substitution needed |
| Export the transcript | Replaced by saving to a user-chosen folder — with transcripts already written there, export was redundant |
| SQLite optional | Used for the history list and summaries; the readable copy is Markdown |
| — | Added: naming recordings, per-meeting cost, AI summary with action items |
| — | Added: a capabilities file, without which Tauri silently denies core commands |

## 9. Rules this project keeps

1. Audio is 24 kHz mono PCM16. Nothing else works.
2. Client-side VAD is mandatory.
3. Order by locally assigned `seq`, never by arrival.
4. Never log meeting content, at any level.
5. The API key never reaches the renderer or a command line.
6. Partials are droppable; finals are not.
7. Nothing is persisted unless the user opted in and chose where.
8. Capture never starts on its own.
9. **Never report a test, benchmark or API behaviour that was not executed.**
   If it was not run, it is marked pending — including in this file.
