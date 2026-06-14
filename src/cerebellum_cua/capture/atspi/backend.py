"""Linux AT-SPI2 capture backend.

Reads the live AT-SPI2 accessibility tree (the Linux analogue of Windows UIA)
through the GObject-Introspection ``Atspi`` 2.0 bindings and emits a pre-order
``CapturedElement`` stream the driver turns into matrix rows.

Hard constraints honoured here:

* **Importing this module never pulls in ``gi``/``Atspi``.** Every binding import
  is lazy, inside a method, so ``import cerebellum_cua.capture.atspi`` succeeds on any
  host (Windows, headless CI, a box with a broken a11y bus).
* **Never let Atspi's C library abort the process.** ``Atspi.init()`` can hard
  ``abort()`` when the a11y bus is unreachable. ``is_available()`` therefore
  probes the ``org.a11y.Bus`` address over D-Bus *without* calling ``Atspi.init``,
  and ``iter_tree`` guards init so a failure raises ``CaptureNotAvailable``
  instead of crashing.

The live ``Atspi.Accessible`` is adapted into the duck-typed shape that
``_convert``/``_predicate`` expect via :class:`~cerebellum_cua.capture.atspi._adapter.LiveAdapter`,
so all the mapping logic stays pure and bus-free for the unit tests.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from typing import Any

from cerebellum_cua.capture.atspi._actions import (
    do_action,
    select,
    set_text,
    set_value,
)
from cerebellum_cua.capture.atspi._adapter import LiveAdapter
from cerebellum_cua.capture.atspi._convert import convert, read_states
from cerebellum_cua.capture.atspi._predicate import atspi_should_include
from cerebellum_cua.capture.atspi._reacquire import (
    matches,
    reacquire_path,
    walk_path,
)
from cerebellum_cua.capture.base import (
    ActionNotSupported,
    CaptureBackend,
    CapturedElement,
    CaptureNode,
    CaptureNotAvailable,
)
from cerebellum_cua.config import MatrixConfig


def _probe_a11y_bus() -> bool:
    """Return True iff the org.a11y.Bus address is reachable WITHOUT Atspi.init.

    Calling ``Atspi.init()`` against a dead bus can hard-abort the process, so we
    instead ask the session bus for the a11y bus address via ``gdbus``/``dbus-send``
    in a child process. Any non-zero exit, missing tool, or empty address means
    "not available" — we never let a probe failure escalate.
    """
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            return False
    cmd = [
        "gdbus", "call", "--session",
        "--dest", "org.a11y.Bus",
        "--object-path", "/org/a11y/bus",
        "--method", "org.a11y.Bus.GetAddress",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=4, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False
    out = (result.stdout or "").strip()
    return result.returncode == 0 and bool(out) and "unix:" in out


class AtspiCaptureBackend(CaptureBackend):
    """Live AT-SPI2 capture backend (Linux)."""

    name = "atspi"

    def is_available(self) -> bool:
        """True only if ``gi``/``Atspi`` import AND the a11y bus probe succeed.

        Guards every failure mode (no bindings, no display, dead/SELinux-blocked
        bus) and NEVER triggers Atspi's C-level abort.
        """
        try:
            import gi  # noqa: F401

            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi  # noqa: F401
        except (ImportError, ValueError):
            return False
        return _probe_a11y_bus()

    def _atspi(self) -> Any:
        """Lazily import + init Atspi, raising CaptureNotAvailable on any failure."""
        if not _probe_a11y_bus():
            raise CaptureNotAvailable("AT-SPI a11y bus is not reachable")
        try:
            import gi

            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi
        except (ImportError, ValueError) as exc:
            raise CaptureNotAvailable(f"Atspi bindings unavailable: {exc}") from exc
        try:
            if Atspi.init() not in (0, 1):  # 0=ok, 1=already inited
                raise CaptureNotAvailable("Atspi.init() failed")
        except CaptureNotAvailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CaptureNotAvailable(f"Atspi.init() error: {exc}") from exc
        return Atspi

    def _roots(self, atspi: Any, target: dict[str, Any]) -> list[Any]:
        """Resolve target -> the live application/frame accessibles to walk."""
        try:
            desktop = atspi.get_desktop(0)
        except Exception as exc:  # noqa: BLE001
            raise CaptureNotAvailable(f"no AT-SPI desktop: {exc}") from exc

        app_name = target.get("app_name") or target.get("title_regex")
        pid = target.get("pid")
        roots: list[Any] = []
        try:
            count = desktop.get_child_count()
        except Exception as exc:  # noqa: BLE001
            raise CaptureNotAvailable(f"desktop unreadable: {exc}") from exc

        for i in range(count):
            try:
                app = desktop.get_child_at_index(i)
            except Exception:  # noqa: BLE001
                continue
            if app is None:
                continue
            if pid is not None:
                try:
                    if app.get_process_id() != int(pid):
                        continue
                except Exception:  # noqa: BLE001
                    continue
            if app_name:
                try:
                    if (app.get_name() or "").lower() != str(app_name).lower():
                        continue
                except Exception:  # noqa: BLE001
                    continue
            roots.append(app)
        return roots

    def iter_tree(
        self, target: dict[str, Any], config: MatrixConfig
    ) -> Iterator[CaptureNode]:
        """Pre-order DFS over the selected AT-SPI subtree(s)."""
        atspi = self._atspi()
        coord_screen = atspi.CoordType.SCREEN
        roots = self._roots(atspi, target)
        for root in roots:
            yield from self._walk(root, 0, None, coord_screen, config)

    def _walk(
        self,
        accessible: Any,
        depth: int,
        parent_key: int | None,
        coord_screen: Any,
        config: MatrixConfig,
    ) -> Iterator[CaptureNode]:
        """Recursive pre-order walk with predicate gating + depth tracking."""
        if accessible is None or depth > config.max_depth:
            return
        adapter = LiveAdapter(accessible, coord_screen)
        element = convert(adapter)
        # native_ref must be the *raw* live object so id() keys match children.
        element.native_ref = accessible

        rect = element.bounding_rect
        states = read_states(adapter)
        included = atspi_should_include(
            element.control_type,
            element.name,
            element.class_name,
            rect,
            states,
            depth,
            config,
        )
        if included:
            yield element, depth, parent_key

        # Descend regardless of self-inclusion so kept descendants aren't lost,
        # but cap by depth. Children use this node's id() when included, else the
        # nearest included ancestor's key.
        child_parent_key = id(accessible) if included else parent_key
        try:
            count = accessible.get_child_count()
        except Exception:  # noqa: BLE001
            return
        for i in range(count):
            try:
                child = accessible.get_child_at_index(i)
            except Exception:  # noqa: BLE001
                continue
            yield from self._walk(
                child, depth + 1, child_parent_key, coord_screen, config
            )

    def reacquire(self, identity: dict[str, Any]) -> CapturedElement | None:
        """Re-find an element from its stored identity (after a DB round-trip).

        ``identity`` needs ``atspi_path`` (the root-first child-index chain stored
        in ``metadata`` at capture) and, optionally, ``role`` / ``name`` for a
        loose verification. Walks ``Atspi.get_desktop(0)`` down those indices and
        returns a freshly converted :class:`CapturedElement` (with ``native_ref``
        set) for the node found there, or ``None`` on any miss or mismatch.

        Guards against the C-level abort like the rest of the backend: a dead bus,
        missing bindings, or an out-of-range index yields ``None`` rather than a
        crash.
        """
        path = reacquire_path(identity)
        if path is None:
            return None
        try:
            atspi = self._atspi()
        except CaptureNotAvailable:
            return None
        try:
            node = walk_path(atspi.get_desktop(0), path)
        except Exception:  # noqa: BLE001
            return None
        if node is None:
            return None
        adapter = LiveAdapter(node, atspi.CoordType.SCREEN)
        element = convert(adapter)
        element.native_ref = node
        return element if matches(element, identity) else None

    def invoke(
        self, element: CapturedElement, action: str = "invoke", **params: Any
    ) -> bool:
        """Execute an action on the live element via the matching AT-SPI interface.

        Supported actions: ``invoke``/``click``/``press`` (Action), ``set_text``
        (EditableText), ``toggle``/``check`` (Action), ``select`` (Selection or
        Action), ``set_value`` (Value), ``expand``/``collapse`` (Action). Raises
        :class:`ActionNotSupported` for an unknown action or a missing interface.
        """
        acc = element.native_ref
        if acc is None:
            raise ActionNotSupported("element has no live AT-SPI ref")
        try:
            return self._dispatch(acc, action, params)
        except ActionNotSupported:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ActionNotSupported(f"AT-SPI action {action!r} failed: {exc}") from exc

    @staticmethod
    def _dispatch(acc: Any, action: str, params: dict[str, Any]) -> bool:
        """Route a normalized action name to the right AT-SPI interface helper."""
        name = action.lower()
        _action_iface = (
            "invoke", "click", "press", "activate", "jump",
            "toggle", "check", "expand", "collapse",
        )
        if name in _action_iface:
            return do_action(acc, name)
        if name == "set_text":
            return set_text(acc, str(params.get("value", params.get("text", ""))))
        if name == "set_value":
            return set_value(acc, float(params["value"]))
        if name == "select":
            return select(acc)
        raise ActionNotSupported(f"atspi backend does not support action {action!r}")
