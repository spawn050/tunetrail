import pytest

from tunetrail.spotify.approval import ApprovalError, PlayerAction, PlayerApprovals


def test_single_use_changed_cancelled_and_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    monkeypatch.setattr("tunetrail.spotify.approval.time.monotonic", lambda: now[0])
    approvals = PlayerApprovals(ttl_seconds=10)
    action = PlayerAction.create(
        "PUT", "/me/player/play", {"device_id": "a"}, {"uris": ["spotify:track:1"]}
    )
    other = PlayerAction.create(
        "PUT", "/me/player/play", {"device_id": "b"}, {"uris": ["spotify:track:1"]}
    )
    token = approvals.propose(action)
    with pytest.raises(ApprovalError):
        approvals.consume(token, action)
    token = approvals.propose(action)
    approvals.approve(token)
    with pytest.raises(ApprovalError):
        approvals.consume(token, other)
    with pytest.raises(ApprovalError):
        approvals.consume(token, action)
    token = approvals.propose(action)
    approvals.approve(token)
    approvals.consume(token, action)
    with pytest.raises(ApprovalError):
        approvals.consume(token, action)
    token = approvals.propose(action)
    approvals.cancel(token)
    with pytest.raises(ApprovalError):
        approvals.approve(token)
    token = approvals.propose(action)
    approvals.approve(token)
    now[0] += 11
    with pytest.raises(ApprovalError):
        approvals.consume(token, action)
