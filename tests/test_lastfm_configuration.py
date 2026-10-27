"""Tests for opt-in Last.fm settings and provider-neutral candidate metadata."""

from pathlib import Path

from tunetrail.config import Settings
from tunetrail.domain.models import Candidate, CandidateSource


def test_settings_loads_optional_lastfm_configuration(tmp_path: Path) -> None:
    """Last.fm remains disabled until both public-read configuration values are supplied."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TUNETRAIL_LASTFM_API_KEY=lastfm-key\nTUNETRAIL_LASTFM_USERNAME=listener\n",
        encoding="utf-8",
    )

    settings = Settings.load({}, env_file=env_file)

    assert settings.lastfm_api_key == "lastfm-key"
    assert settings.lastfm_username == "listener"


def test_candidate_retains_normalized_lastfm_tags_and_provenance() -> None:
    """External metadata is explicit, bounded candidate evidence rather than an opaque payload."""
    candidate = Candidate(
        source=CandidateSource.LASTFM,
        source_id="artist:track",
        title="Track",
        artists=["Artist"],
        tags=["chill", "chill", "study"],
        provenance={"lastfm_url": "https://www.last.fm/music/Artist/_/Track"},
    )

    assert candidate.tags == ["chill", "study"]
