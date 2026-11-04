-- ChunkVault metadata schema, version 1.
-- Idempotent: every statement uses IF NOT EXISTS. Applied by database/init.py.
-- Foreign keys are enforced per-connection (PRAGMA foreign_keys = ON) by
-- database/connection.py; they are NOT a persistent database setting.
--
-- Timestamps are ISO-8601 UTC strings with millisecond precision.
-- Hashes are lowercase hex SHA-256 (64 chars).

CREATE TABLE IF NOT EXISTS files (
    file_id       TEXT PRIMARY KEY CHECK (length(file_id) > 0),
    filename      TEXT NOT NULL CHECK (length(filename) BETWEEN 1 AND 255),
    original_size INTEGER NOT NULL CHECK (original_size >= 0),
    -- Whole-file SHA-256; NULL only while the upload is still in progress.
    sha256        TEXT CHECK (sha256 IS NULL
                              OR (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*')),
    status        TEXT NOT NULL DEFAULT 'uploading'
                  CHECK (status IN ('uploading', 'complete', 'failed', 'deleting')),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (status <> 'complete' OR sha256 IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS chunks (
    hash       TEXT PRIMARY KEY
               CHECK (length(hash) = 64 AND hash NOT GLOB '*[^0-9a-f]*'),
    size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
    -- Invariant: ref_count = number of file_chunks rows whose chunk_hash = hash.
    -- A hash repeated inside one file or across files counts once per row.
    -- Maintained by the triggers below; audited by v_ref_count_mismatch.
    ref_count  INTEGER NOT NULL DEFAULT 0 CHECK (ref_count >= 0),
    -- pending: row exists, bytes may not be published yet (see consistency protocol)
    -- stored : bytes atomically published in the chunk store
    -- corrupt: integrity audit found the stored bytes do not match the hash
    state      TEXT NOT NULL DEFAULT 'pending'
               CHECK (state IN ('pending', 'stored', 'corrupt')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS file_chunks (
    file_id     TEXT    NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    chunk_hash  TEXT    NOT NULL REFERENCES chunks(hash) ON DELETE RESTRICT,
    chunk_size  INTEGER NOT NULL CHECK (chunk_size > 0),
    PRIMARY KEY (file_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS operations (
    operation_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    -- NULL for operations not tied to one file (benchmark, simulation, whole-store
    -- audits) and after the file row is deleted (history is kept).
    file_id        TEXT REFERENCES files(file_id) ON DELETE SET NULL,
    operation_type TEXT NOT NULL
                   CHECK (operation_type IN ('upload', 'restore', 'delete',
                                             'integrity_check', 'reconciliation',
                                             'benchmark', 'simulation')),
    status         TEXT NOT NULL DEFAULT 'running'
                   CHECK (status IN ('running', 'succeeded', 'failed', 'aborted')),
    started_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    completed_at   TEXT,
    error_message  TEXT,
    CHECK ((status = 'running') = (completed_at IS NULL)),
    CHECK (status <> 'failed' OR error_message IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_files_sha256          ON files(sha256);
CREATE INDEX IF NOT EXISTS idx_files_status          ON files(status);
CREATE INDEX IF NOT EXISTS idx_chunks_state          ON chunks(state);
CREATE INDEX IF NOT EXISTS idx_chunks_ref_count      ON chunks(ref_count);
CREATE INDEX IF NOT EXISTS idx_file_chunks_hash      ON file_chunks(chunk_hash);
CREATE INDEX IF NOT EXISTS idx_operations_file_id    ON operations(file_id);
CREATE INDEX IF NOT EXISTS idx_operations_status     ON operations(status, operation_type);

-- ---------------------------------------------------------------------------
-- Reference-count maintenance. The database itself keeps ref_count equal to
-- the number of file_chunks rows, including repeated occurrences of one hash
-- and rows removed by ON DELETE CASCADE when a file row is deleted.
-- ---------------------------------------------------------------------------

CREATE TRIGGER IF NOT EXISTS trg_file_chunks_check_size
BEFORE INSERT ON file_chunks
WHEN NEW.chunk_size <> (SELECT size_bytes FROM chunks WHERE hash = NEW.chunk_hash)
BEGIN
    SELECT RAISE(ABORT, 'file_chunks.chunk_size must equal chunks.size_bytes');
END;

CREATE TRIGGER IF NOT EXISTS trg_file_chunks_ref_inc
AFTER INSERT ON file_chunks
BEGIN
    UPDATE chunks SET ref_count = ref_count + 1 WHERE hash = NEW.chunk_hash;
END;

CREATE TRIGGER IF NOT EXISTS trg_file_chunks_ref_dec
AFTER DELETE ON file_chunks
BEGIN
    UPDATE chunks SET ref_count = ref_count - 1 WHERE hash = OLD.chunk_hash;
END;

-- A file's chunk list is immutable; re-pointing a row would bypass the counters.
CREATE TRIGGER IF NOT EXISTS trg_file_chunks_immutable
BEFORE UPDATE ON file_chunks
BEGIN
    SELECT RAISE(ABORT, 'file_chunks rows are immutable; delete and re-insert instead');
END;

-- A chunk's identity never changes: hash is its content address.
CREATE TRIGGER IF NOT EXISTS trg_chunks_identity_immutable
BEFORE UPDATE OF hash, size_bytes ON chunks
BEGIN
    SELECT RAISE(ABORT, 'chunks.hash and chunks.size_bytes are immutable');
END;

-- Audit view used by the integrity auditor: empty when the invariant holds.
CREATE VIEW IF NOT EXISTS v_ref_count_mismatch AS
SELECT c.hash                 AS hash,
       c.ref_count            AS recorded_ref_count,
       COUNT(fc.chunk_hash)   AS actual_ref_count
FROM chunks c
LEFT JOIN file_chunks fc ON fc.chunk_hash = c.hash
GROUP BY c.hash
HAVING c.ref_count <> COUNT(fc.chunk_hash);
