"""User-takeover kill-switch: cancel automated input when a human intervenes.

When the engine is synthesizing mouse/keyboard events, a real person grabbing the
keyboard or mouse must be able to take over instantly — automation should yield,
not fight. :class:`AbortWatcher` runs a background thread that reads Linux evdev
input devices and sets a :class:`threading.Event` the moment it sees genuine user
input (or a configured panic key). The synthetic-input loops check that event
between steps and raise :class:`AbortedByUser`.

The watcher excludes our *own* synthetic device (ydotool's virtual uinput node)
so the automation does not abort itself. Identification is by device name: any
device whose name contains one of :data:`_SYNTHETIC_NAME_HINTS` is ignored.

The ``evdev`` package and ``/dev/input`` access are both optional. If either is
missing the watcher degrades to a no-op that reports ``available is False`` and
never blocks — importing and using this module must succeed on any host.
"""

from __future__ import annotations

import threading
from typing import Any

#: evdev key codes for the default panic keys (linux/input-event-codes.h).
_KEY_ESC = 1
_KEY_SPACE = 57

#: Substrings (lower-cased) marking a device as our own synthetic injector.
_SYNTHETIC_NAME_HINTS: tuple[str, ...] = ("ydotool", "uinput")


class AbortedByUser(RuntimeError):
    """Raised inside a synthetic-input loop when the user-takeover fires."""


def _load_evdev() -> Any:
    """Return the ``evdev`` module, or ``None`` if it cannot be imported."""
    try:
        import evdev  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - any import failure -> no-op mode
        return None
    return evdev


class AbortWatcher:
    """Watch evdev devices and trip an event on genuine user input.

    Construct, ``start()`` to spawn the monitor thread, and check ``triggered``
    (or pass ``event`` into a synthetic-input call). ``stop()`` joins the thread.
    When ``evdev`` is unavailable or no devices are readable, ``available`` is
    ``False`` and the watcher is an inert no-op.
    """

    def __init__(
        self,
        panic_keys: tuple[int, ...] = (_KEY_SPACE, _KEY_ESC),
        evdev_module: Any | None = None,
    ) -> None:
        self.event = threading.Event()
        self._panic_keys = tuple(panic_keys)
        # Injectable for tests; falls back to the real lazy import.
        self._evdev = evdev_module if evdev_module is not None else _load_evdev()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._devices: list[Any] = []
        self.available = self._evdev is not None

    # --- public API ------------------------------------------------------
    @property
    def triggered(self) -> bool:
        """True once user takeover has fired."""
        return self.event.is_set()

    def start(self) -> None:
        """Begin monitoring in a background thread (no-op if unavailable)."""
        if not self.available or self._thread is not None:
            return
        self._devices = self._open_devices()
        if not self._devices:
            # evdev present but nothing readable (/dev/input perms) -> no-op.
            self.available = False
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop monitoring and join the thread."""
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=1.0)
        for dev in self._devices:
            close = getattr(dev, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    pass
        self._devices = []

    def __enter__(self) -> AbortWatcher:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # --- internals -------------------------------------------------------
    def _open_devices(self) -> list[Any]:
        """List readable, non-synthetic input devices."""
        evdev = self._evdev
        try:
            paths = evdev.list_devices()
        except Exception:  # noqa: BLE001
            return []
        devices: list[Any] = []
        for path in paths:
            try:
                dev = evdev.InputDevice(path)
            except Exception:  # noqa: BLE001 - unreadable node, skip it
                continue
            if self._is_synthetic(dev):
                close = getattr(dev, "close", None)
                if callable(close):
                    close()
                continue
            devices.append(dev)
        return devices

    @staticmethod
    def _is_synthetic(device: Any) -> bool:
        """True if ``device`` is our own injector (matched by name)."""
        name = str(getattr(device, "name", "") or "").lower()
        return any(hint in name for hint in _SYNTHETIC_NAME_HINTS)

    def _run(self) -> None:
        """Thread body: drain device events until a real one trips the event."""
        evdev = self._evdev
        for device in self._devices:
            if self._stop.is_set() or self.event.is_set():
                return
            try:
                self._drain(evdev, device)
            except Exception:  # noqa: BLE001 - a device dropping out is fine
                continue

    def _drain(self, evdev: Any, device: Any) -> None:
        """Consume queued events from one device, tripping on the first real one."""
        for ev in device.read():
            if self._stop.is_set():
                return
            if self._is_user_event(evdev, ev):
                self.event.set()
                return

    def _is_user_event(self, evdev: Any, ev: Any) -> bool:
        """Classify an evdev event as genuine user takeover input.

        Any relative-motion (mouse move/scroll) event counts. A key/button event
        counts on its press edge (``value == 1``); any configured panic key trips
        regardless. ``SYN``/repeat noise is ignored.
        """
        etype = getattr(ev, "type", None)
        ecode = getattr(ev, "code", None)
        evalue = getattr(ev, "value", None)

        if etype == getattr(evdev.ecodes, "EV_REL", -1):
            return True
        if etype == getattr(evdev.ecodes, "EV_KEY", -1):
            if ecode in self._panic_keys:
                return True
            return evalue == 1
        return False
