"""Reconnection over a real socket.

The unit tests in `test_stt_provider.py` drive a fake WebSocket, which proves
the state machine but not that the real `websockets` client path survives a
connection drop. This runs an actual server on loopback and cuts it, so the
real connect/handshake/recv code is exercised end to end.
"""

from __future__ import annotations

import asyncio
import json

import pytest
import websockets

from jlt.config import SttSettings
from jlt.secrets import Secret
from jlt.stt.openai_realtime import OpenAIRealtimeSTTProvider, _Backoff

KEY = Secret("sk-test-local-0000000000000000")


class FakeRealtimeServer:
    """A minimal stand-in for the OpenAI realtime transcription endpoint.

    Speaks the parts of the protocol the client depends on: acknowledges
    `session.update` with `session.created`, and emits transcript events on
    command. `drop_after` closes the connection abruptly to simulate a network
    interruption.
    """

    def __init__(self, drop_first_connection: bool = False) -> None:
        self.connections = 0
        self.session_updates: list[dict] = []
        self.audio_chunks = 0
        self.commits = 0
        self._drop_first = drop_first_connection
        self._server: websockets.Server | None = None
        self._live: set = set()
        self.port = 0

    async def start(self) -> str:
        self._server = await websockets.serve(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return f"ws://127.0.0.1:{self.port}"

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, socket) -> None:
        self.connections += 1
        mine = self.connections
        self._live.add(socket)
        try:
            async for raw in socket:
                event = json.loads(raw)
                kind = event.get("type")
                if kind == "session.update":
                    self.session_updates.append(event)
                    await socket.send(
                        json.dumps({"type": "session.created", "session": {"id": "s"}})
                    )
                    if self._drop_first and mine == 1:
                        # Cut the connection the way a dropped network does:
                        # no close frame, just gone.
                        await socket.close(code=1006)
                        return
                elif kind == "input_audio_buffer.append":
                    self.audio_chunks += 1
                elif kind == "input_audio_buffer.commit":
                    self.commits += 1
        except websockets.ConnectionClosed:
            pass
        finally:
            self._live.discard(socket)

    async def broadcast(self, event: dict) -> None:
        for socket in list(self._live):
            await socket.send(json.dumps(event))


@pytest.fixture
async def server():
    instance = FakeRealtimeServer()
    url = await instance.start()
    instance.url = url  # type: ignore[attr-defined]
    yield instance
    await instance.stop()


async def wait_for(predicate, limit=5.0):
    deadline = asyncio.get_running_loop().time() + limit
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


async def test_connects_and_configures_over_a_real_socket(server):
    prov = OpenAIRealtimeSTTProvider(KEY, SttSettings(), urls=(server.url,))
    await prov.connect()
    assert server.connections == 1
    update = server.session_updates[0]["session"]
    assert update["type"] == "transcription"
    assert update["audio"]["input"]["transcription"]["languages"] == ["ja"]
    assert update["audio"]["input"]["turn_detection"] is None
    await prov.close()


async def test_audio_and_commits_reach_the_server(server):
    prov = OpenAIRealtimeSTTProvider(KEY, SttSettings(), urls=(server.url,))
    await prov.connect()
    await prov.send_audio(b"\x00\x01" * 480)
    await prov.commit(1)
    assert await wait_for(lambda: server.commits == 1)
    assert server.audio_chunks == 1
    await prov.close()


async def test_transcript_events_flow_over_a_real_socket(server):
    prov = OpenAIRealtimeSTTProvider(KEY, SttSettings(), urls=(server.url,))
    await prov.connect()
    await server.broadcast(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "item_1",
            "content_index": 0,
            "transcript": "確認しました。",
        }
    )
    events = []

    async def pump():
        async for event in prov.events():
            events.append(event)
            if any(e.kind == "final" for e in events):
                return

    await asyncio.wait_for(pump(), timeout=5.0)
    assert any(e.text == "確認しました。" for e in events)
    await prov.close()


async def test_reconnects_after_the_network_drops(server):
    """The whole point: a dropped connection must restore itself."""
    server._drop_first = True
    prov = OpenAIRealtimeSTTProvider(
        KEY,
        SttSettings(),
        urls=(server.url,),
        backoff=_Backoff(initial_s=0.01, max_s=0.05),
    )
    await prov.connect()

    assert await wait_for(lambda: server.connections >= 2), "provider did not reconnect"
    assert prov.stats.reconnects >= 1
    # The session must be reconfigured on the new connection, otherwise the
    # model would transcribe under the server's defaults.
    assert len(server.session_updates) >= 2
    await prov.close()


async def test_audio_works_again_after_a_reconnect(server):
    server._drop_first = True
    prov = OpenAIRealtimeSTTProvider(
        KEY,
        SttSettings(),
        urls=(server.url,),
        backoff=_Backoff(initial_s=0.01, max_s=0.05),
    )
    await prov.connect()
    assert await wait_for(lambda: server.connections >= 2)
    await asyncio.sleep(0.1)

    before = server.audio_chunks
    await prov.send_audio(b"\x00\x01" * 480)
    await prov.commit(1)
    assert await wait_for(lambda: server.audio_chunks > before), "audio lost after reconnect"
    await prov.close()


async def test_a_server_that_never_acknowledges_fails_the_connection():
    """A silent server must not leave the client hanging forever."""

    async def handler(socket):
        async for _ in socket:
            pass  # accept the session.update, answer nothing

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        prov = OpenAIRealtimeSTTProvider(KEY, SttSettings(), urls=(f"ws://127.0.0.1:{port}",))
        prov.SESSION_READY_TIMEOUT_S = 0.3
        with pytest.raises(ConnectionError):
            await prov.connect()
    finally:
        server.close()
        await server.wait_closed()
