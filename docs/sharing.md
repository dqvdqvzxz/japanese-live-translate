# Japanese Live Translator — installing a shared copy

Live Japanese → Vietnamese subtitles for Microsoft Teams on macOS.

## Before you start

| You need | Why |
|---|---|
| **macOS 13 (Ventura) or later** on **Apple Silicon** | The app uses ScreenCaptureKit; this build is arm64 only and will not run on an Intel Mac |
| **Your own OpenAI API key** | The app does all transcription and translation through the OpenAI API. The key is yours and is billed to you — roughly **$1.30 per meeting hour** |
| **Screen Recording permission** | macOS requires it for audio capture, with no narrower option |

## 1. Install

Open the `.dmg` and drag the app into **Applications**.

## 2. Let macOS open it

**This app is not signed with an Apple Developer ID, so macOS will refuse to
open it on the first try.** There is nothing wrong with the file — Apple
charges for the certificate that would avoid this, and this build does not
have one. Pick whichever of these you are comfortable with.

**The reliable way** — open Terminal and run:

```
xattr -dr com.apple.quarantine "/Applications/Japanese Live Translator.app"
```

That removes the "downloaded from the internet" flag. Then open the app
normally.

**The other way** — double-click the app, let macOS block it, then go to
**System Settings → Privacy & Security**, scroll down, and press **Open
Anyway** next to the message about the app.

> Only do this for software you actually trust. The instruction above is the
> standard way to run unsigned Mac software, and it is also exactly what
> malware asks people to do. You should be confident about where you got this
> file.

## 3. Grant Screen Recording

**System Settings → Privacy & Security → Screen Recording** → turn on
**Japanese Live Translator**, then quit and reopen the app. macOS does not
apply the change to a running app.

The app only ever reads audio — it never captures a video frame — but macOS
offers no audio-only permission for this API.

## 4. Add your API key

**Settings → OpenAI API key** → paste → **Save to Keychain** → **Restart now**.

The key is stored in your macOS Keychain and nowhere else. It is never written
to a file and never sent anywhere except to OpenAI.

You need an OpenAI project with access to `gpt-live-transcribe` and
`gpt-5.4-mini`.

## 5. Use it

1. Pick the application playing the meeting audio — **Microsoft Teams** for the
   desktop app, your **browser** for Teams on the web, or **Entire system
   audio** if unsure.
2. If the Japanese is being spoken **in the room** rather than coming out of
   the app — an in-person meeting, or a speakerphone — tick **Mic** beside the
   source. macOS will ask for Microphone permission the first time. This also
   records you and anyone near you, so it is off until you turn it on.
3. Press **Start**.
4. Japanese appears as it is spoken; Vietnamese appears underneath each
   finished sentence.

The bar at the bottom always shows whether audio is being captured, and the
level meter moves when sound is actually arriving. If it stays flat while
people are talking, the wrong source is selected.

## What happens to your meeting audio

- Audio and text go to the **OpenAI API** and nowhere else — not to the
  developer, not to any analytics service. There is no telemetry.
- Requests are sent with `store: false`, so OpenAI does not retain them.
- **No audio file is ever written.** Audio only passes through memory.
- Transcripts are **not saved** unless you turn that on in Settings and choose
  a folder yourself.
- Capture only runs after you press **Start**, and macOS shows its own
  recording indicator the whole time.

Recording or transcribing other people may require their consent, depending on
where you are and your employer's rules. Check before using this in a meeting
with others.

## If something goes wrong

The app writes a log to `~/Library/Logs/JapaneseLiveTranslator/backend.log`.
It contains counts, timings and error codes — never any meeting content — and
is the right thing to send when reporting a problem:

```
tail -40 ~/Library/Logs/JapaneseLiveTranslator/backend.log
```

A line beginning `"msg": "pipeline"` appears every 15 seconds during a session
and says where the chain stops:

| Field | Means |
|---|---|
| `level_dbfs` near −120 | no audio is arriving — wrong source |
| `commits` stuck at 0 | audio arrives but is below the speech threshold |
| `deltas` stuck at 0 | audio reached OpenAI but produced nothing |
| `finals` rising, `translated` not | transcription works, translation is failing |


| Symptom | Cause |
|---|---|
| "App is damaged and can't be opened" | The quarantine flag — see step 2 |
| "Screen Recording permission is required" | Grant it and **restart the app** |
| Level meter never moves | Wrong source, or that app is not playing audio. For Teams, pick **Microsoft Teams** — helper processes are included automatically |
| Subtitles stop, status says "Reconnecting…" | Connection to OpenAI dropped; it recovers on its own |
| Japanese is wrong | Try a higher latency setting in Settings, and add your team's terms to the glossary |

---

Developed by **VuongDQ**. Built with Tauri, Swift and Python; all inference
runs on the OpenAI API.
