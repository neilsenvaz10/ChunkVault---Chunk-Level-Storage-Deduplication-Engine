"""The Database port: SQLite implementation behaviour and the factory's refusal to fall back silently."""

import threading

import pytest

from app.core.config import CloudSettings, Settings
from app.core.errors import ConfigurationError
from app.database.factory import build_database
from app.database.sqlite import SqliteDatabase

H = "a" * 64


@pytest.fixture
def sdb(settings):
    db = build_database(settings)
    db.prepare_for_startup()
    return db


def test_factory_builds_sqlite_for_local(settings):
    assert isinstance(build_database(settings), SqliteDatabase)


def _cloud_settings(tmp_path):
    c = CloudSettings(database_url="postgresql://u:p@h.example:5432/db", supabase_url="https://x.supabase.co",
                      supabase_service_key="k" * 40, demo_token="d" * 40, admin_token="a" * 40)
    return Settings(data_dir=tmp_path, db_path=tmp_path / "x.db", cors_origins=("https://a.example",), backend="cloud", cloud=c)


def test_factory_builds_postgres_for_cloud_without_connecting(tmp_path):
    from app.database.postgres import PostgresDatabase
    from app.database.postgres_repository import PostgresMetadataRepository
    from app.database.factory import build_repository

    s = _cloud_settings(tmp_path)
    assert isinstance(build_database(s), PostgresDatabase)  # constructing never touches the network (host does not exist)
    assert isinstance(build_repository(s), PostgresMetadataRepository)
    assert not (tmp_path / "x.db").exists()  # nothing local was created


def test_cloud_services_pair_postgres_with_supabase_storage_and_never_mix(tmp_path):
    from app.core.services import build_services
    from app.database.postgres import PostgresDatabase
    from app.database.sqlite import SqliteDatabase
    from app.storage.filesystem import FilesystemChunkStore
    from app.storage.supabase import SupabaseChunkStore

    cloud = build_services(_cloud_settings(tmp_path))  # constructing never touches the network
    try:
        assert isinstance(cloud.db, PostgresDatabase) and isinstance(cloud.store, SupabaseChunkStore) and cloud.gc is not None
        assert cloud.store.remote and not cloud.store.deletes_on_failed_upload
        assert not (tmp_path / "x.db").exists() and not (tmp_path / "chunks").exists()  # no local chunk or metadata persistence
    finally:
        cloud.store.close()
        cloud.db.close()
    local = build_services(Settings(data_dir=tmp_path / "l", db_path=tmp_path / "l" / "l.db", cors_origins=("http://localhost:5173",)))
    assert isinstance(local.db, SqliteDatabase) and isinstance(local.store, FilesystemChunkStore) and local.gc is None


def test_prepare_for_startup_creates_directories_and_schema(settings, sdb):
    assert settings.chunks_dir.is_dir() and settings.db_path.is_file()
    assert sdb.schema_version() == str(sdb.expected_schema_version())


def test_ping_reports_health(sdb, settings):
    h = sdb.ping()
    assert h.connected and h.foreign_keys_enforced and h.schema_version == str(sdb.expected_schema_version())
    settings.db_path.unlink()
    settings.db_path.write_bytes(b"not a database" * 100)
    assert sdb.ping().connected is False


def test_transaction_commits_and_rolls_back(sdb):
    with sdb.connection() as c:
        with sdb.transaction(c):
            c.execute("INSERT INTO files(file_id, filename, original_size) VALUES ('f1','a',1)")
        with pytest.raises(RuntimeError):
            with sdb.transaction(c):
                c.execute("INSERT INTO files(file_id, filename, original_size) VALUES ('f2','a',1)")
                raise RuntimeError("boom")
    with sdb.connection() as c:
        assert [r[0] for r in c.execute("SELECT file_id FROM files")] == ["f1"]


def test_snapshot_is_read_only_and_consistent(sdb):
    with sdb.connection() as c:
        with sdb.snapshot(c):
            with pytest.raises(Exception):
                c.execute("INSERT INTO files(file_id, filename, original_size) VALUES ('f','a',1)")
            assert c.execute("SELECT count(*) FROM files").fetchone()[0] == 0
        with sdb.transaction(c):  # writes work again afterwards
            c.execute("INSERT INTO files(file_id, filename, original_size) VALUES ('f','a',1)")


def test_snapshot_does_not_see_a_concurrent_commit(sdb):
    with sdb.connection() as reader, sdb.connection() as writer:
        with sdb.snapshot(reader):
            assert reader.execute("SELECT count(*) FROM files").fetchone()[0] == 0
            with sdb.transaction(writer):
                writer.execute("INSERT INTO files(file_id, filename, original_size) VALUES ('f','a',1)")
            assert reader.execute("SELECT count(*) FROM files").fetchone()[0] == 0
        assert reader.execute("SELECT count(*) FROM files").fetchone()[0] == 1


def test_writer_lock_is_exclusive_across_threads(sdb):
    from app.core.locking import WriterBusyError as LockTimeoutError  # noqa: PLC0415
    got = []

    def other():
        try:
            with sdb.writer_lock(0.2):
                got.append("acquired")
        except LockTimeoutError:
            got.append("timeout")

    with sdb.writer_lock(1):
        t = threading.Thread(target=other)
        t.start()
        t.join()
    assert got == ["timeout"]
    with sdb.writer_lock(1):
        pass


def test_introspection_reports_tables_columns_and_foreign_keys(sdb):
    tables = {t.name: t for t in sdb.introspect()}
    assert set(tables) == {"files", "chunks", "file_chunks", "operations"}
    fc = {c.name: c for c in tables["file_chunks"].columns}
    assert fc["file_id"].foreign_key.table == "files" and fc["file_id"].foreign_key.on_delete.upper() == "CASCADE"
    assert fc["chunk_hash"].foreign_key.on_delete.upper() == "RESTRICT"
    assert {c.name for c in tables["files"].columns} >= {"file_id", "sha256", "status"}


def test_footprint_counts_the_database_file(sdb, settings):
    assert sdb.footprint_bytes() >= settings.db_path.stat().st_size > 0
