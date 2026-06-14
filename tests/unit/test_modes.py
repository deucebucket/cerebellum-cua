"""Unit tests for execution modes: the mode->kwargs mapping and ``--mode`` parsing.

These are pure/argparse tests — they never construct a live engine or launch a
container. They assert that each mode selects the documented capture backend and
visible-cursor setting, and that the CLI parser accepts the three valid modes,
defaults correctly, and rejects an unknown one.
"""

from __future__ import annotations

import pytest

from cerebellum_cua.cli.__main__ import _build_parser
from cerebellum_cua.cli.modes import (
    DEFAULT_MODE,
    MODES,
    kwargs_for_mode,
    mode_names,
)


def test_mode_names_are_the_three_documented_modes() -> None:
    assert mode_names() == ["desktop", "vm", "background"]
    assert set(MODES) == {"desktop", "vm", "background"}


def test_default_mode_is_desktop() -> None:
    assert DEFAULT_MODE == "desktop"


def test_desktop_mode_kwargs() -> None:
    """desktop: real session -> auto backend, visible cursor (looks user-operated)."""
    assert kwargs_for_mode("desktop") == {
        "capture_backend_kind": "auto",
        "visible_cursor": True,
    }


def test_vm_mode_kwargs() -> None:
    """vm: isolated session with a viewer -> AT-SPI backend, visible cursor on."""
    assert kwargs_for_mode("vm") == {
        "capture_backend_kind": "atspi",
        "visible_cursor": True,
    }


def test_background_mode_kwargs() -> None:
    """background: isolated, unattended -> AT-SPI backend, no visible cursor."""
    assert kwargs_for_mode("background") == {
        "capture_backend_kind": "atspi",
        "visible_cursor": False,
    }


def test_kwargs_for_mode_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        kwargs_for_mode("nope")


def test_kwargs_for_mode_returns_a_fresh_dict() -> None:
    """Callers mutating the result must not corrupt the canonical table."""
    first = kwargs_for_mode("desktop")
    first["visible_cursor"] = False
    assert kwargs_for_mode("desktop")["visible_cursor"] is True


@pytest.mark.parametrize("mode", ["desktop", "vm", "background"])
def test_parser_accepts_each_mode(mode: str) -> None:
    args = _build_parser().parse_args(
        ["--db-dsn", "./x.db", "--secret", "s", "--mode", mode]
    )
    assert args.mode == mode


def test_parser_mode_defaults_to_desktop() -> None:
    args = _build_parser().parse_args(["--db-dsn", "./x.db", "--secret", "s"])
    assert args.mode == DEFAULT_MODE


def test_parser_rejects_invalid_mode() -> None:
    with pytest.raises(SystemExit):
        _build_parser().parse_args(
            ["--db-dsn", "./x.db", "--secret", "s", "--mode", "bogus"]
        )
