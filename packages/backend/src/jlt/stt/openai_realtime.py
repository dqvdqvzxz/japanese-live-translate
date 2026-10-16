"""OpenAI realtime transcription over WebSocket.

Transport and normalization only: this module turns the API's event stream into
typed `SttEvent`s and keeps the connection alive. It makes no decisions about
merging, translating or displaying anything.

Everything about the protocol here is quoted from the official documentation
and recorded in docs/openai-verification.md. Two verified facts dominate the
design:

  * The model supports no server-side VAD in a transcription session, so
    `input_audio_buffer.commit` is driven entirely by the caller.
  * Completion events are not guaranteed to arrive in turn order, so the caller
    supplies a monotonic `seq` at commit time and we attach it to the turn.

Item ids are assigned by the API and first appear on a *delta*, which arrives
while the turn is still open — before the commit that will give that turn its
`seq`. So an unseen item id is bound to the turn currently in progress, whose
`seq` is known in advance as `last_committed_seq + 1`.
"""

from __future__ import annotations

import asyncio
import base64
import json
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..config import SAMPLE_RATE_HZ, STT_MODEL, SttSettings, realtime_url_candidates
from ..logging import get_logger
from ..models import GlossaryEntry, SttEvent
from ..secrets import Secret

log = get_logger(__name__)

#: Characters the API rejects inside a transcription keyword.
_FORBIDDEN_KEYWORD_CHARS = ("<", ">", "\r", "\n")
#: The docs do not publish the prompt length limit, so we stay well inside any
#: plausible one rather than discover it as a session-update rejection.
_MAX_PROMPT_CHARS = 2000


class WebSocketLike(Protocol):
    """The slice of a websockets client this module uses.

    Narrow on purpose: it is the seam tests substitute, so it must be small
    enough to fake honestly.
    """

    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self) -> None: ...


Connector = Callable[[str, dict[str, str]], Awaitable[WebSocketLike]]


async def _default_connector(url: str, headers: dict[str, str]) -> WebSocketLike:
    import websockets

    return await websockets.connect(url, additional_headers=headers)


def sanitize_keywords(entries: list[GlossaryEntry]) -> list[str]:
    """Glossary terms as transcription keyword hints.

    The API rejects the whole session update if a keyword contains `<`, `>`,
    CR or LF, so terms are stripped rather than passed through and gambled on.
    """
    out: list[str] = []
    for entry in entries:
        term = entry.ja.strip()
        for bad in _FORBIDDEN_KEYWORD_CHARS:
            term = term.replace(bad, " ")
        term = " ".join(term.split())
        if term and term not in out:
            out.append(term)
    return out


def build_session_update(
    settings: SttSettings, glossary: list[GlossaryEntry] | None = None
) -> dict[str, Any]:
    """The `session.update` payload, shaped exactly as the docs specify."""
    transcription: dict[str, Any] = {
        "model": STT_MODEL,
        # Plural. `gpt-live-transcribe` rejects the singular `language`, and
        # sending both is explicitly disallowed.
        "languages": list(settings.languages),
        "delay": settings.delay,
    }
    if settings.prompt:
        transcription["prompt"] = settings.prompt[:_MAX_PROMPT_CHARS]
    keywords = sanitize_keywords(glossary or [])
    if keywords:
        transcription["keywords"] = keywords

    return {
        "type": "session.update",
        "session": {
            "type": "transcription",
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": SAMPLE_RATE_HZ},
                    "transcription": transcription,
                    # Required: this model supports neither server_vad nor
                    # semantic_vad. Client-side VAD drives commits instead.
                    "turn_detection": None,
                }
            },
        },
    }


@dataclass
class _Backoff:
    """Exponential backoff with full jitter, so repeated drops do not sync up."""

    initial_s: float = 0.5
    max_s: float = 30.0
    factor: float = 2.0
    attempt: int = 0

    def next_delay(self) -> float:
        delay = min(self.max_s, self.initial_s * (self.factor**self.attempt))
        self.attempt += 1
        return random.uniform(0, delay)  # noqa: S311 - jitter, not cryptography

    def reset(self) -> None:
        self.attempt = 0


@dataclass
class _Turn:
    seq: int
    text: str = ""
    committed_at: float | None = None
    finalized: bool = False
    deltas: int = 0


@dataclass
class SttStats:
    reconnects: int = 0
    deltas: int = 0
    finals: int = 0
    duplicates_suppressed: int = 0
    audio_chunks_sent: int = 0
    dropped_while_disconnected: int = 0
    urls_tried: list[str] = field(default_factory=list)


class OpenAIRealtimeSTTProvider:
    """A persistent transcription session with automatic reconnection."""

    def __init__(
        self,
        api_key: Secret,
        settings: SttSettings,
        *,
        glossary: list[GlossaryEntry] | None = None,
        connector: Connector | None = None,
        urls: tuple[str, ...] | None = None,
        max_reconnect_attempts: int | None = None,
        backoff: _Backoff | None = None,
    ) -> None:
        self._api_key = api_key
        self._settings = settings
        self._glossary = glossary or []
        self._connect = connector or _default_connector
        self._urls = urls or realtime_url_candidates()
        self._max_reconnect_attempts = max_reconnect_attempts
        self._backoff = backoff or _Backoff()

        self._socket: WebSocketLike | None = None
        self._resolved_url: str | None = None
        self._events: asyncio.Queue[SttEvent] = asyncio.Queue(maxsize=512)
        self._reader: asyncio.Task[None] | None = None
        self._closing = False

        # Turn bookkeeping. `_last_committed_seq + 1` is the seq the open turn
        # will receive, which is what an unseen item id binds to.
        self._last_committed_seq = 0
        self._turns: dict[str, _Turn] = {}
        self._finalized_items: set[str] = set()
        self._pending_commit_at: float | None = None

        self.stats = SttStats()

    # -- connection ---------------------------------------------------------

    @property
    def url(self) -> str | None:
        """The URL that actually worked, once one has."""
        return self._resolved_url

    async def connect(self) -> None:
        """Open a session, trying each candidate URL until one is accepted.

        The transcription WebSocket URL is the one thing the public docs do not
        pin down (docs/openai-verification.md §2). Once a candidate works it is
        remembered, so reconnects do not re-probe.
        """
        candidates = (self._resolved_url,) if self._resolved_url else self._urls
        last_error: Exception | None = None

        for url in candidates:
            assert url is not None
            self.stats.urls_tried.append(url)
            try:
                socket = await self._connect(
                    url, {"Authorization": f"Bearer {self._api_key.reveal()}"}
                )
                await socket.send(json.dumps(build_session_update(self._settings, self._glossary)))
                self._socket = socket
                self._resolved_url = url
                self._closing = False
                self._backoff.reset()
                self._reader = asyncio.create_task(self._read_loop(), name="stt-reader")
                self._emit(SttEvent(kind="connected"))
                # The URL itself is logged by index only: a pinned URL could
                # carry query parameters we would rather not record.
                index = self._urls.index(url) if url in self._urls else -1
                log.info("stt connected", extra={"url_index": index})
                return
            except Exception as exc:
                last_error = exc
                log.warning("stt connect attempt failed", extra={"error": type(exc).__name__})

        raise ConnectionError(
            "Could not open a transcription session on any candidate URL. "
            "Run `make verify-openai` to determine the correct endpoint."
        ) from last_error

    async def close(self) -> None:
        self._closing = True
        if self._reader is not None:
            self._reader.cancel()
            try:
                await self._reader
            except asyncio.CancelledError:
                pass
            self._reader = None
        if self._socket is not None:
            try:
                await self._socket.close()
            finally:
                self._socket = None

    # -- sending ------------------------------------------------------------

    async def send_audio(self, pcm: bytes) -> None:
        """Append PCM16 to the input buffer.

        Audio sent while disconnected is dropped rather than buffered: by the
        time the socket returns, that audio is stale, and the user would rather
        lose two seconds than watch subtitles fall permanently behind.
        """
        socket = self._socket
        if socket is None:
            self.stats.dropped_while_disconnected += 1
            return
        payload = {
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(pcm).decode("ascii"),
        }
        try:
            await socket.send(json.dumps(payload))
            self.stats.audio_chunks_sent += 1
        except Exception:
            self.stats.dropped_while_disconnected += 1

    async def commit(self, seq: int) -> None:
        """Close the current audio turn and tag it with the caller's `seq`."""
        self._last_committed_seq = seq
        self._pending_commit_at = time.monotonic()
        for turn in self._turns.values():
            if turn.seq == seq and turn.committed_at is None:
                turn.committed_at = self._pending_commit_at

        socket = self._socket
        if socket is None:
            self.stats.dropped_while_disconnected += 1
            return
        try:
            await socket.send(json.dumps({"type": "input_audio_buffer.commit"}))
        except Exception:
            self.stats.dropped_while_disconnected += 1

    # -- receiving ----------------------------------------------------------

    async def events(self) -> AsyncIterator[SttEvent]:
        while True:
            event = await self._events.get()
            yield event

    def _emit(self, event: SttEvent) -> None:
        try:
            self._events.put_nowait(event)
        except asyncio.QueueFull:
            # Only partial deltas are ever worth dropping; a final must survive.
            if event.kind == "delta":
                return
            try:
                self._events.get_nowait()
                self._events.put_nowait(event)
            except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover
                pass

    async def _read_loop(self) -> None:
        socket = self._socket
        assert socket is not None
        try:
            while True:
                raw = await socket.recv()
                self._handle_raw(raw)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._closing:
                return
            log.warning("stt connection lost", extra={"error": type(exc).__name__})
            self._socket = None
            await self._reconnect()

    def _handle_raw(self, raw: str | bytes) -> None:
        try:
            event = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            log.warning("unparseable stt frame")
            return
        if not isinstance(event, dict):
            return
        self._handle_event(event)

    def _handle_event(self, event: dict[str, Any]) -> None:
        kind = str(event.get("type", ""))

        if kind == "conversation.item.input_audio_transcription.delta":
            item_id = str(event.get("item_id", ""))
            turn = self._turn_for(item_id)
            if item_id in self._finalized_items:
                # A delta after the final for the same item is stale.
                self.stats.duplicates_suppressed += 1
                return
            turn.text += str(event.get("delta", ""))
            turn.deltas += 1
            self.stats.deltas += 1
            self._emit(SttEvent(kind="delta", item_id=item_id, seq=turn.seq, text=turn.text))
            return

        if kind == "conversation.item.input_audio_transcription.completed":
            item_id = str(event.get("item_id", ""))
            if item_id in self._finalized_items:
                # The API may repeat a completion, e.g. around a reconnect.
                # Emitting it twice would duplicate a line and a translation.
                self.stats.duplicates_suppressed += 1
                return
            turn = self._turn_for(item_id)
            self._finalized_items.add(item_id)
            text = str(event.get("transcript", turn.text))
            latency = None
            if turn.committed_at is not None:
                latency = (time.monotonic() - turn.committed_at) * 1000.0
            elif self._pending_commit_at is not None:
                latency = (time.monotonic() - self._pending_commit_at) * 1000.0
            self.stats.finals += 1
            self._turns.pop(item_id, None)
            self._emit(
                SttEvent(
                    kind="final",
                    item_id=item_id,
                    seq=turn.seq,
                    text=text,
                    latency_ms=latency,
                )
            )
            return

        if kind == "error":
            detail = event.get("error", {})
            code = str(detail.get("code", "stt_error")) if isinstance(detail, dict) else "stt_error"
            message = (
                str(detail.get("message", "The transcription service reported an error."))
                if isinstance(detail, dict)
                else "The transcription service reported an error."
            )
            # Message text comes from the API and describes the request, not the
            # audio, so it carries no meeting content.
            self._emit(SttEvent(kind="error", code=code, message=message))

    def _turn_for(self, item_id: str) -> _Turn:
        """Bind an item id to a turn, creating the binding on first sight.

        A new item id belongs to the turn currently in progress, which will be
        committed as `_last_committed_seq + 1`.
        """
        turn = self._turns.get(item_id)
        if turn is None:
            turn = _Turn(seq=self._last_committed_seq + 1)
            self._turns[item_id] = turn
        return turn

    # -- reconnection -------------------------------------------------------

    async def _reconnect(self) -> None:
        attempts = 0
        while not self._closing:
            if (
                self._max_reconnect_attempts is not None
                and attempts >= self._max_reconnect_attempts
            ):
                self._emit(
                    SttEvent(
                        kind="error",
                        code="stt_unavailable",
                        message="Lost the connection to OpenAI and could not restore it.",
                    )
                )
                return
            attempts += 1
            delay = self._backoff.next_delay()
            self._emit(
                SttEvent(
                    kind="reconnecting",
                    code="stt_reconnecting",
                    message=f"Reconnecting to OpenAI (attempt {attempts}).",
                )
            )
            await asyncio.sleep(delay)
            try:
                await self.connect()
                self.stats.reconnects += 1
                return
            except Exception as exc:
                log.warning(
                    "stt reconnect attempt failed",
                    extra={"attempt": attempts, "error": type(exc).__name__},
                )
