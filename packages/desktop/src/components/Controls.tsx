import type { AudioSource } from "../types/events";

interface Props {
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
  onOpenSettings: () => void;
}

export function Controls({
  sources, selected, onSelect, capture, busy, alwaysOnTop,
  onStart, onStop, onPause, onResume, onToggleAlwaysOnTop, onOpenSettings,
}: Props) {
  const running = capture === "running";
  const paused = capture === "paused";
  const active = running || paused;

  return (
    <header className="controls">
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

      <span className="controls__spacer" />

      <button
        className={`btn btn--icon ${alwaysOnTop ? "btn--on" : ""}`}
        onClick={onToggleAlwaysOnTop}
        aria-pressed={alwaysOnTop}
        title="Always on top"
      >
        Pin
      </button>
      <button className="btn btn--icon" onClick={onOpenSettings} title="Settings">
        Settings
      </button>
    </header>
  );
}
