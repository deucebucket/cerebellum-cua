"""Execution-mode -> engine-kwargs mapping.

A *mode* is a named operating context that selects sensible defaults for two
:class:`~cerebellum_cua.cli.engine.CuaEngine` knobs: which capture backend to use
(``capture_backend_kind``) and whether actions glide a visible cursor first
(``visible_cursor``). Keeping the mapping here (rather than inline in argparse)
makes it independently unit-testable and keeps ``__main__`` thin.

Modes:

- ``desktop`` — attach to the real, logged-in session. Auto-select the capture
  backend (UIA on Windows, AT-SPI on Linux) and move a visible cursor so the
  automation looks user-operated. This needs the host a11y enablement described
  in ``docs/INSTALL.md``.
- ``vm`` — run inside / against the isolated virtual session brought up by
  ``scripts/run-vm.sh`` (Xvfb + openbox + the AT-SPI bus). Force the AT-SPI
  backend and keep the visible cursor on so a viewer (e.g. VNC) attached to the
  virtual display shows realistic motion.
- ``background`` — the same isolated session, but unattended: no viewer, and the
  visible cursor is off (no glide) since nothing is watching.
"""

from __future__ import annotations

from typing import TypedDict


class ModeKwargs(TypedDict):
    """The subset of :class:`CuaEngine` kwargs a mode selects."""

    capture_backend_kind: str
    visible_cursor: bool


#: Canonical mode -> engine-kwargs mapping. Order is the argparse choice order.
MODES: dict[str, ModeKwargs] = {
    "desktop": {"capture_backend_kind": "auto", "visible_cursor": True},
    "vm": {"capture_backend_kind": "atspi", "visible_cursor": True},
    "background": {"capture_backend_kind": "atspi", "visible_cursor": False},
}

#: Default mode when ``--mode`` is omitted.
DEFAULT_MODE = "desktop"


def mode_names() -> list[str]:
    """Return the valid mode names, in declaration order (for argparse choices)."""
    return list(MODES)


def kwargs_for_mode(mode: str) -> ModeKwargs:
    """Return the engine kwargs for ``mode``.

    Raises ``ValueError`` for an unknown mode so the caller can surface a clear
    error; argparse ``choices`` normally rejects bad values before this is hit.
    """
    try:
        selected = MODES[mode]
    except KeyError as exc:
        valid = ", ".join(mode_names())
        raise ValueError(f"unknown execution mode {mode!r}; choose one of: {valid}") from exc
    # Return a fresh dict so callers cannot mutate the canonical table.
    return {
        "capture_backend_kind": selected["capture_backend_kind"],
        "visible_cursor": selected["visible_cursor"],
    }
