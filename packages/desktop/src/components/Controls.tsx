import type { OutputDevice } from "../api/client";
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

/** What the output indicator says, from the detected device. */
function describeOutput(output: OutputDevice | null): { on: boolean; known: boolean; title: string } {
  if (!output || output.kind === "unknown") {
    return { on: false, known: false, title: "Không đọc được thiết bị âm thanh đầu ra" };
  }
  const name = output.name || output.transport;
  switch (output.kind) {
    case "headphones":
      return { on: true, known: true, title: `Đang dùng tai nghe: ${name} (tự phát hiện)` };
    case "speakers":
      return { on: false, known: true, title: `Đang dùng loa ngoài: ${name} (tự phát hiện)` };
    default:
      return { on: false, known: true, title: `Thiết bị đầu ra: ${name} (tự phát hiện)` };
  }
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
  /** Detected output device; null until the first reading arrives. */
  output: OutputDevice | null;
  logOpen: boolean;
  onToggleLog: () => void;
  /** Warnings and errors not yet seen in the log view. */
  logUnseen: number;
  /** True when something is currently failing, not merely noted. */
  logSevere: boolean;
}

export function Controls({
  historyOpen, onToggleHistory,
  sources, selected, onSelect, capture, busy, alwaysOnTop,
  onStart, onStop, onPause, onResume, onToggleAlwaysOnTop,
  settingsOpen, onOpenSettings,
  output, logOpen, onToggleLog, logUnseen, logSevere,
}: Props) {
  const outputState = describeOutput(output);
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

      {/* Read-only on purpose: it reports what the Mac is doing rather than
          asking the user to say. Application audio is captured the same way
          either way, so there is nothing for a manual switch to change. */}
      <label
        className={`controls__output ${outputState.known ? "" : "controls__output--unknown"}`}
        title={outputState.title}
      >
        <input
          type="checkbox"
          checked={outputState.on}
          readOnly
          tabIndex={-1}
          onClick={(event) => event.preventDefault()}
          aria-label={outputState.title}
        />
        Tai nghe
      </label>

      <span className="controls__spacer" />

      <button
        className={`btn btn--icon ${logOpen ? "btn--on" : ""} ${
          logUnseen > 0 ? (logSevere ? "btn--alert-severe" : "btn--alert") : ""
        }`}
        onClick={onToggleLog}
        aria-pressed={logOpen}
        title={logUnseen > 0 ? `Log — ${logUnseen} cảnh báo/lỗi chưa xem` : "Log"}
      >
        Log
        {logUnseen > 0 && <span className="btn__badge">{logUnseen > 9 ? "9+" : logUnseen}</span>}
      </button>
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
