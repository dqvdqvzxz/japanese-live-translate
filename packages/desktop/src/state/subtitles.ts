/**
 * Subtitle state: turns the backend event stream into what the user sees.
 *
 * The display unit is a *segment*, not an STT turn, because the backend merges
 * short finals before translating them. But a final arrives before its segment
 * does, and waiting for the segment would hold finished Japanese off screen for
 * no reason.
 *
 * So finals are shown immediately as pending lines keyed by `seq`, and when
 * `segment.created` arrives it consumes the `source_seqs` it covers and
 * replaces them with one segment entry. The Japanese text never disappears and
 * never flickers between the two representations.
 *
 * Ordering is always by `seq`, never by arrival. The backend assigns `seq` at
 * commit time precisely because the API does not guarantee event order.
 */

import type { JltEvent, SegmentState, SessionStats } from "../types/events";

export interface SubtitleLine {
  /** Stable React key. `seg_*` once translated, `seq_*` while pending. */
  key: string;
  /** Sort key. For a merged segment, the seq of its first transcript. */
  seq: number;
  japanese: string;
  vietnamese: string | null;
  state: SegmentState;
  /** Set when translation failed, so the UI can say why. */
  failureReason: string | null;
}

export interface LivePartial {
  seq: number;
  text: string;
}

export interface SubtitleState {
  lines: SubtitleLine[];
  /** The in-progress turn, rendered separately and replaced wholesale. */
  partial: LivePartial | null;
  capture: string;
  stt: string;
  translation: string;
  audioLevelDbfs: number | null;
  queueDepth: number;
  /** Most recent non-fatal problem, cleared when a session starts. */
  warning: string | null;
  fatal: string | null;
  sessionId: string | null;
  stats: SessionStats | null;
}

export const initialState: SubtitleState = {
  lines: [],
  partial: null,
  capture: "idle",
  stt: "disconnected",
  translation: "ready",
  audioLevelDbfs: null,
  queueDepth: 0,
  warning: null,
  fatal: null,
  sessionId: null,
  stats: null,
};

/** Keeps memory bounded over a long meeting. */
export const MAX_LINES = 500;

function sortedInsert(lines: SubtitleLine[], line: SubtitleLine): SubtitleLine[] {
  const next = [...lines, line];
  next.sort((a, b) => a.seq - b.seq);
  return next.length > MAX_LINES ? next.slice(next.length - MAX_LINES) : next;
}

export function subtitleReducer(state: SubtitleState, event: JltEvent): SubtitleState {
  switch (event.type) {
    case "transcript.partial": {
      // A later delta may revise earlier words, so the partial is replaced
      // whole rather than appended to.
      return { ...state, partial: { seq: event.seq, text: event.text } };
    }

    case "transcript.final": {
      const withoutPartial = state.partial?.seq === event.seq ? null : state.partial;
      const existing = state.lines.find((line) => line.key === `seq_${event.seq}`);
      if (existing) {
        return {
          ...state,
          partial: withoutPartial,
          lines: state.lines.map((line) =>
            line.key === `seq_${event.seq}` ? { ...line, japanese: event.text } : line,
          ),
        };
      }
      return {
        ...state,
        partial: withoutPartial,
        lines: sortedInsert(state.lines, {
          key: `seq_${event.seq}`,
          seq: event.seq,
          japanese: event.text,
          vietnamese: null,
          state: "final",
          failureReason: null,
        }),
      };
    }

    case "segment.created": {
      // The segment supersedes the pending finals it was merged from.
      const consumed = new Set((event.source_seqs ?? [event.seq]).map((s) => `seq_${s}`));
      const remaining = state.lines.filter(
        (line) => !consumed.has(line.key) && line.key !== `seg_${event.segment_id}`,
      );
      return {
        ...state,
        lines: sortedInsert(remaining, {
          key: `seg_${event.segment_id}`,
          seq: event.seq,
          japanese: event.source_text,
          vietnamese: null,
          state: "translating",
          failureReason: null,
        }),
      };
    }

    case "segment.translated": {
      const key = `seg_${event.segment_id}`;
      const found = state.lines.some((line) => line.key === key);
      const updated: SubtitleLine = {
        key,
        seq: event.seq,
        japanese: event.source_text,
        vietnamese: event.target_text,
        state: "translated",
        failureReason: null,
      };
      return {
        ...state,
        lines: found
          ? state.lines.map((line) => (line.key === key ? updated : line))
          : // A translation whose `created` we missed (reconnect, late join)
            // must still be shown rather than dropped.
            sortedInsert(state.lines, updated),
      };
    }

    case "segment.failed": {
      const key = `seg_${event.segment_id}`;
      const consumed = new Set([key]);
      const failed: SubtitleLine = {
        key,
        seq: event.seq,
        japanese: event.source_text,
        vietnamese: null,
        state: "failed",
        failureReason: event.reason,
      };
      const found = state.lines.some((line) => consumed.has(line.key));
      return {
        ...state,
        lines: found
          ? state.lines.map((line) => (line.key === key ? failed : line))
          : sortedInsert(state.lines, failed),
      };
    }

    case "status":
      return {
        ...state,
        capture: event.capture,
        stt: event.stt,
        translation: event.translation,
        audioLevelDbfs: event.audio_level_dbfs,
        queueDepth: event.queue_depth,
      };

    case "session.started":
      return {
        ...initialState,
        sessionId: event.session_id,
        capture: "running",
      };

    case "session.stopped":
      return { ...state, capture: "stopped", stats: event.stats, partial: null };

    case "error":
      return event.severity === "fatal"
        ? { ...state, fatal: event.message }
        : { ...state, warning: event.message };

    default:
      return state;
  }
}

/** Everything the user should be reading, oldest first. */
export function visibleLines(state: SubtitleState): SubtitleLine[] {
  return state.lines;
}

export function isLive(state: SubtitleState): boolean {
  return state.capture === "running" || state.capture === "starting";
}
