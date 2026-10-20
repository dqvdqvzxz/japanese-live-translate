"""Supervisor logic for the audio helper.

Exercised against `fake_helper.py`, which speaks the real protocol. The Swift
binary itself still needs real-audio verification; this covers everything the
Python side is responsible for.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from jlt.audio.capture_service import (
    AudioCaptureService,
    CaptureError,
    PermissionDenied,
    SourceNotFound,
)

HELPER = Path(__file__).parent / "fake_helper.py"


@pytest.fixture(autouse=True)
def _clean_env():
    yield
    os.environ.pop("FAKE_HELPER_MODE", None)


# The service builds argv itself, so the fake is wrapped in a tiny shim script
# that forwards to the interpreter.
@pytest.fixture
def shim(tmp_path: Path) -> Path:
    path = tmp_path / "teams-audio-tap"
    path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{HELPER}" "$@"\n')
    path.chmod(0o755)
    return path


async def test_list_sources_parses_the_helper_output(shim: Path):
    os.environ["FAKE_HELPER_MODE"] = "ok"
    sources = await AudioCaptureService(helper_path=shim).list_sources()
    assert [s.id for s in sources] == ["app:com.microsoft.teams2", "display:1"]
    assert sources[0].is_recommended


async def test_permission_denied_is_a_typed_error(shim: Path):
    os.environ["FAKE_HELPER_MODE"] = "permission_denied"
    with pytest.raises(PermissionDenied) as excinfo:
        await AudioCaptureService(helper_path=shim).list_sources()
    assert "Screen Recording" in str(excinfo.value)


async def test_missing_helper_is_reported_clearly(tmp_path: Path):
    svc = AudioCaptureService(helper_path=tmp_path / "nope")
    with pytest.raises(CaptureError) as excinfo:
        await svc.start("app:com.microsoft.teams2")
    assert excinfo.value.code == "helper_missing"


async def test_source_not_found_surfaces_as_source_not_found(shim: Path):
    os.environ["FAKE_HELPER_MODE"] = "source_not_found"
    svc = AudioCaptureService(helper_path=shim)
    with pytest.raises(SourceNotFound):
        await svc.start("app:does.not.exist")
    assert not svc.is_running


async def test_frame_size_mismatch_is_rejected_rather_than_corrupting_audio(shim: Path):
    os.environ["FAKE_HELPER_MODE"] = "frame_mismatch"
    svc = AudioCaptureService(helper_path=shim)
    with pytest.raises(CaptureError) as excinfo:
        await svc.start("app:com.microsoft.teams2")
    assert excinfo.value.code == "frame_mismatch"


async def test_start_yields_frame_aligned_audio_then_stops_cleanly(shim: Path):
    os.environ["FAKE_HELPER_MODE"] = "ok"
    svc = AudioCaptureService(helper_path=shim)
    source = await svc.start("app:com.microsoft.teams2")
    assert source.name == "Microsoft Teams"
    assert svc.is_running

    collected = 0
    async for frame in svc.frames():
        assert len(frame) == 960, "frames must stay aligned"
        collected += 1
        if collected >= 20:
            break

    await svc.stop()
    assert not svc.is_running


async def test_stop_kills_a_helper_that_ignores_sigint(shim: Path):
    """No orphaned capture process may survive a session stop."""
    os.environ["FAKE_HELPER_MODE"] = "ignores_sigint"
    svc = AudioCaptureService(helper_path=shim)
    await svc.start("app:com.microsoft.teams2")
    process = svc._process  # type: ignore[attr-defined]
    await svc.stop()
    assert process is not None
    assert process.returncode is not None, "helper must not outlive the session"


async def test_stop_is_idempotent(shim: Path):
    os.environ["FAKE_HELPER_MODE"] = "ok"
    svc = AudioCaptureService(helper_path=shim)
    await svc.start("app:com.microsoft.teams2")
    await svc.stop()
    await svc.stop()


async def test_api_key_is_not_passed_to_the_helper(shim: Path, monkeypatch):
    """The capture helper has no use for the key, so it must never see it.

    The fake helper fails the start if `OPENAI_API_KEY` is present in its
    environment, so a successful start is the assertion.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-should-not-propagate-0000")
    os.environ["FAKE_HELPER_MODE"] = "assert_no_key"
    svc = AudioCaptureService(helper_path=shim)
    source = await svc.start("app:com.microsoft.teams2")
    assert source.name == "Microsoft Teams"
    await svc.stop()


def test_default_helper_path_resolves_inside_the_monorepo():
    """An off-by-one here once produced `packages/packages/audio-capture`."""
    from jlt.audio.capture_service import default_helper_path

    path = default_helper_path()
    assert "packages/packages" not in str(path)
    assert path.parts[-4:] == ("audio-capture", ".build", "release", "teams-audio-tap") or (
        path.parts[-4:] == ("audio-capture", ".build", "debug", "teams-audio-tap")
    )


def test_env_override_wins_over_discovery(monkeypatch, tmp_path):
    from jlt.audio.capture_service import default_helper_path

    monkeypatch.setenv("JLT_AUDIO_HELPER", str(tmp_path / "custom-tap"))
    assert default_helper_path() == tmp_path / "custom-tap"


async def test_every_source_entry_has_the_full_key_set(shim: Path):
    """The helper must emit explicit nulls, not omit keys.

    Swift's synthesised encoder drops nil optionals, which once left display
    sources with no `bundle_id` field at all — diverging from the documented
    protocol and from this fake.
    """
    os.environ["FAKE_HELPER_MODE"] = "ok"
    sources = await AudioCaptureService(helper_path=shim).list_sources()
    for source in sources:
        dumped = source.model_dump()
        assert set(dumped) == {"id", "kind", "name", "bundle_id", "pid", "is_recommended"}


async def test_the_mic_flag_is_passed_only_when_asked(shim: Path, monkeypatch):
    """The helper must not be told to record the room by accident."""
    recorded: list[list[str]] = []
    real = asyncio.create_subprocess_exec

    async def spy(*args, **kwargs):
        recorded.append(list(args))
        return await real(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    os.environ["FAKE_HELPER_MODE"] = "ok"

    svc = AudioCaptureService(helper_path=shim)
    await svc.start("app:x")
    await svc.stop()
    assert "--mic" not in recorded[-1]

    svc = AudioCaptureService(helper_path=shim)
    await svc.start("app:x", include_microphone=True)
    await svc.stop()
    assert "--mic" in recorded[-1]


async def test_the_helpers_arrival_stats_are_exposed(shim: Path):
    """`frames_in` only counts the helper's own clock, so what actually arrived
    from each input has to come from the helper — and be kept for the log."""
    os.environ["FAKE_HELPER_MODE"] = "ok"
    svc = AudioCaptureService(helper_path=shim)
    await svc.start("app:x", include_microphone=True)
    try:
        for _ in range(200):
            if svc.stats is not None:
                break
            await asyncio.sleep(0.02)
        assert svc.stats is not None, "no stats message was picked up"
        assert "type" not in svc.stats
        assert svc.stats["app"]["peak_dbfs"] == -23.7
        assert svc.stats["app"]["rejected"] == {"format": 0, "buffer_list": 0, "convert": 0}
        assert svc.stats["mic"]["buffers"] == 0
    finally:
        await svc.stop()
    assert svc.stats is not None, "the last stats must survive stop() for the final log line"


async def test_the_start_log_says_whether_the_microphone_was_requested(shim: Path, caplog):
    """A silent capture cannot be read without knowing if the mic was meant to
    be in it; the log used to record only the source id."""
    import logging

    os.environ["FAKE_HELPER_MODE"] = "ok"
    for requested in (False, True):
        caplog.clear()
        svc = AudioCaptureService(helper_path=shim)
        with caplog.at_level(logging.INFO, logger="jlt.audio.capture_service"):
            await svc.start("app:x", include_microphone=requested)
        await svc.stop()
        record = next(r for r in caplog.records if r.getMessage() == "capture started")
        assert record.microphone is requested  # type: ignore[attr-defined]


async def test_output_device_is_read_from_the_helper(shim: Path):
    os.environ["FAKE_HELPER_MODE"] = "ok"
    device = await AudioCaptureService(helper_path=shim).output_device()
    assert device.kind == "headphones"
    assert device.name == "JBL Quantum800"
    assert device.transport == "usb"


@pytest.mark.parametrize("mode", ["bad_output_json", "output_fails"])
async def test_output_device_never_raises_it_just_reads_unknown(shim: Path, mode: str):
    """The UI polls this. An unreadable device is informational, not an error."""
    os.environ["FAKE_HELPER_MODE"] = mode
    device = await AudioCaptureService(helper_path=shim).output_device()
    assert device.kind == "unknown"


async def test_output_device_with_a_missing_helper_reads_unknown(tmp_path: Path):
    device = await AudioCaptureService(helper_path=tmp_path / "nope").output_device()
    assert device.kind == "unknown"


async def test_helper_warnings_are_logged_with_their_text(shim: Path, caplog):
    """The code alone ("no_app_buffers") is not enough to read the log view."""
    import logging

    svc = AudioCaptureService(helper_path=shim)
    with caplog.at_level(logging.WARNING, logger="jlt.audio.capture_service"):
        svc._handle_control({"type": "warning", "code": "no_audio", "message": "No audio for 10s"})
        svc._handle_control({"type": "error", "code": "start_failed", "message": "It broke"})
    warning = next(r for r in caplog.records if r.getMessage() == "audio helper warning")
    assert warning.detail == "No audio for 10s"  # type: ignore[attr-defined]
    error = next(r for r in caplog.records if r.getMessage() == "audio helper error")
    assert error.code == "start_failed"  # type: ignore[attr-defined]
