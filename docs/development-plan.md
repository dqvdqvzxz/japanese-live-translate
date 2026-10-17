# Development Plan

Status legend: ✅ done & verified · 🟡 implemented, verification pending ·
⬜ not started · ⛔ blocked

Last updated: 2026-10-17 (Phases 0–6 implemented; shareable release built)

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
| Carries an **audible** signal through the system-audio (display) filter | ✅ peak 7517, −32.7 dBFS |
| Carries an audible signal through the **per-application** filter | ✅ Teams test call: peak 25053, −23.7 dBFS |
| 30-minute continuous capture | ⛔ manual |

**Acceptance: PARTIALLY MET.** The tap demonstrably captures real audio: a
system-audio capture taken while a sound played returned peak 7517 and
−32.7 dBFS, proving the whole ScreenCaptureKit → AVAudioConverter → PCM16
path works, not just its shape.

What remains unproven is narrower but still real: that path was exercised
through the **display** content filter. The per-application filter — the one
used for Teams — differs only in how `SCContentFilter` is constructed, but it
has still only ever been run against a silent Teams, so it has not been shown
to carry audio. It is not claimed to.

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

| Task | Status |
|---|---|
| Subtitle view: Japanese above, Vietnamese beneath its own source | ✅ |
| Live partial rendered separately and replaced wholesale | ✅ |
| Start / Stop / Pause / Resume | ✅ |
| Always-on-top toggle (Tauri command wired in Phase 5) | ✅ |
| Settings: theme, font size, partials, STT delay, privacy | ✅ |
| Session history with measured statistics | ✅ |
| Connection and capture status, always visible, with a level meter | ✅ |
| Dark mode following the system, overridable | ✅ |
| Mock event stream for development without a backend | ✅ |
| Subtitle state reducer tests | ✅ — 16 tests |

**Acceptance: MET.** `npm test` green (20 tests), `tsc --noEmit` clean,
`eslint` clean, and the UI was rendered against the mock stream and inspected
at 560px and 360px.

Found and fixed while looking at it: at narrow widths the source `<select>`
collapsed to a bare chevron, hiding which application's audio was being sent
to OpenAI. That is a stated privacy requirement (`docs/security.md` §7), not a
cosmetic issue, so the control bar now wraps and gives the source selector its
own row below 460px.

Note: the Tauri shell itself (Rust, Keychain, backend supervision) is Phase 5.
Without it the UI falls back to the mock stream rather than pretending to be
connected.

## Phase 5 — Integration

| Task | Status |
|---|---|
| `SettingsManager` — atomic writes, mode 0600, corrupt-file recovery | ✅ |
| `HistoryManager` — bounded in memory, SQLite only on opt-in | ✅ |
| `SessionManager` — full pipeline wiring and ordered shutdown | ✅ |
| `WebSocketGateway` — token auth, origin checks, event fan-out | ✅ |
| Process entry point with a single-line stdout handshake | ✅ |
| Tauri shell: Keychain, backend supervision, always-on-top | ✅ compiles |
| Integration tests | ✅ — 54 tests (22 session, 28 gateway, 4 path/history) |
| Live end-to-end run against real Teams and real OpenAI | ✅ |

**Acceptance: MET**, with one caveat named below.

Verified against a running backend process, not just in tests:

| Check | Result |
|---|---|
| stdout carries exactly one handshake line | ✅ 1 line, `{"type":"listening","port":…,"token":…}` |
| Logs are JSON on stderr, no errors | ✅ |
| `/health` works unauthenticated | ✅ |
| `/api/*` without a token | ✅ 401 |
| `/api/*` with a token | ✅ 200, `api_key_present: true`, no `sk-` anywhere in the body |
| Foreign `Origin` with a valid token | ✅ 403 |
| Listening socket | ✅ `127.0.0.1` only, never `0.0.0.0` |
| Source enumeration via ScreenCaptureKit | ✅ 36 sources, Teams and Chrome ranked first |
| `POST /api/session/start` on real Teams | ✅ helper spawned, `stt connected` on the verified URL |
| Silent source | ✅ `no_audio` warning after 10 s, session stayed up |
| `POST /api/session/stop` | ✅ **0 orphan `teams-audio-tap`**, **0 established sockets** |
| Reported stats | ✅ honest: `audio_seconds_sent: 0.0`, `stt_latency_ms.n: 0`, `p50: null` |

Three real defects were found by running it rather than by testing it, all
fixed with regression tests:

1. **The audio path was dead.** `SessionManager` pushed frames into the stream
   manager's queue but never started the worker that drains it, so audio would
   have been queued and never sent to OpenAI. Caught by the first integration
   test that asserted a commit reached the provider.
2. **The redaction filter broke printf logging.** It coerced every log argument
   to `str`, so uvicorn's `"Started server process [%d]" % (pid,)` raised on
   every startup. Now only `str` arguments are scrubbed.
3. **The audio helper could never be found.** `default_helper_path` counted
   parent directories and was off by one, producing
   `packages/packages/audio-capture`. It now walks up looking for the
   monorepo layout.

**Caveat:** the Tauri shell compiles (`cargo check`, `clippy -D warnings`,
`cargo fmt --check` all clean) but has **not been run**. Launching it needs a
full app build; that is Phase 6. Until then the Keychain path and the
supervisor handshake are unexercised, and the UI falls back to the mock
stream.

## Phase 6 — Testing and release

| Task | Status |
|---|---|
| Test against real Microsoft Teams | ✅ capture verified, peak 25053 |
| Test browser audio | ✅ Chrome captured at peak 8898, −15.1 dBFS |
| Test with headphones | ✅ captured at peak 7525 with output on a JBL headset |
| Intel Mac support | ✅ universal build; **running it on an Intel Mac is still untested** |
| Network interruption and reconnection | ✅ 6 tests against a real local WebSocket server |
| Latency benchmarks | ✅ recorded in `docs/benchmarks.md` |
| 60-minute continuous run | ✅ 60.2 min, no leak, 0 orphans, 1 auto-recovered reconnect |
| Package a macOS application | ✅ self-contained `.app` (24.3 MB) and `.dmg` (20.6 MB) |
| Installation and usage documentation | ✅ `docs/installation.md` |
| API key entry in the packaged app | ✅ Keychain input in Settings, with restart |
| Actionable Screen Recording permission dialog | ✅ opens the exact System Settings pane |
| Info section: attribution and data handling (Japanese) | ✅ |
| Code signing and notarization | ⛔ needs an Apple Developer ID |
| Shareable release artifact | ✅ `make release` → ad-hoc signed `.dmg` |

**Acceptance: PARTIALLY MET.** Everything that can be verified on this machine
without another person in a meeting is verified.

### Packaging

`make bundle` now produces a genuinely self-contained app: it builds the Swift
helper, freezes the backend with PyInstaller into a 12 MB standalone binary,
and copies both into `Contents/Resources/`. Verified by launching the bundle
with `PATH=/usr/bin:/bin` — no Python, no `uv`, no source checkout — and
watching the bundled backend come up and serve `/health`.

The app is unsigned and Apple Silicon only.

### Process cleanup — all three exit paths

A real bug was found here. The Rust shell killed the backend on the window's
`Destroyed` event, which does not fire on a SIGTERM or a crash. The first
packaged build left an **orphaned backend** after the app exited, which would
have kept the capture helper alive and kept capturing after the user believed
they had closed the app.

Fixed in two layers, because only the second survives a SIGKILL: the shell now
also handles `RunEvent::Exit`, and the backend independently watches
`JLT_PARENT_PID` and exits when that process disappears.

| Exit path | app | backend | helper |
|---|---|---|---|
| Normal quit (`osascript quit`) | 0 | 0 | 0 |
| `SIGKILL` on the app | 0 | 0 | 0 |
| Supervisor dies with no cleanup chance | — | 0 | 0 |

### Known environment trap

`uv run --env-file <path>` splits its value on whitespace to accept several
files, so an **absolute path containing a space** is silently truncated. This
project's own path contains one. Scripts source `.env` into the environment
instead; the Makefile's relative `--env-file ../../.env` is unaffected.

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
| 2026-10-17 | Phase 4 frontend | `npm test`, `tsc --noEmit`, `eslint` | **20 passed**, type and lint clean |
| 2026-10-17 | Phase 4 visual check | dev server + browser at 560px and 360px | ✅ subtitles, partial, failed line, warning banner, settings and history panels all render; narrow-width source-name bug found and fixed |
| 2026-10-17 | Phase 5 suite | `uv run pytest -q` | **291 passed** (237 → 291) |
| 2026-10-17 | Phase 5 lint + types | `ruff`, `mypy --strict`, `eslint`, `tsc`, `cargo clippy -D warnings`, `cargo fmt --check` | All clean |
| 2026-10-17 | Live backend process | `python -m jlt` + curl | ✅ handshake, auth, origin, loopback-only bind, no key leak |
| 2026-10-17 | Live session on real Teams | `POST /api/session/start` then `/stop` | ✅ helper spawned, STT connected, `no_audio` warning correct, **0 orphans and 0 sockets after stop** |
| 2026-10-17 | Packaged app connects to its own backend | launched the `.app`, read its log | ✅ `WebSocket /ws [accepted]` — real backend, not the mock stream — **after** fixing two bugs that made the packaged build unusable |
| 2026-10-17 | 60-minute stability run | `make stability MINUTES=60` | ✅ 60.2 min; RSS 17.0 MB → 5.5 MB (no leak); exactly 1 helper throughout; 0 orphans after stop; **OpenAI dropped the idle session at minute 39 and the provider recovered in 1.2 s unaided** |
| 2026-10-17 | Reconnect over a real socket | `pytest tests/test_reconnect_integration.py` | ✅ 6 passed — connect, configure, drop, reconnect, reconfigure, audio resumes, silent-server timeout |
| 2026-10-17 | macOS packaging | `make bundle` | ✅ `.app` 24.3 MB, `.dmg` 20.6 MB, self-contained; runs with `PATH=/usr/bin:/bin` |
| 2026-10-17 | Orphan processes on exit | normal quit, SIGKILL, supervisor death | ✅ 0 orphans on all three **after** fixing a real orphaned-backend bug |
| 2026-10-17 | Headphone output | capture while default output was a JBL Quantum800 | ✅ peak 7525, −33.2 dBFS — confirms capture taps the application's audio graph, not the output device |
| 2026-10-17 | Browser audio | throwaway Chrome instance playing a Web Audio tone | ✅ peak 8898, −15.1 dBFS through the per-application filter — the Teams-on-web path works |
| 2026-10-17 | Intel backend | `make freeze-backend-x86`, then ran it | ✅ x86_64 binary, handshake and `/health` OK under Rosetta |
| 2026-10-17 | Universal capture helper | `swiftc` per arch + `lipo` | ✅ fat binary, both slices present |
| 2026-10-17 | Shareable release | `make release` | ✅ `dist/Japanese Live Translator 0.1.0.dmg`, 21 MB; mounted and verified as a recipient would: deep signature valid, backend and capture helper present |
| 2026-10-17 | Ad-hoc signed app launches | ran the signed bundle | ✅ **after** removing `keychain-access-groups`, which an ad-hoc signature cannot satisfy — macOS SIGKILLed (exit 137) every build that carried it |
| 2026-10-17 | Keychain still reachable without that entitlement | dummy item + app launch | ✅ backend received the key; dummy removed afterwards |
| 2026-10-17 | Window drag capability | inspected `gen/schemas/capabilities.json` | ✅ `core:window:allow-start-dragging` granted — absent before, which is why dragging silently did nothing |
| 2026-10-17 | UI against the real backend | browser at `localhost:5173` + live backend | ✅ real sources listed, session started from the UI, helper spawned, STT connected, clean stop. Found and fixed: missing CORS headers, a stale placeholder, and the source selector not adopting a running session |
| 2026-10-17 | Per-application Teams capture | helper, Teams test call, 5.7 s | ✅ **peak 25053, −23.7 dBFS** — the Electron-helper concern was unfounded |
| 2026-10-17 | Full pipeline, real audio + live OpenAI | 78 s session | ✅ 6/6 translated, 0 failed, 0 orphans; stt p50 637 ms, translate p50 1250 ms, e2e p50 4120 ms / p95 4843 ms. Content invalid (captured unrelated system audio) |
| 2026-10-17 | Audible capture (display filter) | helper + `afplay`, 6.08 s | ✅ **peak 7517, RMS −32.7 dBFS** — real audio, not a shaped silent stream |
| 2026-10-17 | Helper JSON key set | `--list-sources` across 36 sources | ✅ after fixing Swift to emit explicit nulls; previously `bundle_id`/`pid` were omitted for displays |
| 2026-10-16 | Live Teams capture | `--capture app:com.microsoft.teams2`, 8 s | 366,720 bytes = 7.64 s at 24 kHz mono 16-bit; 382 frames; **0 dropped**; clean SIGINT exit 0. **Signal was silent (peak 0)** — Teams had no audio playing. Audibility remains unverified. |

Entries are appended only after a command actually runs. No row is written in
advance.

## Open issues

0. **Window dragging has not been confirmed by hand.** The missing capability
   is fixed and the ACL now grants `start-dragging`, but dragging a native
   window cannot be automated from here — it needs a human to try it. Two
   earlier attempts at this bug looked right and were not, so this stays open
   until someone drags the window.
1. **No clean Japanese end-to-end run yet.** The pipeline has been measured on
   real audio (see `docs/benchmarks.md`), but that run captured unrelated
   system audio rather than Japanese. A valid content test needs Japanese
   audio playing with nothing else making sound.
2. **OpenAI drops idle realtime sessions** — observed once after ~38.6 minutes
   of silence. Recovery is automatic and took 1.2 s, but it means reconnection
   is load-bearing for long meetings, not a safety net.
3. **Segmentation dominates end-to-end latency for short utterances** —
   roughly 2.2 s of the 4.1 s p50. `incomplete_flush_ms` is the knob; it is a
   latency-versus-quality trade and needs tuning against real meeting speech.
2. **Spoken identifiers do not survive STT as written identifiers.**
   Confirmed live. Must be handled in Phase 3 by the translation prompt plus a
   deterministic normalisation pass, with the case in the golden dataset.
   Not yet tested with human speech.
3. **Full Xcode absent.** Only matters for signing and notarizing a release
   build; SwiftPM itself works with the Command Line Tools.
4. **`uv` is not on the non-login shell PATH.** See the environment table.
5. **The Intel build has never been run on an Intel Mac.** Both slices are
   present and the x86_64 backend runs under Rosetta here, which is a good
   proxy but not the same thing as a real Intel machine.
