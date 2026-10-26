"""Validated OAuth values used by the Spotify authorization flow."""

from datetime import UTC, datetime, timedelta

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class PkceSession(BaseModel):
    """The short-lived values required to complete one PKCE authorization flow."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: str = Field(min_length=32)
    code_verifier: str = Field(min_length=43, max_length=128)
    code_challenge: str = Field(min_length=43)


class AuthorizationCallback(BaseModel):
    """The authorization result returned to TuneTrail's loopback endpoint."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str | None = None
    state: str | None = None
    error: str | None = None
    error_description: str | None = None

    @field_validator("code", "state", "error", "error_description")
    @classmethod
    def empty_values_are_none(cls, value: str | None) -> str | None:
        """Treat blank OAuth query values as absent values."""
        return value.strip() or None if value is not None else None


class OAuthToken(BaseModel):
    """A Spotify bearer token and its refresh metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    access_token: str = Field(min_length=1)
    refresh_token: str = Field(min_length=1)
    expires_at: AwareDatetime
    scope: str = ""
    token_type: str = "Bearer"

    @property
    def is_expired(self) -> bool:
        """Return whether the token should be refreshed before use."""
        return self.expires_at <= datetime.now(UTC) + timedelta(seconds=30)

    @classmethod
    def from_spotify_response(
        cls,
        payload: dict[str, object],
        *,
        refresh_token: str | None = None,
        now: datetime | None = None,
    ) -> "OAuthToken":
        """Create a persisted token from Spotify's token endpoint response."""
        access_token = payload.get("access_token")
        returned_refresh_token = payload.get("refresh_token", refresh_token)
        expires_in = payload.get("expires_in")
        if not isinstance(access_token, str) or not isinstance(returned_refresh_token, str):
            raise ValueError("Spotify token response did not contain usable token material")
        if not isinstance(expires_in, int | float):
            raise ValueError("Spotify token response did not contain expires_in")
        scope = payload.get("scope")
        token_type = payload.get("token_type")
        timestamp = now or datetime.now(UTC)
        return cls(
            access_token=access_token,
            refresh_token=returned_refresh_token,
            expires_at=timestamp + timedelta(seconds=expires_in),
            scope=scope if isinstance(scope, str) else "",
            token_type=token_type if isinstance(token_type, str) else "Bearer",
        )
