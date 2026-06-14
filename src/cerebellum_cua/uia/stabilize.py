"""Virtualized-subtree stabilization (spec Failure 2 workaround).

Virtualized containers (List / DataGrid / Document) only realize child elements
once they are scrolled into view, so a naive ``GetChildren`` returns a partial
set. This forces realization: focus the container, scroll to top then bottom via
the Scroll pattern, and poll ``GetChildren`` every 25 ms until the count reaches
``expected_min_children``, stabilizes, or the timeout elapses.

Elements are duck-typed; no ``uiautomation`` import. The Scroll pattern is probed
via ``GetScrollPattern`` (non-``None`` means supported) defensively.
"""

from __future__ import annotations

import time
from typing import Any


def stabilize_virtualized(
    container: Any,
    expected_min_children: int = 5,
    timeout_ms: int = 1200,
) -> int:
    """Force a virtualized container to realize children; return the child count.

    Args:
        container: The duck-typed container element (List / DataGrid / Document).
        expected_min_children: Stop early once at least this many children appear.
        timeout_ms: Hard wall-clock budget in milliseconds.

    Returns:
        The last observed child count (best-effort; never raises).
    """
    start = time.time() * 1000
    last_count = 0
    while (time.time() * 1000 - start) < timeout_ms:
        try:
            sp = container.GetScrollPattern()
            if sp is not None:
                sp.SetScrollPercent(0, 0)
                time.sleep(0.05)
                sp.SetScrollPercent(0, 100)
                time.sleep(0.05)
            children = container.GetChildren()
            count = len(children)
            # Stop when we hit the expected floor, or the count has settled.
            if count >= expected_min_children or (count > 0 and count == last_count):
                return count
            last_count = count
            time.sleep(0.025)
        except Exception:  # noqa: BLE001 - bail out, return best-effort count
            break
    return last_count
