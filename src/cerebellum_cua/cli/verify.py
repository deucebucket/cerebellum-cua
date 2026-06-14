"""Action verification: re-capture after an action and report observed change.

The agent that drives the UI cannot see a screen — it must infer whether an
action worked from the matrix. This module provides the opt-in "act, then look"
step: after :func:`~cerebellum_cua.cli.invoke.perform_action` runs, re-capture the
tree of the same target (via :meth:`CuaEngine.recapture`), diff it against the
pre-action snapshot with :func:`~cerebellum_cua.matrix.diff_snapshots`, and annotate
the result with whether anything observably changed.

Design constraints:

* **Opt-in, off by default.** Verification runs only when the engine's
  ``verify_actions`` flag is set or the ``invoke_action`` payload has
  ``"verify": true``. Existing behavior/tests are untouched.
* **Never raises on no-change.** An action with no observable effect reports
  ``verified=False`` / ``effect="no_change"`` so the agent can retry or adapt —
  it is data, not an error.
* **Bounded payload.** Only compact row-id lists are returned (not full element
  patches); the heavy diff stays internal.
* **Degrades cleanly.** If re-capture is impossible (no prior capture context,
  headless, no backend) the result is ``verified=None`` with a ``reason``.

This module reuses the existing capture + diff layers; it reimplements neither.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cerebellum_cua.matrix import diff_snapshots
from cerebellum_cua.model import Snapshot

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cerebellum_cua.cli.engine import CuaEngine


def should_verify(engine: CuaEngine, payload: dict[str, Any]) -> bool:
    """True when this action should be verified (payload opt-in or engine flag).

    A payload ``"verify"`` key (truthy/falsy) always wins; otherwise the engine's
    ``verify_actions`` default applies.
    """
    if "verify" in payload:
        return bool(payload["verify"])
    return bool(getattr(engine, "verify_actions", False))


def verify_action(engine: CuaEngine, before: Snapshot | None) -> dict[str, Any]:
    """Re-capture and diff against ``before``; return the verification fields.

    Returns a dict to merge into the ``invoke_action`` response::

        {"verified": bool | None,
         "effect": "changed" | "no_change" | "unknown",
         "observed_change": {"added_row_ids", "removed_row_ids",
                             "modified_row_ids"},   # present when verified is bool
         "reason": str}                              # present when verified is None

    ``verified`` is ``None`` (with a ``reason``) when no comparison was possible,
    ``True`` when the UI observably changed, and ``False`` when it did not.
    """
    if before is None:
        return _unknown("no_pre_action_snapshot")
    after = engine.recapture()
    if after is None:
        return _unknown("recapture_unavailable")

    delta = diff_snapshots(before, after)
    observed = {
        "added_row_ids": delta["added_row_ids"],
        "removed_row_ids": delta["removed_row_ids"],
        "modified_row_ids": delta["modified_row_ids"],
    }
    changed = any(observed.values())
    return {
        "verified": changed,
        "effect": "changed" if changed else "no_change",
        "observed_change": observed,
    }


def _unknown(reason: str) -> dict[str, Any]:
    """Verification could not run: report ``verified=None`` with a reason."""
    return {"verified": None, "effect": "unknown", "reason": reason}
