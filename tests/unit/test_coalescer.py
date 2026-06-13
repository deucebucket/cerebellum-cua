"""Unit tests for :class:`EventCoalescer` — plain string keys, fake callback.

Covers the Failure-10 coalescing contract: a burst of duplicate keys fires the
callback once with the deduped set; a full queue drops excess and bumps the
counter; start/stop is clean and non-hanging. Kept fast (<1s) and non-flaky by
using a short debounce and generous sleeps relative to it.
"""

from __future__ import annotations

import threading
import time

from cerebellum_cua.events.coalescer import EventCoalescer


class _Sink:
    """Records each on_patch_required(set) call in a thread-safe list."""

    def __init__(self) -> None:
        self.calls: list[set[str]] = []
        self._lock = threading.Lock()

    def __call__(self, keys: set[str]) -> None:
        with self._lock:
            self.calls.append(set(keys))

    def snapshot(self) -> list[set[str]]:
        with self._lock:
            return [set(c) for c in self.calls]


def _wait_for(predicate, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_duplicate_burst_emits_once_with_deduped_set() -> None:
    sink = _Sink()
    coalescer = EventCoalescer(sink, debounce_ms=30, max_queue=64)
    coalescer.start()
    try:
        for _ in range(20):
            coalescer.enqueue("root-A")
        for _ in range(20):
            coalescer.enqueue("root-B")

        assert _wait_for(lambda: len(sink.snapshot()) >= 1)
        # Give the window a moment to ensure no second emit sneaks in.
        time.sleep(0.08)

        calls = sink.snapshot()
        assert len(calls) == 1, f"expected one coalesced emit, got {calls}"
        assert calls[0] == {"root-A", "root-B"}
    finally:
        coalescer.stop()


def test_separate_windows_emit_separately() -> None:
    sink = _Sink()
    coalescer = EventCoalescer(sink, debounce_ms=25, max_queue=64)
    coalescer.start()
    try:
        coalescer.enqueue("first")
        assert _wait_for(lambda: len(sink.snapshot()) >= 1)
        # Wait well past the window so the next key starts a fresh one.
        time.sleep(0.08)
        coalescer.enqueue("second")
        assert _wait_for(lambda: len(sink.snapshot()) >= 2)

        calls = sink.snapshot()
        assert calls[0] == {"first"}
        assert calls[1] == {"second"}
    finally:
        coalescer.stop()


def test_queue_overflow_drops_excess_and_counts() -> None:
    sink = _Sink()
    # Tiny queue; do NOT start the drain thread so nothing is consumed and the
    # queue genuinely fills, making the drop path deterministic.
    coalescer = EventCoalescer(sink, debounce_ms=30, max_queue=4)

    accepted = sum(coalescer.enqueue(f"k{i}") for i in range(10))

    assert accepted == 4
    assert coalescer.dropped_count == 6
    assert sink.snapshot() == []


def test_clean_start_stop_no_events() -> None:
    sink = _Sink()
    coalescer = EventCoalescer(sink, debounce_ms=20, max_queue=8)
    coalescer.start()
    coalescer.stop()  # must return promptly and join cleanly
    assert sink.snapshot() == []


def test_stop_flushes_pending_keys() -> None:
    sink = _Sink()
    # Long debounce so the window would not fire on its own; stop() must flush.
    coalescer = EventCoalescer(sink, debounce_ms=5000, max_queue=64)
    coalescer.start()
    coalescer.enqueue("pending-1")
    coalescer.enqueue("pending-2")
    # Small delay so the drain thread has begun its (long) window wait.
    time.sleep(0.02)
    coalescer.stop()

    calls = sink.snapshot()
    assert len(calls) == 1
    assert calls[0] == {"pending-1", "pending-2"}


def test_double_stop_is_safe() -> None:
    sink = _Sink()
    coalescer = EventCoalescer(sink, debounce_ms=20, max_queue=8)
    coalescer.start()
    coalescer.stop()
    coalescer.stop()  # no hang, no error


def test_stop_without_start_is_safe() -> None:
    sink = _Sink()
    coalescer = EventCoalescer(sink, debounce_ms=20, max_queue=8)
    coalescer.stop()  # never started — must be a no-op
