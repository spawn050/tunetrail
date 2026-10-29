"""Complete-message player command authorization must fail closed."""

import pytest

from tunetrail.api.player_commands import parse_player_command


@pytest.mark.parametrize(
    "message",
    [
        "don't pause",
        "should I pause?",
        "pause and next",
        "pause after this song",
        "play next song by Radiohead",
        "set volume to 101 percent",
        "volume -1",
        "change the volume",
        "shuffle",
        "turn shuffle on and pause",
        "please don't play",
        "if I say next",
        "resume the current song then skip",
        "set volume to 30.5 percent",
    ],
)
def test_ambiguous_or_mixed_messages_never_authorize(message: str) -> None:
    assert parse_player_command(message) is None
