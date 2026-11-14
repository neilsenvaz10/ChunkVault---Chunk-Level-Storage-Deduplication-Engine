"""Password files: exclusive creation, owner-only permissions BEFORE the secret is written, never overwritten, never printed, Git-ignored."""

import os
import subprocess
import sys

import pytest

import app.core.secretfile as sf
from app.core.secretfile import SecretFileError, write_new_secret_file

ROOT = sf.Path(__file__).resolve().parents[2]


def test_creates_a_new_file_with_the_secret_and_never_overwrites(tmp_path):
    p = tmp_path / "pw"
    write_new_secret_file(p, "s3cret\n")
    assert p.read_text(encoding="utf-8") == "s3cret\n"
    with pytest.raises(FileExistsError):
        write_new_secret_file(p, "other\n")
    assert p.read_text(encoding="utf-8") == "s3cret\n"  # untouched


def test_permissions_are_restricted_to_the_current_user(tmp_path):
    p = write_new_secret_file(tmp_path / "pw", "s3cret\n")
    if os.name == "nt":
        listing = subprocess.run(["icacls", str(p)], capture_output=True, text=True).stdout
        entries = [ln for ln in listing.splitlines() if ":(" in ln]
        assert len(entries) == 1 and os.environ["USERNAME"].lower() in entries[0].lower()  # nobody else, nothing inherited
        acl = " ".join(entries).replace(str(p), "").lower()  # the ACL entries only (not the path, which may contain "Users")
        for broad in ("everyone", "builtin", "authenticated users", "nt authority"):
            assert broad not in acl
    else:
        assert (p.stat().st_mode & 0o777) == 0o600


def test_the_secret_is_written_only_after_the_restriction_is_in_place(tmp_path, monkeypatch):
    seen = []
    real = sf._restrict_windows if os.name == "nt" else sf._restrict_posix

    def spy(path):
        seen.append(path.read_text(encoding="utf-8"))  # at this moment the file must still be empty
        real(path)

    monkeypatch.setattr(sf, "_restrict_windows" if os.name == "nt" else "_restrict_posix", spy)
    write_new_secret_file(tmp_path / "pw", "s3cret\n")
    assert seen == [""]


def test_a_failed_restriction_leaves_no_file_and_no_secret(tmp_path, monkeypatch):
    def boom(path):
        raise SecretFileError("cannot restrict")

    monkeypatch.setattr(sf, "_restrict_windows" if os.name == "nt" else "_restrict_posix", boom)
    with pytest.raises(SecretFileError):
        write_new_secret_file(tmp_path / "pw", "s3cret\n")
    assert not (tmp_path / "pw").exists()


def test_the_default_password_file_names_are_ignored_by_git():
    for name in ("backend/.app_db_password", "backend/.app_db_password.txt", ".app_db_password"):
        r = subprocess.run(["git", "check-ignore", "-q", name], cwd=ROOT)
        assert r.returncode == 0, name


def run_script(args, env_extra):
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "OWNER_DATABASE_URL")}
    env.update(CHUNKVAULT_NO_DOTENV="1", **env_extra)
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "setup_app_role.py"), *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)


def test_a_failed_setup_removes_the_generated_password_file_and_prints_no_connection_details(tmp_path):
    pwfile = tmp_path / "pw"
    r = run_script(["--role", "cv_x", "--write-password-file", str(pwfile)], {"OWNER_DATABASE_URL": "postgresql://someone:SuperSecretPw1@127.0.0.1:1/none?connect_timeout=2"})
    assert r.returncode == 1 and not pwfile.exists()
    out = r.stdout + r.stderr
    assert "SuperSecretPw1" not in out and "someone" not in out and "127.0.0.1" not in out and "removed" in out
