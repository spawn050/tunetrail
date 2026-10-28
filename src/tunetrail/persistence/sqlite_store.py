"""Private SQLite storage for independently supplied explicit preferences."""

import asyncio
import json
import sqlite3
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from tunetrail.domain.models import ExplicitPreference

_T = TypeVar("_T")

# Historical migration definitions remain in order for existing installations.
# Their tables and contents are not read, changed, or deleted by profile operations.
_MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE profile_summary (
        id INTEGER PRIMARY KEY CHECK (id = 1), favorite_artists TEXT NOT NULL,
        favorite_track_uris TEXT NOT NULL, recent_track_uris TEXT NOT NULL,
        summary TEXT, updated_at TEXT NOT NULL
    );
    CREATE TABLE feedback (
        id INTEGER PRIMARY KEY, spotify_uri TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN (
            'like', 'dislike', 'more_like_this', 'less_like_this', 'too_familiar', 'too_distracting'
        )), note TEXT, created_at TEXT NOT NULL
    );
    CREATE INDEX feedback_by_uri_created ON feedback (spotify_uri, created_at DESC);
    CREATE TABLE playlist_signal (
        spotify_playlist_id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
        owner_id TEXT, selected_at TEXT NOT NULL, last_ingested_at TEXT
    );
    CREATE TABLE listening_session (
        id INTEGER PRIMARY KEY, action TEXT NOT NULL, device_id TEXT,
        outcome TEXT NOT NULL CHECK (outcome IN ('succeeded', 'failed')),
        created_at TEXT NOT NULL
    );
    CREATE TABLE session_track (
        session_id INTEGER NOT NULL REFERENCES listening_session(id) ON DELETE CASCADE,
        position INTEGER NOT NULL CHECK (position >= 0), spotify_uri TEXT NOT NULL,
        score_json TEXT, PRIMARY KEY (session_id, position), UNIQUE (session_id, spotify_uri)
    );
    CREATE INDEX session_track_by_uri ON session_track (spotify_uri);
    CREATE TABLE cache_metadata (
        cache_key TEXT PRIMARY KEY, refreshed_at TEXT NOT NULL, expires_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE lastfm_cache (
        cache_key TEXT PRIMARY KEY, payload_json TEXT NOT NULL,
        refreshed_at TEXT NOT NULL, expires_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE musicbrainz_cache (
        cache_key TEXT PRIMARY KEY, payload_json TEXT NOT NULL,
        refreshed_at TEXT NOT NULL, expires_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE explicit_preference (identity_key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
    """,
)


class SQLitePersonalizationStore:
    """Operate only on explicit preferences, preserving legacy tables in place."""

    def __init__(self, path: Path) -> None:
        self._path = path
        new_database = not path.exists()
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._path.parent.chmod(0o700)
        self._migrate(new_database=new_database)

    async def set_preference(self, preference: ExplicitPreference) -> None:
        """Replace one explicit preference."""
        await self.set_preferences([preference])

    async def set_preferences(self, preferences: Sequence[ExplicitPreference]) -> None:
        """Atomically save a bounded batch of explicit preferences."""

        def save(connection: sqlite3.Connection) -> None:
            connection.execute("BEGIN IMMEDIATE")
            for preference in preferences:
                identity = json.dumps(
                    [
                        preference.subject.value,
                        preference.name.casefold(),
                        (preference.artist or "").casefold(),
                        (preference.musicbrainz_id or "").lower(),
                    ],
                    separators=(",", ":"),
                )
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
        """Return only independently supplied explicit preferences."""

        def load(connection: sqlite3.Connection) -> list[ExplicitPreference]:
            rows = connection.execute(
                "SELECT value_json FROM explicit_preference ORDER BY identity_key"
            ).fetchall()
            return [ExplicitPreference.model_validate_json(row[0]) for row in rows]

        return await self._run(load)

    async def reset_explicit_preferences(self) -> None:
        """Reset the explicit profile without touching legacy tables or credentials."""
        await self._run(lambda connection: connection.execute("DELETE FROM explicit_preference"))

    async def _run(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        return await asyncio.to_thread(self._transaction, operation)

    def _transaction(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        with self._connect() as connection:
            return operation(connection)

    def _migrate(self, *, new_database: bool) -> None:
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            if new_database:
                connection.executemany(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    [(version, datetime.now().astimezone().isoformat()) for version in range(1, 4)],
                )
            applied = {
                row[0] for row in connection.execute("SELECT version FROM schema_migrations")
            }
            for version, migration in enumerate(_MIGRATIONS, start=1):
                if version not in applied:
                    connection.executescript(migration)
                    connection.execute(
                        "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                        (version, datetime.now().astimezone().isoformat()),
                    )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        self._path.chmod(0o600)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
