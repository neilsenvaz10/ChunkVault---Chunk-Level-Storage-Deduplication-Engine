"""Concurrency and safety review of the cloud backend (C4 follow-up).

Two invariants are proven here, each with the writer lock INTACT (the normal case) and with the lock deliberately disabled for the
operations involved (what a lost lock looks like to a holder that has not noticed yet), so the safety rests on the database's own
fencing and not on the lock alone:

1. the application quota cannot be exceeded by concurrent uploads, and shared bytes are counted once;
2. garbage collection can never delete an object that a committed file references or is about to reference, and reconciliation
   never discards live work.

Real PostgreSQL (throw-away) + the fake Storage server; nothing leaves the machine.
"""

import contextlib
import io
import threading
import time

import psycopg
import pytest

from app.core.errors import ChunkVaultError
from app.database.postgres_repository import PostgresMetadataRepository
from app.database.repository import ChunkReclaiming, QuotaConflict
from app.database.states import ChunkState
from app.deduplication.interfaces import QuotaExceededError
from tests.conftest import make_data
from tests.pg_fixtures import H, pg_url, pgdb, requires_postgres, schema_conn, schema_name, stamp_version  # noqa: F401
from tests.test_cloud_workflows import A, D, KIB, launch  # noqa: F401  (launch is a fixture)

pytestmark = [pytest.mark.postgres, requires_postgres]
repo = PostgresMetadataRepository()


def lose_the_lock(monkeypatch, c):
    """Make every operation proceed as if it still held the writer lock while others run too (a lock lost without being noticed)."""
    @contextlib.contextmanager
    def no_lock(*a, **k):
        yield

    monkeypatch.setattr(c.svc.lock, "acquire", no_lock)
    monkeypatch.setattr(c.svc.lock, "ensure_held", lambda: None)


def run_threads(*fns, timeout=60):
    out = [None] * len(fns)

    def wrap(i, fn):
        try:
            out[i] = ("ok", fn())
        except Exception as exc:  # noqa: BLE001
            out[i] = ("err", exc)

    ts = [threading.Thread(target=wrap, args=(i, f)) for i, f in enumerate(fns)]
    [t.start() for t in ts]
    [t.join(timeout) for t in ts]
    assert not any(t.is_alive() for t in ts), "a worker is stuck"
    return out


def up(c, seed, size, name=None):
    return lambda: c.svc.uploader.upload(name or f"f{seed}.bin", io.BytesIO(make_data(seed, size)))


def no_dangling_references(c):
    """Every chunk referenced by a committed manifest has a stored row AND an object: the invariant GC must never break."""
    rows = c.q("SELECT DISTINCT fc.chunk_hash, ch.state FROM file_chunks fc JOIN chunks ch ON ch.hash = fc.chunk_hash")
    for h, state in rows:
        assert state == "stored", (h, state)
        assert c.fake.get(h) is not None, f"referenced chunk {h[:12]} has no object"
    assert c.q("SELECT count(*) FROM v_ref_count_mismatch") == [(0,)]


# =================================================================================================== 1. quota

@pytest.mark.parametrize("lock_intact", [True, False], ids=["lock-intact", "lock-lost"])
def test_two_uploads_that_each_fit_the_remaining_quota_but_not_together(launch, monkeypatch, lock_intact):
    cap = 300 * KIB
    c = launch({"stored_bytes_cap": cap, "batch_chunks": 4}, {"max_upload_bytes": 200 * KIB, "writer_lock_timeout_seconds": 30.0})
    assert c.upload(make_data(70, 100 * KIB)).status_code == 201  # 200 KiB of quota remain
    if not lock_intact:
        lose_the_lock(monkeypatch, c)
        c.fake.latency = 0.02  # the two uploads genuinely overlap batch by batch
    res = run_threads(up(c, 71, 150 * KIB), up(c, 72, 150 * KIB))  # each fits alone (150 <= 200); together they do not (300 > 200)
    oks = [r for r in res if r[0] == "ok"]
    errs = [r[1] for r in res if r[0] == "err"]
    assert len(oks) <= 1 and all(isinstance(e, (QuotaExceededError, ChunkVaultError)) for e in errs)
    if lock_intact:
        assert len(oks) == 1 and len(errs) == 1 and isinstance(errs[0], QuotaExceededError)  # serialised: exactly one fits
    total = c.one("SELECT coalesce(sum(size_bytes), 0) FROM chunks")
    assert total <= cap  # THE invariant, whatever the interleaving
    assert c.one("SELECT count(*) FROM files WHERE status = 'complete'") == 1 + len(oks)
    no_dangling_references(c)


def test_the_quota_is_enforced_inside_the_registering_transaction_not_before_it(pgdb, schema_conn):
    """The unit behind the guarantee: two connections reserve at the same moment; only one can fit."""
    barrier = threading.Barrier(2)
    items = lambda c: [(H(c), 600)]  # noqa: E731
    outcomes = []

    def reserve(letter):
        with pgdb.connection() as conn:
            barrier.wait(5)
            try:
                with pgdb.transaction(conn):
                    repo.reserve_chunks(conn, items(letter), 1000)  # 600 + 600 > 1000
                    time.sleep(0.2)  # hold the transaction open so the other one must wait for it
                outcomes.append("reserved")
            except QuotaConflict as q:
                outcomes.append(("quota", q.used_bytes, q.needed_bytes))

    ts = [threading.Thread(target=reserve, args=(ch,)) for ch in "ab"]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert sorted(map(str, outcomes)) == sorted(map(str, ["reserved", ("quota", 600, 600)]))  # the loser saw the winner's COMMITTED 600
    assert schema_conn.execute("SELECT sum(size_bytes) FROM chunks").fetchone()[0] == 600


def test_reservation_counts_existing_rows_once_and_refuses_rows_being_reclaimed(pgdb):
    with pgdb.connection() as conn:
        with pgdb.transaction(conn):
            assert repo.reserve_chunks(conn, [(H("a"), 600), (H("b"), 300)], 1000) == [H("a"), H("b")]
        with pgdb.transaction(conn):  # the same chunk again costs nothing, so a 100-byte new one still fits in 1000
            assert repo.reserve_chunks(conn, [(H("a"), 600), (H("c"), 100)], 1000) == [H("c")]
        with pytest.raises(QuotaConflict):
            with pgdb.transaction(conn):
                repo.reserve_chunks(conn, [(H("d"), 1)], 1000)
        with pgdb.transaction(conn):
            repo.mark_chunks_state(conn, [H("c")], ChunkState.DELETING)
        with pytest.raises(ChunkReclaiming):
            with pgdb.transaction(conn):
                repo.reserve_chunks(conn, [(H("c"), 100)], 10_000)


@pytest.mark.parametrize("lock_intact", [True, False], ids=["lock-intact", "lock-lost"])
def test_concurrent_uploads_of_the_same_content_count_shared_bytes_once(launch, monkeypatch, lock_intact):
    size = 150 * KIB
    c = launch({"stored_bytes_cap": size + 10 * KIB, "batch_chunks": 4}, {"max_upload_bytes": size, "writer_lock_timeout_seconds": 30.0})
    if not lock_intact:
        lose_the_lock(monkeypatch, c)
        c.fake.latency = 0.02
    res = run_threads(up(c, 80, size, "one.bin"), up(c, 80, size, "two.bin"), up(c, 80, size, "three.bin"))
    assert [r[0] for r in res] == ["ok"] * 3, [r for r in res if r[0] == "err"]
    assert c.one("SELECT coalesce(sum(size_bytes), 0) FROM chunks") == size  # three files, ONE copy of the bytes
    assert c.q("SELECT count(*) FROM chunks") == [(len(c.fake.objects),)]
    assert c.one("SELECT count(*) FROM files WHERE status = 'complete'") == 3
    assert c.one("SELECT min(ref_count) FROM chunks") >= 3 and c.client.get("/api/storage/quota").json()["used_bytes"] == size
    no_dangling_references(c)
    for r in res:
        assert c.client.get(f"/api/files/{r[1].file_id}/download", headers=D).content == make_data(80, size)


def test_where_the_lock_sits_relative_to_quota_registration_and_commit(launch, monkeypatch):
    """Documents (and pins) the order of events of one upload."""
    c = launch()
    events = []
    real_acquire = c.svc.lock.acquire

    @contextlib.contextmanager
    def traced(*a, **k):
        with real_acquire(*a, **k):
            events.append("lock acquired")
            yield
        events.append("lock released")

    monkeypatch.setattr(c.svc.lock, "acquire", traced)
    real_reserve, real_complete = c.svc.repo.reserve_chunks, c.svc.repo.complete_file
    monkeypatch.setattr(c.svc.repo, "reserve_chunks", lambda *a, **k: (events.append("quota check + register"), real_reserve(*a, **k))[1])
    monkeypatch.setattr(c.svc.repo, "complete_file", lambda *a, **k: (events.append("metadata commit (T2)"), real_complete(*a, **k))[1])
    assert c.upload(make_data(81, 60 * KIB)).status_code == 201
    order = [e for i, e in enumerate(events) if e not in events[:i]]
    assert order[0] == "lock acquired" and order[-1] == "lock released"
    assert order.index("quota check + register") < order.index("metadata commit (T2)") < order.index("lock released")


# =================================================================================================== 2. garbage collection

def stale_unreferenced_copy(c, seed, size):
    """File A uploaded then deleted: its chunks are now unreferenced, old enough (grace 0) and still in the object store."""
    data = make_data(seed, size)
    a = c.upload(data, "a.bin").json()
    assert c.client.delete(f"/api/files/{a['file_id']}", headers=D).status_code == 200
    assert c.one("SELECT count(*) FROM chunks WHERE ref_count = 0") == c.one("SELECT count(*) FROM chunks") > 0
    return data


def test_gc_and_an_upload_of_the_same_content_never_overlap_with_the_lock_intact(launch):
    c = launch({"orphan_min_age_seconds": 0.0}, {"writer_lock_timeout_seconds": 30.0})
    data = stale_unreferenced_copy(c, 90, 120 * KIB)
    first_delete = threading.Event()
    c.fake.on_request = lambda op: first_delete.set() if op == "delete" else None
    c.fake.latency = 0.05  # every storage call is slow: GC takes a while
    gc_result = []
    gc = threading.Thread(target=lambda: gc_result.append(c.svc.gc.run(dry_run=False, limit=1000)))
    gc.start()
    assert first_delete.wait(20)  # GC is in the middle of deleting objects
    uploaded = c.svc.uploader.upload("again.bin", io.BytesIO(data))  # must wait for the lock, then see a clean slate
    gc.join(60)
    assert gc_result and gc_result[0].rows_removed == gc_result[0].objects_removed + gc_result[0].objects_already_absent > 0
    assert uploaded.new_chunks == uploaded.chunk_count - uploaded.duplicate_chunks > 0  # re-published, not adopted from a dying object
    assert c.client.get(f"/api/files/{uploaded.file_id}/download", headers=D).content == data
    no_dangling_references(c)


def test_an_upload_that_meets_a_claimed_chunk_refuses_it_even_without_the_lock(launch, monkeypatch):
    """The window between 'metadata claimed' and 'object deleted': the row stays, in state 'deleting', until the object is gone."""
    c = launch({"orphan_min_age_seconds": 0.0})
    data = stale_unreferenced_copy(c, 91, 100 * KIB)
    lose_the_lock(monkeypatch, c)
    real_delete = c.svc.store.delete
    seen = []

    def delete_with_a_competing_upload(h):
        if not seen:  # GC has claimed every row and is about to delete the first object
            seen.append(h)
            assert c.one("SELECT count(*) FROM chunks WHERE state = 'deleting'") == c.one("SELECT count(*) FROM chunks") > 0
            with pytest.raises(ChunkReclaiming):
                c.svc.uploader.upload("racer.bin", io.BytesIO(data))
        return real_delete(h)

    monkeypatch.setattr(c.svc.store, "delete", delete_with_a_competing_upload)
    r = c.svc.gc.run(dry_run=False, limit=1000)
    assert seen and r.rows_removed > 0 and r.failed == []
    assert c.q("SELECT status FROM files WHERE filename = 'racer.bin'") == [("failed",)]  # refused and recorded, nothing committed
    ok = c.svc.uploader.upload("after.bin", io.BytesIO(data))  # once the reclaim finished, a fresh upload simply works
    assert c.client.get(f"/api/files/{ok.file_id}/download", headers=D).content == data
    no_dangling_references(c)


def test_gc_between_an_uploads_chunk_phase_and_its_commit_cannot_leave_a_dangling_reference(launch, monkeypatch):
    """Upload verified the existing objects, GC then claims, deletes objects AND rows, the upload then tries to commit."""
    c = launch({"orphan_min_age_seconds": 0.0})
    data = stale_unreferenced_copy(c, 92, 100 * KIB)
    lose_the_lock(monkeypatch, c)
    ran = []
    c.svc.uploader.checkpoint = lambda name: ran.append(c.svc.gc.run(dry_run=False, limit=1000)) if name == "chunks_ready" else None
    with pytest.raises(ChunkReclaiming):
        c.svc.uploader.upload("victim.bin", io.BytesIO(data))
    assert ran and ran[0].rows_removed > 0 and ran[0].objects_removed > 0  # GC did its work in the middle of the upload
    assert c.one("SELECT count(*) FROM file_chunks") == 0 and c.one("SELECT count(*) FROM files WHERE status='complete'") == 0
    no_dangling_references(c)
    c.svc.uploader.__dict__.pop("checkpoint")
    ok = c.svc.uploader.upload("retry.bin", io.BytesIO(data))
    assert c.client.get(f"/api/files/{ok.file_id}/download", headers=D).content == data


def test_the_uploads_own_fresh_pending_rows_are_also_protected_by_the_fence(launch, monkeypatch):
    """With a zero grace window GC may claim the pending rows of an upload that is still running; the upload must abort, not dangle."""
    c = launch({"orphan_min_age_seconds": 0.0})
    lose_the_lock(monkeypatch, c)
    c.svc.uploader.checkpoint = lambda name: c.svc.gc.run(dry_run=False, limit=1000) if name == "chunks_ready" else None
    with pytest.raises(ChunkReclaiming):
        c.svc.uploader.upload("fresh.bin", io.BytesIO(make_data(93, 100 * KIB)))
    assert c.one("SELECT count(*) FROM file_chunks") == 0 and c.fake.objects == {}  # GC reclaimed what nothing referenced
    no_dangling_references(c)


def test_a_claim_waits_for_an_uploads_commit_and_then_finds_the_chunks_referenced(launch, monkeypatch):
    """Upload holds the row locks and is mid-commit when GC tries to claim the same chunks."""
    c = launch({"orphan_min_age_seconds": 0.0})
    data = stale_unreferenced_copy(c, 94, 100 * KIB)
    lose_the_lock(monkeypatch, c)
    gc_out, gc_thread = [], []

    def during_t2(name):
        if name == "in_t2":  # the manifest transaction is open and holds FOR SHARE on every chunk row it references
            t = threading.Thread(target=lambda: gc_out.append(c.svc.gc.run(dry_run=False, limit=1000)))
            gc_thread.append(t)
            t.start()
            time.sleep(1.0)
            assert t.is_alive(), "the claim must be blocked by the upload's row locks"
            assert c.fake.count("delete") == 0

    c.svc.uploader.checkpoint = during_t2
    res = c.svc.uploader.upload("winner.bin", io.BytesIO(data))
    gc_thread[0].join(30)
    assert gc_out and gc_out[0].candidates == 0 and gc_out[0].rows_removed == 0 and c.fake.count("delete") == 0
    assert c.client.get(f"/api/files/{res.file_id}/download", headers=D).content == data
    no_dangling_references(c)


def test_a_crashed_gc_leaves_a_durable_state_that_blocks_uploads_until_the_next_run_finishes_it(launch, monkeypatch):
    c = launch({"orphan_min_age_seconds": 0.0})
    data = stale_unreferenced_copy(c, 95, 100 * KIB)
    total = c.one("SELECT count(*) FROM chunks")

    class Crash(BaseException):
        pass

    real_delete = c.svc.store.delete
    monkeypatch.setattr(c.svc.store, "delete", lambda h: (_ for _ in ()).throw(Crash()))  # the process dies right after the claim
    with pytest.raises(Crash):
        c.svc.gc.run(dry_run=False, limit=1000)
    assert c.one("SELECT count(*) FROM chunks WHERE state = 'deleting'") == total  # nothing lost, nothing half-done
    assert c.client.get("/api/storage/quota").json()["used_bytes"] > 0  # still counted by the quota
    r = c.client.post("/api/files", files={"file": ("a.bin", data)}, headers=D)
    assert r.status_code == 409 and r.json()["error"]["code"] == "chunk_reclaiming"  # told to finish the GC first
    monkeypatch.setattr(c.svc.store, "delete", real_delete)
    assert c.svc.gc.run(dry_run=True).candidates == total  # a dry run sees the interrupted claim
    done = c.svc.gc.run(dry_run=False, limit=1000)
    assert done.rows_removed == total and c.one("SELECT count(*) FROM chunks") == 0 and c.fake.objects == {}
    assert c.client.post("/api/files", files={"file": ("a.bin", data)}, headers=D).status_code == 201


def test_object_delete_failures_keep_the_claim_and_are_retried(launch):
    c = launch({"orphan_min_age_seconds": 0.0, "storage_max_attempts": 1})
    stale_unreferenced_copy(c, 96, 60 * KIB)
    total = c.one("SELECT count(*) FROM chunks")
    c.fake.always["delete"] = 500
    r = c.svc.gc.run(dry_run=False, limit=1000)
    assert len(r.failed) == total and r.rows_removed == 0
    assert c.one("SELECT count(*) FROM chunks WHERE state = 'deleting'") == total  # durable, counted, not re-registered as 'pending'
    c.fake.always.clear()
    assert c.svc.gc.run(dry_run=False, limit=1000).rows_removed == total and c.fake.objects == {}


def test_gc_leaves_pending_rows_of_live_uploads_alone_inside_the_grace_window(launch):
    c = launch()  # default grace: 900 s
    with c.svc.db.connection() as conn, c.svc.db.transaction(conn):  # what a running upload's first batch leaves: fresh 'pending' rows
        c.svc.repo.reserve_chunks(conn, [("a" * 64, 100), ("b" * 64, 200)], 10_000_000)
    pending = c.one("SELECT count(*) FROM chunks WHERE state = 'pending'")
    assert pending == 2
    r = c.svc.gc.run(dry_run=False, limit=1000)
    assert r.candidates == 0 and c.one("SELECT count(*) FROM chunks WHERE state = 'pending'") == pending
    c.owner.execute("UPDATE chunks SET created_at = now() - interval '2 hours'")  # once abandoned for long enough ...
    assert c.svc.gc.run(dry_run=False, limit=1000).rows_removed == pending  # ... they are reclaimed


def test_gc_rows_in_every_state_are_covered_by_the_guard(pgdb):
    """The database-level rule: a referenced chunk can be claimed by NO path, whatever its state or age."""
    with pgdb.connection() as conn:
        with pgdb.transaction(conn):
            repo.register_chunks(conn, [(H("a"), 10), (H("b"), 10)], ChunkState.STORED)
            repo.create_file(conn, "f1", "a", 10)
            repo.append_file_chunks(conn, "f1", [(0, H("a"), 10)])
        with pgdb.transaction(conn):
            claimed = [c.hash for c in repo.claim_chunks_for_deletion(conn, 0, 100)]
        assert claimed == [H("b")]  # the referenced chunk is untouched
        assert repo.get_chunk(conn, H("a")).state is ChunkState.STORED
        with pgdb.transaction(conn):
            assert repo.release_deleted_chunks(conn, [H("a"), H("b")]) == [H("b")]  # only the claimed, unreferenced one goes
        assert repo.lock_chunks_for_reference(conn, [H("a"), H("b")]) == [H("a")]  # a released chunk can no longer be referenced


def test_claim_and_lock_serialise_on_the_chunk_row(pgdb):
    """Both orders of the race, at the SQL level."""
    with pgdb.connection() as setup:
        with pgdb.transaction(setup):
            repo.register_chunks(setup, [(H("a"), 10)], ChunkState.STORED)
    # order 1: the manifest transaction locks first; the claim must wait and then skip the (now referenced) chunk
    results = []
    with pgdb.connection() as up_conn, pgdb.connection() as gc_conn:
        up_conn.execute("BEGIN")
        assert repo.lock_chunks_for_reference(up_conn, [H("a")]) == [H("a")]
        repo.create_file(up_conn, "f1", "a", 10)
        t = threading.Thread(target=lambda: results.append([c.hash for c in _claim(pgdb, gc_conn)]))
        t.start()
        time.sleep(0.5)
        assert t.is_alive()  # blocked by the FOR SHARE lock
        repo.append_file_chunks(up_conn, "f1", [(0, H("a"), 10)])
        up_conn.execute("COMMIT")
        t.join(20)
    assert results == [[]]  # the claim re-evaluated its guard after the wait: the chunk is referenced now
    # order 2: the claim commits first; the manifest transaction no longer gets the row and must abort
    with pgdb.connection() as c2:
        with pgdb.transaction(c2):
            repo.register_chunks(c2, [(H("b"), 10)], ChunkState.STORED)
        assert [c.hash for c in _claim(pgdb, c2)] == [H("b")]
        with pgdb.transaction(c2):
            assert repo.lock_chunks_for_reference(c2, [H("b")]) == []


def _claim(db, conn):
    with db.transaction(conn):
        return repo.claim_chunks_for_deletion(conn, 0, 100)


# =================================================================================================== reconcile versus live work

def test_reconcile_leaves_live_work_alone_and_repairs_abandoned_work(launch):
    c = launch()  # grace 900 s
    c.owner.execute("INSERT INTO files(file_id, filename, original_size) VALUES ('live', 'live.bin', 1)")
    c.owner.execute("INSERT INTO operations(file_id, operation_type) VALUES ('live', 'upload')")
    c.owner.execute("INSERT INTO operations(operation_type) VALUES ('restore')")
    c.owner.execute("INSERT INTO files(file_id, filename, original_size, created_at) VALUES ('dead', 'dead.bin', 1, now() - interval '2 hours')")
    c.owner.execute("INSERT INTO operations(file_id, operation_type, started_at) VALUES ('dead', 'upload', now() - interval '2 hours')")
    c.owner.execute("INSERT INTO operations(operation_type, started_at) VALUES ('restore', now() - interval '2 hours')")
    actions = c.client.post("/api/integrity/reconcile", headers=A).json()["actions"]
    assert sorted(a["action"] for a in actions) == ["abort_operation", "abort_operation", "fail_incomplete_file"]
    assert c.q("SELECT file_id, status FROM files ORDER BY file_id") == [("dead", "failed"), ("live", "uploading")]
    assert c.q("SELECT count(*) FROM operations WHERE status = 'running'") == [(2,)]  # the two recent ones survived
    assert c.q("SELECT count(*) FROM operations WHERE status = 'aborted'") == [(2,)]


@pytest.mark.parametrize("grace,completes", [(900.0, True), (0.0, False)], ids=["fresh-work-protected", "abandoned-work-fenced"])
def test_reconcile_during_a_live_upload_cannot_make_it_commit_an_inconsistent_state(launch, monkeypatch, grace, completes):
    c = launch({"orphan_min_age_seconds": grace})
    lose_the_lock(monkeypatch, c)
    reconciled = []
    c.svc.uploader.checkpoint = lambda name: reconciled.append(c.svc.reconciler.reconcile()) if name == "chunks_ready" else None
    data = make_data(98, 80 * KIB)
    if completes:  # the file is fresh: reconcile does not touch it and the upload commits normally
        res = c.svc.uploader.upload("live.bin", io.BytesIO(data))
        assert reconciled and not [a for a in reconciled[0].actions if a.action in ("fail_incomplete_file", "abort_operation")]
        assert c.client.get(f"/api/files/{res.file_id}/download", headers=D).content == data
    else:  # the grace window is zero, so reconcile marks the live upload failed; its commit must then refuse to complete the file
        with pytest.raises(ChunkVaultError):
            c.svc.uploader.upload("live.bin", io.BytesIO(data))
        assert any(a.action == "fail_incomplete_file" for a in reconciled[0].actions)
        assert c.one("SELECT count(*) FROM files WHERE status = 'complete'") == 0 and c.one("SELECT count(*) FROM file_chunks") == 0
        c.svc.uploader.__dict__.pop("checkpoint")
        again = c.svc.uploader.upload("live.bin", io.BytesIO(data))  # and a retry succeeds
        assert c.client.get(f"/api/files/{again.file_id}/download", headers=D).content == data
    no_dangling_references(c)


# =================================================================================================== randomised stress (lock lost)

def test_randomised_upload_delete_and_gc_never_leave_a_dangling_reference(launch, monkeypatch):
    """Uploads, deletes and GC runs overlap freely with the writer lock disabled. Any single operation may be refused or retried
    (that is allowed); what must never happen is a committed file whose chunk has no row or no object, or a counter mismatch."""
    c = launch({"orphan_min_age_seconds": 0.0, "batch_chunks": 4, "io_workers": 3, "stored_bytes_cap": 4 * 1024 * KIB},
               {"max_upload_bytes": 80 * KIB})
    lose_the_lock(monkeypatch, c)
    c.fake.latency = 0.005
    datas = [make_data(300 + i, 60 * KIB) for i in range(3)]
    done, guard = {}, threading.Lock()  # file_id -> content
    stats = {"uploaded": 0, "refused": 0, "deleted": 0, "gc_rows": 0}

    def uploader(k):
        for i in range(4):
            idx = (k + i) % 3
            try:
                r = c.svc.uploader.upload(f"s{idx}.bin", io.BytesIO(datas[idx]))
                with guard:
                    done[r.file_id] = idx
                    stats["uploaded"] += 1
            except ChunkVaultError:
                with guard:
                    stats["refused"] += 1  # refused or retryable: fine

    def deleter():
        for _ in range(6):
            time.sleep(0.05)
            with guard:
                victim = next(iter(done), None)
                if victim is not None:
                    done.pop(victim)
            if victim is not None:
                try:
                    c.svc.deleter.delete(victim)
                    stats["deleted"] += 1
                except ChunkVaultError:
                    pass

    def collector():
        for _ in range(6):
            time.sleep(0.04)
            try:
                stats["gc_rows"] += c.svc.gc.run(dry_run=False, limit=1000).rows_removed
            except ChunkVaultError:
                pass

    res = run_threads(lambda: uploader(0), lambda: uploader(1), deleter, collector, timeout=120)
    assert [r[0] for r in res] == ["ok"] * 4, [r for r in res if r[0] == "err"]  # no unexpected exception type anywhere
    assert stats["uploaded"] + stats["refused"] == 8 and stats["uploaded"] >= 1, stats  # the scenario really exercised the operations
    print("STRESS", stats)
    no_dangling_references(c)
    for fid, idx in done.items():  # every file that is still there restores to exactly what was uploaded
        assert c.client.get(f"/api/files/{fid}/download", headers=D).content == datas[idx]
    assert c.one("SELECT coalesce(sum(size_bytes), 0) FROM chunks") <= 4 * 1024 * KIB
