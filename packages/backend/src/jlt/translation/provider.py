"""Japanese → Vietnamese translation via the OpenAI Responses API.

Prompt design is driven by what actually goes wrong in Japanese IT meetings:

  * **Negation.** `延期されません` and `延期されます` differ by two characters
    and invert the meaning of a release schedule. This is the single highest
    cost error the system can make, so it is the first instruction.
  * **Identifiers.** Measured to survive STT poorly (openai-verification.md
    §3b). The glossary repairs what it can deterministically before we get
    here; the prompt then forbids touching whatever identifiers remain.
  * **Conditionals and politeness levels**, which carry commitment in Japanese
    meetings — `できます` versus `できると思います` is the difference between a
    promise and a guess.
  * **Context without re-translation.** Previous pairs are supplied as
    reference so pronouns and ellipsis resolve, with an explicit instruction
    that only the target text is to be translated.

`reasoning.effort` is `none`: translation is a transformation, not a reasoning
task, and this is a latency-critical path. Verified that gpt-5.4-mini supports
and defaults to `none`; we send it explicitly so an API default change cannot
quietly slow the product down.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

import httpx

from ..config import RESPONSES_URL, TranslationSettings
from ..logging import get_logger
from ..models import GlossaryEntry, Usage
from ..secrets import Secret

log = get_logger(__name__)

_INSTRUCTIONS = """\
You translate Japanese speech from IT project meetings into Vietnamese.

Rules, in order of importance:
1. Preserve negation exactly. A negated Japanese verb must stay negated in \
Vietnamese, and an affirmative one must stay affirmative.
2. Reproduce every identifier, ticket id and version number exactly as \
written. Never translate, reformat, or re-space them.
3. Keep every numeric value exactly as given, but write units, dates and times \
in Vietnamese. Never leave Japanese characters such as 秒, ミリ秒, 月 or 日 in \
the output, and never change the calendar, the timezone or the value itself.
4. Preserve conditionals and hedging. Keep the difference between a commitment \
and an expectation.
5. Use the glossary below for the listed terms.
6. Keep standard IT English loanwords in English where a Vietnamese engineer \
would, rather than forcing a literal translation.
7. Output only the Vietnamese translation. No explanation, no notes, no \
romanisation, no quotation marks around the whole output.
8. The input is a live transcript and may be a sentence fragment. Translate the \
fragment as given; do not complete it, and do not add content.
"""

_MAX_GLOSSARY_TERMS = 80


class TranslationError(RuntimeError):
    """A translation failed after exhausting retries."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def build_instructions(
    glossary: Sequence[GlossaryEntry], context: Sequence[tuple[str, str]]
) -> str:
    """Instructions, glossary and reference context — everything but the target."""
    parts = [_INSTRUCTIONS]

    if glossary:
        lines = [
            f"- {entry.ja} -> {entry.vi}" + (f"  ({entry.note})" if entry.note else "")
            for entry in list(glossary)[:_MAX_GLOSSARY_TERMS]
        ]
        parts.append("Glossary (Japanese -> Vietnamese):\n" + "\n".join(lines))

    if context:
        lines = [f"JA: {ja}\nVI: {vi}" for ja, vi in context]
        parts.append(
            "Earlier lines from this meeting, for reference only. "
            "They are already translated. Do NOT translate or repeat them; "
            "use them only to resolve pronouns, ellipsis and topic.\n\n" + "\n\n".join(lines)
        )

    return "\n\n".join(parts)


def extract_output_text(body: dict[str, Any]) -> str:
    """Pull the text out of a Responses body.

    The docs warn that output text is not reliably at
    `output[0].content[0].text` — the array can also hold reasoning items and
    tool calls. So we walk the whole structure and concatenate every
    `output_text` part, which is what the SDK's `output_text` helper does.
    """
    chunks: list[str] = []
    for item in body.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []) or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                chunks.append(str(content.get("text", "")))
    return "".join(chunks).strip()


def extract_usage(body: dict[str, Any]) -> Usage:
    raw = body.get("usage") or {}
    return Usage(
        input_tokens=int(raw.get("input_tokens", 0) or 0),
        output_tokens=int(raw.get("output_tokens", 0) or 0),
    )


class OpenAITranslationProvider:
    def __init__(
        self,
        api_key: Secret,
        settings: TranslationSettings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._settings = settings
        self._client = client
        self._owns_client = client is None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._settings.timeout_s)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    def build_payload(
        self,
        source_text: str,
        *,
        context: Sequence[tuple[str, str]] = (),
        glossary: Sequence[GlossaryEntry] = (),
    ) -> dict[str, Any]:
        return {
            "model": self._settings.model,
            "instructions": build_instructions(glossary, context),
            "input": source_text,
            "reasoning": {"effort": self._settings.reasoning_effort},
            # Output is a translation of one utterance; a generous ceiling that
            # still prevents a runaway response on a malformed input.
            "max_output_tokens": 1024,
            # Meeting content must not be retained for later retrieval.
            # See docs/security.md §4.
            "store": False,
        }

    async def translate(
        self,
        source_text: str,
        *,
        context: Sequence[tuple[str, str]] = (),
        glossary: Sequence[GlossaryEntry] = (),
    ) -> tuple[str, Usage]:
        payload = self.build_payload(source_text, context=context, glossary=glossary)
        client = await self._http()
        headers = {"Authorization": f"Bearer {self._api_key.reveal()}"}

        last_error: Exception | None = None
        for attempt in range(self._settings.max_retries + 1):
            try:
                response = await client.post(
                    RESPONSES_URL,
                    headers=headers,
                    json=payload,
                    timeout=self._settings.timeout_s,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                await self._sleep_before_retry(attempt, None)
                continue

            if response.status_code == 200:
                body = response.json()
                text = extract_output_text(body)
                if not text:
                    # A 200 with no text is a failure, not an empty translation.
                    last_error = TranslationError("empty translation", status=200)
                    await self._sleep_before_retry(attempt, None)
                    continue
                return text, extract_usage(body)

            if response.status_code in (408, 409, 429) or response.status_code >= 500:
                last_error = TranslationError(
                    f"HTTP {response.status_code}", status=response.status_code
                )
                await self._sleep_before_retry(attempt, response.headers.get("retry-after"))
                continue

            # 4xx other than the retryable ones means the request is wrong, and
            # repeating it would only waste time and money.
            raise TranslationError(
                f"translation request rejected with HTTP {response.status_code}",
                status=response.status_code,
            )

        raise TranslationError(
            f"translation failed after {self._settings.max_retries + 1} attempts"
        ) from last_error

    async def _sleep_before_retry(self, attempt: int, retry_after: str | None) -> None:
        if attempt >= self._settings.max_retries:
            return
        delay = 0.25 * (2**attempt)
        if retry_after:
            try:
                # Honour the server's own pacing when it gives one.
                delay = max(delay, float(retry_after))
            except ValueError:
                pass
        log.info("retrying translation", extra={"attempt": attempt + 1})
        await asyncio.sleep(delay)
