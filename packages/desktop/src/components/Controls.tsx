import type { AudioSource } from "../types/events";

/** A clock with a counter-clockwise arrow — the usual "history" glyph. */
function HistoryIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden focusable="false">
      <path
        d="M3 3v6h6M3.5 9a9 9 0 1 0 2.2-3.6L3 9"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M12 7v5l3.5 2" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

interface Props {
  historyOpen: boolean;
  onToggleHistory: () => void;
  sources: AudioSource[];
  selected: string;
  onSelect: (id: string) => void;
  capture: string;
  busy: boolean;
  alwaysOnTop: boolean;
  onStart: () => void;
  onStop: () => void;
  onPause: () => void;
  onResume: () => void;
  onToggleAlwaysOnTop: () => void;
  settingsOpen: boolean;
  onOpenSettings: () => void;
  includeMicrophone: boolean;
  onToggleMicrophone: () => void;
}

export function Controls({
  historyOpen, onToggleHistory,
  sources, selected, onSelect, capture, busy, alwaysOnTop,
  onStart, onStop, onPause, onResume, onToggleAlwaysOnTop,
  settingsOpen, onOpenSettings,
  includeMicrophone, onToggleMicrophone,
}: Props) {
  const running = capture === "running";
  const paused = capture === "paused";
  const active = running || paused;

  return (
    <header className="controls">
      <button
        className={`btn btn--icon ${historyOpen ? "btn--on" : ""}`}
        onClick={onToggleHistory}
        aria-pressed={historyOpen}
        aria-label="History"
        title="History"
      >
        <HistoryIcon />
      </button>

      <select
        className="controls__source"
        value={selected}
        onChange={(event) => onSelect(event.target.value)}
        // Changing the source mid-session would silently send a different
        // application's audio to OpenAI.
        disabled={active || busy}
        aria-label="Audio source"
      >
        {sources.length === 0 && <option value="">No audio sources found</option>}
        {sources.map((source) => (
          <option key={source.id} value={source.id}>
            {source.is_recommended ? "★ " : ""}
            {source.name}
          </option>
        ))}
      </select>

      {!active && (
        <button className="btn btn--primary" onClick={onStart} disabled={busy || !selected}>
          Start
        </button>
      )}
      {running && (
        <button className="btn" onClick={onPause} disabled={busy}>
          Pause
        </button>
      )}
      {paused && (
        <button className="btn btn--primary" onClick={onResume} disabled={busy}>
          Resume
        </button>
      )}
      {active && (
        <button className="btn" onClick={onStop} disabled={busy}>
          Stop
        </button>
      )}

      {/* Beside the source, not buried in Settings: it changes what is being
          recorded, so it should be visible while choosing what to record. */}
      <label className="controls__mic" title="Cũng thu cả microphone và trộn vào">
        <input
          type="checkbox"
          checked={includeMicrophone}
          disabled={active || busy}
          onChange={onToggleMicrophone}
        />
        Mic
      </label>

      <span className="controls__spacer" />

      <button
        className={`btn btn--icon ${alwaysOnTop ? "btn--on" : ""}`}
        onClick={onToggleAlwaysOnTop}
        aria-pressed={alwaysOnTop}
        title="Always on top"
      >
        Pin
      </button>
      <button
        className={`btn btn--icon ${settingsOpen ? "btn--on" : ""}`}
        onClick={onOpenSettings}
        aria-pressed={settingsOpen}
        title="Settings"
      >
        Settings
      </button>
    </header>
  );
}
