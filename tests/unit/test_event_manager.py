"""Unit tests for :class:`EventManager` — FakeAutomation, no COM.

Verifies the Failure-10 contract: Remove is called before Add on every
registration (so idempotent re-registration cannot leak), unregister removes a
single handler, and shutdown removes every registered handler then clears.
"""

from __future__ import annotations

import time
from typing import Any

from cerebellum_cua.events.coalescer import EventCoalescer
from cerebellum_cua.events.manager import EventManager

# A StructureChanged-ish event id + a scope constant; values are opaque to tests.
EVT_STRUCTURE = 20002
SCOPE_SUBTREE = 4


class _FakeElement:
    """Stand-in UIA element exposing GetRuntimeId() like the real COM element."""

    def __init__(self, runtime_id: list[int]) -> None:
        self._rid = runtime_id

    def GetRuntimeId(self) -> list[int]:  # noqa: N802 - mirror COM method name
        return list(self._rid)


class FakeAutomation:
    """Records Add/Remove calls in order so tests can assert the sequence."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, Any]] = []

    def AddStructureChangedEventHandler(  # noqa: N802 - mirror COM method name
        self, element: _FakeElement, scope: int, cache_request: Any, handler: Any
    ) -> None:
        self.calls.append(("add", id(element), handler))

    def RemoveStructureChangedEventHandler(  # noqa: N802 - mirror COM method name
        self, element: _FakeElement, handler: Any
    ) -> None:
        self.calls.append(("remove", id(element), handler))

    def ops(self) -> list[str]:
        return [c[0] for c in self.calls]


def _manager() -> EventManager:
    coalescer = EventCoalescer(lambda keys: None, debounce_ms=20, max_queue=8)
    return EventManager(coalescer=coalescer)


def test_register_calls_remove_before_add() -> None:
    auto = FakeAutomation()
    mgr = _manager()
    el = _FakeElement([7, 1, 2])
    handler = mgr.make_handler("root-7")

    mgr.register(auto, el, EVT_STRUCTURE, SCOPE_SUBTREE, handler)

    # Even on first registration a defensive Remove precedes Add (Failure 10).
    assert auto.ops() == ["remove", "add"]
    assert len(mgr) == 1


def test_reregister_removes_old_before_adding_new() -> None:
    auto = FakeAutomation()
    mgr = _manager()
    el = _FakeElement([7, 1, 2])
    h1 = mgr.make_handler("root-7")
    h2 = mgr.make_handler("root-7")

    mgr.register(auto, el, EVT_STRUCTURE, SCOPE_SUBTREE, h1)
    auto.calls.clear()

    mgr.register(auto, el, EVT_STRUCTURE, SCOPE_SUBTREE, h2)

    # Same (element, event, scope): old handler torn down, then new one added.
    ops = auto.ops()
    assert ops[-1] == "add"
    assert "remove" in ops
    # The previously registered handler must have been removed.
    removed_handlers = [c[2] for c in auto.calls if c[0] == "remove"]
    assert h1 in removed_handlers
    # Still exactly one live registration for this triple (no leak).
    assert len(mgr) == 1


def test_distinct_triples_coexist() -> None:
    auto = FakeAutomation()
    mgr = _manager()
    el_a = _FakeElement([1])
    el_b = _FakeElement([2])

    mgr.register(auto, el_a, EVT_STRUCTURE, SCOPE_SUBTREE, mgr.make_handler("a"))
    mgr.register(auto, el_b, EVT_STRUCTURE, SCOPE_SUBTREE, mgr.make_handler("b"))

    assert len(mgr) == 2
    assert len(mgr.registered_keys()) == 2


def test_unregister_removes_single_handler() -> None:
    auto = FakeAutomation()
    mgr = _manager()
    el = _FakeElement([9, 9])
    mgr.register(auto, el, EVT_STRUCTURE, SCOPE_SUBTREE, mgr.make_handler("nine"))
    auto.calls.clear()

    existed = mgr.unregister(auto, [9, 9], EVT_STRUCTURE, SCOPE_SUBTREE)

    assert existed is True
    assert auto.ops() == ["remove"]
    assert len(mgr) == 0

    # Unregistering again reports no handler and issues no calls.
    auto.calls.clear()
    assert mgr.unregister(auto, [9, 9], EVT_STRUCTURE, SCOPE_SUBTREE) is False
    assert auto.calls == []


def test_shutdown_removes_all_and_clears() -> None:
    auto = FakeAutomation()
    mgr = _manager()
    for i in range(5):
        el = _FakeElement([i])
        mgr.register(auto, el, EVT_STRUCTURE, SCOPE_SUBTREE, mgr.make_handler(f"k{i}"))
    auto.calls.clear()

    removed = mgr.shutdown(auto)

    assert removed == 5
    assert auto.ops() == ["remove"] * 5
    assert len(mgr) == 0
    assert mgr.registered_keys() == []


def test_handler_enqueues_onto_coalescer() -> None:
    captured: list[set[str]] = []
    coalescer = EventCoalescer(lambda keys: captured.append(keys), debounce_ms=15)
    mgr = EventManager(coalescer=coalescer)
    handler = mgr.make_handler("subtree-X")

    coalescer.start()
    try:
        # Simulate the provider invoking the thin handler several times.
        for _ in range(10):
            handler(None, None)

        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and not captured:
            time.sleep(0.005)
    finally:
        coalescer.stop()

    assert captured, "thin handler should have enqueued onto the coalescer"
    assert captured[0] == {"subtree-X"}


def test_runtime_id_override_skips_get_runtime_id() -> None:
    auto = FakeAutomation()
    mgr = _manager()

    class _NoRid:
        pass  # no GetRuntimeId() — register must use the supplied runtime_id

    el = _NoRid()
    key = mgr.register(
        auto, el, EVT_STRUCTURE, SCOPE_SUBTREE, mgr.make_handler("z"), runtime_id=[3, 3]
    )

    assert key == ((3, 3), EVT_STRUCTURE, SCOPE_SUBTREE)
    assert len(mgr) == 1
