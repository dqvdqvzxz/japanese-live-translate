"""Post-meeting summary generation."""

from __future__ import annotations

import httpx
import pytest

from jlt.config import TranslationSettings
from jlt.models import Segment
from jlt.secrets import Secret
from jlt.translation.provider import TranslationError
from jlt.translation.summary import (
    MAX_CHARS,
    SummaryProvider,
    build_transcript,
    parse_summary,
)

KEY = Secret("sk-test-0000000000000000")


def segment(seq: int, vi: str | None) -> Segment:
    return Segment(segment_id=f"seg_{seq}", seq=seq, source_text=f"JA{seq}", target_text=vi)


def provider(handler) -> SummaryProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SummaryProvider(KEY, TranslationSettings(), client=client)


def ok(text: str) -> httpx.Response:
    return httpx.Response(
        200, json={"output": [{"content": [{"type": "output_text", "text": text}]}]}
    )


# --- transcript assembly ----------------------------------------------------


def test_only_translated_lines_are_summarised():
    text, truncated = build_transcript([segment(1, "Một"), segment(2, None), segment(3, "Ba")])
    assert text == "Một\nBa"
    assert not truncated


def test_a_long_meeting_keeps_its_ending():
    """Decisions and actions land late, so the tail is what matters."""
    segments = [segment(i, "x" * 1000) for i in range(100)]
    text, truncated = build_transcript(segments)
    assert truncated
    assert len(text) <= MAX_CHARS


# --- parsing ----------------------------------------------------------------


def test_clean_json_is_parsed():
    overview, items = parse_summary('{"overview": "Tóm tắt", "action_items": ["A", "B"]}')
    assert overview == "Tóm tắt"
    assert items == ["A", "B"]


def test_json_in_a_code_fence_is_parsed():
    """Models asked for JSON often fence it anyway; that is not a failure."""
    raw = '```json\n{"overview": "Tóm tắt", "action_items": ["A"]}\n```'
    overview, items = parse_summary(raw)
    assert overview == "Tóm tắt" and items == ["A"]


def test_json_with_surrounding_prose_is_recovered():
    raw = 'Here you go:\n{"overview": "Tóm tắt", "action_items": []}\nHope that helps.'
    overview, items = parse_summary(raw)
    assert overview == "Tóm tắt" and items == []


def test_plain_text_is_kept_rather_than_discarded():
    """A good summary in the wrong shape is still a good summary."""
    overview, items = parse_summary("Cuộc họp bàn về release.")
    assert overview == "Cuộc họp bàn về release."
    assert items == []


def test_blank_action_items_are_dropped():
    _, items = parse_summary('{"overview": "x", "action_items": ["A", "", "  "]}')
    assert items == ["A"]


# --- requests ---------------------------------------------------------------


async def test_summary_is_generated_and_labelled():
    prov = provider(lambda r: ok('{"overview": "Chốt release.", "action_items": ["Sửa ABC-1234"]}'))
    summary = await prov.summarize([segment(1, "Một")])
    assert summary.overview == "Chốt release."
    assert summary.action_items == ["Sửa ABC-1234"]
    assert summary.model == "gpt-5.4-mini"
    assert summary.generated_at > 0


async def test_an_empty_transcript_does_not_call_the_api():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return ok("{}")

    summary = await provider(handler).summarize([segment(1, None)])
    assert calls["n"] == 0
    assert summary.action_items == []
    assert "không có nội dung" in summary.overview


async def test_the_prompt_forbids_inventing_content():
    """A recap that invents a decision is worse than a short one."""
    payload = provider(lambda r: ok("{}")).build_payload("abc", False)
    instructions = payload["instructions"].lower()
    assert "do not infer" in instructions
    assert "do not manufacture" in instructions


async def test_summaries_are_not_retained_by_openai():
    payload = provider(lambda r: ok("{}")).build_payload("abc", False)
    assert payload["store"] is False


async def test_summary_uses_reasoning_unlike_live_translation():
    """Latency does not matter here, and reading a whole meeting does."""
    payload = provider(lambda r: ok("{}")).build_payload("abc", False)
    assert payload["reasoning"]["effort"] == "low"


async def test_truncation_is_disclosed_to_the_model():
    payload = provider(lambda r: ok("{}")).build_payload("abc", True)
    assert "later part" in payload["input"]


async def test_an_api_failure_raises():
    with pytest.raises(TranslationError):
        await provider(lambda r: httpx.Response(500)).summarize([segment(1, "Một")])


async def test_an_empty_response_raises_rather_than_storing_nothing():
    with pytest.raises(TranslationError):
        await provider(lambda r: httpx.Response(200, json={"output": []})).summarize(
            [segment(1, "Một")]
        )
