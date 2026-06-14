"""Motion-curve helpers: easing and cursor-path interpolation.

Split out of :mod:`cerebellum_cua.capture.input` to keep that module under the
~300-line cap and to isolate the one pure, side-effect-free responsibility here:
turning a (start, target, steps) request into a list of intermediate integer
cursor positions that trace an ease-in-out path. No sleeping, no event injection,
no platform dependency — just arithmetic, so it is trivially unit-testable.
"""

from __future__ import annotations


def smoothstep(t: float) -> float:
    """Ease-in-out interpolation factor for ``t`` in ``[0, 1]``.

    Classic Hermite smoothstep ``3t^2 - 2t^3``: zero slope at both ends, so a
    path sampled through it starts slow, accelerates through the middle, and
    eases to a stop — the shape of a natural pointer move. ``t`` is clamped.
    """
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return t * t * (3.0 - 2.0 * t)


def interpolate_path(
    start: tuple[int, int],
    target: tuple[int, int],
    steps: int,
) -> list[tuple[int, int]]:
    """Return ``steps`` integer waypoints from ``start`` to ``target``.

    The points follow the :func:`smoothstep` easing curve over normalized time,
    so successive segments are short near the ends and long in the middle. The
    final point is always exactly ``target`` (rounding never drifts off the
    goal). ``steps`` is coerced to at least 1; ``steps == 1`` yields a single
    jump straight to ``target``.
    """
    steps = max(1, int(steps))
    sx, sy = start
    tx, ty = target
    if steps == 1:
        return [(int(tx), int(ty))]

    points: list[tuple[int, int]] = []
    for i in range(1, steps + 1):
        factor = smoothstep(i / steps)
        x = round(sx + (tx - sx) * factor)
        y = round(sy + (ty - sy) * factor)
        points.append((int(x), int(y)))
    points[-1] = (int(tx), int(ty))
    return points
