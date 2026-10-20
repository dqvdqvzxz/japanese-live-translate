"""Structured logging with hard guarantees about what must never be logged.

Two rules from docs/security.md:
  1. Meeting content (transcripts, translations) never appears in a log record,
     at any level. This is enforced by convention at call sites — we log counts,
     ids and latencies, never text.
  2. Credentials never appear. That one is enforced mechanically here, because
     convention is not enough for a secret.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

_API_KEY_RE = re.compile(r"sk-[A-Za-z0-9_\-]{16,}")
_REDACTED = "sk-***"

_extra_secrets: list[str] = []


def register_secret(value: str) -> None:
    """Add a literal string (e.g. the loopback token) to the redaction set."""
    if value and value not in _extra_secrets:
        _extra_secrets.append(value)


def _scrub(text: str) -> str:
    out = _API_KEY_RE.sub(_REDACTED, text)
    for secret in _extra_secrets:
        out = out.replace(secret, "***")
    return out


class RedactionFilter(logging.Filter):
    """Scrubs the message, the args and any formatted exception text."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _scrub(record.msg)
        if record.args:
            # Only strings are scrubbed, and they stay strings. Coercing every
            # argument to str would break printf-style formatting: a library
            # logging "%d" with an int would raise once the int became a str.
            if isinstance(record.args, dict):
                record.args = {
                    key: _scrub(value) if isinstance(value, str) else value
                    for key, value in record.args.items()
                }
            else:
                record.args = tuple(
                    _scrub(arg) if isinstance(arg, str) else arg for arg in record.args
                )
        if record.exc_text:
            record.exc_text = _scrub(record.exc_text)
        return True


class JsonFormatter(logging.Formatter):
    _RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
        "message",
        "asctime",
        "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": record.created,
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in self._RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = _scrub(self.formatException(record.exc_info))
        return _scrub(json.dumps(payload, ensure_ascii=False, default=str))


class RingBufferHandler(logging.Handler):
    """Keeps the most recent records in memory for the UI's log view.

    Records are stored in the same redacted JSON shape as the log file, so what
    the view shows can never contain more than the file already does: counts,
    ids, codes and latencies — never meeting content, never a credential.
    """

    def __init__(self, capacity: int = 500, *, path: Path | None = None) -> None:
        super().__init__()
        self._entries: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._path = path
        self.setFormatter(JsonFormatter())
        self.addFilter(RedactionFilter())

    @property
    def path(self) -> Path | None:
        return self._path

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._entries.append(json.loads(self.format(record)))
        except Exception:
            self.handleError(record)

    def records(self, *, min_level: str = "warning", limit: int = 200) -> list[dict[str, Any]]:
        floor = logging.WARNING if min_level == "warning" else logging.NOTSET
        levels = logging.getLevelNamesMapping()
        # Under the handler's own lock: records are appended from several
        # threads, and copying a deque while it changes is an error.
        self.acquire()
        try:
            snapshot = list(self._entries)
        finally:
            self.release()
        kept = [e for e in snapshot if levels.get(str(e.get("level", "")).upper(), 0) >= floor]
        return kept[-limit:]


def default_log_path() -> Path:
    override = os.environ.get("JLT_LOG_FILE")
    if override:
        return Path(override)
    return Path.home() / "Library" / "Logs" / "JapaneseLiveTranslator" / "backend.log"


def configure(level: str = "INFO", *, log_file: Path | None = None) -> RingBufferHandler:
    """Install JSON logging on stderr and, where possible, a rotating file.

    stderr, not stdout: stdout is the handshake channel to the Rust supervisor
    and must stay parseable.

    The file matters because a packaged app launched from Finder has no
    visible stderr at all — without it there is simply no way to find out why
    something failed on a user's machine. Content is still never logged; this
    is counts, ids, latencies and error codes.
    """
    formatter = JsonFormatter()
    redaction = RedactionFilter()

    handlers: list[logging.Handler] = []
    path = log_file or default_log_path()
    ring = RingBufferHandler(path=path)
    handlers.append(ring)

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    stream.addFilter(redaction)
    handlers.append(stream)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Bounded: a long meeting should not fill the disk, and nobody reads
        # more than the last couple of megabytes when diagnosing.
        rotating = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
        rotating.setFormatter(formatter)
        rotating.addFilter(redaction)
        handlers.append(rotating)
        path.chmod(0o600)
    except OSError:
        # An unwritable log location must never stop the app from starting.
        pass

    root = logging.getLogger()
    root.handlers.clear()
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn's access log would record query strings, which carry the loopback
    # token. Silence it rather than rely on redaction.
    logging.getLogger("uvicorn.access").disabled = True
    return ring


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
