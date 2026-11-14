-- ChunkVault PostgreSQL schema, migration 0001.
-- Same entities, constraints and invariants as backend/app/database/schema.sql (SQLite); see
-- cloud-migration/phase-b-design.md section 3. No schema qualifiers: the target schema comes from the connection's search_path,
-- so tests can run this file in a throw-away schema.
--
-- Hashes are lowercase hex SHA-256. file_id stays text so identifiers of any future migrated data are preserved.

CREATE TABLE files (
    file_id       text PRIMARY KEY CHECK (length(file_id) > 0),
    filename      text NOT NULL CHECK (char_length(filename) BETWEEN 1 AND 255),
    original_size bigint NOT NULL CHECK (original_size >= 0),
    -- Whole-file SHA-256; NULL only while the upload is still in progress.
    sha256        text CHECK (sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'),
    status        text NOT NULL DEFAULT 'uploading'
                  CHECK (status IN ('uploading', 'complete', 'failed', 'deleting')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    CHECK (status <> 'complete' OR sha256 IS NOT NULL)
);

CREATE TABLE chunks (
    hash       text PRIMARY KEY CHECK (hash ~ '^[0-9a-f]{64}$'),
    size_bytes integer NOT NULL CHECK (size_bytes > 0),
    -- Invariant: ref_count = number of file_chunks rows whose chunk_hash = hash (maintained by the triggers below).
    ref_count  integer NOT NULL DEFAULT 0 CHECK (ref_count >= 0),
    -- pending: row exists, object may not be published yet | stored: object published | corrupt: audit found damage
    state      text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'stored', 'corrupt')),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE file_chunks (
    file_id     text    NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
    chunk_index integer NOT NULL CHECK (chunk_index >= 0),
    chunk_hash  text    NOT NULL REFERENCES chunks(hash) ON DELETE RESTRICT,
    chunk_size  integer NOT NULL CHECK (chunk_size > 0),
    PRIMARY KEY (file_id, chunk_index)
);

CREATE TABLE operations (
    operation_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- NULL for whole-store operations and after the file row is deleted (history is kept).
    file_id        text REFERENCES files(file_id) ON DELETE SET NULL,
    operation_type text NOT NULL
                   CHECK (operation_type IN ('upload', 'restore', 'delete', 'integrity_check', 'reconciliation',
                                             'benchmark', 'simulation')),
    status         text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'succeeded', 'failed', 'aborted')),
    started_at     timestamptz NOT NULL DEFAULT now(),
    completed_at   timestamptz,
    error_message  text,
    CHECK ((status = 'running') = (completed_at IS NULL)),
    CHECK (status <> 'failed' OR error_message IS NOT NULL)
);

-- PostgreSQL only: Render's disk is ephemeral, so benchmark results must live in the database.
CREATE TABLE benchmark_results (
    job_id      text PRIMARY KEY,
    kind        text NOT NULL,
    finished_at timestamptz NOT NULL DEFAULT now(),
    spec        jsonb NOT NULL,
    result      jsonb NOT NULL
);

CREATE INDEX idx_files_sha256       ON files(sha256);
CREATE INDEX idx_files_status       ON files(status);
CREATE INDEX idx_chunks_state       ON chunks(state);
CREATE INDEX idx_chunks_ref_count   ON chunks(ref_count);
CREATE INDEX idx_file_chunks_hash   ON file_chunks(chunk_hash);
CREATE INDEX idx_operations_file_id ON operations(file_id);
CREATE INDEX idx_operations_status  ON operations(status, operation_type);

-- ---------------------------------------------------------------------------------------------------------------------------
-- Reference-count maintenance. Row-level AFTER triggers fire once per inserted or deleted manifest row, including repeated
-- occurrences of one hash and rows removed by ON DELETE CASCADE (cascade actions run as ordinary deletes).
-- ---------------------------------------------------------------------------------------------------------------------------

CREATE FUNCTION cv_fc_check_size() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.chunk_size <> (SELECT size_bytes FROM chunks WHERE hash = NEW.chunk_hash) THEN
        RAISE EXCEPTION 'file_chunks.chunk_size must equal chunks.size_bytes' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_file_chunks_check_size BEFORE INSERT ON file_chunks
    FOR EACH ROW EXECUTE FUNCTION cv_fc_check_size();

CREATE FUNCTION cv_fc_ref_inc() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE chunks SET ref_count = ref_count + 1 WHERE hash = NEW.chunk_hash;
    RETURN NULL;
END $$;
CREATE TRIGGER trg_file_chunks_ref_inc AFTER INSERT ON file_chunks
    FOR EACH ROW EXECUTE FUNCTION cv_fc_ref_inc();

CREATE FUNCTION cv_fc_ref_dec() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE chunks SET ref_count = ref_count - 1 WHERE hash = OLD.chunk_hash;
    RETURN NULL;
END $$;
CREATE TRIGGER trg_file_chunks_ref_dec AFTER DELETE ON file_chunks
    FOR EACH ROW EXECUTE FUNCTION cv_fc_ref_dec();

-- A file's chunk list is immutable; re-pointing a row would bypass the counters.
CREATE FUNCTION cv_fc_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'file_chunks rows are immutable; delete and re-insert instead' USING ERRCODE = '23000';
END $$;
CREATE TRIGGER trg_file_chunks_immutable BEFORE UPDATE ON file_chunks
    FOR EACH ROW EXECUTE FUNCTION cv_fc_immutable();

-- A chunk's identity never changes: hash is its content address.
CREATE FUNCTION cv_chunks_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'chunks.hash and chunks.size_bytes are immutable' USING ERRCODE = '23000';
END $$;
CREATE TRIGGER trg_chunks_identity_immutable BEFORE UPDATE OF hash, size_bytes ON chunks
    FOR EACH ROW EXECUTE FUNCTION cv_chunks_identity();

-- Audit view used by the integrity auditor: empty when the invariant holds.
CREATE VIEW v_ref_count_mismatch AS
SELECT c.hash                AS hash,
       c.ref_count           AS recorded_ref_count,
       COUNT(fc.chunk_hash)  AS actual_ref_count
FROM chunks c
LEFT JOIN file_chunks fc ON fc.chunk_hash = c.hash
GROUP BY c.hash
HAVING c.ref_count <> COUNT(fc.chunk_hash);

-- ---------------------------------------------------------------------------------------------------------------------------
-- Exposure control (Supabase publishes tables to the public anon key through its REST API unless they are protected).
-- Row-level security is enabled on every table with no policy for anon/authenticated (default deny); Supabase's API roles anon,
-- authenticated AND service_role (which bypasses RLS and receives default privileges on new tables) lose every privilege on the
-- ChunkVault objects named below -- and only on those: nothing Supabase manages is touched. The backend's own role
-- gets access through scripts/setup_app_role.py. Privileges are also revoked from the Supabase API roles when they exist.
-- ---------------------------------------------------------------------------------------------------------------------------

ALTER TABLE files             ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks            ENABLE ROW LEVEL SECURITY;
ALTER TABLE file_chunks       ENABLE ROW LEVEL SECURITY;
ALTER TABLE operations        ENABLE ROW LEVEL SECURITY;
ALTER TABLE benchmark_results ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON FUNCTION cv_fc_check_size(), cv_fc_ref_inc(), cv_fc_ref_dec(), cv_fc_immutable(), cv_chunks_identity() FROM PUBLIC;

DO $$
DECLARE r text;
BEGIN
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            -- concatenation with quote_ident (no format() placeholders: this file is passed to the driver unchanged)
            EXECUTE 'REVOKE ALL ON files, chunks, file_chunks, operations, benchmark_results, v_ref_count_mismatch FROM ' || quote_ident(r);
            EXECUTE 'REVOKE ALL ON FUNCTION cv_fc_check_size(), cv_fc_ref_inc(), cv_fc_ref_dec(), cv_fc_immutable(), cv_chunks_identity() FROM ' || quote_ident(r);
        END IF;
    END LOOP;
END $$;
