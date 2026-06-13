"""Coalesce bursts of UIA StructureChanged events into one patch signal.

Failure 10 (spec ``the design spec`` Section 2): repeated provider events
must never be processed inline. Thin handlers enqueue a *subtree key* onto a
small bounded queue and return immediately; this background coalescer drains
that queue, deduplicates keys seen within an 80 ms debounce window, and fires a
single ``on_patch_required(set_of_keys)`` callback per window. That collapses a
storm of duplicate StructureChanged notifications for the same subtree root into
one "matrix_patch_required" signal.

Pure stdlib — no COM. Subtree keys are plain strings, so the whole thing is
testable on Linux with a fake callback.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from queue import Empty, Full, Queue

logger = logging.getLogger(__name__)

# Sentinel pushed by stop() to wake the drain thread immediately for shutdown.
_SHUTDOWN = object()


class EventCoalescer:
    """Bounded queue + background drain thread that debounces subtree keys.

    Parameters
    ----------
    debounce_ms:
        Length of the coalescing window. After the first key of a burst is
        dequeued the thread keeps collecting for this long, then emits once.
    max_queue:
        Hard cap on queued, undrained keys. ``enqueue`` is non-blocking and
        drops (with a logged warning and a counter bump) once the queue is full.
    on_patch_required:
        Invoked once per window with the deduplicated ``set[str]`` of keys.
    """

    def __init__(
        self,
        on_patch_required: Callable[[set[str]], None],
        debounce_ms: int = 80,
        max_queue: int = 64,
    ) -> None:
        if debounce_ms <= 0:
            raise ValueError("debounce_ms must be positive")
        if max_queue <= 0:
            raise ValueError("max_queue must be positive")

        self._on_patch_required = on_patch_required
        self._debounce_s = debounce_ms / 1000.0
        self._max_queue = max_queue

        self._queue: Queue[object] = Queue(maxsize=max_queue)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._running = False
        self.dropped_count = 0

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        """Start the background drain thread. Idempotent."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(
                target=self._drain_loop,
                name="EventCoalescer",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Stop the drain thread, flushing any pending window, then join.

        Safe to call more than once and safe to call when never started.
        """
        with self._lock:
            if not self._running:
                return
            self._running = False
            thread = self._thread
            self._thread = None

        # Wake the loop even if it is blocked waiting on an empty queue.
        try:
            self._queue.put_nowait(_SHUTDOWN)
        except Full:
            pass

        if thread is not None:
            thread.join(timeout=timeout)

    # -- producer side ---------------------------------------------------

    def enqueue(self, subtree_key: str) -> bool:
        """Non-blocking enqueue of a subtree key. Returns ``False`` if dropped.

        Called from the thin UIA event handler, so it must never block the
        provider's callback thread.
        """
        try:
            self._queue.put_nowait(subtree_key)
            return True
        except Full:
            self.dropped_count += 1
            logger.warning(
                "EventCoalescer queue full (max=%d); dropped key %r (total dropped=%d)",
                self._max_queue,
                subtree_key,
                self.dropped_count,
            )
            return False

    # -- consumer side ---------------------------------------------------

    def _drain_loop(self) -> None:
        """Block for the first key, then coalesce a debounce window and emit."""
        while True:
            # Block until something arrives or shutdown is signalled.
            first = self._queue.get()
            if first is _SHUTDOWN:
                self._flush_remaining()
                return

            keys: set[str] = set()
            self._collect(first, keys)
            self._drain_window(keys)

            if keys:
                self._emit(keys)

            if not self._running and self._queue.empty():
                return

    def _drain_window(self, keys: set[str]) -> None:
        """Collect keys arriving within the debounce window into ``keys``."""
        deadline = self._monotonic_deadline()
        while True:
            remaining = deadline - self._now()
            if remaining <= 0:
                return
            try:
                item = self._queue.get(timeout=remaining)
            except Empty:
                return
            if item is _SHUTDOWN:
                # Shutdown mid-window: emit what we have and re-signal stop.
                self._running = False
                return
            self._collect(item, keys)

    def _flush_remaining(self) -> None:
        """Drain whatever is queued at shutdown and emit it as one final set."""
        keys: set[str] = set()
        while True:
            try:
                item = self._queue.get_nowait()
            except Empty:
                break
            if item is _SHUTDOWN:
                continue
            self._collect(item, keys)
        if keys:
            self._emit(keys)

    @staticmethod
    def _collect(item: object, keys: set[str]) -> None:
        if isinstance(item, str):
            keys.add(item)

    def _emit(self, keys: set[str]) -> None:
        try:
            self._on_patch_required(set(keys))
        except Exception:  # noqa: BLE001 - never let a callback kill the thread
            logger.exception("on_patch_required callback raised; continuing")

    # -- time hooks (overridable in tests) -------------------------------

    def _now(self) -> float:
        return time.monotonic()

    def _monotonic_deadline(self) -> float:
        return self._now() + self._debounce_s
