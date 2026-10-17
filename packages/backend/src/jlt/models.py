"""Typed domain and wire models.

The event models mirror `schemas/events.json` exactly; `tests/test_schema_sync.py`
fails if they drift apart. The TypeScript mirror lives in
`packages/desktop/src/types/events.ts`.
"""

from __future__ import annotations

import time
from typing import Annotated, Literal

from pydantic import BaseModel, Field

CaptureState = Literal["idle", "starting", "running", "paused", "stopping", "stopped", "error"]
SttState = Literal["disconnected", "connecting", "connected", "reconnecting", "error"]
TranslationState = Literal["ready", "degraded", "error"]
SegmentState = Literal["partial", "final", "translating", "translated", "failed"]
FailureReason = Literal["timeout", "api_error", "cancelled", "queue_full"]
Severity = Literal["warning", "fatal"]


def now_ts() -> float:
    """Wall-clock event timestamp. Never used for latency maths — see `metrics`."""
    return time.time()


class AudioSource(BaseModel):
    id: str
    kind: Literal["application", "display"]
    name: str
    bundle_id: str | None = None
    pid: int | None = None
    is_recommended: bool = False


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


class LatencySummary(BaseModel):
    """Measured latency distribution. `p50`/`p95` are None when `n == 0`."""

    p50: float | None = None
    p95: float | None = None
    n: int = 0


class SessionStats(BaseModel):
    session_id: str
    started_at: float
    duration_s: float
    audio_seconds_sent: float = 0.0
    segments_final: int = 0
    segments_translated: int = 0
    segments_failed: int = 0
    reconnects: int = 0
    stt_latency_ms: LatencySummary = Field(default_factory=LatencySummary)
    translation_latency_ms: LatencySummary = Field(default_factory=LatencySummary)
    e2e_latency_ms: LatencySummary = Field(default_factory=LatencySummary)
    usage: Usage = Field(default_factory=Usage)
    estimated_cost_usd: float | None = None


# --- Events -----------------------------------------------------------------


class _Event(BaseModel):
    ts: float = Field(default_factory=now_ts)
    session_id: str


class TranscriptPartialEvent(_Event):
    type: Literal["transcript.partial"] = "transcript.partial"
    item_id: str
    seq: int
    #: Full accumulated text for the turn, not the raw delta. The model may
    #: revise earlier words, so the UI replaces the line rather than appending.
    text: str


class TranscriptFinalEvent(_Event):
    type: Literal["transcript.final"] = "transcript.final"
    item_id: str
    seq: int
    text: str
    stt_latency_ms: float | None = None


class SegmentCreatedEvent(_Event):
    type: Literal["segment.created"] = "segment.created"
    segment_id: str
    seq: int
    source_text: str
    source_seqs: list[int] = Field(default_factory=list)
    state: SegmentState = "translating"


class SegmentTranslatedEvent(_Event):
    type: Literal["segment.translated"] = "segment.translated"
    segment_id: str
    seq: int
    source_text: str
    target_text: str
    state: SegmentState = "translated"
    translation_latency_ms: float | None = None
    e2e_latency_ms: float | None = None
    usage: Usage | None = None


class SegmentFailedEvent(_Event):
    type: Literal["segment.failed"] = "segment.failed"
    segment_id: str
    seq: int
    source_text: str
    state: SegmentState = "failed"
    reason: FailureReason


class StatusEvent(_Event):
    type: Literal["status"] = "status"
    capture: CaptureState
    stt: SttState
    translation: TranslationState
    audio_level_dbfs: float | None = None
    queue_depth: int = 0
    detail: str | None = None


class SessionStartedEvent(_Event):
    type: Literal["session.started"] = "session.started"
    source: AudioSource


class SessionStoppedEvent(_Event):
    type: Literal["session.stopped"] = "session.stopped"
    stats: SessionStats


class ErrorEvent(_Event):
    type: Literal["error"] = "error"
    severity: Severity
    code: str
    #: Human readable and content-free. Meeting text must never appear here.
    message: str
    retryable: bool = False


Event = Annotated[
    TranscriptPartialEvent
    | TranscriptFinalEvent
    | SegmentCreatedEvent
    | SegmentTranslatedEvent
    | SegmentFailedEvent
    | StatusEvent
    | SessionStartedEvent
    | SessionStoppedEvent
    | ErrorEvent,
    Field(discriminator="type"),
]

EVENT_TYPES: frozenset[str] = frozenset(
    {
        "transcript.partial",
        "transcript.final",
        "segment.created",
        "segment.translated",
        "segment.failed",
        "status",
        "session.started",
        "session.stopped",
        "error",
    }
)


# --- Internal domain objects (never sent over the wire as-is) ---------------


class Transcript(BaseModel):
    """One STT turn, joined to API events by `item_id`, ordered by `seq`."""

    item_id: str
    #: Assigned locally at commit time. The API does not guarantee completion
    #: ordering, so this — not arrival order — defines sequence.
    seq: int
    text: str = ""
    is_final: bool = False
    committed_at: float = 0.0
    finalized_at: float | None = None


class Segment(BaseModel):
    """A translation unit: one or more merged final transcripts."""

    segment_id: str
    seq: int
    source_text: str
    source_seqs: list[int] = Field(default_factory=list)
    state: SegmentState = "final"
    target_text: str | None = None
    created_at: float = 0.0
    committed_at: float = 0.0
    translated_at: float | None = None
    usage: Usage | None = None
    failure_reason: FailureReason | None = None


SttEventKind = Literal["delta", "final", "connected", "reconnecting", "error"]


class SttEvent(BaseModel):
    """A normalized speech-to-text event.

    Defined here rather than in the STT package so that `jlt.interfaces` can
    reference it without depending on a concrete provider: the protocol and the
    implementation must agree on one type, not two structurally similar ones.
    """

    kind: SttEventKind
    item_id: str = ""
    #: Locally assigned ordering key; see `Transcript.seq`.
    seq: int = 0
    text: str = ""
    #: commit → completed, in milliseconds. Only set on `final`.
    latency_ms: float | None = None
    #: Monotonic instant the turn was committed. Carried through so end-to-end
    #: latency is measured from when the audio turn closed, not from when its
    #: transcript happened to arrive.
    committed_at: float | None = None
    code: str = ""
    message: str = ""

    model_config = {"frozen": True}


class SessionSummary(BaseModel):
    """An AI-generated recap of a finished session."""

    overview: str
    #: Concrete things somebody has to do, as stated in the meeting.
    action_items: list[str] = Field(default_factory=list)
    generated_at: float = 0.0
    model: str = ""


class SessionRecord(BaseModel):
    """A finished session, as it appears in the history list."""

    session_id: str
    #: What the user called this recording. Empty means "never named", and the
    #: UI falls back to `default_session_name`.
    name: str = ""
    started_at: float
    ended_at: float | None = None
    duration_s: float = 0.0
    source_name: str = ""
    segments_translated: int = 0
    segments_failed: int = 0
    audio_seconds_sent: float = 0.0
    usage: Usage = Field(default_factory=Usage)
    #: Projection from published unit prices, not a billed amount.
    estimated_cost_usd: float = 0.0
    summary: SessionSummary | None = None


class GlossaryEntry(BaseModel):
    ja: str
    vi: str
    note: str | None = None
