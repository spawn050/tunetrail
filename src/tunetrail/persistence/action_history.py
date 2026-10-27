"""Owner-only, minimal playback action-history storage."""

import json
from collections.abc import Sequence
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from tunetrail.domain.models import ActionHistoryEntry
from tunetrail.security import assert_private_file, write_private_text

_ENTRIES = TypeAdapter(list[ActionHistoryEntry])


class ActionHistoryError(ValueError):
    """Raised when local action-history contents cannot be trusted."""


class FileActionHistory:
    """Store a bounded, owner-only JSON history of confirmed player actions."""

    def __init__(self, path: Path, *, maximum_entries: int = 100) -> None:
        if maximum_entries < 1:
            raise ValueError("maximum_entries must be positive")
        self._path = path
        self._maximum_entries = maximum_entries

    def record(self, entry: ActionHistoryEntry) -> None:
        """Append a confirmed action, retaining only the configured recent window."""
        entries = [*self._load(), entry][-self._maximum_entries :]
        payload = json.dumps(
            [stored.model_dump(mode="json") for stored in entries], separators=(",", ":")
        )
        write_private_text(self._path, payload)

    def recent(self, *, limit: int = 20) -> Sequence[ActionHistoryEntry]:
        """Return newest-first local action records without any catalog copy."""
        if limit < 1:
            raise ValueError("action-history limit must be positive")
        return list(reversed(self._load()[-limit:]))

    def _load(self) -> list[ActionHistoryEntry]:
        """Read and validate history before exposing or extending it."""
        if not self._path.exists():
            return []
        assert_private_file(self._path)
        try:
            decoded: object = json.loads(self._path.read_text(encoding="utf-8"))
            return _ENTRIES.validate_python(decoded)
        except (OSError, json.JSONDecodeError, ValidationError) as error:
            raise ActionHistoryError("TuneTrail action history could not be read safely") from error
