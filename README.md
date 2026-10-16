# Japanese Live Translator

Live Japanese → Vietnamese subtitles for Microsoft Teams meetings on macOS.
All inference runs on the OpenAI API — no local Whisper, no local LLM.

```
Teams audio ──ScreenCaptureKit──► 24 kHz mono PCM16
            ──gpt-live-transcribe──► Japanese partials + finals   (fast lane)
            ──gpt-5.4-mini─────────► Vietnamese translation       (accurate lane)
```

## Status

**Phases 0 and 1 are implemented.** Phases 2–6 are specified in
`docs/development-plan.md` and not yet built. Nothing in this repository claims
a verification that was not actually performed — see the verification log in
the development plan.

## Prerequisites

Install these before `make setup`. None are bundled.

| Tool | Version | Install | Needed for |
|---|---|---|---|
| **Xcode Command Line Tools** | — | `xcode-select --install` | Swift compiler, macOS SDKs |
| **Swift** | 5.9+ | ships with the CLT | the audio capture helper |
| **uv** | latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | installs and manages Python 3.12 |
| **Python** | 3.12+ | `uv python install 3.12` | the backend |
| **Node.js** | 20+ | [nodejs.org](https://nodejs.org) or `nvm install 20` | the React UI |
| **Rust** | stable | `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \| sh` | Tauri desktop shell |
| **Xcode** (full) | 15+ | App Store | only for signing/notarizing a release build |

Check what you have:

```bash
make doctor
```

### macOS permission

ScreenCaptureKit requires **Screen Recording** permission even for audio-only
capture. Grant it to the app (or to your terminal, during development) in
System Settings → Privacy & Security → Screen Recording, then restart the app.
Without it, source enumeration exits with code 2 and a clear message.

### OpenAI API key

Needs a project with access to `gpt-live-transcribe` and `gpt-5.4-mini`.
In the shipped app the key is stored in the **macOS Keychain** and never
written to disk or exposed to the UI. For development, copy `.env.example`
to `.env`.

## Setup

```bash
make setup          # Python deps, npm deps, and the Swift helper
make verify-openai  # confirm models and endpoints against the live API
make test
make lint
```

## Verifying audio capture

This is the highest-risk part of the system and the one that cannot be
automatically verified — only an ear can confirm the captured audio is real.

```bash
make capture-build
./scripts/capture_selftest.sh                       # lists available sources
./scripts/capture_selftest.sh app:com.microsoft.teams2 10
afplay "$TMPDIR/jlt-capture-selftest.wav"
```

For Teams in a browser, pick the browser's bundle id instead, e.g.
`app:com.google.Chrome`.

## Layout

```
packages/audio-capture/  Swift + ScreenCaptureKit helper → PCM on stdout
packages/backend/        Python 3.12 FastAPI + asyncio; all logic and secrets
packages/desktop/        React + TypeScript + Vite in a Tauri 2 shell
schemas/events.json      Source of truth for the backend↔UI protocol
scripts/                 Live API verification, capture self-test
docs/                    Requirements, architecture, API, security, plan,
                         OpenAI verification, benchmarks
```

## Documentation

| Document | What it covers |
|---|---|
| [docs/requirements.md](docs/requirements.md) | Scope, functional and non-functional requirements |
| [docs/architecture.md](docs/architecture.md) | Process topology, components, design decisions |
| [docs/api-design.md](docs/api-design.md) | Local HTTP/WebSocket API and the audio helper protocol |
| [docs/openai-verification.md](docs/openai-verification.md) | What was verified against OpenAI's docs, and what was not |
| [docs/security.md](docs/security.md) | Threat model, key handling, data retention |
| [docs/development-plan.md](docs/development-plan.md) | Phase status and the verification log |
| [docs/benchmarks.md](docs/benchmarks.md) | Benchmark procedure (no results yet) |

## Costs

At published rates, roughly **$1.30 per meeting hour** — dominated by STT at
$0.017/minute. See `docs/openai-verification.md` §4. This is a projection from
unit prices, not measured spend.

## Privacy

Meeting audio and transcripts are sent to OpenAI; that is inherent to the
design and the app says so before the first capture. Nothing is persisted
locally by default, capture never starts on its own, and the capture state is
always visible. Details in [docs/security.md](docs/security.md).
