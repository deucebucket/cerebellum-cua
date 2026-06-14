"""UIA capture backend: adapts the Windows ``cerebellum_cua.uia`` layer to the seam.

This is a thin adapter, NOT a reimplementation. It wraps the existing UIA layer
(:class:`~cerebellum_cua.uia.UiaClient`, :func:`~cerebellum_cua.uia.walk`, and the
pattern/property helpers) behind the universal :class:`CaptureBackend` contract so
everything downstream consumes normalized :class:`CapturedElement` records and
never touches live COM.

Import-safety: this module never imports ``uiautomation`` at module scope. All
COM access flows through ``UiaClient`` (whose import is lazy/guarded) and through
``sys.platform`` checks, so ``import cerebellum_cua.capture.uia_backend`` succeeds on
Linux. Live capture / action execution raise :class:`CaptureNotAvailable` on a
non-Windows host instead of a bare ImportError.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from typing import Any

from cerebellum_cua.capture.base import (
    ActionNotSupported,
    CaptureBackend,
    CapturedElement,
    CaptureNode,
    CaptureNotAvailable,
)
from cerebellum_cua.config import MatrixConfig
from cerebellum_cua.errors import UIAAccessDeniedError
from cerebellum_cua.model import BoundingRect
from cerebellum_cua.uia import (
    PATTERN_MAP,
    UiaClient,
    extract_patterns,
    safe_get_property,
    walk,
)

# Real Microsoft UIA PropertyId constants used to extract CapturedElement fields.
_PROP_CONTROL_TYPE = 30003
_PROP_NAME = 30005
_PROP_CLASS_NAME = 30012
_PROP_AUTOMATION_ID = 30011
_PROP_RUNTIME_ID = 30001
_PROP_BOUNDING_RECT = 30007
_PROP_FRAMEWORK_ID = 30017
_PROP_PROVIDER_DESCRIPTION = 30107
_PROP_NATIVE_WINDOW_HANDLE = 30020
_PROP_PROCESS_ID = 30002
_PROP_IS_ENABLED = 30024
_PROP_IS_KEYBOARD_FOCUSABLE = 30009
_PROP_HAS_KEYBOARD_FOCUS = 30008
_PROP_IS_OFFSCREEN = 30022
_PROP_IS_CONTENT = 30016

# TreeScope.Descendants, for the Name + ControlType re-find in reacquire().
_TREE_SCOPE_DESCENDANTS = 4


def _on_windows() -> bool:
    """True only on a Windows host (where live UIA COM is available)."""
    return sys.platform.startswith("win")


def _rect_from_uia(raw: Any) -> BoundingRect:
    """Normalize a UIA bounding rectangle ``[l, t, r, b]`` to a BoundingRect."""
    try:
        left, top, right, bottom = (int(v) for v in raw[:4])
    except (TypeError, ValueError, IndexError):
        return BoundingRect()
    return BoundingRect(
        left=left,
        top=top,
        width=max(0, right - left),
        height=max(0, bottom - top),
        dpi=96,
    )


def _element_to_captured(element: Any) -> CapturedElement:
    """Convert one live UIA control into a normalized :class:`CapturedElement`.

    Every property read goes through :func:`safe_get_property` (Failure 9 cached-
    then-current) and patterns through :func:`extract_patterns`, so a single bad
    property never aborts extraction of the rest. ``native_ref`` keeps the live
    element for later action execution and for parent/child key resolution.
    """
    patterns = extract_patterns(element)
    is_enabled = bool(safe_get_property(element, _PROP_IS_ENABLED, True))
    focusable = bool(safe_get_property(element, _PROP_IS_KEYBOARD_FOCUSABLE, False))
    has_focus = bool(safe_get_property(element, _PROP_HAS_KEYBOARD_FOCUS, False))
    is_offscreen = bool(safe_get_property(element, _PROP_IS_OFFSCREEN, False))
    is_content = bool(safe_get_property(element, _PROP_IS_CONTENT, False))
    framework_id = safe_get_property(element, _PROP_FRAMEWORK_ID, "") or ""
    value = patterns.get("value", {}).get("current")
    invoke = patterns.get("invoke", {}).get("supported", False)
    properties = {
        "is_enabled": is_enabled,
        "is_keyboard_focusable": focusable,
        "has_keyboard_focus": has_focus,
        "value": value,
        "framework_id": framework_id,
        "provider_description": safe_get_property(
            element, _PROP_PROVIDER_DESCRIPTION, ""
        )
        or "",
        "native_window_handle": int(
            safe_get_property(element, _PROP_NATIVE_WINDOW_HANDLE, 0) or 0
        ),
        "process_id": int(safe_get_property(element, _PROP_PROCESS_ID, 0) or 0),
        "is_offscreen": is_offscreen,
        "is_content_element": is_content,
    }
    return CapturedElement(
        control_type=int(safe_get_property(element, _PROP_CONTROL_TYPE, 0) or 0),
        name=safe_get_property(element, _PROP_NAME, "") or "",
        class_name=safe_get_property(element, _PROP_CLASS_NAME, "") or "",
        automation_id=safe_get_property(element, _PROP_AUTOMATION_ID, "") or "",
        runtime_id=safe_get_property(element, _PROP_RUNTIME_ID, None),
        bounding_rect=_rect_from_uia(
            safe_get_property(element, _PROP_BOUNDING_RECT, None)
        ),
        properties=properties,
        patterns=patterns,
        is_interactive=bool(invoke or focusable),
        is_content=is_content,
        framework_id=framework_id,
        native_ref=element,
    )


class UiaCaptureBackend(CaptureBackend):
    """Wraps the Windows ``cerebellum_cua.uia`` layer as a universal CaptureBackend."""

    name = "uia"

    def __init__(self, client: UiaClient | None = None) -> None:
        self._client = client or UiaClient()

    def is_available(self) -> bool:
        """True only on Windows AND with ``uiautomation`` importable (never raises)."""
        if not _on_windows():
            return False
        try:
            import uiautomation  # noqa: F401, PLC0415 - availability probe
        except Exception:  # noqa: BLE001 - any import failure means unavailable
            return False
        return True

    def iter_tree(
        self, target: dict[str, Any], config: MatrixConfig
    ) -> Iterator[CaptureNode]:
        """Yield ``(CapturedElement, depth, parent_key)`` in pre-order.

        Acquires the root from ``target`` (hwnd / pid / exe_regex+title_regex /
        empty=desktop), walks it via :func:`~cerebellum_cua.uia.walk`, and converts each
        kept control. ``parent_key`` is ``id(parent_element)`` so the driver's
        ``_self_key`` (``id(native_ref)``) resolves children to their parent row.
        """
        if not _on_windows():
            raise CaptureNotAvailable(
                "UIA capture requires Windows 10/11; this host is not Windows."
            )
        root = self._acquire_root(target)
        for element, _depth, parent in walk(root, config):
            captured = _element_to_captured(element)
            parent_key = id(parent) if parent is not None else None
            yield captured, _depth, parent_key

    def reacquire(self, identity: dict[str, Any]) -> CapturedElement | None:
        """Re-find a live UIA element by Name + ControlType (spec demo re-find).

        ``identity`` needs ``name`` and ``control_type``. Returns ``None`` off
        Windows, on a COM failure, or when no element matches — never crashes.
        """
        if not _on_windows():
            return None
        name = identity.get("name")
        control_type = identity.get("control_type")
        if not name or control_type is None:
            return None
        try:
            auto = self._client.auto
            root = auto.GetRootElement()
            condition = auto.CreateAndCondition(
                auto.CreatePropertyCondition(_PROP_NAME, name),
                auto.CreatePropertyCondition(_PROP_CONTROL_TYPE, int(control_type)),
            )
            live = root.FindFirst(_TREE_SCOPE_DESCENDANTS, condition)
        except Exception:  # noqa: BLE001 - COM/import failure -> not re-acquired
            return None
        if live is None:
            return None
        return _element_to_captured(live)

    def invoke(
        self, element: CapturedElement, action: str = "invoke", **params: Any
    ) -> bool:
        """Fire a UIA control pattern on the captured element's live ``native_ref``.

        Supported actions: ``invoke`` (InvokePattern.Invoke), ``set_value`` /
        ``set_text`` (ValuePattern.SetValue, ``value=``), ``toggle``
        (TogglePattern.Toggle). Raises :class:`ActionNotSupported` for anything
        else and :class:`CaptureNotAvailable` off-Windows.
        """
        if not _on_windows():
            raise CaptureNotAvailable("UIA action execution requires Windows.")
        native = element.native_ref
        if native is None:
            raise ActionNotSupported("captured element has no live native_ref")
        if action == "invoke":
            return self._fire_pattern(native, "invoke", "Invoke")
        if action in ("set_value", "set_text"):
            return self._fire_pattern(
                native, "value", "SetValue", params.get("value", "")
            )
        if action == "toggle":
            return self._fire_pattern(native, "toggle", "Toggle")
        raise ActionNotSupported(f"uia backend does not support action {action!r}")

    def _fire_pattern(
        self, native: Any, pattern_name: str, method: str, *args: Any
    ) -> bool:
        """Resolve a UIA pattern object on ``native`` and call ``method`` on it."""
        pid = PATTERN_MAP.get(pattern_name)
        if pid is None:  # pragma: no cover - PATTERN_MAP is static
            raise ActionNotSupported(f"unknown pattern {pattern_name!r}")
        try:
            pattern = native.GetPattern(pid)
        except Exception as exc:  # noqa: BLE001 - COM/attribute failure
            raise ActionNotSupported(
                f"pattern {pattern_name!r} unavailable on element"
            ) from exc
        if pattern is None:
            raise ActionNotSupported(f"element does not support {pattern_name!r}")
        getattr(pattern, method)(*args)
        return True

    def _acquire_root(self, target: dict[str, Any]) -> Any:
        """Acquire the live root element for a target descriptor.

        ``target`` keys (any subset): ``hwnd``, ``pid``, ``exe_regex``,
        ``title_regex``. ``hwnd``/``pid`` are direct; the regex forms scan
        top-level windows; an empty dict means the whole desktop.
        """
        client = self._client
        hwnd = target.get("hwnd")
        if hwnd:
            return client.from_handle(int(hwnd))
        pid = target.get("pid")
        if pid:
            return client.from_pid(int(pid))

        exe_re = target.get("exe_regex")
        title_re = target.get("title_regex")
        if not exe_re and not title_re:
            return client.get_root()
        return self._find_window(client, exe_re, title_re, target)

    def _find_window(
        self,
        client: UiaClient,
        exe_re: str | None,
        title_re: str | None,
        target: dict[str, Any],
    ) -> Any:
        """Scan top-level windows for the first matching exe/title regex."""
        root = client.get_root()
        exe_pat = re.compile(exe_re, re.IGNORECASE) if exe_re else None
        title_pat = re.compile(title_re, re.IGNORECASE) if title_re else None
        try:
            windows = list(root.GetChildren())
        except Exception as exc:  # noqa: BLE001 - COM failure -> access denied
            raise UIAAccessDeniedError(reason="enumerate_top_level_failed") from exc
        for win in windows:
            name = safe_get_property(win, _PROP_NAME, "") or ""
            cls = safe_get_property(win, _PROP_CLASS_NAME, "") or ""
            if title_pat and not title_pat.search(name):
                continue
            if exe_pat and not exe_pat.search(cls) and not exe_pat.search(name):
                continue
            return win
        raise UIAAccessDeniedError(reason="target_window_not_found", target=target)
