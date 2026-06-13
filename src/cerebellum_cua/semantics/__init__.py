"""Semantics layer: map raw UIA elements to high-level domain concepts.

Heuristic rule engine translating UIA primitives (Button, Edit, MenuItem, …) into
domain concepts (action_button, text_input, menu_item, …). Pure logic over the
shared :mod:`cerebellum_cua.model` dataclasses — no COM, storage, or gateway imports.
"""

from __future__ import annotations

from cerebellum_cua.semantics.mappings import (
    SEED_MAPPINGS,
    SemanticMapping,
    match_element,
)

__all__ = ["SEED_MAPPINGS", "SemanticMapping", "match_element"]
