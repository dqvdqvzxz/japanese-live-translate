import type { SubtitleState } from "../state/subtitles";

interface Props {
  state: SubtitleState;
  connected: boolean;
  mock: boolean;
}

const CAPTURE_LABEL: Record<string, string> = {
  idle: "Not capturing",
  starting: "Starting…",
  running: "Capturing",
  paused: "Paused",
  stopping: "Stopping…",
  stopped: "Stopped",
  error: "Capture error",
};

const STT_LABEL: Record<string, string> = {
  disconnected: "Offline",
  connecting: "Connecting…",
  connected: "Connected",
  reconnecting: "Reconnecting…",
  error: "Error",
};

/** Maps dBFS onto a 0..1 bar. Below -60 is effectively silence. */
function levelFraction(dbfs: number | null): number {
  if (dbfs === null || dbfs <= -60) return 0;
  return Math.min(1, (dbfs + 60) / 60);
}

/**
 * Capture state is always visible and never implied.
 *
 * A user must be able to tell at a glance whether audio is leaving their
 * machine — that is a privacy guarantee, not decoration (docs/security.md §5).
 */
export function StatusBar({ state, connected, mock }: Props) {
  const capturing = state.capture === "running";
  return (
    <footer className="status">
      <span className={`status__dot ${capturing ? "status__dot--live" : ""}`} aria-hidden />
      <span className="status__item">{CAPTURE_LABEL[state.capture] ?? state.capture}</span>

      <span className="status__sep" aria-hidden>
        ·
      </span>
      <span className="status__item">OpenAI: {STT_LABEL[state.stt] ?? state.stt}</span>

      <span className="status__meter" aria-label="Audio level">
        <span
          className="status__meter-fill"
          style={{ width: `${levelFraction(state.audioLevelDbfs) * 100}%` }}
        />
      </span>

      {state.queueDepth > 0 && (
        <span className="status__item status__item--dim">queue {state.queueDepth}</span>
      )}

      <span className="status__spacer" />

      {mock && <span className="status__badge">demo data</span>}
      {!mock && !connected && <span className="status__badge status__badge--warn">backend offline</span>}
    </footer>
  );
}
