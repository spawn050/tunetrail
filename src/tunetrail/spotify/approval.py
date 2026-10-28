"""In-memory, single-use approval for one exact Spotify player request."""

import json
import secrets
import time
from dataclasses import dataclass


class ApprovalError(ValueError):
    """No valid approval exists for the exact request."""


@dataclass(frozen=True, slots=True)
class PlayerAction:
    """Exact HTTP mutation, including device and ordered track URIs."""

    method: str
    path: str
    params_json: str
    body_json: str

    @classmethod
    def create(cls, method: str, path: str, params: object, body: object) -> "PlayerAction":
        return cls(method, path, _canonical(params), _canonical(body))


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class PlayerApprovals:
    """Proposals and confirmations are local, short-lived and never persisted."""

    def __init__(self, *, ttl_seconds: float = 300) -> None:
        if not 0 < ttl_seconds <= 300:
            raise ValueError("Approval lifetime must be between 0 and 300 seconds")
        self._ttl = ttl_seconds
        self._pending: dict[str, tuple[PlayerAction, float, bool]] = {}

    def propose(self, action: PlayerAction) -> str:
        self._expire()
        if len(self._pending) >= 100:
            raise ApprovalError("Too many pending player proposals")
        identifier = secrets.token_urlsafe(32)
        self._pending[identifier] = (action, time.monotonic() + self._ttl, False)
        return identifier

    def approve(self, identifier: str) -> None:
        """Call only in response to an explicit owner approval of the displayed proposal."""
        self._expire()
        proposal = self._pending.get(identifier)
        if proposal is None or proposal[2]:
            raise ApprovalError("Proposal is missing, expired, or already approved")
        self._pending[identifier] = (proposal[0], proposal[1], True)

    def cancel(self, identifier: str) -> None:
        self._pending.pop(identifier, None)

    def consume(self, identifier: str | None, action: PlayerAction) -> None:
        self._expire()
        proposal = self._pending.pop(identifier, None) if identifier else None
        if proposal is None or not proposal[2] or proposal[0] != action:
            raise ApprovalError("Exact, unexpired, single-use player approval required")

    def _expire(self) -> None:
        now = time.monotonic()
        for key, (_, expiry, _) in list(self._pending.items()):
            if expiry <= now:
                del self._pending[key]
