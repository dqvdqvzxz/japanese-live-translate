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

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from starlette.websockets import WebSocketDisconnect

from . import __version__
from .audio.capture_service import CaptureError, PermissionDenied
from .events import EventDispatcher
from .export import EXTENSIONS, MEDIA_TYPES, archive_filename, render, render_all
from .export import filename as export_filename
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


def api_error(status: int, code: str, message: str) -> HTTPException:
    """A machine-readable error.

    The UI has to act differently on different failures — a missing Screen
    Recording permission needs a button that opens System Settings, a missing
    API key needs the settings panel — and it cannot do that by matching on
    prose. `detail` is therefore an object, not a string.
    """
    return HTTPException(status_code=status, detail={"code": code, "message": message})


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

    # The renderer is always a different origin from the backend
    # (tauri://localhost or the Vite dev server, versus http://127.0.0.1:<port>),
    # so without CORS headers the browser blocks every request before it is
    # sent — the Origin check below would never even see them.
    #
    # This is an explicit allowlist of the app's own origins, never a wildcard.
    # Preflights are cached so the extra round trip is not paid per call.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(ALLOWED_ORIGINS),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        # Without this the renderer cannot read the filename the backend
        # chose for an export: cross-origin responses only expose a handful
        # of headers by default, and Content-Disposition is not one of them.
        expose_headers=["Content-Disposition"],
        max_age=600,
    )

    async def authorize(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> None:
        if not _origin_allowed(request.headers.get("origin")):
            raise api_error(403, "origin_not_allowed", "This origin is not allowed.")
        presented = None
        if authorization and authorization.lower().startswith("bearer "):
            presented = authorization[7:]
        if not token_matches(token, presented):
            raise api_error(401, "unauthorized", "Missing or invalid token.")

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
            raise api_error(422, "invalid_settings", _first_error(exc)) from exc

    @app.post("/api/glossary", dependencies=guard)
    async def set_glossary(payload: GlossaryRequest) -> dict[str, int]:
        return {"count": settings_manager.set_glossary(payload.entries)}

    # -- audio --------------------------------------------------------------

    @app.get("/api/audio/sources", dependencies=guard)
    async def list_sources() -> dict[str, Any]:
        try:
            sources = await session.list_sources()
        except PermissionDenied as exc:
            raise api_error(403, exc.code, str(exc)) from exc
        except CaptureError as exc:
            raise api_error(503, exc.code, str(exc)) from exc
        return {"sources": [source.model_dump() for source in sources]}

    # -- session ------------------------------------------------------------

    @app.post("/api/session/start", dependencies=guard)
    async def start(payload: StartRequest) -> dict[str, str]:
        try:
            session_id = await session.start(payload.source_id)
        except SessionError as exc:
            status = 409 if exc.code in ("already_running", "api_key_missing") else 400
            raise api_error(status, exc.code, str(exc)) from exc
        except PermissionDenied as exc:
            raise api_error(403, exc.code, str(exc)) from exc
        except CaptureError as exc:
            raise api_error(503, exc.code, str(exc)) from exc
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
        """Segments from the session in progress."""
        return {"segments": [s.model_dump() for s in session.history.recent(limit)]}

    @app.get("/api/history/sessions", dependencies=guard)
    async def list_sessions(
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict[str, Any]:
        """Finished sessions, newest first.

        `persisting` tells the UI whether this list can span restarts; with
        saving turned off it holds only the current session, and the UI says so
        rather than looking broken.
        """
        return {
            "persisting": session.history.persisting,
            "sessions": [r.model_dump() for r in session.history.list_sessions(limit)],
        }

    @app.get("/api/history/export", dependencies=guard)
    async def export_all(fmt: str = Query(default="md", alias="format")) -> Response:
        """Export every stored session as one file.

        Declared before the per-session route so `/export` is not mistaken for
        a session id.
        """
        if fmt not in EXTENSIONS:
            raise api_error(
                400, "unsupported_format", f"Supported formats: {', '.join(EXTENSIONS)}."
            )
        records = session.history.list_sessions(limit=10_000)
        if not records:
            raise api_error(404, "history_empty", "There is nothing to export yet.")
        bundle = [
            (record, session.history.session_segments(record.session_id)) for record in records
        ]
        body = render_all(bundle, fmt)  # type: ignore[arg-type]
        return Response(
            content=body,
            media_type=MEDIA_TYPES[fmt],
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{archive_filename(fmt)}"'  # type: ignore[arg-type]
                )
            },
        )

    @app.get("/api/history/sessions/{session_id}", dependencies=guard)
    async def session_detail(session_id: str) -> dict[str, Any]:
        record = session.history.get_session(session_id)
        if record is None:
            raise api_error(404, "session_not_found", "No such session in history.")
        return {
            "session": record.model_dump(),
            "segments": [s.model_dump() for s in session.history.session_segments(session_id)],
        }

    @app.post("/api/history/sessions/{session_id}/summary", dependencies=guard)
    async def summarize(session_id: str) -> dict[str, Any]:
        record = session.history.get_session(session_id)
        if record is None:
            raise api_error(404, "session_not_found", "No such session in history.")
        try:
            summary = await session.summarize(session_id)
        except SessionError as exc:
            raise api_error(409, exc.code, str(exc)) from exc
        except Exception as exc:
            raise api_error(
                502, "summary_failed", f"Could not generate a summary: {type(exc).__name__}"
            ) from exc
        return summary.model_dump()

    @app.get("/api/history/sessions/{session_id}/export", dependencies=guard)
    async def export_session(
        session_id: str, fmt: str = Query(default="md", alias="format")
    ) -> Response:
        if fmt not in EXTENSIONS:
            raise api_error(
                400, "unsupported_format", f"Supported formats: {', '.join(EXTENSIONS)}."
            )
        record = session.history.get_session(session_id)
        if record is None:
            raise api_error(404, "session_not_found", "No such session in history.")
        segments = session.history.session_segments(session_id)
        body = render(record, segments, fmt)  # type: ignore[arg-type]
        return Response(
            content=body,
            media_type=MEDIA_TYPES[fmt],
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{export_filename(record, fmt)}"'  # type: ignore[arg-type]
                )
            },
        )

    @app.delete("/api/history/sessions/{session_id}", dependencies=guard)
    async def delete_session(session_id: str) -> dict[str, str]:
        session.history.delete_session(session_id)
        return {"status": "deleted"}

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
        return JSONResponse(
            status_code=409, content={"detail": {"code": exc.code, "message": str(exc)}}
        )

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
        raise api_error(status, exc.code, str(exc)) from exc


def _first_error(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "invalid settings"
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    return f"{location}: {first.get('msg', 'invalid')}"
