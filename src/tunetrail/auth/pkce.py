"""RFC 7636 PKCE helpers for Spotify authorization."""

import base64
import hashlib
import secrets
from urllib.parse import urlencode

from tunetrail.auth.models import PkceSession

SPOTIFY_AUTHORIZE_URL = "https://accounts.spotify.com/authorize"


def create_pkce_session() -> PkceSession:
    """Create a cryptographically random state and S256 PKCE verifier pair."""
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    return PkceSession(
        state=secrets.token_urlsafe(32),
        code_verifier=verifier,
        code_challenge=challenge,
    )


def authorization_url(
    client_id: str,
    redirect_uri: str,
    scopes: str,
    session: PkceSession,
    *,
    base_url: str = SPOTIFY_AUTHORIZE_URL,
) -> str:
    """Build a Spotify Authorization Code with PKCE URL."""
    query = urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": scopes,
            "state": session.state,
            "code_challenge_method": "S256",
            "code_challenge": session.code_challenge,
        }
    )
    return f"{base_url}?{query}"
