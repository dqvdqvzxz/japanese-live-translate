"""The in-memory log view: what it keeps, what it hides, and what it must not leak."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jlt.events import EventDispatcher
from jlt.gateway import create_app
from jlt.logging import RingBufferHandler, register_secret
from jlt.secrets import Secret
from jlt.session import SessionManager
from jlt.settings_manager import SettingsManager

from .fakes import FakeCapture, FakeTranslationProvider, ScriptedStt

TOKEN = Secret("test-token-abcdefghijklmnop")
AUTH = {"Authorization": f"Bearer {TOKEN.reveal()}"}


@pytest.fixture
def ring_logger():
    """A private logger feeding a ring, so no global logging state is touched."""
    ring = RingBufferHandler(capacity=5, path=Path("/tmp/jlt-test.log"))
    logger = logging.getLogger("jlt.test_ring")
    logger.handlers = [ring]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    yield logger, ring
    logger.handlers = []


def test_only_warnings_and_errors_by_default(ring_logger):
    logger, ring = ring_logger
    logger.info("fine")
    logger.warning("careful", extra={"code": "no_audio"})
    logger.error("broken")
    assert [e["msg"] for e in ring.records()] == ["careful", "broken"]
    assert [e["msg"] for e in ring.records(min_level="info")] == ["fine", "careful", "broken"]


def test_entries_keep_their_structured_fields(ring_logger):
    logger, ring = ring_logger
    logger.warning("audio helper warning", extra={"code": "app_all_zero", "detail": "zeros"})
    entry = ring.records()[0]
    assert entry["level"] == "warning"
    assert entry["logger"] == "jlt.test_ring"
    assert entry["code"] == "app_all_zero"
    assert entry["detail"] == "zeros"
    assert isinstance(entry["ts"], float)


def test_it_is_bounded_and_keeps_the_newest(ring_logger):
    logger, ring = ring_logger
    for n in range(12):
        logger.warning("w%d", n)
    assert [e["msg"] for e in ring.records(limit=100)] == ["w7", "w8", "w9", "w10", "w11"]
    assert [e["msg"] for e in ring.records(limit=2)] == ["w10", "w11"]


def test_credentials_never_reach_the_log_view(ring_logger):
    """The view is shown to the user and copied into bug reports."""
    logger, ring = ring_logger
    register_secret("loopback-secret-xyz")
    logger.warning("key was sk-abcdefghijklmnopqrstuvwxyz0123456789 here")
    logger.warning(
        "token loopback-secret-xyz leaked", extra={"detail": "sk-ABCDEFGHIJKLMNOPQRSTUV"}
    )
    blob = str(ring.records())
    assert "sk-abcdefghijklmnop" not in blob
    assert "sk-ABCDEFGHIJKLMNOP" not in blob
    assert "loopback-secret-xyz" not in blob


@pytest.fixture
def client_with_logs(tmp_path, ring_logger):
    _, ring = ring_logger
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
        log_buffer=ring,
    )
    with TestClient(app) as test_client:
        yield test_client


def test_the_logs_endpoint_requires_the_token(client_with_logs):
    assert client_with_logs.get("/api/logs").status_code == 401


def test_the_logs_endpoint_returns_entries_and_the_file_path(client_with_logs, ring_logger):
    logger, _ = ring_logger
    logger.info("noise")
    logger.warning("something", extra={"code": "x"})

    body = client_with_logs.get("/api/logs", headers=AUTH).json()
    assert [e["msg"] for e in body["entries"]] == ["something"]
    assert body["file"] == "/tmp/jlt-test.log"

    everything = client_with_logs.get("/api/logs?level=info", headers=AUTH).json()
    assert [e["msg"] for e in everything["entries"]] == ["noise", "something"]


def test_the_logs_endpoint_rejects_an_unknown_level(client_with_logs):
    assert client_with_logs.get("/api/logs?level=debug", headers=AUTH).status_code == 422
