"""Backend process entry point.

Handshake contract with the Rust supervisor:

  * the backend binds 127.0.0.1 on an OS-assigned port;
  * it prints exactly one JSON line to **stdout** —
    `{"type":"listening","port":N,"token":"..."}` — and nothing else, ever;
  * all logging goes to stderr, with credential redaction installed.

The token is generated per process and never written to disk. The API key is
read once from the environment, where the supervisor placed it after reading
the Keychain; it is never accepted on argv, which `ps` exposes to any local
process (docs/security.md §2).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sys

import uvicorn

from . import logging as jlt_logging
from .events import EventDispatcher
from .gateway import create_app
from .history import HistoryManager
from .secrets import new_local_token
from .session import SessionManager, default_factories
from .settings_manager import SettingsManager

HOST = "127.0.0.1"


async def run() -> int:
    jlt_logging.configure(os.environ.get("JLT_LOG_LEVEL", "INFO"))
    log = jlt_logging.get_logger("jlt")

    from .secrets import load_api_key

    api_key = load_api_key()
    token = new_local_token()
    # Belt and braces: even though neither should ever reach a log call, both
    # are registered with the redaction filter.
    jlt_logging.register_secret(token.reveal())
    if api_key is not None:
        jlt_logging.register_secret(api_key.reveal())

    settings_manager = SettingsManager(api_key_present=api_key is not None)
    dispatcher = EventDispatcher()
    history = HistoryManager(
        persist=settings_manager.settings.privacy.persist_transcripts,
        db_path=settings_manager.history_path,
    )

    if api_key is None:
        # Start anyway: the user needs a running UI in order to enter a key.
        # Starting a capture session is refused until one exists.
        log.warning("no OPENAI_API_KEY present; capture will be refused")
    capture_factory, stt_factory, translation_factory = default_factories(api_key, settings_manager)

    loop = asyncio.get_running_loop()
    session = SessionManager(
        settings_manager,
        dispatcher,
        capture_factory=capture_factory,
        stt_factory=stt_factory,
        translation_factory=translation_factory,
        history=history,
    )

    app = create_app(
        token=token,
        settings_manager=settings_manager,
        session=session,
        dispatcher=dispatcher,
        started_at=loop.time(),
    )

    config = uvicorn.Config(app, host=HOST, port=0, log_config=None, access_log=False)
    server = uvicorn.Server(config)

    serve_task = asyncio.create_task(server.serve())
    port = await _wait_for_port(server)
    if port is None:
        log.error("server failed to bind")
        return 1

    # The one and only stdout line.
    sys.stdout.write(
        json.dumps({"type": "listening", "port": port, "token": token.reveal()}) + "\n"
    )
    sys.stdout.flush()
    log.info("backend listening", extra={"port": port})

    stopping = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stopping.set)

    await stopping.wait()
    log.info("shutting down")

    # Stop the session before the server so capture and the helper process are
    # released even if a client is still connected.
    if session.active:
        with contextlib.suppress(Exception):
            await session.stop()
    history.close()

    server.should_exit = True
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(serve_task, timeout=10.0)
    return 0


async def _wait_for_port(server: uvicorn.Server, timeout_s: float = 15.0) -> int | None:
    """Uvicorn assigns the port during startup; read it back once bound."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        servers = getattr(server, "servers", None)
        if servers:
            for bound in servers:
                for socket in bound.sockets:
                    return int(socket.getsockname()[1])
        await asyncio.sleep(0.02)
    return None


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
