"""Stale-element resolver (spec Failure 1 workaround).

Raw ``IUIAutomationElement`` COM pointers go stale after any UI mutation. This
re-acquires a live element from the root using an AndCondition built from the
element's last-known Name + ClassName + ControlType, retrying with exponential
backoff (50/150/400 ms, max 3 attempts).

The ``automation`` module is *injected* (the ``uiautomation`` module on Windows,
a fake in tests) so this stays importable and unit-testable on Linux.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("cerebellum_cua.uia.resolver")

# Real Microsoft UIA PropertyId constants used to rebuild the search condition.
NAME_PROPERTY_ID = 30005
CONTROL_TYPE_PROPERTY_ID = 30003
CLASS_NAME_PROPERTY_ID = 30012

# TreeScope_Descendants flag.
TREE_SCOPE_DESCENDANTS = 4


def resolve_stale_element(
    automation: Any,
    stale_element: Any,
    last_known_props: dict[str, Any],
    max_attempts: int = 3,
) -> Any | None:
    """Re-find a stale element from the root via composite-condition FindFirst.

    Args:
        automation: The injected UIA module (provides ``GetRootElement``,
            ``CreateAndCondition``, ``CreatePropertyCondition``, scope flags).
        stale_element: The element whose pointer may have gone stale.
        last_known_props: Dict with ``name`` / ``class_name`` / ``control_type``.
        max_attempts: Retry count (default 3), each followed by exp. backoff.

    Returns:
        A freshly acquired live element, or ``None`` if all attempts failed.
    """
    for attempt in range(max_attempts):
        try:
            root = automation.GetRootElement()
            condition = automation.CreateAndCondition(
                automation.CreatePropertyCondition(
                    NAME_PROPERTY_ID, last_known_props.get("name", "")
                ),
                automation.CreatePropertyCondition(
                    CLASS_NAME_PROPERTY_ID, last_known_props.get("class_name", "")
                ),
                automation.CreatePropertyCondition(
                    CONTROL_TYPE_PROPERTY_ID, last_known_props.get("control_type", 0)
                ),
            )
            found = root.FindFirst(TREE_SCOPE_DESCENDANTS, condition)
            if found:
                return found
        except Exception as e:  # noqa: BLE001 - any COM failure -> retry/backoff
            logger.warning("Stale resolve attempt %d failed: %s", attempt + 1, e)
        time.sleep(0.05 * (2**attempt))
    return None
