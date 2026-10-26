"""Tests for the application entry point."""

import pytest

from tunetrail.app import main


def test_main_shows_setup_message(capsys: pytest.CaptureFixture[str]) -> None:
    """The installed command gives a useful response before configuration."""
    main()

    captured = capsys.readouterr()
    assert "not configured" in captured.out
