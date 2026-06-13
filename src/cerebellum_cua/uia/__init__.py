"""UIA (Windows UI Automation COM) layer for Cerebellum CUA.

This is the ONLY package allowed to touch Windows-only imports
(``uiautomation``, ``comtypes``, ``ctypes.windll``), and every such import is
lazy/guarded inside :class:`UiaClient`, so importing this package always succeeds
on Linux. Everything else here (the predicate, pattern map, resolver, stabilizer,
traversal) operates purely on duck-typed element wrappers and plain dataclasses.

Public surface:
  * :func:`should_include` — the mandatory filtering predicate.
  * :data:`PATTERN_MAP`, :func:`safe_get_property`, :func:`extract_patterns`.
  * :func:`resolve_stale_element` — Failure 1 stale-pointer re-acquisition.
  * :func:`stabilize_virtualized` — Failure 2 virtualized-subtree realization.
  * :func:`walk` — breadth-first traversal yielding kept ``(element, depth, parent)``.
  * :class:`UiaClient`, :func:`control_type_name` — the live capture facade.
"""

from __future__ import annotations

from cerebellum_cua.uia.client import UiaClient, control_type_name
from cerebellum_cua.uia.patterns import PATTERN_MAP, extract_patterns, safe_get_property
from cerebellum_cua.uia.predicate import should_include
from cerebellum_cua.uia.resolver import resolve_stale_element
from cerebellum_cua.uia.stabilize import stabilize_virtualized
from cerebellum_cua.uia.traversal import walk

__all__ = [
    "should_include",
    "PATTERN_MAP",
    "extract_patterns",
    "safe_get_property",
    "resolve_stale_element",
    "stabilize_virtualized",
    "walk",
    "UiaClient",
    "control_type_name",
]
