"""Acceptance criteria taken from the two original reports (System Design Report, Feasibility Report).

Most criteria were already covered by other test files; this file adds the ones that had no exact test
(three identical uploads, the reports' stated chunker parameters, the required logical storage model) so the
traceability matrix can point at a test for each. See docs/verification/requirements-traceability.md.
"""

import io

from app.core.config import CDCConfig, KIB
from app.database.connection import connect
from app.storage.stats import compute_stats
from tests.conftest import make_data


def _upload(svc, name, data):
    return svc.uploader.upload(name, io.BytesIO(data))


def test_three_identical_uploads_add_no_unique_chunk_payload_after_the_first(svc):
    """Feasibility report, section 9 'Repeat upload': upload an identical file 3 times."""
    data = make_data(11, 300 * KIB)
    first = _upload(svc, "same.bin", data)
    after_first = compute_stats(svc.settings, svc.store)
    second = _upload(svc, "same-copy-1.bin", data)
    third = _upload(svc, "same-copy-2.bin", data)
    final = compute_stats(svc.settings, svc.store)

    assert first.new_chunks == first.chunk_count > 1
    assert second.new_chunks == 0 and third.new_chunks == 0
    assert second.bytes_written == 0 and third.bytes_written == 0
    # physical payload and the number of chunk files are unchanged after the first upload
    assert final.unique_chunk_bytes == after_first.unique_chunk_bytes
    assert final.chunk_files == after_first.chunk_files
    # three logical copies, one physical copy: ratio 3.00, savings 100 * (3 - 1) / 3
    assert final.total_files == 3
    assert final.deduplication_ratio == 3.0
    assert abs(final.storage_savings_percent - 100 * 2 / 3) < 1e-9
    # every manifest occurrence counts: each chunk is referenced once per file
    conn = connect(svc.settings.db_path)
    try:
        counts = {r[0] for r in conn.execute("SELECT ref_count FROM chunks")}
    finally:
        conn.close()
    assert counts == {3}


def test_chunker_parameters_match_the_reports(settings):
    """Design report section 3 and Feasibility report section 3: 4 KiB min, 16 KiB target, 128 KiB max, 64-byte window."""
    cfg = CDCConfig()
    assert (cfg.min_chunk_size, cfg.avg_chunk_size, cfg.max_chunk_size, cfg.window_size) == (4 * KIB, 16 * KIB, 128 * KIB, 64)
    # the 16 KiB expected boundary interval needs a suitable modulus (feasibility report): min + modulus = target
    assert cfg.min_chunk_size + cfg.boundary_divisor == cfg.avg_chunk_size


def test_logical_storage_model_has_the_fields_the_design_report_requires(db):
    """Design report section 4.1 (chunks, files, file_chunks). Column names differ in three places:
    name -> filename, file_size -> original_size, position -> chunk_index (documented in the traceability matrix)."""
    cols = {t: [r[1] for r in db.execute(f"PRAGMA table_info({t})")] for t in ("chunks", "files", "file_chunks")}
    assert {"hash", "size_bytes", "ref_count", "state"} <= set(cols["chunks"])
    assert {"file_id", "filename", "original_size", "sha256", "status"} <= set(cols["files"])
    assert {"file_id", "chunk_index", "chunk_hash"} <= set(cols["file_chunks"])
