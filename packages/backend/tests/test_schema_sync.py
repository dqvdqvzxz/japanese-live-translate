"""The JSON schema, the Python models and the TypeScript types are one contract.

`schemas/events.json` is the source of truth. If this test fails, the backend
and the UI have drifted apart and the UI will silently ignore fields.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jlt import models

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "schemas" / "events.json"
TS_PATH = REPO_ROOT / "packages" / "desktop" / "src" / "types" / "events.ts"

EVENT_MODELS = [
    models.TranscriptPartialEvent,
    models.TranscriptFinalEvent,
    models.SegmentCreatedEvent,
    models.SegmentTranslatedEvent,
    models.SegmentFailedEvent,
    models.StatusEvent,
    models.SessionStartedEvent,
    models.SessionStoppedEvent,
    models.ErrorEvent,
]


@pytest.fixture(scope="module")
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def test_every_schema_event_has_a_python_model(schema: dict):
    schema_types = {
        definition["properties"]["type"]["const"]
        for name, definition in schema["$defs"].items()
        if name.endswith("Event")
    }
    model_types = {m.model_fields["type"].default for m in EVENT_MODELS}
    assert schema_types == model_types


def test_event_types_constant_matches_the_schema(schema: dict):
    schema_types = {
        definition["properties"]["type"]["const"]
        for name, definition in schema["$defs"].items()
        if name.endswith("Event")
    }
    assert models.EVENT_TYPES == frozenset(schema_types)


@pytest.mark.parametrize("model", EVENT_MODELS, ids=lambda m: m.__name__)
def test_model_fields_match_schema_properties(schema: dict, model: type):
    definition = schema["$defs"][model.__name__]
    schema_fields = set(definition["properties"])
    model_field_names = set(model.model_fields)
    assert model_field_names == schema_fields, (
        f"{model.__name__} fields drifted from schemas/events.json"
    )


#: One realistic instance per event type. These are what actually goes over the
#: wire, so validating them against the schema tests the real contract rather
#: than a proxy for it.
SAMPLES: list[models.BaseModel] = [
    models.TranscriptPartialEvent(
        session_id="s_1", item_id="item_003", seq=42, text="\u6765\u9031\u306e"
    ),
    models.TranscriptFinalEvent(
        session_id="s_1",
        item_id="item_003",
        seq=42,
        text="\u6765\u9031\u306e",
        stt_latency_ms=940.0,
    ),
    models.SegmentCreatedEvent(
        session_id="s_1", segment_id="seg_17", seq=42, source_text="x", source_seqs=[42]
    ),
    models.SegmentTranslatedEvent(
        session_id="s_1",
        segment_id="seg_17",
        seq=42,
        source_text="x",
        target_text="y",
        translation_latency_ms=1510.0,
        e2e_latency_ms=2450.0,
        usage=models.Usage(input_tokens=243, output_tokens=38),
    ),
    models.SegmentFailedEvent(
        session_id="s_1", segment_id="seg_17", seq=42, source_text="x", reason="timeout"
    ),
    models.StatusEvent(
        session_id="s_1",
        capture="running",
        stt="connected",
        translation="ready",
        audio_level_dbfs=-31.2,
        queue_depth=1,
    ),
    models.SessionStartedEvent(
        session_id="s_1",
        source=models.AudioSource(
            id="app:com.microsoft.teams2", kind="application", name="Microsoft Teams"
        ),
    ),
    models.SessionStoppedEvent(
        session_id="s_1",
        stats=models.SessionStats(session_id="s_1", started_at=1.0, duration_s=60.0),
    ),
    models.ErrorEvent(
        session_id="s_1",
        severity="warning",
        code="stt_reconnecting",
        message="Reconnecting.",
        retryable=True,
    ),
]


def test_every_event_model_has_a_sample():
    assert {type(s) for s in SAMPLES} == set(EVENT_MODELS)


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: type(s).__name__)
def test_serialized_events_validate_against_the_schema(schema: dict, sample):
    """The real contract: what we actually send must satisfy the schema.

    This catches a required field that is missing from the payload, an extra
    field the UI would not expect, and any type mismatch — none of which a
    field-name comparison alone would notice.
    """
    jsonschema = pytest.importorskip("jsonschema")
    payload = json.loads(sample.model_dump_json())
    definition = {**schema["$defs"][type(sample).__name__], "$defs": schema["$defs"]}
    jsonschema.validate(payload, definition)


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: type(s).__name__)
def test_required_schema_fields_are_always_serialized(schema: dict, sample):
    definition = schema["$defs"][type(sample).__name__]
    payload = json.loads(sample.model_dump_json())
    missing = set(definition.get("required", [])) - set(payload)
    assert not missing, f"{type(sample).__name__} omits required field(s) {missing}"


def test_typescript_types_declare_every_event():
    source = TS_PATH.read_text()
    for model in EVENT_MODELS:
        event_type = model.model_fields["type"].default
        assert f'"{event_type}"' in source, f"events.ts is missing {event_type}"
