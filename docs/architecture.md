# Architecture

## 1. Process topology

```
┌──────────────────────────────────────────────────────────────┐
│ Tauri app (Rust shell)                                       │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ React renderer (TypeScript)                            │  │
│  │  - subtitle view, controls, settings, history          │  │
│  │  - NEVER sees the OpenAI API key                       │  │
│  └───────────────▲────────────────────────────────────────┘  │
│                  │ ws://127.0.0.1:<port>/ws?token=<session>  │
│  ┌───────────────┴────────────────────────────────────────┐  │
│  │ Rust core: spawns/supervises backend, Keychain access, │  │
│  │ always-on-top, token handoff                           │  │
│  └───────────────┬────────────────────────────────────────┘  │
└──────────────────┼───────────────────────────────────────────┘
                   │ spawn
          ┌────────▼─────────────────────────────────────────┐
          │ Python backend (FastAPI + asyncio), 127.0.0.1    │
          │                                                  │
          │  AudioCaptureService ─┐                          │
          │                       ▼                          │
          │  AudioStreamManager (resample guard, VAD,        │
          │                      bounded queue, base64)      │
          │                       ▼                          │
          │  OpenAIRealtimeSTTProvider ──► TranscriptManager │
          │         (WebSocket)                  │           │
          │                                      ▼           │
          │                               SegmentManager     │
          │                                      ▼           │
          │                              TranslationQueue    │
          │                                      ▼           │
          │                        OpenAITranslationProvider │
          │                                      ▼           │
          │  SessionManager ◄── EventDispatcher ──► WebSocketGateway
          │  SettingsManager    HistoryManager                │
          └────────┬─────────────────────────────────────────┘
                   │ spawn, stdout = raw PCM, stderr = JSON control
          ┌────────▼─────────────────────────────────────────┐
          │ TeamsAudioTap (Swift, ScreenCaptureKit)          │
          └──────────────────────────────────────────────────┘
```

### Why three processes

- **Swift helper** is required: ScreenCaptureKit is an Apple framework with no
  usable Python binding, and audio taps must run on a realtime-safe thread.
  Isolating it also means a capture crash cannot take down the session state.
- **Python backend** holds all business logic and all secrets. It is the only
  process that talks to OpenAI.
- **Renderer** is a pure view. It receives already-redacted events and never
  holds credentials.

## 2. Data flow and the two lanes

### Fast lane (target < 1.5 s)
```
PCM frame → VAD (not gating, only observing) → base64
          → input_audio_buffer.append
          → ...transcription.delta
          → TranscriptManager.apply_delta
          → EventDispatcher → WebSocket → UI (grey, italic)
```
Deltas never touch the translation path. They are rendered optimistically and
may be revised by later deltas; the UI replaces the whole partial line rather
than appending, because the model may correct earlier text.

### Accurate lane (target < 3 s after utterance end)
```
VAD end-of-speech → input_audio_buffer.commit (seq assigned here)
                  → ...transcription.completed
                  → TranscriptManager finalizes → SegmentManager
                  → merge / completeness check / dedupe
                  → TranslationQueue (bounded, concurrency-limited)
                  → OpenAITranslationProvider (Responses API)
                  → reorder buffer → EventDispatcher → UI
```

**The sequence number is assigned at `commit` time, in the backend, not derived
from the API.** The docs state completion ordering is not guaranteed, so
`item_id` is used only to *join* events to a turn; `seq` is used to *order* them.

## 3. Components

| Component | Responsibility | Does NOT |
|---|---|---|
| `AudioCaptureService` | Own the Swift helper subprocess lifecycle; parse its control channel; expose an async byte stream | Know about OpenAI |
| `AudioStreamManager` | Frame the byte stream, run VAD, enforce a bounded queue with a drop-oldest policy, emit append/commit intents | Do network I/O |
| `OpenAIRealtimeSTTProvider` | WebSocket session, `session.update`, append/commit, reconnect with backoff, normalize events | Interpret meaning |
| `TranscriptManager` | Accumulate deltas per `item_id`, emit `TranscriptPartial`/`TranscriptFinal`, suppress duplicates | Translate |
| `SegmentManager` | Merge, completeness heuristics, dedupe, own the source↔translation mapping | Call the API |
| `TranslationQueue` | Bounded queue, bounded concurrency, retries, timeouts, reorder buffer | Build prompts |
| `OpenAITranslationProvider` | Build the JA→VI prompt with glossary + context, call Responses, extract text | Decide what to translate |
| `SessionManager` | Session lifecycle (start/pause/resume/stop), wires components, owns cleanup and metrics | Transport |
| `EventDispatcher` | In-process pub/sub, fan-out to subscribers, backpressure per subscriber | Business logic |
| `WebSocketGateway` | FastAPI WS endpoint, auth, serialization, per-client send queue | Business logic |
| `SettingsManager` | Load/validate/persist non-secret settings; Keychain for the key | Hold plaintext keys on disk |
| `HistoryManager` | In-memory ring of segments; on opt-in, SQLite for the session list plus one Markdown file per meeting in the user's chosen folder | Persist by default, or choose the folder itself |
| `SummaryProvider` | Post-meeting recap and action items over the translated transcript | Run automatically |

## 4. Key design decisions

### D-1 Client-side VAD is mandatory, not optional
`gpt-live-transcribe` supports neither `server_vad` nor `semantic_vad` in a
transcription session (verified, see `docs/openai-verification.md`). Without a
`commit` the API never emits a `completed` event, so **no VAD means no
translations at all**. We use a short-time energy detector with a hangover
timer plus a hard maximum turn length, so a continuously-speaking presenter
still produces periodic finals.

### D-2 Ordering by locally assigned sequence
Because the API does not guarantee completion ordering, every `commit`
increments a session-scoped counter. The reorder buffer in `TranslationQueue`
releases segment `n` only after `n-1` has been released, or after a bounded
stall timeout that forces release to avoid head-of-line blocking forever.

### D-3 Backpressure is bounded everywhere
Three bounded queues: audio frames (drop-oldest, because stale audio is
worthless), translation jobs (reject-newest with a visible warning, because
dropping speech silently is worse than telling the user), and per-client WS
send queues (drop-oldest partials, never drop finals).

### D-4 Partials are disposable, finals are not
Dropping a partial degrades smoothness. Dropping a final loses meeting content.
Every queue policy follows this rule.

### D-5 Translation unit is a merged segment, not an STT turn
A VAD turn can be a two-word interjection. Translating those individually
wastes tokens and produces bad Vietnamese. `SegmentManager` merges finals
shorter than `min_translate_chars` with the next final within
`merge_window_ms`, bounded by `max_merge_chars`.

### D-6 Context without re-translation
The translation prompt includes the previous `context_size` *already translated*
JA/VI pairs as reference material, and the instructions state that only the
marked target text is to be translated. Context pairs are never re-emitted.

### D-7 The key never crosses the process boundary toward the UI
Rust reads it from Keychain and passes it to the Python child via an
environment variable on spawn (not argv, which is world-readable via `ps`).
The renderer receives only a boolean `api_key_present`.

### D-11 Per-application capture includes helper processes

Electron-style apps render in separate helper processes: a Teams meeting runs
in `com.microsoft.teams2.helper`, not in `com.microsoft.teams2`. Filtering on
an exact bundle id captured the main process — so macOS showed the app as
being captured and the indicator appeared — while the meeting's audio came
from a helper and never arrived. The filter now matches the chosen bundle id
and anything prefixed with it.

### D-9 The desktop shell needs explicit capabilities

Tauri denies *core* commands unless a capability grants them, while custom
commands registered in `invoke_handler` need no grant. That asymmetry hid a
real bug for a long time: everything hand-written worked, so the bridge looked
healthy, while `data-tauri-drag-region` silently did nothing because
`core:window:allow-start-dragging` was never granted. `capabilities/default.json`
exists for that reason and should be checked first whenever a built-in Tauri
behaviour appears to do nothing.

### D-10 Ad-hoc signing constrains entitlements

A shareable build is ad-hoc signed. `keychain-access-groups` cannot be
satisfied by an ad-hoc signature — the `$(AppIdentifierPrefix)` placeholder
has nothing to resolve against — and macOS SIGKILLs the app at launch if it is
present. The entitlement was removed rather than worked around: it is only
needed to *share* a keychain item between apps, and this app reads its own
item from the default keychain, which needs no entitlement.

### D-8 Degrade, don't crash
Any single component failure maps to a typed `error` event with a severity.
`warning` keeps the session alive (e.g. one translation failed);
`fatal` stops it (e.g. capture permission revoked).

## 5. Threading / concurrency model

- Swift helper: ScreenCaptureKit delivers `CMSampleBuffer` on its own queue.
  The handler converts and writes to a lock-free ring; a separate thread drains
  the ring to stdout. The SCK callback never performs I/O or allocation beyond
  the ring write.
- Python: single asyncio event loop. No blocking calls; subprocess stdout is
  read with an async reader. Base64 encoding of a 20 ms frame is sub-microsecond
  and stays on the loop.
- Translation: `asyncio.Semaphore`-bounded concurrency, default 3.

## 6. Failure handling

| Failure | Detection | Response |
|---|---|---|
| Helper process exits | `returncode` is not None | `fatal` if unexpected; emit `capture.stopped` |
| Screen Recording denied | helper control message `permission_denied` | `fatal` + actionable UI message |
| No audio from target | RMS below floor for `silence_warn_s` | `warning`, session continues |
| WS closed | `ConnectionClosed` | reconnect, exponential backoff 0.5→30 s, jitter; audio buffered up to the bounded queue, then drop-oldest |
| Translation timeout | `asyncio.timeout` | retry up to `max_retries`, then emit segment with `translation_error` and release the reorder slot |
| 429 / 5xx | HTTP status | retry with backoff honoring `Retry-After` when present |
| Backend dies | Rust supervisor notices child exit | UI shows disconnected; offer restart |
