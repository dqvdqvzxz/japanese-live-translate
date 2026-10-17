"""User-facing settings and immutable runtime constants.

Everything here is non-secret. The OpenAI API key lives in the macOS Keychain
and reaches this process only through the environment; see `jlt.secrets`.
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# --- Constants dictated by the OpenAI API, not by preference -----------------
# Verified against the official docs; see docs/openai-verification.md.
# Changing any of these breaks transcription.

SAMPLE_RATE_HZ = 24_000
"""The only sample rate `gpt-live-transcribe` accepts."""

CHANNELS = 1
BYTES_PER_SAMPLE = 2
FRAME_MS = 20
FRAME_BYTES = SAMPLE_RATE_HZ * BYTES_PER_SAMPLE * FRAME_MS // 1000  # 960

STT_MODEL = "gpt-live-transcribe"
TRANSLATION_MODEL = "gpt-5.4-mini"

RESPONSES_URL = "https://api.openai.com/v1/responses"

#: The transcription WebSocket URL is the one item the public docs do not pin
#: down (see docs/openai-verification.md §2), so it was settled empirically.
#:
#: Verified 2026-10-16 by `scripts/verify_openai.py`: the first URL below was
#: accepted and the server replied `session.created`. The second is kept as a
#: fallback because the docs show `?model=` as the general Realtime pattern,
#: and a project without the `intent` form should still connect.
REALTIME_URL_CANDIDATES: tuple[str, ...] = (
    "wss://api.openai.com/v1/realtime?intent=transcription",
    f"wss://api.openai.com/v1/realtime?model={STT_MODEL}",
)

# Published unit prices, USD. Source: developers.openai.com/api/docs/pricing,
# retrieved 2026-10-16. Used only for an *estimate* shown next to measured usage.
PRICE_STT_PER_MINUTE = 0.017
PRICE_TRANSLATION_INPUT_PER_1M = 0.75
PRICE_TRANSLATION_OUTPUT_PER_1M = 4.50


SttDelay = Literal["minimal", "low", "medium", "high", "xhigh"]
ReasoningEffort = Literal["none", "low", "medium", "high", "xhigh"]
Theme = Literal["system", "light", "dark"]


class SttSettings(BaseModel):
    delay: SttDelay = "low"
    #: `gpt-live-transcribe` uses the plural field. Sending `language` as well
    #: is rejected by the API, so we never construct one.
    languages: list[str] = Field(default_factory=lambda: ["ja"])
    prompt: str | None = None

    @field_validator("languages")
    @classmethod
    def _non_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("at least one language code is required")
        return value


class TranslationSettings(BaseModel):
    model: str = TRANSLATION_MODEL
    #: `gpt-5.4-mini` supports `none` and defaults to it, but we send it
    #: explicitly so a change to the API default cannot slow us down silently.
    reasoning_effort: ReasoningEffort = "none"
    context_size: int = Field(default=3, ge=0, le=10)
    timeout_s: float = Field(default=12.0, gt=0)
    max_retries: int = Field(default=2, ge=0, le=5)
    concurrency: int = Field(default=3, ge=1, le=8)
    max_queue: int = Field(default=64, ge=1)
    #: How long the reorder buffer waits for a missing earlier segment before
    #: releasing later ones anyway. Prevents permanent head-of-line blocking.
    reorder_stall_s: float = Field(default=6.0, gt=0)


class SegmentationSettings(BaseModel):
    #: Finals shorter than this are merged with the next one rather than
    #: translated alone — a two-word interjection makes poor Vietnamese.
    min_translate_chars: int = Field(default=8, ge=1)
    merge_window_ms: int = Field(default=1200, ge=0)
    max_merge_chars: int = Field(default=220, ge=20)
    #: A held-back fragment is flushed anyway after this long so nothing is lost.
    incomplete_flush_ms: int = Field(default=2500, ge=100)


class VadSettings(BaseModel):
    """Client-side VAD parameters.

    This is not tuning for its own sake: `gpt-live-transcribe` supports neither
    `server_vad` nor `semantic_vad` in a transcription session, so without this
    detector no `input_audio_buffer.commit` is ever sent and no final transcript
    is ever produced.
    """

    energy_threshold_dbfs: float = Field(default=-45.0, le=0)
    hangover_ms: int = Field(default=600, ge=100)
    min_speech_ms: int = Field(default=300, ge=50)
    #: A presenter who never pauses would otherwise never produce a final.
    max_turn_ms: int = Field(default=12_000, ge=1000)


class AudioSettings(BaseModel):
    """What the capture helper listens to.

    The microphone is off by default. It records the user and everyone in the
    room, which is a different consent question from capturing an application
    the user is already listening to — so it is a deliberate choice, surfaced
    next to the source picker rather than buried.
    """

    include_microphone: bool = False


class UiSettings(BaseModel):
    theme: Theme = "system"
    font_size: int = Field(default=18, ge=10, le=48)
    always_on_top: bool = True
    show_partials: bool = True


class PrivacySettings(BaseModel):
    persist_transcripts: bool = False
    #: Where the readable transcript files go. When unset, only the internal
    #: history database is written. The user picks this explicitly, so the
    #: meeting content lands somewhere they chose rather than somewhere the
    #: app decided.
    transcript_folder: str | None = None
    persist_audio: bool = False  # kept False; no code path writes audio

    @field_validator("persist_audio")
    @classmethod
    def _audio_never_persisted(cls, value: bool) -> bool:
        if value:
            raise ValueError(
                "audio persistence is not implemented and will not be enabled by config"
            )
        return value


class Settings(BaseModel):
    audio: AudioSettings = Field(default_factory=AudioSettings)
    stt: SttSettings = Field(default_factory=SttSettings)
    translation: TranslationSettings = Field(default_factory=TranslationSettings)
    segmentation: SegmentationSettings = Field(default_factory=SegmentationSettings)
    vad: VadSettings = Field(default_factory=VadSettings)
    ui: UiSettings = Field(default_factory=UiSettings)
    privacy: PrivacySettings = Field(default_factory=PrivacySettings)

    #: Read-only mirror of Keychain state. The key itself is never serialised.
    api_key_present: bool = False


def realtime_url_candidates() -> tuple[str, ...]:
    """Candidate transcription WebSocket URLs, most-likely first.

    `JLT_OPENAI_REALTIME_URL` overrides the list entirely, which is how an
    operator pins the URL once `scripts/verify_openai.py` has settled it.
    """
    override = os.environ.get("JLT_OPENAI_REALTIME_URL")
    if override:
        return (override,)
    return REALTIME_URL_CANDIDATES
