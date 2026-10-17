import { useState } from "react";
import type { SessionRecord, StoredSegment } from "../api/client";
import { revealInFinder } from "../api/tauri";
import { formatDuration } from "./historyUtils";

interface Props {
  session: SessionRecord;
  segments: StoredSegment[];
  fontSize: number;
  busy: boolean;
  onRename: (name: string) => void;
  onSummarize: () => void;
  onDelete: () => void;
}

/**
 * A stored meeting, rendered in the main content area.
 *
 * Deliberately the same column the live subtitles use: reading an old meeting
 * should feel like reading the current one, and reusing the layout means the
 * Japanese/Vietnamese pairing stays identical in both.
 */
export function RecordView({
  session,
  segments,
  fontSize,
  busy,
  onRename,
  onSummarize,
  onDelete,
}: Props) {
  const [editing, setEditing] = useState(false);
  const started = new Date(session.started_at * 1000);
  const translated = segments.filter((s) => s.target_text);

  return (
    <div className="record" style={{ fontSize: `${fontSize}px` }}>
      <header className="record__head">
        {editing ? (
          <form
            className="record__rename"
            onSubmit={(event) => {
              event.preventDefault();
              const field = event.currentTarget.elements.namedItem("name");
              if (field instanceof HTMLInputElement) onRename(field.value);
              setEditing(false);
            }}
          >
            <input
              name="name"
              defaultValue={session.name}
              placeholder={started.toLocaleString()}
              maxLength={120}
              autoFocus
              aria-label="Tên bản ghi"
            />
            <button className="btn btn--primary btn--small" type="submit">
              Lưu
            </button>
            <button className="btn btn--small" type="button" onClick={() => setEditing(false)}>
              Huỷ
            </button>
          </form>
        ) : (
          <div className="record__title-row">
            <h1>{session.name || started.toLocaleString()}</h1>
            <button className="btn btn--small" onClick={() => setEditing(true)}>
              Đổi tên
            </button>
          </div>
        )}

        <p className="record__meta">
          {started.toLocaleString()} · {session.source_name || "—"} ·{" "}
          {formatDuration(session.duration_s)} · {session.segments_translated} lines ·{" "}
          ${session.estimated_cost_usd.toFixed(3)} <span className="hist__est">(ước tính)</span>
        </p>
      </header>

      <section className="record__section">
        <h2>Tóm tắt &amp; việc cần làm</h2>
        {session.summary ? (
          <>
            <p className="record__overview">{session.summary.overview}</p>
            {session.summary.action_items.length > 0 ? (
              <ul className="record__actions">
                {session.summary.action_items.map((item, index) => (
                  <li key={index}>{item}</li>
                ))}
              </ul>
            ) : (
              <p className="record__hint">Không có việc cần làm nào được nêu rõ.</p>
            )}
            <p className="record__hint">
              Do {session.summary.model || "mô hình"} tạo từ bản dịch. Hãy đối chiếu
              với nội dung bên dưới trước khi dựa vào nó.
            </p>
          </>
        ) : (
          <>
            <p className="record__hint">
              Chưa có tóm tắt. Việc này gửi bản dịch tới OpenAI một lần nữa và có
              tính phí.
            </p>
            <button
              className="btn btn--primary"
              disabled={busy || translated.length === 0}
              onClick={onSummarize}
            >
              {busy ? "Đang tạo…" : "Tạo tóm tắt"}
            </button>
          </>
        )}
      </section>

      <section className="record__section">
        <h2>
          Nội dung ({segments.length}
          {translated.length !== segments.length && `, ${translated.length} đã dịch`})
        </h2>
        {segments.length === 0 && (
          <p className="record__hint">Phiên này không lưu nội dung nào.</p>
        )}
        {segments.map((segment) => (
          <article key={segment.segment_id} className="line">
            <p className="line__ja" lang="ja">
              {segment.source_text}
            </p>
            {segment.target_text ? (
              <p className="line__vi" lang="vi">
                {segment.target_text}
              </p>
            ) : (
              <p className="line__error">Không có bản dịch cho dòng này.</p>
            )}
          </article>
        ))}
      </section>

      <section className="record__section">
        <div className="panel__row">
          {session.file_path && (
            <button
              className="btn btn--small"
              onClick={() => void revealInFinder(session.file_path!)}
            >
              Hiện tệp trong Finder
            </button>
          )}
          <button className="btn btn--small btn--danger" disabled={busy} onClick={onDelete}>
            Xoá bản ghi
          </button>
        </div>
      </section>
    </div>
  );
}
