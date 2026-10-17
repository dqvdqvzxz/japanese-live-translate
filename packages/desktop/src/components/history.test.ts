import { describe, expect, it } from "vitest";
import { formatDuration, groupByDay, totalCost } from "./historyUtils";
import type { SessionRecord } from "../api/client";

function session(id: string, startedAt: number, cost = 0.1): SessionRecord {
  return {
    session_id: id,
    started_at: startedAt,
    ended_at: startedAt + 600,
    duration_s: 600,
    source_name: "Microsoft Teams",
    segments_translated: 5,
    segments_failed: 0,
    audio_seconds_sent: 400,
    usage: { input_tokens: 10, output_tokens: 5 },
    estimated_cost_usd: cost,
    summary: null,
  };
}

// Built from local time, not UTC. Grouping uses toLocaleDateString, so two
// UTC instants on the same UTC day can fall on different local days — which
// is exactly what broke the first version of this test at UTC+7.
const local = (y: number, m: number, d: number, h: number) =>
  new Date(y, m, d, h, 0, 0, 0).getTime() / 1000;

const DAY_A_MORNING = local(2026, 2, 2, 9);
const DAY_A_EVENING = local(2026, 2, 2, 18);
const DAY_B = local(2026, 2, 5, 12);

describe("groupByDay", () => {
  it("puts the newest day first", () => {
    const groups = groupByDay([session("a", DAY_A_MORNING), session("b", DAY_B)]);
    expect(groups[0]?.[1][0]?.session_id).toBe("b");
  });

  it("puts the newest session first within a day", () => {
    const groups = groupByDay([session("morning", DAY_A_MORNING), session("evening", DAY_A_EVENING)]);
    expect(groups).toHaveLength(1);
    expect(groups[0]?.[1].map((s) => s.session_id)).toEqual(["evening", "morning"]);
  });

  it("does not reorder the input array", () => {
    const input = [session("a", DAY_A_MORNING), session("b", DAY_B)];
    groupByDay(input);
    expect(input.map((s) => s.session_id)).toEqual(["a", "b"]);
  });

  it("handles an empty list", () => {
    expect(groupByDay([])).toEqual([]);
  });
});

describe("totalCost", () => {
  it("sums the estimates", () => {
    expect(totalCost([session("a", DAY_A_MORNING, 0.25), session("b", DAY_B, 0.5)])).toBeCloseTo(0.75);
  });

  it("is zero for no sessions", () => {
    expect(totalCost([])).toBe(0);
  });
});

describe("formatDuration", () => {
  it("shows minutes under an hour", () => {
    expect(formatDuration(600)).toBe("10m");
  });

  it("shows hours and padded minutes beyond one", () => {
    expect(formatDuration(3660)).toBe("1h 01m");
  });

  it("rounds rather than truncating to zero", () => {
    expect(formatDuration(59)).toBe("1m");
  });
});
