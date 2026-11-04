"""Isolated fault-injection experiments.

Every experiment creates its OWN temporary store (database + chunk store + staging) under the
system temp directory, injects one failure into the real upload/restore/delete/recovery code
paths of that private store, observes what the real audit and reconciler report, and then deletes
the store. It never receives the application's database or data directory, never runs
automatically, and is disabled unless ``CHUNKVAULT_ENABLE_FAULT_LAB=1``.

Failures are injected at the points the code really has:
  * ``Uploader.checkpoint`` named points (staged, t1_committed, chunk_published, chunks_ready,
    in_t2, after_commit) raise at exactly that step of the real upload protocol;
  * instance-level replacement (on the private services only) of the chunker, the chunk store
    or one repository method;
  * a ``SimulatedCrash`` (a ``BaseException``) stands for the process dying: the engine's own
    cleanup code does NOT run, exactly as after a real crash. An ordinary ``Exception`` exercises
    the engine's graceful failure handling instead.

The "expected" column of each scenario is declared up front in this file; the "observed" column is
read back from SQLite, the filesystem and the real auditor afterwards. Nothing is assumed.
"""

from __future__ import annotations

import io
import random
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.chunking.interfaces import ChunkingError
from app.core.config import CDCConfig, Settings
from app.core.errors import ChunkVaultError, ConflictError
from app.core.services import Services, build_services
from app.database.connection import connect
from app.database.init import initialize_database
from app.storage.filesystem import ChunkStatus

MAX_RUN_SECONDS = 30.0
CONTROL_SIZE = 96 * 1024
VICTIM_SIZE = 128 * 1024
_RUN_LOCK = threading.Lock()


class FaultLabDisabledError(ChunkVaultError):
    code = "fault_lab_disabled"
    status_code = 403


class UnknownScenarioError(ChunkVaultError):
    code = "unknown_scenario"
    status_code = 422


class SimulatedCrash(BaseException):
    """The process dying. Deliberately NOT an Exception, so engine cleanup code does not run."""


def seeded(seed: int, size: int) -> bytes:
    return random.Random(seed).randbytes(size)


@dataclass(frozen=True)
class Expect:
    key: str  # dotted path into the observed facts
    label: str
    op: str  # "eq" | "gt0"
    value: Any = None

    def describe(self) -> str:
        return "> 0" if self.op == "gt0" else f"= {self.value}"


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    operation: str
    fault_point: str
    kind: str  # "crash" (process dies) | "exception" (the engine handles the failure)
    description: str
    steps: tuple[str, ...]
    fault_step: int  # index in `steps` of the injected fault
    expects: tuple[Expect, ...]
    run: Callable[["Lab"], dict]


# ---------------------------------------------------------------- isolated environment

class Lab:
    """One private temporary store plus a record of what was uploaded (for restore checks)."""

    def __init__(self, cdc: CDCConfig) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="chunkvault-faultlab-"))
        self.settings = Settings(
            data_dir=self.root / "data", db_path=self.root / "data" / "lab.db",
            cors_origins=("http://localhost:5173",), cdc=cdc, max_upload_bytes=1024 * 1024,
            staging_stale_seconds=0, writer_lock_timeout_seconds=2.0)
        self.settings.ensure_directories()
        initialize_database(self.settings.db_path)
        self.svc: Services = build_services(self.settings)
        self.originals: dict[str, bytes] = {}

    def upload(self, name: str, data: bytes):
        r = self.svc.uploader.upload(name, io.BytesIO(data))
        self.originals[r.file_id] = data
        return r

    def conn(self):
        return connect(self.settings.db_path)

    def reset_faults(self) -> None:
        """Remove every injected override so recovery runs the real code."""
        self.svc.store.__dict__.pop("delete", None)
        self.svc.repo.__dict__.pop("delete_unreferenced_chunk_rows", None)
        self.svc.uploader.__dict__.pop("checkpoint", None)
        self.svc.uploader._chunker = self.svc.chunker

    def cleanup(self) -> bool:
        shutil.rmtree(self.root, ignore_errors=True)
        return not self.root.exists()


def _rows(lab: Lab, sql: str, *args):
    c = lab.conn()
    try:
        return [tuple(r) for r in c.execute(sql, args)]
    finally:
        c.close()


def snapshot(lab: Lab) -> dict:
    """Facts about the private store, read straight from SQLite and the filesystem."""
    files = _rows(lab, "SELECT filename, status FROM files ORDER BY created_at, filename")
    ops = _rows(lab, "SELECT operation_type, status FROM operations ORDER BY operation_id")
    row_hashes = {r[0] for r in _rows(lab, "SELECT hash FROM chunks")}
    on_disk = set(lab.svc.store.iter_hashes())
    staging = sorted(p.name for p in lab.svc.store.iter_staged())
    victim = [s for n, s in files if n.startswith("victim")]
    return {
        "files": [{"filename": n, "status": s} for n, s in files],
        "operations": [{"type": t, "status": s} for t, s in ops],
        "complete_files": sum(1 for _, s in files if s == "complete"),
        "uploading_files": sum(1 for _, s in files if s == "uploading"),
        "failed_files": sum(1 for _, s in files if s == "failed"),
        "running_operations": sum(1 for _, s in ops if s == "running"),
        "chunk_rows": len(row_hashes),
        "manifest_rows": _rows(lab, "SELECT COUNT(*) FROM file_chunks")[0][0],
        "ref_count_mismatches": _rows(lab, "SELECT COUNT(*) FROM v_ref_count_mismatch")[0][0],
        "chunk_files": len(on_disk),
        "orphan_chunk_files": len(on_disk - row_hashes),
        "missing_chunk_files": len(row_hashes - on_disk),
        "staging_files": len(staging),
        "staging_names": staging[:20],
        "victim_status": victim[0] if victim else None,
        "victim_visible": bool(victim),
        "victim_manifest_rows": _rows(
            lab, "SELECT COUNT(*) FROM file_chunks fc JOIN files f USING(file_id) WHERE f.filename LIKE 'victim%'")[0][0],
    }


def _audit_summary(report) -> dict:
    kinds: dict[str, int] = {}
    for f in report.findings:
        kinds[f.kind] = kinds.get(f.kind, 0) + 1
    return {"healthy": report.healthy, "files_checked": report.files_checked,
            "chunks_checked": report.chunks_checked, "findings": len(report.findings), "by_kind": kinds}


def _restore_all(lab: Lab) -> list[dict]:
    """Restore every complete file and compare with the bytes originally uploaded."""
    out = []
    for fid, name in _rows(lab, "SELECT file_id, filename FROM files WHERE status='complete' ORDER BY created_at"):
        dest = lab.root / f"restore-{fid}.out"
        try:
            lab.svc.restorer.restore_to_file(fid, dest)
            known = lab.originals.get(fid)
            out.append({"filename": name, "outcome": "restored",
                        "matches_original": (dest.read_bytes() == known) if known is not None else None})
        except ChunkVaultError as exc:
            out.append({"filename": name, "outcome": "refused", "code": exc.code, "partial_output_left": dest.exists()})
        finally:
            dest.unlink(missing_ok=True)
    return out


def _invariants(lab: Lab, audit_after: dict, restores: list[dict], second_actions: int, stable: bool, final: dict) -> list[dict]:
    store = lab.svc.store
    bad_valid = bad_hash = 0
    for (fid,) in _rows(lab, "SELECT file_id FROM files WHERE status='complete'"):
        for h, size in _rows(lab, "SELECT chunk_hash, chunk_size FROM file_chunks WHERE file_id=?", fid):
            if store.verify(h, size) is not ChunkStatus.OK:
                bad_valid += 1
    for h, size, state in _rows(lab, "SELECT hash, size_bytes, state FROM chunks"):
        if state == "stored" and store.verify(h, size) is ChunkStatus.CORRUPT:
            bad_hash += 1
    needed = {h for (h,) in _rows(
        lab, "SELECT DISTINCT chunk_hash FROM file_chunks fc JOIN files f USING(file_id) WHERE f.status='complete'")}
    lost = sum(1 for h in needed if not store.exists(h))
    kinds = audit_after["by_kind"]
    silent = [r for r in restores if r["outcome"] == "restored" and r.get("matches_original") is False]
    manifest_ok = kinds.get("manifest_inconsistent", 0) == 0
    return [
        {"id": "I1", "title": "Every committed file references valid chunks", "holds": bad_valid == 0,
         "detail": "all chunks of complete files verify" if bad_valid == 0
         else f"{bad_valid} manifest chunk(s) missing or damaged (detected; not repairable)"},
        {"id": "I2", "title": "Chunk hashes match stored content", "holds": bad_hash == 0,
         "detail": "every stored chunk re-hashes to its name" if bad_hash == 0
         else f"{bad_hash} stored chunk(s) do not match their SHA-256 (detected; not repairable)"},
        {"id": "I3", "title": "Reference counts match actual references", "holds": final["ref_count_mismatches"] == 0,
         "detail": f"{final['ref_count_mismatches']} mismatching chunk(s)"},
        {"id": "I4", "title": "Failed uploads leave no incomplete file that looks complete",
         "holds": final["uploading_files"] == 0 and manifest_ok,
         "detail": f"after recovery: {final['uploading_files']} file(s) stuck 'uploading'; {final['failed_files']} 'failed' "
                   "row(s) stay listed with status failed (visible, but not restorable)"},
        {"id": "I5", "title": "Restoration never silently returns wrong content", "holds": not silent,
         "detail": "; ".join(f"{r['filename']}: {r['outcome']}" + (f" ({r['code']})" if r.get("code") else "")
                             for r in restores) or "no complete files"},
        {"id": "I6", "title": "Recovery does not delete chunks required by committed files", "holds": lost == 0,
         "detail": f"{lost} required chunk file(s) missing after recovery"},
        {"id": "I7", "title": "Repeated recovery is safe", "holds": second_actions == 0 and stable,
         "detail": f"the second recovery performed {second_actions} action(s); store state unchanged: {stable}"},
        {"id": "I8", "title": "A failed operation leaves no inconsistent metadata",
         "holds": final["ref_count_mismatches"] == 0 and manifest_ok,
         "detail": "reference counts and manifests are consistent"},
    ]


# ---------------------------------------------------------------- scenario bodies

def _shifted_victim() -> bytes:
    """Shares most chunks with the control file (insertion near the start), so rollback and
    recovery must leave the control file's shared chunks and counters intact."""
    base = seeded(1, CONTROL_SIZE)
    return base[:300] + seeded(9, 2048) + base[300:]


def _crash_at(lab: Lab, point: str, exc: type[BaseException] = SimulatedCrash) -> None:
    def hook(name: str) -> None:
        if name == point:
            raise exc(f"injected at '{point}'")

    lab.svc.uploader.checkpoint = hook


def _try(fn) -> dict:
    try:
        fn()
        return {"fault_raised": False, "exception": None}
    except SimulatedCrash as e:
        return {"fault_raised": True, "exception": "SimulatedCrash (process death)", "message": str(e)}
    except Exception as e:  # noqa: BLE001 - the engine's own error reporting is what we observe
        return {"fault_raised": True, "exception": type(e).__name__, "code": getattr(e, "code", None)}


def _s_upload_stream(lab: Lab) -> dict:
    class Dying(io.RawIOBase):
        sent = False

        def readable(self):
            return True

        def read(self, n=-1):
            if not Dying.sent:
                Dying.sent = True
                return b"x" * 40_000
            raise SimulatedCrash("the process died while receiving the upload")

    return _try(lambda: lab.svc.uploader.upload("victim.bin", Dying()))


def _s_cdc(lab: Lab) -> dict:
    class FailingChunker:
        def __init__(self, real):
            self.real = real

        def chunk_stream(self, stream):
            for i, c in enumerate(self.real.chunk_stream(stream)):
                if i == 2:
                    raise ChunkingError("injected: the input stream failed during chunk generation")
                yield c

    lab.svc.uploader._chunker = FailingChunker(lab.svc.chunker)
    return _try(lambda: lab.upload("victim.bin", _shifted_victim()))


def _s_before_commit(lab: Lab) -> dict:
    _crash_at(lab, "chunks_ready")
    return _try(lambda: lab.upload("victim.bin", _shifted_victim()))


def _s_metadata(lab: Lab) -> dict:
    _crash_at(lab, "in_t2", RuntimeError)
    return _try(lambda: lab.upload("victim.bin", _shifted_victim()))


def _s_after_commit(lab: Lab) -> dict:
    _crash_at(lab, "after_commit")
    info = _try(lambda: lab.upload("victim.bin", _shifted_victim()))
    for (fid,) in _rows(lab, "SELECT file_id FROM files WHERE filename='victim.bin'"):
        lab.originals[fid] = _shifted_victim()  # the upload committed, so restore must match these bytes
    return info


def _s_restore(lab: Lab) -> dict:
    r = lab.upload("victim.bin", _shifted_victim())
    solo = _rows(lab, "SELECT hash FROM chunks WHERE ref_count=1 AND hash IN "
                      "(SELECT chunk_hash FROM file_chunks WHERE file_id=?) ORDER BY hash", r.file_id)
    path = lab.svc.store.path_for(solo[0][0])  # a chunk only the victim uses
    raw = bytearray(path.read_bytes())
    raw[len(raw) // 2] ^= 0x01
    path.write_bytes(bytes(raw))
    dest = lab.root / "victim-restore.out"
    info = _try(lambda: lab.svc.restorer.restore_to_file(r.file_id, dest))
    info["partial_output_left"] = dest.exists()
    dest.unlink(missing_ok=True)
    return info


def _s_delete_rollback(lab: Lab) -> dict:
    r = lab.upload("victim.bin", _shifted_victim())

    def boom(*a, **k):
        raise RuntimeError("injected: failure while reclaiming chunk rows inside the delete transaction")

    lab.svc.repo.delete_unreferenced_chunk_rows = boom
    return _try(lambda: lab.svc.deleter.delete(r.file_id))


def _s_delete_crash(lab: Lab) -> dict:
    r = lab.upload("victim.bin", _shifted_victim())

    def crash(_h):
        raise SimulatedCrash("the process died after the delete committed, while unlinking chunk files")

    lab.svc.store.delete = crash
    return _try(lambda: lab.svc.deleter.delete(r.file_id))


def _s_recovery_interrupted(lab: Lab) -> dict:
    _crash_at(lab, "chunks_ready")
    first = _try(lambda: lab.upload("victim.bin", seeded(3, VICTIM_SIZE)))  # many orphan chunks
    lab.svc.uploader.__dict__.pop("checkpoint", None)
    created = snapshot(lab)["orphan_chunk_files"]
    real_delete = lab.svc.store.delete
    count = {"n": 0}

    def flaky(h):
        count["n"] += 1
        if count["n"] == 2:
            raise SimulatedCrash("the process died during recovery, after deleting one orphan chunk")
        return real_delete(h)

    lab.svc.store.delete = flaky
    info = _try(lambda: lab.svc.reconciler.reconcile())
    remaining = snapshot(lab)["orphan_chunk_files"]
    info.update({"initial_fault": first.get("exception"), "orphans_created": created, "orphans_remaining": remaining,
                 "progress_made": 0 < remaining < created})
    return info


_STATUS = Expect("verification.status", "Recovery verification (computed by the backend)", "eq", "verified_clean")
_CONTROL = Expect("verification.control_restorable", "Control file still restorable", "eq", True)

SCENARIOS: dict[str, Scenario] = {s.id: s for s in (
    Scenario(
        "upload_stream_crash", "Failure during file upload", "Upload", "While the request body is being received", "crash",
        "The process dies after part of the upload reached staging, before any metadata exists.",
        ("Upload started", "Bytes partly received", "Fault injected", "Recovery", "Integrity check"), 2,
        (Expect("before.victim_visible", "Victim file visible in metadata", "eq", False),
         Expect("before.staging_files", "Leftover staging files", "gt0"),
         Expect("before.orphan_chunk_files", "Orphan chunk files", "eq", 0),
         Expect("after.staging_files", "Staging files after recovery", "eq", 0), _CONTROL, _STATUS),
        _s_upload_stream),
    Scenario(
        "cdc_failure", "Failure during CDC chunk generation", "Upload", "The chunker raises after producing its third chunk", "exception",
        "The input stream fails mid-chunking. This is an ordinary error, so the engine's own failure handling runs.",
        ("Upload started", "Some chunks generated", "Fault injected", "Engine cleanup", "Integrity check"), 2,
        (Expect("before.victim_status", "Victim file status", "eq", "failed"),
         Expect("before.staging_files", "Leftover staging files", "eq", 0),
         Expect("before.orphan_chunk_files", "Orphan chunk files", "eq", 0),
         Expect("before.ref_count_mismatches", "Reference-count mismatches", "eq", 0),
         Expect("after.recovery_actions", "Recovery actions needed", "eq", 0), _CONTROL, _STATUS),
        _s_cdc),
    Scenario(
        "crash_before_metadata_commit", "Failure after chunk creation but before metadata commit", "Upload",
        "All chunks are published; the process dies before the metadata transaction begins", "crash",
        "Chunk bytes are durable in the store but nothing references them and the file is still 'uploading'.",
        ("Upload started", "Chunks generated", "Chunks persisted", "Fault injected", "Recovery", "Integrity check"), 3,
        (Expect("before.victim_status", "Victim file status", "eq", "uploading"),
         Expect("before.victim_manifest_rows", "Victim manifest rows visible", "eq", 0),
         Expect("before.orphan_chunk_files", "Orphan chunk files", "gt0"),
         Expect("before.running_operations", "Operations stuck running", "eq", 1),
         Expect("after.orphan_chunk_files", "Orphan chunk files after recovery", "eq", 0), _CONTROL, _STATUS),
        _s_before_commit),
    Scenario(
        "metadata_failure", "Failure during metadata persistence", "Upload", "Exception inside the final metadata transaction (T2)", "exception",
        "An error occurs after some metadata was written in T2. SQLite must roll the whole transaction back, including trigger-driven reference counts.",
        ("Upload started", "Chunks persisted", "Metadata transaction begun", "Fault injected", "Rollback", "Integrity check"), 3,
        (Expect("before.victim_status", "Victim file status", "eq", "failed"),
         Expect("before.victim_manifest_rows", "Victim manifest rows visible", "eq", 0),
         Expect("before.ref_count_mismatches", "Reference-count mismatches", "eq", 0),
         Expect("before.orphan_chunk_files", "Orphan chunk files", "eq", 0),
         Expect("verification.control_restorable", "Control file (shares chunks) still restorable", "eq", True), _STATUS),
        _s_metadata),
    Scenario(
        "crash_after_commit", "Failure after metadata commit but before response delivery", "Upload",
        "The process dies right after COMMIT, before cleanup and the HTTP response", "crash",
        "The file is committed and complete, but the client never received the response; a client retry creates a second record.",
        ("Upload started", "Chunks persisted", "Metadata committed", "Fault injected (response lost)", "Recovery", "Integrity check"), 3,
        (Expect("before.victim_status", "Victim file status", "eq", "complete"),
         Expect("before.staging_files", "Leftover staging files", "gt0"),
         Expect("before.orphan_chunk_files", "Orphan chunk files", "eq", 0),
         Expect("verification.victim_restorable", "Victim file restorable", "eq", True),
         Expect("retry.visible_victim_files", "Files named victim.bin after the client retries", "eq", 2),
         Expect("retry.new_chunks", "New chunks written by the retry", "eq", 0), _STATUS),
        _s_after_commit),
    Scenario(
        "restore_failure", "Failure during file restoration", "Restore", "A stored chunk is damaged on disk (one bit flipped) before restore", "exception",
        "Restoration must refuse to return corrupted data. Recovery cannot repair a damaged single-copy chunk; it can only report it.",
        ("Upload committed", "Chunk damaged", "Restore attempted", "Recovery", "Integrity check"), 1,
        (Expect("fault.code", "Restore error code", "eq", "chunk_corrupt"),
         Expect("fault.partial_output_left", "Partial output left behind", "eq", False),
         Expect("before.audit.by_kind.corrupt_chunk", "Corrupt chunks detected by the audit", "eq", 1),
         Expect("after.recovery_actions", "Recovery actions (nothing can be repaired)", "eq", 0), _CONTROL,
         Expect("verification.status", "Recovery verification (computed by the backend)", "eq", "verified_with_unrepairable_damage")),
        _s_restore),
    Scenario(
        "delete_failure_rollback", "Failure during file deletion (before commit)", "Delete", "Exception inside the delete transaction", "exception",
        "The delete transaction fails after the file row was removed inside it; everything must roll back.",
        ("Upload committed", "Delete transaction begun", "Fault injected", "Rollback", "Integrity check"), 2,
        (Expect("before.victim_status", "Victim file status", "eq", "complete"),
         Expect("before.ref_count_mismatches", "Reference-count mismatches", "eq", 0),
         Expect("verification.victim_restorable", "Victim file still restorable", "eq", True), _STATUS),
        _s_delete_rollback),
    Scenario(
        "delete_crash_after_commit", "Failure during file deletion (cleanup interrupted)", "Delete",
        "The delete committed; the process dies while unlinking chunk files", "crash",
        "Metadata is already consistent (the file and its unshared chunk rows are gone); only unreferenced chunk files remain.",
        ("Upload committed", "Delete committed", "Fault injected (unlink)", "Recovery", "Integrity check"), 2,
        (Expect("before.victim_visible", "Victim file visible", "eq", False),
         Expect("before.ref_count_mismatches", "Reference-count mismatches", "eq", 0),
         Expect("before.orphan_chunk_files", "Orphan chunk files", "gt0"),
         Expect("after.orphan_chunk_files", "Orphan chunk files after recovery", "eq", 0),
         Expect("verification.control_restorable", "Control file (shared chunks) still restorable", "eq", True), _STATUS),
        _s_delete_crash),
    Scenario(
        "recovery_interrupted", "Interruption during recovery", "Recovery", "The process dies during reconciliation, after deleting one orphan chunk", "crash",
        "A crash leaves many orphan chunks; recovery itself is then interrupted. Re-running it must converge safely.",
        ("Upload crashed", "Orphans created", "Recovery interrupted", "Recovery re-run", "Integrity check"), 2,
        (Expect("fault.orphans_created", "Orphan chunks before recovery", "gt0"),
         Expect("fault.progress_made", "The interrupted recovery made partial progress", "eq", True),
         Expect("fault.orphans_remaining", "Orphans remaining after the interruption", "gt0"),
         Expect("after.orphan_chunk_files", "Orphan chunk files after the re-run", "eq", 0), _CONTROL, _STATUS),
        _s_recovery_interrupted),
)}


def _get(d: dict, dotted: str):
    cur: Any = d
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _matches(e: Expect, observed) -> bool:
    if e.op == "gt0":
        return isinstance(observed, (int, float)) and not isinstance(observed, bool) and observed > 0
    return observed == e.value


# ---------------------------------------------------------------- public API

def list_scenarios() -> list[dict]:
    return [{"id": s.id, "title": s.title, "operation": s.operation, "fault_point": s.fault_point, "kind": s.kind,
             "description": s.description, "steps": list(s.steps), "fault_step": s.fault_step,
             "expectations": [{"key": e.key, "label": e.label, "expected": e.describe()} for e in s.expects]}
            for s in SCENARIOS.values()]


def run_scenario(scenario_id: str, settings: Settings, *, enabled: bool | None = None) -> dict:
    if not (settings.fault_lab_enabled if enabled is None else enabled):
        raise FaultLabDisabledError(
            "The fault lab is disabled. Start the backend with CHUNKVAULT_ENABLE_FAULT_LAB=1 to run isolated experiments.")
    scenario = SCENARIOS.get(scenario_id)
    if scenario is None:
        raise UnknownScenarioError(f"unknown scenario '{scenario_id}'")
    if not _RUN_LOCK.acquire(blocking=False):
        raise ConflictError("another fault experiment is already running")
    started = time.perf_counter()
    lab: Lab | None = None
    result: dict | None = None
    try:
        lab = Lab(settings.cdc)
        app_data = Path(settings.data_dir).resolve()
        if lab.root.resolve() == app_data or app_data in lab.root.resolve().parents or lab.settings.db_path == settings.db_path:
            raise ConflictError("refusing to run: the experiment store would overlap the application's data")

        control = lab.upload("control.bin", seeded(1, CONTROL_SIZE))  # committed baseline that must survive
        fault = scenario.run(lab)
        lab.reset_faults()

        before = snapshot(lab)
        before["audit"] = _audit_summary(lab.svc.auditor.verify_store())

        recovery = lab.svc.reconciler.reconcile()  # explicit, reported repair
        actions = [{"action": a.action, "subject": a.subject[:16], "detail": a.detail} for a in recovery.actions]
        after = snapshot(lab)
        second = lab.svc.reconciler.reconcile()
        stable = snapshot(lab) == after
        audit_after = _audit_summary(lab.svc.auditor.verify_store())
        after["recovery_actions"] = len(actions)
        after["audit"] = audit_after

        restores = _restore_all(lab)
        invariants = _invariants(lab, audit_after, restores, len(second.actions), stable, after)
        by_kind = audit_after["by_kind"]
        unrepairable_only = (not audit_after["healthy"]) and set(by_kind) <= {"corrupt_chunk", "missing_chunk_file"}
        core_ok = all(i["holds"] for i in invariants if i["id"] in ("I3", "I5", "I6", "I7", "I8"))
        if audit_after["healthy"] and all(i["holds"] for i in invariants):
            status = "verified_clean"
        elif unrepairable_only and core_ok:
            status = "verified_with_unrepairable_damage"
        else:
            status = "not_recovered"
        ctl = [r for r in restores if r["filename"] == "control.bin"]
        vic = [r for r in restores if r["filename"].startswith("victim")]
        facts: dict[str, Any] = {
            "fault": fault, "before": before, "after": after,
            "verification": {
                "status": status,
                "control_restorable": bool(ctl) and ctl[0]["outcome"] == "restored" and ctl[0]["matches_original"] is True,
                "victim_restorable": bool(vic) and vic[0]["outcome"] == "restored" and vic[0]["matches_original"] is True,
            },
        }
        retry = None
        if scenario.id == "crash_after_commit":  # what a client does when the response never arrives
            r2 = lab.upload("victim.bin", _shifted_victim())
            retry = {"visible_victim_files": len(_rows(lab, "SELECT 1 FROM files WHERE filename='victim.bin'")),
                     "new_chunks": r2.new_chunks, "bytes_written": r2.bytes_written}
            facts["retry"] = retry

        expectations = []
        for e in scenario.expects:
            observed = _get(facts, e.key)
            expectations.append({"key": e.key, "label": e.label, "expected": e.describe(),
                                 "observed": observed, "matches": _matches(e, observed)})
        steps = [{"label": label, "phase": "before_fault" if i < scenario.fault_step else "fault" if i == scenario.fault_step else "after_fault"}
                 for i, label in enumerate(scenario.steps)]
        result = {
            "scenario_id": scenario.id, "title": scenario.title, "operation": scenario.operation,
            "fault_point": scenario.fault_point, "fault_kind": scenario.kind, "description": scenario.description,
            "steps": steps, "fault": fault,
            "baseline": {"control_chunks": control.chunk_count},
            "before_recovery": before, "recovery_actions": actions, "second_recovery_actions": len(second.actions),
            "after_recovery": after, "restores": restores, "invariants": invariants,
            "recovery_status": status, "expectations": expectations,
            "all_expectations_met": all(x["matches"] for x in expectations), "retry": retry,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        result["within_time_bound"] = result["duration_ms"] / 1000 < MAX_RUN_SECONDS
        return result
    finally:
        removed = lab.cleanup() if lab else True
        _RUN_LOCK.release()
        if result is not None:
            result["isolation"] = {"temporary_store_used": True, "application_data_touched": False,
                                   "temporary_store_removed": removed}
