"""Stable, content-addressable element identity (spec Failure 3).

Raw ``IUIAutomationElement`` COM pointers go stale after any UI mutation, so the
matrix never addresses elements by pointer. Instead every accepted element gets a
deterministic composite key derived from its stable visible properties:

    (Name or "", ClassName or "", ControlType, rounded BoundingRectangle, parent)

The rectangle is rounded to the nearest 8px before hashing so sub-pixel / minor
layout jitter does not change identity (Failure 3). The tuple is serialized
canonically, SHA-256 hashed, and truncated to 16 bytes (32 hex chars).

Pure logic: stdlib + ``cerebellum_cua.model`` only. No COM, no DB, no randomness, no
clock — identical inputs always yield identical output.
"""

from __future__ import annotations

import hashlib

from cerebellum_cua.model import BoundingRect

# Rectangle quantization grid, in device-independent pixels (Failure 3).
_RECT_GRID_PX = 8

# Truncation length of the composite digest, in bytes (-> 32 hex chars).
_KEY_BYTES = 16


def _round_to_grid(value: int, grid: int = _RECT_GRID_PX) -> int:
    """Round ``value`` to the nearest multiple of ``grid`` (banker-free, symmetric)."""
    if grid <= 1:
        return int(value)
    half = grid // 2
    if value >= 0:
        return ((value + half) // grid) * grid
    # Mirror the rounding for negatives so -4 -> -8 matches +4 -> +8 symmetry.
    return -(((-value) + half) // grid) * grid


def _rounded_rect(rect: BoundingRect) -> tuple[int, int, int, int]:
    """Quantize a rect's geometry to the 8px grid; DPI is intentionally excluded."""
    return (
        _round_to_grid(int(rect.left)),
        _round_to_grid(int(rect.top)),
        _round_to_grid(int(rect.width)),
        _round_to_grid(int(rect.height)),
    )


def composite_key(
    name: str | None,
    class_name: str | None,
    control_type: int,
    rect: BoundingRect,
    parent_row_id: int | None,
) -> str:
    """Return the 32-hex-char composite identity for an element (Failure 3).

    Deterministic for a given tuple of stable properties. ``name`` / ``class_name``
    coalesce to ``""`` so missing values hash consistently. The rect is rounded to
    the nearest 8px so minor positional jitter preserves identity.
    """
    rounded = _rounded_rect(rect)
    # Unit-separator joins; values are str()'d so None/ints serialize unambiguously.
    parts = (
        name or "",
        class_name or "",
        str(int(control_type)),
        str(rounded[0]),
        str(rounded[1]),
        str(rounded[2]),
        str(rounded[3]),
        "" if parent_row_id is None else str(int(parent_row_id)),
    )
    payload = "\x1f".join(parts).encode("utf-8")
    digest = hashlib.sha256(payload).digest()[:_KEY_BYTES]
    return digest.hex()


def runtime_id_hash(runtime_id: list[int] | None) -> str:
    """Hash a UIA RuntimeId array to a stable 32-hex-char key (or "" if absent).

    RuntimeId is a per-session-stable int array that uniquely identifies a live
    element while the provider stays up; we hash it so the diff layer can match
    elements across epochs without retaining the array itself.
    """
    if not runtime_id:
        return ""
    payload = ",".join(str(int(part)) for part in runtime_id).encode("utf-8")
    return hashlib.sha256(payload).digest()[:_KEY_BYTES].hex()
