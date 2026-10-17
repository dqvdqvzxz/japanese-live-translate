"""Session lifecycle: wires the pipeline and owns its shutdown.

Everything the session needs is injected as a factory so the whole lifecycle
can be tested without ScreenCaptureKit, a network, or an API key.

Shutdown order matters and is not arbitrary:

  1. stop capture — no new audio enters the system;
  2. flush the VAD — the half-spoken sentence gets committed rather than lost;
  3. flush the segment manager — a held fragment is released;
  4. drain the translation queue — work already paid for completes;
  5. close the STT socket;
  6. cancel the pumps.

Doing this in any other order either loses the last sentence or leaks a task.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import time
from collections.abc import Callable

from .audio.capture_service import AudioCaptureService, CaptureError
from .audio.stream_manager import AudioStreamManager
from .config import Settings
from .events import EventDispatcher
from .history import HistoryManager
from .interfaces import AudioCapture, SttProvider, TranslationProvider
from .logging import get_logger
from .metrics import estimate_cost_usd
from .models import (
    AudioSource,
    CaptureState,
    ErrorEvent,
    Segment,
    SessionRecord,
    SessionStartedEvent,
    SessionStats,
    SessionStoppedEvent,
    SessionSummary,
    StatusEvent,
    SttState,
    Transcript,
    Usage,
)
from .secrets import Secret
from .segments.manager import SegmentManager
from .settings_manager import SettingsManager
from .stt.openai_realtime import OpenAIRealtimeSTTProvider
from .stt.transcript_manager import TranscriptManager
from .translation.provider import OpenAITranslationProvider
from .translation.queue import TranslationQueue
from .translation.summary import SummaryProvider

log = get_logger(__name__)

#: How often held fragments are flushed and status is published.
TICK_INTERVAL_S = 0.25
STATUS_INTERVAL_S = 1.0
#: How often the counter-only pipeline snapshot is written to the log.
HEARTBEAT_INTERVAL_S = 15.0

CaptureFactory = Callable[[], AudioCapture]
SttFactory = Callable[[Settings], SttProvider]
TranslationFactory = Callable[[Settings], TranslationProvider]
SummaryFactory = Callable[[Settings], SummaryProvider]


class SessionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SessionManager:
    def __init__(
        self,
        settings_manager: SettingsManager,
        dispatcher: EventDispatcher,
        *,
        capture_factory: CaptureFactory,
        stt_factory: SttFactory,
        translation_factory: TranslationFactory,
        history: HistoryManager | None = None,
        summary_factory: SummaryFactory | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings_manager = settings_manager
        self._dispatcher = dispatcher
        self._capture_factory = capture_factory
        self._stt_factory = stt_factory
        self._translation_factory = translation_factory
        self._summary_factory = summary_factory
        self._clock = clock

        # Not `history or HistoryManager()`: HistoryManager defines __len__, so
        # an empty one is falsy and that silently discarded the caller's
        # instance — including its database connection — leaving the app
        # persisting nothing while the settings said otherwise.
        self.history = history if history is not None else HistoryManager()
        self.session_id: str | None = None
        self.source: AudioSource | None = None
        self._capture_state: CaptureState = "idle"
        self._stt_state: SttState = "disconnected"

        self._capture: AudioCapture | None = None
        self._stt: SttProvider | None = None
        self._stream: AudioStreamManager | None = None
        self._transcripts: TranscriptManager | None = None
        self._segments: SegmentManager | None = None
        self._translations: TranslationQueue | None = None

        self._tasks: list[asyncio.Task[None]] = []
        self._started_at = 0.0
        self._started_wall = 0.0
        self._lock = asyncio.Lock()

    # -- state --------------------------------------------------------------

    @property
    def state(self) -> CaptureState:
        return self._capture_state

    @property
    def active(self) -> bool:
        return self._capture_state in ("starting", "running", "paused")

    def _set_capture_state(self, state: CaptureState, detail: str | None = None) -> None:
        self._capture_state = state
        self._publish_status(detail)

    async def list_sources(self) -> list[AudioSource]:
        """Enumerate capture targets.

        Stateless, and safe while a session is running: it spawns a short-lived
        helper invocation rather than touching the active capture.
        """
        return await self._capture_factory().list_sources()

    # -- lifecycle ----------------------------------------------------------

    async def start(self, source_id: str) -> str:
        async with self._lock:
            if self.active:
                raise SessionError("already_running", "A session is already running.")
            settings = self._settings_manager.settings
            if not settings.api_key_present:
                raise SessionError(
                    "api_key_missing",
                    "No OpenAI API key is stored. Add one in Settings before starting.",
                )

            self.session_id = f"s_{secrets.token_hex(8)}"
            self._started_at = self._clock()
            self._started_wall = time.time()
            self._capture_state = "starting"

            try:
                await self._build(settings, source_id)
            except Exception:
                # A partial build leaves half-constructed collaborators and,
                # worse, a running helper process. Tear it all down.
                await self._teardown()
                self._capture_state = "error"
                raise

            self._set_capture_state("running")
            assert self.source is not None
            self.history.begin_session(self.session_id, self.source.name, self._started_wall)
            self._dispatcher.publish(
                SessionStartedEvent(session_id=self.session_id, source=self.source)
            )
            log.info("session started", extra={"session_id": self.session_id})
            return self.session_id

    async def _build(self, settings: Settings, source_id: str) -> None:
        capture = self._capture_factory()
        self._capture = capture
        self.source = await capture.start(source_id)

        stt = self._stt_factory(settings)
        self._stt = stt
        await stt.connect()
        self._stt_state = "connected"

        self._stream = AudioStreamManager(stt, settings.vad)
        self._translations = TranslationQueue(
            self.session_id or "",
            self._translation_factory(settings),
            self._dispatcher,
            settings.translation,
            glossary=lambda: self._settings_manager.glossary.entries,
            on_settled=self._on_segment_settled,
            clock=self._clock,
        )
        await self._translations.start()

        self._segments = SegmentManager(
            settings.segmentation, repair=self._settings_manager.glossary.repair
        )
        self._transcripts = TranscriptManager(
            self.session_id or "",
            self._dispatcher,
            on_final=self._on_final,
            show_partials=settings.ui.show_partials,
        )

        self._tasks = [
            asyncio.create_task(self._audio_pump(), name="session-audio"),
            asyncio.create_task(self._stt_pump(), name="session-stt"),
            asyncio.create_task(self._tick(), name="session-tick"),
        ]

    async def pause(self) -> None:
        async with self._lock:
            if self._capture_state != "running":
                raise SessionError("not_running", "No running session to pause.")
            if self._stream is not None:
                await self._stream.pause()
            self._set_capture_state("paused")

    async def resume(self) -> None:
        async with self._lock:
            if self._capture_state != "paused":
                raise SessionError("not_paused", "The session is not paused.")
            if self._stream is not None:
                self._stream.resume()
            self._set_capture_state("running")

    async def stop(self) -> SessionStats:
        async with self._lock:
            if not self.active and self._capture_state != "error":
                raise SessionError("not_running", "No session to stop.")
            self._capture_state = "stopping"
            stats = await self._teardown()
            self._capture_state = "stopped"
            if self.session_id:
                self.history.end_session(_record_from_stats(stats, self.source))
                self.history.write_transcript_file(self.session_id)
                self._dispatcher.publish(
                    SessionStoppedEvent(session_id=self.session_id, stats=stats)
                )
            log.info("session stopped", extra={"session_id": self.session_id})
            return stats

    async def _teardown(self) -> SessionStats:
        # 1. No new audio.
        if self._capture is not None:
            with contextlib.suppress(Exception):
                await self._capture.stop()

        # 2 and 3. Do not lose the sentence that was in progress.
        if self._stream is not None:
            with contextlib.suppress(Exception):
                await self._stream.drain_and_stop()
        if self._segments is not None:
            for segment in self._segments.flush_all(self._clock()):
                self._enqueue(segment)

        # 4. Finish translations already in flight before closing anything.
        if self._translations is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._translations.stop(drain=True), timeout=15.0)

        # 5 and 6.
        if self._stt is not None:
            with contextlib.suppress(Exception):
                await self._stt.close()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

        stats = self.stats()
        self._tasks = []
        self._capture = None
        self._stt = None
        self._stream = None
        self._stt_state = "disconnected"
        return stats

    # -- pipeline -----------------------------------------------------------

    async def _audio_pump(self) -> None:
        capture, stream = self._capture, self._stream
        if capture is None or stream is None:
            return
        try:
            # `run` owns both halves: it feeds frames in and starts the worker
            # that drains the queue into the STT socket. Calling `offer` alone
            # would queue audio that nothing ever sends.
            await stream.run(capture.frames())
        except asyncio.CancelledError:
            raise
        except CaptureError as exc:
            self._fatal(exc.code, str(exc))
        except Exception as exc:
            self._fatal("capture_failed", f"Audio capture stopped: {type(exc).__name__}")

    async def _stt_pump(self) -> None:
        stt, transcripts = self._stt, self._transcripts
        if stt is None or transcripts is None:
            return
        try:
            async for event in stt.events():
                if event.kind == "connected":
                    self._stt_state = "connected"
                elif event.kind == "reconnecting":
                    self._stt_state = "reconnecting"
                elif event.kind == "error":
                    self._stt_state = "error"
                transcripts.handle(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._fatal("stt_failed", f"Transcription stopped: {type(exc).__name__}")

    async def _tick(self) -> None:
        """Flush held fragments and publish status on a fixed cadence."""
        last_status = 0.0
        last_heartbeat = 0.0
        try:
            while True:
                await asyncio.sleep(TICK_INTERVAL_S)
                if self._segments is not None and self._capture_state == "running":
                    for segment in self._segments.flush_due(self._clock()):
                        self._enqueue(segment)
                now = self._clock()
                if now - last_status >= STATUS_INTERVAL_S:
                    last_status = now
                    self._publish_status()
                if now - last_heartbeat >= HEARTBEAT_INTERVAL_S:
                    last_heartbeat = now
                    self._log_heartbeat()
        except asyncio.CancelledError:
            raise

    def _log_heartbeat(self) -> None:
        """A counter-only snapshot of the whole pipeline.

        Exists because "it is capturing but no text appears" is otherwise
        undiagnosable: these five numbers say immediately whether audio is
        arriving, whether the VAD is closing turns, whether the model is
        answering, and whether translation is running. No meeting content is
        included — counts, levels and states only.
        """
        stream, stt = self._stream, self._stt
        transcripts, queue = self._transcripts, self._translations
        log.info(
            "pipeline",
            extra={
                "capture": self._capture_state,
                "stt": self._stt_state,
                "level_dbfs": round(stream.level_dbfs, 1) if stream else None,
                "speech_audio_s": round(stream.audio_seconds_sent, 1) if stream else 0.0,
                "commits": stream.seq if stream else 0,
                "dropped_frames": stream.dropped_frames if stream else 0,
                "deltas": getattr(getattr(stt, "stats", None), "deltas", 0),
                "finals": transcripts.finals if transcripts else 0,
                "translated": queue.translated if queue else 0,
                "failed": queue.failed if queue else 0,
            },
        )

    def _on_final(self, transcript: Transcript) -> None:
        if self._segments is None:
            return
        for segment in self._segments.add(transcript, self._clock()):
            self._enqueue(segment)

    def _on_segment_settled(self, segment: Segment) -> None:
        """Bring stored history up to date once a translation lands."""
        if self.session_id:
            self.history.update(self.session_id, segment)

    def _enqueue(self, segment: Segment) -> None:
        if self._translations is None:
            return
        if self._translations.submit(segment) and self.session_id:
            self.history.add(self.session_id, segment)

    async def summarize(self, session_id: str) -> SessionSummary:
        """Generate and store a recap for a finished session.

        Refuses while that session is still running: summarising a meeting
        halfway through produces a recap that is quietly incomplete, which is
        worse than making the user wait for Stop.
        """
        if self._summary_factory is None:
            raise SessionError("summary_unavailable", "Summaries are not configured.")
        if self.active and session_id == self.session_id:
            raise SessionError("session_running", "Stop the session before summarising it.")
        segments = self.history.session_segments(session_id)
        if not segments:
            raise SessionError("nothing_to_summarize", "This session has no transcript.")

        provider = self._summary_factory(self._settings_manager.settings)
        try:
            summary = await provider.summarize(segments)
        finally:
            await provider.aclose()
        self.history.save_summary(session_id, summary)
        return summary

    # -- reporting ----------------------------------------------------------

    def _publish_status(self, detail: str | None = None) -> None:
        if not self.session_id:
            return
        level = None
        if self._capture is not None:
            level = self._capture.level_dbfs
        self._dispatcher.publish(
            StatusEvent(
                session_id=self.session_id,
                capture=self._capture_state,
                stt=self._stt_state,
                translation="degraded" if self._translation_degraded() else "ready",
                audio_level_dbfs=level,
                queue_depth=self._translations.depth if self._translations else 0,
                detail=detail,
            )
        )

    def _translation_degraded(self) -> bool:
        queue = self._translations
        if queue is None:
            return False
        return queue.failed > 0 and queue.failed >= max(3, queue.translated // 10)

    def _fatal(self, code: str, message: str) -> None:
        log.error("session fatal", extra={"code": code})
        self._capture_state = "error"
        if self.session_id:
            self._dispatcher.publish(
                ErrorEvent(
                    session_id=self.session_id,
                    severity="fatal",
                    code=code,
                    message=message,
                    retryable=False,
                )
            )

    def stats(self) -> SessionStats:
        queue = self._translations
        audio_seconds = self._stream.audio_seconds_sent if self._stream else 0.0
        usage = queue.usage if queue else Usage()
        stt_provider = self._stt
        reconnects = getattr(getattr(stt_provider, "stats", None), "reconnects", 0)
        return SessionStats(
            session_id=self.session_id or "",
            started_at=self._started_wall,
            duration_s=max(0.0, self._clock() - self._started_at) if self._started_at else 0.0,
            audio_seconds_sent=audio_seconds,
            segments_final=self._transcripts.finals if self._transcripts else 0,
            segments_translated=queue.translated if queue else 0,
            segments_failed=queue.failed if queue else 0,
            reconnects=int(reconnects),
            stt_latency_ms=(
                self._transcripts.latency.summary()
                if self._transcripts
                else SessionStats(session_id="", started_at=0, duration_s=0).stt_latency_ms
            ),
            translation_latency_ms=(
                queue.latency.summary()
                if queue
                else SessionStats(session_id="", started_at=0, duration_s=0).translation_latency_ms
            ),
            e2e_latency_ms=(
                queue.e2e_latency.summary()
                if queue
                else SessionStats(session_id="", started_at=0, duration_s=0).e2e_latency_ms
            ),
            usage=usage,
            estimated_cost_usd=estimate_cost_usd(audio_seconds, usage),
        )


def _record_from_stats(stats: SessionStats, source: AudioSource | None) -> SessionRecord:
    return SessionRecord(
        session_id=stats.session_id,
        started_at=stats.started_at,
        ended_at=time.time(),
        duration_s=stats.duration_s,
        source_name=source.name if source else "",
        segments_translated=stats.segments_translated,
        segments_failed=stats.segments_failed,
        audio_seconds_sent=stats.audio_seconds_sent,
        usage=stats.usage,
        estimated_cost_usd=stats.estimated_cost_usd or 0.0,
    )


# -- default factories -------------------------------------------------------


def default_factories(
    api_key: Secret | None,
    settings_manager: SettingsManager,
) -> tuple[CaptureFactory, SttFactory, TranslationFactory, SummaryFactory]:
    """Production wiring. Tests pass their own factories instead.

    `api_key` may be None so the app can start without one — the user needs a
    running UI in order to add a key. `SessionManager.start` refuses before any
    of these factories is called, so the None branch is unreachable in practice
    and raises loudly rather than silently constructing a broken client.
    """

    def require_key() -> Secret:
        if api_key is None:
            raise SessionError(
                "api_key_missing",
                "No OpenAI API key is stored. Add one in Settings before starting.",
            )
        return api_key

    def capture() -> AudioCapture:
        return AudioCaptureService()

    def stt(settings: Settings) -> SttProvider:
        return OpenAIRealtimeSTTProvider(
            require_key(), settings.stt, glossary=settings_manager.glossary.entries
        )

    def translation(settings: Settings) -> TranslationProvider:
        return OpenAITranslationProvider(require_key(), settings.translation)

    def summary(settings: Settings) -> SummaryProvider:
        return SummaryProvider(require_key(), settings.translation)

    return capture, stt, translation, summary
