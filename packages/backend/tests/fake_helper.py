#!/usr/bin/env python3
"""A stand-in for the Swift `teams-audio-tap`, speaking the same protocol.

Lets the supervisor logic (docs/api-design.md §2) be tested on any machine,
without ScreenCaptureKit, Screen Recording permission, or a running Teams.
The Swift binary remains the thing that must be verified against real audio.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time

FRAME_BYTES = 960


def emit(**payload: object) -> None:
    sys.stderr.write(json.dumps(payload, sort_keys=True) + "\n")
    sys.stderr.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list-sources", action="store_true")
    parser.add_argument("--capture")
    parser.add_argument("--rate", default="24000")
    parser.add_argument("--channels", default="1")
    parser.add_argument("--frame-ms", default="20")
    # Mirrors the real helper: a fake that rejects a flag the app actually
    # sends would fail for a reason the app never would.
    parser.add_argument("--mic", action="store_true")
    args = parser.parse_args()

    mode = os.environ.get("FAKE_HELPER_MODE", "ok")

    if mode == "permission_denied":
        emit(type="error", code="permission_denied", message="Screen Recording permission required")
        return 2
    if mode == "assert_no_key" and os.environ.get("OPENAI_API_KEY"):
        emit(type="error", code="leaked_key", message="OPENAI_API_KEY reached the audio helper")
        return 1
    if mode == "source_not_found":
        emit(type="error", code="source_not_found", message="No such application")
        return 3

    if args.list_sources:
        sys.stdout.write(
            json.dumps(
                {
                    "sources": [
                        {
                            "id": "app:com.microsoft.teams2",
                            "kind": "application",
                            "name": "Microsoft Teams",
                            "bundle_id": "com.microsoft.teams2",
                            "pid": 4821,
                            "is_recommended": True,
                        },
                        {
                            "id": "display:1",
                            "kind": "display",
                            "name": "Entire system audio (display 1)",
                            "bundle_id": None,
                            "pid": None,
                            "is_recommended": False,
                        },
                    ]
                }
            )
            + "\n"
        )
        return 0

    if not args.capture:
        return 64

    frame_bytes = FRAME_BYTES if mode != "frame_mismatch" else 1024
    emit(type="ready", rate=24000, channels=1, frame_bytes=frame_bytes)
    if mode == "never_starts":
        time.sleep(60)
        return 1
    emit(type="started", source_id=args.capture, name="Microsoft Teams", mic=args.mic)

    stop = False

    def handle(_signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle)
    signal.signal(signal.SIGTERM, handle)

    frames = 0
    if mode == "ignores_sigint":
        while True:
            time.sleep(0.05)

    while not stop:
        sys.stdout.buffer.write(b"\x00" * FRAME_BYTES)
        sys.stdout.buffer.flush()
        frames += 1
        if frames % 50 == 0:
            emit(type="level", rms_dbfs=-28.4)
        time.sleep(0.002)

    emit(type="stopped", frames=frames, dropped=0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
