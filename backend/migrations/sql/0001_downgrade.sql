-- Reverts 0001_initial.sql. DESTROYS ALL CHUNKVAULT METADATA in the target schema; run only on purpose.
DROP VIEW IF EXISTS v_ref_count_mismatch;
DROP TABLE IF EXISTS benchmark_results;
DROP TABLE IF EXISTS operations;
DROP TABLE IF EXISTS file_chunks;
DROP TABLE IF EXISTS chunks;
DROP TABLE IF EXISTS files;
DROP FUNCTION IF EXISTS cv_fc_check_size();
DROP FUNCTION IF EXISTS cv_fc_ref_inc();
DROP FUNCTION IF EXISTS cv_fc_ref_dec();
DROP FUNCTION IF EXISTS cv_fc_immutable();
DROP FUNCTION IF EXISTS cv_chunks_identity();
