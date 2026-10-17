# Installation and Usage

For end users on the "Install" section; for developers from "Building from
source" onward.

---

## Install

### 1. Requirements

- macOS 13 (Ventura) or later. ScreenCaptureKit audio capture needs it.
- An OpenAI API key on a project with access to `gpt-live-transcribe` and
  `gpt-5.4-mini`.
- Microsoft Teams (desktop or in a browser).

### 2. Install the app

Open `Japanese Live Translator_0.1.0_aarch64.dmg` and drag the app to
Applications.

The build is **not signed or notarized**, so Gatekeeper will refuse it on first
launch. To open it anyway: right-click the app → **Open** → **Open** in the
dialog. You only need to do this once. If you prefer, System Settings →
Privacy & Security has an "Open Anyway" button after the first blocked attempt.

### 3. Grant Screen Recording permission

macOS requires **Screen Recording** permission for ScreenCaptureKit, even
though this app only ever reads audio and never captures a single video frame.
There is no narrower permission available.

System Settings → Privacy & Security → **Screen Recording** → enable
**Japanese Live Translator**, then quit and reopen the app.

Without it the app will say so explicitly rather than silently capturing
silence.

### 4. Add your API key

Open **Settings** → **OpenAI API key**, paste your key, and press **Save to
Keychain**. The app then offers to restart, which it needs to do: the backend
is handed the key in its environment when it starts, so a key added afterwards
only takes effect on the next launch.

The key is stored in the **macOS Keychain** and nowhere else — not in a file,
not in the app's preferences. The input is cleared as soon as it is saved and
the key is never read back into the window.

Capture cannot start until a key is present.

---

## Using it

1. Start or join your Teams meeting.
2. In the source dropdown, pick where the audio is coming from:
   - **Microsoft Teams** for the desktop app;
   - your **browser** (Chrome, Edge, Safari, …) for Teams on the web;
   - **Entire system audio** if you are not sure, or if the per-application
     capture produces nothing.
   Recommended sources are marked with a star and sorted to the top.
3. Press **Start**.
4. Japanese appears as it is spoken, in grey italic while the model is still
   revising it. Vietnamese appears underneath each finished sentence.

### Controls

| Control | What it does |
|---|---|
| **Start / Stop** | Begin and end capture. Nothing is captured until you press Start. |
| **Pause / Resume** | Stop sending audio without tearing down the session. The sentence in progress is still finished and translated rather than discarded. |
| **Pin** | Keeps the window above other windows, so it can sit beside the meeting. |
| **Settings** | Theme, subtitle size, transcription latency, privacy, API key. |
| **History** | Opens a column beside the subtitles listing past meetings by date, newest first, with what each cost. Selecting one shows it in the main area, with its summary, action items and transcript. |

### Naming and saving

When you press Stop the app asks what to call the recording. Skipping is fine
— it gets a dated name — and you can rename it later from History.

Turn on **Settings → Privacy → Save transcripts to this Mac** and pick a
folder. Each finished meeting is written there as a Markdown file named after
the recording, and renaming a recording renames its file. Nothing is written
anywhere until you choose that folder.

### Reading the status bar

The bar along the bottom is always visible and always tells you what is
happening:

- a **green dot** and "Capturing" means audio is being sent to OpenAI;
- "OpenAI: Connected / Reconnecting…" is the transcription connection;
- the small **level meter** moves when sound is actually being captured. If it
  stays flat while people are talking, you have picked the wrong source.

### Tuning

If subtitles feel slow, Settings → Transcription → latency. `minimal` and
`low` show text sooner; `medium` and above give the model more audio before it
commits, which can improve accuracy. Benchmark against your own meetings; the
right setting depends on the speakers and the audio quality.

If short interjections are being held back too long, that is the segmentation
policy waiting to merge them into a complete sentence. See
`docs/benchmarks.md` — for short utterances this hold, not the API, is the
largest part of the delay.

---

## Privacy, in plain terms

- Audio and transcripts **are sent to OpenAI** to be processed. That is how the
  product works and there is no local mode.
- Nothing is written to this Mac by default — no audio ever, and no transcripts
  unless you turn that on in Settings, which says so when you do.
- Capture never starts on its own. There is no autostart and no hidden hotkey.
- macOS shows its own recording indicator whenever capture is active. The app
  does not and cannot suppress it.
- Recording or transcribing other participants may require their consent
  depending on your jurisdiction and your employer's policy. Check before you
  use this in a meeting with other people.

Full detail in [security.md](security.md).

## What it costs

Roughly **$1.30 per meeting hour** at published prices, dominated by
transcription at $0.017/minute. Silence is not sent, so a quiet meeting costs
less. The History panel shows an estimate next to your measured token usage —
it is a projection from unit prices, not your actual bill.

---

## Building from source

### Toolchains

```bash
xcode-select --install
curl -LsSf https://astral.sh/uv/install.sh | sh && uv python install 3.12
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
# Node 20+ from nodejs.org or nvm
```

Check with `make doctor`.

### Build and verify

```bash
make setup            # Python deps, npm deps, Swift helper
make test             # 300+ backend tests, frontend tests
make lint             # ruff, mypy --strict, eslint, tsc, clippy, rustfmt
make verify-openai    # probe the live API (needs OPENAI_API_KEY)
```

`make verify-openai` is worth running before anything else: it confirms the
models are available to your project and resolves the realtime WebSocket URL.

### Run it

```bash
make dev      # the full app; Tauri spawns and supervises the backend
make backend  # the backend alone, printing its port and token
```

### Package

```bash
make bundle
```

Produces `packages/desktop/src-tauri/target/release/bundle/`:

- `macos/Japanese Live Translator.app`
- `dmg/Japanese Live Translator_0.1.0_aarch64.dmg`

The result is **unsigned**. To ship it to anyone else you need an Apple
Developer ID:

```bash
# Sign, with the hardened runtime and the entitlements the app needs.
codesign --force --deep --options runtime \
  --entitlements packages/desktop/src-tauri/entitlements.plist \
  --sign "Developer ID Application: YOUR NAME (TEAMID)" \
  "packages/desktop/src-tauri/target/release/bundle/macos/Japanese Live Translator.app"

# Notarize, then staple the ticket so it works offline.
xcrun notarytool submit "…/Japanese Live Translator_0.1.0_aarch64.dmg" \
  --apple-id you@example.com --team-id TEAMID --password APP_SPECIFIC_PASSWORD --wait
xcrun stapler staple "…/Japanese Live Translator_0.1.0_aarch64.dmg"
```

The bundle is self-contained. `make bundle` builds the Swift helper, freezes
the backend with PyInstaller, and copies both into `Contents/Resources/`, so
the installed app needs no Python, no `uv` and no source checkout. Verified by
launching it with `PATH=/usr/bin:/bin`.

The app is still **unsigned**, so it needs the right-click → Open dance on
first launch, and it is **Apple Silicon only** (see Architecture below).

### Architecture

This builds for the architecture of the machine you are on — the artifact above
is `aarch64` (Apple Silicon). For a universal binary, add the Intel target and
build for both:

```bash
rustup target add x86_64-apple-darwin
npm --prefix packages/desktop run tauri build -- --target universal-apple-darwin
```

Not tested on Intel.

---

## Troubleshooting

**"Screen Recording permission is required."**
Grant it (step 3) and restart the app. macOS does not apply the change to a
running process.

**The level meter never moves.**
The selected application is not producing audio. Pick the browser if you are
using Teams on the web, or fall back to "Entire system audio".

**"No OpenAI API key is stored."**
Settings → paste your key. Capture is refused without one, by design.

**Subtitles stop and the status says "Reconnecting…".**
The connection to OpenAI dropped. It retries with increasing backoff and
resumes on its own; audio during the gap is dropped rather than queued, because
replaying stale speech would put the subtitles permanently behind.

**Everything works but the Japanese is wrong.**
Try a higher latency setting, and add domain terms to the glossary. Note that
spoken ticket identifiers do not survive transcription reliably — see
`docs/openai-verification.md` §3b. Declaring them in the glossary lets the app
reconstruct them deterministically.
