"""Private SQLite storage for independently supplied explicit preferences."""

import asyncio
import os
import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypeVar

from tunetrail.domain.models import ExplicitPreference

_T = TypeVar("_T")
_VERSION = "explicit-profile-1"
_COLUMNS = (
    "subject",
    "name",
    "artist",
    "name_key",
    "artist_key",
    "musicbrainz_id",
    "strength",
    "source",
    "updated_at",
)


class IncompatibleProfileError(ValueError):
    """An existing database must not be changed without an explicit data decision."""


class PreferenceIdentityConflict(ValueError):
    """A preference identity cannot safely be matched to an existing entry."""


def _key(value: str | None) -> str:
    return (value or "").strip().casefold()


class SQLitePersonalizationStore:
    """Operate on a bounded explicit profile, never migrating existing databases."""

    def __init__(self, path: Path) -> None:
        self._path = path
        if path.exists():
            self._check_existing()
            return
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Exclusive creation avoids replacing a database created concurrently.
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        with sqlite3.connect(path) as connection:
            connection.executescript("""
                CREATE TABLE profile_schema (version TEXT NOT NULL);
                INSERT INTO profile_schema VALUES ('explicit-profile-1');
                CREATE TABLE explicit_preference (
                    subject TEXT NOT NULL CHECK
                        (subject IN ('artist', 'recording', 'genre', 'feature')),
                    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 200),
                    artist TEXT CHECK (artist IS NULL OR length(trim(artist)) BETWEEN 1 AND 200),
                    name_key TEXT NOT NULL CHECK (length(name_key) > 0),
                    artist_key TEXT NOT NULL,
                    musicbrainz_id TEXT CHECK (musicbrainz_id IS NULL OR
                        (subject IN ('artist', 'recording') AND length(musicbrainz_id) = 36)),
                    strength INTEGER NOT NULL CHECK
                        (typeof(strength) = 'integer' AND strength BETWEEN -2 AND 2),
                    source TEXT NOT NULL CHECK (source IN ('direct', 'chat', 'tile', 'survey')),
                    updated_at TEXT NOT NULL,
                    CHECK ((subject = 'recording' AND
                            (artist IS NOT NULL OR musicbrainz_id IS NOT NULL))
                        OR (subject != 'recording' AND artist IS NULL)),
                    UNIQUE (subject, name_key, artist_key),
                    UNIQUE (subject, musicbrainz_id)
                );
            """)

    def _check_existing(self) -> None:
        try:
            with sqlite3.connect(
                f"{self._path.resolve().as_uri()}?mode=ro", uri=True
            ) as connection:
                version = connection.execute("SELECT version FROM profile_schema").fetchall()
                columns = tuple(
                    row[1] for row in connection.execute("PRAGMA table_info(explicit_preference)")
                )
                if version != [(_VERSION,)] or columns != _COLUMNS:
                    raise IncompatibleProfileError(
                        "Existing database has an incompatible profile schema; "
                        "existing data was not changed."
                    )
        except sqlite3.DatabaseError as error:
            raise IncompatibleProfileError(
                "Existing database has an incompatible profile schema; "
                "existing data was not changed."
            ) from error

    async def set_preference(self, preference: ExplicitPreference) -> None:
        """Save one explicit preference."""
        await self.set_preferences([preference])

    async def set_preferences(self, preferences: Sequence[ExplicitPreference]) -> None:
        """Atomically save a bounded batch, rejecting conflicting identities."""

        def save(connection: sqlite3.Connection) -> None:
            connection.execute("BEGIN IMMEDIATE")
            for item in preferences:
                name_key, artist_key = _key(item.name), _key(item.artist)
                mbid = item.musicbrainz_id.lower() if item.musicbrainz_id else None
                existing = connection.execute(
                    "SELECT musicbrainz_id FROM explicit_preference "
                    "WHERE subject=? AND name_key=? AND artist_key=?",
                    (item.subject.value, name_key, artist_key),
                ).fetchone()
                if existing and existing[0] and mbid and existing[0] != mbid:
                    raise PreferenceIdentityConflict(
                        "Name matches a different MusicBrainz identity; clarify before updating."
                    )
                if mbid:
                    match = connection.execute(
                        "SELECT name_key, artist_key FROM explicit_preference "
                        "WHERE subject=? AND musicbrainz_id=?",
                        (item.subject.value, mbid),
                    ).fetchone()
                    if match and match != (name_key, artist_key):
                        raise PreferenceIdentityConflict(
                            "MusicBrainz identity matches a different name; "
                            "clarify before updating."
                        )
                if (
                    not existing
                    and connection.execute("SELECT COUNT(*) FROM explicit_preference").fetchone()[0]
                    >= 1000
                ):
                    raise ValueError(
                        "Explicit preference limit reached; remove or update existing entries."
                    )
                connection.execute(
                    """INSERT INTO explicit_preference VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(subject, name_key, artist_key) DO UPDATE SET
                    name=excluded.name, artist=excluded.artist,
                    musicbrainz_id=COALESCE(excluded.musicbrainz_id, musicbrainz_id),
                    strength=excluded.strength, source=excluded.source,
                    updated_at=excluded.updated_at""",
                    (
                        item.subject.value,
                        item.name,
                        item.artist,
                        name_key,
                        artist_key,
                        mbid,
                        item.strength,
                        item.source.value,
                        item.updated_at.isoformat(),
                    ),
                )

        await self._run(save)

    async def explicit_preferences(self) -> list[ExplicitPreference]:
        """Return only independently supplied explicit preferences."""

        def load(connection: sqlite3.Connection) -> list[ExplicitPreference]:
            rows = connection.execute(
                "SELECT subject, name, artist, musicbrainz_id, strength, source, updated_at "
                "FROM explicit_preference ORDER BY subject, name_key, artist_key"
            ).fetchall()
            keys = (
                "subject",
                "name",
                "artist",
                "musicbrainz_id",
                "strength",
                "source",
                "updated_at",
            )
            return [
                ExplicitPreference.model_validate(dict(zip(keys, row, strict=True))) for row in rows
            ]

        return await self._run(load)

    async def remove_preference(self, preference: ExplicitPreference) -> None:
        """Remove the specified preference without treating zero as removal."""

        def remove(connection: sqlite3.Connection) -> None:
            row = connection.execute(
                "SELECT musicbrainz_id FROM explicit_preference "
                "WHERE subject=? AND name_key=? AND artist_key=?",
                (preference.subject.value, _key(preference.name), _key(preference.artist)),
            ).fetchone()
            if row and preference.musicbrainz_id and row[0] != preference.musicbrainz_id.lower():
                raise PreferenceIdentityConflict(
                    "MusicBrainz identity does not match the stored preference."
                )
            connection.execute(
                "DELETE FROM explicit_preference WHERE subject=? AND name_key=? AND artist_key=?",
                (preference.subject.value, _key(preference.name), _key(preference.artist)),
            )

        await self._run(remove)

    async def reset_explicit_preferences(self) -> None:
        """Reset only the explicit profile."""
        await self._run(lambda connection: connection.execute("DELETE FROM explicit_preference"))

    async def _run(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        return await asyncio.to_thread(self._transaction, operation)

    def _transaction(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        with sqlite3.connect(f"{self._path.resolve().as_uri()}?mode=rw", uri=True) as connection:
            return operation(connection)
