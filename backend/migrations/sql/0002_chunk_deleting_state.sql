-- Migration 0002: a durable 'deleting' chunk state (cloud garbage collection protocol, see cloud-migration/c4-workflow-notes.md).
--
-- A chunk row in state 'deleting' has been CLAIMED by garbage collection: it is unreferenced, it stays in the database (and in the
-- quota) until its object has really been deleted, and no upload may reference it any more (uploads lock the rows they reference
-- and refuse 'deleting' ones). The state is only reachable through the explicit GC; the local SQLite schema never has it.

ALTER TABLE chunks DROP CONSTRAINT IF EXISTS chunks_state_check;
ALTER TABLE chunks ADD CONSTRAINT chunks_state_check CHECK (state IN ('pending', 'stored', 'corrupt', 'deleting'));
