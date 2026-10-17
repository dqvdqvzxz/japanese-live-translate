# CLAUDE.md — Japanese Live Translator

Live Japanese→Vietnamese subtitles for Microsoft Teams on macOS.
All inference is OpenAI API. **No local Whisper, no local LLM, no DeepSeek.**

## Layout

```
packages/audio-capture/   Swift helper (ScreenCaptureKit) → raw PCM on stdout
packages/backend/         Python 3.12 FastAPI + asyncio, all business logic
packages/desktop/         React + TypeScript + Vite inside Tauri 2
schemas/events.json       Single source of truth for backend↔UI events
scripts/verify_openai.py  Probes the live API; the only source of API truth
docs/                     requirements, architecture, api-design, security,
                          development-plan, openai-verification, benchmarks
```

## Commands

```bash
make setup          # install deps in all three packages
make test           # pytest + vitest
make lint           # ruff + mypy + eslint + tsc
make dev            # backend + vite + tauri dev
make verify-openai  # live API probe (needs OPENAI_API_KEY)
make capture-build  # build the Swift helper
make capture-selftest  # 10 s capture → WAV, for ear-checking
make release        # ad-hoc signed, shareable .dmg in dist/
```

## Non-negotiable constraints

These come from verified API behaviour (`docs/openai-verification.md`) and from
the security model. Breaking them breaks the product.

1. **Audio is 24 kHz, mono, signed 16-bit little-endian.** The STT model accepts
   nothing else. Do not "improve" this to 48 kHz or stereo.
2. **Client-side VAD is mandatory.** `gpt-live-transcribe` has no server VAD.
   Without `input_audio_buffer.commit` there are no final transcripts and
   therefore no translations.
3. **Never order segments by event arrival.** The API does not guarantee
   completion ordering. Order by the locally assigned `seq`, assigned at commit.
4. **The STT session uses `languages: ["ja"]`,** never the singular `language`.
   Sending both is rejected.
5. **Never log meeting content** — not transcripts, not translations, not at
   DEBUG. Log counts, latencies, and IDs only.
6. **Never let the API key reach the renderer** or a command line. Keychain →
   Rust → child environment only.
7. **Partials are droppable; finals are not.** Every queue policy follows this.
8. **Do not persist audio or transcripts** unless the user opted in.
9. **Capture never starts without an explicit user action.**
10. **Never fabricate a benchmark, a test result, or an API behaviour.** If it
    was not executed, mark it pending.
11. **Tauri denies core commands without a capability.** Custom commands in
    `invoke_handler` need none, which masks the gap — if a built-in Tauri
    behaviour silently does nothing, check `src-tauri/capabilities/` first.
    That is why window dragging appeared broken for three attempts.
12. **Do not add `keychain-access-groups`.** An ad-hoc signature cannot
    satisfy it and macOS SIGKILLs the app at launch. The default keychain
    needs no entitlement.

## Conventions

- Python: 3.12, `from __future__ import annotations`, full type hints, ruff +
  mypy strict. Async everywhere; no blocking calls on the event loop.
- Dependency injection via constructor arguments, not module globals. Every
  component takes its collaborators as protocol-typed parameters so tests can
  substitute fakes.
- Events are `pydantic` models mirroring `schemas/events.json`. A test asserts
  they stay in sync.
- Swift: no allocation or I/O inside the ScreenCaptureKit callback.
- TypeScript: strict mode; event types mirror the same schema.

## Where things live

| Concern | File |
|---|---|
| Event/data models | `packages/backend/src/jlt/models.py` |
| Component protocols | `packages/backend/src/jlt/interfaces.py` |
| STT WebSocket | `packages/backend/src/jlt/stt/openai_realtime.py` |
| VAD + framing | `packages/backend/src/jlt/audio/stream_manager.py` |
| Merge/dedupe rules | `packages/backend/src/jlt/segments/manager.py` |
| Translation prompt | `packages/backend/src/jlt/translation/provider.py` |
| Golden dataset | `packages/backend/tests/data/golden_ja_vi.json` |
| Session history and files | `packages/backend/src/jlt/history.py` |
| Summary prompt | `packages/backend/src/jlt/translation/summary.py` |
| Native commands | `packages/desktop/src-tauri/src/main.rs` |
| Tauri permissions | `packages/desktop/src-tauri/capabilities/default.json` |
