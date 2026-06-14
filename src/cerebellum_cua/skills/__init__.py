"""Skills / macros layer: named high-level actions over the existing pieces.

A *skill* resolves a target element, acts on it, and (optionally) verifies the
result — composing the resolver, the latest snapshot, and the engine's
``invoke_action`` handler rather than reimplementing any of them.

* :func:`find_elements` / :func:`find_one` — pure element resolution from a query.
* :data:`SKILLS` — the built-in skill registry (``click``, ``type_into``,
  ``open``, ``focus``, ``read``).
* :func:`run_skill` — dispatch a skill by name with an args dict.

The package imports only the shared model + lower layers, so it is import-safe on
any host (no live capture is triggered by importing it).
"""

from __future__ import annotations

from cerebellum_cua.skills.builtin import SKILLS, run_skill
from cerebellum_cua.skills.resolver import find_elements, find_one

__all__ = ["find_elements", "find_one", "SKILLS", "run_skill"]
