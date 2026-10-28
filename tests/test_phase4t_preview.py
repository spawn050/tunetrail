from pathlib import Path
from uuid import UUID

import httpx
import pytest
import respx

from tests.test_spotify_client import spotify_client
from tests.test_spotify_models import track_payload
from tunetrail.domain.models import ListeningIntent, PlaybackAction
from tunetrail.musicbrainz.models import Artist, Recording
from tunetrail.recommendation.independent_discovery import (
    EnrichedCandidate,
    IndependentCandidate,
    IndependentDiscovery,
    IndependentSeed,
    SeedKind,
)
from tunetrail.recommendation.ranking import rank_candidates
from tunetrail.recommendation.spotify_preview import build_preview
from tunetrail.spotify.approval import ApprovalError


def ranking(intent: ListeningIntent | None = None):
    candidate = IndependentCandidate(UUID(int=1), "Track", ("Artist",), 180000)
    return rank_candidates(
        [EnrichedCandidate(candidate, None, "unavailable")], [], intent or ListeningIntent()
    )


@respx.mock
async def test_preview_then_explicit_approval_one_mutation(tmp_path: Path) -> None:
    track = {**track_payload(), "is_playable": True}
    search = respx.get("https://api.example/v1/search").mock(
        return_value=httpx.Response(200, json={"tracks": {"items": [track], "total": 1}})
    )
    play = respx.put("https://api.example/v1/me/player/play").mock(return_value=httpx.Response(204))
    respx.get("https://api.example/v1/me/player").mock(return_value=httpx.Response(204))
    async with httpx.AsyncClient() as http:
        client = await spotify_client(tmp_path, http, [])
        preview = await build_preview(
            ranking(), client, client.approvals, action=PlaybackAction.PLAY_NOW, device_id="device"
        )
        assert search.called and not play.called
        assert preview.proposal_id and len(preview.matches) == 1
        uri = preview.matches[0].uri
        with pytest.raises(ApprovalError):
            await client.start_playback([uri], device_id="device", approval=preview.proposal_id)
        # A failed attempt consumes the proposal, requiring a fresh preview.
        preview = await build_preview(
            ranking(), client, client.approvals, action=PlaybackAction.PLAY_NOW, device_id="device"
        )
        assert preview.proposal_id
        client.approvals.approve(preview.proposal_id)
        await client.start_playback([uri], device_id="device", approval=preview.proposal_id)
        assert play.call_count == 1
        with pytest.raises(ApprovalError):
            await client.start_playback([uri], device_id="device", approval=preview.proposal_id)


@respx.mock
async def test_ambiguity_unplayability_and_unsupported_constraint(tmp_path: Path) -> None:
    track = {**track_payload(), "is_playable": True}
    search = respx.get("https://api.example/v1/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "tracks": {
                    "items": [track, {**track, "id": "other", "uri": "spotify:track:other"}],
                    "total": 2,
                }
            },
        )
    )
    async with httpx.AsyncClient() as http:
        client = await spotify_client(tmp_path, http, [])
        preview = await build_preview(
            ranking(), client, client.approvals, action=PlaybackAction.PLAY_NOW
        )
        assert not preview.matches and preview.proposal_id is None
        search.mock(
            return_value=httpx.Response(200, json={"tracks": {"items": [track], "total": 2}})
        )
        assert not (
            await build_preview(ranking(), client, client.approvals, action=PlaybackAction.PLAY_NOW)
        ).matches
        search.mock(
            return_value=httpx.Response(
                200, json={"tracks": {"items": [{**track, "is_playable": False}], "total": 1}}
            )
        )
        assert not (
            await build_preview(ranking(), client, client.approvals, action=PlaybackAction.PLAY_NOW)
        ).matches
        search.mock(
            return_value=httpx.Response(200, json={"tracks": {"items": [track], "total": 1}})
        )
        preview = await build_preview(
            ranking(ListeningIntent(year_start=2000)),
            client,
            client.approvals,
            action=PlaybackAction.PLAY_NOW,
        )
        assert preview.matches and preview.proposal_id is None and preview.issues


@respx.mock
async def test_unapproved_legacy_methods_cannot_mutate(tmp_path: Path) -> None:
    async with httpx.AsyncClient() as http:
        client = await spotify_client(tmp_path, http, [])
        for call in (
            client.pause(),
            client.resume(),
            client.next_track(),
            client.previous_track(),
            client.select_device("device"),
            client.add_to_queue("spotify:track:abc"),
            client.start_playback(["spotify:track:abc"]),
        ):
            with pytest.raises(ApprovalError):
                await call
        with pytest.raises(ApprovalError):
            await client._request("POST", "/me/player/queue")  # pyright: ignore[reportPrivateUsage]


@respx.mock
async def test_source_outage_is_reported_without_player_mutation(tmp_path: Path) -> None:
    respx.get("https://api.example/v1/search").mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as http:
        client = await spotify_client(tmp_path, http, [])
        preview = await build_preview(
            ranking(), client, client.approvals, action=PlaybackAction.PLAY_NOW
        )
    assert preview.proposal_id is None and any("unavailable" in issue for issue in preview.issues)


@respx.mock
async def test_discovery_to_approved_single_queue_append(tmp_path: Path) -> None:
    class IndependentSource:
        async def recording(self, recording_id: UUID) -> Recording:
            return Recording(
                id=str(recording_id),
                title="Track",
                duration_ms=180000,
                artists=[Artist(id=str(UUID(int=2)), name="Artist")],
            )

        async def browse_artist_recordings(self, artist_id: UUID, *, limit: int) -> list[Recording]:
            return []

    track = {**track_payload(), "is_playable": True}
    respx.get("https://api.example/v1/search").mock(
        return_value=httpx.Response(200, json={"tracks": {"items": [track], "total": 1}})
    )
    queue = respx.post("https://api.example/v1/me/player/queue").mock(
        return_value=httpx.Response(204)
    )
    respx.get("https://api.example/v1/me/player").mock(return_value=httpx.Response(204))
    discovery = await IndependentDiscovery(IndependentSource()).discover(
        [IndependentSeed(SeedKind.RECORDING, UUID(int=1))], limit=1
    )
    ranks = rank_candidates(
        [EnrichedCandidate(c, None, "not requested") for c in discovery.candidates],
        [],
        ListeningIntent(),
    )
    async with httpx.AsyncClient() as http:
        client = await spotify_client(tmp_path, http, [])
        preview = await build_preview(
            ranks, client, client.approvals, action=PlaybackAction.APPEND_QUEUE
        )
        assert preview.proposal_id and not queue.called
        client.approvals.approve(preview.proposal_id)
        await client.add_to_queue(preview.matches[0].uri, approval=preview.proposal_id)
        assert queue.call_count == 1


@respx.mock
async def test_approval_for_preview_cannot_authorize_changed_device(tmp_path: Path) -> None:
    track = {**track_payload(), "is_playable": True}
    respx.get("https://api.example/v1/search").mock(
        return_value=httpx.Response(200, json={"tracks": {"items": [track], "total": 1}})
    )
    async with httpx.AsyncClient() as http:
        client = await spotify_client(tmp_path, http, [])
        preview = await build_preview(
            ranking(), client, client.approvals, action=PlaybackAction.PLAY_NOW, device_id="one"
        )
        assert preview.proposal_id
        client.approvals.approve(preview.proposal_id)
        with pytest.raises(ApprovalError):
            await client.start_playback(
                [preview.matches[0].uri], device_id="two", approval=preview.proposal_id
            )
