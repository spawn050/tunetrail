"""Normalized errors returned by Last.fm's public API."""


class LastfmApiError(RuntimeError):
    """A Last.fm request failed with a safe user-facing description."""


class LastfmUnavailableError(LastfmApiError):
    """Last.fm could not provide optional enrichment at this time."""


class LastfmRateLimitError(LastfmApiError):
    """Last.fm rejected a request due to an API usage limit."""


class LastfmProfileError(LastfmApiError):
    """The configured Last.fm profile is missing, private, or inaccessible."""


class LastfmPayloadError(LastfmApiError):
    """A successful Last.fm response did not match the expected public schema."""
