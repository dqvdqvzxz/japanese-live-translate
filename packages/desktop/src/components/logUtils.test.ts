import { describe, expect, it } from "vitest";
import type { LogRecord } from "../api/client";
import {
  fromBackend,
  fromProblem,
  levelOf,
  newestFirst,
  toPlainText,
} from "./logUtils";

const record = (extra: Record<string, unknown> = {}): LogRecord => ({
  ts: 1000,
  level: "warning",
  logger: "jlt.audio.capture_service",
  msg: "audio helper warning",
  ...extra,
});

describe("levelOf", () => {
  it("collapses logging levels to the three the view shows", () => {
    expect(levelOf("critical")).toBe("error");
    expect(levelOf("ERROR")).toBe("error");
    expect(levelOf("warning")).toBe("warning");
    expect(levelOf("info")).toBe("info");
    expect(levelOf("debug")).toBe("info");
  });
});

describe("fromBackend", () => {
  it("leads with the helper's own wording when the record carries it", () => {
    const row = fromBackend(
      record({ code: "app_all_zero", detail: "Every sample has been zero." }),
      0,
    );
    expect(row.text).toBe("Every sample has been zero.");
    expect(row.level).toBe("warning");
    expect(row.origin).toBe("backend");
    // The technical line keeps what the text replaced, and the code comes first.
    expect(row.meta).toBe(
      "jlt.audio.capture_service · audio helper warning · code=app_all_zero",
    );
  });

  it("falls back to the message and lists structured fields without noise", () => {
    const row = fromBackend(
      record({ level: "info", msg: "pipeline", commits: 0, finals: 0, extra_obj: { a: 1 } }),
      3,
    );
    expect(row.text).toBe("pipeline");
    expect(row.level).toBe("info");
    expect(row.meta).toContain("commits=0");
    expect(row.meta).toContain('extra_obj={"a":1}');
    // No field called ts or level (note "commits=0" merely ends in "ts=0").
    expect(row.meta).not.toMatch(/(^| · )(ts|level|logger|msg)=/);
  });

  it("truncates a very long field instead of flooding the row", () => {
    const row = fromBackend(record({ blob: "x".repeat(500) }), 0);
    expect(row.meta.length).toBeLessThan(300);
    expect(row.meta).toContain("…");
  });

  it("carries a traceback separately", () => {
    const row = fromBackend(record({ level: "error", exc: "Traceback…" }), 0);
    expect(row.trace).toBe("Traceback…");
    expect(row.meta).not.toContain("Traceback");
  });

  it("gives rows with the same timestamp distinct ids", () => {
    expect(fromBackend(record(), 0).id).not.toBe(fromBackend(record(), 1).id);
  });
});

describe("fromProblem", () => {
  it("maps severity onto the log levels and keeps the origin", () => {
    const fatal = fromProblem(
      { ts: 5, severity: "fatal", code: "api_key_missing", message: "No key." },
      "app",
      0,
    );
    expect(fatal.level).toBe("error");
    expect(fatal.origin).toBe("app");
    expect(fatal.meta).toBe("code=api_key_missing");
    expect(
      fromProblem({ ts: 5, severity: "warning", code: "c", message: "m" }, "backend", 0).level,
    ).toBe("warning");
  });
});

describe("ordering and copying", () => {
  const rows = [
    fromProblem({ ts: 1, severity: "warning", code: "a", message: "first" }, "backend", 0),
    fromProblem({ ts: 3, severity: "fatal", code: "c", message: "third" }, "app", 0),
    fromProblem({ ts: 2, severity: "warning", code: "b", message: "second" }, "backend", 1),
  ];

  it("shows the newest first", () => {
    expect(newestFirst(rows).map((r) => r.text)).toEqual(["third", "second", "first"]);
  });

  it("does not reorder the caller's array", () => {
    const before = rows.map((r) => r.text);
    newestFirst(rows);
    expect(rows.map((r) => r.text)).toEqual(before);
  });

  it("copies oldest first, one entry per line, with the technical line beneath", () => {
    const text = toPlainText(rows);
    const lines = text.split("\n");
    expect(lines[0]).toMatch(/^\[\d\d:\d\d:\d\d\] WARNING \(backend\) first$/);
    expect(lines[1]).toBe("    code=a");
    expect(lines[2]).toMatch(/WARNING \(backend\) second$/);
    expect(lines[4]).toMatch(/ERROR \(app\) third$/);
  });
});
