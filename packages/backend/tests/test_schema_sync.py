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


@pytest.mark.parametrize("model", EVENT_MODELS, ids=lambda m: m.__name__)
def test_required_schema_fields_have_no_default(schema: dict, model: type):
    definition = schema["$defs"][model.__name__]
    for name in definition.get("required", []):
        if name in ("type", "ts"):
            continue  # discriminator and timestamp are always populated
        field = model.model_fields[name]
        assert field.is_required(), f"{model.__name__}.{name} is required by the schema"


def test_typescript_types_declare_every_event():
    source = TS_PATH.read_text()
    for model in EVENT_MODELS:
        event_type = model.model_fields["type"].default
        assert f'"{event_type}"' in source, f"events.ts is missing {event_type}"
