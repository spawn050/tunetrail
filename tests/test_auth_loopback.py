"""Tests for safely parsing a local OAuth callback."""

from tunetrail.auth.loopback import parse_callback_target


def test_callback_parser_reads_expected_authorization_values() -> None:
    """A callback code and state are available to the authorization service."""
    callback = parse_callback_target("/callback?code=code-value&state=state-value", "/callback")

    assert callback.code == "code-value"
    assert callback.state == "state-value"


def test_callback_parser_rejects_unexpected_path() -> None:
    """Only the configured callback route can complete authorization."""
    callback = parse_callback_target("/other?code=code-value", "/callback")

    assert callback.error == "invalid_request"
