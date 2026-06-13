"""SQLite DDL for the Cerebellum CUA v4.2 schema (translated from the Postgres draft).

Kept in its own module so ``sqlite.py`` stays under the line cap and reads as pure
backend logic. JSONB->TEXT, BIGSERIAL->INTEGER PRIMARY KEY AUTOINCREMENT,
TIMESTAMPTZ->TEXT ISO-8601, INTEGER[]->JSON TEXT, GIN indexes->plain indexes.
"""

from __future__ import annotations

SCHEMA = """
CREATE TABLE IF NOT EXISTS matrix_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    epoch               INTEGER NOT NULL UNIQUE,
    created_at          TEXT NOT NULL,
    target_exe          TEXT,
    target_window_title TEXT,
    target_pid          INTEGER,
    matrix_version      TEXT NOT NULL DEFAULT '4.2',
    total_elements      INTEGER NOT NULL DEFAULT 0,
    build_duration_ms   INTEGER,
    degraded_branches   INTEGER NOT NULL DEFAULT 0,
    metadata            TEXT NOT NULL DEFAULT '{}',
    CHECK (epoch > 0)
);
CREATE TABLE IF NOT EXISTS elements (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id         INTEGER NOT NULL REFERENCES matrix_snapshots(id) ON DELETE CASCADE,
    matrix_row_id       INTEGER NOT NULL,
    uia_runtime_id_hash TEXT,
    control_type        INTEGER NOT NULL,
    name                TEXT,
    class_name          TEXT,
    automation_id       TEXT,
    bounding_rect       TEXT NOT NULL DEFAULT '{}',
    properties          TEXT NOT NULL DEFAULT '{}',
    patterns            TEXT NOT NULL DEFAULT '{}',
    is_interactive      INTEGER NOT NULL DEFAULT 0,
    is_content          INTEGER NOT NULL DEFAULT 0,
    framework_id        TEXT,
    metadata            TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL,
    UNIQUE (snapshot_id, matrix_row_id),
    CHECK (control_type > 0)
);
CREATE TABLE IF NOT EXISTS relationships (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id       INTEGER NOT NULL REFERENCES matrix_snapshots(id) ON DELETE CASCADE,
    from_row_id       INTEGER NOT NULL,
    to_row_id         INTEGER NOT NULL,
    relationship_code INTEGER NOT NULL,
    weight            REAL NOT NULL DEFAULT 1.0,
    metadata          TEXT NOT NULL DEFAULT '{}',
    CHECK (relationship_code BETWEEN 1 AND 10),
    UNIQUE (snapshot_id, from_row_id, to_row_id, relationship_code)
);
CREATE TABLE IF NOT EXISTS semantic_mappings (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    uia_control_type INTEGER NOT NULL,
    domain_concept   TEXT NOT NULL,
    confidence       REAL NOT NULL DEFAULT 0.85,
    mapping_rules    TEXT NOT NULL DEFAULT '{}',
    source           TEXT NOT NULL DEFAULT 'heuristic',
    version          TEXT NOT NULL DEFAULT 'v4.2',
    created_at       TEXT NOT NULL,
    CHECK (confidence BETWEEN 0.0 AND 1.0)
);
CREATE TABLE IF NOT EXISTS element_semantic_links (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    element_id         INTEGER NOT NULL REFERENCES elements(id) ON DELETE CASCADE,
    mapping_id         INTEGER NOT NULL REFERENCES semantic_mappings(id) ON DELETE CASCADE,
    applied_confidence REAL NOT NULL,
    applied_at         TEXT NOT NULL,
    metadata           TEXT NOT NULL DEFAULT '{}',
    UNIQUE (element_id, mapping_id)
);
CREATE TABLE IF NOT EXISTS lazy_load_tokens (
    token         TEXT PRIMARY KEY,
    snapshot_id   INTEGER NOT NULL REFERENCES matrix_snapshots(id) ON DELETE CASCADE,
    parent_row_id INTEGER NOT NULL,
    max_depth     INTEGER NOT NULL DEFAULT 2,
    created_at    TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    child_count   INTEGER NOT NULL DEFAULT 0,
    metadata      TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS matrix_patches (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id      INTEGER NOT NULL REFERENCES matrix_snapshots(id) ON DELETE CASCADE,
    epoch            INTEGER NOT NULL,
    patch_type       TEXT NOT NULL,
    affected_row_ids TEXT NOT NULL,
    patch_json       TEXT NOT NULL,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_epoch         ON matrix_snapshots(epoch);
CREATE INDEX IF NOT EXISTS idx_elements_snapshot_row   ON elements(snapshot_id, matrix_row_id);
CREATE INDEX IF NOT EXISTS idx_elements_control_type   ON elements(control_type);
CREATE INDEX IF NOT EXISTS idx_relationships_snap_from ON relationships(snapshot_id, from_row_id);
CREATE INDEX IF NOT EXISTS idx_relationships_snap_to   ON relationships(snapshot_id, to_row_id);
CREATE INDEX IF NOT EXISTS idx_lazy_tokens_expires     ON lazy_load_tokens(expires_at);
CREATE INDEX IF NOT EXISTS idx_patches_snapshot_epoch  ON matrix_patches(snapshot_id, epoch);
"""
