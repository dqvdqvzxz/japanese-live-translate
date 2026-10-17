from __future__ import annotations

import pytest

from jlt.audio.stream_manager import AudioStreamManager
from jlt.config import VadSettings

from .conftest import SILENCE, SPEECH
from .fakes import FakeSttProvider, frames_from


def manager(stt, **kwargs):
    settings = VadSettings(min_speech_ms=100, hangover_ms=200, max_turn_ms=5000)
    return AudioStreamManager(stt, settings, **kwargs)


async def test_speech_then_silence_sends_audio_and_one_commit():
    stt = FakeSttProvider()
    mgr = manager(stt)
    stream = [SILENCE] * 20 + [SPEECH] * 20 + [SILENCE] * 20

    await mgr.run(frames_from(stream))

    assert stt.total_bytes > 0, "speech must reach the provider"
    assert stt.commits == [1], "exactly one turn should be committed"


async def test_seq_is_monotonic_across_turns():
    stt = FakeSttProvider()
    mgr = manager(stt)
    turn = [SPEECH] * 20 + [SILENCE] * 20
    await mgr.run(frames_from(turn * 3))
    assert stt.commits == [1, 2, 3]


async def test_silence_is_not_forwarded():
    """Billing is per minute of audio sent, and meetings are mostly silence."""
    stt = FakeSttProvider()
    mgr = manager(stt)
    await mgr.run(frames_from([SILENCE] * 500))
    assert stt.total_bytes == 0
    assert stt.commits == []


async def test_pause_commits_the_open_turn_and_stops_forwarding():
    stt = FakeSttProvider()
    mgr = manager(stt)
    for frame in [SPEECH] * 20:
        mgr.offer(frame)
    await mgr.drain_pending()

    await mgr.pause()
    assert stt.commits == [1], "a turn in flight must not be silently discarded"

    before = stt.total_bytes
    mgr.offer(SPEECH)
    assert mgr.queue_depth == 0, "paused manager must not queue audio"
    assert stt.total_bytes == before


async def test_resume_accepts_audio_again():
    stt = FakeSttProvider()
    mgr = manager(stt)
    await mgr.pause()
    mgr.resume()
    mgr.offer(SPEECH)
    assert mgr.queue_depth == 1


async def test_queue_drops_oldest_when_full():
    """Stale audio is worthless; the newest frames are the ones worth keeping."""
    stt = FakeSttProvider()
    mgr = manager(stt, max_queued_frames=4)
    for _ in range(10):
        mgr.offer(SPEECH)
    assert mgr.queue_depth == 4
    assert mgr.dropped_frames == 6


async def test_audio_seconds_sent_is_measured_not_estimated():
    stt = FakeSttProvider()
    mgr = manager(stt)
    await mgr.run(frames_from([SPEECH] * 50 + [SILENCE] * 20))
    expected = stt.total_bytes / 2 / 24_000
    assert mgr.audio_seconds_sent == pytest.approx(expected)


async def test_stop_flushes_an_open_turn():
    stt = FakeSttProvider()
    mgr = manager(stt)
    await mgr.run(frames_from([SPEECH] * 40))  # ends mid-speech
    assert stt.commits == [1], "stopping mid-sentence must not lose the sentence"


async def test_frames_in_counts_what_the_helper_delivered():
    """Separates "the helper sent nothing" from "it sent silence" — the two
    are indistinguishable from the level alone, and they have different causes."""
    stt = FakeSttProvider()
    mgr = manager(stt)
    for _ in range(7):
        mgr.offer(SILENCE)
    assert mgr.frames_in == 7
    assert stt.total_bytes == 0


async def test_frames_in_counts_frames_arriving_while_paused():
    """A paused session still proves the helper is alive."""
    stt = FakeSttProvider()
    mgr = manager(stt)
    await mgr.pause()
    mgr.offer(SPEECH)
    assert mgr.frames_in == 1
