import { describe, expect, it } from "vitest";
import schema from "../../../../schemas/events.json";
import { EVENT_TYPES, isJltEvent, type JltEvent } from "./events";

/**
 * The schema is the contract. These tests fail if the TypeScript types drift
 * from `schemas/events.json`, which is the same invariant the backend asserts
 * in `tests/test_schema_sync.py`.
 */

const schemaEventTypes = Object.entries(
  schema.$defs as Record<string, { properties?: { type?: { const?: string } } }>,
)
  .filter(([name]) => name.endsWith("Event"))
  .map(([, def]) => def.properties?.type?.const)
  .filter((value): value is string => typeof value === "string");

describe("event schema sync", () => {
  it("declares every event type in the schema", () => {
    expect([...EVENT_TYPES].sort()).toEqual([...schemaEventTypes].sort());
  });

  it("declares no event type the schema does not have", () => {
    for (const type of EVENT_TYPES) {
      expect(schemaEventTypes).toContain(type);
    }
  });
});

describe("isJltEvent", () => {
  it("accepts a well-formed event", () => {
    const event: JltEvent = {
      type: "transcript.partial",
      ts: 1,
      session_id: "s_1",
      item_id: "item_1",
      seq: 1,
      text: "テスト",
    };
    expect(isJltEvent(event)).toBe(true);
  });

  it("rejects an unknown type, null, and non-objects", () => {
    expect(isJltEvent({ type: "not.a.real.event" })).toBe(false);
    expect(isJltEvent(null)).toBe(false);
    expect(isJltEvent("transcript.partial")).toBe(false);
    expect(isJltEvent({})).toBe(false);
  });
});
