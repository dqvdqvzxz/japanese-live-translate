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


#: Characters a macOS filename cannot contain, plus control characters.
_UNSAFE = set('/\\:*?"<>|') | {chr(c) for c in range(32)}


def default_session_name(record: SessionRecord) -> str:
    """The name used when the user does not supply one.

    Date first so a folder of exports sorts chronologically, and the source
    after it so two meetings on the same day are still distinguishable.
    """
    stamp = datetime.fromtimestamp(record.started_at, tz=UTC).astimezone()
    source = record.source_name or "Meeting"
    return f"{stamp.strftime('%Y-%m-%d %H%M')} {source}"


def display_name(record: SessionRecord) -> str:
    return record.name.strip() or default_session_name(record)


def safe_filename(name: str) -> str:
    """Turn a user-chosen name into something a filesystem accepts.

    Leading dots are dropped too: a name beginning with one would produce a
    hidden file, and a user who typed it would reasonably think the export had
    failed.
    """
    cleaned = "".join(" " if ch in _UNSAFE else ch for ch in name)
    cleaned = " ".join(cleaned.split()).lstrip(". ")
    return (cleaned or "meeting")[:120]


def filename(record: SessionRecord, fmt: ExportFormat) -> str:
    """A single export is named after the recording itself."""
    return f"{safe_filename(display_name(record))}.{EXTENSIONS[fmt]}"


def archive_folder_name() -> str:
    return f"All History - {datetime.now().astimezone().strftime('%Y%m%d')}"


def archive_filename(fmt: ExportFormat) -> str:
    return f"{archive_folder_name()}.{EXTENSIONS[fmt]}"


def render_bundle(
    sessions: list[tuple[SessionRecord, list[Segment]]], fmt: ExportFormat
) -> dict[str, object]:
    """One file per meeting, for writing into a folder.

    Exporting the whole history as a single blob makes a year of meetings
    unusable; one file each, named after the recording, is what a person can
    actually search and file away.

    Names are deduplicated, because two recordings may share a name — the user
    is free to call them both "Standup".
    """
    folder = archive_folder_name()
    used: set[str] = set()
    files: list[dict[str, str]] = []

    for record, segments in sessions:
        base = safe_filename(display_name(record))
        candidate = f"{base}.{EXTENSIONS[fmt]}"
        counter = 2
        while candidate in used:
            candidate = f"{base} ({counter}).{EXTENSIONS[fmt]}"
            counter += 1
        used.add(candidate)
        files.append({"filename": candidate, "content": render(record, segments, fmt)})

    return {"folder": folder, "files": files}


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
        f"# {display_name(record)}",
        "",
        f"_{_local_time(record.started_at)}_",
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
