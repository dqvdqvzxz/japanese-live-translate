"""Non-secret settings persistence.

The API key is never here — it lives in the macOS Keychain and reaches this
process through the environment (docs/security.md §2). What is persisted is
preferences and the user's own glossary, both written with mode 0600.

A corrupt or partially-written settings file must not stop the app from
starting; a user who cannot launch the app cannot fix their settings either.
So a bad file is logged and replaced with defaults rather than raised.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .config import Settings
from .logging import get_logger
from .models import GlossaryEntry
from .translation.glossary import GlossaryManager

log = get_logger(__name__)


def default_config_dir() -> Path:
    override = os.environ.get("JLT_CONFIG_DIR")
    if override:
        return Path(override)
    return Path.home() / "Library" / "Application Support" / "JapaneseLiveTranslator"


class SettingsManager:
    def __init__(self, config_dir: Path | None = None, *, api_key_present: bool = False) -> None:
        self._dir = config_dir or default_config_dir()
        self._settings_path = self._dir / "settings.json"
        self._glossary_path = self._dir / "glossary.json"
        self._settings = self._load()
        self._settings.api_key_present = api_key_present
        self.glossary = GlossaryManager.load(self._glossary_path)

    # -- settings -----------------------------------------------------------

    @property
    def settings(self) -> Settings:
        return self._settings

    def _load(self) -> Settings:
        if not self._settings_path.exists():
            return Settings()
        try:
            raw = json.loads(self._settings_path.read_text(encoding="utf-8"))
            raw.pop("api_key_present", None)  # derived, never trusted from disk
            return Settings.model_validate(raw)
        except (json.JSONDecodeError, ValidationError, OSError) as exc:
            log.warning(
                "settings file unusable; falling back to defaults",
                extra={"error": type(exc).__name__},
            )
            return Settings()

    def update(self, patch: dict[str, Any]) -> Settings:
        """Apply a partial update, section by section.

        An invalid patch leaves the previous settings untouched and raises, so
        a bad request from the UI cannot corrupt a working configuration.
        """
        current = self._settings.model_dump()
        current.pop("api_key_present", None)
        for section, value in patch.items():
            if section == "api_key_present":
                continue  # read-only mirror of Keychain state
            if isinstance(value, dict) and isinstance(current.get(section), dict):
                current[section].update(value)
            else:
                current[section] = value

        updated = Settings.model_validate(current)
        updated.api_key_present = self._settings.api_key_present
        self._settings = updated
        self.save()
        return updated

    def save(self) -> None:
        payload = self._settings.model_dump()
        payload.pop("api_key_present", None)
        _atomic_write(self._settings_path, json.dumps(payload, indent=2, ensure_ascii=False))

    # -- glossary -----------------------------------------------------------

    def set_glossary(self, entries: list[GlossaryEntry]) -> int:
        self.glossary.replace(entries)
        self.glossary.save(self._glossary_path)
        return len(entries)

    @property
    def history_path(self) -> Path:
        return self._dir / "history.sqlite3"


def _atomic_write(path: Path, text: str) -> None:
    """Write via a temporary file and rename.

    A crash midway through a direct write would leave a truncated settings file
    that fails to parse on next launch.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        tmp.chmod(0o600)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
