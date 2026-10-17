import type { SessionRecord } from "../api/client";
import { formatDuration, groupByDay, totalCost } from "./historyUtils";

interface Props {
  sessions: SessionRecord[];
  persisting: boolean;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onOpenSettings: () => void;
}

function fallbackName(session: SessionRecord): string {
  return (
    session.name ||
    new Date(session.started_at * 1000).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    })
  );
}

/**
 * The history list, as a column beside the subtitles rather than on top of
 * them.
 *
 * An overlay would hide the live transcript, which is the one thing a user
 * cannot get back by scrolling. Widening the layout keeps both visible, so
 * glancing at an old meeting does not mean losing the current one.
 */
export function HistorySidebar({
  sessions,
  persisting,
  selectedId,
  onSelect,
  onOpenSettings,
}: Props) {
  const grouped = groupByDay(sessions);

  return (
    <aside className="sidebar" aria-label="History">
      <div className="sidebar__head">
        <h2>History</h2>
        {sessions.length > 0 && (
          <span className="sidebar__total">${totalCost(sessions).toFixed(2)}</span>
        )}
      </div>

      {!persisting && (
        <div className="sidebar__notice">
          Chỉ giữ phiên hiện tại. Bật lưu transcript để giữ lại sau khi đóng app.
          <button className="btn btn--small" onClick={onOpenSettings}>
            Mở Settings
          </button>
        </div>
      )}

      {sessions.length === 0 && <p className="sidebar__empty">Chưa có bản ghi nào.</p>}

      {selectedId && (
        <button className="btn btn--small sidebar__live" onClick={() => onSelect(null)}>
          ← Về phiên trực tiếp
        </button>
      )}

      <div className="sidebar__scroll">
        {grouped.map(([day, items]) => (
          <section key={day}>
            <h3 className="sidebar__day">{day}</h3>
            <ul className="sidebar__list">
              {items.map((session) => (
                <li key={session.session_id}>
                  <button
                    className={`sidebar__item ${
                      selectedId === session.session_id ? "sidebar__item--active" : ""
                    }`}
                    aria-current={selectedId === session.session_id}
                    onClick={() => onSelect(session.session_id)}
                  >
                    <span className="sidebar__name">{fallbackName(session)}</span>
                    <span className="sidebar__cost">
                      ${session.estimated_cost_usd.toFixed(3)}
                    </span>
                    <span className="sidebar__meta">
                      {formatDuration(session.duration_s)} · {session.segments_translated} lines
                      {session.summary && <span className="sidebar__dot" title="Có tóm tắt" />}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>

      {sessions.length > 0 && (
        <p className="sidebar__foot">Chi phí là ước tính, không phải hoá đơn.</p>
      )}
    </aside>
  );
}
