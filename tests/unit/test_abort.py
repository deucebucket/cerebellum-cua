"""Unit tests for the user-takeover kill-switch — no real /dev/input, no evdev.

A :class:`FakeEvdev` stands in for the ``evdev`` module: it exposes a device list,
an ``ecodes`` namespace, and devices that emit a scripted sequence of synthetic
events. Tests assert the watcher trips on a real key/mouse event, ignores events
from the excluded synthetic device, trips on a panic key, and degrades to a
no-op (``available is False``) when the evdev import fails.

The thread is driven deterministically: ``start()`` runs the monitor, then the
test joins via ``stop()`` (short timeout), so there are no real sleeps.
"""

from __future__ import annotations

from typing import Any

from cerebellum_cua.capture.abort import AbortWatcher


class _Ecodes:
    EV_KEY = 1
    EV_REL = 2
    EV_SYN = 0


class _Event:
    def __init__(self, etype: int, code: int, value: int) -> None:
        self.type = etype
        self.code = code
        self.value = value


class _FakeDevice:
    def __init__(self, path: str, name: str, events: list[_Event]) -> None:
        self.path = path
        self.name = name
        self._events = events
        self.closed = False

    def read(self) -> list[_Event]:
        return list(self._events)

    def close(self) -> None:
        self.closed = True


class FakeEvdev:
    """Injectable stand-in for the ``evdev`` module."""

    ecodes = _Ecodes

    def __init__(self, devices: dict[str, _FakeDevice]) -> None:
        self._devices = devices

    def list_devices(self) -> list[str]:
        return list(self._devices)

    def InputDevice(self, path: str) -> _FakeDevice:  # noqa: N802 - mirror evdev API
        return self._devices[path]


def _watch(devices: dict[str, _FakeDevice], **kw: object) -> AbortWatcher:
    w = AbortWatcher(evdev_module=FakeEvdev(devices), **kw)  # type: ignore[arg-type]
    w.start()
    w.stop()  # joins the (daemon) monitor thread deterministically
    return w


def test_trips_on_real_key_press() -> None:
    dev = _FakeDevice("/dev/input/event0", "USB Keyboard",
                      [_Event(_Ecodes.EV_KEY, 30, 1)])  # 'a' press
    w = _watch({"/dev/input/event0": dev})
    assert w.available is True
    assert w.triggered is True
    assert w.event.is_set() is True


def test_trips_on_mouse_motion() -> None:
    dev = _FakeDevice("/dev/input/event1", "USB Mouse",
                      [_Event(_Ecodes.EV_REL, 0, 5)])  # REL_X move
    w = _watch({"/dev/input/event1": dev})
    assert w.triggered is True


def test_ignores_key_release_only() -> None:
    dev = _FakeDevice("/dev/input/event0", "USB Keyboard",
                      [_Event(_Ecodes.EV_KEY, 30, 0)])  # 'a' release (value 0)
    w = _watch({"/dev/input/event0": dev})
    assert w.triggered is False


def test_ignores_synthetic_ydotool_device() -> None:
    dev = _FakeDevice("/dev/input/event9", "ydotoold virtual device",
                      [_Event(_Ecodes.EV_KEY, 30, 1)])
    w = _watch({"/dev/input/event9": dev})
    # The only device is our own injector -> nothing to watch -> no-op.
    assert w.triggered is False
    assert w.available is False
    assert dev.closed is True


def test_ignores_synthetic_uinput_device_among_real() -> None:
    synth = _FakeDevice("/dev/input/event9", "py-uinput-fake",
                        [_Event(_Ecodes.EV_KEY, 30, 1)])
    real = _FakeDevice("/dev/input/event0", "Real Keyboard", [])  # no events
    w = _watch({"/dev/input/event9": synth, "/dev/input/event0": real})
    # Synthetic device's press is ignored; real device emitted nothing.
    assert w.triggered is False
    assert w.available is True


def test_trips_on_panic_key_space() -> None:
    dev = _FakeDevice("/dev/input/event0", "Keyboard",
                      [_Event(_Ecodes.EV_KEY, 57, 1)])  # KEY_SPACE
    w = _watch({"/dev/input/event0": dev})
    assert w.triggered is True


def test_trips_on_panic_key_esc() -> None:
    dev = _FakeDevice("/dev/input/event0", "Keyboard",
                      [_Event(_Ecodes.EV_KEY, 1, 1)])  # KEY_ESC
    w = _watch({"/dev/input/event0": dev})
    assert w.triggered is True


def test_custom_panic_key() -> None:
    # KEY_ESC release (value 0) would normally be ignored; mark it a panic key.
    dev = _FakeDevice("/dev/input/event0", "Keyboard",
                      [_Event(_Ecodes.EV_KEY, 1, 0)])
    w = _watch({"/dev/input/event0": dev}, panic_keys=(1,))
    assert w.triggered is True


def test_unavailable_when_evdev_import_fails(monkeypatch: Any) -> None:
    # Force the import-failure branch deterministically, regardless of whether
    # evdev happens to be installed on the host.
    import cerebellum_cua.capture.abort as mod

    monkeypatch.setattr(mod, "_load_evdev", lambda: None)
    w = AbortWatcher(evdev_module=None)
    assert w.available is False
    w.start()  # no-op
    w.stop()
    assert w.available is False
    assert w.triggered is False


def test_no_readable_devices_degrades() -> None:
    w = _watch({})  # evdev present but zero devices
    assert w.available is False
    assert w.triggered is False
