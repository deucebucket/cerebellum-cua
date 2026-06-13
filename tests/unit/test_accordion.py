"""Unit tests for the accordion lazy-loader (gateway/accordion.py).

Uses a real SQLite backend seeded via ``build_snapshot`` with a three-level tree
(window -> menu -> menu items) so grandchild stub token issuance is exercised.
"""

from __future__ import annotations

import pytest

from cerebellum_cua.gateway.accordion import Accordion
from cerebellum_cua.gateway.tokens import LazyTokenCodec
from cerebellum_cua.matrix import build_snapshot
from cerebellum_cua.storage import get_backend

SECRET = "accordion-test-secret"


def _seed_snapshot(backend):
    """Build + persist a 3-level tree. Returns the snapshot_id.

    row 0: Window (root)
      row 1: File menu
        row 2: New
        row 3: Open
      row 4: Edit menu  (leaf, no children)
    """
    walked = [
        ({"control_type": 50032, "name": "Main Window",
          "class_name": "Win32"}, 0, None),
        ({"control_type": 50011, "name": "File", "automation_id": "FileMenu",
          "is_interactive": True, "properties": {"is_enabled": True},
          "patterns": {"invoke": {"supported": True}}}, 1, 0),
        ({"control_type": 50011, "name": "New", "is_interactive": True,
          "is_content": True}, 2, 1),
        ({"control_type": 50011, "name": "Open...", "is_interactive": True,
          "is_content": True}, 2, 1),
        ({"control_type": 50011, "name": "Edit", "is_interactive": True}, 1, 0),
    ]
    snapshot = build_snapshot(walked, epoch=1007)
    return backend.persist_snapshot(snapshot)


@pytest.fixture()
def env(tmp_path):
    backend = get_backend(str(tmp_path / "matrix.db"))
    backend.connect()
    backend.init_schema()
    sid = _seed_snapshot(backend)
    codec = LazyTokenCodec(SECRET)
    accordion = Accordion(backend, codec)
    yield backend, codec, accordion, sid
    backend.close()


def test_initial_context_hydrates_root_with_stub(env):
    backend, codec, accordion, sid = env
    ctx = accordion.get_initial_context(sid)
    assert ctx["snapshot_id"] == sid
    assert ctx["total_roots"] == 1
    root = ctx["root_elements"][0]
    assert root["row_id"] == 0
    assert root["name"] == "Main Window"
    assert root["control_type"] == 50032
    stub = root["children_stub"]
    assert stub["has_children"] is True
    assert stub["count"] == 2  # File + Edit
    # Root issues a token (max_depth=1 -> max_d=... still issues at depth 1? no)
    # get_initial_context calls _hydrate with max_depth=1, so no token issued.
    assert stub["lazy_token"] is None


def test_initial_context_missing_snapshot_raises(env):
    _, _, accordion, _ = env
    from cerebellum_cua.errors import SnapshotNotFoundError

    with pytest.raises(SnapshotNotFoundError):
        accordion.get_initial_context(99999)


def test_load_children_returns_hydrated_children(env):
    backend, codec, accordion, sid = env
    result = accordion.load_children(sid, parent_row_id=0, max_depth=2)
    assert result["parent_row_id"] == 0
    names = {c["name"] for c in result["children"]}
    assert names == {"File", "Edit"}
    assert result["has_more"] is False
    assert "token_expires_at" in result


def test_load_children_issues_grandchild_token(env):
    backend, codec, accordion, sid = env
    result = accordion.load_children(sid, parent_row_id=0, max_depth=2)
    by_name = {c["name"]: c for c in result["children"]}
    # File has 2 children -> grandchild stub with a real, valid token.
    file_stub = by_name["File"]["children_stub"]
    assert file_stub["has_children"] is True
    assert file_stub["count"] == 2
    token = file_stub["lazy_token"]
    assert token is not None
    decoded = codec.decode(token, expected_sid=sid, expected_pid=1)
    assert decoded["pid"] == 1
    # Token was persisted server-side.
    assert backend.lazy_token_valid(token) is True
    # Edit is a leaf -> no token.
    edit_stub = by_name["Edit"]["children_stub"]
    assert edit_stub["has_children"] is False
    assert edit_stub["lazy_token"] is None


def test_load_children_with_valid_token_round_trips(env):
    backend, codec, accordion, sid = env
    parent = accordion.load_children(sid, parent_row_id=0, max_depth=2)
    file_token = {c["name"]: c for c in parent["children"]}["File"][
        "children_stub"
    ]["lazy_token"]
    # Use that token to expand File.
    grandkids = accordion.load_children(
        sid, parent_row_id=1, lazy_token=file_token, max_depth=2
    )
    names = {c["name"] for c in grandkids["children"]}
    assert names == {"New", "Open..."}


def test_load_children_rejects_token_for_wrong_parent(env):
    backend, codec, accordion, sid = env
    from cerebellum_cua.errors import InvalidLazyTokenError

    # Token minted for parent 1, but used to expand parent 0.
    token = codec.generate(sid, 1, 1)
    backend.save_lazy_token(token, sid, 1, 1, 2)
    with pytest.raises(InvalidLazyTokenError):
        accordion.load_children(sid, parent_row_id=0, lazy_token=token)


def test_load_children_respects_include_properties_false(env):
    backend, codec, accordion, sid = env
    result = accordion.load_children(
        sid, parent_row_id=0, max_depth=2, include_properties=False
    )
    file_child = {c["name"]: c for c in result["children"]}["File"]
    assert file_child["properties"] == {}
    assert file_child["patterns"] == {}


def test_load_children_include_semantics_false(env):
    backend, codec, accordion, sid = env
    result = accordion.load_children(
        sid, parent_row_id=0, max_depth=2, include_semantics=False
    )
    file_child = {c["name"]: c for c in result["children"]}["File"]
    assert file_child["semantics"] == []


def test_get_element_returns_relationships_and_semantics(env):
    backend, codec, accordion, sid = env
    # Seed a semantic mapping + link for the File menu (row 1).
    backend.conn.execute(
        """INSERT INTO semantic_mappings
           (uia_control_type, domain_concept, confidence, created_at)
           VALUES (50011, 'menu_item', 0.95, '2026-01-01T00:00:00+00:00')"""
    )
    mid = backend.conn.execute(
        "SELECT id FROM semantic_mappings LIMIT 1"
    ).fetchone()["id"]
    backend.conn.commit()
    backend.link_semantic(sid, row_id=1, mapping_id=mid, confidence=0.95)

    out = accordion.get_element(sid, row_id=1)
    el = out["element"]
    assert el["row_id"] == 1
    assert el["name"] == "File"
    assert el["semantics"][0]["domain_concept"] == "menu_item"
    # File -> New, Open (PARENT_OF edges) at minimum.
    rel_targets = {r["to_row_id"] for r in el["relationships"]}
    assert {2, 3}.issubset(rel_targets)
    assert el["children_stub"]["count"] == 2


def test_get_element_missing_raises(env):
    _, _, accordion, sid = env
    from cerebellum_cua.errors import ElementNotFoundError

    with pytest.raises(ElementNotFoundError):
        accordion.get_element(sid, row_id=9999)
