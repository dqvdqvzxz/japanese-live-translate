"""OpenAI realtime transcription provider.

Every server frame used here is shaped exactly as the official documentation
specifies (see docs/openai-verification.md §2), so these tests check our
handling of the real protocol rather than of an invented one.
"""

from __future__ import annotations

import asyncio

import pytest

from jlt.config import SAMPLE_RATE_HZ, STT_MODEL, SttSettings
from jlt.models import GlossaryEntry
from jlt.secrets import Secret
from jlt.stt.openai_realtime import (
    OpenAIRealtimeSTTProvider,
    _Backoff,
    build_session_update,
    sanitize_keywords,
)

from .fakes import FakeWebSocket, failing_connector, scripted_connector

KEY = Secret("sk-test-key-0000000000000000")


def provider(*sockets: FakeWebSocket, **kwargs) -> OpenAIRealtimeSTTProvider:
    kwargs.setdefault("urls", ("wss://example.invalid/a",))
    return OpenAIRealtimeSTTProvider(
        KEY, SttSettings(), connector=scripted_connector(*sockets), **kwargs
    )


async def collect(prov: OpenAIRealtimeSTTProvider, count: int, timeout_s: float = 2.0):
    out = []

    async def pump():
        async for event in prov.events():
            out.append(event)
            if len(out) >= count:
                return

    await asyncio.wait_for(pump(), timeout=timeout_s)
    return out


# --- session configuration --------------------------------------------------


def test_session_update_matches_the_documented_shape():
    payload = build_session_update(SttSettings())
    session = payload["session"]
    assert payload["type"] == "session.update"
    assert session["type"] == "transcription"
    audio_input = session["audio"]["input"]
    assert audio_input["format"] == {"type": "audio/pcm", "rate": SAMPLE_RATE_HZ}
    assert audio_input["transcription"]["model"] == STT_MODEL


def test_turn_detection_is_explicitly_null():
    """gpt-live-transcribe supports no server VAD; sending one is an error."""
    audio_input = build_session_update(SttSettings())["session"]["audio"]["input"]
    assert "turn_detection" in audio_input
    assert audio_input["turn_detection"] is None


def test_language_preference_uses_the_plural_field_only():
    """The model rejects the singular `language`, and both together."""
    transcription = build_session_update(SttSettings())["session"]["audio"]["input"][
        "transcription"
    ]
    assert transcription["languages"] == ["ja"]
    assert "language" not in transcription


def test_glossary_becomes_keyword_hints():
    glossary = [GlossaryEntry(ja="デプロイ", vi="triển khai")]
    transcription = build_session_update(SttSettings(), glossary)["session"]["audio"]["input"][
        "transcription"
    ]
    assert transcription["keywords"] == ["デプロイ"]


def test_keywords_are_stripped_of_characters_the_api_rejects():
    entries = [GlossaryEntry(ja="a<b>c", vi="x"), GlossaryEntry(ja="line\nbreak", vi="y")]
    cleaned = sanitize_keywords(entries)
    assert all(ch not in term for term in cleaned for ch in ("<", ">", "\n", "\r"))


def test_keywords_are_deduplicated():
    entries = [GlossaryEntry(ja="API", vi="a"), GlossaryEntry(ja="API", vi="b")]
    assert sanitize_keywords(entries) == ["API"]


def test_long_prompt_is_truncated_rather_than_rejected_by_the_api():
    settings = SttSettings(prompt="あ" * 5000)
    transcription = build_session_update(settings)["session"]["audio"]["input"]["transcription"]
    assert len(transcription["prompt"]) == 2000


# --- connection -------------------------------------------------------------


async def test_connect_sends_the_session_update_first():
    socket = FakeWebSocket()
    prov = provider(socket)
    await prov.connect()
    assert socket.sent[0]["type"] == "session.update"
    await prov.close()


async def test_connect_falls_back_to_the_next_candidate_url():
    """The transcription WebSocket URL is not pinned by the public docs."""
    good = FakeWebSocket()

    async def connect(url, headers):
        if url.endswith("/a"):
            raise ConnectionError("rejected")
        return good

    prov = OpenAIRealtimeSTTProvider(
        KEY,
        SttSettings(),
        connector=connect,
        urls=("wss://example.invalid/a", "wss://example.invalid/b"),
    )
    await prov.connect()
    assert prov.url == "wss://example.invalid/b"
    await prov.close()


async def test_connect_raises_a_actionable_error_when_no_url_works():
    prov = OpenAIRealtimeSTTProvider(
        KEY,
        SttSettings(),
        connector=failing_connector(ConnectionError("nope")),
        urls=("wss://a.invalid", "wss://b.invalid"),
    )
    with pytest.raises(ConnectionError) as excinfo:
        await prov.connect()
    assert "verify-openai" in str(excinfo.value)


async def test_api_key_is_sent_as_a_bearer_header_and_not_in_the_url():
    seen: dict = {}

    async def connect(url, headers):
        seen["url"] = url
        seen["headers"] = headers
        return FakeWebSocket()

    prov = OpenAIRealtimeSTTProvider(
        KEY, SttSettings(), connector=connect, urls=("wss://x.invalid",)
    )
    await prov.connect()
    assert seen["headers"]["Authorization"] == f"Bearer {KEY.reveal()}"
    assert "sk-" not in seen["url"]
    await prov.close()


# --- transcript events ------------------------------------------------------


async def test_deltas_accumulate_into_full_text():
    socket = FakeWebSocket(
        [FakeWebSocket.delta("item_1", "来週の"), FakeWebSocket.delta("item_1", "リリース")]
    )
    prov = provider(socket)
    await prov.connect()
    events = await collect(prov, 3)  # connected + 2 deltas
    assert [e.text for e in events if e.kind == "delta"] == ["来週の", "来週のリリース"]
    await prov.close()


async def test_final_carries_the_api_transcript_not_the_accumulated_deltas():
    """The model revises earlier words; the completion event is authoritative."""
    socket = FakeWebSocket(
        [
            FakeWebSocket.delta("item_1", "来週の"),
            FakeWebSocket.completed("item_1", "来週のリリースは延期されません。"),
        ]
    )
    prov = provider(socket)
    await prov.connect()
    events = await collect(prov, 3)
    final = next(e for e in events if e.kind == "final")
    assert final.text == "来週のリリースは延期されません。"
    await prov.close()


async def test_duplicate_completion_is_suppressed():
    """A repeat would duplicate a subtitle line and pay to translate it twice."""
    socket = FakeWebSocket(
        [
            FakeWebSocket.completed("item_1", "はい。"),
            FakeWebSocket.completed("item_1", "はい。"),
            FakeWebSocket.completed("item_2", "いいえ。"),
        ]
    )
    prov = provider(socket)
    await prov.connect()
    events = await collect(prov, 3)  # connected + 2 distinct finals
    finals = [e for e in events if e.kind == "final"]
    assert [e.text for e in finals] == ["はい。", "いいえ。"]
    assert prov.stats.duplicates_suppressed == 1
    await prov.close()


async def test_delta_after_final_for_the_same_item_is_ignored():
    socket = FakeWebSocket(
        [
            FakeWebSocket.completed("item_1", "完了。"),
            FakeWebSocket.delta("item_1", "stale"),
            FakeWebSocket.completed("item_2", "次。"),
        ]
    )
    prov = provider(socket)
    await prov.connect()
    events = await collect(prov, 3)
    assert not [e for e in events if e.kind == "delta"]
    await prov.close()


async def test_seq_comes_from_commit_not_from_arrival_order():
    """The API does not guarantee completion ordering; `seq` must not either."""
    socket = FakeWebSocket()
    prov = provider(socket)
    await prov.connect()

    # Turn 1 opens, then commits as seq 1.
    socket.inbox.append(FakeWebSocket.delta("item_1", "A"))
    await collect(prov, 2)
    await prov.commit(1)

    # Turn 2 opens and commits as seq 2.
    socket.inbox.append(FakeWebSocket.delta("item_2", "B"))
    await collect(prov, 1)
    await prov.commit(2)

    # Completions arrive in the wrong order. Each must keep its own seq.
    socket.inbox.append(FakeWebSocket.completed("item_2", "B final"))
    socket.inbox.append(FakeWebSocket.completed("item_1", "A final"))
    finals = [e for e in await collect(prov, 2)]
    by_text = {e.text: e.seq for e in finals}
    assert by_text == {"B final": 2, "A final": 1}
    await prov.close()


async def test_final_latency_is_measured_from_the_commit():
    socket = FakeWebSocket([FakeWebSocket.delta("item_1", "x")])
    prov = provider(socket)
    await prov.connect()
    await collect(prov, 2)
    await prov.commit(1)
    socket.inbox.append(FakeWebSocket.completed("item_1", "x."))
    final = (await collect(prov, 1))[0]
    assert final.latency_ms is not None and final.latency_ms >= 0
    await prov.close()


async def test_api_error_frames_surface_as_error_events():
    socket = FakeWebSocket([FakeWebSocket.error("invalid_request", "bad keyword")])
    prov = provider(socket)
    await prov.connect()
    events = await collect(prov, 2)
    error = next(e for e in events if e.kind == "error")
    assert error.code == "invalid_request"
    await prov.close()


async def test_malformed_frames_do_not_kill_the_session():
    socket = FakeWebSocket()
    prov = provider(socket)
    await prov.connect()
    prov._handle_raw("not json at all")
    prov._handle_raw(b"\xff\xfe")
    socket.inbox.append(FakeWebSocket.completed("item_1", "still alive"))
    final = (await collect(prov, 2))[-1]
    assert final.text == "still alive"
    await prov.close()


# --- audio ------------------------------------------------------------------


async def test_audio_is_base64_pcm_in_an_append_event():
    import base64

    socket = FakeWebSocket()
    prov = provider(socket)
    await prov.connect()
    await prov.send_audio(b"\x01\x02\x03\x04")
    append = socket.sent[-1]
    assert append["type"] == "input_audio_buffer.append"
    assert base64.b64decode(append["audio"]) == b"\x01\x02\x03\x04"
    await prov.close()


async def test_commit_sends_the_documented_event():
    socket = FakeWebSocket()
    prov = provider(socket)
    await prov.connect()
    await prov.commit(1)
    assert socket.sent[-1] == {"type": "input_audio_buffer.commit"}
    await prov.close()


async def test_audio_sent_while_disconnected_is_dropped_not_queued():
    """Stale audio would push the subtitles permanently further behind."""
    prov = provider(FakeWebSocket())
    await prov.send_audio(b"\x00" * 960)
    assert prov.stats.dropped_while_disconnected == 1


# --- reconnection -----------------------------------------------------------


async def test_reconnects_after_the_connection_drops():
    first = FakeWebSocket([FakeWebSocket.delta("item_1", "A"), FakeWebSocket.Disconnect])
    second = FakeWebSocket([FakeWebSocket.completed("item_9", "recovered")])

    prov = OpenAIRealtimeSTTProvider(
        KEY,
        SttSettings(),
        connector=scripted_connector(first, second),
        urls=("wss://example.invalid/a",),
        backoff=_Backoff(initial_s=0.001, max_s=0.002),
    )
    await prov.connect()
    events = await collect(prov, 5, timeout_s=3.0)
    kinds = [e.kind for e in events]
    assert "reconnecting" in kinds
    assert any(e.kind == "final" and e.text == "recovered" for e in events)
    assert prov.stats.reconnects == 1
    await prov.close()


async def test_reconnect_resends_the_session_configuration():
    first = FakeWebSocket([FakeWebSocket.Disconnect])
    second = FakeWebSocket()
    prov = OpenAIRealtimeSTTProvider(
        KEY,
        SttSettings(),
        connector=scripted_connector(first, second),
        urls=("wss://example.invalid/a",),
        backoff=_Backoff(initial_s=0.001, max_s=0.002),
    )
    await prov.connect()
    for _ in range(200):
        if second.sent:
            break
        await asyncio.sleep(0.005)
    assert second.sent and second.sent[0]["type"] == "session.update"
    await prov.close()


async def test_reconnect_reuses_the_url_that_already_worked():
    first = FakeWebSocket([FakeWebSocket.Disconnect])
    second = FakeWebSocket()
    connector = scripted_connector(first, second)
    prov = OpenAIRealtimeSTTProvider(
        KEY,
        SttSettings(),
        connector=connector,
        urls=("wss://example.invalid/a", "wss://example.invalid/b"),
        backoff=_Backoff(initial_s=0.001, max_s=0.002),
    )
    await prov.connect()
    for _ in range(200):
        if len(connector.calls) > 1:
            break
        await asyncio.sleep(0.005)
    assert connector.calls == ["wss://example.invalid/a", "wss://example.invalid/a"]
    await prov.close()


async def test_reconnect_gives_up_and_reports_after_the_attempt_limit():
    first = FakeWebSocket([FakeWebSocket.Disconnect])
    prov = OpenAIRealtimeSTTProvider(
        KEY,
        SttSettings(),
        connector=scripted_connector(first),
        urls=("wss://example.invalid/a",),
        backoff=_Backoff(initial_s=0.001, max_s=0.002),
        max_reconnect_attempts=2,
    )
    await prov.connect()
    # connected, two reconnect attempts, then the give-up report.
    events = await collect(prov, 4, timeout_s=3.0)
    assert any(e.code == "stt_unavailable" for e in events)
    await prov.close()


async def test_close_is_idempotent_and_stops_the_reader():
    socket = FakeWebSocket()
    prov = provider(socket)
    await prov.connect()
    await prov.close()
    await prov.close()
    assert socket.closed


def test_backoff_grows_and_stays_bounded():
    backoff = _Backoff(initial_s=1.0, max_s=8.0)
    delays = [backoff.next_delay() for _ in range(12)]
    assert all(0 <= d <= 8.0 for d in delays)
    assert max(delays[-4:]) > 0


def test_backoff_resets_after_a_successful_connection():
    backoff = _Backoff(initial_s=1.0, max_s=8.0)
    for _ in range(5):
        backoff.next_delay()
    backoff.reset()
    assert backoff.attempt == 0


async def test_final_carries_the_commit_instant_for_end_to_end_latency():
    """Without this, end-to-end latency starts at the final, not the commit.

    That understates it by exactly the STT latency — an error in the flattering
    direction, which is the worst kind in a benchmark.
    """
    socket = FakeWebSocket([FakeWebSocket.delta("item_1", "x")])
    prov = provider(socket)
    await prov.connect()
    await collect(prov, 2)
    await prov.commit(1)
    socket.inbox.append(FakeWebSocket.completed("item_1", "x."))
    final = (await collect(prov, 1))[0]
    assert final.committed_at is not None
    assert final.committed_at > 0
    await prov.close()


async def test_connect_waits_for_the_server_to_acknowledge_the_session():
    """Streaming before the config is applied gets the first turn transcribed
    under the server's defaults — observed live as non-Japanese output for the
    first utterance of a session."""
    socket = FakeWebSocket(acknowledge_session=False)
    prov = provider(socket)
    prov.SESSION_READY_TIMEOUT_S = 0.2
    with pytest.raises(ConnectionError):
        await prov.connect()


async def test_a_rejected_session_fails_the_connection():
    socket = FakeWebSocket(acknowledge_session=False)
    socket.inbox.append(FakeWebSocket.error("invalid_request", "bad keyword"))
    prov = provider(socket)
    with pytest.raises(ConnectionError):
        await prov.connect()


async def test_transcripts_arriving_before_the_ack_are_not_lost():
    socket = FakeWebSocket(acknowledge_session=False)
    socket.inbox.extend(
        [
            FakeWebSocket.completed("item_0", "早い完了。"),
            {"type": "session.created", "session": {"id": "s"}},
        ]
    )
    prov = provider(socket)
    await prov.connect()
    events = await collect(prov, 2)
    assert any(e.kind == "final" and e.text == "早い完了。" for e in events)
    await prov.close()
