"""Normalized errors returned by Spotify's Web API."""


class SpotifyApiError(RuntimeError):
    """A Spotify Web API error with a safe user-facing description."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class SpotifyAuthenticationError(SpotifyApiError):
    """Spotify rejected the bearer token or its authorization state."""


class SpotifyPermissionError(SpotifyApiError):
    """The account, Premium entitlement, or granted scopes block an action."""


class SpotifyNoActiveDeviceError(SpotifyApiError):
    """A player action requires an active Spotify device."""


class SpotifyUnavailableError(SpotifyApiError):
    """A requested Spotify resource is unavailable in the current catalog."""


class SpotifyRateLimitError(SpotifyApiError):
    """Spotify throttled a request and supplied a retry deadline."""

    def __init__(self, message: str, retry_after_seconds: float) -> None:
        super().__init__(429, message)
        self.retry_after_seconds = retry_after_seconds
