"""Alembic environment: raw-SQL migrations against PostgreSQL, run manually.

* The URL comes from OWNER_DATABASE_URL, else DATABASE_URL. It is never printed or logged here.
* ``CHUNKVAULT_PG_SCHEMA`` (optional) runs the migrations in that schema, including its ``alembic_version`` table; the tests use
  throw-away schemas this way. Without it the connection's default search_path is used.
* There is no offline (SQL-script) mode: the migrations are SQL files executed as written.
"""

from __future__ import annotations

import os
import re
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

# Same rule as the application: backend/.env fills only variables the operating system has not set.
from app.core.envfile import load_env_file  # noqa: E402

load_env_file()

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

_SCHEMA = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _database_url() -> str:
    url = os.environ.get("OWNER_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("OWNER_DATABASE_URL (or DATABASE_URL) is required: use the owner connection; never commit it.")
    return re.sub(r"^postgres(ql)?://", "postgresql+psycopg://", url)


def run_migrations_online() -> None:
    schema = os.environ.get("CHUNKVAULT_PG_SCHEMA") or None
    if schema is not None and not _SCHEMA.match(schema):
        raise SystemExit("CHUNKVAULT_PG_SCHEMA must be a plain lowercase identifier.")
    connect_args = {"options": f"-c search_path={schema}"} if schema else {}
    engine = create_engine(_database_url(), poolclass=pool.NullPool, connect_args=connect_args)
    with engine.connect() as connection:
        if schema:
            connection.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            connection.commit()
        context.configure(connection=connection, target_metadata=None, version_table_schema=schema,
                          transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("Offline mode is not supported: the migrations are SQL files run as written.")
run_migrations_online()
