import type { SubtitleLine } from "../state/subtitles";
import type { SessionStats } from "../types/events";

interface Props {
  lines: SubtitleLine[];
  stats: SessionStats | null;
  onClose: () => void;
}

function ms(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${Math.round(value)} ms`;
}

/**
 * Session history and measured statistics.
 *
 * Latency figures are shown only when samples exist; an absent measurement
 * renders as "—" rather than as zero, so the panel never implies a speed that
 * was not observed.
 */
export function HistoryPanel({ lines, stats, onClose }: Props) {
  const translated = lines.filter((line) => line.vietnamese);

  return (
    <div className="panel" role="dialog" aria-label="Session history">
      <div className="panel__head">
        <h2>This session</h2>
        <button className="btn btn--icon" onClick={onClose} aria-label="Close history">
          ×
        </button>
      </div>

      {stats && (
        <section className="panel__section">
          <h3>Measured</h3>
          <dl className="stats">
            <dt>Duration</dt>
            <dd>{Math.round(stats.duration_s)}s</dd>
            <dt>Lines translated</dt>
            <dd>
              {stats.segments_translated}
              {stats.segments_failed > 0 && ` (${stats.segments_failed} failed)`}
            </dd>
            <dt>Japanese text (p50)</dt>
            <dd>{ms(stats.stt_latency_ms.p50)}</dd>
            <dt>Translation (p50)</dt>
            <dd>{ms(stats.translation_latency_ms.p50)}</dd>
            <dt>End to end (p95)</dt>
            <dd>{ms(stats.e2e_latency_ms.p95)}</dd>
            <dt>Reconnects</dt>
            <dd>{stats.reconnects}</dd>
            <dt>Estimated cost</dt>
            <dd>
              {stats.estimated_cost_usd === null
                ? "—"
                : `$${stats.estimated_cost_usd.toFixed(2)}`}
            </dd>
          </dl>
          <p className="panel__hint">
            Cost is a projection from published unit prices, not your actual bill.
          </p>
        </section>
      )}

      <section className="panel__section">
        <h3>Transcript ({translated.length})</h3>
        {translated.length === 0 && <p className="panel__hint">Nothing yet.</p>}
        <ol className="history">
          {translated.map((line) => (
            <li key={line.key}>
              <p lang="ja">{line.japanese}</p>
              <p lang="vi" className="history__vi">
                {line.vietnamese}
              </p>
            </li>
          ))}
        </ol>
        <p className="panel__hint">
          Held in memory only. Closing the app discards it unless you enabled
          saving in Settings.
        </p>
      </section>
    </div>
  );
}
