"""AT-SPI Text-interface content extraction (pure, binding-free).

Reads an accessible's full text buffer and caret offset through a *duck-typed*
surface (``get_text(start, end)`` / ``get_caret_offset()``). The live backend's
:class:`~cerebellum_cua.capture.atspi._adapter.LiveAdapter` implements those by
calling ``Atspi.Text.get_text(acc, 0, -1)`` / ``Atspi.Text.get_caret_offset``,
while unit-test fakes expose them directly — so NO ``gi``/``Atspi`` import lives
here and the extraction logic stays exercised by plain fakes.

This is what makes terminals/editors/documents (e.g. Konsole contents) readable
exactly and cheaply: text + caret only, never pixels.
"""

from __future__ import annotations

from typing import Any

# Default cap on captured Text-interface buffers (chars); keeps elements lean.
DEFAULT_TEXT_MAX_CHARS = 4000


def read_text_content(
    accessible: Any, max_chars: int = DEFAULT_TEXT_MAX_CHARS
) -> tuple[str, bool]:
    """Read the full Text-interface buffer, capped at ``max_chars``.

    Reads via a duck-typed ``get_text(start, end)`` getter. Returns
    ``(text, truncated)`` where ``truncated`` is True when the buffer exceeded
    ``max_chars`` and was clipped. Any failure yields ``("", False)``.
    """
    getter = getattr(accessible, "get_text", None)
    if not callable(getter):
        return "", False
    try:
        raw = getter(0, -1)
    except Exception:  # noqa: BLE001 - never let a live-bus hiccup crash convert
        return "", False
    if raw is None:
        return "", False
    text = str(raw)
    if max_chars >= 0 and len(text) > max_chars:
        return text[:max_chars], True
    return text, False


def read_caret_offset(accessible: Any) -> int | None:
    """Return the Text-interface caret offset, or ``None`` when unavailable."""
    getter = getattr(accessible, "get_caret_offset", None)
    if not callable(getter):
        return None
    try:
        return int(getter())
    except Exception:  # noqa: BLE001
        return None


def apply_text_content(
    properties: dict[str, Any],
    accessible: Any,
    interfaces: set[str],
    max_chars: int,
) -> None:
    """Populate ``text_content``/``text_truncated``/``caret_offset`` in place.

    Only touches ``properties`` when the element supports the ``Text`` (or
    ``EditableText``) interface, so elements without it are never bloated. Keeps
    the live ``Atspi.Text`` call behind the duck-typed getter so the pure path
    stays testable.
    """
    if "Text" not in interfaces and "EditableText" not in interfaces:
        return
    text, truncated = read_text_content(accessible, max_chars)
    if text:
        properties["text_content"] = text
        if truncated:
            properties["text_truncated"] = True
    caret = read_caret_offset(accessible)
    if caret is not None:
        properties["caret_offset"] = caret
