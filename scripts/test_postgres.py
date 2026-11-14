"""Start/stop a THROW-AWAY PostgreSQL cluster for the PostgreSQL tests (no Docker, no installation).

It uses the binaries of an existing PostgreSQL installation (initdb / pg_ctl) but creates its own data directory in the
temp folder and listens on its own port (default 55432, localhost only). It never touches an installed server's data.

    python scripts/test_postgres.py start     # creates the cluster if needed, starts it, prints TEST_DATABASE_URL
    python scripts/test_postgres.py url       # prints TEST_DATABASE_URL
    python scripts/test_postgres.py stop      # stops it
    python scripts/test_postgres.py destroy   # stops it and deletes the throw-away data directory

Set PG_BIN to the folder containing initdb/pg_ctl if it is not found automatically.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PORT = int(os.environ.get("CHUNKVAULT_TEST_PG_PORT", "55432"))
DATA = Path(os.environ.get("CHUNKVAULT_TEST_PG_DIR", Path(tempfile.gettempdir()) / "chunkvault-test-pg"))
URL = f"postgresql://postgres@127.0.0.1:{PORT}/postgres"


def pg_bin() -> Path:
    candidates = [os.environ.get("PG_BIN", "")]
    candidates += sorted(glob.glob(r"C:\Program Files\PostgreSQL\*\bin"), reverse=True)
    candidates += ["/usr/lib/postgresql/16/bin", "/usr/lib/postgresql/15/bin", "/usr/local/bin", "/opt/homebrew/bin"]
    for c in candidates:
        if c and (Path(c) / ("pg_ctl.exe" if os.name == "nt" else "pg_ctl")).exists():
            return Path(c)
    sys.exit("PostgreSQL binaries not found; set PG_BIN to the folder containing initdb and pg_ctl")


def run(cmd, check=True):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, check=check)


def running(b: Path) -> bool:
    return run([b / "pg_ctl", "status", "-D", DATA], check=False).returncode == 0


def start() -> None:
    b = pg_bin()
    if not (DATA / "PG_VERSION").exists():
        run([b / "initdb", "-D", DATA, "-U", "postgres", "--auth=trust", "--encoding=UTF8", "--locale=C"])
    if not running(b):
        # The server inherits pg_ctl's output handles: send them to a file, never to a pipe we would wait on.
        with open(DATA / "pg_ctl.out", "wb") as out:
            subprocess.run([str(b / "pg_ctl"), "start", "-D", str(DATA), "-w", "-t", "60", "-l", str(DATA / "server.log"),
                            "-o", f"-p {PORT} -c listen_addresses=127.0.0.1 -c fsync=off -c synchronous_commit=off -c full_page_writes=off"],
                           stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, check=True)
    for _ in range(30):
        if run([b / "pg_isready", "-h", "127.0.0.1", "-p", str(PORT)], check=False).returncode == 0:
            break
        time.sleep(0.5)
    print(URL)


def stop() -> None:
    b = pg_bin()
    if (DATA / "PG_VERSION").exists() and running(b):
        run([b / "pg_ctl", "stop", "-D", DATA, "-m", "fast", "-w"])


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "url"
    if cmd == "start":
        start()
    elif cmd == "url":
        print(URL)
    elif cmd == "stop":
        stop()
    elif cmd == "destroy":
        stop()
        shutil.rmtree(DATA, ignore_errors=True)
    else:
        sys.exit(__doc__)
