"""Latency and usage accounting.

All latencies come from `time.monotonic()`, never the wall clock, so an NTP
correction mid-meeting cannot produce a negative or absurd measurement.

Nothing here estimates. If no sample was observed, the summary reports `n=0`
and `None` percentiles rather than inventing a plausible number.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable

from .config import (
    PRICE_STT_PER_MINUTE,
    PRICE_TRANSLATION_INPUT_PER_1M,
    PRICE_TRANSLATION_OUTPUT_PER_1M,
)
from .models import LatencySummary, Usage


class LatencyTracker:
    """Keeps a bounded window of samples; percentiles are exact over the window."""

    def __init__(self, window: int = 2048) -> None:
        self._samples: deque[float] = deque(maxlen=window)
        self._count = 0

    def record_ms(self, value_ms: float) -> None:
        if value_ms < 0:
            return  # a clock anomaly is not a measurement
        self._samples.append(value_ms)
        self._count += 1

    def summary(self) -> LatencySummary:
        if not self._samples:
            return LatencySummary(p50=None, p95=None, n=0)
        ordered = sorted(self._samples)
        return LatencySummary(
            p50=_percentile(ordered, 0.50),
            p95=_percentile(ordered, 0.95),
            n=self._count,
        )

    def __len__(self) -> int:
        return self._count


def _percentile(ordered: list[float], q: float) -> float:
    """Nearest-rank percentile: the smallest value at or above rank ceil(q*n).

    No interpolation. With a few hundred samples per session, an interpolated
    percentile would imply a precision the measurement does not have, and the
    nearest-rank value is always an observation that actually happened.
    """
    if not ordered:
        raise ValueError("empty sample set")
    rank = max(1, min(len(ordered), math.ceil(q * len(ordered))))
    return ordered[rank - 1]


def estimate_cost_usd(audio_seconds: float, usage: Usage) -> float:
    """Projection from published unit prices — not billed spend.

    Shown beside measured token counts so the user can sanity-check the bill,
    and labelled as an estimate everywhere it surfaces.
    """
    stt = (audio_seconds / 60.0) * PRICE_STT_PER_MINUTE
    translation = (
        usage.input_tokens / 1_000_000 * PRICE_TRANSLATION_INPUT_PER_1M
        + usage.output_tokens / 1_000_000 * PRICE_TRANSLATION_OUTPUT_PER_1M
    )
    return round(stt + translation, 4)


def summarize(values: Iterable[float]) -> LatencySummary:
    tracker = LatencyTracker()
    for value in values:
        tracker.record_ms(value)
    return tracker.summary()
