#!/usr/bin/env python3
"""Evaluate the golden translation dataset against the live model.

Translation quality cannot be asserted offline, so this is separate from
`make test` and needs a real key. It prints a per-case verdict and a summary,
and exits non-zero if any case fails.

Checks per case, all mechanical:
  * `must_contain`     — tokens that must appear verbatim (ids, versions, numbers)
  * `must_contain_any` — at least one of these must appear
  * `polarity`         — a negative source must produce a Vietnamese negation,
                         and an affirmative one must not
  * non-empty output

Polarity is checked with a marker-word list, which is a blunt instrument: it
can be fooled by a double negative. It is good enough to catch the failure that
matters — a dropped negation — and the per-case output is printed so a human
can read the translations rather than trusting the verdict alone.

    make eval-translation
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

from jlt.config import TranslationSettings
from jlt.metrics import LatencyTracker
from jlt.models import GlossaryEntry, Usage
from jlt.secrets import load_api_key
from jlt.translation.provider import OpenAITranslationProvider

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "packages/backend/tests/data/golden_ja_vi.json"
GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

#: Hiragana, katakana and CJK ideographs. Vietnamese output containing any of
#: these has left source text untranslated.
_JAPANESE_SCRIPT = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")

GLOSSARY = [
    GlossaryEntry(ja="ABC-1234", vi="ABC-1234", note="ticket id"),
    GlossaryEntry(ja="デプロイ", vi="deploy"),
    GlossaryEntry(ja="ロールバック", vi="rollback"),
    GlossaryEntry(ja="レートリミット", vi="rate limit"),
    GlossaryEntry(ja="ステージング環境", vi="môi trường staging"),
    GlossaryEntry(ja="本番環境", vi="môi trường production"),
]


def check(case: dict, output: str, negation_markers: list[str]) -> list[str]:
    problems: list[str] = []
    lowered = output.lower()

    if not output.strip():
        return ["empty output"]

    for token in case.get("must_contain", []):
        if token not in output:
            problems.append(f"missing {token!r}")

    any_of = case.get("must_contain_any")
    if any_of and not any(token.lower() in lowered for token in any_of):
        problems.append(f"none of {any_of} present")

    if case.get("no_japanese_script"):
        leaked = _JAPANESE_SCRIPT.findall(output)
        if leaked:
            problems.append(f"Japanese script left in output: {''.join(sorted(set(leaked)))}")

    polarity = case.get("polarity")
    if polarity:
        negated = any(marker in lowered for marker in negation_markers)
        if polarity == "negative" and not negated:
            problems.append("negation lost")
        if polarity == "affirmative" and negated:
            problems.append("negation added")

    return problems


async def main() -> int:
    api_key = load_api_key()
    if api_key is None:
        print("OPENAI_API_KEY is not set.")
        return 2

    dataset = json.loads(DATA.read_text(encoding="utf-8"))
    cases = dataset["cases"]
    markers = dataset["negation_markers_vi"]

    settings = TranslationSettings()
    provider = OpenAITranslationProvider(api_key, settings)

    latency = LatencyTracker()
    usage = Usage()
    failures: list[tuple[str, list[str]]] = []

    print(f"Evaluating {len(cases)} golden cases against {settings.model}")
    print(f"reasoning.effort={settings.reasoning_effort}\n")

    # Serial, with rolling context, so this mirrors how the pipeline actually
    # calls the model rather than measuring an unrealistic parallel best case.
    context: list[tuple[str, str]] = []
    for case in cases:
        started = time.monotonic()
        try:
            output, case_usage = await provider.translate(
                case["ja"], context=context[-settings.context_size :], glossary=GLOSSARY
            )
        except Exception as exc:
            print(f"{RED}FAIL{RESET} {case['id']}  request error: {exc}")
            failures.append((case["id"], [f"request error: {type(exc).__name__}"]))
            continue

        elapsed_ms = (time.monotonic() - started) * 1000.0
        latency.record_ms(elapsed_ms)
        usage = usage + case_usage
        context.append((case["ja"], output))

        problems = check(case, output, markers)
        status = f"{GREEN}PASS{RESET}" if not problems else f"{RED}FAIL{RESET}"
        print(f"{status} {case['id']:<8} [{case['focus']}] {elapsed_ms:>6.0f} ms")
        print(f"     JA: {case['ja']}")
        print(f"     VI: {output}")
        if problems:
            print(f"     {RED}{'; '.join(problems)}{RESET}")
            failures.append((case["id"], problems))
        print()

    await provider.aclose()

    summary = latency.summary()
    print("Summary")
    print(f"  cases        : {len(cases)}")
    print(f"  passed       : {len(cases) - len(failures)}")
    print(f"  failed       : {len(failures)}")
    if summary.n:
        print(f"  latency p50  : {summary.p50:.0f} ms")
        print(f"  latency p95  : {summary.p95:.0f} ms")
    print(f"  tokens       : in={usage.input_tokens} out={usage.output_tokens}")
    if failures:
        print(f"\n{RED}Failures:{RESET}")
        for case_id, problems in failures:
            print(f"  {case_id}: {'; '.join(problems)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
