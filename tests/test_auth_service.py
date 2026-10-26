"""Tests for Spotify token exchange, refresh, and logout."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from tunetrail.auth.models import AuthorizationCallback, OAuthToken
from tunetrail.auth.pkce import create_pkce_session
from tunetrail.auth.service import (
    SpotifyAuthenticator,
    SpotifyNotAuthenticatedError,
    SpotifyStateMismatchError,
)
from tunetrail.auth.store import FileCredentialStore


def authenticator(path: Path, client: httpx.AsyncClient) -> SpotifyAuthenticator:
    """Build an authenticator against a mocked Spotify accounts host."""
    return SpotifyAuthenticator(
        client_id="client-id",
        redirect_uri="http://127.0.0.1:8765/callback",
        scopes="user-read-private",
        credentials=FileCredentialStore(path),
        http_client=client,
        accounts_url="https://accounts.example",
    )


@respx.mock
async def test_exchange_callback_validates_state_and_saves_token(tmp_path: Path) -> None:
    """A valid matching callback is exchanged and persisted for future launches."""
    route = respx.post("https://accounts.example/api/token").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "new-access-token",
                "refresh_token": "new-refresh-token",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        )
    )
    async with httpx.AsyncClient() as client:
        service = authenticator(tmp_path / "auth.json", client)
        session = create_pkce_session()

        token = await service.exchange_callback(
            session, AuthorizationCallback(code="authorization-code", state=session.state)
        )

    assert route.called
    assert token.access_token == "new-access-token"
    assert (await FileCredentialStore(tmp_path / "auth.json").load_token()) == token


async def test_exchange_callback_rejects_wrong_state(tmp_path: Path) -> None:
    """A callback cannot be used to complete a different authorization request."""
    async with httpx.AsyncClient() as client:
        service = authenticator(tmp_path / "auth.json", client)
        with pytest.raises(SpotifyStateMismatchError):
            await service.exchange_callback(
                create_pkce_session(), AuthorizationCallback(code="code", state="wrong-state")
            )


@respx.mock
async def test_refresh_preserves_existing_refresh_token_when_omitted(tmp_path: Path) -> None:
    """Spotify refresh responses may omit refresh_token without ending a session."""
    old_token = OAuthToken(
        access_token="old-access-token",
        refresh_token="old-refresh-token",
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    route = respx.post("https://accounts.example/api/token").mock(
        return_value=httpx.Response(
            200, json={"access_token": "fresh-access-token", "expires_in": 3600}
        )
    )
    async with httpx.AsyncClient() as client:
        service = authenticator(tmp_path / "auth.json", client)
        refreshed = await service.refresh(old_token)

    assert route.called
    assert refreshed.refresh_token == old_token.refresh_token
    assert refreshed.access_token == "fresh-access-token"


async def test_logout_removes_saved_token(tmp_path: Path) -> None:
    """Logout removes token material from local disk."""
    async with httpx.AsyncClient() as client:
        store = FileCredentialStore(tmp_path / "auth.json")
        await store.save_token(
            OAuthToken(
                access_token="access-token",
                refresh_token="refresh-token",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        service = authenticator(tmp_path / "auth.json", client)

        await service.logout()

    assert await store.load_token() is None


@respx.mock
async def test_revoked_refresh_token_is_removed_and_requires_sign_in(tmp_path: Path) -> None:
    """A rejected refresh token cannot leave an unusable local session behind."""
    store = FileCredentialStore(tmp_path / "auth.json")
    await store.save_token(
        OAuthToken(
            access_token="expired-access-token",
            refresh_token="revoked-refresh-token",
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
    )
    respx.post("https://accounts.example/api/token").mock(
        return_value=httpx.Response(
            400,
            json={"error": "invalid_grant", "error_description": "Refresh token revoked"},
        )
    )
    async with httpx.AsyncClient() as client:
        service = authenticator(tmp_path / "auth.json", client)
        with pytest.raises(SpotifyNotAuthenticatedError, match="Sign in again"):
            await service.access_token()

    assert await store.load_token() is None
