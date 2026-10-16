"""Glossary and identifier reconstruction.

The repair rules exist because of a measured STT failure, not a hypothetical
one: `チケットABC-1234` came back as `チケットA B Cの千二百三十四` from a live
session (docs/openai-verification.md 3b).
"""

from __future__ import annotations

import pytest

from jlt.models import GlossaryEntry
from jlt.translation.glossary import (
    GlossaryManager,
    int_to_kanji,
    is_identifier,
    normalize_identifiers,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "零"),
        (4, "四"),
        (10, "十"),
        (11, "十一"),
        (34, "三十四"),
        (100, "百"),
        (234, "二百三十四"),
        (1000, "千"),
        (1234, "千二百三十四"),
        (4821, "四千八百二十一"),
        (20260, "二万二百六十"),
    ],
)
def test_int_to_kanji(value: int, expected: str):
    assert int_to_kanji(value) == expected


def test_int_to_kanji_falls_back_to_digits_when_too_large():
    assert int_to_kanji(123_456_789) == "123456789"


@pytest.mark.parametrize("term", ["ABC-1234", "PR4821", "JIRA_42", "X#9"])
def test_identifier_shapes_recognised(term: str):
    assert is_identifier(term)


@pytest.mark.parametrize("term", ["デプロイ", "rollback", "1234", "a-very-long-term"])
def test_non_identifier_shapes_rejected(term: str):
    assert not is_identifier(term)


def test_repairs_the_exact_live_failure_case():
    text = "チケットA B Cの千二百三十四のステータスを確認してください。"
    assert normalize_identifiers(text, ["ABC-1234"]) == (
        "チケットABC-1234のステータスを確認してください。"
    )


def test_repairs_the_partially_improved_form():
    """With keyword hints the number came back as digits but letters stayed split."""
    text = "チケットA B Cの1234のステータス"
    assert "ABC-1234" in normalize_identifiers(text, ["ABC-1234"])


def test_already_correct_text_is_untouched():
    text = "チケットABC-1234です。"
    assert normalize_identifiers(text, ["ABC-1234"]) == text


def test_fullwidth_forms_are_normalised():
    assert normalize_identifiers("ＡＢＣ－１２３４", ["ABC-1234"]) == "ABC-1234"


def test_lowercase_spoken_letters_are_repaired():
    assert normalize_identifiers("abc 1234", ["ABC-1234"]) == "ABC-1234"


def test_unrelated_numbers_are_left_alone():
    """A false repair silently changes which ticket a sentence is about."""
    text = "影響を受けるユーザーは千二百三十四人です。"
    assert normalize_identifiers(text, ["ABC-1234"]) == text


def test_identifiers_not_in_the_glossary_are_never_invented():
    text = "チケットX Y Zの九十九の件"
    assert normalize_identifiers(text, ["ABC-1234"]) == text


def test_empty_inputs_are_safe():
    assert normalize_identifiers("", ["ABC-1234"]) == ""
    assert normalize_identifiers("なにか", []) == "なにか"


def test_manager_exposes_only_identifier_shaped_terms():
    manager = GlossaryManager(
        [
            GlossaryEntry(ja="ABC-1234", vi="ABC-1234"),
            GlossaryEntry(ja="デプロイ", vi="triển khai"),
        ]
    )
    assert manager.identifiers == ["ABC-1234"]


def test_longer_identifiers_are_matched_first():
    manager = GlossaryManager(
        [GlossaryEntry(ja="ABC-1234", vi="x"), GlossaryEntry(ja="ABC-12345", vi="y")]
    )
    assert manager.identifiers[0] == "ABC-12345"


def test_manager_repair_uses_its_own_identifiers():
    manager = GlossaryManager([GlossaryEntry(ja="ABC-1234", vi="ABC-1234")])
    assert manager.repair("A B Cの千二百三十四") == "ABC-1234"


def test_roundtrip_through_disk_preserves_entries(tmp_path):
    path = tmp_path / "glossary.json"
    manager = GlossaryManager([GlossaryEntry(ja="デプロイ", vi="triển khai", note="CI")])
    manager.save(path)
    assert GlossaryManager.load(path).entries == manager.entries


def test_saved_glossary_is_not_world_readable(tmp_path):
    path = tmp_path / "glossary.json"
    GlossaryManager([GlossaryEntry(ja="a", vi="b")]).save(path)
    assert path.stat().st_mode & 0o077 == 0


def test_missing_or_corrupt_file_yields_an_empty_glossary(tmp_path):
    assert GlossaryManager.load(tmp_path / "absent.json").entries == []
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert GlossaryManager.load(bad).entries == []
