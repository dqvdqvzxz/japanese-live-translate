/**
 * Turns backend log records and UI-side problems into rows the log view can
 * show, sort and copy. Pure on purpose: the view has no test harness, so the
 * logic worth testing lives here.
 */

import type { LogRecord } from "../api/client";
import type { ProblemEntry } from "../state/subtitles";

export type RowLevel = "info" | "warning" | "error";

export interface LogRow {
  id: string;
  ts: number;
  level: RowLevel;
  /** "backend" came from the backend log; "app" is something this window hit. */
  origin: "backend" | "app";
  /** What happened, in the words a person should read. */
  text: string;
  /** The technical line: logger, original message, structured fields. */
  meta: string;
  trace?: string;
}

/** Keys that are part of every record or already shown elsewhere in the row. */
const NOT_EXTRA = new Set(["ts", "level", "logger", "msg", "detail", "exc"]);
const MAX_VALUE_CHARS = 140;

export function levelOf(raw: string): RowLevel {
  const level = raw.toLowerCase();
  if (level === "error" || level === "critical" || level === "fatal") return "error";
  if (level === "warning" || level === "warn") return "warning";
  return "info";
}

function show(value: unknown): string {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > MAX_VALUE_CHARS ? `${text.slice(0, MAX_VALUE_CHARS)}…` : text;
}

export function fromBackend(record: LogRecord, index: number): LogRow {
  const detail = typeof record.detail === "string" && record.detail ? record.detail : null;
  const extras = Object.entries(record)
    .filter(([key]) => !NOT_EXTRA.has(key))
    // The code is the thing people search for, so it leads.
    .sort(([a], [b]) => (a === "code" ? -1 : b === "code" ? 1 : 0))
    .map(([key, value]) => `${key}=${show(value)}`);

  return {
    id: `b${record.ts}-${index}`,
    ts: record.ts,
    level: levelOf(record.level),
    origin: "backend",
    text: detail ?? record.msg,
    meta: [record.logger, detail ? record.msg : null, ...extras].filter(Boolean).join(" · "),
    trace: typeof record.exc === "string" ? record.exc : undefined,
  };
}

export function fromProblem(problem: ProblemEntry, origin: "backend" | "app", index: number): LogRow {
  return {
    id: `p${origin}${problem.ts}-${index}`,
    ts: problem.ts,
    level: problem.severity === "fatal" ? "error" : "warning",
    origin,
    text: problem.message,
    meta: `code=${problem.code}`,
  };
}

/** Newest first: what just went wrong is what the user opened the view for. */
export function newestFirst(rows: LogRow[]): LogRow[] {
  return [...rows].sort((a, b) => b.ts - a.ts);
}

export function clock(ts: number): string {
  const d = new Date(ts * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** What the Copy button puts on the clipboard: oldest first, one entry per line. */
export function toPlainText(rows: LogRow[]): string {
  return [...rows]
    .sort((a, b) => a.ts - b.ts)
    .map((row) => {
      const head = `[${clock(row.ts)}] ${row.level.toUpperCase()} (${row.origin}) ${row.text}`;
      const tail = row.meta ? `\n    ${row.meta}` : "";
      const trace = row.trace ? `\n${row.trace}` : "";
      return `${head}${tail}${trace}`;
    })
    .join("\n");
}
