"""Write a secret to a NEW file that only the current user can read, without the secret ever being visible to anyone else.

Order matters: the file is created EMPTY and exclusively (an existing file is never overwritten), its permissions are restricted,
the restriction is verified, and only then is the secret written. If any step fails the file is removed and the error propagates, so a
secret never sits in a file that other accounts could read. The caller never prints the secret.

POSIX: mode 0600. Windows: the inherited ACL is removed and a single full-control entry for the current user remains (``icacls``);
the resulting ACL is read back and must name nobody else.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


class SecretFileError(RuntimeError):
    """The file could not be created with restrictive permissions (the secret was not written)."""


def _restrict_windows(path: Path) -> None:
    user = os.environ.get("USERNAME") or ""
    if not user:
        raise SecretFileError("cannot determine the current Windows user")
    domain = os.environ.get("USERDOMAIN")
    principal = f"{domain}\\{user}" if domain else user
    r = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{principal}:(F)"], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise SecretFileError("could not restrict the file's permissions (icacls failed)")
    listing = subprocess.run(["icacls", str(path)], capture_output=True, text=True, timeout=30).stdout
    entries = [ln.strip() for ln in listing.splitlines() if ":(" in ln]
    if len(entries) != 1 or user.lower() not in entries[0].lower():
        raise SecretFileError("the file's permissions are not restricted to the current user")


def _restrict_posix(path: Path) -> None:
    os.chmod(path, 0o600)
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise SecretFileError("the file's permissions are not 0600")


def write_new_secret_file(path: str | os.PathLike, text: str) -> Path:
    p = Path(path)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # FileExistsError if it exists: never overwritten
    os.close(fd)
    try:
        (_restrict_windows if os.name == "nt" else _restrict_posix)(p)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    except BaseException:
        try:
            p.unlink()
        except OSError:
            pass
        raise
    return p
