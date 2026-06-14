"""Pure detection of elevation/authentication prompt windows.

Given a window dict (the shape produced by
:meth:`cerebellum_cua.desktop.windows.WindowState.to_dict` or any equivalent
mapping), decide whether it is an elevation prompt — a polkit auth dialog on
Linux or the UAC consent dialog on Windows. Matching is case-insensitive over
the window's title, app name, and (optional) accessible role/class, against the
keyword tables in :mod:`cerebellum_cua.elevation.base`.

Both functions are pure (no I/O, no engine, no live capture) so they are
trivially unit-testable on hand-built dicts.
"""

from __future__ import annotations

from typing import Any

from cerebellum_cua.elevation.base import POLKIT_KEYWORDS, UAC_KEYWORDS

#: Window-dict keys searched for keyword matches (any present key is checked).
_TEXT_KEYS = ("title", "app", "name", "role", "class", "class_name", "wm_class")


def _haystack(window: dict[str, Any]) -> str:
    """Join every searchable text field of a window dict, lowercased."""
    parts: list[str] = []
    for key in _TEXT_KEYS:
        value = window.get(key)
        if isinstance(value, str) and value:
            parts.append(value)
    return "\n".join(parts).casefold()


def _matches_any(text: str, keywords: tuple[str, ...]) -> bool:
    """True if any keyword is a substring of the already-lowercased ``text``."""
    return any(keyword in text for keyword in keywords)


def is_elevation_prompt(window: dict[str, Any]) -> bool:
    """Return True if ``window`` looks like a polkit or UAC elevation prompt.

    Args:
        window: A window dict with at least a ``title`` and/or ``app`` (other
            keys in :data:`_TEXT_KEYS` are also consulted when present).

    Returns:
        True when any polkit or UAC keyword matches case-insensitively.
    """
    if not isinstance(window, dict):
        return False
    text = _haystack(window)
    if not text:
        return False
    return _matches_any(text, POLKIT_KEYWORDS) or _matches_any(text, UAC_KEYWORDS)


def prompt_kind(window: dict[str, Any]) -> str | None:
    """Classify a prompt window as ``"polkit"``, ``"uac"``, or ``None``.

    UAC keywords are checked first so a Windows consent dialog is never
    misclassified as polkit. Returns ``None`` when the window is not a prompt.
    """
    if not isinstance(window, dict):
        return None
    text = _haystack(window)
    if not text:
        return None
    if _matches_any(text, UAC_KEYWORDS):
        return "uac"
    if _matches_any(text, POLKIT_KEYWORDS):
        return "polkit"
    return None


def find_elevation_prompt(windows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the first window in ``windows`` that is an elevation prompt.

    Args:
        windows: A list of window dicts (e.g. the ``windows`` field of the
            ``list_windows`` operation result).

    Returns:
        The first matching window dict, or ``None`` when none match.
    """
    for window in windows or []:
        if is_elevation_prompt(window):
            return window
    return None


__all__ = ["is_elevation_prompt", "find_elevation_prompt", "prompt_kind"]
