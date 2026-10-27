"""Private SQLite persistence for local TuneTrail personalization signals."""

import asyncio
import json
import sqlite3
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import TypeVar, cast

from tunetrail.domain.models import (
    ActionHistoryEntry,
    Candidate,
    ExplicitPreference,
    Feedback,
    LastfmProfile,
    PlaylistSignal,
    ProfileSummary,
)
from tunetrail.musicbrainz.matching import RecordingMatch
from tunetrail.musicbrainz.models import Recording

_T = TypeVar("_T")

_MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE profile_summary (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        favorite_artists TEXT NOT NULL,
        favorite_track_uris TEXT NOT NULL,
        recent_track_uris TEXT NOT NULL,
        summary TEXT,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE feedback (
        id INTEGER PRIMARY KEY,
        spotify_uri TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN (
            'like', 'dislike', 'more_like_this', 'less_like_this', 'too_familiar', 'too_distracting'
        )),
        note TEXT,
        created_at TEXT NOT NULL
    );
    CREATE INDEX feedback_by_uri_created ON feedback (spotify_uri, created_at DESC);
    CREATE TABLE playlist_signal (
        spotify_playlist_id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        owner_id TEXT,
        selected_at TEXT NOT NULL,
        last_ingested_at TEXT
    );
    CREATE TABLE listening_session (
        id INTEGER PRIMARY KEY,
        action TEXT NOT NULL,
        device_id TEXT,
        outcome TEXT NOT NULL CHECK (outcome IN ('succeeded', 'failed')),
        created_at TEXT NOT NULL
    );
    CREATE TABLE session_track (
        session_id INTEGER NOT NULL REFERENCES listening_session(id) ON DELETE CASCADE,
        position INTEGER NOT NULL CHECK (position >= 0),
        spotify_uri TEXT NOT NULL,
        score_json TEXT,
        PRIMARY KEY (session_id, position),
        UNIQUE (session_id, spotify_uri)
    );
    CREATE INDEX session_track_by_uri ON session_track (spotify_uri);
    CREATE TABLE cache_metadata (
        cache_key TEXT PRIMARY KEY,
        refreshed_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE lastfm_cache (
        cache_key TEXT PRIMARY KEY,
        payload_json TEXT NOT NULL,
        refreshed_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE musicbrainz_cache (
        cache_key TEXT PRIMARY KEY,
        payload_json TEXT NOT NULL,
        refreshed_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE explicit_preference (
        identity_key TEXT PRIMARY KEY,
        value_json TEXT NOT NULL
    );
    """,
)


class SQLitePersonalizationStore:
    """Persist small local signals without retaining raw Spotify payloads or prompts."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._path.parent.chmod(0o700)
        self._migrate()

    async def set_preference(self, preference: ExplicitPreference) -> None:
        """Replace one explicit preference; never consult legacy Spotify records."""
        identity = json.dumps(
            [
                preference.subject.value,
                preference.name.casefold(),
                (preference.artist or "").casefold(),
                (preference.musicbrainz_id or "").lower(),
            ],
            separators=(",", ":"),
        )

        def save(connection: sqlite3.Connection) -> None:
            exists = connection.execute(
                "SELECT 1 FROM explicit_preference WHERE identity_key = ?", (identity,)
            ).fetchone()
            count = connection.execute("SELECT COUNT(*) FROM explicit_preference").fetchone()[0]
            if exists is None and count >= 1000:
                raise ValueError(
                    "Explicit preference limit reached; reset or update existing entries."
                )
            connection.execute(
                "INSERT INTO explicit_preference VALUES (?, ?) "
                "ON CONFLICT(identity_key) DO UPDATE SET value_json = excluded.value_json",
                (identity, preference.model_dump_json()),
            )

        await self._run(save)

    async def explicit_preferences(self) -> list[ExplicitPreference]:
        """Return only the independent explicit profile."""

        def load(connection: sqlite3.Connection) -> list[ExplicitPreference]:
            rows = connection.execute(
                "SELECT value_json FROM explicit_preference ORDER BY identity_key"
            ).fetchall()
            return [ExplicitPreference.model_validate_json(row[0]) for row in rows]

        return await self._run(load)

    async def reset_explicit_preferences(self) -> None:
        """Reset the new profile without touching legacy records or credentials."""
        await self._run(lambda connection: connection.execute("DELETE FROM explicit_preference"))

    async def get(self) -> ProfileSummary | None:
        """Return the single compact local profile, if it has been ingested."""
        return await self._run(self._get_profile)

    async def save(self, profile: ProfileSummary) -> None:
        """Replace the compact profile atomically without retaining source payloads."""
        await self._run(lambda connection: self._save_profile(connection, profile))

    async def save_profile_refresh(
        self,
        profile: ProfileSummary,
        signals: Sequence[PlaylistSignal],
        *,
        refreshed_at: datetime,
        expires_at: datetime,
    ) -> None:
        """Atomically save profile data, refreshed playlist references, and cache metadata."""
        if expires_at < refreshed_at:
            raise ValueError("cache expiry cannot precede refresh time")
        await self._run(
            lambda connection: self._save_profile_refresh(
                connection, profile, signals, refreshed_at, expires_at
            )
        )

    async def add(self, feedback: Feedback) -> None:
        """Append explicit feedback so newer user choices always remain available."""
        await self._run(
            lambda connection: connection.execute(
                "INSERT INTO feedback (spotify_uri, kind, note, created_at) VALUES (?, ?, ?, ?)",
                (
                    feedback.spotify_uri,
                    feedback.kind.value,
                    feedback.note,
                    _timestamp(feedback.created_at),
                ),
            )
        )

    async def recent(self, *, limit: int = 100) -> Sequence[Feedback]:
        """Return newest-first feedback records for deterministic preference calculation."""
        if limit < 1:
            raise ValueError("feedback limit must be positive")
        return await self._run(lambda connection: self._recent_feedback(connection, limit))

    async def playlist_signals(self) -> Sequence[PlaylistSignal]:
        """Return opted-in playlist references, not their contents."""
        return await self._run(self._playlist_signals)

    async def replace(self, signals: Sequence[PlaylistSignal]) -> None:
        """Atomically replace playlist opt-ins with the user's current selection."""
        identifiers = [signal.spotify_playlist_id for signal in signals]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("playlist signals must have unique Spotify IDs")
        await self._run(lambda connection: self._replace_playlist_signals(connection, signals))

    async def record_action(
        self, entry: ActionHistoryEntry, *, score_json: Sequence[str | None]
    ) -> None:
        """Persist a confirmed or failed queue action and its URI-only session tracks."""
        if len(entry.spotify_uris) != len(score_json):
            raise ValueError("each session track requires an aligned score entry")
        await self._run(lambda connection: self._record_action(connection, entry, score_json))

    async def recent_track_uris(self, *, limit: int = 100) -> Sequence[str]:
        """Return recently generated track URIs for repetition penalties."""
        if limit < 1:
            raise ValueError("session track limit must be positive")
        return await self._run(lambda connection: self._recent_track_uris(connection, limit))

    async def cache_fresh(self, cache_key: str, *, now: datetime) -> bool:
        """Report whether a named ingestion source remains fresh at `now`."""
        return await self._run(lambda connection: self._cache_fresh(connection, cache_key, now))

    async def mark_cache(
        self, cache_key: str, *, refreshed_at: datetime, expires_at: datetime
    ) -> None:
        """Atomically record cache freshness metadata without retaining its raw response."""
        if expires_at < refreshed_at:
            raise ValueError("cache expiry cannot precede refresh time")
        await self._run(
            lambda connection: connection.execute(
                """
                INSERT INTO cache_metadata (cache_key, refreshed_at, expires_at) VALUES (?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    refreshed_at = excluded.refreshed_at,
                    expires_at = excluded.expires_at
                """,
                (cache_key, _timestamp(refreshed_at), _timestamp(expires_at)),
            )
        )

    async def reset(self) -> None:
        """Delete application-owned personalization data without touching Spotify credentials."""
        await self._run(self._reset)

    async def export_data(self) -> dict[str, object]:
        """Return local signals only, never credentials or raw Spotify payloads."""
        return await self._run(self._export_data)

    async def lastfm_profile(self, cache_key: str, *, now: datetime) -> LastfmProfile | None:
        """Return a fresh bounded normalized Last.fm profile, never a raw API response."""
        return await self._run(lambda connection: self._lastfm_profile(connection, cache_key, now))

    async def save_lastfm_profile(
        self,
        cache_key: str,
        profile: LastfmProfile,
        *,
        refreshed_at: datetime,
        expires_at: datetime,
    ) -> None:
        """Cache one normalized Last.fm profile under a short explicit expiry window."""
        await self._save_lastfm_payload(
            cache_key, profile.model_dump(mode="json"), refreshed_at, expires_at
        )

    async def lastfm_candidates(
        self, cache_key: str, *, now: datetime
    ) -> Sequence[Candidate] | None:
        """Return fresh normalized Last.fm candidate expansions for one opaque cache key."""
        return await self._run(
            lambda connection: self._lastfm_candidates(connection, cache_key, now)
        )

    async def save_lastfm_candidates(
        self,
        cache_key: str,
        candidates: Sequence[Candidate],
        *,
        refreshed_at: datetime,
        expires_at: datetime,
    ) -> None:
        """Cache a bounded normalized Last.fm candidate set with no raw source response."""
        await self._save_lastfm_payload(
            cache_key,
            {"candidates": [candidate.model_dump(mode="json") for candidate in candidates]},
            refreshed_at,
            expires_at,
        )

    async def musicbrainz_match(self, cache_key: str, *, now: datetime) -> RecordingMatch | None:
        """Load a fresh, normalized matched recording; misses and failures are not stored."""

        def load(connection: sqlite3.Connection) -> RecordingMatch | None:
            row = connection.execute(
                "SELECT payload_json, expires_at FROM musicbrainz_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            if row is None or _datetime(row["expires_at"]) <= now:
                return None
            payload = _json_object(row["payload_json"])
            return RecordingMatch(
                recording=Recording.model_validate(payload["recording"]),
                confidence=float(cast(float, payload["confidence"])),
                method=str(payload["method"]),
            )

        return await self._run(load)

    async def save_musicbrainz_match(
        self, cache_key: str, match: RecordingMatch, *, refreshed_at: datetime, expires_at: datetime
    ) -> None:
        """Store one normalized match and limit retained records to 1,000."""
        if expires_at <= refreshed_at:
            raise ValueError("cache expiry must follow refresh time")
        payload = json.dumps(
            {
                "recording": match.recording.model_dump(mode="json"),
                "confidence": match.confidence,
                "method": match.method,
            },
            separators=(",", ":"),
        )

        def save(connection: sqlite3.Connection) -> None:
            connection.execute(
                "INSERT OR REPLACE INTO musicbrainz_cache VALUES (?, ?, ?, ?)",
                (cache_key, payload, _timestamp(refreshed_at), _timestamp(expires_at)),
            )
            connection.execute(
                "DELETE FROM musicbrainz_cache WHERE cache_key IN "
                "(SELECT cache_key FROM musicbrainz_cache ORDER BY refreshed_at DESC "
                "LIMIT -1 OFFSET 1000)"
            )

        await self._run(save)

    async def _run(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        """Run a short transaction off the event loop using a fresh SQLite connection."""
        return await asyncio.to_thread(self._transaction, operation)

    async def _save_lastfm_payload(
        self,
        cache_key: str,
        payload: dict[str, object],
        refreshed_at: datetime,
        expires_at: datetime,
    ) -> None:
        """Validate expiry then persist one JSON-safe normalized Last.fm cache record."""
        if expires_at < refreshed_at:
            raise ValueError("cache expiry cannot precede refresh time")
        await self._run(
            lambda connection: self._save_lastfm_payload_transaction(
                connection, cache_key, payload, refreshed_at, expires_at
            )
        )

    def _transaction(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        """Commit one repository operation or roll it back on every failure."""
        with self._connect() as connection:
            return operation(connection)

    def _migrate(self) -> None:
        """Apply numbered schema migrations exactly once in version order."""
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {
                row[0] for row in connection.execute("SELECT version FROM schema_migrations")
            }
            for version, migration in enumerate(_MIGRATIONS, start=1):
                if version in applied:
                    continue
                connection.executescript(migration)
                connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, _timestamp(datetime.now().astimezone())),
                )

    def _connect(self) -> sqlite3.Connection:
        """Open an owner-only database connection with foreign-key enforcement."""
        connection = sqlite3.connect(self._path)
        self._path.chmod(0o600)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _get_profile(connection: sqlite3.Connection) -> ProfileSummary | None:
        row = connection.execute("SELECT * FROM profile_summary WHERE id = 1").fetchone()
        if row is None:
            return None
        return ProfileSummary(
            favorite_artists=_string_list(row["favorite_artists"]),
            favorite_track_uris=_string_list(row["favorite_track_uris"]),
            recent_track_uris=_string_list(row["recent_track_uris"]),
            summary=row["summary"],
            updated_at=_datetime(row["updated_at"]),
        )

    @staticmethod
    def _save_profile(connection: sqlite3.Connection, profile: ProfileSummary) -> None:
        connection.execute(
            """
            INSERT INTO profile_summary (
                id, favorite_artists, favorite_track_uris, recent_track_uris, summary, updated_at
            ) VALUES (1, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                favorite_artists = excluded.favorite_artists,
                favorite_track_uris = excluded.favorite_track_uris,
                recent_track_uris = excluded.recent_track_uris,
                summary = excluded.summary,
                updated_at = excluded.updated_at
            """,
            (
                json.dumps(profile.favorite_artists, separators=(",", ":")),
                json.dumps(profile.favorite_track_uris, separators=(",", ":")),
                json.dumps(profile.recent_track_uris, separators=(",", ":")),
                profile.summary,
                _timestamp(profile.updated_at),
            ),
        )

    @staticmethod
    def _save_profile_refresh(
        connection: sqlite3.Connection,
        profile: ProfileSummary,
        signals: Sequence[PlaylistSignal],
        refreshed_at: datetime,
        expires_at: datetime,
    ) -> None:
        SQLitePersonalizationStore._save_profile(connection, profile)
        SQLitePersonalizationStore._replace_playlist_signals(connection, signals)
        connection.execute(
            """
            INSERT INTO cache_metadata (cache_key, refreshed_at, expires_at) VALUES (?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                refreshed_at = excluded.refreshed_at,
                expires_at = excluded.expires_at
            """,
            ("profile-summary", _timestamp(refreshed_at), _timestamp(expires_at)),
        )

    @staticmethod
    def _lastfm_profile(
        connection: sqlite3.Connection, cache_key: str, now: datetime
    ) -> LastfmProfile | None:
        payload = SQLitePersonalizationStore._lastfm_payload(connection, cache_key, now)
        return LastfmProfile.model_validate(payload) if payload is not None else None

    @staticmethod
    def _lastfm_candidates(
        connection: sqlite3.Connection, cache_key: str, now: datetime
    ) -> list[Candidate] | None:
        payload = SQLitePersonalizationStore._lastfm_payload(connection, cache_key, now)
        if payload is None:
            return None
        candidates = payload.get("candidates")
        if not isinstance(candidates, list):
            raise ValueError("TuneTrail database contained invalid Last.fm candidates")
        return [Candidate.model_validate(item) for item in cast(list[object], candidates)]

    @staticmethod
    def _lastfm_payload(
        connection: sqlite3.Connection, cache_key: str, now: datetime
    ) -> dict[str, object] | None:
        row = connection.execute(
            "SELECT payload_json, expires_at FROM lastfm_cache WHERE cache_key = ?", (cache_key,)
        ).fetchone()
        if row is None or _datetime(row["expires_at"]) < now:
            return None
        return _json_object(row["payload_json"])

    @staticmethod
    def _save_lastfm_payload_transaction(
        connection: sqlite3.Connection,
        cache_key: str,
        payload: dict[str, object],
        refreshed_at: datetime,
        expires_at: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO lastfm_cache (cache_key, payload_json, refreshed_at, expires_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                payload_json = excluded.payload_json,
                refreshed_at = excluded.refreshed_at,
                expires_at = excluded.expires_at
            """,
            (
                cache_key,
                json.dumps(payload, separators=(",", ":")),
                _timestamp(refreshed_at),
                _timestamp(expires_at),
            ),
        )

    @staticmethod
    def _recent_feedback(connection: sqlite3.Connection, limit: int) -> list[Feedback]:
        rows = connection.execute(
            "SELECT spotify_uri, kind, note, created_at FROM feedback ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            Feedback(
                spotify_uri=row["spotify_uri"],
                kind=row["kind"],
                note=row["note"],
                created_at=_datetime(row["created_at"]),
            )
            for row in rows
        ]

    @staticmethod
    def _playlist_signals(connection: sqlite3.Connection) -> list[PlaylistSignal]:
        rows = connection.execute(
            "SELECT * FROM playlist_signal ORDER BY selected_at DESC"
        ).fetchall()
        return [
            PlaylistSignal(
                spotify_playlist_id=row["spotify_playlist_id"],
                display_name=row["display_name"],
                owner_id=row["owner_id"],
                selected_at=_datetime(row["selected_at"]),
                last_ingested_at=_optional_datetime(row["last_ingested_at"]),
            )
            for row in rows
        ]

    @staticmethod
    def _replace_playlist_signals(
        connection: sqlite3.Connection, signals: Sequence[PlaylistSignal]
    ) -> None:
        connection.execute("DELETE FROM playlist_signal")
        connection.executemany(
            """
            INSERT INTO playlist_signal (
                spotify_playlist_id, display_name, owner_id, selected_at, last_ingested_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    signal.spotify_playlist_id,
                    signal.display_name,
                    signal.owner_id,
                    _timestamp(signal.selected_at),
                    _timestamp(signal.last_ingested_at)
                    if signal.last_ingested_at is not None
                    else None,
                )
                for signal in signals
            ],
        )

    @staticmethod
    def _record_action(
        connection: sqlite3.Connection, entry: ActionHistoryEntry, score_json: Sequence[str | None]
    ) -> None:
        cursor = connection.execute(
            """
            INSERT INTO listening_session (action, device_id, outcome, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                entry.action.value,
                entry.device_id,
                entry.outcome.value,
                _timestamp(entry.created_at),
            ),
        )
        session_id = cursor.lastrowid
        if not isinstance(session_id, int):
            raise AssertionError("SQLite did not return a listening session identifier")
        connection.executemany(
            """
            INSERT INTO session_track (session_id, position, spotify_uri, score_json)
            VALUES (?, ?, ?, ?)
            """,
            [
                (session_id, position, uri, score)
                for position, (uri, score) in enumerate(
                    zip(entry.spotify_uris, score_json, strict=True)
                )
            ],
        )

    @staticmethod
    def _recent_track_uris(connection: sqlite3.Connection, limit: int) -> list[str]:
        rows = connection.execute(
            """
            SELECT session_track.spotify_uri
            FROM session_track
            JOIN listening_session ON listening_session.id = session_track.session_id
            WHERE listening_session.outcome = 'succeeded'
            ORDER BY listening_session.id DESC, session_track.position DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [row["spotify_uri"] for row in rows]

    @staticmethod
    def _cache_fresh(connection: sqlite3.Connection, cache_key: str, now: datetime) -> bool:
        row = connection.execute(
            "SELECT expires_at FROM cache_metadata WHERE cache_key = ?", (cache_key,)
        ).fetchone()
        return row is not None and _datetime(row["expires_at"]) >= now

    @staticmethod
    def _reset(connection: sqlite3.Connection) -> None:
        connection.execute("DELETE FROM profile_summary")
        connection.execute("DELETE FROM feedback")
        connection.execute("DELETE FROM playlist_signal")
        connection.execute("DELETE FROM listening_session")
        connection.execute("DELETE FROM cache_metadata")
        connection.execute("DELETE FROM lastfm_cache")
        connection.execute("DELETE FROM musicbrainz_cache")

    @staticmethod
    def _export_data(connection: sqlite3.Connection) -> dict[str, object]:
        """Serialize privacy-bounded local state for user-requested profile export."""
        profile = SQLitePersonalizationStore._get_profile(connection)
        feedback = SQLitePersonalizationStore._recent_feedback(connection, 10_000)
        signals = SQLitePersonalizationStore._playlist_signals(connection)
        recent_sessions = SQLitePersonalizationStore._recent_track_uris(connection, 10_000)
        return {
            "profile": profile.model_dump(mode="json") if profile is not None else None,
            "feedback": [item.model_dump(mode="json") for item in feedback],
            "playlist_signals": [item.model_dump(mode="json") for item in signals],
            "recent_generated_track_uris": recent_sessions,
        }


def _timestamp(value: datetime) -> str:
    """Serialize aware Pydantic datetimes in an SQLite-compatible format."""
    return value.isoformat()


def _datetime(value: object) -> datetime:
    """Deserialize a trusted database timestamp or reject corrupted local state."""
    if not isinstance(value, str):
        raise ValueError("TuneTrail database contained an invalid timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("TuneTrail database contained a timezone-naive timestamp")
    return parsed


def _optional_datetime(value: object) -> datetime | None:
    """Deserialize an optional trusted database timestamp."""
    return _datetime(value) if value is not None else None


def _string_list(value: object) -> list[str]:
    """Deserialize a compact JSON string array from local application state."""
    if not isinstance(value, str):
        raise ValueError("TuneTrail database contained an invalid string list")
    decoded: object = json.loads(value)
    if not isinstance(decoded, list):
        raise ValueError("TuneTrail database contained an invalid string list")
    items = cast(list[object], decoded)
    if not all(isinstance(item, str) for item in items):
        raise ValueError("TuneTrail database contained an invalid string list")
    return cast(list[str], items)


def _json_object(value: object) -> dict[str, object]:
    """Deserialize a normalized JSON object from private application-owned cache state."""
    if not isinstance(value, str):
        raise ValueError("TuneTrail database contained invalid Last.fm cache data")
    decoded: object = json.loads(value)
    if not isinstance(decoded, dict):
        raise ValueError("TuneTrail database contained invalid Last.fm cache data")
    return cast(dict[str, object], decoded)
