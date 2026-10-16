#!/usr/bin/env python3
"""Probe the live OpenAI API and report what actually works.

This script is the *only* source of truth about runtime API behaviour. It makes
real calls and prints real results; it never assumes, and it exits non-zero when
something the application depends on is unavailable.

It settles the one question the published docs leave open: which WebSocket URL
accepts a `gpt-live-transcribe` transcription session
(see docs/openai-verification.md §2).

    OPENAI_API_KEY=sk-... python3 scripts/verify_openai.py

Checks:
  1. the API key authenticates;
  2. `gpt-live-transcribe` and `gpt-5.4-mini` are visible to the project;
  3. which realtime URL candidate accepts a transcription `session.update`;
  4. a real JA→VI translation round-trip through the Responses API.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages", "backend", "src"))

from jlt.config import (
    REALTIME_URL_CANDIDATES,
    RESPONSES_URL,
    SAMPLE_RATE_HZ,
    STT_MODEL,
    TRANSLATION_MODEL,
)

GREEN, RED, YELLOW, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[0m"


def ok(message: str) -> None:
    print(f"{GREEN}  PASS{RESET} {message}")


def fail(message: str) -> None:
    print(f"{RED}  FAIL{RESET} {message}")


def warn(message: str) -> None:
    print(f"{YELLOW}  WARN{RESET} {message}")


async def check_models(api_key: str) -> bool:
    import httpx

    print("\n[1/3] Model availability")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
    if response.status_code == 401:
        fail("the API key was rejected (401). Check OPENAI_API_KEY.")
        return False
    if response.status_code != 200:
        fail(f"GET /v1/models returned {response.status_code}")
        return False

    available = {item["id"] for item in response.json().get("data", [])}
    all_present = True
    for model in (STT_MODEL, TRANSLATION_MODEL):
        if model in available:
            ok(f"{model} is available to this project")
        else:
            fail(
                f"{model} is NOT listed for this project. "
                "The application cannot work without it; request access or pick "
                "a verified alternative (see docs/openai-verification.md)."
            )
            all_present = False
    return all_present


async def check_realtime_url(api_key: str) -> str | None:
    """Try each documented candidate and report which accepts a session."""
    import websockets

    print("\n[2/3] Realtime transcription WebSocket URL")
    session_update = {
        "type": "session.update",
        "session": {
            "type": "transcription",
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": SAMPLE_RATE_HZ},
                    "transcription": {"model": STT_MODEL, "languages": ["ja"]},
                    "turn_detection": None,
                }
            },
        },
    }

    for url in REALTIME_URL_CANDIDATES:
        print(f"  trying {url}")
        try:
            async with websockets.connect(
                url, additional_headers={"Authorization": f"Bearer {api_key}"}
            ) as socket:
                await socket.send(json.dumps(session_update))
                # Read a few events; the server either confirms the session or
                # sends an error describing exactly what it disliked.
                for _ in range(5):
                    raw = await asyncio.wait_for(socket.recv(), timeout=10)
                    event = json.loads(raw)
                    kind = event.get("type", "")
                    if kind.startswith("session."):
                        ok(f"accepted a transcription session at {url}")
                        print(f"       server replied: {kind}")
                        return url
                    if kind == "error":
                        fail(f"server error: {event.get('error', event)}")
                        break
        except Exception as exc:
            fail(f"{type(exc).__name__}: {exc}")

    fail(
        "No candidate URL accepted a transcription session. Set "
        "JLT_OPENAI_REALTIME_URL once you know the correct endpoint."
    )
    return None


async def check_translation(api_key: str) -> bool:
    import httpx

    print("\n[3/3] Responses API translation round-trip")
    source = "来週のリリースは延期されません。チケットABC-1234を確認してください。"
    payload = {
        "model": TRANSLATION_MODEL,
        "instructions": (
            "Translate the Japanese text to Vietnamese. Preserve negation and "
            "identifiers exactly. Output only the translation."
        ),
        "input": source,
        "reasoning": {"effort": "none"},
        "max_output_tokens": 300,
        "store": False,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            RESPONSES_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
    if response.status_code != 200:
        fail(f"POST /v1/responses returned {response.status_code}: {response.text[:400]}")
        return False

    body = response.json()
    # Per the docs, text is not reliably at output[0].content[0].text.
    chunks = [
        content.get("text", "")
        for item in body.get("output", [])
        for content in item.get("content", [])
        if content.get("type") == "output_text"
    ]
    text = "".join(chunks).strip()
    if not text:
        fail("the response contained no output_text")
        return False

    ok("translation returned")
    print(f"       JA: {source}")
    print(f"       VI: {text}")
    if "ABC-1234" in text:
        ok("the ticket identifier survived translation")
    else:
        warn("the ticket identifier was not preserved verbatim; tighten the prompt")

    usage = body.get("usage", {})
    print(f"       usage: {usage}")
    return True


async def main() -> int:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("OPENAI_API_KEY is not set. Export it and re-run.")
        return 2

    print("Verifying the OpenAI API against what this application requires.")
    print("Nothing below is assumed; every line is the result of a real call.")

    models_ok = await check_models(api_key)
    url = await check_realtime_url(api_key)
    translation_ok = await check_translation(api_key)

    print("\nSummary")
    print(f"  models:      {'ok' if models_ok else 'FAILED'}")
    print(f"  realtime ws: {url or 'UNRESOLVED'}")
    print(f"  translation: {'ok' if translation_ok else 'FAILED'}")
    if url:
        print(f"\nPin the URL with:\n  export JLT_OPENAI_REALTIME_URL='{url}'")

    return 0 if (models_ok and url and translation_ok) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
