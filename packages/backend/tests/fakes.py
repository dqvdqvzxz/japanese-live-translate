"""Test doubles for the protocol seams in `jlt.interfaces`."""

from __future__ import annotations

import asyncio
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
