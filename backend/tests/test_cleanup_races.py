"""Adversarial interleavings of the failed-upload cleanup protocol (C5E-2A).

The writer lock is deliberately LOST in these tests (every operation proceeds as if it held it), so that only the database protocol
(guarded claim, row locks, fence in the manifest transaction, durable 'deleting' state) stands between a failed upload's cleanup and a
chunk that another upload references. Interleavings are forced with checkpoints, not left to luck. Real PostgreSQL + fake Storage.
"""

import io
import threading
import time

import pytest

from app.core.errors import ChunkVaultError
from tests.conftest import make_data
from tests.pg_fixtures import pg_url, requires_postgres, schema_conn, schema_name  # noqa: F401
from tests.test_cloud_concurrency import lose_the_lock, no_dangling_references
from tests.test_cloud_workflows import A, D, KIB, launch  # noqa: F401  (launch is a fixture)

pytestmark = [pytest.mark.postgres, requires_postgres]
WAIT = 30


class Boom(RuntimeError):
    pass


def hooks(c, fn):
    """Call ``fn(checkpoint_name, thread_name)`` at every checkpoint of every upload."""
    real = c.svc.uploader.checkpoint
    c.svc.uploader.checkpoint = lambda n: (fn(n, threading.current_thread().name), real(n))[1]
    return real


def spawn(name, c, data, results):
    def run():
        try:
            results[name] = ("ok", c.svc.uploader.upload(f"{name}.bin", io.BytesIO(data)))
        except BaseException as exc:  # noqa: BLE001
            results[name] = ("err", exc)

    t = threading.Thread(target=run, name=name)
    t.start()
    return t


def settle(c):
    """Whatever happened: no committed manifest points at a missing chunk or object, counters match, nothing is orphaned."""
    no_dangling_references(c)
    assert c.q("SELECT count(*) FROM v_ref_count_mismatch") == [(0,)]
    rows = {h for (h,) in c.q("SELECT hash FROM chunks")}
    for key in c.fake.objects:
        assert key.rsplit("/", 1)[-1] in rows, "an object without a row was left behind"


def complete_files(c):
    return c.q("SELECT file_id FROM files WHERE status = 'complete'")


# ---------------------------------------------------------------------------------------------------------------------------
def test_a_failing_upload_never_deletes_a_chunk_a_concurrent_upload_committed_after_adopting_it(launch, monkeypatch):
    c = launch()
    lose_the_lock(monkeypatch, c)
    data = make_data(501, 300 * KIB)
    a_published, b_done, res = threading.Event(), threading.Event(), {}

    def fn(name, thread):
        if thread == "A" and name == "chunks_ready":
            a_published.set()
            assert b_done.wait(WAIT)  # B adopts the pending rows of A and COMMITS while A is still alive
            raise Boom("A fails after B committed")

    hooks(c, fn)
    ta = spawn("A", c, data, res)
    assert a_published.wait(WAIT)
    tb = spawn("B", c, data, res)
    tb.join(WAIT)
    b_done.set()
    ta.join(WAIT)
    assert res["B"][0] == "ok" and res["A"][0] == "err"
    fid = res["B"][1].file_id
    assert c.client.get(f"/api/files/{fid}/download", headers=D).content == data  # every chunk B references is intact
    assert c.q("SELECT DISTINCT state FROM chunks") == [("stored",)]
    settle(c)


def test_a_cleanup_that_runs_before_the_other_upload_commits_makes_that_upload_abort_never_dangle(launch, monkeypatch):
    c = launch()
    lose_the_lock(monkeypatch, c)
    data = make_data(502, 300 * KIB)
    a_published, b_ready, a_cleaned, res = threading.Event(), threading.Event(), threading.Event(), {}

    def fn(name, thread):
        if thread == "A" and name == "chunks_ready":
            a_published.set()
            assert b_ready.wait(WAIT)
            raise Boom("A fails; its cleanup runs before the manifest transaction of B")
        if thread == "B" and name == "chunks_ready":
            b_ready.set()
            assert a_cleaned.wait(WAIT)  # B has adopted the rows of A and is about to write its manifest

    real = hooks(c, fn)
    ta = spawn("A", c, data, res)
    assert a_published.wait(WAIT)
    tb = spawn("B", c, data, res)
    ta.join(WAIT)
    a_cleaned.set()
    tb.join(WAIT)
    assert res["A"][0] == "err" and res["B"][0] == "err"
    assert isinstance(res["B"][1], ChunkVaultError)  # a clear, retryable refusal, not a corrupt commit
    assert complete_files(c) == [] and c.one("SELECT count(*) FROM file_chunks") == 0
    settle(c)
    c.svc.uploader.checkpoint = real
    r = c.upload(data, "retry.bin")
    assert r.status_code == 201 and c.client.get(f"/api/files/{r.json()['file_id']}/download", headers=D).content == data
    settle(c)


def test_a_cleanup_blocked_by_the_other_manifest_transaction_skips_what_that_upload_referenced(launch, monkeypatch):
    c = launch()
    lose_the_lock(monkeypatch, c)
    data = make_data(503, 300 * KIB)
    a_ready, b_in_t2, b_may_commit, res = threading.Event(), threading.Event(), threading.Event(), {}

    def fn(name, thread):
        if thread == "A" and name == "chunks_ready":
            a_ready.set()
            assert b_in_t2.wait(WAIT)  # B now holds the row locks of the shared chunks, inside its manifest transaction
            raise Boom("A fails and its claim must wait for the row locks of B")
        if thread == "B" and name == "in_t2":
            b_in_t2.set()
            assert b_may_commit.wait(WAIT)

    hooks(c, fn)
    ta = spawn("A", c, data, res)
    assert a_ready.wait(WAIT)
    tb = spawn("B", c, data, res)
    assert b_in_t2.wait(WAIT)
    ta.join(1.5)  # the cleanup of A is queued behind the locks of B
    b_may_commit.set()
    ta.join(WAIT)
    tb.join(WAIT)
    assert res["B"][0] == "ok" and res["A"][0] == "err"
    assert c.client.get(f"/api/files/{res['B'][1].file_id}/download", headers=D).content == data
    assert c.q("SELECT DISTINCT state FROM chunks") == [("stored",)]
    settle(c)


def test_many_overlapping_uploads_with_random_failures_never_lose_a_referenced_chunk(launch, monkeypatch):
    c = launch({"io_workers": 4})
    base = make_data(510, 240 * KIB)
    keeper = c.upload(base, "keeper.bin").json()["file_id"]
    lose_the_lock(monkeypatch, c)
    variants = [base[:160 * KIB] + make_data(520 + i, 120 * KIB) for i in range(6)]
    res = {}

    def fn(name, thread):
        if thread in ("F1", "F3", "F5") and name in ("chunk_published", "chunks_ready"):
            raise Boom(thread)

    hooks(c, fn)
    ts = [spawn(f"F{i}", c, v, res) for i, v in enumerate(variants)]
    [t.join(WAIT * 2) for t in ts]
    assert not any(t.is_alive() for t in ts)
    for i in (1, 3, 5):
        assert res[f"F{i}"][0] == "err"
    assert c.client.get(f"/api/files/{keeper}/download", headers=D).content == base
    for i in (0, 2, 4):  # the others either completed exactly, or were refused cleanly
        if res[f"F{i}"][0] == "ok":
            assert c.client.get(f"/api/files/{res[f'F{i}'][1].file_id}/download", headers=D).content == variants[i]
        else:
            assert isinstance(res[f"F{i}"][1], ChunkVaultError)
    settle(c)


def test_the_quota_stays_exact_when_a_failing_and_a_committing_upload_share_new_chunks(launch, monkeypatch):
    c = launch({"stored_bytes_cap": 600 * KIB}, {"max_upload_bytes": 400 * KIB})
    lose_the_lock(monkeypatch, c)
    data = make_data(530, 320 * KIB)
    a_ready, b_done, res = threading.Event(), threading.Event(), {}

    def fn(name, thread):
        if thread == "A" and name == "chunks_ready":
            a_ready.set()
            assert b_done.wait(WAIT)
            raise Boom("A")

    hooks(c, fn)
    ta = spawn("A", c, data, res)
    assert a_ready.wait(WAIT)
    tb = spawn("B", c, data, res)
    tb.join(WAIT)
    b_done.set()
    ta.join(WAIT)
    assert res["B"][0] == "ok"
    used = c.one("SELECT coalesce(sum(size_bytes), 0) FROM chunks")
    referenced = c.one("SELECT coalesce(sum(size_bytes), 0) FROM chunks c WHERE EXISTS (SELECT 1 FROM file_chunks f WHERE f.chunk_hash = c.hash)")
    assert used == referenced <= len(data)  # shared chunks are counted once, and nothing is left counted for the failed upload
    settle(c)


# ---------------------------------------------------------------------------------------------------------------------------
def test_a_delete_still_running_after_a_bounded_cleanup_can_never_remove_a_republished_object(launch):
    """The row of a chunk whose delete request is still in flight stays 'deleting': GC will not release it and uploads get 409, so a
    late delete cannot hit an object that was published again after its row was gone."""
    c = launch({"upload_cleanup_budget_seconds": 1, "orphan_min_age_seconds": 0.0, "storage_max_attempts": 1, "storage_timeout_seconds": 20})
    u = c.svc.uploader
    real = u.checkpoint
    u.checkpoint = lambda n: (_ for _ in ()).throw(Boom("late failure")) if n == "chunks_ready" else real(n)
    c.fake.faults += [{"op": "delete", "delay": 4.0}] * 60  # every delete is stuck for 4 s
    data = make_data(540, 100 * KIB)
    with pytest.raises(ChunkVaultError):
        u.upload("doomed.bin", io.BytesIO(data))
    stuck = [h for (h,) in c.q("SELECT hash FROM chunks WHERE state = 'deleting'")]
    flying = {h for h in stuck if c.svc.store.delete_in_flight(h)}
    assert flying  # (deletes beyond the worker count were cancelled before they were sent: those cannot land late)
    r = c.client.post("/api/admin/gc", headers=A, json={"dry_run": False, "limit": 1000}).json()
    kept = set(r["failed"])  # GC refuses to release rows with a delete in flight (some may have timed out meanwhile)
    assert kept and kept <= flying and r["rows_removed"] == len(stuck) - len(kept)
    assert {h for (h,) in c.q("SELECT hash FROM chunks WHERE state = 'deleting'")} == kept
    u.checkpoint = real
    assert c.upload(data, "same.bin").status_code == 409  # being reclaimed: nothing can be published over the pending delete
    end = time.time() + 30
    while any(c.svc.store.delete_in_flight(h) for h in flying) and time.time() < end:
        time.sleep(0.2)
    c.fake.faults.clear()
    r = c.client.post("/api/admin/gc", headers=A, json={"dry_run": False, "limit": 1000}).json()
    assert r["rows_removed"] == len(kept) and c.one("SELECT count(*) FROM chunks") == 0
    ok = c.upload(data, "again.bin")
    assert ok.status_code == 201 and c.client.get(f"/api/files/{ok.json()['file_id']}/download", headers=D).content == data
    settle(c)
