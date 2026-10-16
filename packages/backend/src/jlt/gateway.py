"""Loopback HTTP and WebSocket gateway.

Transport only — no business logic lives here. Security properties enforced at
this boundary (docs/security.md §3):

  * bind to 127.0.0.1 only (done by the caller in `__main__`);
  * every `/api/*` route and the WebSocket handshake require a per-process
    bearer token, compared in constant time;
  * `Origin`, when present, must be one this app actually uses, so a web page
    the user happens to visit cannot drive the backend even if it guessed the
    port;
  * the API key is never returned, only `api_key_present`.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from starlette.websockets import WebSocketDisconnect

from . import __version__
from .audio.capture_service import CaptureError, PermissionDenied
from .events import EventDispatcher
from .logging import get_logger
from .models import GlossaryEntry
from .secrets import Secret, token_matches
from .session import SessionError, SessionManager
from .settings_manager import SettingsManager

log = get_logger(__name__)

#: The renderer's own origins. Anything else is refused.
ALLOWED_ORIGINS = frozenset(
    {
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    }
)

WS_CLOSE_UNAUTHORIZED = 4401


class StartRequest(BaseModel):
    source_id: str


class GlossaryRequest(BaseModel):
    entries: list[GlossaryEntry]


def _origin_allowed(origin: str | None) -> bool:
    # A missing Origin is a non-browser client (the Tauri webview on some
    # paths, curl during development). The bearer token is the real control.
    return origin is None or origin in ALLOWED_ORIGINS


def create_app(
    *,
    token: Secret,
    settings_manager: SettingsManager,
    session: SessionManager,
    dispatcher: EventDispatcher,
    started_at: float,
) -> FastAPI:
    app = FastAPI(
        title="Japanese Live Translator", version=__version__, docs_url=None, redoc_url=None
    )

    async def authorize(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> None:
        if not _origin_allowed(request.headers.get("origin")):
            raise HTTPException(status_code=403, detail="origin_not_allowed")
        presented = None
        if authorization and authorization.lower().startswith("bearer "):
            presented = authorization[7:]
        if not token_matches(token, presented):
            raise HTTPException(status_code=401, detail="unauthorized")

    guard = [Depends(authorize)]

    # -- unauthenticated ----------------------------------------------------

    @app.get("/health")
    async def health() -> dict[str, Any]:
        """Readiness probe for the supervisor. Carries no session content."""
        return {
            "status": "ok",
            "version": __version__,
            "uptime_s": round(asyncio.get_running_loop().time() - started_at, 1),
        }

    # -- settings -----------------------------------------------------------

    @app.get("/api/settings", dependencies=guard)
    async def get_settings() -> dict[str, Any]:
        return settings_manager.settings.model_dump()

    @app.patch("/api/settings", dependencies=guard)
    async def patch_settings(patch: dict[str, Any]) -> dict[str, Any]:
        try:
            return settings_manager.update(patch).model_dump()
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=_first_error(exc)) from exc

    @app.post("/api/glossary", dependencies=guard)
    async def set_glossary(payload: GlossaryRequest) -> dict[str, int]:
        return {"count": settings_manager.set_glossary(payload.entries)}

    # -- audio --------------------------------------------------------------

    @app.get("/api/audio/sources", dependencies=guard)
    async def list_sources() -> dict[str, Any]:
        try:
            sources = await session.list_sources()
        except PermissionDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except CaptureError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"sources": [source.model_dump() for source in sources]}

    # -- session ------------------------------------------------------------

    @app.post("/api/session/start", dependencies=guard)
    async def start(payload: StartRequest) -> dict[str, str]:
        try:
            session_id = await session.start(payload.source_id)
        except SessionError as exc:
            status = 409 if exc.code in ("already_running", "api_key_missing") else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        except PermissionDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except CaptureError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"session_id": session_id}

    @app.post("/api/session/pause", dependencies=guard)
    async def pause() -> dict[str, str]:
        await _session_action(session.pause)
        return {"state": session.state}

    @app.post("/api/session/resume", dependencies=guard)
    async def resume() -> dict[str, str]:
        await _session_action(session.resume)
        return {"state": session.state}

    @app.post("/api/session/stop", dependencies=guard)
    async def stop() -> dict[str, Any]:
        stats = await _session_action(session.stop)
        return {"state": session.state, "stats": stats.model_dump() if stats else None}

    @app.get("/api/session", dependencies=guard)
    async def session_state() -> dict[str, Any]:
        return {
            "session_id": session.session_id,
            "state": session.state,
            "source": session.source.model_dump() if session.source else None,
        }

    @app.get("/api/metrics", dependencies=guard)
    async def metrics() -> dict[str, Any]:
        return session.stats().model_dump()

    # -- history ------------------------------------------------------------

    @app.get("/api/history", dependencies=guard)
    async def history(limit: int = Query(default=200, ge=1, le=2000)) -> dict[str, Any]:
        return {"segments": [s.model_dump() for s in session.history.recent(limit)]}

    @app.delete("/api/history", dependencies=guard)
    async def clear_history() -> dict[str, str]:
        session.history.clear()
        return {"status": "cleared"}

    # -- events -------------------------------------------------------------

    @app.websocket("/ws")
    async def events(socket: WebSocket, ws_token: str = Query(alias="token")) -> None:
        # The token is in the query string because a browser cannot set headers
        # on a WebSocket handshake. See docs/security.md §3 for why that is
        # acceptable here and what it does not protect against.
        if not _origin_allowed(socket.headers.get("origin")) or not token_matches(token, ws_token):
            await socket.close(code=WS_CLOSE_UNAUTHORIZED)
            return

        await socket.accept()
        async with dispatcher.subscribe() as subscription:
            sender = asyncio.create_task(_pump(socket, subscription))
            try:
                while True:
                    # Reading is what detects a disconnect promptly. The only
                    # client message we honour is a keepalive ping.
                    message = await socket.receive_json()
                    if isinstance(message, dict) and message.get("type") == "ping":
                        await socket.send_json({"type": "pong"})
            except (WebSocketDisconnect, RuntimeError, ValueError):
                pass
            finally:
                sender.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sender

    @app.exception_handler(SessionError)
    async def session_error_handler(_: Request, exc: SessionError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    return app


async def _pump(socket: WebSocket, subscription: Any) -> None:
    try:
        while True:
            event = await subscription.get()
            await socket.send_json(event.model_dump(mode="json"))
    except (WebSocketDisconnect, RuntimeError):
        return


async def _session_action(action: Any) -> Any:
    try:
        return await action()
    except SessionError as exc:
        status = 409 if exc.code in ("not_running", "not_paused", "already_running") else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc


def _first_error(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "invalid settings"
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    return f"{location}: {first.get('msg', 'invalid')}"
