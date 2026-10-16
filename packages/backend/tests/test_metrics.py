from __future__ import annotations

import pytest

from jlt.metrics import LatencyTracker, estimate_cost_usd
from jlt.models import Usage


def test_empty_tracker_reports_no_samples_rather_than_zero():
    """A missing measurement must never look like a fast one."""
    summary = LatencyTracker().summary()
    assert summary.n == 0
    assert summary.p50 is None
    assert summary.p95 is None


def test_percentiles_are_nearest_rank():
    tracker = LatencyTracker()
    for value in range(1, 101):
        tracker.record_ms(float(value))
    assert tracker.summary().p50 == 50.0
    assert tracker.summary().p95 == 95.0


def test_single_sample_is_both_percentiles():
    tracker = LatencyTracker()
    tracker.record_ms(42.0)
    summary = tracker.summary()
    assert summary.p50 == 42.0 and summary.p95 == 42.0 and summary.n == 1


def test_negative_samples_are_rejected_as_clock_anomalies():
    tracker = LatencyTracker()
    tracker.record_ms(-5.0)
    assert tracker.summary().n == 0


def test_window_bounds_memory_but_count_keeps_counting():
    tracker = LatencyTracker(window=10)
    for value in range(100):
        tracker.record_ms(float(value))
    assert len(tracker) == 100
    # Only the last 10 samples (90..99) are retained; nearest-rank p50 is 94.
    assert tracker.summary().p50 == pytest.approx(94.0)


def test_cost_estimate_uses_published_unit_prices():
    # 60 minutes of audio at $0.017/min = $1.02
    cost = estimate_cost_usd(3600.0, Usage(input_tokens=0, output_tokens=0))
    assert cost == pytest.approx(1.02, abs=1e-4)


def test_cost_estimate_includes_translation_tokens():
    cost = estimate_cost_usd(0.0, Usage(input_tokens=1_000_000, output_tokens=1_000_000))
    assert cost == pytest.approx(0.75 + 4.50, abs=1e-4)
