"""Background benchmark jobs.

A job is one worker process (see worker.py). The server never runs benchmark code itself, so a
long benchmark cannot block requests; at most ONE job runs at a time; every job has a hard time
limit; and a job can be cancelled (the process is terminated and its private temporary store is
removed). Results are JSON files under data/benchmarks/ (git-ignored).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.benchmarking.interfaces import MAX_RUNTIME_SECONDS, BenchmarkSpec
from app.core.config import CDCConfig
from app.core.errors import ConflictError, NotFoundError, scrub_paths

JOB_ID = re.compile(r"^[0-9a-f]{12}$")
_BACKEND_DIR = Path(__file__).resolve().parents[2]


@dataclass
class _Job:
    job_id: str
    spec: BenchmarkSpec
    proc: subprocess.Popen
    started: float
    cancelled: bool = False
    timed_out: bool = False


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class BenchmarkJobs:
    def __init__(self, directory: Path, cdc: CDCConfig, max_seconds: float = MAX_RUNTIME_SECONDS) -> None:
        self._dir = Path(directory)
        self._cdc = cdc
        self._max_seconds = max_seconds
        self._lock = threading.Lock()
        self._job: _Job | None = None

    # -- paths ---------------------------------------------------------------
    def _p(self, job_id: str, suffix: str) -> Path:
        return self._dir / f"{job_id}.{suffix}"

    def _check_id(self, job_id: str) -> None:
        if not JOB_ID.fullmatch(job_id):
            raise NotFoundError("benchmark not found")

    # -- lifecycle -----------------------------------------------------------
    def start(self, spec: BenchmarkSpec) -> str:
        with self._lock:
            self._reap()
            if self._job is not None:
                raise ConflictError("a benchmark is already running")
            self._dir.mkdir(parents=True, exist_ok=True)
            job_id = uuid.uuid4().hex[:12]
            self._p(job_id, "spec.json").write_text(json.dumps({
                "job_id": job_id, "kind": spec.kind, "workload": spec.workload, "size_bytes": spec.size_bytes,
                "iterations": spec.iterations, "seed": spec.seed, "fixed_chunk_bytes": spec.fixed_chunk_bytes,
                "cdc": {"min_chunk_size": self._cdc.min_chunk_size, "avg_chunk_size": self._cdc.avg_chunk_size,
                        "max_chunk_size": self._cdc.max_chunk_size, "window_size": self._cdc.window_size}}), encoding="utf-8")
            with open(self._p(job_id, "log"), "wb") as log:
                proc = subprocess.Popen(
                    [sys.executable, "-m", "app.benchmarking.worker", str(self._p(job_id, "spec.json")),
                     str(self._p(job_id, "json")), str(self._p(job_id, "progress.json"))],
                    cwd=_BACKEND_DIR, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log)
            self._job = _Job(job_id, spec, proc, time.monotonic())
            return job_id

    def _reap(self) -> None:
        """Enforce the time limit and forget a finished process. Caller holds the lock."""
        job = self._job
        if job is None:
            return
        if job.proc.poll() is None:
            if time.monotonic() - job.started > self._max_seconds:
                job.timed_out = True
                self._kill(job)
            else:
                return
        self._job = None
        self._p(job.job_id, "spec.json").unlink(missing_ok=True)
        if not self._p(job.job_id, "json").exists():  # the worker died without reporting (e.g. import error)
            tail = ""
            try:
                lines = [l for l in self._p(job.job_id, "log").read_text(errors="replace").splitlines() if l.strip()]
                tail = scrub_paths(lines[-1])[:200] if lines else ""
            except OSError:
                pass
            self._p(job.job_id, "json").write_text(json.dumps({
                "job_id": job.job_id, "status": "failed",
                "error": f"worker exited with code {job.proc.returncode}: {tail}".strip()}), encoding="utf-8")

    def _kill(self, job: _Job) -> None:
        job.proc.terminate()
        try:
            job.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            job.proc.kill()
            job.proc.wait()
        self._remove_temp_stores()
        why = "cancelled" if job.cancelled else "timed out after %d seconds" % self._max_seconds
        self._p(job.job_id, "json").write_text(json.dumps({
            "job_id": job.job_id, "status": "cancelled" if job.cancelled else "failed", "error": None if job.cancelled else why,
            "spec": {"kind": job.spec.kind, "workload": job.spec.workload, "size_bytes": job.spec.size_bytes,
                     "iterations": job.spec.iterations, "seed": job.spec.seed}}), encoding="utf-8")

    @staticmethod
    def _remove_temp_stores() -> None:
        """Remove private stores left by a killed worker (only directories this subsystem creates)."""
        for p in Path(tempfile.gettempdir()).glob("chunkvault-bench-*"):
            shutil.rmtree(p, ignore_errors=True)

    def cancel(self, job_id: str) -> dict:
        self._check_id(job_id)
        with self._lock:
            job = self._job
            if job is None or job.job_id != job_id or job.proc.poll() is not None:
                raise NotFoundError("no running benchmark with that id")
            job.cancelled = True
            self._kill(job)
            self._job = None
            self._p(job_id, "spec.json").unlink(missing_ok=True)
        return self.status(job_id)

    def shutdown(self) -> None:
        with self._lock:
            if self._job and self._job.proc.poll() is None:
                self._job.cancelled = True
                self._kill(self._job)
            self._job = None

    # -- queries -------------------------------------------------------------
    def status(self, job_id: str) -> dict:
        self._check_id(job_id)
        with self._lock:
            self._reap()
            running = self._job is not None and self._job.job_id == job_id
        if running:
            return {"job_id": job_id, "status": "running", "progress": _read(self._p(job_id, "progress.json")), "result": None,
                    "error": None}
        result = _read(self._p(job_id, "json"))
        if result is None:
            if self._p(job_id, "spec.json").exists():  # finished between polls, result not yet visible
                return {"job_id": job_id, "status": "running", "progress": None, "result": None, "error": None}
            raise NotFoundError("benchmark not found")
        status = result.get("status", "done")
        if status == "failed":
            return {"job_id": job_id, "status": "failed", "progress": None, "result": None, "error": result.get("error")}
        if status == "cancelled":
            return {"job_id": job_id, "status": "cancelled", "progress": None, "result": None, "error": None}
        return {"job_id": job_id, "status": "done", "progress": None, "result": result, "error": None}

    def current(self) -> dict | None:
        with self._lock:
            self._reap()
            job = self._job
        if job is None:
            return None
        return {"job_id": job.job_id, "status": "running", "progress": _read(self._p(job.job_id, "progress.json")),
                "spec": {"kind": job.spec.kind, "workload": job.spec.workload, "size_bytes": job.spec.size_bytes,
                         "iterations": job.spec.iterations, "seed": job.spec.seed}}

    def list_results(self, limit: int = 50) -> list[dict]:
        out: list[dict[str, Any]] = []
        for p in sorted(self._dir.glob("*.json"), key=lambda q: q.stat().st_mtime, reverse=True):
            if p.name.endswith((".spec.json", ".progress.json")):
                continue
            r = _read(p)
            if not r or r.get("status") != "done":
                continue
            summ = r.get("summary") or {}
            out.append({
                "job_id": r["job_id"], "kind": r["kind"], "spec": r["spec"], "finished_at": r["finished_at"],
                "upload_throughput_mib_s": (summ.get("upload_throughput_mib_s") or {}).get("median"),
                "restore_throughput_mib_s": (summ.get("restore_throughput_mib_s") or {}).get("median"),
                "dedup_ratio": (summ.get("dedup_ratio") or {}).get("median"),
                "savings_percent": (summ.get("savings_percent") or {}).get("median"),
                "total_execution_seconds": summ.get("total_execution_seconds"),
            })
            if len(out) >= limit:
                break
        return out
