"""Session lifecycle: start, pause, resume, stop, failure and cleanup.

Every collaborator is injected, so the whole lifecycle runs without
ScreenCaptureKit, a network, or an API key.
"""

from __future__ import annotations

import asyncio

import pytest

from jlt.config import Settings
from jlt.events import EventDispatcher
from jlt.models import SttEvent
from jlt.session import SessionError, SessionManager
from jlt.settings_manager import SettingsManager

from .conftest import SILENCE, SPEECH
from .fakes import FakeCapture, FakeTranslationProvider, ScriptedStt


class Recorder:
    def __init__(self, dispatcher: EventDispatcher) -> None:
        self.events: list = []
        dispatcher.publish = self.events.append  # type: ignore[method-assign]

    def of(self, event_type: str) -> list:
        return [e for e in self.events if e.type == event_type]


def build(tmp_path, *, frames=None, capture=None, stt=None, api_key_present=True):
    settings_manager = SettingsManager(tmp_path, api_key_present=api_key_present)
    dispatcher = EventDispatcher()
    recorder = Recorder(dispatcher)
    capture = capture or FakeCapture(frames)
    stt = stt or ScriptedStt()
    session = SessionManager(
        settings_manager,
        dispatcher,
        capture_factory=lambda: capture,
        stt_factory=lambda _s: stt,
        translation_factory=lambda _s: FakeTranslationProvider(),
    )
    return session, recorder, capture, stt


async def wait_for(predicate, limit=2.0):
    deadline = asyncio.get_running_loop().time() + limit
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


# --- start ------------------------------------------------------------------


async def test_start_publishes_session_started(tmp_path):
    session, recorder, capture, _ = build(tmp_path)
    session_id = await session.start("app:com.microsoft.teams2")
    assert session_id.startswith("s_")
    assert session.state == "running"
    assert capture.started
    assert recorder.of("session.started")
    await session.stop()


async def test_start_is_refused_without_an_api_key(tmp_path):
    """Capture must never begin when there is no key to send audio with."""
    session, _, capture, _ = build(tmp_path, api_key_present=False)
    with pytest.raises(SessionError) as excinfo:
        await session.start("app:x")
    assert excinfo.value.code == "api_key_missing"
    assert not capture.started


async def test_starting_twice_is_refused(tmp_path):
    session, _, _, _ = build(tmp_path)
    await session.start("app:x")
    with pytest.raises(SessionError) as excinfo:
        await session.start("app:x")
    assert excinfo.value.code == "already_running"
    await session.stop()


async def test_a_failed_start_leaves_nothing_running(tmp_path):
    """A half-built session must not leak a capture helper process."""
    capture = FakeCapture(fail_start=RuntimeError("no such source"))
    session, _, _, _ = build(tmp_path, capture=capture)
    with pytest.raises(RuntimeError):
        await session.start("app:missing")
    assert session.state == "error"
    assert capture.stopped, "capture must be released after a failed start"
    assert not session.active


# --- the pipeline -----------------------------------------------------------


async def test_audio_reaches_the_stt_provider(tmp_path):
    frames = [SILENCE] * 10 + [SPEECH] * 30 + [SILENCE] * 40
    session, _, _, stt = build(tmp_path, frames=frames)
    await session.start("app:x")
    assert await wait_for(lambda: len(stt.commits) >= 1)
    assert stt.appended, "speech must be forwarded"
    await session.stop()


async def test_a_final_transcript_becomes_a_translated_segment(tmp_path):
    session, recorder, _, stt = build(tmp_path)
    await session.start("app:x")

    stt.emit(SttEvent(kind="final", item_id="i1", seq=1, text="来週のリリースは延期されません。"))
    assert await wait_for(lambda: recorder.of("segment.translated"))

    translated = recorder.of("segment.translated")[0]
    assert translated.source_text == "来週のリリースは延期されません。"
    assert translated.target_text.startswith("VI<")
    await session.stop()


async def test_partials_are_published(tmp_path):
    session, recorder, _, stt = build(tmp_path)
    await session.start("app:x")
    stt.emit(SttEvent(kind="delta", item_id="i1", seq=1, text="来週の"))
    assert await wait_for(lambda: recorder.of("transcript.partial"))
    await session.stop()


async def test_history_records_translated_segments(tmp_path):
    session, recorder, _, stt = build(tmp_path)
    await session.start("app:x")
    stt.emit(SttEvent(kind="final", item_id="i1", seq=1, text="内容を確認しました。"))
    assert await wait_for(lambda: recorder.of("segment.translated"))
    assert len(session.history) == 1
    await session.stop()


async def test_a_held_fragment_is_flushed_by_the_tick(tmp_path):
    """A trailing fragment must not sit unseen until the session ends."""
    session, recorder, _, stt = build(tmp_path)
    session._settings_manager.update({"segmentation": {"incomplete_flush_ms": 100}})
    await session.start("app:x")
    stt.emit(SttEvent(kind="final", item_id="i1", seq=1, text="それで、"))
    assert await wait_for(lambda: recorder.of("segment.created"), limit=3.0)
    await session.stop()


# --- pause and resume -------------------------------------------------------


async def test_pause_and_resume_change_state(tmp_path):
    session, _, _, _ = build(tmp_path)
    await session.start("app:x")
    await session.pause()
    assert session.state == "paused"
    await session.resume()
    assert session.state == "running"
    await session.stop()


async def test_pause_requires_a_running_session(tmp_path):
    session, _, _, _ = build(tmp_path)
    with pytest.raises(SessionError):
        await session.pause()


async def test_resume_requires_a_paused_session(tmp_path):
    session, _, _, _ = build(tmp_path)
    await session.start("app:x")
    with pytest.raises(SessionError):
        await session.resume()
    await session.stop()


async def test_pause_stops_forwarding_audio(tmp_path):
    session, _, _, stt = build(tmp_path)
    await session.start("app:x")
    await session.pause()
    before = len(stt.appended)
    session._stream.offer(SPEECH)
    await asyncio.sleep(0.05)
    assert len(stt.appended) == before
    await session.stop()


# --- stop and cleanup -------------------------------------------------------


async def test_stop_releases_every_resource(tmp_path):
    session, recorder, capture, stt = build(tmp_path)
    await session.start("app:x")
    stats = await session.stop()

    assert session.state == "stopped"
    assert capture.stopped, "the capture helper must be released"
    assert stt.closed, "the OpenAI socket must be closed"
    assert all(task.done() for task in session._tasks or []), "no task may outlive the session"
    assert recorder.of("session.stopped")
    assert stats.session_id == session.session_id


async def test_stop_requires_a_session(tmp_path):
    session, _, _, _ = build(tmp_path)
    with pytest.raises(SessionError):
        await session.stop()


async def test_stop_flushes_a_sentence_still_in_progress(tmp_path):
    """Stopping mid-sentence must not discard what was already said."""
    session, recorder, _, stt = build(tmp_path)
    await session.start("app:x")
    stt.emit(SttEvent(kind="final", item_id="i1", seq=1, text="途中の話"))
    await asyncio.sleep(0.1)
    await session.stop()
    assert recorder.of("segment.created") or recorder.of("segment.translated")


async def test_a_second_session_can_start_after_a_stop(tmp_path):
    session, _, capture, _ = build(tmp_path)
    first = await session.start("app:x")
    await session.stop()
    second = await session.start("app:x")
    assert second != first
    assert capture.start_calls == 2
    await session.stop()


# --- failure handling -------------------------------------------------------


async def test_capture_failure_is_fatal_and_reported(tmp_path):
    class ExplodingCapture(FakeCapture):
        async def frames(self):
            raise RuntimeError("helper died")
            yield b""  # pragma: no cover - unreachable, keeps this a generator

    session, recorder, _, _ = build(tmp_path, capture=ExplodingCapture())
    await session.start("app:x")
    assert await wait_for(lambda: recorder.of("error"))
    error = recorder.of("error")[0]
    assert error.severity == "fatal"
    await session.stop()


async def test_stt_errors_surface_as_warnings_not_crashes(tmp_path):
    session, recorder, _, stt = build(tmp_path)
    await session.start("app:x")
    stt.emit(SttEvent(kind="error", code="invalid_request", message="bad keyword"))
    assert await wait_for(lambda: recorder.of("error"))
    assert recorder.of("error")[0].severity == "warning"
    assert session.state == "running", "a transient API error must not end the session"
    await session.stop()


# --- reporting --------------------------------------------------------------


async def test_status_is_published_periodically(tmp_path):
    session, recorder, _, _ = build(tmp_path)
    await session.start("app:x")
    assert await wait_for(lambda: len(recorder.of("status")) >= 2, limit=3.0)
    await session.stop()


async def test_stats_report_no_samples_rather_than_zero(tmp_path):
    session, _, _, _ = build(tmp_path)
    await session.start("app:x")
    stats = await session.stop()
    assert stats.stt_latency_ms.n == 0
    assert stats.stt_latency_ms.p50 is None
    assert stats.estimated_cost_usd is not None


async def test_settings_are_defaults_when_none_are_stored(tmp_path):
    manager = SettingsManager(tmp_path)
    assert manager.settings == Settings(api_key_present=False)


async def test_end_to_end_latency_includes_the_stt_stage(tmp_path):
    """e2e must be measured from the commit, so it is never below stt latency."""
    import time

    session, recorder, _, stt = build(tmp_path)
    await session.start("app:x")
    committed = time.monotonic() - 0.5  # as if the turn closed 500 ms ago
    stt.emit(
        SttEvent(
            kind="final",
            item_id="i1",
            seq=1,
            text="来週のリリースは延期されません。",
            latency_ms=500.0,
            committed_at=committed,
        )
    )
    assert await wait_for(lambda: recorder.of("segment.translated"))
    event = recorder.of("segment.translated")[0]
    assert event.e2e_latency_ms >= 500.0, "e2e must include the STT stage"
    await session.stop()
