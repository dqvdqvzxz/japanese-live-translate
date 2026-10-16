"""Bounded, concurrent translation with ordered output.

Three constraints pull against each other:

  * **Concurrency** — translating segments one at a time would make a busy
    meeting fall steadily behind.
  * **Order** — subtitles must appear in the order they were spoken, and
    concurrent requests finish out of order.
  * **Liveness** — a single slow or failed translation must not hold up every
    later line indefinitely.

The reorder buffer resolves them. Jobs get a monotonic `order` at enqueue time,
workers run them concurrently, and results are released strictly in `order`.
When the head of the buffer stalls for longer than `reorder_stall_s`, the
buffer gives up on it and releases what it has, accepting one out-of-order line
rather than a frozen subtitle track.

Note `order` is separate from the wire `seq`. A segment's `seq` is the
transcript sequence it started at, which skips when finals are merged. `order`
is dense, which is what an in-order release needs.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ..config import TranslationSettings
from ..events import EventDispatcher
from ..interfaces import TranslationProvider
from ..logging import get_logger
from ..metrics import LatencyTracker
from ..models import (
    FailureReason,
    GlossaryEntry,
    Segment,
    SegmentCreatedEvent,
    SegmentFailedEvent,
    SegmentTranslatedEvent,
    Usage,
)
from .provider import TranslationError

log = get_logger(__name__)


@dataclass(order=True)
class _Job:
    order: int
    segment: Segment = field(compare=False)
    enqueued_at: float = field(default=0.0, compare=False)


@dataclass
class _Result:
    job: _Job
    target_text: str | None = None
    usage: Usage | None = None
    reason: FailureReason | None = None
    translation_latency_ms: float | None = None


class TranslationQueue:
    def __init__(
        self,
        session_id: str,
        provider: TranslationProvider,
        dispatcher: EventDispatcher,
        settings: TranslationSettings,
        *,
        glossary: Callable[[], list[GlossaryEntry]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session_id = session_id
        self._provider = provider
        self._dispatcher = dispatcher
        self._settings = settings
        self._glossary = glossary or (lambda: [])
        self._clock = clock

        self._queue: asyncio.Queue[_Job] = asyncio.Queue(maxsize=settings.max_queue)
        self._workers: list[asyncio.Task[None]] = []
        self._releaser: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()

        self._next_order = 0
        self._release_order = 0
        self._ready: dict[int, _Result] = {}
        self._blocked_since: float | None = None
        self._inflight = 0

        #: Already-translated pairs offered to later translations as context.
        self._context: list[tuple[str, str]] = []

        self.translated = 0
        self.failed = 0
        self.rejected = 0
        self.released_out_of_order = 0
        self.latency = LatencyTracker()
        self.e2e_latency = LatencyTracker()
        self.usage = Usage()

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        if self._workers:
            return
        self._workers = [
            asyncio.create_task(self._worker(), name=f"translate-{index}")
            for index in range(self._settings.concurrency)
        ]
        self._releaser = asyncio.create_task(self._release_loop(), name="translate-order")

    async def stop(self, *, drain: bool = True) -> None:
        """Stop workers. With `drain`, finish what is already queued first."""
        if drain:
            await self._queue.join()
            self._release_ready(force=True)
        for task in self._workers:
            task.cancel()
        for task in self._workers:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._workers.clear()
        if self._releaser is not None:
            self._releaser.cancel()
            try:
                await self._releaser
            except asyncio.CancelledError:
                pass
            self._releaser = None

    # -- input --------------------------------------------------------------

    def submit(self, segment: Segment) -> bool:
        """Enqueue a segment. Returns False if the queue is full.

        Rejection is newest-first and visible: the user is told a line could
        not be translated rather than quietly missing it. Dropping an older
        queued line instead would discard work already paid for.
        """
        job = _Job(order=self._next_order, segment=segment, enqueued_at=self._clock())
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            self.rejected += 1
            log.warning("translation queue full", extra={"seq": segment.seq})
            self._dispatcher.publish(
                SegmentFailedEvent(
                    session_id=self._session_id,
                    segment_id=segment.segment_id,
                    seq=segment.seq,
                    source_text=segment.source_text,
                    reason="queue_full",
                )
            )
            return False

        self._next_order += 1
        segment.state = "translating"
        self._dispatcher.publish(
            SegmentCreatedEvent(
                session_id=self._session_id,
                segment_id=segment.segment_id,
                seq=segment.seq,
                source_text=segment.source_text,
                source_seqs=list(segment.source_seqs),
                state="translating",
            )
        )
        return True

    # -- workers ------------------------------------------------------------

    async def _worker(self) -> None:
        while True:
            job = await self._queue.get()
            self._inflight += 1
            try:
                result = await self._translate(job)
            finally:
                self._inflight -= 1
                self._queue.task_done()
            self._ready[job.order] = result
            self._wake.set()

    async def _translate(self, job: _Job) -> _Result:
        started = self._clock()
        # Snapshot the context now: by the time this finishes, later segments
        # may have been translated, and feeding a line its own future would be
        # both wrong and non-deterministic.
        context = list(self._context[-self._settings.context_size :])
        try:
            async with asyncio.timeout(self._settings.timeout_s):
                text, usage = await self._provider.translate(
                    job.segment.source_text,
                    context=context,
                    glossary=self._glossary(),
                )
        except TimeoutError:
            log.warning("translation timed out", extra={"seq": job.segment.seq})
            return _Result(job=job, reason="timeout")
        except asyncio.CancelledError:
            raise
        except TranslationError as exc:
            log.warning(
                "translation failed",
                extra={"seq": job.segment.seq, "status": exc.status},
            )
            return _Result(job=job, reason="api_error")
        except Exception as exc:
            log.warning(
                "translation failed",
                extra={"seq": job.segment.seq, "error": type(exc).__name__},
            )
            return _Result(job=job, reason="api_error")

        return _Result(
            job=job,
            target_text=text,
            usage=usage,
            translation_latency_ms=(self._clock() - started) * 1000.0,
        )

    # -- ordered release ----------------------------------------------------

    async def _release_loop(self) -> None:
        while True:
            try:
                async with asyncio.timeout(0.1):
                    await self._wake.wait()
            except TimeoutError:
                pass
            self._wake.clear()
            self._release_ready()

    def _release_ready(self, *, force: bool = False) -> None:
        while True:
            result = self._ready.pop(self._release_order, None)
            if result is not None:
                self._blocked_since = None
                self._publish(result)
                self._release_order += 1
                continue

            if not self._ready:
                self._blocked_since = None
                return

            # Something later finished first. Wait a bounded time for the gap
            # to fill, then release anyway rather than freeze the subtitles.
            now = self._clock()
            if self._blocked_since is None:
                self._blocked_since = now
            stalled = (now - self._blocked_since) >= self._settings.reorder_stall_s
            if not (force or stalled):
                return

            skipped_to = min(self._ready)
            self.released_out_of_order += 1
            log.warning(
                "reorder buffer stalled; releasing out of order",
                extra={"expected": self._release_order, "released": skipped_to},
            )
            self._release_order = skipped_to
            self._blocked_since = None

    def _publish(self, result: _Result) -> None:
        segment = result.job.segment
        now = self._clock()

        if result.target_text is None:
            segment.state = "failed"
            segment.failure_reason = result.reason
            self.failed += 1
            self._dispatcher.publish(
                SegmentFailedEvent(
                    session_id=self._session_id,
                    segment_id=segment.segment_id,
                    seq=segment.seq,
                    source_text=segment.source_text,
                    reason=result.reason or "api_error",
                )
            )
            return

        segment.state = "translated"
        segment.target_text = result.target_text
        segment.translated_at = now
        segment.usage = result.usage
        self.translated += 1
        if result.usage is not None:
            self.usage = self.usage + result.usage
        if result.translation_latency_ms is not None:
            self.latency.record_ms(result.translation_latency_ms)

        e2e_ms = None
        if segment.committed_at:
            e2e_ms = (now - segment.committed_at) * 1000.0
            self.e2e_latency.record_ms(e2e_ms)

        # Context is appended in release order, so every translation sees the
        # same history it would have seen in a serial pipeline.
        self._context.append((segment.source_text, result.target_text))
        if len(self._context) > max(self._settings.context_size * 4, 16):
            del self._context[: -self._settings.context_size]

        self._dispatcher.publish(
            SegmentTranslatedEvent(
                session_id=self._session_id,
                segment_id=segment.segment_id,
                seq=segment.seq,
                source_text=segment.source_text,
                target_text=result.target_text,
                translation_latency_ms=result.translation_latency_ms,
                e2e_latency_ms=e2e_ms,
                usage=result.usage,
            )
        )

    # -- observability ------------------------------------------------------

    @property
    def depth(self) -> int:
        return self._queue.qsize() + self._inflight

    @property
    def context_pairs(self) -> list[tuple[str, str]]:
        return list(self._context)
