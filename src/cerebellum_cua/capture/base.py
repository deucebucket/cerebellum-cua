"""Universal capture-backend contract.

Cerebellum CUA is platform-neutral below this seam. A ``CaptureBackend`` reads the
live accessibility tree of whatever OS it runs on and emits a normalized,
pre-order stream of ``CapturedElement`` records. Everything downstream (matrix,
storage, gateway, protocol) consumes those records and never knows which OS
produced them.

Backends:
  - Windows -> UIA  (cerebellum_cua.capture.uia_backend, wraps cerebellum_cua.uia)
  - Linux   -> AT-SPI (cerebellum_cua.capture.atspi)
  - macOS   -> AX    (future)

A backend owns ONLY live capture + action execution. It does not assign matrix
row ids (the driver does), touch the DB, or make protocol decisions.

``control_type`` on a CapturedElement is the canonical cross-platform taxonomy:
the integer values of ``cerebellum_cua.model.ControlType`` (UIA-derived). Each backend
maps its native roles into that taxonomy so semantics/predicates are uniform.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Hashable, Iterator
from dataclasses import dataclass, field
from typing import Any

from cerebellum_cua.config import MatrixConfig
from cerebellum_cua.model import BoundingRect


@dataclass(slots=True)
class CapturedElement:
    """One normalized element from a live accessibility tree (OS-agnostic).

    Field shapes match what ``cerebellum_cua.matrix.build_snapshot`` expects so a
    captured element dict can be fed straight in. ``native_ref`` is an opaque,
    backend-private handle (e.g. a COM element or an Atspi.Accessible) the
    backend may use later for action execution; it is never persisted.
    """

    control_type: int
    name: str = ""
    class_name: str = ""
    automation_id: str = ""
    runtime_id: list[int] | None = None
    uia_runtime_id_hash: str = ""
    bounding_rect: BoundingRect = field(default_factory=BoundingRect)
    properties: dict[str, Any] = field(default_factory=dict)
    patterns: dict[str, Any] = field(default_factory=dict)
    is_interactive: bool = False
    is_content: bool = False
    framework_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    native_ref: Any = None

    def to_element_data(self) -> dict[str, Any]:
        """Return the dict shape consumed by ``build_snapshot`` (no native_ref)."""
        data: dict[str, Any] = {
            "control_type": self.control_type,
            "name": self.name,
            "class_name": self.class_name,
            "automation_id": self.automation_id,
            "bounding_rect": self.bounding_rect,
            "properties": self.properties,
            "patterns": self.patterns,
            "is_interactive": self.is_interactive,
            "is_content": self.is_content,
            "framework_id": self.framework_id,
            "metadata": self.metadata,
        }
        if self.uia_runtime_id_hash:
            data["uia_runtime_id_hash"] = self.uia_runtime_id_hash
        elif self.runtime_id is not None:
            data["runtime_id"] = self.runtime_id
        return data


# A single yielded node: (element, depth, parent_key). parent_key is any stable,
# hashable identifier for the parent node within THIS walk (e.g. id(native) or a
# runtime-id tuple); the driver maps it to the parent's assigned matrix row id.
# parent_key is None for root(s).
CaptureNode = tuple[CapturedElement, int, Hashable | None]


class CaptureNotAvailable(RuntimeError):
    """Raised when a backend cannot run on the current host/session."""


class ActionNotSupported(RuntimeError):
    """Raised when a backend cannot perform a requested action."""


class CaptureBackend(ABC):
    """Reads a live accessibility tree and executes element actions."""

    #: short stable identifier, e.g. "uia" or "atspi".
    name: str = "base"

    @abstractmethod
    def is_available(self) -> bool:
        """True if this backend can actually run here (OS, libs, live bus)."""

    @abstractmethod
    def iter_tree(
        self, target: dict[str, Any], config: MatrixConfig
    ) -> Iterator[CaptureNode]:
        """Yield ``(CapturedElement, depth, parent_key)`` in PRE-ORDER.

        Parents MUST be yielded before their children. ``target`` selects the
        root (keys like exe_regex / title_regex / pid / hwnd / app_name; an empty
        dict means the whole desktop). Should raise ``CaptureNotAvailable`` if the
        backend cannot run here.
        """

    def reacquire(self, identity: dict[str, Any]) -> CapturedElement | None:
        """Re-find a live element from a persisted identity (post-DB round-trip).

        ``identity`` carries whatever the backend stored at capture time to locate
        the node again without a live handle (e.g. an AT-SPI child-index path, or
        Name + ControlType for UIA). Returns a ``CapturedElement`` with a fresh
        ``native_ref``, or ``None`` if the element cannot be re-found here. Default
        returns ``None``; live backends override.
        """
        return None

    def invoke(self, element: CapturedElement, action: str = "invoke", **params: Any) -> bool:
        """Execute an action (click/invoke/set_text/...) on a captured element.

        Default raises ActionNotSupported; live backends override. Uses the
        element's ``native_ref`` (re-acquiring it if stale is the backend's job).
        """
        raise ActionNotSupported(f"{self.name} backend does not support action {action!r}")
