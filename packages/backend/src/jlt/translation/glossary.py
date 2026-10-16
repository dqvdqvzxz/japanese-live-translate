"""IT glossary and identifier reconstruction.

Two jobs:

1. Hold the user's JA→VI term list and expose it to the translation prompt and
   to the STT keyword hints.
2. Repair identifiers that speech-to-text could not preserve.

The second exists because of a measured failure, not a hypothetical one. A live
smoke test (docs/openai-verification.md §3b) showed `チケットABC-1234` coming
back as `チケットA B Cの千二百三十四`, and keyword hints only improved the
number to `1234` — the letters stayed separated. Ticket ids are exactly the
tokens a meeting attendee cannot afford to have mangled, so we repair them with
a deterministic rule rather than asking a model to be careful.

The repair is deliberately conservative: it only rewrites text into an
identifier the user has already declared in their glossary. It never invents
one. A false repair would be worse than no repair, because it would silently
change which ticket the sentence is about.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from ..logging import get_logger
from ..models import GlossaryEntry

log = get_logger(__name__)

#: A glossary term is treated as an identifier if it looks like one: letters and
#: digits joined by a separator, e.g. ABC-1234, JIRA_42, #4821.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z]{1,10}[-_#]?\d{1,8}$")

_KANJI_DIGITS = {
    0: "",
    1: "一",
    2: "二",
    3: "三",
    4: "四",
    5: "五",
    6: "六",
    7: "七",
    8: "八",
    9: "九",
}


def int_to_kanji(value: int) -> str:
    """Render a non-negative integer the way Japanese speech-to-text writes it.

    Used to build the *spoken* form of an identifier's number so it can be
    matched and replaced. Supports values up to 99,999,999, which comfortably
    covers ticket numbers.
    """
    if value == 0:
        return "零"
    if value >= 100_000_000:
        return str(value)

    def under_10000(n: int) -> str:
        out = ""
        for unit_value, unit in ((1000, "千"), (100, "百"), (10, "十")):
            digit, n = divmod(n, unit_value)
            if digit:
                # 1000 is 千, not 一千; same for 百 and 十.
                out += ("" if digit == 1 else _KANJI_DIGITS[digit]) + unit
        if n:
            out += _KANJI_DIGITS[n]
        return out

    man, rest = divmod(value, 10_000)
    out = (under_10000(man) + "万") if man else ""
    return out + under_10000(rest)


def is_identifier(term: str) -> bool:
    return bool(_IDENTIFIER_RE.match(term.strip()))


def _spoken_letter_pattern(letters: str) -> str:
    """Letters may arrive separated, as `A B C`, and in either case."""
    return r"[\s　]*".join(re.escape(ch) + "" for ch in letters)


def _identifier_pattern(identifier: str) -> re.Pattern[str] | None:
    """A regex matching the ways this identifier can be spoken and transcribed."""
    match = re.match(r"^([A-Za-z]{1,10})[-_#]?(\d{1,8})$", identifier.strip())
    if match is None:
        return None
    letters, digits = match.group(1), match.group(2)

    number_forms = [re.escape(digits), re.escape(int_to_kanji(int(digits)))]
    # Separators actually observed between the letters and the number: a hyphen,
    # whitespace, or the Japanese particle の.
    separator = r"[\s　]*(?:[-_#]|の|ノ)?[\s　]*"
    pattern = (
        rf"(?i:{_spoken_letter_pattern(letters)})"
        rf"{separator}"
        rf"(?:{'|'.join(number_forms)})"
    )
    return re.compile(pattern)


def normalize_identifiers(text: str, identifiers: list[str]) -> str:
    """Rewrite spoken identifier forms back to their canonical written form.

    Only identifiers supplied by the caller are ever produced, so this can
    correct `A B Cの千二百三十四` to `ABC-1234` but can never invent a ticket
    number that the user never declared.
    """
    if not text or not identifiers:
        return text

    # Full-width letters and digits become ASCII, so Ａ and １ match too.
    out = unicodedata.normalize("NFKC", text)
    for identifier in identifiers:
        pattern = _identifier_pattern(identifier)
        if pattern is not None:
            out = pattern.sub(identifier, out)
    return out


class GlossaryManager:
    """Owns the term list and derives the views other components need."""

    def __init__(self, entries: list[GlossaryEntry] | None = None) -> None:
        self._entries: list[GlossaryEntry] = list(entries or [])

    @property
    def entries(self) -> list[GlossaryEntry]:
        return list(self._entries)

    def replace(self, entries: list[GlossaryEntry]) -> None:
        self._entries = list(entries)
        log.info("glossary updated", extra={"count": len(self._entries)})

    @property
    def identifiers(self) -> list[str]:
        """Glossary terms that look like identifiers, longest first.

        Longest first so `ABC-12345` is matched before `ABC-1234` would consume
        its prefix.
        """
        found = [e.ja.strip() for e in self._entries if is_identifier(e.ja)]
        return sorted(set(found), key=len, reverse=True)

    def repair(self, text: str) -> str:
        return normalize_identifiers(text, self.identifiers)

    # -- persistence --------------------------------------------------------
    #
    # The glossary is user-authored terminology, not meeting content, so unlike
    # transcripts it is persisted by default. See docs/security.md §4.

    @classmethod
    def load(cls, path: Path) -> GlossaryManager:
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            entries = [GlossaryEntry.model_validate(item) for item in raw.get("entries", [])]
        except (json.JSONDecodeError, ValueError):
            log.warning("glossary file is unreadable; starting empty")
            return cls()
        return cls(entries)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"entries": [e.model_dump() for e in self._entries]}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        path.chmod(0o600)
