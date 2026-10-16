#!/usr/bin/env python3
"""Live end-to-end smoke test for the Japanese STT lane.

Synthesises Japanese speech with the macOS `say` voice, converts it to the
exact format the API requires (24 kHz mono PCM16), streams it through the real
`OpenAIRealtimeSTTProvider`, and prints the partials and finals that come back.

This exercises the production code path — session configuration, framing,
base64 append, commit, delta accumulation, final normalization — against the
real service. It is not a unit test and is not run by `make test`; it needs a
key and it costs money (a few seconds of audio at $0.017/minute).

    make smoke-stt
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
import time
from pathlib import Path

from jlt.config import FRAME_BYTES, SAMPLE_RATE_HZ, SttSettings
from jlt.secrets import load_api_key
from jlt.stt.openai_realtime import OpenAIRealtimeSTTProvider

SENTENCES = [
    "来週のリリースは延期されません。",
    "チケットABC-1234のステータスを確認してください。",
]


def synthesize(text: str, out_dir: Path, index: int) -> bytes:
    """Japanese speech as 24 kHz mono PCM16 — the only format the model takes."""
    aiff = out_dir / f"s{index}.aiff"
    raw = out_dir / f"s{index}.pcm"
    subprocess.run(  # noqa: S603
        ["/usr/bin/say", "-v", "Kyoko", "-o", str(aiff), text], check=True
    )
    subprocess.run(  # noqa: S603
        [
            "/usr/bin/afconvert",
            "-f",
            "caff",
            "-d",
            f"LEI16@{SAMPLE_RATE_HZ}",
            "-c",
            "1",
            str(aiff),
            str(raw),
        ],
        check=True,
    )
    data = raw.read_bytes()
    # Strip the CAF container: find the `data` chunk payload.
    marker = data.find(b"data")
    return data[marker + 12 :] if marker >= 0 else data


async def main() -> int:
    api_key = load_api_key()
    if api_key is None:
        print("OPENAI_API_KEY is not set.")
        return 2

    provider = OpenAIRealtimeSTTProvider(api_key, SttSettings(delay="low"))
    await provider.connect()
    print(f"connected: {provider.url}\n")

    finals: list[tuple[int, str, float | None]] = []

    async def reader() -> None:
        async for event in provider.events():
            if event.kind == "delta":
                print(f"  partial[{event.seq}] {event.text}")
            elif event.kind == "final":
                latency = f"{event.latency_ms:.0f} ms" if event.latency_ms else "n/a"
                print(f"  FINAL  [{event.seq}] {event.text}   (commit→final {latency})")
                finals.append((event.seq, event.text, event.latency_ms))
            elif event.kind == "error":
                print(f"  ERROR  {event.code}: {event.message}")

    reader_task = asyncio.create_task(reader())

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        for index, sentence in enumerate(SENTENCES, start=1):
            pcm = synthesize(sentence, out_dir, index)
            seconds = len(pcm) / 2 / SAMPLE_RATE_HZ
            print(f"[{index}] sending {seconds:.2f}s: {sentence}")
            started = time.monotonic()
            first_delta_at: float | None = None
            before = provider.stats.deltas

            # Stream in real time so latency numbers mean something.
            for offset in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES):
                await provider.send_audio(pcm[offset : offset + FRAME_BYTES])
                await asyncio.sleep(0.02)
                if first_delta_at is None and provider.stats.deltas > before:
                    first_delta_at = time.monotonic() - started

            await provider.commit(index)
            if first_delta_at is not None:
                print(f"    first partial after {first_delta_at * 1000:.0f} ms of audio")
            await asyncio.sleep(4.0)
            print()

    reader_task.cancel()
    await provider.close()

    print("Summary")
    print(f"  sentences sent : {len(SENTENCES)}")
    print(f"  finals received: {len(finals)}")
    print(f"  seqs           : {[seq for seq, _, _ in finals]}")
    print(f"  duplicates     : {provider.stats.duplicates_suppressed}")
    return 0 if len(finals) == len(SENTENCES) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
