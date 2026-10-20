import { useEffect, useMemo, useState } from "react";
import type { BackendClient, LogRecord } from "../api/client";
import { isDesktop, revealInFinder } from "../api/tauri";
import type { ProblemEntry } from "../state/subtitles";
import { clock, fromBackend, fromProblem, newestFirst, toPlainText } from "./logUtils";

interface Props {
  client: BackendClient | null;
  /**
   * Warnings and errors from the live event stream. Shown on their own only
   * when the backend log cannot be read; otherwise the backend log already
   * contains them, and listing both would show everything twice.
   */
  problems: ProblemEntry[];
  /** Things this window tried and failed at — a refused Start, say — which the backend never logs. */
  appProblems: ProblemEntry[];
  onClose: () => void;
}

const POLL_MS = 2000;

/**
 * The log view: what went wrong and when, without opening a file.
 *
 * It exists because a silent capture used to leave nothing to look at but a
 * log file in ~/Library/Logs that a packaged app gives no way to find. The
 * backend's records are counts, codes and latencies by the project's own rule
 * (CLAUDE.md §5), so showing and copying them cannot leak a meeting.
 */
export function LogPanel({ client, problems, appProblems, onClose }: Props) {
  const [everything, setEverything] = useState(false);
  const [records, setRecords] = useState<LogRecord[] | null>(null);
  const [file, setFile] = useState<string | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!client) return;
    let cancelled = false;
    const load = async () => {
      try {
        const body = await client.getLogs(everything ? "info" : "warning");
        if (cancelled) return;
        setRecords(body.entries);
        setFile(body.file);
        setFetchError(null);
      } catch (error) {
        if (!cancelled) setFetchError(error instanceof Error ? error.message : String(error));
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [client, everything]);

  const rows = useMemo(() => {
    const fromServer = records
      ? records.map((record, index) => fromBackend(record, index))
      : problems.map((problem, index) => fromProblem(problem, "backend", index));
    const fromWindow = appProblems.map((problem, index) => fromProblem(problem, "app", index));
    return newestFirst([...fromServer, ...fromWindow]);
  }, [records, problems, appProblems]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(toPlainText(rows));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard access can be refused; the entries are still on screen.
    }
  };

  return (
    <div className="panel panel--wide" role="dialog" aria-label="Log">
      <div className="panel__head">
        <h2>Log</h2>
        <button className="btn btn--icon" onClick={onClose} aria-label="Close log">
          ×
        </button>
      </div>

      <div className="log__bar">
        <span className="log__seg" role="group" aria-label="Mức hiển thị">
          <button
            className={`btn btn--small ${everything ? "" : "btn--on"}`}
            aria-pressed={!everything}
            onClick={() => setEverything(false)}
          >
            Cảnh báo &amp; lỗi
          </button>
          <button
            className={`btn btn--small ${everything ? "btn--on" : ""}`}
            aria-pressed={everything}
            onClick={() => setEverything(true)}
          >
            Tất cả
          </button>
        </span>
        <span className="controls__spacer" />
        <button className="btn btn--small" onClick={() => void copy()} disabled={rows.length === 0}>
          {copied ? "Đã sao chép" : "Sao chép"}
        </button>
        {isDesktop() && file && (
          <button className="btn btn--small" onClick={() => void revealInFinder(file)}>
            Mở file log
          </button>
        )}
      </div>

      {fetchError && (
        <p className="panel__warn">
          Không đọc được log từ backend ({fetchError}). Đang hiện các cảnh báo nhận được trong
          cửa sổ này.
        </p>
      )}

      {rows.length === 0 ? (
        <p className="panel__hint">
          {everything ? "Log đang trống." : "Chưa có cảnh báo hay lỗi nào."}
        </p>
      ) : (
        <ul className="log__list">
          {rows.map((row) => (
            <li key={row.id} className={`log__row log__row--${row.level}`}>
              <div className="log__head">
                <span>{clock(row.ts)}</span>
                <span className="log__level">{row.level}</span>
                {row.origin === "app" && <span>· app</span>}
              </div>
              <p className="log__text">{row.text}</p>
              {row.meta && <p className="log__meta">{row.meta}</p>}
              {row.trace && (
                <details>
                  <summary className="log__meta">chi tiết lỗi</summary>
                  <pre className="log__trace">{row.trace}</pre>
                </details>
              )}
            </li>
          ))}
        </ul>
      )}

      <p className="panel__hint">
        Log chỉ ghi số đếm, mã lỗi và độ trễ — không bao giờ có nội dung cuộc họp hay API key.
      </p>
    </div>
  );
}
