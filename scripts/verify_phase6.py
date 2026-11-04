"""Phase 6 independent end-to-end verification (Tests A-G, database integrity, reliability,
benchmark and simulation checks) on throw-away storage.

Run from the backend directory:
    .venv/Scripts/python ../scripts/verify_phase6.py

Everything runs in temporary directories through the real HTTP API (FastAPI TestClient) and the real
services; the development database and data/ are never touched. Where a check recomputes a value
independently (chunk boundaries, hashes, reference counts) it does so with separate code paths
(a fresh chunker, hashlib and raw SQL), not by trusting the engine's own report.
Exit status is non-zero if any check fails.
"""

from __future__ import annotations

import hashlib
import io
import json
import random
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from app.benchmarking.interfaces import validate_spec  # noqa: E402
from app.benchmarking.runner import run_chunking_comparison, run_pipeline_iteration  # noqa: E402
from app.chunking.rabin import RabinChunker  # noqa: E402
from app.core.config import CDCConfig, Settings  # noqa: E402
from app.core.services import build_services  # noqa: E402
from app.database.connection import connect  # noqa: E402
from app.database.init import initialize_database  # noqa: E402
from app.main import create_app  # noqa: E402
from app.reliability.faultlab import SCENARIOS, run_scenario  # noqa: E402
from app.simulation.model import SimulationInput, simulate  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
CFG = CDCConfig()


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    return bool(ok)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def independent_chunks(data: bytes) -> list[tuple[str, int]]:
    return [(sha(c.data), c.size) for c in RabinChunker(CFG).chunk_stream(io.BytesIO(data))]


class Store:
    """Raw read-only view of a store's SQLite file and chunk directory (no engine code)."""

    def __init__(self, settings: Settings) -> None:
        self.s = settings

    def q(self, sql: str, *args):
        conn = sqlite3.connect(str(self.s.db_path))
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def chunk_files(self) -> dict[str, Path]:
        return {p.name: p for p in self.s.chunks_dir.rglob("*") if p.is_file()}

    def manifest(self, file_id: str) -> list[tuple[int, str, int]]:
        return self.q("SELECT chunk_index, chunk_hash, chunk_size FROM file_chunks WHERE file_id=? ORDER BY chunk_index", file_id)

    def refs_ok(self) -> bool:
        bad = self.q("SELECT c.hash FROM chunks c WHERE c.ref_count <> (SELECT COUNT(*) FROM file_chunks f WHERE f.chunk_hash=c.hash)")
        return not bad and not self.q("SELECT * FROM v_ref_count_mismatch")

    def integrity(self) -> tuple[str, list]:
        conn = sqlite3.connect(str(self.s.db_path))
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            return conn.execute("PRAGMA integrity_check").fetchone()[0], conn.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            conn.close()


def main() -> int:
    original = random.Random(6006).randbytes(768 * 1024)
    modified = original[:700] + b"<<61 bytes inserted near the beginning of the file for Test C>>!!" + original[700:]
    with tempfile.TemporaryDirectory(prefix="chunkvault-verify6-") as tmp:
        root = Path(tmp)
        settings = Settings(data_dir=root / "data", db_path=root / "data" / "v.db", cors_origins=("http://localhost:5173",),
                            staging_stale_seconds=0)
        st = Store(settings)
        with TestClient(create_app(settings), raise_server_exceptions=False, base_url="http://localhost") as c:
            def put(data, name):
                r = c.post("/api/files", files={"file": (name, data)})
                assert r.status_code == 201, r.text
                return r.json()

            def get_file(fid):
                return c.get(f"/api/files/{fid}").json()

            def stats():
                return c.get("/api/storage/stats").json()

            # ------------------------------------------------------------ Test A
            print("Test A - original upload")
            a = put(original, "original.bin")
            exp = independent_chunks(original)
            man = st.manifest(a["file_id"])
            files = st.chunk_files()
            check("A: file record created and complete", st.q("SELECT status, original_size, sha256 FROM files WHERE file_id=?", a["file_id"]) == [("complete", len(original), sha(original))])
            check("A: manifest equals an independent re-chunking (order, hashes, sizes)", [(h, s) for _, h, s in man] == exp, f"{len(man)} chunks")
            check("A: manifest indices are 0..n-1 in order", [i for i, _, _ in man] == list(range(len(man))))
            check("A: chunk sizes respect min/max (last may be short)", all(CFG.min_chunk_size <= s <= CFG.max_chunk_size for _, s in exp[:-1]) and exp[-1][1] <= CFG.max_chunk_size)
            check("A: every manifest chunk exists on disk and re-hashes to its name", all(h in files and sha(files[h].read_bytes()) == h for _, h, _ in man))
            check("A: sizes add up to the file size", sum(s for _, _, s in man) == len(original))
            check("A: reference counts equal manifest occurrences (raw SQL)", st.refs_ok() and all(r == 1 for (r,) in st.q("SELECT ref_count FROM chunks")))
            check("A: one physical file per distinct chunk", len(files) == len({h for h, _ in exp}))

            # ------------------------------------------------------------ Test B
            print("Test B - exact duplicate")
            before = set(st.chunk_files())
            b = put(original, "copy.bin")
            s_b = stats()
            check("B: a second logical file record exists", len(st.q("SELECT 1 FROM files")) == 2 and b["file_id"] != a["file_id"])
            check("B: no new chunk payload files", set(st.chunk_files()) == before and b["new_chunks"] == 0 and b["bytes_written"] == 0)
            check("B: every reference count is 2", all(r == 2 for (r,) in st.q("SELECT ref_count FROM chunks")) and st.refs_ok())
            check("B: savings 50.0% and ratio 2.0 reported", s_b["storage_savings_percent"] == 50.0 and s_b["deduplication_ratio"] == 2.0,
                  f"{s_b['storage_savings_percent']}% / {s_b['deduplication_ratio']}x")

            # ------------------------------------------------------------ Test C
            print("Test C - modified version (61 bytes inserted at offset 700)")
            old_hashes = {h for h, _ in exp}
            cc = put(modified, "modified.bin")
            exp_c = independent_chunks(modified)
            new_expected = {h for h, _ in exp_c} - old_hashes
            man_c = st.manifest(cc["file_id"])
            check("C: manifest equals an independent re-chunking of the modified data", [(h, s) for _, h, s in man_c] == exp_c)
            check("C: chunks that did not change are reused", cc["duplicate_chunks"] == len([1 for h, _ in exp_c if h in old_hashes]) and cc["duplicate_chunks"] > 0,
                  f"{cc['duplicate_chunks']} of {cc['chunk_count']} reused")
            check("C: only changed content produced new chunks", cc["new_chunks"] == len(new_expected) and 0 < cc["new_chunks"] < cc["chunk_count"],
                  f"{cc['new_chunks']} new chunk(s), {cc['bytes_written']} bytes written")
            check("C: boundaries resynchronised after the insertion (later chunks identical)", exp[-1] in exp_c and exp[len(exp) // 2] in exp_c)
            check("C: new chunk files exist for exactly the new hashes", new_expected <= set(st.chunk_files()) and len(st.chunk_files()) == len(old_hashes | new_expected))
            check("C: reference counts still equal manifest occurrences", st.refs_ok())

            # ------------------------------------------------------------ Test D
            print("Test D - restoration")
            for rec, data in ((a, original), (b, original), (cc, modified)):
                r = c.get(f"/api/files/{rec['file_id']}/download")
                check(f"D: {rec['filename']} restored byte-for-byte", r.status_code == 200 and sha(r.content) == sha(data) == rec["sha256"]
                      and r.headers.get("x-chunkvault-sha256") == sha(data))
            check("D: restores leave no temporary files behind", not list(settings.staging_dir.iterdir()))

            # ------------------------------------------------------------ Test E
            print("Test E - safe deletion")
            files_a = {h for h, _ in exp}
            d1 = c.delete(f"/api/files/{a['file_id']}").json()
            check("E: deleting the original reclaims nothing that is still shared", d1["chunks_reclaimed"] == 0 and d1["chunks_released"] == len(exp))
            for rec, data in ((b, original), (cc, modified)):
                r = c.get(f"/api/files/{rec['file_id']}/download")
                check(f"E: {rec['filename']} still restores", r.status_code == 200 and sha(r.content) == sha(data))
            check("E: reference counts remain consistent after deletion", st.refs_ok())
            d2 = c.delete(f"/api/files/{b['file_id']}").json()
            only_ab = files_a - {h for h, _ in exp_c}
            check("E: deleting the last file that used a chunk reclaims exactly those chunks", d2["chunks_reclaimed"] == len(only_ab),
                  f"{d2['chunks_reclaimed']} reclaimed, expected {len(only_ab)}")
            check("E: the remaining file's chunks are intact and nothing extra remains", set(st.chunk_files()) == {h for h, _ in exp_c} and st.refs_ok())
            r = c.get(f"/api/files/{cc['file_id']}/download")
            check("E: the remaining file still restores byte-for-byte", r.status_code == 200 and sha(r.content) == sha(modified))
            check("E: a repeated delete is a clean 404 and changes nothing", c.delete(f"/api/files/{a['file_id']}").status_code == 404)

            # ------------------------------------------------------------ Test F
            print("Test F - integrity failure")
            solo = [h for (h,) in st.q("SELECT hash FROM chunks WHERE ref_count=1 ORDER BY hash")]
            victim = solo[0]
            p = st.chunk_files()[victim]
            raw = bytearray(p.read_bytes())
            raw[len(raw) // 2] ^= 0x01
            p.write_bytes(bytes(raw))
            audit = c.post("/api/integrity/audit", json={}).json()
            check("F: the audit detects exactly the corrupted chunk", audit["counts_by_kind"] == {"corrupt_chunk": 1}
                  and [f["subject"] for f in audit["findings"]] == [victim])
            r = c.get(f"/api/files/{cc['file_id']}/download")
            check("F: restoration fails explicitly (500 chunk_corrupt) with no file payload",
                  r.status_code == 500 and r.json()["error"]["code"] == "chunk_corrupt" and "content-disposition" not in r.headers
                  and r.headers["content-type"].startswith("application/json"))
            check("F: no partial output remains", not list(settings.staging_dir.iterdir()))
            rec = c.post("/api/integrity/reconcile").json()
            check("F: reconciliation does not claim or perform a repair", p.read_bytes() == bytes(raw)
                  and any(f["kind"] == "corrupt_chunk" for f in rec["remaining"]["findings"]))
            check("F: the failed restore is recorded as failed", ("restore", "failed") in st.q("SELECT operation_type, status FROM operations"))
            p.write_bytes(bytes(raw))

            # ------------------------------------------------------------ DB integrity (throw-away store)
            print("Database integrity")
            s2 = Settings(data_dir=root / "d2", db_path=root / "d2" / "i.db", cors_origins=("http://localhost:5173",), staging_stale_seconds=0)
            s2.ensure_directories()
            initialize_database(s2.db_path)
            svc = build_services(s2)
            zeros = bytes(300 * 1024)  # chunks of 128K, 128K, 44K: the first two are identical
            z = svc.uploader.upload("zeros.bin", io.BytesIO(zeros))
            other = svc.uploader.upload("other.bin", io.BytesIO(random.Random(3).randbytes(100 * 1024)))
            st2 = Store(s2)
            check("DB: repeated chunk inside one file counts once per occurrence",
                  sorted(r for (r,) in st2.q("SELECT c.ref_count FROM chunks c WHERE c.hash IN (SELECT chunk_hash FROM file_chunks WHERE file_id=?)", z.file_id)) == [1, 2]
                  and st2.refs_ok())
            integ, fkc = st2.integrity()
            check("DB: PRAGMA integrity_check = ok", integ == "ok", integ)
            check("DB: PRAGMA foreign_key_check is empty", fkc == [], str(fkc))
            conn = connect(s2.db_path)
            check("DB: foreign keys are ON for every new connection", conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
                  and connect(s2.db_path).execute("PRAGMA foreign_keys").fetchone()[0] == 1)
            shared = st2.q("SELECT hash FROM chunks WHERE ref_count=2")[0][0]

            def fails(sql, *args):
                try:
                    conn.execute(sql, args)
                    return False
                except sqlite3.IntegrityError:
                    return True

            check("DB: a chunk referenced by a manifest cannot be deleted", fails("DELETE FROM chunks WHERE hash=?", shared))
            check("DB: a manifest row cannot point at a missing chunk", fails("INSERT INTO file_chunks VALUES (?,?,?,?)", z.file_id, 99, "f" * 64, 1))
            check("DB: duplicate (file_id, chunk_index) is rejected", fails("INSERT INTO file_chunks VALUES (?,?,?,?)", z.file_id, 0, shared, 131072))
            check("DB: manifest rows are immutable", fails("UPDATE file_chunks SET chunk_index=50 WHERE file_id=? AND chunk_index=0", z.file_id))
            check("DB: invalid hashes / states are rejected", fails("INSERT INTO chunks(hash,size_bytes,state) VALUES ('xyz',1,'stored')")
                  and fails("INSERT INTO chunks(hash,size_bytes,state) VALUES (?,1,'bogus')", "a" * 64))
            conn.close()
            snap = (s2.db_path.read_bytes(), sorted(p.name for p in s2.chunks_dir.rglob("*") if p.is_file()))
            c2 = connect(s2.db_path)
            c2.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            c2.close()
            snap = (s2.db_path.read_bytes(), sorted(p.name for p in s2.chunks_dir.rglob("*") if p.is_file()))
            svc.auditor.verify_store()
            c3 = connect(s2.db_path)
            c3.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            c3.close()
            check("DB: the integrity audit is read-only (database file and chunk files unchanged)",
                  (s2.db_path.read_bytes(), sorted(p.name for p in s2.chunks_dir.rglob("*") if p.is_file())) == snap)
            svc.deleter.delete(z.file_id)
            check("DB: deleting a file never removes a chunk another file still references", st2.refs_ok()
                  and all(h in st2.chunk_files() for (h,) in st2.q("SELECT chunk_hash FROM file_chunks")))
            check("DB: delete is predictable on a missing file", _raises(lambda: svc.deleter.delete(z.file_id)))

        # ---------------------------------------------------------------- Test G + reliability
        print("Test G / reliability - the nine fault scenarios (isolated stores)")
        lab_settings = Settings(data_dir=root / "app-data", db_path=root / "app-data" / "app.db", cors_origins=("http://localhost:5173",))
        default_off = _raises(lambda: run_scenario("cdc_failure", lab_settings))
        check("G: the fault lab is disabled by default", default_off)
        lab_settings = Settings(data_dir=root / "app-data", db_path=root / "app-data" / "app.db", cors_origins=("http://localhost:5173",), fault_lab_enabled=True)
        before_tmp = set(Path(tempfile.gettempdir()).glob("chunkvault-faultlab-*"))
        for sid in SCENARIOS:
            r = run_scenario(sid, lab_settings)
            ok = (r["all_expectations_met"] and r["second_recovery_actions"] == 0 and r["isolation"]["temporary_store_removed"]
                  and r["recovery_status"] in ("verified_clean", "verified_with_unrepairable_damage"))
            control = [x for x in r["restores"] if x["filename"] == "control.bin"]
            ok = ok and control and control[0]["matches_original"] is True
            check(f"G: {sid}", ok, f"{r['recovery_status']}; actions={len(r['recovery_actions'])}; second run={r['second_recovery_actions']}; "
                                   f"orphans {r['before_recovery']['orphan_chunk_files']}->{r['after_recovery']['orphan_chunk_files']}")
        check("G: the fault lab never created the application's data directory", not lab_settings.data_dir.exists())
        check("G: no experiment store remains", set(Path(tempfile.gettempdir()).glob("chunkvault-faultlab-*")) == before_tmp)

        # ---------------------------------------------------------------- Benchmark verification
        print("Benchmark verification")
        spec = validate_spec("pipeline", "identical", 128 * 1024, 1, 9)
        it1 = run_pipeline_iteration(spec, CFG, 1)
        it2 = run_pipeline_iteration(spec, CFG, 2)
        check("BM: identical input -> identical work in every iteration", [(u["chunk_count"], u["new_chunks"], u["reused_chunks"]) for u in it1["uploads"]]
              == [(u["chunk_count"], u["new_chunks"], u["reused_chunks"]) for u in it2["uploads"]])
        for u in it1["uploads"] + it1["restores"]:
            if not (abs(sum(u["stages_seconds"].values()) - u["total_seconds"]) < 1e-9 and all(v >= 0 for v in u["stages_seconds"].values())):
                check("BM: stage timings sum exactly to the wall time (no overlap)", False, u["name"])
                break
        else:
            check("BM: stage timings sum exactly to the wall time (no overlap)", True)
        check("BM: throughput = bytes / seconds", all(abs(u["throughput_mib_s"] - (u["size_bytes"] / 1048576) / u["total_seconds"]) < 1e-9 for u in it1["uploads"]))
        sto = it1["storage"]
        check("BM: dedup ratio and savings recompute from logical/unique bytes",
              abs(sto["dedup_ratio"] - sto["logical_bytes"] / sto["unique_chunk_bytes"]) < 1e-12
              and abs(sto["savings_percent"] - 100 * (sto["logical_bytes"] - sto["unique_chunk_bytes"]) / sto["logical_bytes"]) < 1e-9
              and sto["dedup_ratio"] == 2.0)
        check("BM: restored files verified independently", all(r["integrity_verified"] for r in it1["restores"]))
        # instrumentation overhead: the same upload without any timers
        plain = []
        data = random.Random(9).randbytes(128 * 1024)
        for _ in range(5):
            with tempfile.TemporaryDirectory() as t:
                sx = Settings(data_dir=Path(t) / "d", db_path=Path(t) / "d" / "p.db", cors_origins=("http://localhost:5173",))
                sx.ensure_directories()
                initialize_database(sx.db_path)
                sv = build_services(sx)
                t0 = time.perf_counter()
                sv.uploader.upload("p", io.BytesIO(data))
                plain.append(time.perf_counter() - t0)
        instr = [run_pipeline_iteration(validate_spec("pipeline", "random", 128 * 1024, 1, 9), CFG, i)["uploads"][0]["total_seconds"] for i in range(5)]
        ratio = statistics.median(instr) / statistics.median(plain)
        check("BM: timer overhead is small (instrumented / plain median upload)", 0.7 < ratio < 1.5,
              f"plain {statistics.median(plain) * 1000:.1f} ms vs instrumented {statistics.median(instr) * 1000:.1f} ms (x{ratio:.2f})")
        cmp1 = run_chunking_comparison(validate_spec("chunking_comparison", None, 64 * 1024, 1, 4), CFG, lambda *a: None)
        check("BM: CDC and fixed-size are compared on identical bytes", all(x["cdc"]["logical_bytes"] == x["fixed"]["logical_bytes"] == sum(x["versions"]) for x in cmp1["cases"])
              and len(cmp1["cases"]) == 7)

        # ---------------------------------------------------------------- Simulation verification
        print("Capacity model verification")

        def sim(**kw):
            base = dict(file_count=1000, avg_file_size_bytes=1_000_000, avg_chunks_per_file=10.0, duplicate_percent=0.0, metadata_bytes_per_file=100,
                        metadata_bytes_per_unique_chunk=50, metadata_bytes_per_manifest_row=20, monthly_growth_percent=0.0, retention_months=0, horizon_months=12)
            base.update(kw)
            return simulate(SimulationInput(**base))

        o = sim(duplicate_percent=25.0)["outputs"]
        check("SIM: hand-computed example (1000 x 1,000,000 B, 10 chunks, 25% duplicates)",
              o["logical_bytes"] == 1e9 and o["physical_chunk_bytes"] == 7.5e8 and o["unique_chunks"] == 7500 and o["metadata_bytes"] == 675000
              and o["total_disk_bytes"] == 7.5e8 + 675000 and abs(o["dedup_ratio"] - 4 / 3) < 1e-12)
        check("SIM: zero duplicates -> physical == logical", sim(duplicate_percent=0.0)["outputs"]["physical_chunk_bytes"] == 1e9)
        check("SIM: 100% duplicates -> no physical chunks, undefined ratio", sim(duplicate_percent=100.0)["outputs"]["dedup_ratio"] is None)
        check("SIM: nearly 100% duplicates is metadata-dominated", sim(duplicate_percent=99.9, avg_file_size_bytes=1_000_000)["outputs"]["metadata_percent_of_total"] > 5)
        big = sim(file_count=10**12, avg_file_size_bytes=10**12, avg_chunks_per_file=6.1e7, monthly_growth_percent=50.0, horizon_months=120)
        check("SIM: very large inputs stay finite", all(isinstance(v, float) and v == v and v != float("inf") for v in (big["outputs"]["total_disk_bytes"], big["growth_summary"]["final_total_bytes"])))
        d1 = sim(avg_chunks_per_file=None, avg_file_size_bytes=1024 * 1024)["inputs"]["avg_chunks_per_file_used"]
        d2 = sim(avg_chunks_per_file=None, avg_file_size_bytes=1_000_000)["inputs"]["avg_chunks_per_file_used"]
        check("SIM: the default chunk size is binary 16 KiB (1 MiB -> 64 chunks; 1,000,000 B -> 62, not 63)", d1 == 64 and d2 == 62, f"{d1} / {d2}")
        with TestClient(create_app(Settings(data_dir=root / "sim", db_path=root / "sim" / "s.db", cors_origins=("http://localhost:5173",))),
                        raise_server_exceptions=False, base_url="http://localhost") as c:
            codes = {k: c.post("/api/simulations", json={k: v}).status_code for k, v in
                     {"file_count": 0, "duplicate_percent": 101, "avg_file_size_bytes": 0, "monthly_growth_percent": 99, "retention_months": 500}.items()}
            check("SIM: invalid parameters are rejected (zero files, >100% duplicates, zero size, ...)", set(codes.values()) == {422}, str(codes))
            check("SIM: the simulator is labelled a mathematical model", "not a measurement" in c.post("/api/simulations", json={}).json()["model"])

    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:  # noqa: BLE001
        return True


if __name__ == "__main__":
    raise SystemExit(main())
