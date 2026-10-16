"""Golden dataset integrity, plus the parts checkable without the API.

Translation quality itself needs the live model, so it is evaluated by
`scripts/eval_translation.py` and the results are recorded in
`docs/benchmarks.md`. What is asserted here is everything that must hold
regardless of the model: the dataset is well formed, the cases cover the
priorities the brief names, and the deterministic identifier handling keeps
identifiers intact on the way in.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jlt.translation.glossary import normalize_identifiers

DATA = Path(__file__).parent / "data" / "golden_ja_vi.json"
DATASET = json.loads(DATA.read_text(encoding="utf-8"))
CASES = DATASET["cases"]
REQUIRED_FOCUSES = {"negation", "condition", "identifier", "number", "terminology"}


def test_dataset_loads_and_is_non_trivial():
    assert len(CASES) >= 15


def test_case_ids_are_unique():
    ids = [case["id"] for case in CASES]
    assert len(set(ids)) == len(ids)


def test_every_priority_from_the_brief_is_covered():
    """Negation, conditions, numbers, ticket ids and terminology."""
    assert REQUIRED_FOCUSES <= {case["focus"] for case in CASES}


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_case_is_well_formed(case: dict):
    assert case["ja"].strip()
    assert case["focus"]
    assert case.get("note"), "every case must say why it exists"
    if "polarity" in case:
        assert case["polarity"] in ("negative", "affirmative")


def test_negation_cases_include_both_polarities():
    """A translator that negates everything must fail somewhere in the set."""
    polarities = {case.get("polarity") for case in CASES if case["focus"] == "negation"}
    assert {"negative", "affirmative"} <= polarities


@pytest.mark.parametrize(
    "case",
    [c for c in CASES if "must_contain" in c],
    ids=lambda c: c["id"],
)
def test_expected_tokens_actually_occur_in_the_source(case: dict):
    """A `must_contain` token that is not in the Japanese would be untestable."""
    for token in case["must_contain"]:
        normalized = token.replace("-", "").replace("#", "")
        assert token in case["ja"] or normalized in case["ja"].replace("#", ""), (
            f"{case['id']}: {token!r} does not appear in the source"
        )


@pytest.mark.parametrize(
    "case",
    [c for c in CASES if c["focus"] == "identifier"],
    ids=lambda c: c["id"],
)
def test_identifier_repair_never_damages_a_correct_identifier(case: dict):
    """The repair pass runs on every segment; it must be a no-op when correct."""
    identifiers = case.get("must_contain", [])
    assert normalize_identifiers(case["ja"], identifiers) == case["ja"]


def test_the_live_stt_failure_case_is_present_as_a_regression():
    """The sentence that actually failed in the live smoke test stays in the set."""
    assert any("ABC-1234" in case["ja"] for case in CASES)
