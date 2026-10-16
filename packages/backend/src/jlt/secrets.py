"""API key and local-token handling.

Rules enforced here (docs/security.md §2):
  * the key is read from the environment once, never from disk;
  * it is wrapped so that printing, logging or formatting it yields `***`;
  * the local bearer token is generated per process and compared in constant
    time.
"""

from __future__ import annotations

import os
import secrets as _secrets


class Secret:
    """A string that refuses to render itself.

    `repr`, `str` and f-string interpolation all produce `***`, so an accidental
    `logger.info(f"key={key}")` leaks nothing. Call `reveal()` deliberately at
    the single point of use.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __bool__(self) -> bool:
        return bool(self._value)

    def __repr__(self) -> str:
        return "Secret(***)"

    __str__ = __repr__

    def __format__(self, _spec: str) -> str:
        return "***"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Secret):
            return NotImplemented
        return _secrets.compare_digest(self._value, other._value)

    def __hash__(self) -> int:  # pragma: no cover - identity semantics suffice
        return hash(("Secret", len(self._value)))


def load_api_key() -> Secret | None:
    """Read the OpenAI key from the environment.

    The Rust shell reads it from the Keychain and places it in the child's
    environment on spawn. It is deliberately not accepted on argv, which is
    readable by any local process via `ps`.
    """
    raw = os.environ.get("OPENAI_API_KEY", "").strip()
    return Secret(raw) if raw else None


def new_local_token() -> Secret:
    """A 256-bit bearer token for the loopback API, valid for this process only."""
    return Secret(_secrets.token_urlsafe(32))


def token_matches(expected: Secret, presented: str | None) -> bool:
    if not presented:
        return False
    return _secrets.compare_digest(expected.reveal(), presented)
