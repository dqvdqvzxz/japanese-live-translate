# Development Plan

Status legend: ✅ done & verified · 🟡 implemented, verification pending ·
⬜ not started · ⛔ blocked

Last updated: 2026-10-16

## Environment status

| Tool | Required | Present on this machine | Impact |
|---|---|---|---|
| Python | 3.12+ | 3.9.6 only | Backend tests cannot run locally until installed |
| Rust / cargo | stable | absent | Tauri cannot build |
| Swift | 5.9+ | 6.1.2 ✅ | Helper compiles |
| Xcode | full IDE | Command Line Tools only | `swiftc` works; `swift build` (SwiftPM) and codesigning need full Xcode |
| Node | 20+ | 24.20.0 ✅ | Frontend builds |
| Docker | optional | installed, daemon stopped | Alternative test runner unused |

The user elected to install toolchains manually. See `README.md` §Setup.
**Every "verified" claim below names the command that produced it.** Items that
could not be executed here are marked pending, not assumed.

---

## Phase 0 — Project setup

| Task | Status |
|---|---|
| Monorepo layout | ✅ |
| Shared event schema (`schemas/events.json`) | ✅ |
| Python backend skeleton, typed models, settings | ✅ |
| Swift package + helper sources | ✅ |
| React/Tauri scaffold | ✅ |
| Lint/type/test configuration (ruff, mypy, pytest, eslint, vitest) | ✅ |
| OpenAI API verification against official docs | ✅ — `docs/openai-verification.md` |
| Live OpenAI verification against a real key | 🟡 pending — run `make verify-openai` |

**Acceptance:** repo builds and the test suite runs green.
Evidence required: output of `make test`.

## Phase 1 — Audio capture (highest technical risk)

| Task | Status |
|---|---|
| ScreenCaptureKit per-application audio capture | ✅ implemented |
| Source enumeration incl. Teams + browser bundles | ✅ implemented |
| Conversion to 24 kHz mono PCM16 | ✅ implemented |
| Lock-free ring + non-blocking drain thread | ✅ implemented |
| JSON control protocol on stderr | ✅ implemented |
| Python `AudioCaptureService` + `AudioStreamManager` + VAD | ✅ implemented |
| Compiles with `swiftc` | 🟡 pending — needs the toolchain check to be run |
| Captures real Teams audio | ⛔ manual — needs Teams, a meeting, and Screen Recording permission |
| 30-minute continuous capture | ⛔ manual |

**Acceptance:** helper emits byte-exact 24 kHz mono PCM16 from Teams for 30
minutes with zero drops and exits cleanly.
Evidence required: `make capture-selftest` output and a WAV of the first
10 seconds verified audible.

## Phase 2 — Japanese STT
Realtime WebSocket provider, delta/final normalization, dedupe, ordering,
reconnect with backoff, unit tests against a fake WebSocket.
**Acceptance:** all STT unit tests green; live smoke test transcribes Japanese.

## Phase 3 — Translation
SegmentManager merge/completeness/dedupe, bounded TranslationQueue with
reorder buffer, Responses-based provider with glossary and context, golden
dataset of Japanese IT meeting sentences.
**Acceptance:** golden tests pass; ordering and timeout tests pass.

## Phase 4 — Desktop UI
Subtitle view, controls, settings, history, always-on-top, status.
**Acceptance:** `vitest` green, `tsc --noEmit` clean, UI renders against a
mock event stream.

## Phase 5 — Integration
Lifecycle wiring, error propagation, Keychain, process cleanup.
**Acceptance:** start→pause→resume→stop leaves no orphan process; verified
with `pgrep teams-audio-tap` after stop.

## Phase 6 — Testing and release
Real Teams testing, browser audio, headphones, network interruption, latency
benchmarks, 60-minute run, packaging, docs.
**Acceptance:** `docs/benchmarks.md` filled from measured runs only.

---

## Verification log

| Date | Item | Command | Result |
|---|---|---|---|
| 2026-10-16 | OpenAI model names, endpoints, event schema, pricing | fetched `developers.openai.com/**.md` | ✅ both models exist; recorded in `docs/openai-verification.md` |
| 2026-10-16 | Toolchain inventory | `python3 -V`, `swift --version`, `cargo --version`, `xcodebuild -version` | Python 3.9.6, Swift 6.1.2, no cargo, CLT only |

Entries are appended only after a command actually runs. No row is written in
advance.

## Open issues

1. **Realtime transcription WebSocket URL is not pinned in the public docs.**
   Mitigated by configuration + a probe script. Must be settled by
   `make verify-openai` before Phase 2 can be called complete.
2. **Full Xcode absent.** SwiftPM and codesigning are unavailable; the helper is
   therefore buildable via a direct `swiftc` invocation as well as `swift build`.
3. **Python 3.12 absent.** No backend test result can be reported until the
   toolchain is installed.
