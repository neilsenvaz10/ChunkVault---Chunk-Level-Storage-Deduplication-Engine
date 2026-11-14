"""Create/refresh the dedicated least-privilege application role (decision E3) in the ChunkVault database.

Run by hand with the OWNER connection, after `alembic upgrade head`.

Connection: ``OWNER_DATABASE_URL`` if set, otherwise ``DATABASE_URL`` (backend/.env is loaded by the approved loader; variables of the
operating system win). Keep the owner URL out of the application's environment: the running app uses only ``DATABASE_URL``.

Password (never printed, never on the command line, never sent to the server in cleartext -- only as a SCRAM-SHA-256 verifier):
  * ``--write-password-file PATH``  generates a strong random password, writes it to PATH (a NEW file, refused if it exists) and uses it;
  * otherwise it is read from the environment variable named by ``--password-env`` (default APP_DB_PASSWORD);
  * on a rerun without either, the role's password is left unchanged.

    python scripts/setup_app_role.py --role chunkvault_app --schema public --write-password-file backend/.app_db_password

Then use a connection string for that role (same pooler host; on Supabase's pooler the user is `chunkvault_app.<project-ref>`) as the
DATABASE_URL of the web service, and delete the password file.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import psycopg  # noqa: E402

from app.core.envfile import load_env_file  # noqa: E402
from app.core.secretfile import SecretFileError, write_new_secret_file  # noqa: E402
from app.database.pg_admin import setup_app_role  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--role", default="chunkvault_app")
    ap.add_argument("--schema", default="public")
    ap.add_argument("--password-env", default="APP_DB_PASSWORD", help="name of the environment variable holding the password")
    ap.add_argument("--write-password-file", metavar="PATH", help="generate a random password and write it to this NEW file (mode 0600 where supported)")
    args = ap.parse_args()
    load_env_file()  # backend/.env fills only variables the operating system has not set
    url = os.environ.get("OWNER_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        print("OWNER_DATABASE_URL (or DATABASE_URL) with the owner connection is required", file=sys.stderr)
        return 2
    created_file = None
    if args.write_password_file:
        password = secrets.token_urlsafe(32)  # URL-safe alphabet: usable in a connection string without encoding
        try:
            created_file = write_new_secret_file(args.write_password_file, password + "\n")  # exclusive, restricted BEFORE the secret is written
        except FileExistsError:
            print("refusing to overwrite an existing password file", file=sys.stderr)
            return 2
        except SecretFileError as exc:
            print(f"password file not created: {exc}", file=sys.stderr)
            return 2
    else:
        password = os.environ.get(args.password_env) or None
    try:
        with psycopg.connect(url, autocommit=False) as conn:
            setup_app_role(conn, args.role, args.schema, password)
            conn.commit()  # all or nothing: CREATE ROLE, grants and policies are one transaction
    except BaseException as exc:
        if created_file is not None:
            created_file.unlink(missing_ok=True)  # the generated password was never (or no longer reliably) in effect
            print("setup failed: the generated password file was removed; fix the cause and run again (a rerun is safe)", file=sys.stderr)
        print(f"setup failed: {type(exc).__name__}", file=sys.stderr)  # the class only: driver messages can name hosts and users
        return 1
    print(f"role {args.role!r} is ready on schema {args.schema!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
