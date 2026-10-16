from __future__ import annotations

import struct
from collections.abc import Iterator

import pytest

from jlt.config import FRAME_BYTES, SAMPLE_RATE_HZ


def pcm_frame(amplitude: int, frame_bytes: int = FRAME_BYTES) -> bytes:
    """A constant-amplitude frame. Deterministic, so level maths is exact."""
    count = frame_bytes // 2
    return struct.pack(f"<{count}h", *([amplitude] * count))


SILENCE = pcm_frame(0)
#: ~ -24 dBFS, comfortably above the default -45 dBFS threshold.
SPEECH = pcm_frame(2000)
#: ~ -60 dBFS, below the threshold: room tone, not speech.
QUIET = pcm_frame(30)


@pytest.fixture
def frames() -> Iterator[None]:
    yield None


__all__ = ["FRAME_BYTES", "QUIET", "SAMPLE_RATE_HZ", "SILENCE", "SPEECH", "pcm_frame"]
