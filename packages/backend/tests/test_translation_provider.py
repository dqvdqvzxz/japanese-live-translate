"""Responses API request construction and error handling."""

from __future__ import annotations

import httpx
import pytest

from jlt.config import TranslationSettings
from jlt.models import GlossaryEntry
from jlt.secrets import Secret
from jlt.translation.provider import (
    OpenAITranslationProvider,
    TranslationError,
    build_instructions,
    extract_output_text,
    extract_usage,
)

KEY = Secret("sk-test-0000000000000000")


def provider(handler, **overrides) -> OpenAITranslationProvider:
    settings = TranslationSettings(**{"max_retries": 1, "timeout_s": 2.0, **overrides})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAITranslationProvider(KEY, settings, client=client)


def ok_response(text: str = "Bản dịch", **extra):
    body = {
        "output": [{"content": [{"type": "output_text", "text": text}]}],
        "usage": {"input_tokens": 10, "output_tokens": 4},
        **extra,
    }
    return httpx.Response(200, json=body)


# --- payload ---------------------------------------------------------------


def test_payload_targets_the_responses_endpoint_fields():
    payload = provider(lambda r: ok_response()).build_payload("テスト")
    assert payload["model"] == "gpt-5.4-mini"
    assert payload["input"] == "テスト"
    assert "instructions" in payload


def test_reasoning_effort_is_sent_explicitly():
    """Latency-critical path; an API default change must not slow it down."""
    payload = provider(lambda r: ok_response()).build_payload("テスト")
    assert payload["reasoning"] == {"effort": "none"}


def test_store_is_false_so_meeting_content_is_not_retained():
    payload = provider(lambda r: ok_response()).build_payload("テスト")
    assert payload["store"] is False


def test_instructions_lead_with_negation():
    instructions = build_instructions([], [])
    first_rule = instructions.split("1.")[1].split("2.")[0]
    assert "negation" in first_rule.lower()


def test_glossary_appears_in_the_instructions():
    instructions = build_instructions([GlossaryEntry(ja="デプロイ", vi="triển khai")], [])
    assert "デプロイ -> triển khai" in instructions


def test_context_is_marked_reference_only():
    instructions = build_instructions([], [("前の文。", "Câu trước.")])
    assert "前の文。" in instructions
    assert "Do NOT translate" in instructions


def test_no_context_section_when_there_is_no_context():
    assert "reference only" not in build_instructions([], [])


def test_glossary_is_capped_so_the_prompt_cannot_grow_without_bound():
    entries = [GlossaryEntry(ja=f"用語{i}", vi=f"t{i}") for i in range(500)]
    instructions = build_instructions(entries, [])
    # Count entry lines only; the section header also contains " -> ".
    entry_lines = [ln for ln in instructions.splitlines() if ln.startswith("- ")]
    assert len(entry_lines) == 80


# --- response parsing ------------------------------------------------------


def test_output_text_is_gathered_from_the_whole_output_array():
    """The docs warn text is not reliably at output[0].content[0].text."""
    body = {
        "output": [
            {"type": "reasoning", "content": []},
            {"content": [{"type": "output_text", "text": "Xin "}]},
            {"content": [{"type": "output_text", "text": "chào"}]},
        ]
    }
    assert extract_output_text(body) == "Xin chào"


def test_non_text_content_parts_are_ignored():
    body = {"output": [{"content": [{"type": "refusal", "refusal": "no"}]}]}
    assert extract_output_text(body) == ""


def test_usage_defaults_to_zero_when_absent():
    assert extract_usage({}) == extract_usage({"usage": {}})


# --- request behaviour -----------------------------------------------------


async def test_successful_translation_returns_text_and_usage():
    prov = provider(lambda r: ok_response("Bản phát hành"))
    text, usage = await prov.translate("来週のリリース")
    assert text == "Bản phát hành"
    assert usage.input_tokens == 10


async def test_api_key_is_sent_as_a_bearer_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return ok_response()

    await provider(handler).translate("テスト")
    assert seen["auth"] == f"Bearer {KEY.reveal()}"


async def test_server_errors_are_retried():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500) if calls["n"] == 1 else ok_response("ok")

    text, _ = await provider(handler).translate("テスト")
    assert text == "ok"
    assert calls["n"] == 2


async def test_rate_limits_are_retried():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return ok_response("ok")

    text, _ = await provider(handler).translate("テスト")
    assert text == "ok"


async def test_client_errors_are_not_retried():
    """Repeating a malformed request only wastes time and money."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error": {"message": "bad"}})

    with pytest.raises(TranslationError) as excinfo:
        await provider(handler).translate("テスト")
    assert excinfo.value.status == 400
    assert calls["n"] == 1


async def test_retries_are_bounded_and_then_raise():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503)

    with pytest.raises(TranslationError):
        await provider(handler, max_retries=2).translate("テスト")
    assert calls["n"] == 3


async def test_a_200_with_no_text_is_treated_as_a_failure():
    """An empty string is not a translation; silently returning it loses a line."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"output": []})

    with pytest.raises(TranslationError):
        await provider(handler, max_retries=0).translate("テスト")


async def test_transport_errors_are_retried_then_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    with pytest.raises(TranslationError):
        await provider(handler, max_retries=1).translate("テスト")
