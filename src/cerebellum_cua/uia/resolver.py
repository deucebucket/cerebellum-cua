"""Stale-element resolver (spec Failure 1 workaround).

Live ``uiautomation`` Control objects go stale after any UI mutation. This
re-acquires a fresh control from the desktop root using a lazy ``Control`` search
built from the element's last-known Name + ControlType, retrying with exponential
backoff (50/150/400 ms, max 3 attempts).

The ``automation`` module is *injected* (the ``uiautomation`` module on Windows,
a fake in tests) so this stays importable and unit-testable on Linux.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("cerebellum_cua.uia.resolver")

# Search the entire subtree under the desktop root.
_SEARCH_DEPTH_DESCENDANTS = 0xFFFFFFFF


def resolve_stale_element(
    automation: Any,
    stale_element: Any,
    last_known_props: dict[str, Any],
    max_attempts: int = 3,
) -> Any | None:
    """Re-find a stale control from the root via an ``auto.Control`` search.

    Args:
        automation: The injected UIA module (provides ``GetRootControl`` and the
            ``Control`` factory).
        stale_element: The control whose pointer may have gone stale.
        last_known_props: Dict with ``name`` / ``control_type`` (``class_name``
            is included as a search hint when present).
        max_attempts: Retry count (default 3), each followed by exp. backoff.

    Returns:
        A freshly acquired live control, or ``None`` if all attempts failed.
    """
    name = last_known_props.get("name", "")
    control_type = last_known_props.get("control_type")
    class_name = last_known_props.get("class_name")
    for attempt in range(max_attempts):
        try:
            root = automation.GetRootControl()
            criteria: dict[str, Any] = {
                "searchFromControl": root,
                "searchDepth": _SEARCH_DEPTH_DESCENDANTS,
                "Name": name,
            }
            if control_type is not None:
                criteria["ControlType"] = int(control_type)
            if class_name:
                criteria["ClassName"] = class_name
            candidate = automation.Control(**criteria)
            if candidate.Exists(0.5, 0.1):
                return candidate
        except Exception as e:  # noqa: BLE001 - any COM failure -> retry/backoff
            logger.warning("Stale resolve attempt %d failed: %s", attempt + 1, e)
        time.sleep(0.05 * (2**attempt))
    return None
