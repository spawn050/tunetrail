"""Locally recognize complete, explicit player commands without model authorization."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PlayerCommand:
    action: str
    method: str
    path: str
    params: dict[str, str | int]


_PREFIX = r"(?:(?:ok(?:ay)?[ ,]+)?(?:can you |could you |please |let(?:'s|s| us) )?)(?:please )?"
_SUFFIX = r"(?: please)?"
_SUBJECT = r"(?:the |this )?(?:current )?(?:song|track|music|playback)"


def parse_player_command(message: str) -> PlayerCommand | None:
    """Match only a whole, unambiguous current-message request."""
    text = message.casefold().strip().rstrip(".!?").strip()
    text = re.sub(r"\s+", " ", text)
    patterns = (
        (rf"(?:pause|stop)(?: {_SUBJECT})?", "pause", "PUT", "pause"),
        (rf"(?:resume|play)(?: {_SUBJECT})?", "resume", "PUT", "play"),
        (
            r"(?:next|skip)(?: (?:the )?(?:next |current )?(?:song|track))?"
            r"|play (?:the )?next (?:song|track)",
            "next",
            "POST",
            "next",
        ),
        (
            r"(?:previous|prev)(?: (?:the )?(?:song|track))?"
            r"|(?:play|go to) (?:the )?(?:previous|prev) (?:song|track)",
            "previous",
            "POST",
            "previous",
        ),
    )
    for pattern, action, method, endpoint in patterns:
        if re.fullmatch(_PREFIX + "(?:" + pattern + ")" + _SUFFIX, text):
            return PlayerCommand(action, method, f"/me/player/{endpoint}", {})
    volume = re.fullmatch(
        _PREFIX
        + r"(?:set |change )?(?:the )?volume (?:to |at )?(\d{1,3})(?:\s*(?:%|percent))?"
        + _SUFFIX,
        text,
    )
    if volume and 0 <= int(volume.group(1)) <= 100:
        value = int(volume.group(1))
        return PlayerCommand(
            f"volume {value}%", "PUT", "/me/player/volume", {"volume_percent": value}
        )
    shuffle = re.fullmatch(
        _PREFIX + r"(?:(?:turn |switch )?shuffle (on|off)|(enable|disable) shuffle)" + _SUFFIX,
        text,
    )
    if shuffle:
        enabled = shuffle.group(1) == "on" or shuffle.group(2) == "enable"
        state = "true" if enabled else "false"
        return PlayerCommand(
            f"shuffle {'on' if enabled else 'off'}", "PUT", "/me/player/shuffle", {"state": state}
        )
    return None
