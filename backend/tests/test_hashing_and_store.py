import hashlib
import io
import os

import pytest

from app.hashing.interfaces import InvalidDigestError
from app.hashing.sha256 import Sha256Service, is_valid_digest, validate_digest
from app.storage.filesystem import ChunkStatus, FilesystemChunkStore
from app.storage.interfaces import ChunkCorruptedError, ChunkNotFoundError, ChunkWriteError

H = Sha256Service()
ABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
EMPTY = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_known_vectors():
    assert H.digest(b"abc") == ABC
    assert H.digest(b"") == EMPTY


def test_incremental_equals_one_shot():
    h = H.new_hasher()
    h.update(b"a")
    h.update(b"bc")
    assert h.hexdigest() == ABC


def test_digest_stream_and_verify():
    assert H.digest_stream(io.BytesIO(b"abc")) == (ABC, 3)
    assert H.verify(b"abc", ABC) and not H.verify(b"abd", ABC)


@pytest.mark.parametrize("bad", ["", "../etc/passwd", ABC.upper(), ABC[:-1], ABC + "0", None, 5])
def test_invalid_digests_rejected(bad):
    assert not is_valid_digest(bad)
    with pytest.raises(InvalidDigestError):
        validate_digest(bad)
    if isinstance(bad, str):
        with pytest.raises(InvalidDigestError):
            H.verify(b"abc", bad)


@pytest.fixture
def store(tmp_path):
    return FilesystemChunkStore(tmp_path / "chunks", tmp_path / "staging")


def put(store, data):
    h = H.digest(data)
    return h, store.publish(store.stage(h, data))


def test_publish_creates_content_addressed_file(store):
    h, created = put(store, b"abc")
    assert created
    assert store.path_for(h) == store.chunks_dir / ABC[:2] / ABC
    assert store.read(h) == b"abc" and store.exists(h)
    assert list(store.iter_hashes()) == [h]
    assert list(store.iter_staged()) == []


def test_duplicate_publish_stores_once(store):
    put(store, b"abc")
    h, created = put(store, b"abc")
    assert not created
    assert len(list(store.iter_hashes())) == 1 and list(store.iter_staged()) == []


def test_existing_corrupt_chunk_is_not_overwritten(store):
    h, _ = put(store, b"abc")
    store.path_for(h).write_bytes(b"EVIL")
    staged = store.stage(h, b"abc")
    with pytest.raises(ChunkCorruptedError):
        store.publish(staged)
    assert store.path_for(h).read_bytes() == b"EVIL"  # untouched
    assert store.verify(h) is ChunkStatus.CORRUPT


def test_verify_statuses(store):
    h, _ = put(store, b"abc")
    assert store.verify(h) is ChunkStatus.OK
    assert store.verify(h, expected_size=2) is ChunkStatus.CORRUPT
    store.path_for(h).unlink()
    assert store.verify(h) is ChunkStatus.MISSING
    with pytest.raises(ChunkNotFoundError):
        store.read(h)


@pytest.mark.parametrize("bad", ["../../x", "a" * 63, "g" * 64, ABC.upper(), "..\\..\\x" + "a" * 60])
def test_path_traversal_and_bad_ids_rejected(store, bad):
    for op in (store.path_for, store.exists, store.read, store.delete, store.verify):
        with pytest.raises(InvalidDigestError):
            op(bad)
    with pytest.raises(InvalidDigestError):
        store.stage(bad, b"x")


def test_stage_rejects_bytes_not_matching_digest(store):
    with pytest.raises(ChunkWriteError):
        store.stage(ABC, b"not abc")
    assert list(store.iter_staged()) == []


def test_stage_failure_leaves_no_partial_file(store, monkeypatch):
    def boom(fd):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(ChunkWriteError):
        store.stage(ABC, b"abc")
    assert list(store.iter_staged()) == []


def test_delete_is_idempotent(store):
    h, _ = put(store, b"abc")
    assert store.delete(h) is True
    assert store.delete(h) is False


def test_iter_hashes_ignores_foreign_files(store):
    h, _ = put(store, b"abc")
    (store.chunks_dir / "zz").mkdir()
    (store.chunks_dir / "zz" / "notes.txt").write_text("x")
    (store.chunks_dir / "ff").mkdir()
    (store.chunks_dir / "ff" / ABC).write_bytes(b"abc")  # right name, wrong directory
    assert list(store.iter_hashes()) == [h]


def test_disk_usage(store):
    put(store, b"abc")
    put(store, b"hello")
    assert store.disk_usage() == (2, 8)


def test_chunks_and_staging_must_share_filesystem(tmp_path, monkeypatch):
    from app.core.errors import ConfigurationError

    real = os.stat
    calls = {"n": 0}

    class R:
        def __init__(self, r, dev):
            self._r, self.st_dev = r, dev

        def __getattr__(self, k):
            return getattr(self._r, k)

    def fake(self, *a, **k):
        calls["n"] += 1
        return R(real(self), calls["n"])

    monkeypatch.setattr("pathlib.Path.stat", fake)
    with pytest.raises(ConfigurationError):
        FilesystemChunkStore(tmp_path / "c", tmp_path / "s")
