"""Segment merging, completeness and deduplication rules.

The manager is pure and takes an explicit clock reading, so every timing rule
is asserted exactly rather than by sleeping.
"""

from __future__ import annotations

from jlt.config import SegmentationSettings
from jlt.models import Transcript
from jlt.segments.manager import SegmentManager, ends_sentence


def transcript(seq: int, text: str) -> Transcript:
    return Transcript(item_id=f"i{seq}", seq=seq, text=text, is_final=True)


def manager(**overrides) -> SegmentManager:
    defaults = {
        "min_translate_chars": 8,
        "merge_window_ms": 1000,
        "max_merge_chars": 60,
        "incomplete_flush_ms": 2000,
    }
    return SegmentManager(SegmentationSettings(**{**defaults, **overrides}))


def test_terminators_recognised():
    assert ends_sentence("終わりました。")
    assert ends_sentence("本当ですか？")
    assert ends_sentence("Done.")
    assert not ends_sentence("それで、")
    assert not ends_sentence("")


def test_complete_sentence_is_emitted_immediately():
    mgr = manager()
    out = mgr.add(transcript(1, "来週のリリースは延期されません。"), now=0.0)
    assert len(out) == 1
    assert out[0].source_text == "来週のリリースは延期されません。"
    assert out[0].seq == 1


def test_short_interjection_is_held_not_translated_alone():
    """ "はい。" on its own makes poor Vietnamese and wastes tokens."""
    mgr = manager()
    assert mgr.add(transcript(1, "はい。"), now=0.0) == []
    assert mgr.has_pending


def test_unterminated_fragment_is_held():
    mgr = manager()
    assert mgr.add(transcript(1, "それで、その件なんですけど"), now=0.0) == []
    assert mgr.has_pending


def test_held_fragment_merges_with_the_next_final():
    mgr = manager()
    mgr.add(transcript(1, "はい。"), now=0.0)
    out = mgr.add(transcript(2, "確認して折り返します。"), now=0.5)
    assert len(out) == 1
    assert out[0].source_text == "はい。確認して折り返します。"
    assert out[0].source_seqs == [1, 2]
    assert mgr.merges == 1


def test_merged_segment_keeps_the_seq_of_the_first_transcript():
    """Ordering must follow where the speech started, not where it ended."""
    mgr = manager()
    mgr.add(transcript(7, "はい。"), now=0.0)
    out = mgr.add(transcript(8, "確認して折り返します。"), now=0.2)
    assert out[0].seq == 7


def test_merge_window_expiry_releases_the_fragment_separately():
    """Two utterances a long pause apart are not one sentence.

    The held fragment is released on its own, and the new final — itself a
    complete sentence — is released immediately after it. Two segments, never
    one glued-together line.
    """
    mgr = manager()
    mgr.add(transcript(1, "はい。"), now=0.0)
    out = mgr.add(transcript(2, "別の話を始めます。"), now=5.0)
    assert [s.source_text for s in out] == ["はい。", "別の話を始めます。"]
    assert [s.source_seqs for s in out] == [[1], [2]]
    assert not mgr.has_pending


def test_merge_respects_the_maximum_length():
    mgr = manager()
    mgr.add(transcript(1, "あ" * 50), now=0.0)
    out = mgr.add(transcript(2, "い" * 50), now=0.1)
    assert len(out) == 1
    assert out[0].source_text == "あ" * 50


def test_overlong_fragment_is_released_even_without_a_terminator():
    mgr = manager()
    out = mgr.add(transcript(1, "あ" * 70), now=0.0)
    assert len(out) == 1
    assert not mgr.has_pending


def test_held_fragment_is_flushed_after_the_deadline():
    """Nothing a participant said may be silently dropped."""
    mgr = manager()
    mgr.add(transcript(1, "それで、"), now=0.0)
    assert mgr.flush_due(now=1.0) == []
    out = mgr.flush_due(now=2.5)
    assert len(out) == 1 and out[0].source_text == "それで、"
    assert not mgr.has_pending


def test_flush_due_is_idempotent():
    mgr = manager()
    mgr.add(transcript(1, "それで、"), now=0.0)
    assert len(mgr.flush_due(now=9.0)) == 1
    assert mgr.flush_due(now=9.0) == []


def test_flush_all_releases_on_stop():
    mgr = manager()
    mgr.add(transcript(1, "途中で"), now=0.0)
    out = mgr.flush_all(now=0.1)
    assert len(out) == 1 and out[0].source_text == "途中で"


def test_flush_all_with_nothing_pending_is_empty():
    assert manager().flush_all(now=0.0) == []


def test_duplicate_seq_is_suppressed():
    mgr = manager()
    mgr.add(transcript(1, "確認しました。"), now=0.0)
    assert mgr.add(transcript(1, "確認しました。"), now=0.1) == []
    assert mgr.duplicates_suppressed == 1


def test_identical_text_at_different_seqs_is_kept():
    """Three people saying はい are three things that were actually said."""
    mgr = manager(min_translate_chars=1)
    first = mgr.add(transcript(1, "はい。"), now=0.0)
    second = mgr.add(transcript(2, "はい。"), now=5.0)
    third = mgr.add(transcript(3, "はい。"), now=10.0)
    assert len(first) == len(second) == len(third) == 1
    assert mgr.duplicates_suppressed == 0


def test_empty_transcript_produces_nothing():
    mgr = manager()
    assert mgr.add(transcript(1, "   "), now=0.0) == []


def test_segment_ids_are_unique():
    mgr = manager()
    ids = []
    for seq in range(1, 6):
        ids += [s.segment_id for s in mgr.add(transcript(seq, "十分に長い文です。"), now=seq)]
    assert len(set(ids)) == 5


def test_repair_hook_is_applied_to_the_emitted_text():
    """Identifier reconstruction runs on the merged text, not per fragment."""
    settings = SegmentationSettings(min_translate_chars=1)
    mgr = SegmentManager(settings, repair=lambda text: text.replace("A B C", "ABC"))
    out = mgr.add(transcript(1, "チケットA B Cの件です。"), now=0.0)
    assert out[0].source_text == "チケットABCの件です。"


def test_committed_at_is_carried_for_end_to_end_latency():
    mgr = manager()
    item = Transcript(
        item_id="i1", seq=1, text="十分に長い文です。", is_final=True, committed_at=123.0
    )
    out = mgr.add(item, now=130.0)
    assert out[0].committed_at == 123.0
