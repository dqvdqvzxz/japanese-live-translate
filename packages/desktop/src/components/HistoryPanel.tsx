import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  type BackendClient,
  type ExportFormat,
  type SessionRecord,
  type StoredSegment,
} from "../api/client";
import { isDesktop, saveFolderToDownloads, saveToDownloads } from "../api/tauri";
import { formatDuration, groupByDay, totalCost } from "./historyUtils";

interface Props {
  client: BackendClient | null;
  onClose: () => void;
  onEnablePersistence: () => void;
}

const FORMATS: ExportFormat[] = ["md", "txt", "csv", "json"];

function dateLabel(ts: number): string {
  return new Date(ts * 1000).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function HistoryPanel({ client, onClose, onEnablePersistence }: Props) {
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [persisting, setPersisting] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<{
    session: SessionRecord;
    segments: StoredSegment[];
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!client) return;
    try {
      const body = await client.listSessions();
      setSessions(body.sessions);
      setPersisting(body.persisting);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }, [client]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!client || !selected) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const body = await client.sessionDetail(selected);
        if (!cancelled) setDetail(body);
      } catch (e) {
        if (!cancelled) setError(e instanceof ApiError ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client, selected]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      await action();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  /** Export is always something the user asks for; nothing is written on its own. */
  const deliver = (fetchFile: () => Promise<{ filename: string; body: string }>) =>
    run(async () => {
      const { filename, body } = await fetchFile();
      if (isDesktop()) {
        // The webview blocks blob downloads, so the native side writes it.
        setSaved(await saveToDownloads(filename, body));
        return;
      }
      const url = URL.createObjectURL(new Blob([body], { type: "text/plain" }));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
      setSaved(filename);
    });

  const exportSession = (id: string, format: ExportFormat) =>
    deliver(() => client!.exportSession(id, format));

  const exportEverything = (format: ExportFormat) =>
    run(async () => {
      if (!client) return;
      if (isDesktop()) {
        // One document per meeting, in one folder: a year of meetings in a
        // single file is not something anyone can search or file away.
        const bundle = await client.exportBundle(format);
        setSaved(await saveFolderToDownloads(bundle.folder, bundle.files));
        return;
      }
      // A browser cannot be handed a folder, so it gets the combined file.
      const { filename, body } = await client.exportAll(format);
      const url = URL.createObjectURL(new Blob([body], { type: "text/plain" }));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
      setSaved(filename);
    });

  const rename = (id: string, name: string) =>
    run(async () => {
      if (!client) return;
      await client.renameSession(id, name);
      setRenaming(null);
      await refresh();
      if (selected === id) setDetail(await client.sessionDetail(id));
    });

  // --- detail ---------------------------------------------------------------

  if (selected && detail) {
    const { session, segments } = detail;
    const translated = segments.filter((s) => s.target_text);
    return (
      <div className="panel" role="dialog" aria-label="Session detail">
        <div className="panel__head">
          <button className="btn btn--icon" onClick={() => setSelected(null)}>
            ← Back
          </button>
          <button className="btn btn--icon" onClick={onClose} aria-label="Close history">
            ×
          </button>
        </div>

        {renaming === session.session_id ? (
          <form
            className="hist__name"
            onSubmit={(e) => {
              e.preventDefault();
              const input = e.currentTarget.elements.namedItem("name");
              if (input instanceof HTMLInputElement) rename(session.session_id, input.value);
            }}
          >
            <input
              name="name"
              defaultValue={session.name}
              placeholder={dateLabel(session.started_at)}
              maxLength={120}
              autoFocus
              aria-label="Tên bản ghi"
            />
            <button className="btn btn--primary" type="submit" disabled={busy}>
              Lưu
            </button>
            <button className="btn" type="button" onClick={() => setRenaming(null)}>
              Huỷ
            </button>
          </form>
        ) : (
          <div className="hist__name">
            <h2 className="hist__title">{session.name || dateLabel(session.started_at)}</h2>
            <button
              className="btn btn--icon"
              onClick={() => setRenaming(session.session_id)}
              aria-label="Đổi tên"
            >
              Đổi tên
            </button>
          </div>
        )}
        <p className="panel__hint">
          {session.source_name || "Unknown source"} · {formatDuration(session.duration_s)} ·{" "}
          {session.segments_translated} lines · ${session.estimated_cost_usd.toFixed(3)}{" "}
          <span className="hist__est">(estimated)</span>
        </p>

        <section className="panel__section">
          <h3>Tóm tắt &amp; việc cần làm</h3>
          {session.summary ? (
            <>
              <p className="hist__overview">{session.summary.overview}</p>
              {session.summary.action_items.length > 0 ? (
                <ul className="hist__actions">
                  {session.summary.action_items.map((item, i) => (
                    <li key={i}>{item}</li>
                  ))}
                </ul>
              ) : (
                <p className="panel__hint">Không có việc cần làm nào được nêu rõ.</p>
              )}
              <p className="panel__hint">
                Tóm tắt do {session.summary.model || "mô hình"} tạo từ bản dịch. Hãy
                đối chiếu với nội dung bên dưới trước khi dựa vào nó.
              </p>
            </>
          ) : (
            <>
              <p className="panel__hint">
                Chưa có tóm tắt. Việc này gửi bản dịch của phiên tới OpenAI một lần
                nữa và có tính phí.
              </p>
              <button
                className="btn btn--primary"
                disabled={busy || translated.length === 0}
                onClick={() =>
                  run(async () => {
                    if (!client) return;
                    await client.summarize(session.session_id);
                    const body = await client.sessionDetail(session.session_id);
                    setDetail(body);
                    await refresh();
                  })
                }
              >
                {busy ? "Đang tạo…" : "Tạo tóm tắt"}
              </button>
            </>
          )}
        </section>

        <section className="panel__section">
          <h3>Nội dung ({translated.length})</h3>
          {segments.length === 0 && (
            <p className="panel__hint">Phiên này không lưu nội dung nào.</p>
          )}
          <ol className="history">
            {segments.map((segment) => (
              <li key={segment.segment_id}>
                <p lang="ja">{segment.source_text}</p>
                <p lang="vi" className="history__vi">
                  {segment.target_text ?? "—"}
                </p>
              </li>
            ))}
          </ol>
        </section>

        <section className="panel__section">
          <h3>Export</h3>
          <div className="panel__row">
            {FORMATS.map((format) => (
              <button
                key={format}
                className="btn"
                disabled={busy}
                onClick={() => exportSession(session.session_id, format)}
              >
                {format.toUpperCase()}
              </button>
            ))}
          </div>
          {saved && <p className="panel__hint">Đã lưu: {saved}</p>}
          {error && <p className="panel__error">{error}</p>}
        </section>
      </div>
    );
  }

  // --- list -----------------------------------------------------------------

  const grouped = groupByDay(sessions);
  const total = totalCost(sessions);

  return (
    <div className="panel" role="dialog" aria-label="Session history">
      <div className="panel__head">
        <h2>History</h2>
        <button className="btn btn--icon" onClick={onClose} aria-label="Close history">
          ×
        </button>
      </div>

      {!persisting && (
        <div className="panel__confirm">
          Lịch sử chỉ giữ phiên hiện tại. Bật lưu transcript để giữ lại sau khi đóng
          app — nội dung cuộc họp sẽ được ghi vào máy này.
          <div>
            <button className="btn btn--primary" onClick={onEnablePersistence}>
              Mở Settings
            </button>
          </div>
        </div>
      )}

      {sessions.length > 0 && (
        <p className="panel__hint">
          {sessions.length} phiên · tổng chi phí ước tính{" "}
          <strong>${total.toFixed(2)}</strong>
        </p>
      )}

      {sessions.length === 0 && <p className="panel__hint">Chưa có phiên nào.</p>}

      {grouped.map(([day, items]) => (
        <section key={day} className="panel__section">
          <h3>{day}</h3>
          <ul className="hist__list">
            {items.map((session) => (
              <li key={session.session_id}>
                <button className="hist__item" onClick={() => setSelected(session.session_id)}>
                  <span className="hist__time">
                    {new Date(session.started_at * 1000).toLocaleTimeString(undefined, {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </span>
                  <span className="hist__meta">
                    {session.name || session.source_name || "—"} ·{" "}
                    {formatDuration(session.duration_s)} ·{" "}
                    {session.segments_translated} lines
                    {session.summary && <span className="hist__badge">tóm tắt</span>}
                  </span>
                  <span className="hist__cost">${session.estimated_cost_usd.toFixed(3)}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}

      {sessions.length > 0 && (
        <section className="panel__section">
          <h3>Export toàn bộ</h3>
          <p className="panel__hint">
            {isDesktop()
              ? `Tạo thư mục "All History - yyyymmdd" trong Downloads, mỗi phiên một tệp riêng.`
              : `Trình duyệt không nhận được thư mục, nên ${sessions.length} phiên sẽ gộp vào một tệp.`}{" "}
            Không có gì được xuất tự động — chỉ khi bạn bấm.
          </p>
          <div className="panel__row">
            {FORMATS.map((format) => (
              <button
                key={format}
                className="btn"
                disabled={busy}
                onClick={() => exportEverything(format)}
              >
                {format.toUpperCase()}
              </button>
            ))}
          </div>
          {saved && <p className="panel__hint">Đã lưu: {saved}</p>}
        </section>
      )}

      {sessions.length > 0 && (
        <p className="panel__hint">
          Chi phí là ước tính từ đơn giá công bố, không phải hoá đơn thực tế.
        </p>
      )}
      {error && <p className="panel__error">{error}</p>}
    </div>
  );
}
