# Development Plan

Status legend: ✅ done & verified · 🟡 implemented, verification pending ·
⬜ not started · ⛔ blocked

Last updated: 2026-10-16 (toolchain installed; Phase 0 accepted)

## Environment status

| Tool | Required | Present on this machine | Impact |
|---|---|---|---|
| Python | 3.12+ | 3.12.15 via uv ✅ | Backend tests run |
| uv | any | 0.13.1 ✅ | Pinned in `.python-version` |
| Rust / cargo | stable | 1.99.1 ✅ | Tauri can build |
| Swift | 5.9+ | 6.1.2 ✅ | Helper compiles |
| Xcode | full IDE | Command Line Tools only | SwiftPM works; only codesigning/notarizing a release needs full Xcode |
| Node | 20+ | 24.20.0 ✅ | Frontend builds |
| Screen Recording permission | granted | granted ✅ | Source enumeration and capture work |

Note: `uv` installs to `/opt/homebrew/bin`, which is not on a non-login shell's
PATH on this machine. Prefix make targets with
`export PATH="/opt/homebrew/bin:$PATH"` if `make doctor` reports uv missing.
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
| Live OpenAI verification against a real key | 🟡 pending — needs `OPENAI_API_KEY`; run `make verify-openai` |

**Acceptance: MET.** `make test` → 75 backend + 4 frontend tests pass.
`make lint` → ruff, ruff format, mypy strict and eslint/tsc all clean.

## Phase 1 — Audio capture (highest technical risk)

| Task | Status |
|---|---|
| ScreenCaptureKit per-application audio capture | ✅ implemented |
| Source enumeration incl. Teams + browser bundles | ✅ implemented |
| Conversion to 24 kHz mono PCM16 | ✅ implemented |
| Lock-free ring + non-blocking drain thread | ✅ implemented |
| JSON control protocol on stderr | ✅ implemented |
| Python `AudioCaptureService` + `AudioStreamManager` + VAD | ✅ implemented |
| Builds with `swift build -c release` | ✅ |
| Enumerates sources, finds Teams | ✅ — `app:com.microsoft.teams2` detected, ranked as recommended |
| Opens a capture stream on Teams and emits aligned 24 kHz mono PCM16 | ✅ — 7.64 s captured, 382 frames, 0 dropped |
| Graceful SIGINT stop, clean exit, no orphan process | ✅ |
| Carries an **audible** signal (not just a correctly-shaped silent stream) | ⛔ manual — see below |
| 30-minute continuous capture | ⛔ manual |

**Acceptance: PARTIALLY MET.** Everything that can be verified without a live
meeting is verified. The outstanding item is specific and important: the test
capture above was byte-perfect but **entirely silent** (peak sample 0, −120
dBFS) because Teams was not playing any audio at the time. A correctly-shaped
stream of zeros proves the format and framing path, not the audio tap itself.

To close it, play audio through Teams (or a browser tab) and run:

```
./scripts/capture_selftest.sh app:com.microsoft.teams2 10
afplay "$TMPDIR/jlt-capture-selftest.wav"
```

Non-zero peak amplitude plus an audible WAV closes this item. Until then it
stays open, and Phase 1 is not called complete.

## Phase 2 — Japanese STT

| Task | Status |
|---|---|
| `OpenAIRealtimeSTTProvider` — session.update, append, commit | ✅ |
| URL-candidate fallback + sticky resolved URL | ✅ |
| Delta accumulation and final normalization | ✅ |
| Duplicate suppression (repeat completion, late delta, re-delivered turn) | ✅ |
| Ordering by commit-assigned `seq`, not arrival | ✅ |
| Reconnect with exponential backoff + full jitter, config re-sent | ✅ |
| `TranscriptManager` — publishes `transcript.partial` / `transcript.final` | ✅ |
| Measured STT latency (commit → completed) | ✅ |
| Unit tests against a fake WebSocket | ✅ — 41 tests |
| Live smoke test against the real API | ✅ — `scripts/smoke_stt.py` |

**Acceptance: MET.** `make verify-openai` resolved the WebSocket URL to
`wss://api.openai.com/v1/realtime?intent=transcription` (server replied
`session.created`), and confirmed both models are available to the project.

`scripts/smoke_stt.py` then streamed synthesised Japanese through the
production provider and got back correct partials and finals, in order, with no
duplicates. First partial 949–1057 ms after audio start; commit→final
682–685 ms. **n=2 on synthetic speech — indicative, not a benchmark.**

One real finding, recorded in `docs/openai-verification.md` §3b: a spoken
ticket id does not arrive as a written identifier. Keyword hints improved it
(kanji numerals → digits) but did not restore `ABC-1234`. Identifier
reconstruction is therefore a Phase 3 responsibility, not something to assume
the STT layer provides.

## Phase 3 — Translation

| Task | Status |
|---|---|
| `SegmentManager` — merge, completeness, flush deadlines | ✅ |
| Deduplication by transcript `seq` (never by content) | ✅ |
| Identifier reconstruction from the glossary | ✅ |
| `OpenAITranslationProvider` — Responses, retries, timeouts | ✅ |
| Prompt: negation, identifiers, units, conditionals, glossary, context | ✅ |
| `TranslationQueue` — bounded queue, bounded concurrency | ✅ |
| Reorder buffer with a bounded stall release | ✅ |
| Golden dataset, 18 cases across the brief's five priorities | ✅ |
| Offline tests | ✅ — 121 tests |
| Live golden evaluation | ✅ — **18/18**, P50 1096 ms |

**Acceptance: MET.** `make eval-translation` passes every golden case against
the live model. Measured translation-stage latency is recorded in
`docs/benchmarks.md`; it is a component measurement, not the end-to-end number.

The first evaluation run found two mislabelled golden cases and one real output
defect (Japanese units surviving into Vietnamese). All three are documented in
`docs/benchmarks.md`; the labels were corrected and the prompt was tightened.

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
| 2026-10-16 | Toolchain inventory | `make doctor` | Python 3.9.6, Swift 6.1.2, no cargo, CLT only |
| 2026-10-16 | Toolchain after user install | `make doctor` | uv 0.13.1, Python 3.12.15, cargo 1.99.1, Swift 6.1.2, Node 24.20.0 |
| 2026-10-16 | Swift helper builds | `swift build -c release` | Build complete, no warnings |
| 2026-10-16 | Backend tests | `uv run pytest -q` | **75 passed** in 4.99 s |
| 2026-10-16 | Frontend tests | `npm test` | **4 passed** |
| 2026-10-16 | Lint + types | `ruff check`, `ruff format --check`, `mypy` (strict), `eslint`, `tsc --noEmit` | All clean; mypy 15 source files |
| 2026-10-16 | Source enumeration | `teams-audio-tap --list-sources` | exit 0; Teams (`com.microsoft.teams2`, pid 44832) and Chrome detected and ranked recommended |
| 2026-10-16 | Permission denial path | same, before permission was granted | exit 2 + `permission_denied` control message, as specified |
| 2026-10-16 | Argument validation | `--rate 48000` | rejected, exit 64 |
| 2026-10-16 | Phase 2 STT suite | `uv run pytest -q` | **116 passed** (75 → 116; +29 provider, +12 transcript manager) |
| 2026-10-16 | Phase 2 lint + types | `ruff check`, `ruff format --check`, `mypy --strict` | All clean, 17 source files |
| 2026-10-16 | Live OpenAI verification | `make verify-openai` | ✅ both models available; WS URL resolved to `?intent=transcription` (`session.created`); Responses round-trip correct, negation and ticket id preserved; `reasoning_tokens: 0` confirms `effort: none` |
| 2026-10-16 | Live STT smoke test | `scripts/smoke_stt.py` | ✅ 2/2 finals, seqs `[1, 2]`, 0 duplicates; first partial 949–1057 ms; commit→final 682–685 ms (synthetic speech, n=2) |
| 2026-10-16 | Keyword-hint efficacy | ad-hoc A/B on the same sentence | ⚠️ partial: `千二百三十四` → `1234`, letters stayed `A B C`; see openai-verification.md §3b |
| 2026-10-16 | Phase 3 suite | `uv run pytest -q` | **237 passed** (116 → 237) |
| 2026-10-16 | Phase 3 lint + types | `ruff`, `ruff format`, `mypy --strict` | All clean, 21 source files |
| 2026-10-16 | Live golden evaluation | `make eval-translation` | ✅ **18/18** after fixing 2 mislabelled cases and 1 real prompt defect; P50 1096 ms, P95 1915 ms, 8,022 in / 290 out tokens |
| 2026-10-16 | Live Teams capture | `--capture app:com.microsoft.teams2`, 8 s | 366,720 bytes = 7.64 s at 24 kHz mono 16-bit; 382 frames; **0 dropped**; clean SIGINT exit 0. **Signal was silent (peak 0)** — Teams had no audio playing. Audibility remains unverified. |

Entries are appended only after a command actually runs. No row is written in
advance.

## Open issues

1. **Audio tap audibility unverified.** The capture path produces a correctly
   framed stream, but every sample so far has been zero because no audio was
   playing. This is the single remaining Phase 1 acceptance item. See Phase 1
   above for the one-command check.
2. **Spoken identifiers do not survive STT as written identifiers.**
   Confirmed live. Must be handled in Phase 3 by the translation prompt plus a
   deterministic normalisation pass, with the case in the golden dataset.
   Not yet tested with human speech.
3. **Full Xcode absent.** Only matters for signing and notarizing a release
   build; SwiftPM itself works with the Command Line Tools.
4. **`uv` is not on the non-login shell PATH.** See the environment table.
