-- Reverts 0002_chunk_deleting_state.sql. Rows claimed for deletion go back to 'pending' (still unreferenced, still collectable).
UPDATE chunks SET state = 'pending' WHERE state = 'deleting';
ALTER TABLE chunks DROP CONSTRAINT IF EXISTS chunks_state_check;
ALTER TABLE chunks ADD CONSTRAINT chunks_state_check CHECK (state IN ('pending', 'stored', 'corrupt'));
