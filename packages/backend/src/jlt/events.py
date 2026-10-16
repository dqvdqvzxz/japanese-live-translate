"""In-process event bus.

One publisher (the session pipeline), many subscribers (WebSocket clients, the
history manager, the metrics collector). Each subscriber gets its own bounded
queue so a slow client cannot stall the pipeline.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from .logging import get_logger
from .models import Event

log = get_logger(__name__)

#: Finals must never be dropped; partials may be. See docs/architecture.md D-4.
_DROPPABLE: frozenset[str] = frozenset({"transcript.partial", "status"})


class Subscription:
    def __init__(self, maxsize: int) -> None:
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    def offer(self, event: Event) -> None:
        try:
            self._queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            pass

        if event.type in _DROPPABLE:
            # Drop this partial outright: a newer one is already on its way and
            # stale partial text is worthless.
            self.dropped += 1
            return

        # A final, a translation or an error. Make room by discarding the oldest
        # droppable item; if there is none, discard the oldest item regardless,
        # because an unbounded queue is a worse failure than a lost line.
        try:
            self._queue.get_nowait()
            self.dropped += 1
            self._queue.put_nowait(event)
        except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover
            self.dropped += 1

    async def __aiter__(self) -> AsyncIterator[Event]:
        while True:
            yield await self._queue.get()

    async def get(self) -> Event:
        return await self._queue.get()

    def qsize(self) -> int:
        return self._queue.qsize()


class EventDispatcher:
    """Fan-out with per-subscriber backpressure."""

    def __init__(self, queue_size: int = 256) -> None:
        self._queue_size = queue_size
        self._subscribers: set[Subscription] = set()

    def publish(self, event: Event) -> None:
        """Non-blocking by construction, so the pipeline never awaits a client."""
        for subscription in self._subscribers:
            subscription.offer(event)

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[Subscription]:
        subscription = Subscription(self._queue_size)
        self._subscribers.add(subscription)
        try:
            yield subscription
        finally:
            self._subscribers.discard(subscription)
            if subscription.dropped:
                log.warning("subscriber dropped events", extra={"dropped": subscription.dropped})

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
