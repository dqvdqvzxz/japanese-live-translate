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


def default_log_path() -> Path:
    override = os.environ.get("JLT_LOG_FILE")
    if override:
        return Path(override)
    return Path.home() / "Library" / "Logs" / "JapaneseLiveTranslator" / "backend.log"


def configure(level: str = "INFO", *, log_file: Path | None = None) -> None:
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

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    stream.addFilter(redaction)
    handlers.append(stream)

    path = log_file or default_log_path()
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


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
