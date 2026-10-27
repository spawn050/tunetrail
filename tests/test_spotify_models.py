"""Tests for Spotify payload mappings."""

from tunetrail.spotify.models import (
    candidate_from_track,
    catalog_item_from_album,
    current_queue_from_payload,
    playback_from_payload,
)


def track_payload() -> dict[str, object]:
    """Return representative Spotify track data."""
    return {
        "id": "4uLU6hMCjMI75M1A2tKUQC",
        "uri": "spotify:track:4uLU6hMCjMI75M1A2tKUQC",
        "name": "Track",
        "artists": [{"name": "Artist"}],
        "album": {"name": "Album", "release_date": "2001-09-17"},
        "duration_ms": 180_000,
        "explicit": False,
        "external_ids": {"isrc": "USRC17607839"},
        "type": "track",
    }


def test_candidate_mapping_preserves_track_metadata() -> None:
    """Spotify search results become provider-neutral candidates."""
    candidate = candidate_from_track(track_payload())

    assert candidate.source_id == "4uLU6hMCjMI75M1A2tKUQC"
    assert candidate.year == 2001
    assert candidate.provenance["spotify_uri"].startswith("spotify:track:")


def test_playback_mapping_handles_a_current_track_and_device() -> None:
    """Current playback maps its current track and active device."""
    state = playback_from_payload(
        {
            "device": {
                "id": "device-1",
                "name": "Desktop",
                "type": "Computer",
                "is_active": True,
                "is_restricted": False,
                "volume_percent": 60,
            },
            "item": track_payload(),
            "is_playing": True,
            "progress_ms": 1_000,
        }
    )

    assert state.device is not None and state.device.name == "Desktop"
    assert state.track is not None and state.track.title == "Track"


def test_catalog_album_and_current_queue_mapping_are_typed() -> None:
    """Catalog contexts and queue entries retain only playable track metadata."""
    album = catalog_item_from_album(
        {
            "id": "album-1",
            "uri": "spotify:album:album-1",
            "name": "Album",
            "artists": [{"name": "Artist"}],
        }
    )
    queue = current_queue_from_payload(
        {
            "currently_playing": track_payload(),
            "queue": [track_payload(), {"type": "episode", "name": "Podcast"}],
        }
    )

    assert album.title == "Album"
    assert album.subtitle == "Artist"
    assert queue.current is not None and queue.current.title == "Track"
    assert [track.title for track in queue.upcoming] == ["Track"]
