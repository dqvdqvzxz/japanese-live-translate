"""Test doubles for the protocol seams in `jlt.interfaces`."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

from jlt.models import GlossaryEntry, Usage


@dataclass
class FakeSttProvider:
    """Records calls instead of touching the network."""

    appended: list[bytes] = field(default_factory=list)
    commits: list[int] = field(default_factory=list)
    connected: bool = False
    closed: bool = False
    send_delay_s: float = 0.0
    fail_on_send: Exception | None = None

    async def connect(self) -> None:
        self.connected = True

    async def send_audio(self, pcm: bytes) -> None:
        if self.fail_on_send is not None:
            raise self.fail_on_send
        if self.send_delay_s:
            await asyncio.sleep(self.send_delay_s)
        self.appended.append(pcm)

    async def commit(self, seq: int) -> None:
        self.commits.append(seq)

    def events(self) -> AsyncIterator[object]:  # pragma: no cover - unused here
        raise NotImplementedError

    async def close(self) -> None:
        self.closed = True

    @property
    def total_bytes(self) -> int:
        return sum(len(chunk) for chunk in self.appended)


@dataclass
class FakeTranslationProvider:
    """Deterministic translator with controllable latency and failures."""

    responses: dict[str, str] = field(default_factory=dict)
    delays: dict[str, float] = field(default_factory=dict)
    errors: dict[str, Exception] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    default_delay_s: float = 0.0

    async def translate(
        self,
        source_text: str,
        *,
        context: Sequence[tuple[str, str]] = (),
        glossary: Sequence[GlossaryEntry] = (),
    ) -> tuple[str, Usage]:
        self.calls.append(source_text)
        delay = self.delays.get(source_text, self.default_delay_s)
        if delay:
            await asyncio.sleep(delay)
        error = self.errors.get(source_text)
        if error is not None:
            raise error
        text = self.responses.get(source_text, f"VI<{source_text}>")
        return text, Usage(input_tokens=10, output_tokens=5)


async def frames_from(chunks: list[bytes]) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


class FakeWebSocket:
    """A scripted WebSocket.

    `inbox` holds frames the server will send, in order. A `Disconnect` marker
    raises instead, which is how connection loss is simulated. Everything the
    client sends is recorded in `sent` as parsed JSON.
    """

    class Disconnect(Exception):
        pass

    def __init__(self, inbox: list[object] | None = None) -> None:
        self.inbox: list[object] = list(inbox or [])
        self.sent: list[dict] = []
        self.closed = False
        self._idle = asyncio.Event()

    async def send(self, message: str) -> None:
        if self.closed:
            raise ConnectionError("socket closed")
        self.sent.append(json.loads(message))

    async def recv(self) -> str:
        while True:
            if not self.inbox:
                # Nothing scripted left. Idle like a quiet connection would,
                # rather than reporting a close the test did not ask for — but
                # keep polling, because a test may append frames mid-scenario.
                await asyncio.sleep(0.001)
                continue
            item = self.inbox.pop(0)
            if isinstance(item, type) and issubclass(item, BaseException):
                raise item("scripted disconnect")
            if isinstance(item, BaseException):
                raise item
            return json.dumps(item)

    async def close(self) -> None:
        self.closed = True

    # -- helpers for building scripted server frames ------------------------

    @staticmethod
    def delta(item_id: str, text: str) -> dict:
        return {
            "type": "conversation.item.input_audio_transcription.delta",
            "item_id": item_id,
            "content_index": 0,
            "delta": text,
        }

    @staticmethod
    def completed(item_id: str, transcript: str) -> dict:
        return {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": item_id,
            "content_index": 0,
            "transcript": transcript,
        }

    @staticmethod
    def error(code: str, message: str) -> dict:
        return {"type": "error", "error": {"code": code, "message": message}}


def scripted_connector(*sockets: FakeWebSocket):
    """A connector that hands out the given sockets in order.

    Records every URL it was asked for, so URL-fallback behaviour is testable.
    """
    queue = list(sockets)
    calls: list[str] = []

    async def connect(url: str, headers: dict[str, str]):
        calls.append(url)
        if not queue:
            raise ConnectionError("no more scripted sockets")
        socket = queue.pop(0)
        if isinstance(socket, BaseException):
            raise socket
        return socket

    connect.calls = calls  # type: ignore[attr-defined]
    return connect


def failing_connector(error: Exception):
    calls: list[str] = []

    async def connect(url: str, headers: dict[str, str]):
        calls.append(url)
        raise error

    connect.calls = calls  # type: ignore[attr-defined]
    return connect
