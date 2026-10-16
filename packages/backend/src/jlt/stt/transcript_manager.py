"""Turns normalized STT events into published transcript events.

This is the boundary between the transport layer and the rest of the pipeline:
`OpenAIRealtimeSTTProvider` knows about WebSockets, this knows about the
session. It publishes `transcript.partial` / `transcript.final` for the UI and
hands finals to a sink (the `SegmentManager`) for translation.

Duplicate suppression happens here as well as in the provider, deliberately:
the provider suppresses repeats of the same `item_id`, while this also
suppresses a final whose `seq` has already been finalized. A reconnect can
produce a fresh `item_id` for a turn that already landed, and without the
second check the user would see the same Japanese sentence twice and pay to
translate it twice.
"""

from __future__ import annotations

from collections.abc import Callable

from ..events import EventDispatcher
from ..logging import get_logger
from ..metrics import LatencyTracker
from ..models import (
    ErrorEvent,
    SttEvent,
    Transcript,
    TranscriptFinalEvent,
    TranscriptPartialEvent,
)

log = get_logger(__name__)

FinalSink = Callable[[Transcript], None]


class TranscriptManager:
    def __init__(
        self,
        session_id: str,
        dispatcher: EventDispatcher,
        *,
        on_final: FinalSink | None = None,
        show_partials: bool = True,
    ) -> None:
        self._session_id = session_id
        self._dispatcher = dispatcher
        self._on_final = on_final
        self._show_partials = show_partials

        self._finalized_seqs: set[int] = set()
        self._finalized_texts: set[tuple[int, str]] = set()
        self.latency = LatencyTracker()
        self.finals = 0
        self.partials = 0
        self.duplicates_suppressed = 0

    def handle(self, event: SttEvent) -> None:
        if event.kind == "delta":
            self._handle_delta(event)
        elif event.kind == "final":
            self._handle_final(event)
        elif event.kind in ("reconnecting", "error"):
            self._handle_problem(event)
        # "connected" carries no transcript information; SessionManager tracks
        # connection state from the same stream.

    def _handle_delta(self, event: SttEvent) -> None:
        if not self._show_partials or not event.text:
            return
        if event.seq in self._finalized_seqs:
            # A late delta for a turn that already finalized would overwrite the
            # finished line with older text.
            self.duplicates_suppressed += 1
            return
        self.partials += 1
        self._dispatcher.publish(
            TranscriptPartialEvent(
                session_id=self._session_id,
                item_id=event.item_id,
                seq=event.seq,
                text=event.text,
            )
        )

    def _handle_final(self, event: SttEvent) -> None:
        text = event.text.strip()
        if not text:
            # An empty final means the turn held no speech — a VAD false
            # positive. Nothing to show and nothing to translate.
            return
        if event.seq in self._finalized_seqs or (event.seq, text) in self._finalized_texts:
            self.duplicates_suppressed += 1
            log.info("duplicate final suppressed", extra={"seq": event.seq})
            return

        self._finalized_seqs.add(event.seq)
        self._finalized_texts.add((event.seq, text))
        self.finals += 1
        if event.latency_ms is not None:
            self.latency.record_ms(event.latency_ms)

        self._dispatcher.publish(
            TranscriptFinalEvent(
                session_id=self._session_id,
                item_id=event.item_id,
                seq=event.seq,
                text=text,
                stt_latency_ms=event.latency_ms,
            )
        )
        if self._on_final is not None:
            self._on_final(
                Transcript(
                    item_id=event.item_id,
                    seq=event.seq,
                    text=text,
                    is_final=True,
                )
            )

    def _handle_problem(self, event: SttEvent) -> None:
        self._dispatcher.publish(
            ErrorEvent(
                session_id=self._session_id,
                severity="warning",
                code=event.code or event.kind,
                message=event.message or "The transcription service reported a problem.",
                retryable=event.kind == "reconnecting",
            )
        )
