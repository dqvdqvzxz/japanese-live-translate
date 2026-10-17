/**
 * Pure helpers for the history list.
 *
 * Separated from the component so the grouping and the money arithmetic can be
 * tested without rendering anything.
 */

import type { SessionRecord } from "../api/client";

export function formatDuration(seconds: number): string {
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.round((total % 3600) / 60);
  return hours ? `${hours}h ${String(minutes).padStart(2, "0")}m` : `${minutes}m`;
}

/** Sessions grouped under a day heading, newest day and newest session first. */
export function groupByDay(sessions: SessionRecord[]): [string, SessionRecord[]][] {
  const groups = new Map<string, SessionRecord[]>();
  // Copied before sorting: reordering the caller's array would be a surprise.
  for (const session of [...sessions].sort((a, b) => b.started_at - a.started_at)) {
    const day = new Date(session.started_at * 1000).toLocaleDateString(undefined, {
      weekday: "short",
      year: "numeric",
      month: "short",
      day: "numeric",
    });
    const bucket = groups.get(day);
    if (bucket) bucket.push(session);
    else groups.set(day, [session]);
  }
  return [...groups.entries()];
}

export function totalCost(sessions: SessionRecord[]): number {
  return sessions.reduce((sum, session) => sum + session.estimated_cost_usd, 0);
}
