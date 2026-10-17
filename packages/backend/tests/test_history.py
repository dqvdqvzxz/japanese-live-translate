"""Session history: persistence, listing, export and summarisation."""

from __future__ import annotations

import pytest

from jlt.export import render
from jlt.history import HistoryManager
from jlt.models import Segment, SessionRecord, SessionSummary, Usage


def segment(seq: int, ja: str = "日本語", vi: str | None = "Tiếng Việt") -> Segment:
    return Segment(
        segment_id=f"seg_{seq}",
        seq=seq,
        source_text=f"{ja}{seq}",
        target_text=f"{vi}{seq}" if vi else None,
        created_at=float(seq),
        state="translated" if vi else "failed",
    )


def record(session_id: str, started_at: float, cost: float = 0.5) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        started_at=started_at,
        ended_at=started_at + 600,
        duration_s=600.0,
        source_name="Microsoft Teams",
        segments_translated=3,
        segments_failed=0,
        audio_seconds_sent=500.0,
        usage=Usage(input_tokens=100, output_tokens=50),
        estimated_cost_usd=cost,
    )


def persistent(tmp_path) -> HistoryManager:
    return HistoryManager(persist=True, db_path=tmp_path / "history.sqlite3")


# --- the default: nothing on disk ------------------------------------------


def test_nothing_is_written_to_disk_by_default(tmp_path):
    """Meeting content is the most sensitive thing here; saving stays opt-in."""
    manager = HistoryManager()
    manager.begin_session("s_1", "Teams", 1000.0)
    manager.add("s_1", segment(1))
    assert not manager.persisting
    assert list(tmp_path.iterdir()) == []


def test_without_persistence_history_holds_only_the_current_session(tmp_path):
    manager = HistoryManager()
    manager.begin_session("s_1", "Teams", 1000.0)
    manager.add("s_1", segment(1))
    assert [r.session_id for r in manager.list_sessions()] == ["s_1"]

    manager.begin_session("s_2", "Teams", 2000.0)
    assert [r.session_id for r in manager.list_sessions()] == ["s_2"]


# --- persistence ------------------------------------------------------------


def test_sessions_survive_a_restart(tmp_path):
    path = tmp_path / "history.sqlite3"
    first = HistoryManager(persist=True, db_path=path)
    first.begin_session("s_1", "Teams", 1000.0)
    first.add("s_1", segment(1))
    first.end_session(record("s_1", 1000.0))
    first.close()

    second = HistoryManager(persist=True, db_path=path)
    sessions = second.list_sessions()
    assert [r.session_id for r in sessions] == ["s_1"]
    assert sessions[0].estimated_cost_usd == 0.5
    assert [s.source_text for s in second.session_segments("s_1")] == ["日本語1"]


def test_sessions_are_listed_newest_first(tmp_path):
    manager = persistent(tmp_path)
    for index, started in enumerate([1000.0, 3000.0, 2000.0], start=1):
        manager.begin_session(f"s_{index}", "Teams", started)
        manager.end_session(record(f"s_{index}", started))
    assert [r.started_at for r in manager.list_sessions()] == [3000.0, 2000.0, 1000.0]


def test_cost_and_usage_are_stored_for_the_list(tmp_path):
    manager = persistent(tmp_path)
    # The source name is recorded at the start, from the capture source, so it
    # is the same string both times in production.
    manager.begin_session("s_1", "Microsoft Teams", 1000.0)
    manager.end_session(record("s_1", 1000.0, cost=1.23))
    listed = manager.list_sessions()[0]
    assert listed.estimated_cost_usd == 1.23
    assert listed.usage.input_tokens == 100
    assert listed.source_name == "Microsoft Teams"


def test_segments_come_back_in_sequence_order(tmp_path):
    manager = persistent(tmp_path)
    manager.begin_session("s_1", "Teams", 1000.0)
    for seq in (3, 1, 2):
        manager.add("s_1", segment(seq))
    assert [s.seq for s in manager.session_segments("s_1")] == [1, 2, 3]


def test_a_resent_segment_replaces_rather_than_duplicates(tmp_path):
    manager = persistent(tmp_path)
    manager.begin_session("s_1", "Teams", 1000.0)
    manager.add("s_1", segment(1, vi=None))
    manager.update("s_1", segment(1))
    stored = manager.session_segments("s_1")
    assert len(stored) == 1
    assert stored[0].target_text == "Tiếng Việt1"


def test_the_database_is_not_world_readable(tmp_path):
    manager = persistent(tmp_path)
    assert (tmp_path / "history.sqlite3").stat().st_mode & 0o077 == 0
    manager.close()


def test_summary_round_trips(tmp_path):
    manager = persistent(tmp_path)
    manager.begin_session("s_1", "Teams", 1000.0)
    manager.end_session(record("s_1", 1000.0))
    manager.save_summary(
        "s_1",
        SessionSummary(overview="Tóm tắt", action_items=["Làm X"], generated_at=1.0),
    )
    stored = manager.get_session("s_1")
    assert stored is not None and stored.summary is not None
    assert stored.summary.overview == "Tóm tắt"
    assert stored.summary.action_items == ["Làm X"]


def test_a_corrupt_stored_summary_does_not_break_the_list(tmp_path):
    manager = persistent(tmp_path)
    manager.begin_session("s_1", "Teams", 1000.0)
    manager.end_session(record("s_1", 1000.0))
    manager._connection.execute(  # type: ignore[union-attr]
        "UPDATE sessions SET summary_json = ?", ("{not json",)
    )
    listed = manager.list_sessions()
    assert len(listed) == 1 and listed[0].summary is None


def test_deleting_one_session_leaves_the_others(tmp_path):
    manager = persistent(tmp_path)
    for index in (1, 2):
        manager.begin_session(f"s_{index}", "Teams", 1000.0 * index)
        manager.add(f"s_{index}", segment(index))
        manager.end_session(record(f"s_{index}", 1000.0 * index))
    manager.delete_session("s_1")
    assert [r.session_id for r in manager.list_sessions()] == ["s_2"]
    assert manager.session_segments("s_1") == []


def test_clear_removes_everything_including_from_disk(tmp_path):
    manager = persistent(tmp_path)
    manager.begin_session("s_1", "Teams", 1000.0)
    manager.add("s_1", segment(1))
    manager.end_session(record("s_1", 1000.0))
    manager.clear()
    assert manager.list_sessions() == []
    assert manager.session_segments("s_1") == []


def test_an_unknown_session_is_absent_rather_than_empty(tmp_path):
    assert persistent(tmp_path).get_session("s_nope") is None


# --- export -----------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["md", "txt", "json", "csv"])
def test_every_format_renders_the_content(fmt: str):
    text = render(record("s_1", 1000.0), [segment(1), segment(2)], fmt)  # type: ignore[arg-type]
    assert "日本語1" in text
    assert "Tiếng Việt1" in text


def test_markdown_includes_the_summary_and_action_items():
    rec = record("s_1", 1000.0)
    rec.summary = SessionSummary(overview="Chốt lịch release.", action_items=["Kiểm tra ABC-1234"])
    text = render(rec, [segment(1)], "md")
    assert "## Tóm tắt" in text
    assert "Chốt lịch release." in text
    assert "- [ ] Kiểm tra ABC-1234" in text


def test_export_labels_the_cost_as_an_estimate():
    """A number a colleague might paste into an expense note must not look billed."""
    text = render(record("s_1", 1000.0), [segment(1)], "md")
    assert "not a bill" in text


def test_export_says_so_when_there_is_no_transcript():
    text = render(record("s_1", 1000.0), [], "md")
    assert "No transcript" in text


def test_csv_has_a_header_and_one_row_per_segment():
    text = render(record("s_1", 1000.0), [segment(1), segment(2)], "csv")
    lines = [line for line in text.splitlines() if line.strip()]
    assert lines[0] == "seq,japanese,vietnamese"
    assert len(lines) == 3


def test_json_export_is_machine_readable():
    import json

    payload = json.loads(render(record("s_1", 1000.0), [segment(1)], "json"))
    assert payload["session"]["session_id"] == "s_1"
    assert payload["segments"][0]["seq"] == 1
