"""Tests for local secret-file protections."""

import os
import stat
from pathlib import Path

import pytest

from tunetrail.security import InsecureFilePermissionsError, assert_private_file, write_private_text


def test_write_private_text_uses_owner_only_permissions(tmp_path: Path) -> None:
    """New token files are readable and writable only by their owner."""
    path = tmp_path / "auth.json"

    write_private_text(path, '{"refresh_token": "secret"}')

    assert path.read_text() == '{"refresh_token": "secret"}'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_assert_private_file_rejects_group_or_other_access(tmp_path: Path) -> None:
    """Existing insecure files fail before their secrets can be used."""
    path = tmp_path / "auth.json"
    path.write_text("secret")
    os.chmod(path, 0o644)

    with pytest.raises(InsecureFilePermissionsError, match="insecure permissions"):
        assert_private_file(path)
