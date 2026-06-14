-- Matrix-UI Database Schema v4.2
-- PostgreSQL 16+ required. Idempotent creation script.
-- Transcribed from the design spec, Section 3.
--
-- DEVIATION FROM SPEC (intentional): the spec's seed data uses a scrambled,
-- internally-inconsistent control_type enum (e.g. it labels 50014 as both
-- "window" in the seed and "ScrollBar"-adjacent elsewhere; 50004 as "combo_box"
-- when real UIA 50004 is Edit). The `uiautomation` library emits the REAL
-- Microsoft UIA ControlTypeId constants, so this file (and src/cerebellum_cua/model.py
-- ControlType) use the real constants while preserving the spec's mapping INTENT.
-- See src/cerebellum_cua/semantics/mappings.py for the authoritative Python seed.
--
-- Run: psql -U matrixui -d matrixui -f sql/cerebellum_cua_v42_schema.sql

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gin;

-- Core snapshot table (immutable after insert)
CREATE TABLE IF NOT EXISTS matrix_snapshots (
    id                  BIGSERIAL PRIMARY KEY,
    epoch               BIGINT NOT NULL UNIQUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    target_exe          TEXT,
    target_window_title TEXT,
    target_pid          INTEGER,
    matrix_version      TEXT NOT NULL DEFAULT '4.2',
    total_elements      INTEGER NOT NULL DEFAULT 0,
    build_duration_ms   INTEGER,
    degraded_branches   INTEGER NOT NULL DEFAULT 0,
    metadata            JSONB NOT NULL DEFAULT '{}',
    CONSTRAINT valid_epoch CHECK (epoch > 0)
);
COMMENT ON TABLE matrix_snapshots IS
    'Immutable snapshot of the entire Matrix-UI relational representation at a specific epoch. One row per build or forced full rebuild.';
COMMENT ON COLUMN matrix_snapshots.epoch IS
    'Strictly increasing 64-bit epoch identifier. CLI agents use this for diff requests.';
COMMENT ON COLUMN matrix_snapshots.metadata IS
    'Build config, host DPI, process modules hash, applied hacks bitmask, telemetry summary.';

-- Elements table (core of the matrix)
CREATE TABLE IF NOT EXISTS elements (
    id                  BIGSERIAL PRIMARY KEY,
    snapshot_id         BIGINT NOT NULL REFERENCES matrix_snapshots(id) ON DELETE CASCADE,
    matrix_row_id       INTEGER NOT NULL,
    uia_runtime_id_hash TEXT,
    control_type        INTEGER NOT NULL,
    name                TEXT,
    class_name          TEXT,
    automation_id       TEXT,
    bounding_rect       JSONB NOT NULL DEFAULT '{"left":0,"top":0,"width":0,"height":0,"dpi":96}',
    properties          JSONB NOT NULL DEFAULT '{}',
    patterns            JSONB NOT NULL DEFAULT '{}',
    is_interactive      BOOLEAN NOT NULL DEFAULT FALSE,
    is_content          BOOLEAN NOT NULL DEFAULT FALSE,
    framework_id        TEXT,
    metadata            JSONB NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT unique_row_per_snapshot UNIQUE (snapshot_id, matrix_row_id),
    CONSTRAINT valid_control_type CHECK (control_type > 0)
);
COMMENT ON TABLE elements IS
    'One row per included UIA element after the should_include predicate. matrix_row_id is the dense stable ID used in the adjacency matrix and all CLI commands.';
COMMENT ON COLUMN elements.bounding_rect IS
    'Normalized rect with DPI. Updated only on geometry change events.';
COMMENT ON COLUMN elements.properties IS
    'Full property tensor: value, toggle_state, grid_row_count, scroll_percent, provider_description, is_enabled, is_keyboard_focusable, has_keyboard_focus, and framework-specific extras as flat key-value.';
COMMENT ON COLUMN elements.patterns IS
    'Bitmask + state object, e.g. {"invoke":{"supported":true},"toggle":{"supported":true,"state":1}}.';

-- Directed relationships (sparse adjacency matrix edges)
CREATE TABLE IF NOT EXISTS relationships (
    id                BIGSERIAL PRIMARY KEY,
    snapshot_id       BIGINT NOT NULL REFERENCES matrix_snapshots(id) ON DELETE CASCADE,
    from_row_id       INTEGER NOT NULL,
    to_row_id         INTEGER NOT NULL,
    relationship_code SMALLINT NOT NULL,
    weight            REAL NOT NULL DEFAULT 1.0,
    metadata          JSONB NOT NULL DEFAULT '{}',
    CONSTRAINT valid_relationship_code CHECK (relationship_code BETWEEN 1 AND 10),
    CONSTRAINT unique_edge UNIQUE (snapshot_id, from_row_id, to_row_id, relationship_code)
);
COMMENT ON TABLE relationships IS
    'Sparse adjacency matrix representation. relationship_code maps to the enum in the Matrix-UI engine (1=parent_of .. 8=contains_via_geometry, 9=scrolls, 10=invokes).';
COMMENT ON COLUMN relationships.metadata IS
    'Geometric overlap score, z-order, inference_method (uia_nav vs rect_overlap).';

-- Semantic mapping rules
CREATE TABLE IF NOT EXISTS semantic_mappings (
    id               BIGSERIAL PRIMARY KEY,
    uia_control_type INTEGER NOT NULL,
    domain_concept   TEXT NOT NULL,
    confidence       REAL NOT NULL DEFAULT 0.85,
    mapping_rules    JSONB NOT NULL DEFAULT '{}',
    source           TEXT NOT NULL DEFAULT 'heuristic',
    version          TEXT NOT NULL DEFAULT 'v4.2',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT valid_confidence CHECK (confidence BETWEEN 0.0 AND 1.0)
);
COMMENT ON TABLE semantic_mappings IS
    'Rule-based and ML-assisted translation from raw UIA primitives to high-level domain actions for CLI agent symbolic execution.';
COMMENT ON COLUMN semantic_mappings.mapping_rules IS
    'Predicate object e.g. {"name_contains_any":["submit","login","ok"],"patterns":["invoke"],"framework":"winform"}.';

-- Element-to-semantic links (many-to-many with confidence)
CREATE TABLE IF NOT EXISTS element_semantic_links (
    id                 BIGSERIAL PRIMARY KEY,
    element_id         BIGINT NOT NULL REFERENCES elements(id) ON DELETE CASCADE,
    mapping_id         BIGINT NOT NULL REFERENCES semantic_mappings(id) ON DELETE CASCADE,
    applied_confidence REAL NOT NULL,
    applied_at         TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata           JSONB NOT NULL DEFAULT '{}',
    CONSTRAINT unique_link UNIQUE (element_id, mapping_id)
);

-- Lazy-load token cache (ephemeral, supports accordion interface)
CREATE TABLE IF NOT EXISTS lazy_load_tokens (
    token         TEXT PRIMARY KEY,
    snapshot_id   BIGINT NOT NULL REFERENCES matrix_snapshots(id) ON DELETE CASCADE,
    parent_row_id INTEGER NOT NULL,
    max_depth     SMALLINT NOT NULL DEFAULT 2,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at    TIMESTAMPTZ NOT NULL DEFAULT (CURRENT_TIMESTAMP + INTERVAL '300 seconds'),
    child_count   INTEGER NOT NULL DEFAULT 0,
    metadata      JSONB NOT NULL DEFAULT '{}'
);
COMMENT ON TABLE lazy_load_tokens IS
    'Opaque tokens returned in children_stub for accordion lazy expansion. Server-side TTL 5 minutes.';

-- Patch log for incremental sync
CREATE TABLE IF NOT EXISTS matrix_patches (
    id               BIGSERIAL PRIMARY KEY,
    snapshot_id      BIGINT NOT NULL REFERENCES matrix_snapshots(id) ON DELETE CASCADE,
    epoch            BIGINT NOT NULL,
    patch_type       TEXT NOT NULL,          -- 'structure' or 'property'
    affected_row_ids INTEGER[] NOT NULL,
    patch_json       JSONB NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Full set of indexes (no elisions)
CREATE INDEX IF NOT EXISTS idx_snapshots_epoch          ON matrix_snapshots(epoch);
CREATE INDEX IF NOT EXISTS idx_snapshots_created        ON matrix_snapshots(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_elements_snapshot_row    ON elements(snapshot_id, matrix_row_id);
CREATE INDEX IF NOT EXISTS idx_elements_control_type    ON elements(control_type);
CREATE INDEX IF NOT EXISTS idx_elements_name_trgm       ON elements USING GIN (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_elements_automation_id   ON elements(automation_id)
    WHERE automation_id IS NOT NULL AND automation_id <> '';
CREATE INDEX IF NOT EXISTS idx_elements_properties_gin  ON elements USING GIN (properties jsonb_path_ops);
CREATE INDEX IF NOT EXISTS idx_elements_patterns_gin    ON elements USING GIN (patterns jsonb_path_ops);
CREATE INDEX IF NOT EXISTS idx_elements_interactive     ON elements(is_interactive) WHERE is_interactive = TRUE;
CREATE INDEX IF NOT EXISTS idx_relationships_snap_from  ON relationships(snapshot_id, from_row_id);
CREATE INDEX IF NOT EXISTS idx_relationships_snap_to    ON relationships(snapshot_id, to_row_id);
CREATE INDEX IF NOT EXISTS idx_relationships_code       ON relationships(relationship_code);
CREATE INDEX IF NOT EXISTS idx_semantic_type_concept    ON semantic_mappings(uia_control_type, domain_concept);
CREATE INDEX IF NOT EXISTS idx_semantic_rules_gin       ON semantic_mappings USING GIN (mapping_rules jsonb_path_ops);
CREATE INDEX IF NOT EXISTS idx_links_element            ON element_semantic_links(element_id);
CREATE INDEX IF NOT EXISTS idx_lazy_tokens_expires      ON lazy_load_tokens(expires_at);
CREATE INDEX IF NOT EXISTS idx_patches_snapshot_epoch   ON matrix_patches(snapshot_id, epoch);

-- Seed data: common semantic mappings.
-- control_type ids are REAL Microsoft UIA constants (see deviation note above).
INSERT INTO semantic_mappings (uia_control_type, domain_concept, confidence, mapping_rules, source, version) VALUES
(50000, 'action_button', 0.94, '{"name_contains_any":["submit","ok","login","save","apply","confirm","send"],"patterns":["invoke"],"is_enabled":true}', 'heuristic', 'v4.2'),
(50000, 'cancel_button', 0.91, '{"name_contains_any":["cancel","close","dismiss","abort"],"patterns":["invoke"]}', 'heuristic', 'v4.2'),
(50004, 'text_input',    0.89, '{"patterns":["value"],"is_keyboard_focusable":true,"automation_id_contains_any":["username","password","email","search"]}', 'heuristic', 'v4.2'),
(50002, 'checkbox',      0.93, '{"patterns":["toggle"]}', 'heuristic', 'v4.2'),
(50013, 'radio_option',  0.90, '{"patterns":["selection","toggle"],"name_length_lt":60}', 'heuristic', 'v4.2'),
(50003, 'combo_box',     0.88, '{"patterns":["expand_collapse","value"]}', 'heuristic', 'v4.2'),
(50007, 'list_item',     0.87, '{"parent_control_type":50008}', 'heuristic', 'v4.2'),
(50008, 'list_view',     0.92, '{"patterns":["selection","scroll"],"child_count_gt":0}', 'heuristic', 'v4.2'),
(50011, 'menu_item',     0.95, '{"patterns":["invoke"],"parent_control_type":50010}', 'heuristic', 'v4.2'),
(50010, 'menu_bar',      0.91, '{"child_count_gt":1}', 'heuristic', 'v4.2'),
(50028, 'data_grid',     0.93, '{"patterns":["grid","table","selection"],"framework_any":["winform","wpf"]}', 'heuristic', 'v4.2'),
(50029, 'data_grid_row', 0.89, '{"parent_control_type":50028,"patterns":["selection"]}', 'heuristic', 'v4.2'),
(50019, 'tab_item',      0.90, '{"patterns":["selection"],"parent_control_type":50018}', 'heuristic', 'v4.2'),
(50018, 'tab_control',   0.88, '{"patterns":["selection"],"child_count_gt":1}', 'heuristic', 'v4.2'),
(50024, 'tree_item',     0.91, '{"patterns":["expand_collapse","invoke"],"has_children_hint":true}', 'heuristic', 'v4.2'),
(50032, 'window',        0.97, '{"is_content":false}', 'heuristic', 'v4.2'),
(50030, 'document',      0.85, '{"framework_any":["chrome","edge","mozilla"]}', 'heuristic', 'v4.2'),
(50014, 'scroll_bar',    0.70, '{"exclude":true}', 'heuristic', 'v4.2'),
(50033, 'pane_container',0.75, '{"name_length_eq":0,"child_count_eq":0}', 'heuristic', 'v4.2'),
(50005, 'hyperlink',     0.92, '{"patterns":["invoke"],"name_contains":"http"}', 'heuristic', 'v4.2')
ON CONFLICT DO NOTHING;

-- Helper view for current active snapshot (used by accordion API)
CREATE OR REPLACE VIEW current_active_matrix AS
SELECT e.*, s.epoch, s.created_at AS snapshot_created_at
FROM elements e
JOIN matrix_snapshots s ON e.snapshot_id = s.id
WHERE s.epoch = (SELECT MAX(epoch) FROM matrix_snapshots);

-- Helper function for semantic lookup (used by CLI)
CREATE OR REPLACE FUNCTION get_semantic_concepts(p_element_id BIGINT)
RETURNS TABLE(domain_concept TEXT, confidence REAL) AS $$
BEGIN
    RETURN QUERY
    SELECT sm.domain_concept, esl.applied_confidence
    FROM element_semantic_links esl
    JOIN semantic_mappings sm ON esl.mapping_id = sm.id
    WHERE esl.element_id = p_element_id
    ORDER BY esl.applied_confidence DESC;
END;
$$ LANGUAGE plpgsql STABLE;
