"""Benchmark runner: pipeline measurements and the CDC-vs-fixed comparison.

Everything here is measured, never preset. Pipeline benchmarks drive the REAL services (the same
uploader, restorer and chunk store the API uses) against a private temporary store, with stage
timers installed around the real components. Run it in a worker process (see worker.py) so the
timers, which patch module-level functions for the duration of a run, cannot affect the server.
"""

from __future__ import annotations

import hashlib
import io
import os
import platform
import shutil
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from app.benchmarking import datasets
from app.benchmarking.interfaces import MIB, BenchmarkSpec
from app.benchmarking.profiler import Profiler, summarize
from app.chunking.rabin import RabinChunker
from app.core.config import CDCConfig, Settings
from app.core.services import Services, build_services
from app.database.connection import connect
from app.database.init import initialize_database
from app.storage.stats import compute_stats

Progress = Callable[[str, int, int, str], None]

METHODOLOGY = [
    "Every pipeline benchmark runs the real upload and restore code on a private temporary store; the application's data is never used.",
    "Datasets are generated from a seed (random.Random(seed)), so the same workload, size and seed give byte-identical input every time.",
    "Wall-clock time uses time.perf_counter() around the complete uploader.upload() / restorer.restore_to_file() call.",
    "Stage timings are exclusive: when one stage runs inside another (for example the SHA-256 re-hash inside the chunk store's verify), the outer stage is paused, so stages never overlap and sum to at most the wall-clock time. 'other' is the remainder (Python glue, locking, the output file write).",
    "Stage definitions - upload_staging: receiving the upload into a staged file and fsync; cdc: the Rabin chunker including reading the staged file; sha256_identity: hashing each chunk for its identity; dedup_read: reading existing chunks from disk to verify them before reuse; sha256_verify: re-hashing during that check and during staging; chunk_persistence: writing, fsync and atomic publication of new chunks; metadata: SQLite connection, repository calls, BEGIN and COMMIT.",
    "Restore stages - chunk_read: reading chunk files; sha256_verify: per-chunk verification; metadata: SQLite; other: the output write and the whole-file hash.",
    "Each iteration uses a fresh store with identical input, so the spread between iterations shows measurement noise, not different work. The first iteration may include cold caches.",
    "Throughput is bytes / seconds for the whole operation. The chunker is pure Python, so absolute numbers are specific to this machine and implementation.",
    "Database overhead is the growth of the SQLite file (after a full checkpoint) over the freshly initialised schema; it is page-granular and includes all tables and indexes.",
]


def environment(cdc: CDCConfig) -> dict:
    return {
        "python": platform.python_version(), "implementation": platform.python_implementation(),
        "platform": platform.platform(), "machine": platform.machine(), "cpu_count": os.cpu_count(),
        "sqlite_version": sqlite3.sqlite_version, "chunker": "pure-Python Rabin-style CDC",
        "cdc": {"min_chunk_size": cdc.min_chunk_size, "avg_chunk_size": cdc.avg_chunk_size,
                "max_chunk_size": cdc.max_chunk_size, "window_size": cdc.window_size,
                "boundary_divisor": cdc.boundary_divisor},
    }


# ---------------------------------------------------------------- instrumentation

class _TimedChunker:
    def __init__(self, real, prof: Profiler) -> None:
        self._real, self._prof = real, prof

    def chunk_stream(self, stream):
        it = iter(self._real.chunk_stream(stream))
        while True:
            with self._prof.section("cdc"):
                try:
                    chunk = next(it)
                except StopIteration:
                    return
            yield chunk


@contextmanager
def instrumented(svc: Services, prof: Profiler, *, phase: str) -> Iterator[None]:
    """Install stage timers around the real components for one phase ('upload' or 'restore'),
    restoring everything afterwards."""
    import app.deduplication.upload as upload_mod
    import app.restoration.engine as restore_mod

    undo: list[Callable[[], None]] = []

    def patch(obj, name, value):
        had_instance = name in vars(obj)
        original = vars(obj).get(name)
        setattr(obj, name, value)
        undo.append(lambda: (setattr(obj, name, original) if had_instance else delattr(obj, name)))

    def timed(fn, name):
        def wrapper(*a, **k):
            with prof.section(name):
                return fn(*a, **k)
        return wrapper

    hasher, store, repo = svc.hasher, svc.store, svc.repo
    raw_digest = hasher.digest

    def digest(data):
        if prof.current() in ("sha256", "sha256_verify"):
            return raw_digest(data)  # already attributed to the enclosing stage
        with prof.section("sha256"):
            return raw_digest(data)

    patch(hasher, "digest", digest)
    patch(hasher, "verify", timed(hasher.verify, "sha256_verify"))
    for name in dir(repo):
        if not name.startswith("_") and callable(getattr(repo, name)):
            patch(repo, name, timed(getattr(repo, name), "metadata"))

    mods = [upload_mod] if phase == "upload" else [restore_mod]
    for mod in mods:
        orig_connect = mod.connect

        def connect_w(*a, _orig=orig_connect, **k):
            with prof.section("metadata"):
                return _orig(*a, **k)

        @contextmanager
        def tx_w(conn):
            with prof.section("metadata"):
                conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                with prof.section("metadata"):
                    conn.execute("ROLLBACK")
                raise
            else:
                with prof.section("metadata"):
                    conn.execute("COMMIT")

        original_connect, original_tx = mod.connect, mod.transaction
        mod.connect, mod.transaction = connect_w, tx_w
        undo.append(lambda m=mod, c=original_connect, t=original_tx: (setattr(m, "connect", c), setattr(m, "transaction", t)))

    if phase == "upload":
        patch(store, "verify", timed(store.verify, "dedup_read"))
        patch(store, "stage", timed(store.stage, "chunk_persistence"))
        patch(store, "publish", timed(store.publish, "chunk_persistence"))
        patch(svc.uploader, "_stage_upload", timed(svc.uploader._stage_upload, "upload_staging"))
        patch(svc.uploader, "_chunker", _TimedChunker(svc.chunker, prof))
    else:
        patch(store, "read", timed(store.read, "chunk_read"))
    try:
        yield
    finally:
        for fn in reversed(undo):
            fn()


# ---------------------------------------------------------------- pipeline benchmark

def _db_bytes(settings: Settings) -> int:
    conn = connect(settings.db_path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    return settings.db_path.stat().st_size


def _stage_report(prof: Profiler, total: float) -> tuple[dict, dict]:
    stages = prof.snapshot()
    stages["other"] = max(0.0, total - sum(stages.values()))
    pct = {k: (100.0 * v / total if total > 0 else 0.0) for k, v in stages.items()}
    return stages, pct


def run_pipeline_iteration(spec: BenchmarkSpec, cdc: CDCConfig, iteration: int) -> dict:
    root = Path(tempfile.mkdtemp(prefix="chunkvault-bench-"))
    try:
        settings = Settings(data_dir=root / "data", db_path=root / "data" / "bench.db",
                            cors_origins=("http://localhost:5173",), cdc=cdc, max_upload_bytes=8 * MIB)
        settings.ensure_directories()
        initialize_database(settings.db_path)
        baseline_db = _db_bytes(settings)
        svc = build_services(settings)
        prof = Profiler()
        versions = datasets.workload_versions(spec.workload, spec.size_bytes, spec.seed)

        uploads, stored = [], []
        for name, data in versions:
            prof.reset()
            with instrumented(svc, prof, phase="upload"):
                t0 = time.perf_counter()
                res = svc.uploader.upload(name, io.BytesIO(data))
                total = time.perf_counter() - t0
            stages, pct = _stage_report(prof, total)
            stored.append((res.file_id, name, data, hashlib.sha256(data).hexdigest()))
            uploads.append({
                "name": name, "size_bytes": len(data), "total_seconds": total,
                "throughput_mib_s": (len(data) / MIB) / total if total > 0 else None,
                "stages_seconds": stages, "stages_percent": pct, "chunk_count": res.chunk_count,
                "new_chunks": res.new_chunks, "reused_chunks": res.duplicate_chunks, "bytes_written": res.bytes_written,
                "sha256_matches_source": res.sha256 == stored[-1][3]})

        restores = []
        for fid, name, data, digest in stored:
            dest = root / f"restored-{fid}"
            prof.reset()
            with instrumented(svc, prof, phase="restore"):
                t0 = time.perf_counter()
                result = svc.restorer.restore_to_file(fid, dest)
                total = time.perf_counter() - t0
            stages, pct = _stage_report(prof, total)
            ok = hashlib.sha256(dest.read_bytes()).hexdigest() == digest  # independent check, not timed
            dest.unlink(missing_ok=True)
            restores.append({"name": name, "size_bytes": result.size, "total_seconds": total,
                             "throughput_mib_s": (result.size / MIB) / total if total > 0 else None,
                             "stages_seconds": stages, "stages_percent": pct, "integrity_verified": ok})

        stats = compute_stats(settings, svc.store)
        db_total = _db_bytes(settings)
        conn = connect(settings.db_path)
        try:
            manifest_rows = conn.execute("SELECT COUNT(*) FROM file_chunks").fetchone()[0]
        finally:
            conn.close()
        generated = sum(u["chunk_count"] for u in uploads)
        storage = {
            "logical_bytes": stats.logical_bytes, "unique_chunk_bytes": stats.unique_chunk_bytes,
            "dedup_ratio": stats.deduplication_ratio, "savings_percent": stats.storage_savings_percent,
            "chunks_generated": generated, "unique_chunks": stats.unique_chunks,
            "reused_chunks": sum(u["reused_chunks"] for u in uploads),
            "avg_chunk_bytes": (stats.unique_chunk_bytes / stats.unique_chunks) if stats.unique_chunks else None,
            "database_bytes": db_total, "database_overhead_bytes": max(0, db_total - baseline_db),
            "manifest_rows": manifest_rows,
            "overhead_bytes_per_manifest_row": (max(0, db_total - baseline_db) / manifest_rows) if manifest_rows else None,
            "chunk_files": stats.chunk_files, "chunk_store_bytes": stats.chunk_store_bytes}
        return {"iteration": iteration, "uploads": uploads, "restores": restores, "storage": storage}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def summarize_pipeline(iterations: list[dict]) -> dict:
    def col(f):
        return [f(it) for it in iterations]

    up_total = col(lambda it: sum(u["total_seconds"] for u in it["uploads"]))
    up_bytes = col(lambda it: sum(u["size_bytes"] for u in it["uploads"]))
    re_total = col(lambda it: sum(r["total_seconds"] for r in it["restores"]))
    re_bytes = col(lambda it: sum(r["size_bytes"] for r in it["restores"]))
    stage_names = sorted({k for it in iterations for u in it["uploads"] for k in u["stages_seconds"]})
    return {
        "upload_total_seconds": summarize(up_total),
        "upload_throughput_mib_s": summarize([(b / MIB) / t for b, t in zip(up_bytes, up_total) if t > 0]),
        "restore_total_seconds": summarize(re_total),
        "restore_throughput_mib_s": summarize([(b / MIB) / t for b, t in zip(re_bytes, re_total) if t > 0]),
        "dedup_ratio": summarize([v for v in col(lambda it: it["storage"]["dedup_ratio"]) if v is not None]),
        "savings_percent": summarize([v for v in col(lambda it: it["storage"]["savings_percent"]) if v is not None]),
        "upload_stage_seconds": {s: summarize(col(lambda it, s=s: sum(u["stages_seconds"].get(s, 0.0) for u in it["uploads"])))
                                 for s in stage_names},
        "integrity_all_verified": all(r["integrity_verified"] for it in iterations for r in it["restores"]),
        "total_execution_seconds": sum(up_total) + sum(re_total),
    }


def run_pipeline(spec: BenchmarkSpec, cdc: CDCConfig, progress: Progress) -> dict:
    iterations = []
    for i in range(1, spec.iterations + 1):
        progress("pipeline", i - 1, spec.iterations, f"iteration {i} of {spec.iterations}: uploading and restoring")
        iterations.append(run_pipeline_iteration(spec, cdc, i))
    progress("pipeline", spec.iterations, spec.iterations, "done")
    return {"kind": "pipeline", "iterations": iterations, "summary": summarize_pipeline(iterations)}


# ---------------------------------------------------------------- CDC vs fixed-size

def _fixed_chunks(data: bytes, size: int) -> list[bytes]:
    return [data[i:i + size] for i in range(0, len(data), size)]


def _scheme_result(versions: list[bytes], chunker: Callable[[bytes], list[bytes]]) -> tuple[dict, float]:
    t0 = time.perf_counter()
    per_version = [chunker(v) for v in versions]
    digests = [[hashlib.sha256(c).digest() for c in chunks] for chunks in per_version]  # identical hashing for both schemes
    elapsed = time.perf_counter() - t0
    seen: set[bytes] = set()
    total_chunks = reused_chunks = reused_bytes = unique_bytes = 0
    for chunks, hs in zip(per_version, digests):
        for c, h in zip(chunks, hs):
            total_chunks += 1
            if h in seen:
                reused_chunks += 1
                reused_bytes += len(c)
            else:
                seen.add(h)
                unique_bytes += len(c)
    logical = sum(len(v) for v in versions)
    return ({"chunk_count": total_chunks, "unique_chunks": total_chunks - reused_chunks,
             "avg_chunk_bytes": (logical / total_chunks) if total_chunks else None, "reused_chunks": reused_chunks,
             "reused_bytes": reused_bytes, "logical_bytes": logical, "unique_bytes": unique_bytes,
             "savings_percent": (100.0 * (logical - unique_bytes) / logical) if logical else None}, elapsed)


def run_chunking_comparison(spec: BenchmarkSpec, cdc: CDCConfig, progress: Progress) -> dict:
    fixed_size = spec.fixed_chunk_bytes or cdc.avg_chunk_size
    chunker = RabinChunker(cdc)

    def cdc_chunks(data: bytes) -> list[bytes]:
        return [c.data for c in chunker.chunk_stream(io.BytesIO(data))]

    def fixed_chunks(data: bytes) -> list[bytes]:
        return _fixed_chunks(data, fixed_size)

    cases = datasets.comparison_cases(spec.size_bytes, spec.seed)
    out = []
    for idx, case in enumerate(cases):
        progress("comparison", idx, len(cases), f"case {idx + 1} of {len(cases)}: {case['title']}")
        row = {"id": case["id"], "title": case["title"], "note": case["note"],
               "versions": [len(v) for v in case["versions"]]}
        for scheme, fn in (("cdc", cdc_chunks), ("fixed", fixed_chunks)):
            times, result = [], None
            for _ in range(spec.iterations):
                result, elapsed = _scheme_result(case["versions"], fn)
                times.append(elapsed)
            result["processing_seconds"] = summarize(times)
            row[scheme] = result
        delta = None
        if row["cdc"]["savings_percent"] is not None and row["fixed"]["savings_percent"] is not None:
            delta = row["cdc"]["savings_percent"] - row["fixed"]["savings_percent"]
        row["savings_difference_points"] = delta
        row["verdict"] = ("no data" if delta is None else "CDC saves more" if delta > 1.0
                          else "fixed-size saves more" if delta < -1.0 else "no meaningful difference")
        out.append(row)
    progress("comparison", len(cases), len(cases), "done")
    return {"kind": "chunking_comparison", "fixed_chunk_bytes": fixed_size, "cases": out}


def run_benchmark(spec: BenchmarkSpec, cdc: CDCConfig, progress: Progress) -> dict:
    body = run_pipeline(spec, cdc, progress) if spec.kind == "pipeline" else run_chunking_comparison(spec, cdc, progress)
    return {**body, "environment": environment(cdc), "methodology": METHODOLOGY}
