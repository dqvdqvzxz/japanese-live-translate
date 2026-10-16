"""Supervisor logic for the audio helper.

Exercised against `fake_helper.py`, which speaks the real protocol. The Swift
binary itself still needs real-audio verification; this covers everything the
Python side is responsible for.
"""

from __future__ import annotations

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
