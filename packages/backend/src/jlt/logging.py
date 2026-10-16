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
import re
import sys
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
            if isinstance(record.args, dict):
                record.args = {k: _scrub(str(v)) for k, v in record.args.items()}
            else:
                record.args = tuple(_scrub(str(a)) for a in record.args)
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


def configure(level: str = "INFO") -> None:
    """Install JSON logging on stderr.

    stderr, not stdout: stdout is the handshake channel to the Rust supervisor
    and must stay parseable.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn's access log would record query strings, which carry the loopback
    # token. Silence it rather than rely on redaction.
    logging.getLogger("uvicorn.access").disabled = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
