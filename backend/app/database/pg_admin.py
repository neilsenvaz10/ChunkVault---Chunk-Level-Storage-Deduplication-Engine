"""Administrative PostgreSQL helpers: the dedicated least-privilege application role (decision E3).

Used by ``scripts/setup_app_role.py`` and by the tests; the running application never imports this module. It needs an OWNER
connection (it creates a role and grants), never the application role itself.

What the role can do: connect, use the ChunkVault schema, and SELECT/INSERT/UPDATE/DELETE on the ChunkVault tables (row-level
security passes it through with one explicit policy per table). It cannot create objects, truncate, alter triggers, own anything,
bypass row-level security, or create roles/databases. Re-run after a migration that adds tables.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re

from psycopg import Connection, sql

TABLES = ("files", "chunks", "file_chunks", "operations", "benchmark_results")
VIEWS = ("v_ref_count_mismatch",)
POLICY = "cv_app_all"
VERSION_TABLE = "alembic_version"
VERSION_POLICY = "cv_app_read"
_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _ident(value: str, what: str) -> sql.Identifier:
    if not _IDENT.match(value):
        raise ValueError(f"{what} must be a plain lowercase identifier")
    return sql.Identifier(value)


SCRAM_ITERATIONS = 4096  # PostgreSQL's default scram_iterations
# pg_roles column -> (value the application role must have, keyword that sets it)
_ROLE_ATTRIBUTES = (("rolsuper", False, "NOSUPERUSER"), ("rolcreatedb", False, "NOCREATEDB"), ("rolcreaterole", False, "NOCREATEROLE"),
                    ("rolreplication", False, "NOREPLICATION"), ("rolbypassrls", False, "NOBYPASSRLS"), ("rolcanlogin", True, "LOGIN"))


def scram_verifier(password: str, *, salt: bytes | None = None, iterations: int = SCRAM_ITERATIONS) -> str:
    """The SCRAM-SHA-256 verifier PostgreSQL stores for a password (RFC 5802/7677 as PostgreSQL implements it).

    Sending the verifier instead of the cleartext means the password never appears in the statement text, so it cannot end up in the
    server's statement logs (a failed CREATE/ALTER ROLE logs its statement) or in a monitoring tool. The server stores it unchanged."""
    salt = os.urandom(16) if salt is None else salt
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda raw: base64.b64encode(raw).decode("ascii")  # noqa: E731
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def _password_literal(password: str) -> sql.Literal:
    return sql.Literal(password if password.startswith("SCRAM-SHA-256$") else scram_verifier(password))  # never the cleartext


def setup_app_role(conn: Connection, role: str, schema: str, password: str | None = None) -> None:
    """Create (if needed) and grant the application role. Idempotent. The password is only sent if given, and only as a verifier.

    On a rerun only the attributes that DIFFER from the required ones are altered: a CREATEROLE owner that is not a superuser may not
    touch attributes it does not hold itself (REPLICATION, BYPASSRLS), so a blanket ALTER ROLE would fail needlessly."""
    r, s = _ident(role, "role"), _ident(schema, "schema")
    current = conn.execute("SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, rolcanlogin FROM pg_roles "
                           "WHERE rolname = %s", (role,)).fetchone()
    if current is None:
        if not password:
            raise ValueError("a password is required to create the role (pass it through an environment variable)")
        attrs = sql.SQL(" ").join(sql.SQL(kw) for _, _, kw in _ROLE_ATTRIBUTES)
        conn.execute(sql.SQL("CREATE ROLE {} PASSWORD {} {}").format(r, _password_literal(password), attrs))
    else:
        differing = [kw for (_, want, kw), have in zip(_ROLE_ATTRIBUTES, tuple(current)) if have != want]
        if differing:
            conn.execute(sql.SQL("ALTER ROLE {} {}").format(r, sql.SQL(" ").join(sql.SQL(kw) for kw in differing)))
        if password:
            conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(r, _password_literal(password)))
    conn.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM {}").format(s, r))
    conn.execute(sql.SQL("REVOKE CREATE ON SCHEMA {} FROM {}").format(s, r))
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(s, r))
    for t in TABLES:
        tbl = sql.SQL("{}.{}").format(s, sql.Identifier(t))
        conn.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON {} TO {}").format(tbl, r))
        conn.execute(sql.SQL("DROP POLICY IF EXISTS {} ON {}").format(sql.Identifier(POLICY), tbl))
        conn.execute(sql.SQL("CREATE POLICY {} ON {} FOR ALL TO {} USING (true) WITH CHECK (true)")
                     .format(sql.Identifier(POLICY), tbl, r))
    for v in VIEWS:
        conn.execute(sql.SQL("GRANT SELECT ON {}.{} TO {}").format(s, sql.Identifier(v), r))
    _grant_version_read(conn, s, r)


def _grant_version_read(conn: Connection, s: sql.Identifier, r: sql.Identifier) -> None:
    """Let the application role READ (never change) Alembic's one-row version table, so start-up can verify the schema version.

    Alembic creates that table as the owner, outside our migration, so it is also protected here: row-level security on, no
    privileges for the Supabase API roles.
    """
    tbl = sql.SQL("{}.{}").format(s, sql.Identifier(VERSION_TABLE))
    if conn.execute("SELECT to_regclass(%s)", (tbl.as_string(conn),)).fetchone()[0] is None:
        return  # not migrated yet; run this again after `alembic upgrade head`
    conn.execute(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(tbl))
    for api_role in ("anon", "authenticated", "service_role"):
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (api_role,)).fetchone():
            conn.execute(sql.SQL("REVOKE ALL ON {} FROM {}").format(tbl, sql.Identifier(api_role)))
    conn.execute(sql.SQL("REVOKE ALL ON {} FROM {}").format(tbl, r))
    conn.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(tbl, r))
    conn.execute(sql.SQL("DROP POLICY IF EXISTS {} ON {}").format(sql.Identifier(VERSION_POLICY), tbl))
    conn.execute(sql.SQL("CREATE POLICY {} ON {} FOR SELECT TO {} USING (true)").format(sql.Identifier(VERSION_POLICY), tbl, r))
