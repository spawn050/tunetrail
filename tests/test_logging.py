"""Tests for secret-safe structured logging."""

import logging

import pytest

from tunetrail.logging import configure_logging, redact


def test_redact_removes_values_from_context_and_messages() -> None:
    """Known secret fields and bearer values never reach log output."""
    secret = "very-secret-token"

    redacted = redact(
        {
            "access_token": secret,
            "nested": {"authorization": f"Bearer {secret}"},
            "message": f"token={secret}; Authorization: Bearer {secret}",
        }
    )

    assert secret not in str(redacted)
    assert redacted["access_token"] == "[REDACTED]"


def test_configured_logger_redacts_output(capsys: pytest.CaptureFixture[str]) -> None:
    """Formatting a record cannot leak a token provided in an extra context."""
    secret = "very-secret-token"
    configure_logging()
    logging.getLogger("tunetrail.test").info("request failed", extra={"context": {"token": secret}})

    captured = capsys.readouterr()
    assert secret not in captured.err
    assert "[REDACTED]" in captured.err
