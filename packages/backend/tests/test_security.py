"""Guarantees from docs/security.md that are enforced by code, not convention."""

from __future__ import annotations

import logging

from jlt import logging as jlt_logging
from jlt.secrets import Secret, new_local_token, token_matches


def test_secret_never_renders_itself():
    secret = Secret("sk-abcdefghijklmnopqrstuvwxyz")
    assert "sk-" not in repr(secret)
    assert "sk-" not in str(secret)
    assert "sk-" not in f"{secret}"
    assert "sk-" not in "{}".format(secret)  # noqa: UP032
    assert secret.reveal() == "sk-abcdefghijklmnopqrstuvwxyz"


def test_token_comparison_rejects_wrong_and_missing_tokens():
    token = new_local_token()
    assert token_matches(token, token.reveal())
    assert not token_matches(token, "wrong")
    assert not token_matches(token, None)
    assert not token_matches(token, "")


def test_redaction_filter_scrubs_keys_from_messages():
    record = logging.LogRecord(
        "t",
        logging.INFO,
        __file__,
        1,
        "connecting with sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
        None,
        None,
    )
    jlt_logging.RedactionFilter().filter(record)
    assert "sk-ABCDEFGHIJ" not in record.getMessage()
    assert "sk-***" in record.getMessage()


def test_redaction_filter_scrubs_registered_secrets():
    jlt_logging.register_secret("local-token-value-xyz")
    record = logging.LogRecord(
        "t", logging.INFO, __file__, 1, "token=local-token-value-xyz", None, None
    )
    jlt_logging.RedactionFilter().filter(record)
    assert "local-token-value-xyz" not in record.getMessage()


def test_redaction_preserves_printf_style_arguments():
    """Coercing args to str would break any library logging "%d" with an int.

    uvicorn does exactly that on startup, which is how this was found.
    """
    record = logging.LogRecord(
        "t", logging.INFO, __file__, 1, "Started server process [%d]", (20505,), None
    )
    jlt_logging.RedactionFilter().filter(record)
    assert record.getMessage() == "Started server process [20505]"


def test_redaction_still_scrubs_string_arguments():
    record = logging.LogRecord(
        "t", logging.INFO, __file__, 1, "key=%s", ("sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",), None
    )
    jlt_logging.RedactionFilter().filter(record)
    assert "sk-ABCDEFGHIJ" not in record.getMessage()


def test_json_formatter_scrubs_keys_in_structured_fields():
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "x", None, None)
    record.url = "wss://api.openai.com/?k=sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
    output = jlt_logging.JsonFormatter().format(record)
    assert "sk-ABCDEFGHIJ" not in output


def test_audio_persistence_cannot_be_enabled_by_config():
    import pytest
    from pydantic import ValidationError

    from jlt.config import PrivacySettings

    PrivacySettings(persist_audio=False)
    with pytest.raises(ValidationError):
        PrivacySettings(persist_audio=True)
