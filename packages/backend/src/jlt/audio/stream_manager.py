"""Bridges raw capture frames to the STT provider.

Responsibilities:
  * decouple the capture reader from the network with a bounded queue;
  * run the VAD segmenter and translate its actions into provider calls;
  * assign the monotonic `seq` at commit time — the ordering key the rest of
    the pipeline relies on, because the API does not guarantee completion
    ordering;
  * honour pause/resume without tearing down the capture or the socket.

Overflow policy is drop-oldest. If the network stalls, the newest audio is the
audio worth keeping; replaying a backlog of stale speech would make the
subtitles drift further behind with every second.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from ..config import FRAME_MS, SAMPLE_RATE_HZ, VadSettings
from ..interfaces import SttProvider
from ..logging import get_logger
from .vad import ActionKind, VadSegmenter

log = get_logger(__name__)


class AudioStreamManager:
    def __init__(
        self,
        stt: SttProvider,
        vad_settings: VadSettings,
        *,
        frame_ms: int = FRAME_MS,
        max_queued_frames: int = 250,  # ~5 s at 20 ms
    ) -> None:
        self._stt = stt
        self._segmenter = VadSegmenter(vad_settings, frame_ms=frame_ms)
        self._frame_ms = frame_ms
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=max_queued_frames)
        self._paused = False
        self._seq = 0
        self._dropped_frames = 0
        #: Frames handed over by the capture helper. Distinguishes "the helper
        #: is sending nothing" from "it is sending silence", which look
        #: identical from the level alone.
        self._frames_in = 0
        self._audio_seconds_sent = 0.0
        self._pump_task: asyncio.Task[None] | None = None

    # -- producer side ------------------------------------------------------

    def offer(self, frame: bytes) -> None:
        """Called from the capture reader. Never blocks, never awaits."""
        self._frames_in += 1
        if self._paused:
            return
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            # Drop the oldest frame to make room for the newest.
            try:
                self._queue.get_nowait()
                self._dropped_frames += 1
                self._queue.put_nowait(frame)
            except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover
                self._dropped_frames += 1

    async def run(self, frames: AsyncIterator[bytes]) -> None:
        """Consume the capture stream until it ends."""
        self._pump_task = asyncio.create_task(self._pump(), name="audio-pump")
        try:
            async for frame in frames:
                self.offer(frame)
        finally:
            await self.drain_and_stop()

    # -- consumer side ------------------------------------------------------

    async def _pump(self) -> None:
        while True:
            frame = await self._queue.get()
            await self._process(frame)

    async def _process(self, frame: bytes) -> None:
        for action in self._segmenter.feed(frame):
            if action.kind is ActionKind.APPEND:
                if action.pcm:
                    await self._stt.send_audio(action.pcm)
                    self._audio_seconds_sent += len(action.pcm) / 2 / SAMPLE_RATE_HZ
            else:
                await self._commit()

    async def _commit(self) -> None:
        self._seq += 1
        await self._stt.commit(self._seq)

    # -- control ------------------------------------------------------------

    async def drain_pending(self) -> None:
        """Process every frame currently queued, without waiting for more."""
        while not self._queue.empty():
            await self._process(self._queue.get_nowait())

    async def pause(self) -> None:
        """Stop forwarding audio, but close the open turn first.

        Dropping an in-flight turn on pause would silently lose the sentence the
        user was listening to, so we commit it and let the transcript land.
        """
        if self._paused:
            return
        self._paused = True
        # Process what is already queued, then close the open turn, so the
        # sentence the user was listening to still lands.
        await self.drain_pending()
        await self._flush_segmenter()

    def resume(self) -> None:
        self._paused = False

    async def drain_and_stop(self) -> None:
        if self._pump_task is not None:
            self._pump_task.cancel()
            try:
                await self._pump_task
            except asyncio.CancelledError:
                pass
            self._pump_task = None
        # Process whatever is still queued before closing the turn.
        await self.drain_pending()
        await self._flush_segmenter()

    async def _flush_segmenter(self) -> None:
        for action in self._segmenter.flush():
            if action.kind is ActionKind.COMMIT:
                await self._commit()

    # -- observability ------------------------------------------------------

    @property
    def level_dbfs(self) -> float:
        return self._segmenter.last_dbfs

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def frames_in(self) -> int:
        return self._frames_in

    @property
    def dropped_frames(self) -> int:
        return self._dropped_frames

    @property
    def audio_seconds_sent(self) -> float:
        return self._audio_seconds_sent

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def seq(self) -> int:
        return self._seq
