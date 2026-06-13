"""The mandatory ``should_include`` filtering predicate (spec Part 1, Section 2).

This is the config-driven, 5-argument Postgres-draft version, implemented
*verbatim* in logic and ordering. Every Cerebellum CUA traversal path (initial build
and incremental patch) must run it to prune noise — scrollbars, 1x1 hit-test
elements, offscreen chrome, decorative panes, browser overlay junk — while always
keeping structural anchors and anything carrying identity, children, or value.

The ``element`` argument is a *duck-typed* UIA control wrapper. We never import
``uiautomation`` here; we only read the element's attributes/methods defensively.
ControlType values are the raw Microsoft UIA integer constants (mirrored by
``model.ControlType``). On any unexpected error the predicate fails open
(includes the element) when ``config.fail_open_on_predicate_error`` is set, so a
downstream agent can decide rather than silently dropping signal.

Returns ``True`` only for elements that contribute meaningful identity,
interaction surface, or data content to the canonical relational matrix. Must be
called after the CacheRequest has already been applied to the element.
"""

from __future__ import annotations

from typing import Any

import cerebellum_cua.uia._predicate_rules as rules
from cerebellum_cua.config import MatrixConfig
from cerebellum_cua.model import ControlType

_BROWSER_DOC_FRAMEWORKS = {"chrome", "edge", "mozilla"}


def should_include(
    element: Any,
    depth: int,
    parent_matrix_id: int | None,
    matrix_context: dict[str, Any],
    config: MatrixConfig,
) -> bool:
    """Return True iff ``element`` belongs in the canonical relational matrix.

    Args:
        element: Duck-typed UIA control wrapper (or None).
        depth: 0-based depth from the traversal root.
        parent_matrix_id: Matrix row id of the parent (unused in filtering;
            carried for parity with the spec signature and telemetry).
        matrix_context: Free-form traversal context (carried for parity).
        config: Active :class:`MatrixConfig` supplying every inclusion knob.
    """
    if element is None:
        return False

    # Hard global depth guard (prevents stack blow-up on pathological trees).
    if depth > config.max_depth:
        return False

    try:
        # --- ControlType fast-path exclusions (noise that never carries semantics) ---
        verdict = rules.check_control_type(element, config)
        if verdict is not None:
            return verdict
        ct = element.ControlType

        # --- BoundingRectangle sanity and size filter ---
        verdict = rules.check_bounding_rect(element, config)
        if verdict is not None:
            return verdict

        # --- Visibility and offscreen handling (fails open on read error) ---
        verdict = rules.check_visibility(element, config)
        if verdict is not None:
            return verdict

        # --- Name / ClassName / AutomationId noise keyword filter ---
        name, classname, automation_id = rules.read_identity(element)
        verdict = rules.check_noise_substrings(name, classname, config)
        if verdict is not None:
            return verdict

        # --- Framework-specific early exits and forced inclusions ---
        framework = rules.read_framework(element)
        verdict = rules.check_framework(element, framework, ct, depth, config)
        if verdict is not None:
            return verdict

        # --- Interactive / content element scoring (used by interactive_only mode) ---
        interactive = rules.compute_interactive(element)
        if config.interactive_only and not interactive and depth > 1:
            return False

        # --- Empty leaf pruning (childless, unlabelled containers are noise) ---
        child_count = rules.read_child_count(element)
        verdict = rules.check_empty_leaf(
            child_count, name, automation_id, interactive, ct, depth
        )
        if verdict is not None:
            return verdict

        # --- Special always-include rules for structural anchors ---
        if depth <= 1:
            return True
        if ct == int(ControlType.WINDOW):
            return True
        if ct == int(ControlType.DOCUMENT) and framework in _BROWSER_DOC_FRAMEWORKS:
            return True

        # --- Final content-element gate (IsContentElement is advisory but useful) ---
        is_content = rules.read_is_content_element(element)
        if is_content is False and not interactive and not config.include_non_content:
            return False

        # --- Cerebellum CUA specific: require identity OR children OR value ---
        has_identity = bool(name or automation_id or interactive)
        has_children = child_count > 0
        has_value = rules.read_has_value(element)

        if not (has_identity or has_children or has_value):
            if not config.include_anonymous_leaves:
                return False

        return True

    except Exception:  # noqa: BLE001 - mirror spec's fail-open final guard
        # Fail-open policy: on any unexpected exception during predicate
        # evaluation we still include the element so the downstream CLI agent can
        # decide; telemetry records the exception type and element RuntimeId hash.
        if config.fail_open_on_predicate_error:
            return True
        return False
