# Security and Privacy

The application handles live meeting audio and a paid API credential. Both are
sensitive. The design goal is that a compromise of the renderer, a stray log
file, or a crash dump should not leak either.

## 1. Threat model

| Asset | Threat | Mitigation |
|---|---|---|
| OpenAI API key | Read from disk / repo / logs | Keychain only; never written to disk by us; never logged; redaction filter on all log records |
| OpenAI API key | Exfiltrated by compromised renderer or an XSS in the webview | Key never leaves the Rust/Python side; renderer receives only `api_key_present: bool` |
| OpenAI API key | Visible in `ps` output | Passed to the child via environment on spawn, never as a command-line argument |
| Meeting audio | Written to disk | Never persisted; audio exists only in bounded in-memory queues |
| Meeting transcripts | Persisted or logged | In-memory ring buffer by default; `persist_transcripts` is opt-in; content never appears in logs at any level |
| Local backend | Reached by another local process or a web page | Bound to `127.0.0.1`; per-process random bearer token; strict `Origin` check; CORS denies all browser origins |
| Local backend | CSRF from a page the user visits | Token is not a cookie, so it is not auto-attached; `Origin` header validated on both HTTP and the WS handshake |
| Covert recording | App captures without the user knowing | Capture never auto-starts; explicit status in the UI at all times; macOS shows its own recording indicator |

## 2. API key handling

1. The user enters the key once in Settings.
2. The renderer sends it to the **Rust** side via a Tauri command — not to the
   Python backend over HTTP — and Rust immediately stores it in the macOS
   Keychain (service `com.jlt.japanese-live-translator`, account `openai_api_key`).
3. The renderer's copy is overwritten and the input cleared.
4. On backend spawn, Rust reads the key from Keychain and sets
   `OPENAI_API_KEY` in the child's environment.
5. Python reads it once at startup into a `SecretStr`-like wrapper whose
   `__repr__`/`__str__` return `***`. It is never interpolated into a log line,
   an error message, or an event.
6. `GET /api/settings` returns `api_key_present`, never the key.

If the key is absent, the backend starts and serves `/health` and settings but
refuses `/api/session/start` with `409 api_key_missing`.

### Log redaction

`jlt.logging` installs a filter that redacts anything matching `sk-[A-Za-z0-9_-]{16,}`
and the session bearer token from every record, including exception tracebacks,
as defence in depth even though the key should never reach a log call.

## 3. Local transport

- `uvicorn` binds `127.0.0.1` with an ephemeral port chosen by the OS; the port
  is reported to Rust on stdout as `{"type":"listening","port":N}`.
- A 256-bit token is generated per backend process (`secrets.token_urlsafe(32)`)
  and printed on the same stdout line. It is never written to disk.
- Every `/api/*` route and the WS handshake require it, compared with
  `secrets.compare_digest` to avoid timing leaks.
- `Origin`, when present, must be `tauri://localhost` or `http://localhost:<dev-port>`.
  Any other origin is rejected, which blocks a malicious web page from driving
  the backend even if it somehow learned the token.
- CORS is enabled for exactly the origins above and nothing else. It is
  required, not optional: the renderer is always a different origin from the
  backend, so without `Access-Control-Allow-Origin` the browser refuses every
  request before the `Origin` check can even run. `allow_credentials` is false
  and the origin list is never a wildcard.

Residual risk: any process running as the same user can read the token from the
process environment or `/proc`-equivalent interfaces and reach the backend.
macOS's user-level isolation is the boundary here; we do not claim protection
against an attacker already executing code as the user.

## 4. Data retention

| Data | Default | Where |
|---|---|---|
| Audio PCM | Not persisted | Bounded in-memory queue, overwritten continuously |
| Japanese transcripts | Not persisted | In-memory ring buffer, cleared on session stop unless opted in |
| Vietnamese translations | Not persisted | Same |
| Settings (non-secret) | Persisted | `~/Library/Application Support/JapaneseLiveTranslator/settings.json`, mode 0600 |
| Glossary | Persisted | Same directory. This is user-authored terminology, not meeting content |
| API key | Persisted | macOS Keychain only |
| Logs | Persisted | `~/Library/Logs/JapaneseLiveTranslator/`, structured JSON, content-free |

Opting into `persist_transcripts` creates `history.sqlite3` with mode 0600 in
the Application Support directory, holding one row per session and one per
translated line. The UI states plainly that this writes meeting content to
disk, and offers "Delete all history" as well as deleting a single session.

The cost of that default is visible rather than hidden: with saving off, the
History list holds only the session in progress and says so, offering to turn
saving on. It does not quietly keep nothing.

Summaries are generated only when the user asks, and the UI says that doing so
sends the transcript to OpenAI again and costs money. Exports are likewise
user-initiated — nothing is ever written out automatically.

### What the in-app notice says, and why

The Info section in Settings states, in Japanese, that the only destination for
audio and text is the OpenAI API — not that data is never sent to a third
party. The second claim would be false: OpenAI *is* a third party, audio is
sent for transcription and Japanese text is sent for translation.

This matters beyond pedantry. Other meeting participants may read that notice
and rely on it, and a privacy claim that someone relies on has to be true. The
accurate version is still reassuring: one destination, no storage, no
telemetry, and capture only on an explicit press of Start.

### What OpenAI receives and retains

Audio and text are sent to OpenAI to be processed — this is inherent to the
product and must be disclosed to the user before first capture. We set
`store: false` on Responses requests so the payloads are not retained for
retrieval. OpenAI's own abuse-monitoring retention is governed by their data
policy, which we do not control and do not misrepresent. Users in meetings
subject to confidentiality obligations should confirm their organisation
permits sending audio to a third-party API; the first-run dialog says so.

## 5. Consent and transparency

- Capture requires an explicit Start. There is no autostart, no "start on
  launch", and no hidden hotkey that begins capture.
- Closing the app ends capture. The shell kills the backend on quit, and the
  backend independently watches its supervisor (`JLT_PARENT_PID`) and exits if
  that process disappears. Both exist because only the second survives a
  SIGKILL or a crash, and a backend that outlived the window would keep
  capturing after the user believed they had stopped.
- The capture indicator is always visible in the window; the status bar shows
  `capture` and `stt` states and a live audio level meter, so a user can always
  tell whether audio is leaving the machine.
- First run shows a consent dialog naming OpenAI as the processor.
- macOS requires Screen Recording permission for ScreenCaptureKit even for
  audio-only capture, and shows its own system indicator. We do not and cannot
  suppress it.
- Recording other participants may require their consent depending on
  jurisdiction and company policy. The app states this once and does not
  pretend to give legal advice.

## 6. Supply chain and build

- Python dependencies are pinned in `pyproject.toml` with a lockfile.
- No telemetry, no analytics, no auto-update channel in v1.
- The only network destination is `api.openai.com`. The helper, the backend, and
  the renderer make no other outbound requests.
- Release builds should be signed and notarized; the hardened runtime entitlement
  for audio/screen capture is in `packages/desktop/src-tauri/entitlements.plist`.

## 7. Known gaps

- No encryption at rest for the optional SQLite history beyond file permissions.
  If persistence is enabled on a machine without FileVault, transcripts are
  readable by anyone with the disk.
- The loopback token protects against other *browsers*, not against another
  process running as the same user (see §3).
- We do not verify that the audio source actually is Teams; the user picks it.
  A mis-pick sends the wrong application's audio to OpenAI. The UI shows the
  selected application name prominently for this reason.
