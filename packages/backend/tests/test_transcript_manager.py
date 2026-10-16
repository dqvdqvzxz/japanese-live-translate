from __future__ import annotations

from jlt.events import EventDispatcher
from jlt.models import Transcript
from jlt.stt.openai_realtime import SttEvent
from jlt.stt.transcript_manager import TranscriptManager


class Recorder:
    """Captures published events without needing a WebSocket subscriber."""

    def __init__(self, dispatcher: EventDispatcher) -> None:
        self.events: list = []
        dispatcher.publish = self.events.append  # type: ignore[method-assign]


def build(**kwargs):
    dispatcher = EventDispatcher()
    recorder = Recorder(dispatcher)
    finals: list[Transcript] = []
    manager = TranscriptManager("s_1", dispatcher, on_final=finals.append, **kwargs)
    return manager, recorder, finals


def test_delta_publishes_a_partial():
    manager, recorder, _ = build()
    manager.handle(SttEvent(kind="delta", item_id="i1", seq=1, text="来週の"))
    assert [e.type for e in recorder.events] == ["transcript.partial"]
    assert recorder.events[0].text == "来週の"


def test_partials_can_be_turned_off():
    manager, recorder, _ = build(show_partials=False)
    manager.handle(SttEvent(kind="delta", item_id="i1", seq=1, text="x"))
    assert recorder.events == []


def test_final_publishes_and_forwards_to_the_sink():
    manager, recorder, finals = build()
    manager.handle(
        SttEvent(kind="final", item_id="i1", seq=1, text="確認します。", latency_ms=900.0)
    )
    assert [e.type for e in recorder.events] == ["transcript.final"]
    assert recorder.events[0].stt_latency_ms == 900.0
    assert [t.text for t in finals] == ["確認します。"]


def test_duplicate_final_for_the_same_seq_is_suppressed():
    """A reconnect can re-deliver a turn under a fresh item id."""
    manager, recorder, finals = build()
    event = SttEvent(kind="final", item_id="i1", seq=1, text="はい。")
    manager.handle(event)
    manager.handle(SttEvent(kind="final", item_id="i2", seq=1, text="はい。"))
    assert len(recorder.events) == 1
    assert len(finals) == 1
    assert manager.duplicates_suppressed == 1


def test_empty_final_is_dropped_as_a_vad_false_positive():
    manager, recorder, finals = build()
    manager.handle(SttEvent(kind="final", item_id="i1", seq=1, text="   "))
    assert recorder.events == []
    assert finals == []


def test_late_delta_does_not_overwrite_a_finished_line():
    manager, recorder, _ = build()
    manager.handle(SttEvent(kind="final", item_id="i1", seq=1, text="完了。"))
    manager.handle(SttEvent(kind="delta", item_id="i1", seq=1, text="stale"))
    assert [e.type for e in recorder.events] == ["transcript.final"]


def test_final_text_is_stripped():
    manager, _, finals = build()
    manager.handle(SttEvent(kind="final", item_id="i1", seq=1, text="  はい。  "))
    assert finals[0].text == "はい。"


def test_latency_is_tracked_only_when_reported():
    manager, _, _ = build()
    manager.handle(SttEvent(kind="final", item_id="i1", seq=1, text="a", latency_ms=500.0))
    manager.handle(SttEvent(kind="final", item_id="i2", seq=2, text="b", latency_ms=None))
    assert manager.latency.summary().n == 1


def test_reconnecting_surfaces_as_a_retryable_warning():
    manager, recorder, _ = build()
    manager.handle(SttEvent(kind="reconnecting", code="stt_reconnecting", message="Reconnecting."))
    event = recorder.events[0]
    assert event.type == "error"
    assert event.severity == "warning"
    assert event.retryable is True


def test_api_error_surfaces_as_a_non_retryable_warning():
    manager, recorder, _ = build()
    manager.handle(SttEvent(kind="error", code="invalid_request", message="bad keyword"))
    event = recorder.events[0]
    assert event.code == "invalid_request"
    assert event.retryable is False


def test_connected_publishes_nothing():
    manager, recorder, _ = build()
    manager.handle(SttEvent(kind="connected"))
    assert recorder.events == []


def test_published_events_never_carry_the_session_of_another_session():
    manager, recorder, _ = build()
    manager.handle(SttEvent(kind="final", item_id="i1", seq=1, text="x"))
    assert all(e.session_id == "s_1" for e in recorder.events)
