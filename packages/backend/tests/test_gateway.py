"""Loopback API: authentication, origin checks, and what must never leak."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jlt.events import EventDispatcher
from jlt.gateway import create_app
from jlt.secrets import Secret
from jlt.session import SessionManager
from jlt.settings_manager import SettingsManager

from .fakes import FakeCapture, FakeTranslationProvider, ScriptedStt

TOKEN = Secret("test-token-abcdefghijklmnop")
AUTH = {"Authorization": f"Bearer {TOKEN.reveal()}"}


@pytest.fixture
def persisting_client(tmp_path):
    """A client whose history is saved, so more than one session can exist.

    The default fixture deliberately does not persist, matching the product
    default — but that means its history only ever holds the current session,
    which is the wrong setting for testing a multi-meeting export.
    """
    from jlt.history import HistoryManager

    settings_manager = SettingsManager(tmp_path, api_key_present=True)
    dispatcher = EventDispatcher()
    session = SessionManager(
        settings_manager,
        dispatcher,
        capture_factory=FakeCapture,
        stt_factory=lambda _s: ScriptedStt(),
        translation_factory=lambda _s: FakeTranslationProvider(),
        history=HistoryManager(persist=True, db_path=tmp_path / "history.sqlite3"),
    )
    app = create_app(
        token=TOKEN,
        settings_manager=settings_manager,
        session=session,
        dispatcher=dispatcher,
        started_at=0.0,
    )
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client(tmp_path):
    settings_manager = SettingsManager(tmp_path, api_key_present=True)
    dispatcher = EventDispatcher()
    session = SessionManager(
        settings_manager,
        dispatcher,
        capture_factory=FakeCapture,
        stt_factory=lambda _s: ScriptedStt(),
        translation_factory=lambda _s: FakeTranslationProvider(),
    )
    app = create_app(
        token=TOKEN,
        settings_manager=settings_manager,
        session=session,
        dispatcher=dispatcher,
        started_at=0.0,
    )
    with TestClient(app) as test_client:
        yield test_client


# --- authentication ---------------------------------------------------------


def test_health_needs_no_token_so_the_supervisor_can_poll(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_carries_no_session_content(client):
    body = client.get("/health").json()
    assert set(body) == {"status", "version", "uptime_s"}


@pytest.mark.parametrize(
    "path",
    ["/api/settings", "/api/session", "/api/metrics", "/api/history", "/api/audio/sources"],
)
def test_api_routes_reject_a_missing_token(client, path):
    assert client.get(path).status_code == 401


def test_api_routes_reject_a_wrong_token(client):
    assert client.get("/api/settings", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_api_routes_reject_a_malformed_authorization_header(client):
    assert client.get("/api/settings", headers={"Authorization": TOKEN.reveal()}).status_code == 401


def test_a_valid_token_is_accepted(client):
    assert client.get("/api/settings", headers=AUTH).status_code == 200


# --- origin -----------------------------------------------------------------


def test_a_foreign_origin_is_refused_even_with_a_valid_token(client):
    """A page the user visits must not be able to drive the backend."""
    response = client.get("/api/settings", headers={**AUTH, "Origin": "https://evil.example"})
    assert response.status_code == 403


def test_the_renderer_origin_is_accepted(client):
    response = client.get("/api/settings", headers={**AUTH, "Origin": "tauri://localhost"})
    assert response.status_code == 200


def test_the_dev_server_origin_is_accepted(client):
    response = client.get("/api/settings", headers={**AUTH, "Origin": "http://localhost:5173"})
    assert response.status_code == 200


# --- secrets ----------------------------------------------------------------


def test_settings_report_key_presence_but_never_the_key(client):
    body = client.get("/api/settings", headers=AUTH).json()
    assert body["api_key_present"] is True
    assert "api_key" not in body
    assert "sk-" not in str(body)


def test_api_key_present_cannot_be_set_by_a_client(client):
    """Key presence mirrors the Keychain; a client must not be able to fake it."""
    body = client.patch("/api/settings", headers=AUTH, json={"api_key_present": False}).json()
    assert body["api_key_present"] is True


# --- settings ---------------------------------------------------------------


def test_settings_can_be_patched_section_by_section(client):
    body = client.patch("/api/settings", headers=AUTH, json={"ui": {"font_size": 26}}).json()
    assert body["ui"]["font_size"] == 26
    # Untouched fields in the same section survive.
    assert body["ui"]["always_on_top"] is True


def test_an_invalid_patch_is_rejected_and_changes_nothing(client):
    before = client.get("/api/settings", headers=AUTH).json()
    response = client.patch("/api/settings", headers=AUTH, json={"ui": {"font_size": 999}})
    assert response.status_code == 422
    assert client.get("/api/settings", headers=AUTH).json() == before


def test_audio_persistence_cannot_be_enabled_over_the_api(client):
    response = client.patch(
        "/api/settings", headers=AUTH, json={"privacy": {"persist_audio": True}}
    )
    assert response.status_code == 422


def test_glossary_can_be_replaced(client):
    response = client.post(
        "/api/glossary",
        headers=AUTH,
        json={"entries": [{"ja": "デプロイ", "vi": "triển khai"}]},
    )
    assert response.json() == {"count": 1}


# --- session ----------------------------------------------------------------


def test_sources_can_be_listed(client):
    body = client.get("/api/audio/sources", headers=AUTH).json()
    assert body["sources"][0]["id"] == "app:com.microsoft.teams2"


def test_full_session_lifecycle_over_http(client):
    started = client.post("/api/session/start", headers=AUTH, json={"source_id": "app:x"})
    assert started.status_code == 200
    assert started.json()["session_id"].startswith("s_")

    assert client.post("/api/session/pause", headers=AUTH).json()["state"] == "paused"
    assert client.post("/api/session/resume", headers=AUTH).json()["state"] == "running"

    stopped = client.post("/api/session/stop", headers=AUTH)
    assert stopped.status_code == 200
    assert stopped.json()["state"] == "stopped"
    assert stopped.json()["stats"]["session_id"]


def test_starting_twice_conflicts(client):
    client.post("/api/session/start", headers=AUTH, json={"source_id": "app:x"})
    second = client.post("/api/session/start", headers=AUTH, json={"source_id": "app:x"})
    assert second.status_code == 409
    client.post("/api/session/stop", headers=AUTH)


def test_pausing_without_a_session_conflicts(client):
    assert client.post("/api/session/pause", headers=AUTH).status_code == 409


def test_metrics_are_available_before_any_session(client):
    body = client.get("/api/metrics", headers=AUTH).json()
    assert body["stt_latency_ms"]["n"] == 0
    assert body["stt_latency_ms"]["p50"] is None


def test_history_starts_empty_and_can_be_cleared(client):
    assert client.get("/api/history", headers=AUTH).json()["segments"] == []
    assert client.delete("/api/history", headers=AUTH).json()["status"] == "cleared"


# --- websocket --------------------------------------------------------------


def test_websocket_rejects_a_bad_token(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/ws?token=wrong") as socket:
            socket.receive_json()
    assert excinfo.value.code == 4401


def test_websocket_accepts_a_valid_token_and_answers_ping(client):
    with client.websocket_connect(f"/ws?token={TOKEN.reveal()}") as socket:
        socket.send_json({"type": "ping"})
        assert socket.receive_json() == {"type": "pong"}


def test_websocket_delivers_session_events(client):
    with client.websocket_connect(f"/ws?token={TOKEN.reveal()}") as socket:
        client.post("/api/session/start", headers=AUTH, json={"source_id": "app:x"})
        seen = [socket.receive_json() for _ in range(2)]
        assert any(event["type"] == "session.started" for event in seen)
        client.post("/api/session/stop", headers=AUTH)


# --- CORS -------------------------------------------------------------------
#
# The renderer is always a different origin from the backend, so missing CORS
# headers block every request in the browser before the Origin check can run.


def test_preflight_from_the_renderer_is_allowed(client):
    response = client.options(
        "/api/settings",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_preflight_from_the_tauri_origin_is_allowed(client):
    response = client.options(
        "/api/settings",
        headers={
            "Origin": "tauri://localhost",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.headers["access-control-allow-origin"] == "tauri://localhost"


def test_a_real_request_carries_the_allow_origin_header(client):
    response = client.get("/api/settings", headers={**AUTH, "Origin": "http://localhost:5173"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_a_foreign_origin_gets_no_cors_grant(client):
    """No wildcard: a page the user visits must not be handed access."""
    response = client.options(
        "/api/settings",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers.get("access-control-allow-origin") != "https://evil.example"
    assert response.headers.get("access-control-allow-origin") != "*"


# --- structured errors ------------------------------------------------------
#
# The UI must branch on the failure (open System Settings for a permission
# problem, open Settings for a missing key). It cannot do that by matching on
# prose, so every error carries a code.


def test_errors_carry_a_code_and_a_message(client):
    body = client.get("/api/settings").json()
    assert body["detail"]["code"] == "unauthorized"
    assert body["detail"]["message"]


def test_permission_denied_is_identifiable_by_code(tmp_path):
    """The UI shows a button that opens System Settings for exactly this code."""
    from fastapi.testclient import TestClient

    from jlt.audio.capture_service import PermissionDenied
    from jlt.events import EventDispatcher
    from jlt.gateway import create_app
    from jlt.session import SessionManager
    from jlt.settings_manager import SettingsManager

    from .fakes import FakeCapture, FakeTranslationProvider, ScriptedStt

    class DeniedCapture(FakeCapture):
        async def list_sources(self):
            raise PermissionDenied("permission_denied", "Screen Recording permission is required.")

    dispatcher = EventDispatcher()
    session = SessionManager(
        SettingsManager(tmp_path, api_key_present=True),
        dispatcher,
        capture_factory=DeniedCapture,
        stt_factory=lambda _s: ScriptedStt(),
        translation_factory=lambda _s: FakeTranslationProvider(),
    )
    app = create_app(
        token=TOKEN,
        settings_manager=SettingsManager(tmp_path, api_key_present=True),
        session=session,
        dispatcher=dispatcher,
        started_at=0.0,
    )
    with TestClient(app) as local:
        response = local.get("/api/audio/sources", headers=AUTH)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "permission_denied"
    assert "Screen Recording" in response.json()["detail"]["message"]


def test_session_conflict_carries_its_code(client):
    client.post("/api/session/start", headers=AUTH, json={"source_id": "app:x"})
    body = client.post("/api/session/start", headers=AUTH, json={"source_id": "app:x"}).json()
    assert body["detail"]["code"] == "already_running"
    client.post("/api/session/stop", headers=AUTH)


def test_invalid_settings_carry_a_code(client):
    body = client.patch("/api/settings", headers=AUTH, json={"ui": {"font_size": 999}}).json()
    assert body["detail"]["code"] == "invalid_settings"


def test_missing_api_key_is_identifiable(tmp_path):
    """The UI opens the settings panel on this one, so it must be a code."""
    from fastapi.testclient import TestClient

    from jlt.events import EventDispatcher
    from jlt.gateway import create_app
    from jlt.session import SessionManager
    from jlt.settings_manager import SettingsManager

    from .fakes import FakeCapture, FakeTranslationProvider, ScriptedStt

    settings_manager = SettingsManager(tmp_path, api_key_present=False)
    dispatcher = EventDispatcher()
    session = SessionManager(
        settings_manager,
        dispatcher,
        capture_factory=FakeCapture,
        stt_factory=lambda _s: ScriptedStt(),
        translation_factory=lambda _s: FakeTranslationProvider(),
    )
    app = create_app(
        token=TOKEN,
        settings_manager=settings_manager,
        session=session,
        dispatcher=dispatcher,
        started_at=0.0,
    )
    with TestClient(app) as local:
        body = local.post("/api/session/start", headers=AUTH, json={"source_id": "app:x"}).json()
    assert body["detail"]["code"] == "api_key_missing"


# --- history API ------------------------------------------------------------


def test_history_sessions_reports_whether_it_can_span_restarts(client):
    """With saving off the list holds only the current session; the UI says so."""
    body = client.get("/api/history/sessions", headers=AUTH).json()
    assert body["persisting"] is False
    assert body["sessions"] == []


def test_a_finished_session_appears_in_the_list_with_its_cost(client):
    client.post("/api/session/start", headers=AUTH, json={"source_id": "app:x"})
    client.post("/api/session/stop", headers=AUTH)
    sessions = client.get("/api/history/sessions", headers=AUTH).json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["estimated_cost_usd"] is not None
    assert sessions[0]["source_name"] == "Microsoft Teams"


def test_session_detail_returns_stats_and_segments(client):
    started = client.post("/api/session/start", headers=AUTH, json={"source_id": "app:x"}).json()[
        "session_id"
    ]
    client.post("/api/session/stop", headers=AUTH)
    body = client.get(f"/api/history/sessions/{started}", headers=AUTH).json()
    assert body["session"]["session_id"] == started
    assert isinstance(body["segments"], list)


def test_an_unknown_session_is_a_typed_404(client):
    body = client.get("/api/history/sessions/s_nope", headers=AUTH)
    assert body.status_code == 404
    assert body.json()["detail"]["code"] == "session_not_found"


def test_summarising_a_running_session_is_refused(client):
    """A recap of half a meeting is quietly incomplete, which is worse."""
    started = client.post("/api/session/start", headers=AUTH, json={"source_id": "app:x"}).json()[
        "session_id"
    ]
    response = client.post(f"/api/history/sessions/{started}/summary", headers=AUTH)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] in ("session_running", "summary_unavailable")
    client.post("/api/session/stop", headers=AUTH)


def test_a_session_can_be_deleted(client):
    started = client.post("/api/session/start", headers=AUTH, json={"source_id": "app:x"}).json()[
        "session_id"
    ]
    client.post("/api/session/stop", headers=AUTH)
    assert client.delete(f"/api/history/sessions/{started}", headers=AUTH).status_code == 200
    assert client.get("/api/history/sessions", headers=AUTH).json()["sessions"] == []


@pytest.mark.parametrize("path", ["/api/history/sessions", "/api/history/sessions/s_1"])
def test_history_routes_require_a_token(client, path):
    assert client.get(path).status_code == 401


def test_a_recording_can_be_renamed(client):
    started = client.post("/api/session/start", headers=AUTH, json={"source_id": "app:x"}).json()[
        "session_id"
    ]
    client.post("/api/session/stop", headers=AUTH)
    body = client.patch(
        f"/api/history/sessions/{started}", headers=AUTH, json={"name": "  Daily  standup "}
    ).json()
    assert body["name"] == "Daily standup"
    listed = client.get("/api/history/sessions", headers=AUTH).json()["sessions"]
    assert listed[0]["name"] == "Daily standup"


def test_renaming_an_unknown_session_is_a_typed_404(client):
    response = client.patch("/api/history/sessions/s_nope", headers=AUTH, json={"name": "x"})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "session_not_found"


def test_choosing_a_transcript_folder_takes_effect_immediately(client, tmp_path):
    """Requiring a restart would make the setting look like it had not worked."""
    folder = tmp_path / "Meetings"
    client.patch(
        "/api/settings",
        headers=AUTH,
        json={"privacy": {"persist_transcripts": True, "transcript_folder": str(folder)}},
    )
    started = client.post("/api/session/start", headers=AUTH, json={"source_id": "app:x"}).json()[
        "session_id"
    ]
    client.post("/api/session/stop", headers=AUTH)

    detail = client.get(f"/api/history/sessions/{started}", headers=AUTH).json()
    assert detail["session"]["file_path"]
    assert folder.exists()


def test_turning_persistence_off_stops_writing(client, tmp_path):
    folder = tmp_path / "Meetings"
    client.patch(
        "/api/settings",
        headers=AUTH,
        json={"privacy": {"persist_transcripts": True, "transcript_folder": str(folder)}},
    )
    client.patch("/api/settings", headers=AUTH, json={"privacy": {"persist_transcripts": False}})
    body = client.get("/api/history/sessions", headers=AUTH).json()
    assert body["persisting"] is False
