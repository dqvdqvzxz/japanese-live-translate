/**
 * Backend → UI event types.
 *
 * Mirrors `schemas/events.json` and `packages/backend/src/jlt/models.py`.
 * `tests/test_schema_sync.py` fails if these three drift apart.
 */

export type CaptureState =
  | "idle" | "starting" | "running" | "paused" | "stopping" | "stopped" | "error";

export type SttState =
  | "disconnected" | "connecting" | "connected" | "reconnecting" | "error";

export type TranslationState = "ready" | "degraded" | "error";

export type SegmentState =
  | "partial" | "final" | "translating" | "translated" | "failed";

export type FailureReason = "timeout" | "api_error" | "cancelled" | "queue_full";

export interface AudioSource {
  id: string;
  kind: "application" | "display";
  name: string;
  bundle_id?: string | null;
  pid?: number | null;
  is_recommended?: boolean;
}

export interface Usage {
  input_tokens: number;
  output_tokens: number;
}

/** Measured only. `p50`/`p95` are null when `n === 0`. */
export interface LatencySummary {
  p50: number | null;
  p95: number | null;
  n: number;
}

export interface SessionStats {
  session_id: string;
  started_at: number;
  duration_s: number;
  audio_seconds_sent: number;
  segments_final: number;
  segments_translated: number;
  segments_failed: number;
  reconnects: number;
  stt_latency_ms: LatencySummary;
  translation_latency_ms: LatencySummary;
  e2e_latency_ms: LatencySummary;
  usage: Usage;
  /** Projection from published unit prices, not billed spend. */
  estimated_cost_usd: number | null;
}

interface EventBase {
  ts: number;
  session_id: string;
}

export interface TranscriptPartialEvent extends EventBase {
  type: "transcript.partial";
  item_id: string;
  /** Locally assigned ordering key. Never order by arrival. */
  seq: number;
  /** Full accumulated text for the turn — replace the line, do not append. */
  text: string;
}

export interface TranscriptFinalEvent extends EventBase {
  type: "transcript.final";
  item_id: string;
  seq: number;
  text: string;
  stt_latency_ms: number | null;
}

export interface SegmentCreatedEvent extends EventBase {
  type: "segment.created";
  segment_id: string;
  seq: number;
  source_text: string;
  source_seqs: number[];
  state: SegmentState;
}

export interface SegmentTranslatedEvent extends EventBase {
  type: "segment.translated";
  segment_id: string;
  seq: number;
  source_text: string;
  target_text: string;
  state: SegmentState;
  translation_latency_ms: number | null;
  e2e_latency_ms: number | null;
  usage: Usage | null;
}

export interface SegmentFailedEvent extends EventBase {
  type: "segment.failed";
  segment_id: string;
  seq: number;
  source_text: string;
  state: SegmentState;
  reason: FailureReason;
}

export interface StatusEvent extends EventBase {
  type: "status";
  capture: CaptureState;
  stt: SttState;
  translation: TranslationState;
  audio_level_dbfs: number | null;
  queue_depth: number;
  detail: string | null;
}

export interface SessionStartedEvent extends EventBase {
  type: "session.started";
  source: AudioSource;
}

export interface SessionStoppedEvent extends EventBase {
  type: "session.stopped";
  stats: SessionStats;
}

export interface ErrorEvent extends EventBase {
  type: "error";
  severity: "warning" | "fatal";
  code: string;
  /** Human readable and content-free: never contains meeting text. */
  message: string;
  retryable: boolean;
}

export type JltEvent =
  | TranscriptPartialEvent
  | TranscriptFinalEvent
  | SegmentCreatedEvent
  | SegmentTranslatedEvent
  | SegmentFailedEvent
  | StatusEvent
  | SessionStartedEvent
  | SessionStoppedEvent
  | ErrorEvent;

export const EVENT_TYPES = [
  "transcript.partial",
  "transcript.final",
  "segment.created",
  "segment.translated",
  "segment.failed",
  "status",
  "session.started",
  "session.stopped",
  "error",
] as const;

export function isJltEvent(value: unknown): value is JltEvent {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as { type?: unknown }).type === "string" &&
    (EVENT_TYPES as readonly string[]).includes((value as { type: string }).type)
  );
}
