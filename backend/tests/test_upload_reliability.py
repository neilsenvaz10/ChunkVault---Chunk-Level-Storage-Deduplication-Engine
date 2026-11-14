"""Cloud upload reliability (C5E-2): lock hold time, deadline, give-back of reservations, duplicate verification, interrupted uploads.

Real PostgreSQL (disposable) + the fake Storage server. Nothing here talks to a real cloud service.
"""

import io
import threading
import time

import pytest

from app.core.errors import ChunkVaultError
from app.deduplication.interfaces import UploadDeadlineError
from tests.conftest import make_data
from tests.pg_fixtures import pg_url, requires_postgres, schema_conn, schema_name  # noqa: F401
from tests.test_cloud_workflows import A, D, KIB, launch  # noqa: F401  (launch is a fixture)

pytestmark = [pytest.mark.postgres, requires_postgres]


class FakeClock:
    """A controllable monotonic clock for the upload deadline."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def clocked(c):
    clock = FakeClock()
    c.svc.uploader._pipeline._clock = clock
    return clock


def up(c, seed, size, name=None):
    return c.svc.uploader.upload(name or f"f{seed}.bin", io.BytesIO(make_data(seed, size)))


def state(c):
    return dict(files=c.q("SELECT status, count(*) FROM files GROUP BY 1 ORDER BY 1"), chunks=c.one("SELECT count(*) FROM chunks"),
                manifest=c.one("SELECT count(*) FROM file_chunks"), objects=len(c.fake.objects), used=c.one("SELECT coalesce(sum(size_bytes),0) FROM chunks"))


# ============================================================================================ 1. what holds the writer lock

def test_staging_and_chunking_happen_before_the_writer_lock_and_storage_io_inside_it(launch):
    c = launch()
    held = lambda: not c.svc.lock.is_free()  # noqa: E731  (works from any thread: the Storage writes run in pool threads)
    seen = {"stage": [], "chunk": [], "publish": []}
    u = c.svc.uploader
    real_stage, real_chunk, real_publish = u._stage_upload, c.svc.chunker.chunk_stream, c.svc.store.publish

    def stage(*a, **k):
        seen["stage"].append(held())
        return real_stage(*a, **k)

    def chunk(*a, **k):
        for x in real_chunk(*a, **k):
            seen["chunk"].append(held())
            yield x

    def publish(*a, **k):
        seen["publish"].append(held())
        return real_publish(*a, **k)

    u._stage_upload, c.svc.chunker.chunk_stream, c.svc.store.publish = stage, chunk, publish
    res = up(c, 1, 300 * KIB)
    assert seen["stage"] == [False] and set(seen["chunk"]) == {False}  # body staging and chunking: lock NOT held
    assert seen["publish"] and set(seen["publish"]) == {True}  # Storage writes: lock held
    assert res.new_chunks == res.chunk_count


def test_the_file_is_chunked_exactly_once_even_when_the_quota_preflight_needs_the_plan(launch):
    c = launch({"stored_bytes_cap": 400 * KIB}, {"max_upload_bytes": 400 * KIB})
    up(c, 2, 300 * KIB)  # 100 KiB of quota left: the next upload's worst case no longer fits, so the preflight must look closer
    calls = []
    real = c.svc.chunker.chunk_stream
    c.svc.chunker.chunk_stream = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
    res = up(c, 2, 300 * KIB, "same-content-again.bin")  # a pure duplicate: needs no new bytes, so it must be accepted
    assert res.new_chunks == 0 and len(calls) == 1  # not chunked a second time for the quota check


def test_a_second_upload_is_staged_and_chunked_while_the_first_still_holds_the_lock(launch):
    c = launch({}, {"writer_lock_timeout_seconds": 30.0})
    first_in_lock, release_first = threading.Event(), threading.Event()
    second_analysed = threading.Event()
    u = c.svc.uploader
    real_checkpoint = u.checkpoint

    def checkpoint(name):
        if name == "t1_committed" and not first_in_lock.is_set():
            first_in_lock.set()
            assert second_analysed.wait(20), "the second upload could not be analysed while the first held the lock"
            release_first.wait(0.0)
        real_checkpoint(name)

    u.checkpoint = checkpoint
    real_chunk = c.svc.chunker.chunk_stream

    def chunk(*a, **k):
        if first_in_lock.is_set():
            second_analysed.set()
        return real_chunk(*a, **k)

    c.svc.chunker.chunk_stream = chunk
    out = {}
    t1 = threading.Thread(target=lambda: out.setdefault("a", up(c, 3, 200 * KIB)))
    t1.start()
    assert first_in_lock.wait(20)
    t2 = threading.Thread(target=lambda: out.setdefault("b", up(c, 4, 200 * KIB)))
    t2.start()
    t1.join(60)
    t2.join(60)
    assert set(out) == {"a", "b"} and second_analysed.is_set()
    assert c.one("SELECT count(*) FROM files WHERE status='complete'") == 2


def test_lock_hold_time_excludes_the_cpu_heavy_phase(launch):
    """Measured, not assumed: slow the chunker down and see that the writer lock is only held for the part after it."""
    c = launch({})
    real_chunk = c.svc.chunker.chunk_stream

    def slow_chunk(*a, **k):
        for x in real_chunk(*a, **k):
            time.sleep(0.01)  # ~16 chunks -> ~0.2 s of "CPU time"
            yield x

    c.svc.chunker.chunk_stream = slow_chunk
    spans = []
    real_acquire = c.svc.lock.acquire

    import contextlib

    @contextlib.contextmanager
    def timed(*a, **k):
        with real_acquire(*a, **k):
            t0 = time.perf_counter()
            try:
                yield
            finally:
                spans.append(time.perf_counter() - t0)

    c.svc.lock.acquire = timed
    t0 = time.perf_counter()
    up(c, 5, 200 * KIB)
    total = time.perf_counter() - t0
    assert len(spans) == 1 and total - spans[0] >= 0.15  # at least the chunking time happened outside the lock


# ============================================================================================ 2. deadline and give-back

@pytest.mark.parametrize("stage_hook,stage_name", [("staged", "staging"), ("chunk_published", "publishing chunks"), ("chunks_ready", "finishing")])
def test_a_deadline_stops_the_upload_cleanly_and_gives_back_everything_it_created(launch, stage_hook, stage_name):
    c = launch()
    clock = clocked(c)
    u = c.svc.uploader
    real = u.checkpoint
    u.checkpoint = lambda n: (setattr(clock, "t", clock.t + 1000.0) if n == stage_hook else None, real(n))[1]
    data_before = state(c)
    with pytest.raises(UploadDeadlineError) as e:
        up(c, 6, 300 * KIB)
    assert e.value.status_code == 504 and e.value.code == "upload_timeout" and stage_name in e.value.message
    after = state(c)
    assert after["manifest"] == 0 and after["chunks"] == 0 and after["objects"] == 0 and after["used"] == 0  # no reservations, no objects
    assert c.q("SELECT count(*) FROM files WHERE status = 'complete'") == [(0,)]
    if stage_hook == "staged":
        assert after["files"] == data_before["files"] == []  # before the lock: not even a file row
    else:
        assert after["files"] == [("failed", 1)] and c.q("SELECT status FROM operations") == [("failed",)]
    assert list(c.svc.settings.staging_dir.iterdir()) == []  # scratch removed
    del u.__dict__["checkpoint"]
    assert up(c, 6, 300 * KIB).new_chunks > 0  # and the system is simply usable again


def test_the_deadline_is_a_clean_504_through_the_api(launch):
    c = launch()
    clock = clocked(c)
    c.svc.uploader.checkpoint = lambda n: setattr(clock, "t", clock.t + 1000.0) if n == "t1_committed" else None
    r = c.upload(make_data(7, 100 * KIB))
    assert r.status_code == 504 and r.json()["error"]["code"] == "upload_timeout"
    assert r.json()["error"]["details"]["deadline_seconds"] == c.svc.settings.cloud.upload_deadline_seconds


def test_the_remaining_deadline_bounds_the_wait_for_the_writer_lock(launch):
    c = launch({}, {"writer_lock_timeout_seconds": 30.0})
    clock = clocked(c)
    c.svc.uploader.checkpoint = lambda n: setattr(clock, "t", clock.t + c.svc.settings.cloud.upload_deadline_seconds - 0.2) if n == "staged" else None
    t0 = time.perf_counter()
    with c.svc.lock.acquire():  # someone else holds the lock
        with pytest.raises(ChunkVaultError) as e:
            up(c, 8, 50 * KIB)
    assert e.value.code == "writer_busy" and time.perf_counter() - t0 < 5  # waited ~0.2 s of budget, not the configured 30 s
    assert state(c)["files"] == [] and c.one("SELECT count(*) FROM chunks") == 0


def test_cleanup_is_bounded_and_what_it_cannot_finish_stays_durable_and_collectable(launch):
    c = launch({"upload_cleanup_budget_seconds": 1, "orphan_min_age_seconds": 0.0, "storage_max_attempts": 1})
    clock = clocked(c)
    u = c.svc.uploader
    real = u.checkpoint
    u.checkpoint = lambda n: (setattr(clock, "t", clock.t + 1000.0) if n == "chunks_ready" else None, real(n))[1]
    c.fake.faults += [{"op": "delete", "delay": 3.0}] * 40  # Storage deletes are very slow
    t0 = time.perf_counter()
    with pytest.raises(UploadDeadlineError):
        up(c, 9, 200 * KIB)
    assert time.perf_counter() - t0 < 6  # the failure was not held hostage by the slow deletes
    left = c.one("SELECT count(*) FROM chunks")
    assert left > 0 and c.q("SELECT DISTINCT state FROM chunks") == [("deleting",)]  # durable, counted, refused by uploads ...
    time.sleep(3.5)  # let the abandoned delete threads finish
    c.fake.faults.clear()
    r = c.client.post("/api/admin/gc", headers=A, json={"dry_run": False, "limit": 1000}).json()  # ... and the explicit GC finishes it
    assert r["rows_removed"] == left and c.one("SELECT count(*) FROM chunks") == 0 and c.fake.objects == {}


def test_failure_cleanup_touches_only_what_that_upload_created(launch, monkeypatch):
    c = launch()
    base = make_data(10, 200 * KIB)
    ok = c.upload(base, "keeper.bin").json()  # a complete, referenced file whose chunks must survive anything below
    keeper_objects = dict(c.fake.objects)
    # a 'pending' row that does not belong to the failing upload (e.g. a concurrent upload's) and that the failing upload will adopt
    other = make_data(11, 40 * KIB)
    with c.svc.db.connection() as conn, c.svc.db.transaction(conn):
        h_other = c.svc.hasher.digest(other)
        c.svc.repo.reserve_chunks(conn, [(h_other, len(other))], 10**9)
    real = c.svc.repo.append_file_chunks
    monkeypatch.setattr(c.svc.repo, "append_file_chunks", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("injected T2 failure")))
    failing = base[:120 * KIB] + make_data(12, 80 * KIB)  # shares a prefix with the keeper, adds new chunks
    r = c.upload(failing, "doomed.bin")
    assert r.status_code == 500
    assert all(c.fake.objects.get(k) == v for k, v in keeper_objects.items())  # the keeper's objects: untouched
    assert c.client.get(f"/api/files/{ok['file_id']}/download", headers=D).content == base  # and it still restores
    assert c.one("SELECT count(*) FROM chunks WHERE hash = %s", h_other) == 1 and c.q("SELECT state FROM chunks WHERE hash = %s", h_other) == [("pending",)]
    assert c.q("SELECT DISTINCT state FROM chunks WHERE ref_count > 0") == [("stored",)]
    assert c.q("SELECT count(*) FROM v_ref_count_mismatch") == [(0,)]
    # only the doomed upload's own NEW chunks were given back: what remains is the keeper's rows plus the foreign pending row
    assert c.one("SELECT count(*) FROM chunks") == c.one("SELECT count(DISTINCT chunk_hash) FROM file_chunks") + 1


# ============================================================================================ 3. interrupted uploads

class Crash(BaseException):
    """Stands for the process dying (SIGKILL, out-of-memory, instance replaced): not an Exception, so no cleanup code runs."""


@pytest.mark.parametrize("hook", ["staged", "t1_committed", "chunk_published", "chunks_ready", "in_t2"])
def test_a_process_death_at_any_point_leaves_nothing_committed_and_the_system_recovers(launch, hook):
    c = launch({"orphan_min_age_seconds": 0.0})
    c.svc.uploader.checkpoint = lambda n: (_ for _ in ()).throw(Crash()) if n == hook else None
    with pytest.raises(Crash):
        up(c, 13, 200 * KIB)
    assert c.one("SELECT count(*) FROM file_chunks") == 0 and c.one("SELECT count(*) FROM files WHERE status='complete'") == 0
    assert list(c.svc.settings.staging_dir.iterdir()) == []  # scratch is removed even when the process "dies" (finally)
    assert c.svc.lock.is_free()  # the lock session is closed: nothing is blocked forever
    del c.svc.uploader.__dict__["checkpoint"]
    # whatever the dead upload left (a file in 'uploading', 'pending' rows, objects) is found and cleaned by the explicit tools ...
    c.owner.execute("UPDATE files SET created_at = now() - interval '2 hours'")
    c.owner.execute("UPDATE operations SET started_at = now() - interval '2 hours'")
    assert c.client.post("/api/integrity/reconcile", headers=A).status_code == 200
    c.client.post("/api/admin/gc", headers=A, json={"dry_run": False, "limit": 1000})
    assert c.one("SELECT count(*) FROM chunks") == 0 and c.q("SELECT count(*) FROM files WHERE status = 'uploading'") == [(0,)]
    ok = up(c, 13, 200 * KIB)  # ... and the same content uploads fine afterwards
    assert c.client.get(f"/api/files/{ok.file_id}/download", headers=D).content == make_data(13, 200 * KIB)


def test_a_retry_after_a_process_death_adopts_the_leftovers_instead_of_double_counting(launch):
    c = launch()
    c.svc.uploader.checkpoint = lambda n: (_ for _ in ()).throw(Crash()) if n == "chunks_ready" else None
    with pytest.raises(Crash):
        up(c, 14, 200 * KIB)
    counted = c.one("SELECT coalesce(sum(size_bytes),0) FROM chunks")
    assert counted == 200 * KIB  # the dead upload's reservations still count (it cannot give them back)
    del c.svc.uploader.__dict__["checkpoint"]
    ok = up(c, 14, 200 * KIB)
    assert c.one("SELECT sum(size_bytes) FROM chunks") == 200 * KIB  # the retry adopted the same rows: counted once
    assert c.client.get(f"/api/files/{ok.file_id}/download", headers=D).content == make_data(14, 200 * KIB)


# ============================================================================================ 4. writer-lock contention

def test_contending_uploads_are_serialised_without_losing_or_duplicating_anything(launch):
    c = launch({}, {"writer_lock_timeout_seconds": 60.0})
    results = []

    def go(seed):
        try:
            results.append(("ok", up(c, seed, 150 * KIB, f"c{seed}.bin").file_id))
        except ChunkVaultError as exc:
            results.append(("err", exc.code))

    ts = [threading.Thread(target=go, args=(s,)) for s in (20, 21, 22, 23)]
    [t.start() for t in ts]
    [t.join(120) for t in ts]
    assert [r[0] for r in results] == ["ok"] * 4
    assert c.one("SELECT count(*) FROM files WHERE status='complete'") == 4 and c.q("SELECT count(*) FROM v_ref_count_mismatch") == [(0,)]
    assert c.one("SELECT coalesce(sum(size_bytes),0) FROM chunks") == 4 * 150 * KIB  # distinct content: each counted exactly once


def test_a_waiter_that_times_out_gets_a_clear_409_and_changes_nothing(launch):
    c = launch({}, {"writer_lock_timeout_seconds": 0.3})
    with c.svc.lock.acquire():
        r = c.upload(make_data(24, 60 * KIB))
    assert r.status_code == 409 and r.json()["error"]["code"] == "writer_busy"
    assert state(c)["files"] == [] and c.one("SELECT count(*) FROM chunks") == 0 and list(c.svc.settings.staging_dir.iterdir()) == []


# ============================================================================================ 5. duplicate verification

def list_requests(c):
    return c.fake.count("list")


def test_verifying_duplicates_costs_at_most_one_listing_per_prefix_not_one_per_chunk(launch):
    c = launch({"batch_chunks": 64}, {"max_upload_bytes": 6 * 1024 * KIB})
    data = make_data(30, 5 * 1024 * KIB)  # ~300 chunks
    first = c.upload(data).json()
    n = first["chunk_count"]
    assert n > 256
    c.fake.requests.clear()
    second = c.upload(data, "again.bin").json()
    assert second["new_chunks"] == 0 and second["duplicate_chunks"] == n
    assert list_requests(c) <= 256 < n  # strictly fewer requests than chunks (the C5D figure was one request PER chunk)
    assert c.fake.count("upload") == 0 and c.fake.count("download") == 0


def test_the_prefix_batching_still_detects_a_missing_or_resized_duplicate_chunk(launch):
    c = launch()
    data = make_data(31, 300 * KIB)
    c.upload(data)
    hashes = [h for (h,) in c.q("SELECT hash FROM chunks ORDER BY hash")]
    c.fake.objects.pop(f"{c.fake.bucket}/{hashes[0][:2]}/{hashes[0]}")  # one object vanished
    r = c.upload(data, "dup.bin")
    assert r.status_code == 409 and r.json()["error"]["code"] == "chunk_unavailable" and "missing" in r.json()["error"]["message"]
    c.fake.put(hashes[0], c.fake.get(hashes[1]) or b"x")  # wrong size at that key
    r = c.upload(data, "dup2.bin")
    assert r.status_code == 409 and r.json()["error"]["code"] == "chunk_unavailable"
    assert c.one("SELECT count(*) FROM files WHERE status='complete'") == 1  # nothing was committed on top of damaged storage


def test_a_storage_failure_during_verification_fails_the_upload_it_is_never_read_as_ok(launch):
    c = launch({"storage_max_attempts": 1})
    data = make_data(32, 200 * KIB)
    c.upload(data)
    c.fake.always["list"] = 503
    r = c.upload(data, "dup.bin")
    assert r.status_code == 503 and r.json()["error"]["code"] == "storage_unavailable"
    assert c.one("SELECT count(*) FROM files WHERE status='complete'") == 1


@pytest.mark.parametrize("mode,lists,downloads", [("none", False, False), ("exists", True, False), ("full", False, True)])
def test_verification_modes_keep_their_meaning(launch, mode, lists, downloads):
    c = launch({"verify_on_reuse": mode})
    data = make_data(33, 150 * KIB)
    c.upload(data)
    c.fake.requests.clear()
    assert c.upload(data, "dup.bin").status_code == 201
    assert (c.fake.count("list") > 0) is lists and (c.fake.count("download") > 0) is downloads and c.fake.count("upload") == 0


def test_full_mode_still_catches_same_size_corruption(launch):
    c = launch({"verify_on_reuse": "full"})
    data = make_data(34, 150 * KIB)
    c.upload(data)
    h = c.q("SELECT hash FROM chunks ORDER BY hash LIMIT 1")[0][0]
    good = c.fake.get(h)
    c.fake.put(h, bytes([good[0] ^ 1]) + good[1:])
    r = c.upload(data, "dup.bin")
    assert r.status_code == 409 and r.json()["error"]["code"] == "chunk_unavailable"
