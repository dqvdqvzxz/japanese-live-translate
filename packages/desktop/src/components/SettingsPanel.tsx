import { useState } from "react";
import type { Settings } from "../api/client";

interface Props {
  settings: Settings | null;
  onPatch: (patch: Record<string, unknown>) => void;
  onClose: () => void;
  onClearHistory: () => void;
}

const DELAYS = ["minimal", "low", "medium", "high", "xhigh"] as const;

/**
 * Settings.
 *
 * The API key is conspicuously absent: it lives in the macOS Keychain and the
 * renderer only ever learns whether one is present. Entering it is a Tauri
 * command handled on the Rust side (docs/security.md §2).
 */
export function SettingsPanel({ settings, onPatch, onClose, onClearHistory }: Props) {
  const [confirmPersist, setConfirmPersist] = useState(false);
  if (!settings) return null;

  const ui = settings.ui;
  const privacy = settings.privacy;

  return (
    <div className="panel" role="dialog" aria-label="Settings">
      <div className="panel__head">
        <h2>Settings</h2>
        <button className="btn btn--icon" onClick={onClose} aria-label="Close settings">
          ×
        </button>
      </div>

      <section className="panel__section">
        <h3>Appearance</h3>
        <label>
          Theme
          <select
            value={ui.theme}
            onChange={(e) => onPatch({ ui: { theme: e.target.value } })}
          >
            <option value="system">Follow system</option>
            <option value="light">Light</option>
            <option value="dark">Dark</option>
          </select>
        </label>
        <label>
          Subtitle size: {ui.font_size}px
          <input
            type="range"
            min={12}
            max={40}
            value={ui.font_size}
            onChange={(e) => onPatch({ ui: { font_size: Number(e.target.value) } })}
          />
        </label>
        <label className="panel__check">
          <input
            type="checkbox"
            checked={ui.show_partials}
            onChange={(e) => onPatch({ ui: { show_partials: e.target.checked } })}
          />
          Show Japanese as it is being spoken
        </label>
      </section>

      <section className="panel__section">
        <h3>Transcription</h3>
        <label>
          Latency / accuracy
          <select
            value={String(settings.stt.delay)}
            onChange={(e) => onPatch({ stt: { delay: e.target.value } })}
          >
            {DELAYS.map((delay) => (
              <option key={delay} value={delay}>
                {delay}
              </option>
            ))}
          </select>
        </label>
        <p className="panel__hint">
          Lower settings show text sooner; higher settings give the model more
          audio before it commits, which can improve accuracy.
        </p>
      </section>

      <section className="panel__section">
        <h3>Privacy</h3>
        <p className="panel__hint">
          Audio and transcripts are sent to OpenAI to be processed. Nothing is
          saved on this Mac unless you turn it on below.
        </p>
        <label className="panel__check">
          <input
            type="checkbox"
            checked={privacy.persist_transcripts}
            onChange={(e) => {
              if (e.target.checked && !confirmPersist) {
                setConfirmPersist(true);
                return;
              }
              setConfirmPersist(false);
              onPatch({ privacy: { persist_transcripts: e.target.checked } });
            }}
          />
          Save transcripts to this Mac
        </label>
        {confirmPersist && (
          <div className="panel__confirm">
            This writes meeting content to disk in your Application Support
            folder. Turn it on?
            <div>
              <button
                className="btn btn--primary"
                onClick={() => {
                  setConfirmPersist(false);
                  onPatch({ privacy: { persist_transcripts: true } });
                }}
              >
                Turn on
              </button>
              <button className="btn" onClick={() => setConfirmPersist(false)}>
                Cancel
              </button>
            </div>
          </div>
        )}
        <button className="btn" onClick={onClearHistory}>
          Delete all history
        </button>
      </section>

      <section className="panel__section">
        <h3>OpenAI</h3>
        <p className="panel__hint">
          {settings.api_key_present
            ? "An API key is stored in your macOS Keychain."
            : "No API key stored. Capture cannot start without one."}
        </p>
      </section>
    </div>
  );
}
