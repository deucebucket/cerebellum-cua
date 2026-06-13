"""Singleton-style UIA event-handler registry (Failure 10 workaround).

Repeated ``Add*EventHandler`` calls without matching ``Remove*`` calls cause the
UIA provider to deliver thousands of events per second, pinning CPU at 30-60 %
even on an idle UI. The spec (``the design spec`` Section 2, Failure 10)
mandates a single ``EventManager`` that:

* keys every registration by ``(runtime_id_tuple, event_id, scope)``;
* calls the provider's ``Remove*EventHandler`` for that exact triple *before*
  every ``Add*`` (idempotent re-registration — no leaked duplicate handlers);
* wraps work in thin handlers that merely enqueue onto an
  :class:`~cerebellum_cua.events.coalescer.EventCoalescer` and return immediately;
* on ``shutdown`` enumerates and removes *every* registered handler before the
  caller releases the ``IUIAutomation`` reference.

The ``automation`` object is **injected** (it owns the ``Add*EventHandler`` /
``Remove*EventHandler`` methods). This module never imports ``uiautomation`` or
``comtypes``, so it imports and unit-tests cleanly on Linux with a fake
automation that records add/remove calls.
"""

from __future__ import annotations

import logging
import threading
import weakref
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from cerebellum_cua.events.coalescer import EventCoalescer

logger = logging.getLogger(__name__)

# A registry key: (runtime_id tuple, event_id, scope). All hashable scalars so
# re-registration of the exact same element/event/scope collapses onto one slot.
RegistryKey = tuple[tuple[int, ...], int, int]

# Signature the injected automation must expose. Both take the registry triple's
# components plus the handler delegate; mirrors AddStructureChangedEventHandler /
# RemoveStructureChangedEventHandler (and the other event families).
AddFn = Callable[[Any, int, int, Any], None]
RemoveFn = Callable[[Any, int, int, Any], None]


@dataclass(slots=True)
class _Registration:
    """Bookkeeping for one live handler so shutdown can remove it precisely."""

    key: RegistryKey
    element: Any
    event_id: int
    scope: int
    handler: Any
    # weakref to the original target if it supported it, else None (strong path).
    target_ref: weakref.ref[Any] | None


class EventManager:
    """Registry of UIA event handlers with Remove-before-Add and bulk shutdown.

    Parameters
    ----------
    coalescer:
        Sink for the thin handlers. ``make_handler`` returns a callable that
        enqueues a subtree key onto this coalescer and returns immediately.
    add_handler / remove_handler:
        Optional overrides for *how* to call the injected automation. By default
        :meth:`register` calls ``automation.AddStructureChangedEventHandler`` and
        ``automation.RemoveStructureChangedEventHandler``; supply these to drive
        a different event family (Focus, WindowOpened, PropertyChanged, ...).
    """

    def __init__(
        self,
        coalescer: EventCoalescer | None = None,
        add_handler: AddFn | None = None,
        remove_handler: RemoveFn | None = None,
    ) -> None:
        self._coalescer = coalescer
        self._add_override = add_handler
        self._remove_override = remove_handler
        self._lock = threading.RLock()
        self._registry: dict[RegistryKey, _Registration] = {}

    # -- key construction ------------------------------------------------

    @staticmethod
    def make_key(runtime_id: Sequence[int], event_id: int, scope: int) -> RegistryKey:
        """Build the canonical ``(runtime_id_tuple, event_id, scope)`` key."""
        return (tuple(int(x) for x in runtime_id), int(event_id), int(scope))

    # -- thin handler factory -------------------------------------------

    def make_handler(self, subtree_key: str) -> Callable[..., None]:
        """Return a thin handler that enqueues ``subtree_key`` and returns.

        The provider calls this on its own callback thread, so it must do no
        real work — it only feeds the coalescer (Failure 10).
        """

        def _handler(*_args: Any, **_kwargs: Any) -> None:
            if self._coalescer is not None:
                self._coalescer.enqueue(subtree_key)

        return _handler

    # -- registration ----------------------------------------------------

    def register(
        self,
        automation: Any,
        element: Any,
        event_id: int,
        scope: int,
        handler: Any,
        runtime_id: Sequence[int] | None = None,
    ) -> RegistryKey:
        """Remove any handler for this exact triple, then Add the new one.

        ``runtime_id`` defaults to ``element.GetRuntimeId()`` (the live UIA
        call) when not supplied. Returns the registry key.
        """
        rid = runtime_id if runtime_id is not None else element.GetRuntimeId()
        key = self.make_key(rid, event_id, scope)

        with self._lock:
            # Idempotent re-registration: tear down the prior handler first so we
            # never leak a duplicate subscription (the actual Failure-10 cause).
            existing = self._registry.pop(key, None)
            if existing is not None:
                self._do_remove(automation, existing)

            # Defensive Remove for the exact (element, event, scope) even when we
            # have no prior record — covers handlers leaked by a crashed session.
            self._call_remove(automation, element, event_id, scope, handler)
            self._call_add(automation, element, event_id, scope, handler)

            self._registry[key] = _Registration(
                key=key,
                element=element,
                event_id=event_id,
                scope=scope,
                handler=handler,
                target_ref=_try_weakref(handler),
            )
        return key

    def unregister(
        self,
        automation: Any,
        runtime_id: Sequence[int],
        event_id: int,
        scope: int,
    ) -> bool:
        """Remove the handler registered for this triple. Returns whether one existed."""
        key = self.make_key(runtime_id, event_id, scope)
        with self._lock:
            reg = self._registry.pop(key, None)
            if reg is None:
                return False
            self._do_remove(automation, reg)
        return True

    def shutdown(self, automation: Any) -> int:
        """Enumerate and remove every registered handler, then clear the registry.

        Call this before releasing the ``IUIAutomation`` reference on process
        exit. Returns the number of handlers removed.
        """
        with self._lock:
            registrations = list(self._registry.values())
            for reg in registrations:
                self._do_remove(automation, reg)
            removed = len(self._registry)
            self._registry.clear()
        return removed

    # -- introspection ---------------------------------------------------

    def registered_keys(self) -> list[RegistryKey]:
        with self._lock:
            return list(self._registry)

    def __len__(self) -> int:
        with self._lock:
            return len(self._registry)

    # -- automation call plumbing ---------------------------------------

    def _do_remove(self, automation: Any, reg: _Registration) -> None:
        self._call_remove(automation, reg.element, reg.event_id, reg.scope, reg.handler)

    def _call_add(
        self, automation: Any, element: Any, event_id: int, scope: int, handler: Any
    ) -> None:
        try:
            if self._add_override is not None:
                self._add_override(element, event_id, scope, handler)
            else:
                automation.AddStructureChangedEventHandler(element, scope, None, handler)
        except Exception:  # noqa: BLE001 - log and surface, never silently leak
            logger.exception("AddStructureChangedEventHandler failed")
            raise

    def _call_remove(
        self, automation: Any, element: Any, event_id: int, scope: int, handler: Any
    ) -> None:
        try:
            if self._remove_override is not None:
                self._remove_override(element, event_id, scope, handler)
            else:
                automation.RemoveStructureChangedEventHandler(element, handler)
        except Exception:  # noqa: BLE001 - a failed Remove must not abort shutdown
            logger.warning("RemoveStructureChangedEventHandler failed", exc_info=True)


def _try_weakref(target: Any) -> weakref.ref[Any] | None:
    """Return a weakref to ``target`` if it supports weak references, else None.

    Bound methods and plain functions are not weak-referenceable as-is, so the
    registry holds a strong ref to the handler and removes it explicitly; the
    weakref is best-effort liveness tracking only.
    """
    try:
        return weakref.ref(target)
    except TypeError:
        return None
