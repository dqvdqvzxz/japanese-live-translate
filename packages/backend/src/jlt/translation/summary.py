"""Post-meeting summary and action items.

Runs once, after a session ends, over the Vietnamese translations. It is a
separate concern from live translation: here latency does not matter and
reading the whole meeting at once does, so this is the one place the project
spends reasoning effort.

The output is grounded deliberately hard. A meeting recap that invents a
decision nobody made, or an action item nobody agreed to, is worse than no
recap at all — somebody will act on it. The prompt forbids inference and the
model is told to return nothing rather than guess.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from ..config import RESPONSES_URL, TranslationSettings
from ..logging import get_logger
from ..models import Segment, SessionSummary
from ..secrets import Secret
from .provider import TranslationError, extract_output_text

log = get_logger(__name__)

#: Long meetings can exceed a comfortable prompt. Summarising the most recent
#: portion is better than failing, and the truncation is disclosed in the text.
MAX_CHARS = 60_000

_INSTRUCTIONS = """\
You are summarising a transcript of a Japanese IT project meeting that has \
already been translated into Vietnamese.

Write the summary in Vietnamese.

Return JSON only, with exactly this shape:
{"overview": "...", "action_items": ["...", "..."]}

Rules:
1. Ground everything in the transcript. Do not infer, extrapolate, or add \
context that is not there. A summary that invents a decision is worse than a \
short one.
2. "overview" is a short paragraph: what the meeting was about and what was \
decided. If nothing was decided, say so.
3. "action_items" are concrete things a person has to do, as actually stated. \
Include who is responsible and any deadline when the transcript gives them. \
Keep identifiers, ticket ids and dates exactly as written.
4. If the transcript contains no action items, return an empty list. Do not \
manufacture one.
5. If the transcript is too fragmentary to summarise, say that in "overview" \
and return an empty list.
"""


def build_transcript(segments: list[Segment]) -> tuple[str, bool]:
    """Render the meeting for the model. Returns the text and whether it was cut."""
    lines = [
        f"{segment.target_text}"
        for segment in segments
        if segment.target_text and segment.target_text.strip()
    ]
    text = "\n".join(lines)
    if len(text) <= MAX_CHARS:
        return text, False
    # Keep the end: later discussion is where decisions and actions land.
    return text[-MAX_CHARS:], True


def parse_summary(raw: str) -> tuple[str, list[str]]:
    """Pull overview and action items out of the model's reply.

    The model is asked for JSON, but a model asked for JSON sometimes wraps it
    in a fence or adds a sentence. Falling back to the raw text keeps a usable
    summary instead of throwing away a good answer over formatting.
    """
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            payload = json.loads(cleaned[start : end + 1])
            overview = str(payload.get("overview", "")).strip()
            items = [
                str(item).strip() for item in payload.get("action_items", []) if str(item).strip()
            ]
            if overview:
                return overview, items
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass

    return cleaned, []


class SummaryProvider:
    def __init__(
        self,
        api_key: Secret,
        settings: TranslationSettings,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        self._api_key = api_key
        self._settings = settings
        self._client = client
        self._owns_client = client is None
        self._timeout_s = timeout_s

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout_s)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    def build_payload(self, transcript: str, truncated: bool) -> dict[str, Any]:
        note = "\n\n(Note: only the later part of a long meeting is included.)" if truncated else ""
        return {
            "model": self._settings.model,
            "instructions": _INSTRUCTIONS,
            "input": transcript + note,
            # Unlike live translation, this is a reading-comprehension task over
            # a whole meeting and runs once, so it is worth the thinking time.
            "reasoning": {"effort": "low"},
            "max_output_tokens": 4096,
            "store": False,
        }

    async def summarize(self, segments: list[Segment]) -> SessionSummary:
        transcript, truncated = build_transcript(segments)
        if not transcript.strip():
            return SessionSummary(
                overview="Phiên này không có nội dung nào được dịch.",
                action_items=[],
                generated_at=time.time(),
                model=self._settings.model,
            )

        client = await self._http()
        response = await client.post(
            RESPONSES_URL,
            headers={"Authorization": f"Bearer {self._api_key.reveal()}"},
            json=self.build_payload(transcript, truncated),
            timeout=self._timeout_s,
        )
        if response.status_code != 200:
            raise TranslationError(
                f"summary request failed with HTTP {response.status_code}",
                status=response.status_code,
            )

        text = extract_output_text(response.json())
        if not text:
            raise TranslationError("the summary response contained no text", status=200)

        overview, items = parse_summary(text)
        log.info(
            "summary generated",
            extra={"segments": len(segments), "action_items": len(items)},
        )
        return SessionSummary(
            overview=overview,
            action_items=items,
            generated_at=time.time(),
            model=self._settings.model,
        )
