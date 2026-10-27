"""Optional cached Last.fm candidate expansion resolved conservatively through Spotify."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from tunetrail.domain.models import Candidate, LastfmProfile, ListeningIntent, ProfileSummary
from tunetrail.domain.protocols import LastfmGateway, PersonalizationStore
from tunetrail.lastfm.errors import LastfmApiError
from tunetrail.recommendation.resolver import SpotifyCandidateResolver

_PROFILE_TTL = timedelta(hours=6)
_SIMILARITY_TTL = timedelta(hours=24)
_PROFILE_TRACK_LIMIT = 10
_MAX_TRACK_SEEDS = 3
_MAX_ARTIST_SEEDS = 3
_SIMILAR_TRACK_LIMIT = 4
_SIMILAR_ARTIST_LIMIT = 2
_ARTIST_TRACK_LIMIT = 2
_TAG_LIMIT = 5


class LastfmEnrichmentService:
    """Expand bounded Last.fm signals without blocking the existing Spotify-only workflow."""

    def __init__(
        self,
        *,
        lastfm: LastfmGateway,
        resolver: SpotifyCandidateResolver,
        store: PersonalizationStore,
        username: str,
    ) -> None:
        self._lastfm = lastfm
        self._resolver = resolver
        self._store = store
        self._profile_cache_key = _cache_key("profile", username)

    async def find(
        self, intent: ListeningIntent, profile: ProfileSummary | None
    ) -> list[Candidate]:
        """Return Spotify-resolvable tagged expansions, or none when optional Last.fm fails."""
        del intent
        try:
            lastfm_profile = await self._profile()
            expansions = await asyncio.gather(
                *(
                    self._similar_track_expansion(track)
                    for track in lastfm_profile.top_tracks[:_MAX_TRACK_SEEDS]
                ),
                *(
                    self._similar_artist_expansion(artist)
                    for artist in _unique(
                        [
                            *lastfm_profile.top_artists,
                            *(profile.favorite_artists if profile is not None else []),
                        ]
                    )[:_MAX_ARTIST_SEEDS]
                ),
            )
        except LastfmApiError:
            return []
        external = _unique_candidates([candidate for group in expansions for candidate in group])
        resolved = await asyncio.gather(
            *(self._resolver.resolve(candidate) for candidate in external)
        )
        return [candidate for candidate in resolved if candidate is not None]

    async def _profile(self) -> LastfmProfile:
        """Load fresh normalized history or import and cache a bounded replacement."""
        now = datetime.now(UTC)
        cached = await self._store.lastfm_profile(self._profile_cache_key, now=now)
        if cached is not None:
            return cached
        top_artists, top_tracks, recent_tracks = await asyncio.gather(
            self._lastfm.user_top_artists(limit=_PROFILE_TRACK_LIMIT),
            self._lastfm.user_top_tracks(limit=_PROFILE_TRACK_LIMIT),
            self._lastfm.user_recent_tracks(limit=_PROFILE_TRACK_LIMIT),
        )
        imported = LastfmProfile(
            top_artists=_unique(list(top_artists)),
            top_tracks=list(top_tracks),
            recent_tracks=list(recent_tracks),
            updated_at=now,
        )
        await self._store.save_lastfm_profile(
            self._profile_cache_key,
            imported,
            refreshed_at=now,
            expires_at=now + _PROFILE_TTL,
        )
        return imported

    async def _similar_track_expansion(self, seed: Candidate) -> list[Candidate]:
        """Retrieve/caches community-similar tracks and attach normalized community tags."""
        cache_key = _cache_key("similar-track", seed.artists[0], seed.title)
        return await self._cached_candidates(
            cache_key,
            lambda: self._tag_tracks(
                self._lastfm.similar_tracks(
                    artist=seed.artists[0], title=seed.title, limit=_SIMILAR_TRACK_LIMIT
                )
            ),
        )

    async def _similar_artist_expansion(self, artist: str) -> list[Candidate]:
        """Expand similar artists into a few tagged tracks suitable for Spotify matching."""
        cache_key = _cache_key("similar-artist", artist)

        async def load() -> list[Candidate]:
            similar_artists = await self._lastfm.similar_artists(
                artist, limit=_SIMILAR_ARTIST_LIMIT
            )
            groups = await asyncio.gather(
                *(
                    self._artist_tracks_with_tags(similar_artist)
                    for similar_artist in similar_artists
                )
            )
            return [candidate for group in groups for candidate in group]

        return await self._cached_candidates(cache_key, load)

    async def _artist_tracks_with_tags(self, artist: str) -> list[Candidate]:
        """Attach one similar artist's normalized community tags to its track suggestions."""
        tracks, tags = await asyncio.gather(
            self._lastfm.artist_top_tracks(artist, limit=_ARTIST_TRACK_LIMIT),
            self._lastfm.artist_tags(artist, limit=_TAG_LIMIT),
        )
        normalized_tags = _normalize_tags(tags)
        return [track.model_copy(update={"tags": normalized_tags}) for track in tracks]

    async def _tag_tracks(self, tracks: Awaitable[Sequence[Candidate]]) -> list[Candidate]:
        """Attach normalized Last.fm tags to each bounded similar-track response."""
        candidates = list(await tracks)
        tag_groups = await asyncio.gather(
            *(
                self._lastfm.track_tags(artist=item.artists[0], title=item.title, limit=_TAG_LIMIT)
                for item in candidates
            )
        )
        return [
            candidate.model_copy(update={"tags": _normalize_tags(tags)})
            for candidate, tags in zip(candidates, tag_groups, strict=True)
        ]

    async def _cached_candidates(
        self, cache_key: str, loader: Callable[[], Awaitable[list[Candidate]]]
    ) -> list[Candidate]:
        """Use fresh candidate expansions or persist one normalized bounded refresh result."""
        now = datetime.now(UTC)
        cached = await self._store.lastfm_candidates(cache_key, now=now)
        if cached is not None:
            return list(cached)
        candidates = await loader()
        await self._store.save_lastfm_candidates(
            cache_key,
            candidates,
            refreshed_at=now,
            expires_at=now + _SIMILARITY_TTL,
        )
        return candidates


def _normalize_tags(tags: Sequence[str]) -> list[str]:
    """Normalize Last.fm community labels for deterministic mood/activity comparison only."""
    return _unique([" ".join(tag.casefold().split()) for tag in tags if tag.strip()])[:_TAG_LIMIT]


def _cache_key(kind: str, *values: str) -> str:
    """Avoid retaining readable user/artist/title values as persistent cache keys."""
    digest = sha256("\0".join(value.casefold().strip() for value in values).encode()).hexdigest()
    return f"lastfm:{kind}:{digest}"


def _unique(values: Sequence[str]) -> list[str]:
    """Deduplicate ordered profile/tag values without changing their strongest-first position."""
    return list(dict.fromkeys(values))


def _unique_candidates(candidates: Sequence[Candidate]) -> list[Candidate]:
    """Deduplicate external suggestions before Spotify resolution by artist/title identity."""
    result: dict[tuple[str, str], Candidate] = {}
    for candidate in candidates:
        key = (candidate.artists[0].casefold(), candidate.title.casefold())
        result.setdefault(key, candidate)
    return list(result.values())
