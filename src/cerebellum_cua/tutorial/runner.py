"""Run a tutorial against a live engine, recording a captioned timeline.

:func:`run_tutorial` executes each :class:`~cerebellum_cua.tutorial.spec.TutorialStep`
in order through the engine's operation handlers and records, per step, the
caption and the wall-clock window it occupied. The window offsets are taken from
an injectable ``clock`` so tests can drive deterministic timings without sleeping.

Dispatch is uniform and reuses the existing handler surface:

* ``skill`` -> the ``run_skill`` handler with ``{"skill": name, "args": args}``.
* ``op``    -> the handler registered under ``name`` with ``args`` as its payload.
* ``pause`` -> no action; the caption simply holds for ``hold`` seconds.

A step that raises is recorded as ``ok=false`` (with the error text summarized)
and the run continues — a tutorial never crashes the recording session. The
returned timeline is exactly what :mod:`cerebellum_cua.tutorial.captions` needs
to overlay the captions onto the recorded video.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from cerebellum_cua.gateway.budget import estimate_tokens
from cerebellum_cua.tutorial.spec import Tutorial, TutorialStep

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cerebellum_cua.cli.engine import CuaEngine

#: A monotonic, no-arg clock returning seconds as a float.
Clock = Callable[[], float]


def run_tutorial(
    engine: CuaEngine,
    tutorial: Tutorial,
    clock: Clock = time.perf_counter,
) -> dict[str, Any]:
    """Run ``tutorial`` step-by-step, returning a captioned timeline.

    Args:
        engine: The engine whose ``handlers`` dispatch skills and operations.
        tutorial: The authored :class:`Tutorial` to run.
        clock: A monotonic no-arg clock (injected for deterministic tests).

    Returns:
        ``{"title", "timeline", "success"}`` where ``timeline`` is a list of
        ``{"caption", "start", "end", "ok", "result_summary"}`` entries with
        ``start``/``end`` as second offsets from the first step, and ``success``
        is true only when every step succeeded.
    """
    origin = clock()
    timeline: list[dict[str, Any]] = []
    success = True
    for step in tutorial.steps:
        entry = _run_step(engine, step, clock, origin)
        timeline.append(entry)
        success = success and bool(entry["ok"])
    totals = {"a11y_tokens": sum(int(e["tokens"]) for e in timeline)}
    return {
        "title": tutorial.title,
        "timeline": timeline,
        "success": success,
        "totals": totals,
    }


def _run_step(
    engine: CuaEngine, step: TutorialStep, clock: Clock, origin: float
) -> dict[str, Any]:
    """Execute one step and build its timeline entry (never raises)."""
    start = clock() - origin
    ok = True
    summary = ""
    tokens = 0
    perceived = ""
    try:
        result = _dispatch(engine, step)
        ok = _step_ok(result)
        summary = _summarize(result)
        tokens = estimate_tokens(result) if result is not None else 0
        perceived = _perceived(result)
    except Exception as exc:  # noqa: BLE001 - a step must never crash the run
        ok = False
        summary = f"error: {type(exc).__name__}: {exc}"
    end = max(clock() - origin, start + step.hold)
    return {
        "caption": step.caption,
        "start": round(start, 3),
        "end": round(end, 3),
        "ok": ok,
        "tokens": tokens,
        "perceived": perceived,
        "result_summary": summary,
    }


def _perceived(result: Any) -> str:
    """Best-effort label of the element a step acted on / perceived."""
    if not isinstance(result, dict):
        return ""
    for key in ("name", "target", "resolved", "title"):
        val = result.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _dispatch(engine: CuaEngine, step: TutorialStep) -> Any:
    """Run one step's action and return its raw result (or ``None`` for pause)."""
    if step.action == "pause":
        return None
    if step.action == "skill":
        return engine.handlers["run_skill"](
            {"skill": step.name, "args": dict(step.args)}
        )
    handler = engine.handlers.get(step.name)
    if handler is None:
        raise KeyError(f"unknown operation {step.name!r}")
    return handler(dict(step.args))


def _step_ok(result: Any) -> bool:
    """Decide whether a step result counts as a success.

    A ``pause`` (``None``) is always ok. A handler result that carries an explicit
    ``success`` flag is judged by it; any other non-``None`` result is treated as
    a success (the handler returned a payload rather than raising).
    """
    if result is None:
        return True
    if isinstance(result, dict) and "success" in result:
        return bool(result["success"])
    return True


def _summarize(result: Any) -> str:
    """Build a short, single-line summary of a step result for the timeline."""
    if result is None:
        return "pause"
    if isinstance(result, dict):
        keys = ", ".join(sorted(result)[:6])
        return f"keys: {keys}" if keys else "{}"
    return str(result)[:120]


__all__ = ["run_tutorial", "Clock"]
