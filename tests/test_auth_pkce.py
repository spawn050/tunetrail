"""Tests for Spotify PKCE authorization primitives."""

from base64 import urlsafe_b64encode
from hashlib import sha256
from urllib.parse import parse_qs, urlsplit

from tunetrail.auth.pkce import authorization_url, create_pkce_session


def test_pkce_session_has_a_valid_s256_challenge() -> None:
    """The challenge is the unpadded base64 SHA-256 digest of the verifier."""
    session = create_pkce_session()

    expected = (
        urlsafe_b64encode(sha256(session.code_verifier.encode()).digest()).rstrip(b"=").decode()
    )

    assert session.code_challenge == expected
    assert len(session.state) >= 32


def test_authorization_url_includes_required_pkce_values() -> None:
    """Spotify receives the client, redirect URI, scope, state, and S256 challenge."""
    session = create_pkce_session()

    url = authorization_url(
        "client-id",
        "http://127.0.0.1:8765/callback",
        "user-read-private",
        session,
    )

    query = parse_qs(urlsplit(url).query)
    assert query["response_type"] == ["code"]
    assert query["state"] == [session.state]
    assert query["code_challenge"] == [session.code_challenge]
    assert query["code_challenge_method"] == ["S256"]
