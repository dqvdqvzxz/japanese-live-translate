"""Rendering a session for export.

Markdown by default because it survives being pasted into a ticket, a wiki or
a chat message, which is where a meeting recap actually goes.

Every export carries the measured statistics and says plainly that the cost is
an estimate — a number a colleague might put in an expense note should not
quietly look like a bill.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from typing import Literal

from .models import Segment, SessionRecord

ExportFormat = Literal["md", "txt", "json", "csv"]

EXTENSIONS: dict[str, str] = {"md": "md", "txt": "txt", "json": "json", "csv": "csv"}
MEDIA_TYPES: dict[str, str] = {
    "md": "text/markdown; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
}


def _local_time(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).astimezone().strftime("%Y-%m-%d %H:%M")


def _duration(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m {secs:02d}s"


def filename(record: SessionRecord, fmt: ExportFormat) -> str:
    stamp = datetime.fromtimestamp(record.started_at, tz=UTC).astimezone()
    return f"meeting-{stamp.strftime('%Y%m%d-%H%M')}.{EXTENSIONS[fmt]}"


def archive_filename(fmt: ExportFormat) -> str:
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M")
    return f"meeting-history-{stamp}.{EXTENSIONS[fmt]}"


def render_all(sessions: list[tuple[SessionRecord, list[Segment]]], fmt: ExportFormat) -> str:
    """Render the whole history as one file.

    Exporting everything is a deliberate act, so it is worth making the result
    self-describing: the header states how many meetings it covers and what
    they cost in total, and repeats that the cost is an estimate.
    """
    if fmt == "json":
        return json.dumps(
            {
                "exported_at": datetime.now().astimezone().isoformat(),
                "sessions": [
                    {
                        "session": record.model_dump(),
                        "segments": [s.model_dump() for s in segments],
                    }
                    for record, segments in sessions
                ],
                "note": "estimated_cost_usd is a projection from published unit "
                "prices, not a billed amount.",
            },
            ensure_ascii=False,
            indent=2,
        )

    if fmt == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["session_id", "started_at", "source", "seq", "japanese", "vietnamese"])
        for record, segments in sessions:
            for segment in segments:
                writer.writerow(
                    [
                        record.session_id,
                        _local_time(record.started_at),
                        record.source_name,
                        segment.seq,
                        segment.source_text,
                        segment.target_text or "",
                    ]
                )
        return buffer.getvalue()

    if fmt == "txt":
        blocks = []
        for record, segments in sessions:
            blocks.append(f"=== {_local_time(record.started_at)} — {record.source_name} ===")
            blocks.append(render(record, segments, "txt"))
        return "\n".join(blocks)

    total_cost = sum(record.estimated_cost_usd for record, _ in sessions)
    total_lines = sum(record.segments_translated for record, _ in sessions)
    out = [
        "# Meeting history",
        "",
        f"- Exported: {_local_time(datetime.now().timestamp())}",
        f"- Meetings: {len(sessions)}",
        f"- Lines translated: {total_lines}",
        f"- Estimated cost: ${total_cost:.3f} "
        "_(projection from published unit prices, not a bill)_",
        "",
        "---",
        "",
    ]
    for record, segments in sessions:
        # Each meeting keeps its own structure, demoted one level so the whole
        # file still has a single top-level heading.
        out.append(
            _markdown(record, segments)
            .replace("\n#", "\n##")
            .replace("# Meeting —", "## Meeting —")
        )
        out.append("---")
        out.append("")
    return "\n".join(out)


def render(record: SessionRecord, segments: list[Segment], fmt: ExportFormat) -> str:
    if fmt == "json":
        return json.dumps(
            {
                "session": record.model_dump(),
                "segments": [s.model_dump() for s in segments],
                "note": "estimated_cost_usd is a projection from published unit "
                "prices, not a billed amount.",
            },
            ensure_ascii=False,
            indent=2,
        )
    if fmt == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["seq", "japanese", "vietnamese"])
        for segment in segments:
            writer.writerow([segment.seq, segment.source_text, segment.target_text or ""])
        return buffer.getvalue()
    if fmt == "txt":
        lines = [f"{s.source_text}\n{s.target_text or ''}\n" for s in segments]
        return "\n".join(lines)
    return _markdown(record, segments)


def _markdown(record: SessionRecord, segments: list[Segment]) -> str:
    out: list[str] = [
        f"# Meeting — {_local_time(record.started_at)}",
        "",
        f"- Source: {record.source_name or 'unknown'}",
        f"- Duration: {_duration(record.duration_s)}",
        f"- Lines translated: {record.segments_translated}"
        + (f" ({record.segments_failed} failed)" if record.segments_failed else ""),
        f"- Tokens: {record.usage.input_tokens} in / {record.usage.output_tokens} out",
        f"- Estimated cost: ${record.estimated_cost_usd:.3f} "
        "_(projection from published unit prices, not a bill)_",
        "",
    ]

    if record.summary:
        out += ["## Tóm tắt", "", record.summary.overview, ""]
        if record.summary.action_items:
            out += ["## Việc cần làm", ""]
            out += [f"- [ ] {item}" for item in record.summary.action_items]
            out += [""]
        out += [
            f"_Summary generated by {record.summary.model or 'the model'} from the "
            "translated transcript._",
            "",
        ]

    out += ["## Nội dung", ""]
    if not segments:
        out += ["_No transcript was recorded for this session._", ""]
    for segment in segments:
        out.append(f"**JA** {segment.source_text}")
        out.append(f"**VI** {segment.target_text or '_(not translated)_'}")
        out.append("")

    return "\n".join(out)
