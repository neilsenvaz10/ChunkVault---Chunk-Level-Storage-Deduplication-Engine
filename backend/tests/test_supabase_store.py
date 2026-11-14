"""SupabaseChunkStore against a disposable local fake of the Storage REST API (no network, no real Supabase project).

Covers: key safety, upload/download, immutability and duplicates, ambiguous outcomes, retries and backoff, timeouts and the
total deadline, authorization failures, content corruption, delete behaviour, enumeration, redirects, size caps and secrecy.
"""

import hashlib
import logging
import threading
import time

import httpx
import pytest

from app.core.config import CloudSettings
from app.hashing.interfaces import InvalidDigestError
from app.storage.filesystem import ChunkStatus
from app.storage.interfaces import (
    ChunkCorruptedError,
    ChunkNotFoundError,
    ChunkStoreAuthError,
    ChunkStoreError,
    ChunkStoreUnavailableError,
    ChunkWriteError,
)
from app.storage.supabase import CloudStagedChunk, StorageConfig, SupabaseChunkStore, object_key
from tests.fake_supabase import KEY, FakeStorage


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture
def fake():
    s = FakeStorage()
    yield s
    s.close()


def make_store(fake, **kw):
    sleeps = []
    cfg = dict(base_url=fake.url, bucket=fake.bucket, connect_timeout=1.0, timeout=2.0, max_attempts=3, deadline=20.0,
               max_object_bytes=64 * 1024)
    cfg.update(kw.pop("cfg", {}))
    store = SupabaseChunkStore(StorageConfig(**cfg), kw.pop("key", KEY), sleep=sleeps.append, rng=lambda: 1.0, **kw)
    store.sleeps = sleeps
    return store


@pytest.fixture
def store(fake):
    s = make_store(fake)
    yield s
    s.close()


def put(store, data: bytes) -> tuple[str, bool]:
    h = sha(data)
    return h, store.publish(store.stage(h, data))


# ------------------------------------------------------------------------------------------------- keys / staging

@pytest.mark.parametrize("bad", ["", "../etc/passwd", "ab/../cd", "A" * 64, "a" * 63, "a" * 65, "g" * 64, "a" * 63 + "/",
                                 "ab/" + "a" * 61, ("a" * 63) + "\n", "é" * 64, None, 5])
def test_unsafe_keys_are_rejected_before_any_request(store, fake, bad):
    for call in (store.exists, store.read, store.delete, store.stat, store.verify):
        with pytest.raises(InvalidDigestError):
            call(bad)
    with pytest.raises(InvalidDigestError):
        store.stage(bad, b"x")
    assert fake.requests == []


def test_object_key_layout_matches_the_local_store():
    h = "ab" + "c" * 62
    assert object_key(h) == f"ab/{h}"


def test_stage_checks_digest_size_and_holds_bytes_in_memory(store, fake):
    data = b"hello chunk"
    st = store.stage(sha(data), data)
    assert isinstance(st, CloudStagedChunk) and st.size_bytes == len(data) and st.data == data and not st.path.exists()
    with pytest.raises(ChunkWriteError):
        store.stage(sha(b"other"), data)
    with pytest.raises(ChunkWriteError):
        store.stage(sha(b""), b"")
    with pytest.raises(ChunkWriteError):
        store.stage(sha(b"x" * 70_000), b"x" * 70_000)  # over max_object_bytes
    store.discard(st)  # nothing to remove
    assert list(store.iter_staged()) == [] and fake.requests == []


def test_repr_and_describe_never_contain_the_key(store):
    assert KEY not in repr(store) and KEY not in str(store.describe())


# ------------------------------------------------------------------------------------------------- round trip

def test_upload_then_download_round_trip_uses_the_private_endpoints(store, fake):
    data = b"A" * 5000
    h, created = put(store, data)
    assert created is True and fake.get(h) == data
    up = fake.requests[0]
    assert up["method"] == "POST" and up["path"] == f"/storage/v1/object/chunkvault-chunks/{h[:2]}/{h}"
    assert up["authorized"] and up["headers"]["x-upsert"] == "false" and up["headers"]["content-type"] == "application/octet-stream"
    assert store.read(h) == data
    dl = fake.requests[-1]
    assert dl["method"] == "GET" and "/object/authenticated/" in dl["path"] and "/object/public/" not in dl["path"] and dl["authorized"]
    assert all(r["authorized"] for r in fake.requests) and all("apikey" in r["headers"] for r in fake.requests)


def test_exists_stat_check_and_verify(store, fake):
    data = b"B" * 3000
    h, _ = put(store, data)
    assert store.exists(h) and store.stat(h) == 3000
    assert store.check(h, 3000) is ChunkStatus.OK and store.check(h, 3001) is ChunkStatus.CORRUPT
    assert store.verify(h, 3000) is ChunkStatus.OK
    absent = sha(b"nothing")
    assert not store.exists(absent) and store.stat(absent) is None
    assert store.check(absent) is ChunkStatus.MISSING and store.verify(absent) is ChunkStatus.MISSING


def test_stat_matches_the_exact_name_not_a_substring(store, fake):
    h, _ = put(store, b"C" * 100)
    similar = h[:2] + h[2:-1] + ("0" if h[-1] != "0" else "1")
    assert store.stat(similar) is None  # the listing's search is a substring filter; the client demands an exact name


# ------------------------------------------------------------------------------------------------- immutability

@pytest.mark.parametrize("style", ["409", "400"])
def test_duplicate_object_is_not_overwritten_and_reports_existing(store, fake, style):
    fake.duplicate_style = style
    data = b"D" * 2000
    h, first = put(store, data)
    original = fake.get(h)
    again = store.publish(store.stage(h, data))
    assert first is True and again is False and fake.get(h) == original
    assert fake.count("upload") == 2 and fake.count("download") == 1  # the existing object was verified, not replaced


@pytest.mark.parametrize("style", ["409", "400"])
def test_a_corrupt_existing_object_is_reported_and_left_untouched(store, fake, style):
    fake.duplicate_style = style
    data = b"E" * 2000
    h = sha(data)
    fake.put(h, b"tampered bytes!")
    with pytest.raises(ChunkCorruptedError):
        store.publish(store.stage(h, data))
    assert fake.get(h) == b"tampered bytes!"  # NEVER overwritten
    assert fake.count("delete") == 0


def test_concurrent_publishers_of_the_same_chunk_create_it_exactly_once(fake):
    data = b"F" * 4000
    h = sha(data)
    results, errors = [], []

    def worker():
        s = make_store(fake)
        try:
            results.append(s.publish(s.stage(h, data)))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            s.close()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    assert errors == [] and sorted(results) == [False] * 7 + [True] and fake.get(h) == data


def test_publish_refuses_a_foreign_staged_object(store):
    from app.storage.interfaces import StagedChunk
    from pathlib import Path
    with pytest.raises(ChunkWriteError):
        store.publish(StagedChunk(hash=sha(b"x"), path=Path("x"), size_bytes=1))


# ------------------------------------------------------------------------------------------------- corruption on read

def test_downloaded_bytes_are_verified_before_being_returned(store, fake):
    h, _ = put(store, b"G" * 2000)
    fake.faults.append({"op": "download", "corrupt": True})
    with pytest.raises(ChunkCorruptedError):
        store.read(h)
    fake.faults.append({"op": "download", "body": b"short"})
    with pytest.raises(ChunkCorruptedError):
        store.read(h)
    assert store.read(h) == b"G" * 2000  # the stored object itself was fine; only the transfers were damaged
    fake.faults.append({"op": "download", "corrupt": True})
    assert store.verify(h) is ChunkStatus.CORRUPT
    fake.put(h, b"damaged at rest")
    assert store.verify(h) is ChunkStatus.CORRUPT and store.verify(h, 15) is ChunkStatus.CORRUPT
    with pytest.raises(ChunkCorruptedError):
        store.read(h)


@pytest.mark.parametrize("style", ["404", "400"])
def test_missing_object_is_reported_in_both_response_shapes(store, fake, style):
    fake.missing_style = style
    h = sha(b"never stored")
    with pytest.raises(ChunkNotFoundError):
        store.read(h)
    assert store.verify(h) is ChunkStatus.MISSING


# ------------------------------------------------------------------------------------------------- retries

def test_transient_failures_are_retried_with_backoff_then_succeed(store, fake):
    h, _ = put(store, b"H" * 1500)
    fake.faults += [{"op": "download", "status": 503}, {"op": "download", "status": 429}]
    assert store.read(h) == b"H" * 1500
    assert fake.count("download") == 3 and store.sleeps == [pytest.approx(0.25), pytest.approx(0.5)]  # exponential, jitter fixed at 1.0


def test_retries_are_bounded_and_end_in_a_clear_error(store, fake):
    h = sha(b"x")
    fake.faults += [{"op": "download", "status": 500}] * 10
    with pytest.raises(ChunkStoreUnavailableError) as e:
        store.read(h)
    assert fake.count("download") == 3 and e.value.details["attempts"] == 3 and "unavailable" in e.value.message


def test_max_attempts_one_never_retries(fake):
    s = make_store(fake, cfg={"max_attempts": 1})
    fake.faults += [{"op": "download", "status": 503}]
    with pytest.raises(ChunkStoreUnavailableError):
        s.read(sha(b"x"))
    assert fake.count("download") == 1 and s.sleeps == []
    s.close()


def test_client_errors_other_than_the_known_ones_are_not_retried(store, fake):
    fake.faults.append({"op": "upload", "status": 422})
    data = b"I" * 500
    with pytest.raises(ChunkWriteError):
        store.publish(store.stage(sha(data), data))
    assert fake.count("upload") == 1


def test_oversized_upload_rejection_is_a_write_error_without_retry(store, fake):
    fake.faults.append({"op": "upload", "status": 413})
    data = b"J" * 500
    with pytest.raises(ChunkWriteError, match="too large"):
        store.publish(store.stage(sha(data), data))
    assert fake.count("upload") == 1


# ------------------------------------------------------------------------------------------------- ambiguous outcomes

def test_upload_lost_after_the_object_was_stored_is_resolved_on_retry(store, fake):
    data = b"K" * 3000
    h = sha(data)
    fake.faults.append({"op": "upload", "drop_after_store": True})  # stored, but the client never sees the answer
    assert store.publish(store.stage(h, data)) is True  # retry hit the duplicate, verified it, and reports "created" (conservative)
    assert fake.get(h) == data and fake.count("upload") == 2 and fake.count("download") == 1


def test_all_attempts_lost_but_object_stored_is_found_by_the_final_probe(store, fake):
    data = b"L" * 3000
    h = sha(data)
    fake.faults += [{"op": "upload", "drop_after_store": True}, {"op": "upload", "drop": True}, {"op": "upload", "drop": True}]
    assert store.publish(store.stage(h, data)) is True
    assert fake.get(h) == data


def test_unknown_outcome_is_reported_as_unknown_and_nothing_is_claimed(store, fake):
    data = b"M" * 3000
    h = sha(data)
    fake.faults += [{"op": "upload", "drop": True}] * 3
    with pytest.raises(ChunkStoreUnavailableError) as e:
        store.publish(store.stage(h, data))
    assert e.value.details["outcome"] == "unknown" and fake.get(h) is None


def test_ambiguous_upload_whose_object_is_corrupt_is_not_accepted(store, fake):
    data = b"N" * 3000
    h = sha(data)
    fake.put(h, b"not the right bytes")
    fake.faults.append({"op": "upload", "status": 503})  # first attempt ambiguous; the retry then meets the bad object
    with pytest.raises(ChunkCorruptedError):
        store.publish(store.stage(h, data))
    assert fake.get(h) == b"not the right bytes"


# ------------------------------------------------------------------------------------------------- timeouts

def test_a_slow_service_times_out_and_the_wait_is_bounded(fake):
    s = make_store(fake, cfg={"timeout": 0.3, "max_attempts": 2})
    fake.faults += [{"op": "download", "delay": 1.5}, {"op": "download", "delay": 1.5}]
    t0 = time.monotonic()
    with pytest.raises(ChunkStoreUnavailableError):
        s.read(sha(b"x"))
    assert time.monotonic() - t0 < 3.0  # two attempts of ~0.3 s each, not the server's 1.5 s delays
    s.close()


def test_a_timed_out_attempt_is_retried_and_can_succeed(fake):
    s = make_store(fake, cfg={"timeout": 0.3})
    h, _ = put(s, b"O" * 1000)
    fake.faults.append({"op": "download", "delay": 1.0})
    assert s.read(h) == b"O" * 1000
    s.close()


def test_the_total_deadline_stops_further_attempts(fake):
    now = [0.0]
    sleeps = []

    def clock():
        return now[0]

    def sleep(d):
        sleeps.append(d)
        now[0] += 100  # a very slow backoff blows the 10 s budget

    s = SupabaseChunkStore(StorageConfig(base_url=fake.url, bucket=fake.bucket, max_attempts=6, deadline=10.0, timeout=2.0),
                           KEY, sleep=sleep, rng=lambda: 1.0, clock=clock)
    fake.faults += [{"op": "download", "status": 503}] * 10
    with pytest.raises(ChunkStoreUnavailableError):
        s.read(sha(b"x"))
    assert fake.count("download") == 1  # budget exhausted after the first failure
    s.close()


def test_connection_refused_is_unavailable_not_a_crash():
    s = SupabaseChunkStore(StorageConfig(base_url="http://127.0.0.1:9", bucket="b", connect_timeout=0.5, max_attempts=2, deadline=5.0),
                           KEY, sleep=lambda d: None)
    with pytest.raises(ChunkStoreUnavailableError):
        s.read(sha(b"x"))
    assert s.ping() is False
    s.close()


# ------------------------------------------------------------------------------------------------- authorization

@pytest.mark.parametrize("style", ["401", "403", "400jwt"])
def test_rejected_credentials_fail_fast_without_retrying_or_leaking(fake, style, caplog):
    fake.auth_failure_style = style
    wrong = "wrong-service-key-" + "w" * 24
    s = make_store(fake, key=wrong)
    h = sha(b"x")
    with caplog.at_level(logging.DEBUG):
        for call in (lambda: s.read(h), lambda: s.exists(h), lambda: s.delete(h), lambda: s.publish(s.stage(sha(b"y"), b"y"))):
            with pytest.raises(ChunkStoreAuthError) as e:
                call()
            assert wrong not in str(e.value) and KEY not in str(e.value) and "Bearer" not in str(e.value)
    assert len(fake.requests) == 4 and s.sleeps == []  # one request each: never retried
    assert wrong not in caplog.text and "Bearer" not in caplog.text and s.ping() is False
    s.close()


def test_the_service_key_is_never_logged_even_when_retrying(store, fake, caplog):
    fake.faults += [{"op": "download", "status": 500}, {"op": "download", "status": 500}]
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ChunkStoreError):
            store.read(sha(b"x"))
    assert caplog.text and KEY not in caplog.text and "Bearer" not in caplog.text and "injected" not in caplog.text  # no bodies either


def test_redirects_are_not_followed_so_the_key_cannot_leave(store, fake):
    other = FakeStorage()
    try:
        fake.faults.append({"op": "download", "redirect": other.url + "/storage/v1/object/authenticated/x/y"})
        with pytest.raises(ChunkStoreError):
            store.read(sha(b"x"))
        assert other.requests == []
    finally:
        other.close()


# ------------------------------------------------------------------------------------------------- delete

def test_delete_removes_exactly_one_object_and_is_idempotent(store, fake):
    h1, _ = put(store, b"P" * 800)
    h2, _ = put(store, b"Q" * 800)
    assert store.delete(h1) is True
    assert fake.get(h1) is None and fake.get(h2) == b"Q" * 800
    assert store.delete(h1) is False  # already absent
    req = [r for r in fake.requests if r["op"] == "delete"][0]
    assert req["method"] == "DELETE" and req["path"] == "/storage/v1/object/chunkvault-chunks"


def test_delete_request_names_only_the_validated_key(store, fake):
    import json
    h, _ = put(store, b"R" * 600)
    store.delete(h)
    sent = json.loads([r for r in fake.requests if r["op"] == "delete"][0]["body"])
    assert sent == {"prefixes": [f"{h[:2]}/{h}"]}  # one exact key: no prefix, wildcard or listing-driven deletion


def test_nothing_deletes_automatically(store, fake):
    data = b"S" * 2000
    h, _ = put(store, data)
    store.publish(store.stage(h, data))  # duplicate path
    fake.put(sha(b"z"), b"corrupt")
    for call in (lambda: store.read(sha(b"z")), lambda: store.verify(sha(b"z")), lambda: store.stat(h), lambda: list(store.iter_hashes()),
                 lambda: store.disk_usage(), lambda: store.ping()):
        try:
            call()
        except ChunkStoreError:
            pass
    assert fake.count("delete") == 0 and fake.get(h) == data and fake.get(sha(b"z")) == b"corrupt"


def test_delete_failure_surfaces_as_an_error_not_silence(store, fake):
    h, _ = put(store, b"T" * 700)
    fake.faults.append({"op": "delete", "status": 422})
    with pytest.raises(ChunkWriteError):
        store.delete(h)
    assert fake.get(h) is not None


# ------------------------------------------------------------------------------------------------- size caps / listing

def test_oversized_download_is_refused_while_streaming(fake):
    s = make_store(fake, cfg={"max_object_bytes": 2048})
    h = sha(b"U")
    fake.put(h, b"U" * 10_000)
    with pytest.raises(ChunkStoreError, match="unexpectedly large"):
        s.read(h)
    s.close()


def test_listing_paginates_ignores_foreign_names_and_sums_sizes(fake, monkeypatch):
    import app.storage.supabase as mod

    monkeypatch.setattr(mod, "_LIST_PAGE", 2)
    s = make_store(fake)
    stored = {}
    for i in range(7):
        d = f"chunk-{i}".encode() * 10
        h = sha(d)
        s.publish(s.stage(h, d))
        stored[h] = len(d)
    fake.objects[f"{fake.bucket}/{'ab'}/not-a-digest"] = b"junk"  # foreign object: must be ignored
    assert set(s.iter_hashes()) == set(stored)
    assert s.disk_usage() == (len(stored), sum(stored.values()))
    assert s.ping() is True
    s.close()


def test_from_settings_builds_the_configuration(fake):
    c = CloudSettings(database_url="postgresql://u:p@h.example:5432/db", supabase_url=fake.url, supabase_service_key=KEY,
                      demo_token="d" * 40, admin_token="a" * 40, bucket=fake.bucket, storage_timeout_seconds=7.5,
                      storage_max_attempts=2)
    s = SupabaseChunkStore.from_settings(c, sleep=lambda d: None)
    assert s.describe()["bucket"] == fake.bucket and s.describe()["timeout_seconds"] == 7.5 and s.describe()["max_attempts"] == 2
    h, created = put(s, b"V" * 900)
    assert created and s.read(h) == b"V" * 900
    s.close()


def test_constructor_refuses_insecure_or_missing_urls():
    for bad in ("", "ftp://x", "http://example.com"):
        with pytest.raises(ValueError):
            SupabaseChunkStore(StorageConfig(base_url=bad, bucket="b"), KEY)


def test_a_non_json_error_body_is_handled_without_crashing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, content=b"<html>proxy error</html>")

    s = SupabaseChunkStore(StorageConfig(base_url="https://x.supabase.co", bucket="b"), KEY, transport=httpx.MockTransport(handler),
                           sleep=lambda d: None)
    data = b"W" * 100
    with pytest.raises(ChunkWriteError):
        s.publish(s.stage(sha(data), data))
    with pytest.raises(ChunkStoreError):
        s.read(sha(data))
    s.close()


# ------------------------------------------------------------------------------------------------- C4 additions

def test_read_many_returns_verified_chunks_in_order_with_bounded_concurrency(fake):
    s = make_store(fake, io_workers=3)
    datas = [bytes([i]) * 700 for i in range(12)]
    hs = [sha(d) for d in datas]
    for d in datas:
        s.publish(s.stage(sha(d), d))
    fake.latency = 0.03
    assert list(s.read_many(hs)) == datas  # order preserved even though requests overlap
    assert 2 <= fake.max_inflight["download"] <= 3
    assert list(s.read_many([hs[0], hs[0], hs[1]])) == [datas[0], datas[0], datas[1]]  # repeats allowed
    assert list(s.read_many([])) == []
    s.close()


def test_read_many_stops_at_the_first_failure_and_cancels_the_rest(fake):
    s = make_store(fake, io_workers=2)
    datas = [bytes([i]) * 500 for i in range(10)]
    hs = [sha(d) for d in datas]
    for d in datas:
        s.publish(s.stage(sha(d), d))
    fake.objects.pop(f"{fake.bucket}/{hs[3][:2]}/{hs[3]}")
    got = []
    with pytest.raises(ChunkNotFoundError):
        for chunk in s.read_many(hs):
            got.append(chunk)
    assert got == datas[:3]
    assert fake.count("download") < len(hs) + 2  # nothing like a full fan-out after the failure
    with pytest.raises(InvalidDigestError):
        list(s.read_many(["../x"]))
    s.close()


def test_inventory_is_one_listing_per_prefix_and_reports_sizes(fake):
    s = make_store(fake)
    stored = {}
    for i in range(5):
        d = f"inv-{i}".encode() * (20 + i)
        s.publish(s.stage(sha(d), d))
        stored[sha(d)] = len(d)
    fake.requests.clear()
    assert s.inventory() == stored and fake.count("list") == 256
    s.close()


def test_staging_scratch_lists_leftovers_and_is_never_used_for_chunks(fake, tmp_path):
    s = make_store(fake, staging_dir=tmp_path / "scratch")
    (tmp_path / "scratch" / "upload-x.part").write_bytes(b"left over")
    assert [p.name for p in s.iter_staged()] == ["upload-x.part"]
    d = b"chunk"
    st = s.stage(sha(d), d)
    assert list((tmp_path / "scratch").iterdir()) == [tmp_path / "scratch" / "upload-x.part"]  # chunk staging is in memory
    s.close()


# ------------------------------------------------------------------------------------------------- check_many (C5E-2)

def test_check_many_gives_the_same_verdicts_as_check_with_one_listing_per_prefix(store, fake):
    datas = [bytes([i]) * (500 + i) for i in range(40)]
    hashes = [put(store, d)[0] for d in datas]
    items = [(h, len(d)) for h, d in zip(hashes, datas)]
    items.append((sha(b"never stored"), 10))  # missing
    items.append((hashes[1], len(datas[1]) + 1))  # wrong size (replaces the OK entry: one verdict per hash)
    fake.requests.clear()
    got = store.check_many(items)
    assert all(got[h] is store.check(h, n) for h, n in items[:-2] if h != hashes[1]) and got[items[-2][0]] is ChunkStatus.MISSING
    assert got[hashes[1]] is ChunkStatus.CORRUPT
    prefixes = {h[:2] for h, _ in items}
    # one listing per prefix involved (not per chunk); the per-chunk `check` calls above are excluded by clearing first
    fake.requests.clear()
    store.check_many(items)
    assert fake.count("list") == len(prefixes) <= 256 and fake.count("upload") == 0 and fake.count("download") == 0


def test_check_many_of_nothing_makes_no_request(store, fake):
    assert store.check_many([]) == {} and fake.requests == []


def test_check_many_rejects_unsafe_hashes_before_any_request(store, fake):
    with pytest.raises(InvalidDigestError):
        store.check_many([("../x", 1)])
    assert fake.requests == []


def test_check_many_handles_a_prefix_listing_longer_than_one_page(store, fake):
    prefix = "ab"
    hashes = []
    for i in range(1200):  # force pagination of one prefix by fabricating names that all share it
        h = prefix + hashlib.sha256(str(i).encode()).hexdigest()[2:]
        fake.put(h, b"x" * 7)
        hashes.append(h)
    fake.requests.clear()
    got = store.check_many([(h, 7) for h in hashes])
    assert set(got.values()) == {ChunkStatus.OK} and len(got) == 1200
    assert fake.count("list") >= 2  # several pages, still a handful of requests instead of 1200


def test_check_many_never_turns_a_storage_failure_into_ok(fake):
    store = make_store(fake, cfg={"max_attempts": 1})
    h, _ = put(store, b"D" * 100)
    fake.faults += [{"op": "list", "status": 500}]
    with pytest.raises(ChunkStoreError):
        store.check_many([(h, 100)])
    store.close()
