"""Authoring model for tutorials: an ordered list of captioned steps.

A *tutorial* is a small, declarative how-to that drives a real application while
narrating each step with an on-screen caption. The model is intentionally plain
so a tutorial can be authored as JSON and round-tripped without loss.

* :class:`TutorialStep` — one captioned step: a caption, what to do
  (``"skill"`` runs a named skill, ``"op"`` runs an engine operation handler,
  ``"pause"`` just holds the caption on screen), the target name, its args, and
  how long the caption should hold.
* :class:`Tutorial` — a titled, ordered list of steps with ``from_dict`` /
  ``to_dict`` so the whole thing serializes to a JSON document.

This module imports nothing from the rest of the package, so it is import-safe
on any host and usable from tests without a live engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: The three step kinds. ``skill`` -> run_skill, ``op`` -> that handler,
#: ``pause`` -> hold the caption with no action.
STEP_ACTIONS: tuple[str, ...] = ("skill", "op", "pause")


@dataclass(slots=True)
class TutorialStep:
    """One captioned step of a tutorial.

    Attributes:
        caption: Human-readable text drawn on screen during the step.
        action: One of :data:`STEP_ACTIONS` (``"skill"``/``"op"``/``"pause"``).
        name: Skill name (for ``skill``) or operation name (for ``op``).
            Ignored for ``pause``.
        args: Argument dict passed to the skill/operation handler.
        hold: Seconds the caption stays on screen (also the ``pause`` duration).
    """

    caption: str
    action: str = "pause"
    name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    hold: float = 2.0

    def __post_init__(self) -> None:
        """Validate the action kind so a bad tutorial fails at load, not at run."""
        if self.action not in STEP_ACTIONS:
            raise ValueError(
                f"unknown step action {self.action!r}; expected one of {STEP_ACTIONS}"
            )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TutorialStep:
        """Build a step from a plain dict (e.g. one JSON tutorial entry)."""
        return cls(
            caption=str(data["caption"]),
            action=str(data.get("action", "pause")),
            name=str(data.get("name", "")),
            args=dict(data.get("args") or {}),
            hold=float(data.get("hold", 2.0)),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the step back to a plain JSON-ready dict."""
        return {
            "caption": self.caption,
            "action": self.action,
            "name": self.name,
            "args": dict(self.args),
            "hold": self.hold,
        }


@dataclass(slots=True)
class Tutorial:
    """A titled, ordered sequence of :class:`TutorialStep` steps."""

    title: str
    steps: list[TutorialStep] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Tutorial:
        """Build a tutorial from a plain dict (a parsed JSON document)."""
        return cls(
            title=str(data["title"]),
            steps=[TutorialStep.from_dict(s) for s in data.get("steps") or []],
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the tutorial back to a plain JSON-ready dict."""
        return {"title": self.title, "steps": [s.to_dict() for s in self.steps]}


__all__ = ["Tutorial", "TutorialStep", "STEP_ACTIONS"]
