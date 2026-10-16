# API Design

Two interfaces are defined here:

1. The **local backend API** (HTTP + WebSocket) consumed by the Tauri app.
2. The **audio helper control protocol** between the Swift helper and Python.

The OpenAI-facing protocol is not redefined here; it is recorded verbatim in
`docs/openai-verification.md`.

---

## 1. Local backend

Bound to `127.0.0.1` only. Every request requires
`Authorization: Bearer <session_token>`, where the token is generated once per
backend process and handed to the renderer by the Rust shell. See
`docs/security.md`.

### 1.1 HTTP

| Method | Path | Body | Returns |
|---|---|---|---|
| `GET` | `/health` | — | `{"status":"ok","version":"...","uptime_s":...}` (no auth) |
| `GET` | `/api/settings` | — | `Settings` |
| `PATCH` | `/api/settings` | partial `Settings` | `Settings` |
| `GET` | `/api/audio/sources` | — | `{"sources":[AudioSource]}` |
| `POST` | `/api/session/start` | `{"source_id": "...", "mode":"application"}` | `{"session_id": "..."}` |
| `POST` | `/api/session/pause` | — | `{"state":"paused"}` |
| `POST` | `/api/session/resume` | — | `{"state":"running"}` |
| `POST` | `/api/session/stop` | — | `{"state":"stopped","stats":SessionStats}` |
| `GET` | `/api/session` | — | `SessionState` |
| `GET` | `/api/history?limit=&session_id=` | — | `{"segments":[Segment]}` |
| `GET` | `/api/metrics` | — | `SessionStats` (measured latencies, usage, cost estimate) |
| `POST` | `/api/glossary` | `{"entries":[{"ja":"...","vi":"...","note":"..."}]}` | `{"count":N}` |

`/health` is intentionally unauthenticated so the Rust supervisor can poll
readiness before handing over the token. It returns no session content.

### 1.2 WebSocket `/ws`

Connect: `ws://127.0.0.1:<port>/ws?token=<session_token>`.
(The token is in the query string because browsers cannot set headers on a
WebSocket handshake. It is a loopback-only, per-process, ephemeral token and is
never logged — see `docs/security.md` for the threat analysis.)

The server closes with code `4401` if the token is missing or wrong.

All messages are JSON objects with a `type` discriminator and a `ts` field
(float, Unix seconds, backend clock). The canonical schema is
`schemas/events.json`; the TypeScript mirror is
`packages/desktop/src/types/events.ts` and the Python mirror is
`packages/backend/src/jlt/models.py`. These three must stay in sync; a test
asserts the Python models match the JSON schema.

#### Server → client events

```jsonc
// Japanese partial transcript. May be emitted many times per segment.
{
  "type": "transcript.partial",
  "ts": 1760000000.123,
  "session_id": "s_01H...",
  "item_id": "item_003",          // from the OpenAI event, used to join
  "seq": 42,                       // locally assigned, monotonic
  "text": "来週のリリースは"        // full accumulated text, not a delta
}

// Japanese final transcript for a turn.
{
  "type": "transcript.final",
  "ts": 1760000001.880,
  "session_id": "s_01H...",
  "item_id": "item_003",
  "seq": 42,
  "text": "来週のリリースは延期されません。",
  "stt_latency_ms": 940          // commit -> completed, measured
}

// A translation unit. segment_id may cover several merged finals.
{
  "type": "segment.created",
  "ts": 1760000001.900,
  "session_id": "s_01H...",
  "segment_id": "seg_17",
  "seq": 42,
  "source_text": "来週のリリースは延期されません。",
  "source_seqs": [42],            // which transcript finals were merged
  "state": "translating"
}

{
  "type": "segment.translated",
  "ts": 1760000003.410,
  "session_id": "s_01H...",
  "segment_id": "seg_17",
  "seq": 42,
  "source_text": "来週のリリースは延期されません。",
  "target_text": "Bản phát hành tuần tới sẽ không bị hoãn.",
  "state": "translated",
  "translation_latency_ms": 1510, // enqueue -> response, measured
  "e2e_latency_ms": 2450,         // commit -> translated, measured
  "usage": {"input_tokens": 243, "output_tokens": 38}
}

{
  "type": "segment.failed",
  "ts": 1760000006.000,
  "session_id": "s_01H...",
  "segment_id": "seg_17",
  "seq": 42,
  "source_text": "...",
  "state": "failed",
  "reason": "timeout"            // timeout | api_error | cancelled
}

// Capture / connection status. Emitted on every change and every 5 s heartbeat.
{
  "type": "status",
  "ts": 1760000000.0,
  "session_id": "s_01H...",
  "capture": "running",          // idle|starting|running|paused|stopping|stopped|error
  "stt": "connected",            // disconnected|connecting|connected|reconnecting|error
  "translation": "ready",        // ready|degraded|error
  "audio_level_dbfs": -31.2,     // for the level meter; -120 when silent
  "queue_depth": 1,
  "detail": null
}

{
  "type": "session.started",  "ts": ..., "session_id": "...", "source": AudioSource }
{ "type": "session.stopped",  "ts": ..., "session_id": "...", "stats": SessionStats }

{
  "type": "error",
  "ts": ...,
  "session_id": "...",
  "severity": "warning",         // warning | fatal
  "code": "stt_reconnecting",
  "message": "Lost connection to OpenAI; retrying (attempt 2).",
  "retryable": true
}
```

#### Client → server messages

```jsonc
{ "type": "ping" }                       // → {"type":"pong","ts":...}
{ "type": "subscribe", "events": ["*"] } // default is "*"
```

Session control is HTTP, not WebSocket, so that it is auditable and idempotent.

### 1.3 Shared types

```jsonc
AudioSource {
  "id": "app:com.microsoft.teams2",
  "kind": "application",          // application | display
  "name": "Microsoft Teams",
  "bundle_id": "com.microsoft.teams2",
  "pid": 4821,
  "is_recommended": true          // true for known Teams/browser bundles
}

Settings {
  "stt": { "delay": "low", "languages": ["ja"], "prompt": null },
  "translation": {
    "model": "gpt-5.4-mini",
    "reasoning_effort": "none",
    "context_size": 3,
    "timeout_s": 12.0,
    "max_retries": 2,
    "concurrency": 3
  },
  "segmentation": {
    "min_translate_chars": 8,
    "merge_window_ms": 1200,
    "max_merge_chars": 220,
    "incomplete_flush_ms": 2500
  },
  "vad": {
    "energy_threshold_dbfs": -45.0,
    "hangover_ms": 600,
    "min_speech_ms": 300,
    "max_turn_ms": 12000
  },
  "ui": { "theme": "system", "font_size": 18, "always_on_top": true, "show_partials": true },
  "privacy": { "persist_transcripts": false, "persist_audio": false },
  "api_key_present": true         // read-only, never the key itself
}

SessionStats {
  "session_id": "...",
  "started_at": 1760000000.0,
  "duration_s": 3612.4,
  "audio_seconds_sent": 3540.0,
  "segments_final": 412,
  "segments_translated": 410,
  "segments_failed": 2,
  "reconnects": 1,
  "stt_latency_ms":  {"p50": 910, "p95": 1680, "n": 412},
  "translation_latency_ms": {"p50": 1420, "p95": 3100, "n": 410},
  "e2e_latency_ms": {"p50": 2330, "p95": 4900, "n": 410},
  "usage": {"input_tokens": 99000, "output_tokens": 15600},
  "estimated_cost_usd": 1.31
}
```

All latency fields are **measured**, computed from monotonic clocks, and are
absent (`null`) when no sample exists. They are never estimated.

---

## 2. Audio helper control protocol

The Swift binary `teams-audio-tap` is spawned as:

```
teams-audio-tap --list-sources
teams-audio-tap --capture <source_id> --rate 24000 --channels 1 --frame-ms 20
```

- **stdout** — with `--capture`: a raw byte stream of signed 16-bit
  little-endian mono PCM at 24,000 Hz, written in frame-sized chunks
  (20 ms = 480 samples = 960 bytes). Nothing else is ever written to stdout, so
  the stream is byte-exact.
- **stderr** — newline-delimited JSON control messages. One object per line.
- **exit code** — `0` clean stop, `2` permission denied, `3` source not found,
  `1` other error.

With `--list-sources`, stdout carries a single JSON object
`{"sources":[AudioSource,...]}` and the process exits.

Control messages (stderr):

```jsonc
{"type":"ready","rate":24000,"channels":1,"frame_bytes":960}
{"type":"started","source_id":"app:com.microsoft.teams2","name":"Microsoft Teams"}
{"type":"level","rms_dbfs":-28.4}            // ~4 Hz, for the UI meter
{"type":"warning","code":"no_audio","message":"No audio for 10s"}
{"type":"error","code":"permission_denied","message":"Screen Recording permission required"}
{"type":"stopped","frames":180000,"dropped":0}
```

Graceful stop: Python sends `SIGINT`; the helper stops the stream, flushes,
emits `stopped`, and exits 0. Python waits up to 3 s, then `SIGKILL`.

Backpressure: if Python stops reading, the OS pipe fills and the helper's
drain thread blocks. The helper detects a blocked write longer than
`frame_ms * 10`, drops the oldest frames from its ring, and reports them in
`dropped`. The ScreenCaptureKit callback is never blocked.
