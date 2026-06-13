"""Events layer: UIA event-handler registry + structure-change coalescer.

The Failure-10 workaround (spec ``the design spec`` Section 2). One
:class:`EventManager` owns every UIA event subscription, removing the old
handler before adding a new one so re-registration never leaks. Thin handlers
enqueue subtree keys onto an :class:`EventCoalescer`, whose background thread
debounces a storm of StructureChanged events into a single
``matrix_patch_required`` signal.

Neither module imports ``uiautomation``/``comtypes`` — the live automation
object is injected — so this package imports and unit-tests cleanly on Linux.
"""

from __future__ import annotations

from cerebellum_cua.events.coalescer import EventCoalescer
from cerebellum_cua.events.manager import EventManager

__all__ = [
    "EventCoalescer",
    "EventManager",
]
