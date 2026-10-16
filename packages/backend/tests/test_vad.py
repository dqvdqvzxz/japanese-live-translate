"""VAD / turn segmentation.

These rules are load-bearing: no commit means no final transcript from
`gpt-live-transcribe`, which means no translation at all.
"""

from __future__ import annotations

import pytest

from jlt.config import FRAME_MS, VadSettings
from jlt.audio.vad import ActionKind, VadSegmenter, frame_dbfs

from .conftest import QUIET, SILENCE, SPEECH, pcm_frame


def kinds(actions):
    return [a.kind for a in actions]


def feed_all(segmenter, frame, count):
    out = []
    for _ in range(count):
        out.extend(segmenter.feed(frame))
    return out


def test_frame_dbfs_silence_is_floor():
    assert frame_dbfs(SILENCE) == -120.0


def test_frame_dbfs_full_scale_is_zero():
    assert frame_dbfs(pcm_frame(32767)) == pytest.approx(0.0, abs=0.01)


def test_frame_dbfs_empty_frame_does_not_divide_by_zero():
    assert frame_dbfs(b"") == -120.0


def test_silence_produces_no_actions():
    segmenter = VadSegmenter(VadSettings())
    assert feed_all(segmenter, SILENCE, 100) == []
    assert not segmenter.in_speech


def test_quiet_room_tone_does_not_trigger_speech():
    segmenter = VadSegmenter(VadSettings())
    assert feed_all(segmenter, QUIET, 100) == []


def test_brief_transient_does_not_trigger_speech():
    """A door slam is loud for one frame. min_speech_ms must absorb it."""
    settings = VadSettings(min_speech_ms=300)
    segmenter = VadSegmenter(settings)
    actions = segmenter.feed(SPEECH)  # 20 ms, far below 300 ms
    assert actions == []
    assert not segmenter.in_speech


def test_onset_emits_preroll_before_first_speech_frame():
    settings = VadSettings(min_speech_ms=100)
    segmenter = VadSegmenter(settings, frame_ms=FRAME_MS)
    feed_all(segmenter, SILENCE, 20)  # fills the 300 ms pre-roll ring
    actions = feed_all(segmenter, SPEECH, 5)  # 100 ms sustained

    appends = [a for a in actions if a.kind is ActionKind.APPEND]
    assert appends, "onset must emit audio"
    # The first append carries the pre-roll: 300 ms = 15 frames of history.
    preroll_frames = len(appends[0].pcm) // len(SILENCE)
    assert preroll_frames == 15


def test_hangover_triggers_exactly_one_commit():
    settings = VadSettings(min_speech_ms=100, hangover_ms=200)
    segmenter = VadSegmenter(settings)
    feed_all(segmenter, SPEECH, 10)
    actions = feed_all(segmenter, SILENCE, 10)  # 200 ms of silence
    assert kinds(actions).count(ActionKind.COMMIT) == 1
    assert not segmenter.in_speech


def test_short_pause_does_not_commit():
    settings = VadSettings(min_speech_ms=100, hangover_ms=600)
    segmenter = VadSegmenter(settings)
    feed_all(segmenter, SPEECH, 10)
    actions = feed_all(segmenter, SILENCE, 5)  # 100 ms < 600 ms hangover
    assert ActionKind.COMMIT not in kinds(actions)
    assert segmenter.in_speech


def test_continuous_speech_commits_at_max_turn():
    """A presenter who never pauses must still produce periodic finals."""
    settings = VadSettings(min_speech_ms=100, max_turn_ms=1000)
    segmenter = VadSegmenter(settings)
    actions = feed_all(segmenter, SPEECH, 200)  # 4 s of unbroken speech
    commits = kinds(actions).count(ActionKind.COMMIT)
    assert commits >= 3
    assert segmenter.in_speech, "max-turn commit must not end the turn"


def test_trailing_silence_is_forwarded_with_the_turn():
    """The silence after a word is what tells the model the word ended."""
    settings = VadSettings(min_speech_ms=100, hangover_ms=200)
    segmenter = VadSegmenter(settings)
    feed_all(segmenter, SPEECH, 10)
    actions = feed_all(segmenter, SILENCE, 10)
    assert any(a.kind is ActionKind.APPEND for a in actions)


def test_flush_commits_an_open_turn():
    settings = VadSettings(min_speech_ms=100)
    segmenter = VadSegmenter(settings)
    feed_all(segmenter, SPEECH, 20)
    assert kinds(segmenter.flush()) == [ActionKind.COMMIT]


def test_flush_refuses_to_commit_an_empty_buffer():
    """Committing an empty audio buffer is an API error, not a transcript."""
    segmenter = VadSegmenter(VadSettings())
    assert segmenter.flush() == []


def test_flush_is_idempotent():
    settings = VadSettings(min_speech_ms=100)
    segmenter = VadSegmenter(settings)
    feed_all(segmenter, SPEECH, 20)
    assert segmenter.flush() != []
    assert segmenter.flush() == []
