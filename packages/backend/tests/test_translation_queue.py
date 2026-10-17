"""Bounded concurrency, ordered release, timeouts and failure reporting."""

from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest

from jlt.config import TranslationSettings
from jlt.events import EventDispatcher
from jlt.models import GlossaryEntry, Segment
from jlt.translation.provider import TranslationError
from jlt.translation.queue import TranslationQueue

from .fakes import FakeTranslationProvider


class Recorder:
    def __init__(self, dispatcher: EventDispatcher) -> None:
        self.events: list = []
        dispatcher.publish = self.events.append  # type: ignore[method-assign]

    def of(self, event_type: str) -> list:
        return [e for e in self.events if e.type == event_type]


def segment(seq: int, text: str | None = None, committed_at: float = 0.0) -> Segment:
    return Segment(
        segment_id=f"seg_{seq}",
        seq=seq,
        source_text=text or f"文{seq}。",
        source_seqs=[seq],
        committed_at=committed_at,
    )


def build(provider, **overrides):
    settings = TranslationSettings(
        **{"concurrency": 3, "timeout_s": 1.0, "reorder_stall_s": 0.2, **overrides}
    )
    dispatcher = EventDispatcher()
    recorder = Recorder(dispatcher)
    queue = TranslationQueue("s_1", provider, dispatcher, settings)
    return queue, recorder


async def settle(queue: TranslationQueue, recorder: Recorder, expected: int, limit=3.0):
    deadline = asyncio.get_running_loop().time() + limit
    while asyncio.get_running_loop().time() < deadline:
        done = len(recorder.of("segment.translated")) + len(recorder.of("segment.failed"))
        if done >= expected:
            return
        await asyncio.sleep(0.01)


async def test_submitting_publishes_created_then_translated():
    queue, recorder = build(FakeTranslationProvider())
    await queue.start()
    queue.submit(segment(1))
    await settle(queue, recorder, 1)
    await queue.stop()

    assert [e.type for e in recorder.events] == ["segment.created", "segment.translated"]
    assert recorder.of("segment.translated")[0].target_text == "VI<文1。>"


async def test_output_is_released_in_submission_order_despite_finishing_out_of_order():
    provider = FakeTranslationProvider(delays={"文1。": 0.25, "文2。": 0.10, "文3。": 0.01})
    # The stall release must not fire here, or this would be testing the
    # opposite behaviour. It needs to exceed the head job's own delay, which
    # the shared default (0.2 s) does not — that made this test race its own
    # fixture. The stall path has its own test below.
    queue, recorder = build(provider, reorder_stall_s=5.0)
    await queue.start()
    for seq in (1, 2, 3):
        queue.submit(segment(seq))
    await settle(queue, recorder, 3)
    await queue.stop()

    assert [e.seq for e in recorder.of("segment.translated")] == [1, 2, 3]
    # The provider really did finish them out of order.
    assert provider.calls == ["文1。", "文2。", "文3。"]


async def test_a_stalled_head_does_not_block_later_lines_forever():
    """One slow translation must not freeze the whole subtitle track."""
    provider = FakeTranslationProvider(delays={"文1。": 5.0})
    queue, recorder = build(provider, concurrency=3, timeout_s=10.0, reorder_stall_s=0.1)
    await queue.start()
    for seq in (1, 2, 3):
        queue.submit(segment(seq))
    await settle(queue, recorder, 2)

    released = [e.seq for e in recorder.of("segment.translated")]
    assert 2 in released and 3 in released
    assert queue.released_out_of_order >= 1
    await queue.stop(drain=False)


async def test_timeout_is_reported_as_a_failed_segment():
    provider = FakeTranslationProvider(delays={"文1。": 5.0})
    queue, recorder = build(provider, timeout_s=0.1)
    await queue.start()
    queue.submit(segment(1))
    await settle(queue, recorder, 1)
    await queue.stop(drain=False)

    failed = recorder.of("segment.failed")
    assert len(failed) == 1 and failed[0].reason == "timeout"


async def test_api_error_is_reported_as_a_failed_segment():
    provider = FakeTranslationProvider(errors={"文1。": TranslationError("boom", status=500)})
    queue, recorder = build(provider)
    await queue.start()
    queue.submit(segment(1))
    await settle(queue, recorder, 1)
    await queue.stop()

    assert recorder.of("segment.failed")[0].reason == "api_error"


async def test_a_failure_does_not_block_the_segments_after_it():
    provider = FakeTranslationProvider(errors={"文1。": TranslationError("boom")})
    queue, recorder = build(provider)
    await queue.start()
    for seq in (1, 2):
        queue.submit(segment(seq))
    await settle(queue, recorder, 2)
    await queue.stop()

    assert len(recorder.of("segment.failed")) == 1
    assert [e.seq for e in recorder.of("segment.translated")] == [2]


async def test_queue_full_rejects_the_newest_and_says_so():
    provider = FakeTranslationProvider(default_delay_s=5.0)
    queue, recorder = build(provider, max_queue=2, concurrency=1)
    await queue.start()
    accepted = [queue.submit(segment(seq)) for seq in range(1, 8)]
    await asyncio.sleep(0.05)
    await queue.stop(drain=False)

    assert accepted.count(False) >= 1
    assert queue.rejected >= 1
    assert any(e.reason == "queue_full" for e in recorder.of("segment.failed"))


async def test_concurrency_is_bounded():
    peak = 0
    current = 0

    class CountingProvider:
        async def translate(self, text, *, context=(), glossary=()):
            nonlocal peak, current
            current += 1
            peak = max(peak, current)
            try:
                await asyncio.sleep(0.05)
                return "vi", __import__("jlt.models", fromlist=["Usage"]).Usage()
            finally:
                current -= 1

    queue, recorder = build(CountingProvider(), concurrency=2)
    await queue.start()
    for seq in range(1, 7):
        queue.submit(segment(seq))
    await settle(queue, recorder, 6)
    await queue.stop()
    assert peak <= 2


async def test_context_accumulates_in_release_order():
    provider = FakeTranslationProvider(delays={"文1。": 0.15, "文2。": 0.01})
    queue, recorder = build(provider, context_size=3)
    await queue.start()
    for seq in (1, 2):
        queue.submit(segment(seq))
    await settle(queue, recorder, 2)
    await queue.stop()

    assert [ja for ja, _ in queue.context_pairs] == ["文1。", "文2。"]


async def test_context_is_passed_to_the_provider_but_never_retranslated():
    class CapturingProvider(FakeTranslationProvider):
        contexts: ClassVar[list] = []

        async def translate(self, text, *, context=(), glossary=()):
            CapturingProvider.contexts.append(list(context))
            return await super().translate(text, context=context, glossary=glossary)

    provider = CapturingProvider()
    queue, recorder = build(provider, context_size=2, concurrency=1)
    await queue.start()
    for seq in (1, 2, 3):
        queue.submit(segment(seq))
    await settle(queue, recorder, 3)
    await queue.stop()

    assert CapturingProvider.contexts[0] == []
    assert provider.calls == ["文1。", "文2。", "文3。"], "context must not be re-translated"


async def test_context_window_is_bounded():
    queue, recorder = build(FakeTranslationProvider(), context_size=2, concurrency=1)
    await queue.start()
    for seq in range(1, 40):
        queue.submit(segment(seq))
    await settle(queue, recorder, 39, limit=10.0)
    await queue.stop()
    assert len(queue.context_pairs) <= 16


async def test_end_to_end_latency_is_measured_from_the_commit():
    queue, recorder = build(FakeTranslationProvider())
    await queue.start()
    loop_now = asyncio.get_running_loop().time
    queue.submit(segment(1, committed_at=loop_now() - 0.0))
    await settle(queue, recorder, 1)
    await queue.stop()
    event = recorder.of("segment.translated")[0]
    assert event.e2e_latency_ms is not None


async def test_usage_is_accumulated_from_real_responses():
    queue, recorder = build(FakeTranslationProvider())
    await queue.start()
    for seq in (1, 2):
        queue.submit(segment(seq))
    await settle(queue, recorder, 2)
    await queue.stop()
    assert queue.usage.input_tokens == 20
    assert queue.usage.output_tokens == 10


async def test_stop_drains_outstanding_work_by_default():
    queue, recorder = build(FakeTranslationProvider(default_delay_s=0.05), concurrency=2)
    await queue.start()
    for seq in (1, 2, 3, 4):
        queue.submit(segment(seq))
    await queue.stop()
    assert len(recorder.of("segment.translated")) == 4


async def test_glossary_is_supplied_per_request():
    seen: list = []

    class GlossaryCapturing(FakeTranslationProvider):
        async def translate(self, text, *, context=(), glossary=()):
            seen.append(list(glossary))
            return await super().translate(text, context=context, glossary=glossary)

    settings = TranslationSettings(concurrency=1)
    dispatcher = EventDispatcher()
    recorder = Recorder(dispatcher)
    entries = [GlossaryEntry(ja="デプロイ", vi="triển khai")]
    queue = TranslationQueue(
        "s_1", GlossaryCapturing(), dispatcher, settings, glossary=lambda: entries
    )
    await queue.start()
    queue.submit(segment(1))
    await settle(queue, recorder, 1)
    await queue.stop()
    assert seen[0] == entries


@pytest.mark.parametrize("drain", [True, False])
async def test_stop_is_safe_in_both_modes(drain: bool):
    queue, _ = build(FakeTranslationProvider())
    await queue.start()
    await queue.stop(drain=drain)
