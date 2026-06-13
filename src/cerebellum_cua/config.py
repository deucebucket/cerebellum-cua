"""Traversal / extraction configuration.

``MatrixConfig`` carries every knob consumed by the should_include predicate and
the traversal engine (spec Section 2, and the build_matrix ``config`` block in the
v4.2 JSONL contract, Section 4). Defaults match the spec's documented defaults.

The config is a plain dataclass so it is trivially serializable to/from the JSON
``config`` payload via ``from_dict`` / ``to_dict``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any

# Default ControlType ids pruned outright (scrollbar, spinner, status bar,
# separator, tooltip — raw UIA ints). Overridable per request.
_DEFAULT_EXCLUDED_CONTROL_TYPES: tuple[int, ...] = (50014, 50016, 50017, 50038, 50022)

_DEFAULT_NOISE_SUBSTRINGS: tuple[str, ...] = (
    "scroll", "grip", "resize", "shadow", "overlay",
    "background", "decoration", "spacer", "splitter", "track", "thumb", "caret",
)


@dataclass(slots=True)
class MatrixConfig:
    """Filtering + traversal configuration. All fields have spec defaults."""

    # Depth / size bounds.
    max_depth: int = 14
    max_elements: int = 12000
    browser_max_depth: int = 9

    # Inclusion toggles (default = prune for a lean matrix).
    interactive_only: bool = False
    include_invisible: bool = False
    include_offscreen: bool = False
    include_zero_sized: bool = False
    include_negative_rect: bool = False
    include_non_content: bool = True
    include_anonymous_leaves: bool = False
    force_include_noise: bool = False
    force_full_browser_tree: bool = False

    # Failure-mode policy.
    include_on_rect_error: bool = True
    fail_open_on_predicate_error: bool = True

    # Noise filters.
    excluded_control_types: tuple[int, ...] = _DEFAULT_EXCLUDED_CONTROL_TYPES
    noise_substrings: tuple[str, ...] = _DEFAULT_NOISE_SUBSTRINGS

    # Free-form target/build metadata echoed into the snapshot.
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> MatrixConfig:
        """Build from a (possibly partial) JSON config payload, ignoring extras."""
        d = dict(d or {})
        known = {f.name for f in fields(cls)}
        kwargs: dict[str, Any] = {}
        for key in list(d):
            if key in known and key != "extra":
                kwargs[key] = d.pop(key)
        if "excluded_control_types" in kwargs:
            kwargs["excluded_control_types"] = tuple(kwargs["excluded_control_types"])
        if "noise_substrings" in kwargs:
            kwargs["noise_substrings"] = tuple(kwargs["noise_substrings"])
        cfg = cls(**kwargs)
        cfg.extra = d  # unknown keys preserved for forward-compat
        return cfg

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in fields(self):
            if f.name == "extra":
                continue
            val = getattr(self, f.name)
            out[f.name] = list(val) if isinstance(val, tuple) else val
        out.update(self.extra)
        return out
