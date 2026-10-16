"""Decides what constitutes a unit of translation.

A VAD turn is not a sentence. It can be a two-word interjection ("はい。"), or
half a clause cut off by a breath. Translating those individually wastes tokens
and produces bad Vietnamese, because the model has no clause to work with.

So finals are merged into *segments* under three rules:

  * a final that ends in a sentence terminator and is long enough goes out
    immediately;
  * a short or unterminated final is held briefly, in case the next one
    completes it;
  * a held fragment is released anyway once `incomplete_flush_ms` passes, so
    nothing a participant said is ever silently dropped.

Deliberately **not** done: content-hash deduplication. Japanese meetings are
full of genuine repeats — "はい。" three times from three people is three
things that were actually said. Deduplicating by text would delete real
content. Identity is the transcript `seq`, and that is what we dedupe on.

This class is pure and synchronous: it takes transcripts and a clock reading,
and returns segments. Every timing rule is therefore testable without an event
loop or a real clock.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass, field

from ..config import SegmentationSettings
from ..logging import get_logger
from ..models import Segment, Transcript

log = get_logger(__name__)

#: Japanese and ASCII sentence terminators. A reading comma (、) is not one.
SENTENCE_TERMINATORS = "。．.！!？?"


def ends_sentence(text: str) -> bool:
    stripped = text.rstrip()
    return bool(stripped) and stripped[-1] in SENTENCE_TERMINATORS


@dataclass
class _Pending:
    texts: list[str] = field(default_factory=list)
    seqs: list[int] = field(default_factory=list)
    first_seq: int = 0
    committed_at: float = 0.0
    last_at: float = 0.0
    deadline: float = 0.0

    @property
    def text(self) -> str:
        return "".join(self.texts)


class SegmentManager:
    def __init__(
        self,
        settings: SegmentationSettings,
        *,
        repair: Callable[[str], str] | None = None,
    ) -> None:
        self._settings = settings
        # `repair` is the glossary's identifier reconstruction, injected rather
        # than imported so the merge rules can be tested without a glossary.
        self._repair = repair
        self._pending: _Pending | None = None
        self._seen_seqs: set[int] = set()
        self._ids = itertools.count(1)

        self.segments_emitted = 0
        self.duplicates_suppressed = 0
        self.merges = 0

    # -- input --------------------------------------------------------------

    def add(self, transcript: Transcript, now: float) -> list[Segment]:
        """Feed one final transcript. Returns any segments it completes."""
        if transcript.seq in self._seen_seqs:
            self.duplicates_suppressed += 1
            log.info("duplicate transcript suppressed", extra={"seq": transcript.seq})
            return []
        self._seen_seqs.add(transcript.seq)

        text = transcript.text.strip()
        if not text:
            return []

        out: list[Segment] = []
        pending = self._pending

        if pending is not None:
            window_s = self._settings.merge_window_ms / 1000.0
            too_late = (now - pending.last_at) > window_s
            too_long = len(pending.text) + len(text) > self._settings.max_merge_chars
            if too_late or too_long:
                # The fragment cannot absorb this final. Release it on its own
                # rather than gluing together two unrelated utterances.
                out.append(self._emit(pending, now))
                pending = None

        if pending is None:
            pending = _Pending(
                first_seq=transcript.seq,
                committed_at=transcript.committed_at or now,
            )
        else:
            self.merges += 1

        pending.texts.append(text)
        pending.seqs.append(transcript.seq)
        pending.last_at = now
        pending.deadline = now + self._settings.incomplete_flush_ms / 1000.0
        self._pending = pending

        if self._is_translatable(pending.text):
            out.append(self._emit(pending, now))
            self._pending = None

        return out

    def _is_translatable(self, text: str) -> bool:
        if len(text) >= self._settings.max_merge_chars:
            # Too long to keep holding; the next final would only make it worse.
            return True
        if not ends_sentence(text):
            return False
        return len(text) >= self._settings.min_translate_chars

    # -- time-driven output -------------------------------------------------

    def flush_due(self, now: float) -> list[Segment]:
        """Release a held fragment whose patience has run out.

        Called periodically by the session loop. Without it, a trailing
        fragment that never gets a follow-up would sit in memory unseen.
        """
        pending = self._pending
        if pending is None or now < pending.deadline:
            return []
        self._pending = None
        return [self._emit(pending, now)]

    def flush_all(self, now: float) -> list[Segment]:
        """Release everything, e.g. on pause or stop."""
        pending = self._pending
        if pending is None:
            return []
        self._pending = None
        return [self._emit(pending, now)]

    # -- helpers ------------------------------------------------------------

    def _emit(self, pending: _Pending, now: float) -> Segment:
        text = pending.text
        if self._repair is not None:
            text = self._repair(text)
        self.segments_emitted += 1
        return Segment(
            segment_id=f"seg_{next(self._ids)}",
            # The segment inherits the seq of its *first* transcript, so merged
            # segments still sort where the speech actually started.
            seq=pending.first_seq,
            source_text=text,
            source_seqs=list(pending.seqs),
            state="final",
            created_at=now,
            committed_at=pending.committed_at,
        )

    @property
    def has_pending(self) -> bool:
        return self._pending is not None
