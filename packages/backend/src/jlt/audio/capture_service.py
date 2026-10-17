"""Supervises the Swift `teams-audio-tap` helper.

Contract (docs/api-design.md §2):
  * stdout  — nothing but raw 24 kHz mono PCM16 bytes;
  * stderr  — newline-delimited JSON control messages;
  * SIGINT  — graceful stop, flush, exit 0.

The helper is a separate process on purpose: ScreenCaptureKit has no Python
binding, and a crash in the capture path must not take session state with it.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from ..config import CHANNELS, FRAME_BYTES, FRAME_MS, SAMPLE_RATE_HZ
from ..logging import get_logger
from ..models import AudioSource

log = get_logger(__name__)

_EXIT_PERMISSION_DENIED = 2
_EXIT_SOURCE_NOT_FOUND = 3
_GRACEFUL_STOP_TIMEOUT_S = 3.0


class CaptureError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PermissionDenied(CaptureError):
    pass


class SourceNotFound(CaptureError):
    pass


def default_helper_path() -> Path:
    """Locate the helper binary.

    Order: explicit env override, the bundled Resources path (release), the
    SwiftPM release build (development), the SwiftPM debug build.
    """
    override = os.environ.get("JLT_AUDIO_HELPER")
    if override:
        return Path(override)

    on_path = shutil.which("teams-audio-tap")
    if on_path:
        return Path(on_path)

    # Walk up until the monorepo root is recognisable, rather than counting
    # parent directories: an off-by-one there produced a path like
    # `packages/packages/audio-capture` and a confusing "helper not found".
    here = Path(__file__).resolve()
    fallback: Path | None = None
    for ancestor in here.parents:
        capture_dir = ancestor / "packages" / "audio-capture" / ".build"
        if not capture_dir.parent.is_dir():
            continue
        candidates = [
            capture_dir / "release" / "teams-audio-tap",
            capture_dir / "debug" / "teams-audio-tap",
        ]
        fallback = fallback or candidates[0]
        for candidate in candidates:
            if candidate.exists():
                return candidate
    return fallback or Path("teams-audio-tap")


class AudioCaptureService:
    """Implements `jlt.interfaces.AudioCapture` on top of the Swift helper."""

    def __init__(
        self,
        helper_path: Path | None = None,
        *,
        frame_ms: int = FRAME_MS,
    ) -> None:
        self._helper = helper_path or default_helper_path()
        self._frame_ms = frame_ms
        self._process: asyncio.subprocess.Process | None = None
        self._control_task: asyncio.Task[None] | None = None
        self._level_dbfs: float | None = None
        self._source: AudioSource | None = None
        self._started = asyncio.Event()
        self._start_error: CaptureError | None = None
        self._warnings: list[dict[str, Any]] = []
        self._stopped_stats: dict[str, Any] | None = None
        #: Called with (code, message) for warnings the helper reports while
        #: running. Without a route to the UI these were logged and nowhere
        #: else, so a denied microphone or a silent source looked like the app
        #: was simply not working.
        self.on_warning: Callable[[str, str], None] | None = None

    # -- discovery ----------------------------------------------------------

    async def list_sources(self) -> list[AudioSource]:
        process = await asyncio.create_subprocess_exec(
            str(self._helper),
            "--list-sources",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode == _EXIT_PERMISSION_DENIED:
            raise PermissionDenied(
                "permission_denied",
                "Screen Recording permission is required. Grant it in System Settings > "
                "Privacy & Security > Screen Recording, then restart the app.",
            )
        if process.returncode != 0:
            raise CaptureError(
                "enumeration_failed",
                _first_control_message(stderr) or "Could not list audio sources.",
            )

        payload = json.loads(stdout.decode("utf-8"))
        return [AudioSource.model_validate(item) for item in payload.get("sources", [])]

    # -- lifecycle ----------------------------------------------------------

    async def start(self, source_id: str, *, include_microphone: bool = False) -> AudioSource:
        if self._process is not None:
            raise CaptureError("already_running", "Capture is already running.")

        if not self._helper.exists():
            raise CaptureError(
                "helper_missing",
                f"Audio helper not found at {self._helper}. Run `make capture-build`.",
            )

        arguments = [
            str(self._helper),
            "--capture",
            source_id,
            "--rate",
            str(SAMPLE_RATE_HZ),
            "--channels",
            str(CHANNELS),
            "--frame-ms",
            str(self._frame_ms),
        ]
        if include_microphone:
            arguments.append("--mic")

        self._process = await asyncio.create_subprocess_exec(
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # The key is not in this child's environment; it has no need for it.
            env={k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"},
        )
        self._control_task = asyncio.create_task(self._read_control(), name="audio-control")

        done, _ = await asyncio.wait(
            [
                asyncio.create_task(self._started.wait()),
                asyncio.create_task(self._process.wait()),
            ],
            timeout=15.0,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            task.cancel()

        if self._start_error is not None:
            await self._terminate()
            raise self._start_error
        if not self._started.is_set():
            await self._terminate()
            raise CaptureError("start_timeout", "The audio helper did not start within 15 seconds.")

        assert self._source is not None
        log.info("capture started", extra={"source_id": source_id})
        return self._source

    async def frames(self) -> AsyncIterator[bytes]:
        """Yield frame-aligned PCM chunks.

        `readexactly` guarantees alignment: a short read at shutdown raises
        `IncompleteReadError`, and a partial trailing frame is discarded rather
        than forwarded, because half a frame would desynchronise the stream.
        """
        if self._process is None or self._process.stdout is None:
            raise CaptureError("not_running", "Capture is not running.")
        stdout = self._process.stdout
        frame_bytes = SAMPLE_RATE_HZ * 2 * self._frame_ms // 1000
        while True:
            try:
                chunk = await stdout.readexactly(frame_bytes)
            except asyncio.IncompleteReadError:
                return
            if not chunk:
                return
            yield chunk

    async def stop(self) -> None:
        process = self._process
        if process is None:
            return
        self._process = None

        if process.returncode is None:
            try:
                process.send_signal(signal.SIGINT)
                await asyncio.wait_for(process.wait(), timeout=_GRACEFUL_STOP_TIMEOUT_S)
            except (TimeoutError, ProcessLookupError):
                log.warning("audio helper did not stop gracefully; killing")
                with _suppress_process_errors():
                    process.kill()
                    await process.wait()

        if self._control_task is not None:
            self._control_task.cancel()
            with _suppress_process_errors():
                await self._control_task
            self._control_task = None

        log.info(
            "capture stopped",
            extra={"stats": self._stopped_stats, "warnings": len(self._warnings)},
        )
        self._level_dbfs = None
        self._started.clear()

    async def _terminate(self) -> None:
        process, self._process = self._process, None
        if process is not None and process.returncode is None:
            with _suppress_process_errors():
                process.kill()
                await process.wait()
        if self._control_task is not None:
            self._control_task.cancel()
            self._control_task = None

    # -- control channel ----------------------------------------------------

    async def _read_control(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        async for raw in process.stderr:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                log.warning("unparseable control line from audio helper")
                continue
            self._handle_control(message)

    def _handle_control(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "ready":
            if message.get("frame_bytes") != FRAME_BYTES:
                # A mismatch here would silently corrupt every frame boundary.
                self._start_error = CaptureError(
                    "frame_mismatch",
                    "Audio helper frame size does not match the backend configuration.",
                )
                self._started.set()
        elif kind == "started":
            self._source = AudioSource(
                id=str(message.get("source_id", "")),
                kind="display"
                if str(message.get("source_id", "")).startswith("display:")
                else "application",
                name=str(message.get("name", "")),
            )
            self._started.set()
        elif kind == "level":
            self._level_dbfs = float(message.get("rms_dbfs", -120.0))
        elif kind == "warning":
            self._warnings.append(message)
            log.warning("audio helper warning", extra={"code": message.get("code")})
        elif kind == "error":
            code = str(message.get("code", "capture_error"))
            text = str(message.get("message", "Audio capture failed."))
            self._start_error = (
                PermissionDenied(code, text)
                if code == "permission_denied"
                else SourceNotFound(code, text)
                if code == "source_not_found"
                else CaptureError(code, text)
            )
            self._started.set()
        elif kind == "stopped":
            self._stopped_stats = message

    @property
    def level_dbfs(self) -> float | None:
        return self._level_dbfs

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None


def _first_control_message(stderr: bytes) -> str | None:
    for raw in stderr.decode("utf-8", errors="replace").splitlines():
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict) and "message" in message:
            return str(message["message"])
    return None


class _suppress_process_errors:
    """Swallow the expected races around killing a process that just exited."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: type[BaseException] | None, *_: object) -> bool:
        return exc_type is not None and issubclass(
            exc_type, (ProcessLookupError, asyncio.CancelledError)
        )
