"""Spotify OAuth exchange, refresh, and logout operations."""

from collections.abc import Callable, Mapping
from typing import cast

import httpx

from tunetrail.auth.loopback import LoopbackCallbackReceiver
from tunetrail.auth.models import AuthorizationCallback, OAuthToken, PkceSession
from tunetrail.auth.pkce import authorization_url, create_pkce_session
from tunetrail.auth.store import FileCredentialStore


class SpotifyAuthorizationError(RuntimeError):
    """Raised when Spotify authorization cannot produce a usable token."""


class SpotifyNotAuthenticatedError(SpotifyAuthorizationError):
    """Raised when an operation requires a missing local Spotify token."""


class SpotifyAuthorizationDeniedError(SpotifyAuthorizationError):
    """Raised when Spotify redirects with an authorization error."""


class SpotifyStateMismatchError(SpotifyAuthorizationError):
    """Raised when an OAuth callback does not match its originating request."""


class SpotifyAuthenticator:
    """Perform Spotify's Authorization Code with PKCE lifecycle."""

    def __init__(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        scopes: str,
        credentials: FileCredentialStore,
        http_client: httpx.AsyncClient,
        accounts_url: str = "https://accounts.spotify.com",
    ) -> None:
        self._client_id = client_id
        self._redirect_uri = redirect_uri
        self._scopes = scopes
        self._credentials = credentials
        self._http_client = http_client
        self._accounts_url = accounts_url.rstrip("/")

    def begin_authorization(self) -> tuple[PkceSession, str]:
        """Create one PKCE session and the URL to open in the user's browser."""
        session = create_pkce_session()
        return session, authorization_url(
            self._client_id,
            self._redirect_uri,
            self._scopes,
            session,
            base_url=f"{self._accounts_url}/authorize",
        )

    async def receive_and_exchange(
        self, session: PkceSession, *, timeout_seconds: float = 180.0
    ) -> OAuthToken:
        """Receive one local callback and exchange its code for a token."""
        callback = await LoopbackCallbackReceiver(self._redirect_uri).receive(
            timeout_seconds=timeout_seconds
        )
        return await self.exchange_callback(session, callback)

    async def exchange_callback(
        self,
        session: PkceSession,
        callback: AuthorizationCallback,
        *,
        before_save: Callable[[], None] | None = None,
    ) -> OAuthToken:
        """Validate a callback's state and exchange its code for an OAuth token."""
        if callback.state != session.state:
            raise SpotifyStateMismatchError("Spotify authorization state did not match the request")
        if callback.error is not None:
            description = callback.error_description or callback.error
            raise SpotifyAuthorizationDeniedError(description)
        if callback.code is None:
            raise SpotifyAuthorizationError(
                "Spotify callback did not include an authorization code"
            )
        return await self._request_token(
            {
                "grant_type": "authorization_code",
                "code": callback.code,
                "redirect_uri": self._redirect_uri,
                "client_id": self._client_id,
                "code_verifier": session.code_verifier,
            },
            before_save=before_save,
        )

    async def access_token(self) -> str:
        """Return a usable access token, refreshing it when close to expiry."""
        token = await self._credentials.load_token()
        if token is None:
            raise SpotifyNotAuthenticatedError("Sign in to Spotify before using this action")
        if token.is_expired:
            token = await self.refresh(token)
        return token.access_token

    async def refresh(self, current_token: OAuthToken | None = None) -> OAuthToken:
        """Refresh and securely persist an existing Spotify token."""
        token = current_token or await self._credentials.load_token()
        if token is None:
            raise SpotifyNotAuthenticatedError("No Spotify refresh token is available")
        try:
            return await self._request_token(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": token.refresh_token,
                    "client_id": self._client_id,
                },
                refresh_token=token.refresh_token,
            )
        except SpotifyAuthorizationError as error:
            raise SpotifyNotAuthenticatedError(
                "Spotify token refresh failed. Existing credentials were preserved; "
                "check the connection or sign in again."
            ) from error

    async def logout(self) -> None:
        """Remove local Spotify OAuth token material."""
        await self._credentials.clear()

    async def _request_token(
        self,
        form: Mapping[str, str],
        *,
        refresh_token: str | None = None,
        before_save: Callable[[], None] | None = None,
    ) -> OAuthToken:
        """Request, validate, and securely save an OAuth token response."""
        response = await self._http_client.post(f"{self._accounts_url}/api/token", data=form)
        if response.is_error:
            detail = _spotify_error_detail(response)
            raise SpotifyAuthorizationError(f"Spotify token request failed: {detail}")
        payload_object = response.json()
        if not isinstance(payload_object, dict):
            raise SpotifyAuthorizationError("Spotify token response was not a JSON object")
        payload = cast(dict[str, object], payload_object)
        try:
            token = OAuthToken.from_spotify_response(payload, refresh_token=refresh_token)
        except ValueError as error:
            raise SpotifyAuthorizationError(str(error)) from error
        if before_save is not None:
            before_save()
        await self._credentials.save_token(token)
        return token


def _spotify_error_detail(response: httpx.Response) -> str:
    """Extract a short safe error description without including response secrets."""
    try:
        payload_object = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    if isinstance(payload_object, dict):
        payload = cast(dict[str, object], payload_object)
        description = payload.get("error_description", payload.get("error"))
        if isinstance(description, str):
            return description
    return f"HTTP {response.status_code}"
