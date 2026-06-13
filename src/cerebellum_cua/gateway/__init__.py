"""Gateway layer: token-bounded accordion + JSONL v4.2 protocol.

This is the seam downstream CLI/LLM agents talk to. It reads exclusively from a
:class:`~cerebellum_cua.storage.base.StorageBackend` — it never walks the live UIA
tree and never imports the uia layer.

- :class:`LazyTokenCodec` — HS256 lazy-load token codec (300s TTL).
- :class:`Accordion` — lazy expansion of a persisted snapshot's element tree.
- :class:`Protocol` — JSONL framing + operation dispatch (engine handlers injected).
"""

from __future__ import annotations

from cerebellum_cua.gateway.accordion import Accordion
from cerebellum_cua.gateway.protocol import Protocol
from cerebellum_cua.gateway.tokens import LazyTokenCodec

__all__ = ["LazyTokenCodec", "Accordion", "Protocol"]
