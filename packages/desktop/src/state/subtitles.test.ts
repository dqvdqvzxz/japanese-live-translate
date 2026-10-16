import { describe, expect, it } from "vitest";
import type { JltEvent } from "../types/events";
import { MAX_LINES, initialState, subtitleReducer, type SubtitleState } from "./subtitles";

function reduce(events: JltEvent[], from: SubtitleState = initialState): SubtitleState {
  return events.reduce(subtitleReducer, from);
}

const base = { ts: 0, session_id: "s_1" };

const partial = (seq: number, text: string): JltEvent => ({
  ...base, type: "transcript.partial", item_id: `i${seq}`, seq, text,
});
const final = (seq: number, text: string): JltEvent => ({
  ...base, type: "transcript.final", item_id: `i${seq}`, seq, text, stt_latency_ms: 900,
});
const created = (id: string, seq: number, text: string, seqs = [seq]): JltEvent => ({
  ...base, type: "segment.created", segment_id: id, seq, source_text: text,
  source_seqs: seqs, state: "translating",
});
const translated = (id: string, seq: number, ja: string, vi: string): JltEvent => ({
  ...base, type: "segment.translated", segment_id: id, seq, source_text: ja,
  target_text: vi, state: "translated", translation_latency_ms: 1000,
  e2e_latency_ms: 2000, usage: null,
});

describe("partials", () => {
  it("shows the in-progress turn separately from finished lines", () => {
    const state = reduce([partial(1, "来週の")]);
    expect(state.partial).toEqual({ seq: 1, text: "来週の" });
    expect(state.lines).toHaveLength(0);
  });

  it("replaces the partial wholesale, because the model revises earlier words", () => {
    const state = reduce([partial(1, "来週のリリー"), partial(1, "来週のリリース")]);
    expect(state.partial?.text).toBe("来週のリリース");
  });

  it("clears the partial when its own turn finalises", () => {
    const state = reduce([partial(1, "来週の"), final(1, "来週のリリースです。")]);
    expect(state.partial).toBeNull();
  });

  it("keeps a partial that belongs to a different, newer turn", () => {
    const state = reduce([partial(2, "次の話"), final(1, "前の話です。")]);
    expect(state.partial?.seq).toBe(2);
  });
});

describe("finals and segments", () => {
  it("shows Japanese immediately on final, before the segment exists", () => {
    const state = reduce([final(1, "確認します。")]);
    expect(state.lines[0]?.japanese).toBe("確認します。");
    expect(state.lines[0]?.state).toBe("final");
  });

  it("replaces pending finals with the segment that merged them", () => {
    const state = reduce([
      final(1, "はい。"),
      final(2, "確認します。"),
      created("seg_1", 1, "はい。確認します。", [1, 2]),
    ]);
    expect(state.lines).toHaveLength(1);
    expect(state.lines[0]?.japanese).toBe("はい。確認します。");
    expect(state.lines[0]?.key).toBe("seg_seg_1");
  });

  it("attaches the Vietnamese to its own Japanese", () => {
    const state = reduce([
      final(1, "来週のリリースは延期されません。"),
      created("seg_1", 1, "来週のリリースは延期されません。"),
      translated("seg_1", 1, "来週のリリースは延期されません。", "Bản phát hành tuần tới sẽ không bị hoãn."),
    ]);
    expect(state.lines).toHaveLength(1);
    expect(state.lines[0]?.vietnamese).toBe("Bản phát hành tuần tới sẽ không bị hoãn.");
    expect(state.lines[0]?.state).toBe("translated");
  });

  it("orders by seq, never by arrival", () => {
    const state = reduce([
      translated("seg_2", 2, "二番目。", "Thứ hai."),
      translated("seg_1", 1, "一番目。", "Thứ nhất."),
    ]);
    expect(state.lines.map((l) => l.seq)).toEqual([1, 2]);
  });

  it("shows a translation whose creation event was missed", () => {
    const state = reduce([translated("seg_9", 9, "後から。", "Sau đó.")]);
    expect(state.lines).toHaveLength(1);
    expect(state.lines[0]?.vietnamese).toBe("Sau đó.");
  });

  it("does not duplicate a segment when created arrives twice", () => {
    const state = reduce([
      created("seg_1", 1, "テスト。"),
      created("seg_1", 1, "テスト。"),
    ]);
    expect(state.lines).toHaveLength(1);
  });

  it("keeps the Japanese visible when translation fails", () => {
    const state = reduce([
      created("seg_1", 1, "テストです。"),
      { ...base, type: "segment.failed", segment_id: "seg_1", seq: 1,
        source_text: "テストです。", state: "failed", reason: "timeout" },
    ]);
    expect(state.lines[0]?.japanese).toBe("テストです。");
    expect(state.lines[0]?.state).toBe("failed");
    expect(state.lines[0]?.failureReason).toBe("timeout");
  });

  it("bounds memory over a long meeting", () => {
    const events: JltEvent[] = [];
    for (let i = 1; i <= MAX_LINES + 50; i += 1) {
      events.push(translated(`seg_${i}`, i, `文${i}`, `Câu ${i}`));
    }
    const state = reduce(events);
    expect(state.lines).toHaveLength(MAX_LINES);
    // The newest lines are the ones kept.
    expect(state.lines[state.lines.length - 1]?.seq).toBe(MAX_LINES + 50);
  });
});

describe("status and lifecycle", () => {
  it("tracks capture, stt and translation state", () => {
    const state = reduce([
      { ...base, type: "status", capture: "running", stt: "connected",
        translation: "ready", audio_level_dbfs: -31.2, queue_depth: 2, detail: null },
    ]);
    expect(state.capture).toBe("running");
    expect(state.audioLevelDbfs).toBe(-31.2);
    expect(state.queueDepth).toBe(2);
  });

  it("starting a session clears the previous one entirely", () => {
    const dirty = reduce([translated("seg_1", 1, "古い。", "Cũ.")]);
    const state = subtitleReducer(dirty, {
      ...base, type: "session.started",
      source: { id: "app:x", kind: "application", name: "Teams" },
    });
    expect(state.lines).toHaveLength(0);
    expect(state.sessionId).toBe("s_1");
  });

  it("separates warnings from fatal errors", () => {
    const warned = reduce([
      { ...base, type: "error", severity: "warning", code: "stt_reconnecting",
        message: "Reconnecting.", retryable: true },
    ]);
    expect(warned.warning).toBe("Reconnecting.");
    expect(warned.fatal).toBeNull();

    const fatal = subtitleReducer(warned, {
      ...base, type: "error", severity: "fatal", code: "permission_denied",
      message: "Screen Recording permission required.", retryable: false,
    });
    expect(fatal.fatal).toBe("Screen Recording permission required.");
  });

  it("keeps the final stats when a session stops", () => {
    const state = reduce([
      { ...base, type: "session.stopped",
        stats: { session_id: "s_1", started_at: 0, duration_s: 60,
          audio_seconds_sent: 50, segments_final: 10, segments_translated: 10,
          segments_failed: 0, reconnects: 0,
          stt_latency_ms: { p50: 900, p95: 1600, n: 10 },
          translation_latency_ms: { p50: 1100, p95: 1900, n: 10 },
          e2e_latency_ms: { p50: 2100, p95: 3400, n: 10 },
          usage: { input_tokens: 100, output_tokens: 50 },
          estimated_cost_usd: 0.03 } },
    ]);
    expect(state.capture).toBe("stopped");
    expect(state.stats?.segments_translated).toBe(10);
  });
});
