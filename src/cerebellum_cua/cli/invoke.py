"""Live action invocation (Windows-only COM, isolated from the handlers).

``invoke_element`` re-acquires a live UIA element from a persisted
:class:`~cerebellum_cua.model.Element` (by Name + ControlType, the spec's demo re-find)
and fires its InvokePattern. It touches COM only via the
:class:`~cerebellum_cua.uia.UiaClient` facade, so importing this module never fails on
Linux; calling it without ``uiautomation`` raises a clear ImportError that the
handler maps to a typed :class:`~cerebellum_cua.errors.UIAAccessDeniedError`.
"""

from __future__ import annotations

from typing import Any

from cerebellum_cua.model import Element
from cerebellum_cua.uia import UiaClient

# Raw UIA constants for the re-find + invoke (mirrors spec Section 5 demo path).
_NAME_PROPERTY_ID = 30005
_CONTROL_TYPE_PROPERTY_ID = 30003
_INVOKE_PATTERN_ID = 10000
_TREE_SCOPE_DESCENDANTS = 4


def invoke_element(element: Element, client: UiaClient | None = None) -> bool:
    """Re-find ``element`` on the live tree and invoke it. Returns success.

    Raises:
        ImportError: with an actionable hint when ``uiautomation`` is absent.
    """
    cli = client or UiaClient()
    auto = cli.auto  # triggers the guarded lazy import (ImportError on Linux)
    root = auto.GetRootElement()
    condition = auto.CreateAndCondition(
        auto.CreatePropertyCondition(_NAME_PROPERTY_ID, element.name),
        auto.CreatePropertyCondition(_CONTROL_TYPE_PROPERTY_ID, element.control_type),
    )
    live: Any = root.FindFirst(_TREE_SCOPE_DESCENDANTS, condition)
    if live and live.SupportsPattern(_INVOKE_PATTERN_ID):
        live.GetInvokePattern().Invoke()
        return True
    return False
