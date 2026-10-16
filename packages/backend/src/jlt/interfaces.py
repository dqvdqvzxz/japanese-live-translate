"""Protocols that define the seams between components.

Every component takes its collaborators as constructor arguments typed by these
protocols, so tests substitute fakes without patching module globals. This is
the dependency-injection boundary referred to in docs/architecture.md §3.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol, runtime_checkable

from .models import AudioSource, GlossaryEntry, SttEvent, Usage


@runtime_checkable
class AudioCapture(Protocol):
    """Produces 24 kHz mono PCM16 bytes from a chosen system audio source."""

    async def list_sources(self) -> list[AudioSource]: ...

    async def start(self, source_id: str) -> AudioSource: ...

    def frames(self) -> AsyncIterator[bytes]:
        """Yields frame-aligned PCM16 chunks until the capture stops."""
        ...

    async def stop(self) -> None: ...

    @property
    def level_dbfs(self) -> float | None: ...


@runtime_checkable
class SttProvider(Protocol):
    """A realtime speech-to-text session."""

    async def connect(self) -> None: ...

    async def send_audio(self, pcm: bytes) -> None: ...

    async def commit(self, seq: int) -> None:
        """Close the current audio turn. `seq` is the local ordering key."""
        ...

    def events(self) -> AsyncIterator[SttEvent]: ...

    async def close(self) -> None: ...


@runtime_checkable
class TranslationProvider(Protocol):
    """Translates one Japanese segment into Vietnamese."""

    async def translate(
        self,
        source_text: str,
        *,
        context: Sequence[tuple[str, str]],
        glossary: Sequence[GlossaryEntry],
    ) -> tuple[str, Usage]: ...
