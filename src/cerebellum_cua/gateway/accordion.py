"""Hierarchical accordion lazy-loading over the stored matrix (spec Section 4).

The accordion exposes the relational matrix as an expandable tree. It reads
exclusively from a :class:`~cerebellum_cua.storage.base.StorageBackend` — it never
walks the live UIA tree. Root elements (depth 0-1) are hydrated inline by
:meth:`Accordion.get_initial_context`; deeper levels are fetched on demand by
:meth:`Accordion.load_children` against a single-use lazy token that the
accordion issues, signs (via :class:`LazyTokenCodec`), and persists through the
backend's ``save_lazy_token`` (300s server-side TTL).

Token discipline (spec): a token is single-use for a given parent within an
epoch; reuse after expiry is rejected. We validate both the JWT signature/expiry
*and* the backend's server-side record before loading any children.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from cerebellum_cua.errors import (
    ElementNotFoundError,
    InvalidLazyTokenError,
    SnapshotNotFoundError,
)
from cerebellum_cua.gateway._hydrate import element_to_dict, semantics_to_list
from cerebellum_cua.gateway.tokens import LazyTokenCodec
from cerebellum_cua.model import ChildStub, Element
from cerebellum_cua.storage.base import StorageBackend

#: Soft cap on children returned per expansion (spec: <=40 visible, position-sorted).
CHILD_PAGE_LIMIT = 500


class Accordion:
    """Token-bounded lazy expansion of a persisted snapshot's element tree."""

    def __init__(self, storage: StorageBackend, codec: LazyTokenCodec) -> None:
        self._storage = storage
        self._codec = codec

    # --- public operations ----------------------------------------------
    def get_initial_context(self, snapshot_id: int) -> dict[str, Any]:
        """Return root elements (depth <= 1) hydrated with grandchild stubs.

        Each root carries a ``children_stub`` whose ``lazy_token`` (when it has
        children and depth budget remains) is freshly issued and persisted so the
        agent can expand it via :meth:`load_children`.
        """
        roots = self._fetch_roots(snapshot_id)
        if not roots:
            raise SnapshotNotFoundError(snapshot_id=snapshot_id)
        hydrated = [
            self._hydrate(snapshot_id, el, max_depth=1) for el in roots
        ]
        return {
            "snapshot_id": snapshot_id,
            "root_elements": hydrated,
            "total_roots": len(hydrated),
        }

    def load_children(
        self,
        snapshot_id: int,
        parent_row_id: int,
        lazy_token: str | None = None,
        max_depth: int = 2,
        include_properties: bool = True,
        include_semantics: bool = True,
    ) -> dict[str, Any]:
        """Expand one accordion node: validate the token, hydrate direct children.

        Each child gets a grandchild ``children_stub`` whose ``lazy_token`` is
        freshly generated and persisted when the child has children and the depth
        budget (``max_depth > 1``) allows another level.
        """
        if lazy_token is not None:
            self._codec.decode(
                lazy_token, expected_sid=snapshot_id, expected_pid=parent_row_id
            )
            if not self._storage.lazy_token_valid(lazy_token):
                raise InvalidLazyTokenError(
                    reason="server_side_invalid_or_expired", token_present=True
                )

        children = self._storage.get_children(snapshot_id, parent_row_id)
        hydrated = [
            self._hydrate(
                snapshot_id,
                child,
                max_depth=max_depth,
                include_properties=include_properties,
                include_semantics=include_semantics,
            )
            for child in children[:CHILD_PAGE_LIMIT]
        ]
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=self._codec.ttl_seconds
        )
        return {
            "parent_row_id": parent_row_id,
            "children": hydrated,
            "has_more": len(children) > CHILD_PAGE_LIMIT,
            "token_expires_at": expires_at.isoformat(),
        }

    def get_element(
        self,
        snapshot_id: int,
        row_id: int,
        include_relationships: bool = True,
        include_semantics: bool = True,
        include_children_stub: bool = True,
    ) -> dict[str, Any]:
        """Return a single fully-hydrated element by its dense row_id."""
        element = self._storage.get_element(snapshot_id, row_id)
        if element is None:
            raise ElementNotFoundError(snapshot_id=snapshot_id, row_id=row_id)

        semantics = (
            self._storage.get_semantic_concepts(snapshot_id, row_id)
            if include_semantics
            else []
        )
        stub = (
            self._issue_stub(snapshot_id, row_id, max_depth=2)
            if include_children_stub
            else ChildStub()
        )
        payload = element_to_dict(
            element, semantics=semantics, children_stub=stub
        )
        if not include_semantics:
            payload["semantics"] = []
        if include_relationships:
            payload["relationships"] = [
                {
                    "to_row_id": rel.to_row_id,
                    "code": rel.relationship_code,
                    "weight": rel.weight,
                    "metadata": rel.metadata,
                }
                for rel in self._storage.get_relationships(snapshot_id, row_id)
            ]
        return {"element": payload}

    # --- internals -------------------------------------------------------
    def _fetch_roots(self, snapshot_id: int) -> list[Element]:
        """Roots are elements with no parent (metadata.parent_row_id is None)."""
        # ``get_children(snapshot_id, -1)`` returns nothing; the canonical root
        # parent sentinel is ``None``. The matrix builder roots a snapshot at
        # row_id 0, so we walk down from there: row 0 is the window root and its
        # depth<=1 subtree are the initial-context rows.
        root = self._storage.get_element(snapshot_id, 0)
        if root is None:
            return []
        return [root]

    def _hydrate(
        self,
        snapshot_id: int,
        element: Element,
        *,
        max_depth: int,
        include_properties: bool = True,
        include_semantics: bool = True,
    ) -> dict[str, Any]:
        """Hydrate one element with semantics + a (possibly tokenized) stub."""
        semantics = (
            self._storage.get_semantic_concepts(snapshot_id, element.row_id)
            if include_semantics
            else []
        )
        stub = self._issue_stub(snapshot_id, element.row_id, max_depth=max_depth)
        payload = element_to_dict(
            element,
            include_properties=include_properties,
            include_patterns=include_properties,
            semantics=semantics,
            children_stub=stub,
        )
        if not include_semantics:
            payload["semantics"] = semantics_to_list([])
        return payload

    def _issue_stub(
        self, snapshot_id: int, row_id: int, *, max_depth: int
    ) -> ChildStub:
        """Build a children_stub, minting+persisting a lazy token when expandable.

        A token is issued only when the node actually has children and there is
        depth budget left (``max_depth > 1``) for the agent to descend.
        """
        count = self._storage.count_children(snapshot_id, row_id)
        if count <= 0:
            return ChildStub(has_children=False, count=0, lazy_token=None)
        token: str | None = None
        if max_depth > 1:
            token = self._codec.generate(snapshot_id, row_id, max_depth - 1)
            self._storage.save_lazy_token(
                token,
                snapshot_id,
                row_id,
                max_depth - 1,
                count,
                ttl_seconds=self._codec.ttl_seconds,
            )
        return ChildStub(has_children=True, count=count, lazy_token=token)
