"""Unit tests for the SQLite storage backend.

Covers the round-trip persist/read path, child ordering and counting, lazy-token
save/validate/expiry, relationship reads, semantic concept linkage, and the patch
log. Uses a temporary on-disk database so WAL mode behaves like production.
"""

from __future__ import annotations

import sqlite3

import pytest

from cerebellum_cua.model import (
    BoundingRect,
    Element,
    Relationship,
    RelationshipCode,
    Snapshot,
)
from cerebellum_cua.storage import SQLiteBackend, get_backend
from cerebellum_cua.storage.postgres import PostgresBackend


@pytest.fixture()
def backend(tmp_path):
    db = SQLiteBackend(str(tmp_path / "matrix.db"))
    db.connect()
    db.init_schema()
    yield db
    db.close()


def _sample_snapshot(epoch: int = 1) -> Snapshot:
    root = Element(
        row_id=0, control_type=50032, name="Main Window", class_name="Win32",
        bounding_rect=BoundingRect(0, 0, 800, 600), is_content=False,
    )
    btn = Element(
        row_id=1, control_type=50000, name="OK", automation_id="okBtn",
        bounding_rect=BoundingRect(10, 500, 80, 30), is_interactive=True,
        properties={"is_enabled": True}, patterns={"invoke": {"supported": True}},
    )
    edit = Element(
        row_id=2, control_type=50004, name="Search", automation_id="search",
        bounding_rect=BoundingRect(10, 10, 200, 24), is_interactive=True,
    )
    rels = [
        Relationship(0, 1, RelationshipCode.PARENT_OF),
        Relationship(0, 2, RelationshipCode.PARENT_OF),
        Relationship(1, 2, RelationshipCode.NEXT_SIBLING_OF, weight=0.5),
    ]
    return Snapshot(epoch=epoch, elements=[root, btn, edit], relationships=rels,
                    target={"target_exe": "app.exe", "target_pid": 1234})


def test_persist_and_get_element_round_trip(backend):
    snap = _sample_snapshot()
    sid = backend.persist_snapshot(snap)
    assert isinstance(sid, int) and sid > 0
    assert snap.snapshot_id == sid
    assert backend.get_last_snapshot_id() == sid

    el = backend.get_element(sid, 1)
    assert el is not None
    assert el.row_id == 1
    assert el.name == "OK"
    assert el.control_type == 50000
    assert el.automation_id == "okBtn"
    assert el.is_interactive is True
    assert el.properties == {"is_enabled": True}
    assert el.patterns == {"invoke": {"supported": True}}
    assert el.bounding_rect.width == 80 and el.bounding_rect.height == 30

    assert backend.get_element(sid, 999) is None


def test_get_children_ordering(backend):
    snap = _sample_snapshot()
    # Persist children out of row_id order to prove the query sorts them.
    snap.elements[1], snap.elements[2] = snap.elements[2], snap.elements[1]
    sid = backend.persist_snapshot(snap)

    children = backend.get_children(sid, 0)
    assert [c.row_id for c in children] == [1, 2]
    assert children[0].name == "OK"


def test_count_children(backend):
    sid = backend.persist_snapshot(_sample_snapshot())
    assert backend.count_children(sid, 0) == 2
    assert backend.count_children(sid, 1) == 0


def test_get_relationships(backend):
    sid = backend.persist_snapshot(_sample_snapshot())
    out = backend.get_relationships(sid, 0)
    assert len(out) == 2
    assert all(r.from_row_id == 0 for r in out)
    assert {r.to_row_id for r in out} == {1, 2}
    assert all(r.relationship_code == RelationshipCode.PARENT_OF for r in out)

    sibling = backend.get_relationships(sid, 1)
    assert len(sibling) == 1
    assert sibling[0].relationship_code == RelationshipCode.NEXT_SIBLING_OF
    assert sibling[0].weight == 0.5


def test_record_patch(backend):
    sid = backend.persist_snapshot(_sample_snapshot())
    backend.record_patch(
        sid, epoch=2, patch_type="property",
        affected_row_ids=[1, 2], patch_json={"1": {"name": "Submit"}},
    )
    row = backend.conn.execute(
        "SELECT * FROM matrix_patches WHERE snapshot_id = ?", (sid,)
    ).fetchone()
    assert row["patch_type"] == "property"
    assert row["affected_row_ids"] == "[1, 2]"
    assert "Submit" in row["patch_json"]


def test_lazy_token_save_valid_and_expiry(backend):
    sid = backend.persist_snapshot(_sample_snapshot())

    backend.save_lazy_token("tok-live", sid, parent_row_id=0, max_depth=2,
                            child_count=2, ttl_seconds=300)
    assert backend.lazy_token_valid("tok-live") is True

    # Already-expired TTL must read as invalid.
    backend.save_lazy_token("tok-dead", sid, parent_row_id=0, max_depth=2,
                            child_count=2, ttl_seconds=-1)
    assert backend.lazy_token_valid("tok-dead") is False

    # Unknown token is invalid, never raises.
    assert backend.lazy_token_valid("nope") is False


def test_semantic_concepts_round_trip(backend):
    sid = backend.persist_snapshot(_sample_snapshot())
    # Seed a mapping then link it to the button (row_id=1).
    backend.conn.execute(
        """INSERT INTO semantic_mappings
           (uia_control_type, domain_concept, confidence, created_at)
           VALUES (50000, 'action_button', 0.94, '2026-01-01T00:00:00+00:00')"""
    )
    mapping_id = backend.conn.execute(
        "SELECT id FROM semantic_mappings LIMIT 1"
    ).fetchone()["id"]
    backend.conn.commit()

    backend.link_semantic(sid, row_id=1, mapping_id=mapping_id, confidence=0.94)
    concepts = backend.get_semantic_concepts(sid, 1)
    assert len(concepts) == 1
    assert concepts[0].domain_concept == "action_button"
    assert concepts[0].confidence == pytest.approx(0.94)

    el = backend.get_element(sid, 1)
    assert el is not None
    assert el.semantics[0].domain_concept == "action_button"


def test_epoch_uniqueness_enforced(backend):
    backend.persist_snapshot(_sample_snapshot(epoch=5))
    with pytest.raises(sqlite3.IntegrityError):
        backend.persist_snapshot(_sample_snapshot(epoch=5))


def test_multiple_snapshots_last_id_tracks_max_epoch(backend):
    s1 = backend.persist_snapshot(_sample_snapshot(epoch=1))
    s2 = backend.persist_snapshot(_sample_snapshot(epoch=2))
    assert backend.get_last_snapshot_id() == s2
    assert s2 > s1


def test_context_manager_connects_and_closes(tmp_path):
    with SQLiteBackend(str(tmp_path / "ctx.db")) as db:
        sid = db.persist_snapshot(_sample_snapshot())
        assert db.get_element(sid, 0) is not None


class TestGetBackendFactory:
    def test_none_returns_sqlite_memory(self):
        assert isinstance(get_backend(None), SQLiteBackend)

    def test_plain_path_returns_sqlite(self, tmp_path):
        be = get_backend(str(tmp_path / "x.db"))
        assert isinstance(be, SQLiteBackend)

    def test_sqlite_url_scheme(self):
        be = get_backend("sqlite:///:memory:")
        assert isinstance(be, SQLiteBackend)
        assert be.path == ":memory:"

    def test_sqlite_url_four_slash_is_absolute(self):
        # sqlite:////abs/path -> absolute /abs/path (SQLAlchemy convention).
        be = get_backend("sqlite:////tmp/cere.db")
        assert isinstance(be, SQLiteBackend)
        assert be.path == "/tmp/cere.db"

    def test_sqlite_url_three_slash_is_relative(self):
        # sqlite:///rel.db -> relative rel.db (one slash is the scheme separator).
        be = get_backend("sqlite:///cere.db")
        assert be.path == "cere.db"

    def test_sqlite_url_empty_path_is_memory(self):
        assert get_backend("sqlite://").path == ":memory:"

    def test_postgres_dsn_returns_postgres(self):
        be = get_backend("postgresql://u:p@localhost/matrixui")
        assert isinstance(be, PostgresBackend)

    def test_postgres_keyword_dsn(self):
        be = get_backend("dbname=matrixui host=localhost")
        assert isinstance(be, PostgresBackend)


class TestPackagedSchema:
    """The Postgres DDL must ship inside the package and load from an install."""

    def test_load_schema_ddl_returns_v42_ddl(self):
        from cerebellum_cua.storage.postgres import load_schema_ddl

        ddl = load_schema_ddl()
        assert "CREATE TABLE" in ddl
        assert "matrix_snapshots" in ddl
        assert "elements" in ddl

    def test_schema_resource_present_in_package(self):
        from importlib import resources

        res = resources.files("cerebellum_cua.storage.schema").joinpath(
            "cerebellum_cua_v42_schema.sql"
        )
        assert res.is_file()


@pytest.mark.postgres
class TestPostgresBackend:
    """Live Postgres round-trip; auto-skips unless MATRIX_UI_PG_DSN is set."""

    @pytest.fixture()
    def pg(self):
        import os

        dsn = os.environ.get("MATRIX_UI_PG_DSN")
        if not dsn:
            pytest.skip("MATRIX_UI_PG_DSN not set")
        try:
            import psycopg2  # noqa: F401
        except ImportError:
            pytest.skip("psycopg2 not installed")
        be = PostgresBackend(dsn)
        be.connect()
        be.init_schema()
        yield be
        be.close()

    def test_round_trip(self, pg):
        sid = pg.persist_snapshot(_sample_snapshot(epoch=999))
        el = pg.get_element(sid, 1)
        assert el is not None and el.name == "OK"
        assert pg.count_children(sid, 0) == 2
