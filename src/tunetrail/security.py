"""Safe handling of local credential and token files."""

import os
import stat
from pathlib import Path


class InsecureFilePermissionsError(PermissionError):
    """Raised when a secret file is readable or writable by other users."""


def assert_private_file(path: Path) -> None:
    """Raise if an existing secret file grants any group or other permissions."""
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        return
    if mode & 0o077:
        raise InsecureFilePermissionsError(
            f"{path} has insecure permissions {mode:04o}; expected owner-only access"
        )


def write_private_text(path: Path, contents: str) -> None:
    """Atomically write owner-only secret contents to a local file."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temporary_path = path.with_name(f".{path.name}.tmp")
    descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as secret_file:
            secret_file.write(contents)
        os.replace(temporary_path, path)
        path.chmod(0o600)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    assert_private_file(path)
