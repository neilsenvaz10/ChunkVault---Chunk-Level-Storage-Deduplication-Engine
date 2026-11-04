"""Benchmark worker process: ``python -m app.benchmarking.worker <spec.json> <result.json> <progress.json>``.

Runs one bounded benchmark and writes its result atomically. Isolating the run in its own process
keeps the stage timers (which patch module-level functions) away from the server and lets the
server enforce a hard time limit and cancel by terminating the process.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from app.benchmarking.interfaces import validate_spec
from app.benchmarking.runner import run_benchmark
from app.core.config import CDCConfig
from app.core.errors import scrub_paths


def _write_json(path: Path, payload: dict, *, attempts: int = 50) -> None:
    """Atomic write. On Windows os.replace fails with PermissionError while another process (the
    server polling for progress) has the target open, so retry briefly."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    for attempt in range(attempts):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.02)


def main(argv: list[str]) -> int:
    spec_path, result_path, progress_path = (Path(a) for a in argv[1:4])
    raw = json.loads(spec_path.read_text(encoding="utf-8"))
    started = datetime.now(timezone.utc).isoformat()

    def progress(phase: str, done: int, total: int, message: str) -> None:
        try:  # progress is informational: failing to report it must never fail the benchmark
            _write_json(progress_path, {"phase": phase, "done": done, "total": total, "message": message,
                                        "fraction": (done / total) if total else 0.0}, attempts=10)
        except OSError:
            pass

    try:
        spec = validate_spec(raw["kind"], raw.get("workload"), raw.get("size_bytes"), raw["iterations"], raw["seed"],
                             raw.get("fixed_chunk_bytes"))
        cdc = CDCConfig(**raw["cdc"])
        body = run_benchmark(spec, cdc, progress)
        _write_json(result_path, {
            "job_id": raw["job_id"], "status": "done", "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "spec": {"kind": spec.kind, "workload": spec.workload, "size_bytes": spec.size_bytes,
                     "iterations": spec.iterations, "seed": spec.seed, "fixed_chunk_bytes": spec.fixed_chunk_bytes},
            **body})
        return 0
    except Exception as exc:  # noqa: BLE001 - reported to the server as a failed job
        traceback.print_exc(file=sys.stderr)
        _write_json(result_path, {"job_id": raw.get("job_id"), "status": "failed", "started_at": started,
                                  "finished_at": datetime.now(timezone.utc).isoformat(),
                                  "error": scrub_paths(f"{type(exc).__name__}: {exc}")[:300]})
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
