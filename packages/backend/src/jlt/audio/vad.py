"""Client-side voice activity detection and turn segmentation.

Why this exists at all: `gpt-live-transcribe` supports neither `server_vad` nor
`semantic_vad` in a transcription session (verified — docs/openai-verification.md
§2.2). The API only emits a final transcript in response to
`input_audio_buffer.commit`, so *the client must decide where turns end*.
Without this module the application would produce partial Japanese text forever
and never a single Vietnamese translation.

The detector is deliberately simple — short-time energy with a hangover timer.
Meeting audio arrives already mixed and level-controlled by Teams, so the
elaborate noise-robustness of a learned VAD buys little here, and a transparent
detector is one a user can actually tune from the settings panel.

This module is pure and synchronous: feed it frames, get back actions. That
makes every timing rule testable without a clock or an event loop.
"""

from __future__ import annotations

import math
import struct
from collections import deque
from dataclasses import dataclass
from enum import Enum

from ..config import FRAME_MS, VadSettings

_SILENCE_DBFS = -120.0


def frame_dbfs(frame: bytes) -> float:
    """RMS level of a signed 16-bit little-endian mono frame, in dBFS."""
    count = len(frame) // 2
    if count == 0:
        return _SILENCE_DBFS
    samples = struct.unpack(f"<{count}h", frame[: count * 2])
    total = 0.0
    for sample in samples:
        normalized = sample / 32768.0
        total += normalized * normalized
    rms = math.sqrt(total / count)
    if rms <= 0.0:
        return _SILENCE_DBFS
    return 20.0 * math.log10(rms)


class ActionKind(Enum):
    APPEND = "append"
    COMMIT = "commit"


@dataclass(frozen=True, slots=True)
class Action:
    kind: ActionKind
    #: Present for APPEND. Frame-aligned PCM16 ready to base64-encode.
    pcm: bytes = b""


class VadSegmenter:
    """Turns a stream of PCM frames into append/commit actions.

    State machine:

        SILENT ──(frame above threshold)──► MAYBE_SPEECH
        MAYBE_SPEECH ──(min_speech_ms sustained)──► SPEECH
        MAYBE_SPEECH ──(drops below)──► SILENT
        SPEECH ──(hangover_ms below threshold)──► COMMIT ─► SILENT
        SPEECH ──(max_turn_ms elapsed)──► COMMIT ─► SPEECH

    Pre-roll: the frames immediately before an onset are buffered and emitted
    with the first APPEND, so the first mora of a word is not clipped. Without
    it, Japanese utterances starting with an unvoiced consonant lose their
    opening sound and the transcript starts mid-word.

    Silence is not forwarded. Teams meetings are mostly silence, and
    `gpt-live-transcribe` bills per minute of audio sent, so withholding silence
    is a direct and significant cost reduction as well as a latency one.
    """

    PREROLL_MS = 300

    def __init__(self, settings: VadSettings, frame_ms: int = FRAME_MS) -> None:
        self._settings = settings
        self._frame_ms = frame_ms
        preroll_frames = max(1, self.PREROLL_MS // frame_ms)
        self._preroll: deque[bytes] = deque(maxlen=preroll_frames)

        self._in_speech = False
        self._candidate_ms = 0
        self._silence_ms = 0
        self._turn_ms = 0
        self._appended_ms = 0
        self._last_dbfs = _SILENCE_DBFS

    @property
    def last_dbfs(self) -> float:
        return self._last_dbfs

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def feed(self, frame: bytes) -> list[Action]:
        """Process one frame and return the actions it triggers."""
        level = frame_dbfs(frame)
        self._last_dbfs = level
        loud = level > self._settings.energy_threshold_dbfs
        actions: list[Action] = []

        if not self._in_speech:
            self._preroll.append(frame)
            if not loud:
                self._candidate_ms = 0
                return actions

            self._candidate_ms += self._frame_ms
            if self._candidate_ms < self._settings.min_speech_ms:
                # Not yet convinced. A door slam is loud for one frame.
                return actions

            # Onset confirmed: flush the pre-roll as one append so the turn
            # starts a little before the energy did.
            self._in_speech = True
            self._silence_ms = 0
            self._turn_ms = 0
            self._appended_ms = 0
            preroll = b"".join(self._preroll)
            self._preroll.clear()
            actions.append(Action(ActionKind.APPEND, preroll))
            self._appended_ms += len(preroll) // 2 * 1000 // 24_000
            self._turn_ms += self._frame_ms
            return actions

        # In speech: everything is forwarded, including the hangover tail,
        # because trailing silence is what tells the model the word finished.
        actions.append(Action(ActionKind.APPEND, frame))
        self._appended_ms += self._frame_ms
        self._turn_ms += self._frame_ms

        if loud:
            self._silence_ms = 0
        else:
            self._silence_ms += self._frame_ms
            if self._silence_ms >= self._settings.hangover_ms:
                actions.append(Action(ActionKind.COMMIT))
                self._reset_turn()
                return actions

        if self._turn_ms >= self._settings.max_turn_ms:
            # A presenter who never pauses still needs periodic finals,
            # otherwise no translation would appear for minutes.
            actions.append(Action(ActionKind.COMMIT))
            self._turn_ms = 0
            self._silence_ms = 0
            self._appended_ms = 0
            # Stay in speech: the talking has not stopped.

        return actions

    def flush(self) -> list[Action]:
        """Close any open turn, e.g. on pause or stop.

        Refuses to commit a turn with too little audio: committing an
        effectively empty buffer is an API error, not a transcript.
        """
        if self._in_speech and self._appended_ms >= self._settings.min_speech_ms:
            self._reset_turn()
            return [Action(ActionKind.COMMIT)]
        self._reset_turn()
        return []

    def _reset_turn(self) -> None:
        self._in_speech = False
        self._candidate_ms = 0
        self._silence_ms = 0
        self._turn_ms = 0
        self._appended_ms = 0
        self._preroll.clear()
