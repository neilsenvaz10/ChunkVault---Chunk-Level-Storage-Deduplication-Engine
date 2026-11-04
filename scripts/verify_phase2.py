"""Phase 2 end-to-end verification (tests A-G) on a throw-away store.

Run from the backend directory:
    .venv/Scripts/python ../scripts/verify_phase2.py

Everything happens in a temporary directory through the real HTTP API
(FastAPI TestClient). The development database and data/ are never touched.
Exit status is non-zero if any check fails.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="chunkvault-verify-") as tmp:
        tmp_path = Path(tmp)
        settings = Settings(data_dir=tmp_path / "data", db_path=tmp_path / "data" / "verify.db",
                            cors_origins=("http://localhost:5173",), staging_stale_seconds=0)
        with TestClient(create_app(settings), raise_server_exceptions=False, base_url="http://localhost") as c:
            def put(data, name):
                r = c.post("/api/files", files={"file": (name, data, "application/octet-stream")})
                assert r.status_code == 201, r.text
                return r.json()

            def stats():
                return c.get("/api/storage/stats").json()

            original = random.Random(2024).randbytes(1024 * 1024)
            inserted = original[:700] + b"<<61 bytes inserted near the beginning of the file, test C>>!" + original[700:]
            assert len(inserted) - len(original) == 61

            print("Test A - original upload")
            a = put(original, "original.bin")
            sa = stats()
            print(json.dumps({"size": a["original_size"], "sha256": a["sha256"], "chunks": a["chunk_count"],
                              "unique_chunk_bytes": sa["unique_chunk_bytes"], "chunk_files": sa["chunk_files"]}))
            check("A: size and sha256 recorded", a["original_size"] == len(original) and a["sha256"] == sha(original))
            check("A: every chunk newly stored", a["new_chunks"] == a["chunk_count"] and a["duplicate_chunks"] == 0)
            check("A: unique payload == file size", sa["unique_chunk_bytes"] == len(original))

            print("Test B - exact duplicate")
            b = put(original, "copy.bin")
            sb = stats()
            print(json.dumps({"new_chunks": b["new_chunks"], "bytes_written": b["bytes_written"],
                              "files": sb["total_files"], "unique_chunk_bytes": sb["unique_chunk_bytes"],
                              "chunk_files": sb["chunk_files"], "savings_percent": sb["storage_savings_percent"],
                              "dedup_ratio": sb["deduplication_ratio"]}))
            check("B: second logical file exists", sb["total_files"] == 2 and b["file_id"] != a["file_id"])
            check("B: no new payload bytes", sb["unique_chunk_bytes"] == sa["unique_chunk_bytes"] and b["bytes_written"] == 0)
            check("B: no new physical chunk files", sb["chunk_files"] == sa["chunk_files"])

            print("Test C - modified version (61 bytes inserted near the start)")
            m = put(inserted, "modified.bin")
            sm = stats()
            reused = m["duplicate_chunks"]
            print(json.dumps({"chunks": m["chunk_count"], "reused": reused, "new": m["new_chunks"],
                              "bytes_written": m["bytes_written"], "unique_chunk_bytes": sm["unique_chunk_bytes"]}))
            check("C: most chunks reused, a few new", m["new_chunks"] >= 1 and reused >= m["chunk_count"] - 3,
                  f"{reused}/{m['chunk_count']} reused, {m['new_chunks']} new")
            check("C: payload grew only by the new chunks",
                  sm["unique_chunk_bytes"] == sb["unique_chunk_bytes"] + m["bytes_written"])

            print("Test D - restoration")
            for rec, data in ((a, original), (b, original), (m, inserted)):
                r = c.get(f"/api/files/{rec['file_id']}/download")
                check(f"D: {rec['filename']} restored byte-for-byte",
                      r.status_code == 200 and sha(r.content) == sha(data) == rec["sha256"])

            print("Test E - delete the first file; the others must stay restorable")
            d = c.delete(f"/api/files/{a['file_id']}").json()
            print(json.dumps(d))
            check("E: shared chunks kept", d["chunks_released"] == a["chunk_count"])
            for rec, data in ((b, original), (m, inserted)):
                r = c.get(f"/api/files/{rec['file_id']}/download")
                check(f"E: {rec['filename']} still restorable", r.status_code == 200 and sha(r.content) == sha(data))
            check("E: audit clean after delete", c.post("/api/integrity/audit").json()["healthy"])

            print("Test F - corrupt a chunk in the isolated store")
            detail = c.get(f"/api/files/{b['file_id']}").json()
            victim = detail["chunks"][len(detail["chunks"]) // 2]["chunk_hash"]
            vp = settings.chunks_dir / victim[:2] / victim
            raw = bytearray(vp.read_bytes())
            raw[len(raw) // 2] ^= 0x01
            vp.write_bytes(bytes(raw))
            r = c.get(f"/api/files/{b['file_id']}/download")
            print(r.status_code, r.json()["error"]["code"])
            check("F: restore fails explicitly", r.status_code == 500 and r.json()["error"]["code"] == "chunk_corrupt")
            audit = c.post("/api/integrity/audit").json()
            found = [f for f in audit["findings"] if f["kind"] == "corrupt_chunk"]
            check("F: audit reports the corrupted chunk", [f["subject"] for f in found] == [victim])
            check("F: audit did not repair it", vp.read_bytes() == bytes(raw))

            print("Test G - interrupted upload (failure injected before the metadata commit)")
            svc = c.app.state.services
            before_files = c.get("/api/files").json()["total"]
            before_chunks = sorted(svc.store.iter_hashes())
            fresh = random.Random(77).randbytes(300 * 1024)
            orig = svc.repo.append_file_chunk
            calls = {"n": 0}

            def failing(*args, **kw):
                calls["n"] += 1
                if calls["n"] == 4:
                    raise RuntimeError("injected failure before metadata commit")
                return orig(*args, **kw)

            svc.repo.append_file_chunk = failing
            r = c.post("/api/files", files={"file": ("interrupted.bin", fresh, "application/octet-stream")})
            svc.repo.append_file_chunk = orig
            listing = c.get("/api/files").json()["items"]
            interrupted = [f for f in listing if f["filename"] == "interrupted.bin"]
            print(r.status_code, r.json()["error"]["code"], [f["status"] for f in interrupted])
            check("G: upload reported as failed", r.status_code == 500)
            check("G: no completed file for it", all(f["status"] != "complete" for f in interrupted))
            check("G: complete files unchanged", c.get("/api/files").json()["total"] == before_files + len(interrupted))
            check("G: no manifest rows or chunk rows for it",
                  all(not c.get(f"/api/files/{f['file_id']}").json()["chunks"] for f in interrupted))
            check("G: unreferenced bytes cleaned up on clean failure", sorted(svc.store.iter_hashes()) == before_chunks)
            # now model a hard crash that leaves published bytes behind
            class Crash(BaseException):
                pass

            orig_fin = svc.repo.finish_operation

            def die(conn, op, status, msg=None):
                if status.value == "succeeded":
                    raise Crash()
                return orig_fin(conn, op, status, msg)

            svc.repo.finish_operation = die
            try:
                svc.uploader.upload("crashed.bin", __import__("io").BytesIO(random.Random(78).randbytes(200 * 1024)))
            except Crash:
                pass
            svc.repo.finish_operation = orig_fin
            audit = c.post("/api/integrity/audit").json()
            print(json.dumps(audit["counts_by_kind"]))
            check("G: crash leftovers detected", audit["counts_by_kind"].get("orphan_chunk_file", 0) > 0
                  and audit["counts_by_kind"].get("stuck_operation", 0) == 1)
            check("G: no complete file points at missing content",
                  not any(f["kind"] == "missing_chunk_file" for f in audit["findings"]))
            rec = c.post("/api/integrity/reconcile").json()
            print(json.dumps(sorted({a["action"] for a in rec["actions"]})))
            check("G: reconciliation removed the orphans", sorted(svc.store.iter_hashes()) == before_chunks)
            check("G: only the deliberate corruption remains",
                  [f["kind"] for f in rec["remaining"]["findings"]] == ["corrupt_chunk"])

    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
