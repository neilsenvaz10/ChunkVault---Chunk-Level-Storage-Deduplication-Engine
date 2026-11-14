"""The backend/.env loader: precedence, parsing, secrecy, opt-out, and wiring into the entry points."""

import subprocess
import sys

import pytest

from app.core.envfile import BACKEND_DIR, ENV_FILE, load_env_file, parse_env_text
from app.core.errors import ConfigurationError

SECRET = "sup3r-s3cret-VALUE-#with-hash"


def test_the_loader_reads_only_backend_dot_env():
    assert ENV_FILE == BACKEND_DIR / ".env" and BACKEND_DIR.name == "backend"


def test_file_fills_gaps_and_the_operating_system_always_wins(tmp_path):
    f = tmp_path / ".env"
    f.write_text(f"A=from-file\nB=from-file\nexport C = 'quoted'\nD=\"dq\"\n# comment\n\nE={SECRET}\n", encoding="utf-8-sig")
    env = {"B": "from-os", "EMPTY_BUT_SET": ""}
    f.write_text(f.read_text() + "EMPTY_BUT_SET=file\n")
    loaded = load_env_file(f, env)
    assert env["A"] == "from-file" and env["B"] == "from-os" and env["EMPTY_BUT_SET"] == ""
    assert (env["C"], env["D"], env["E"]) == ("quoted", "dq", SECRET)  # '#' inside a value is preserved
    assert sorted(loaded) == ["A", "C", "D", "E"]  # names only; B and EMPTY_BUT_SET were already set


def test_missing_file_is_fine_and_the_opt_out_disables_loading(tmp_path):
    assert load_env_file(tmp_path / "nope.env", {}) == []
    f = tmp_path / ".env"
    f.write_text("A=1\n")
    env = {"CHUNKVAULT_NO_DOTENV": "1"}
    assert load_env_file(f, env) == [] and "A" not in env


def test_malformed_lines_are_reported_by_number_without_quoting_content(tmp_path):
    f = tmp_path / ".env"
    f.write_text(f"OK=1\nthis is not valid {SECRET}\n")
    with pytest.raises(ConfigurationError) as e:
        load_env_file(f, {})
    assert "line 2" in str(e.value) and SECRET not in str(e.value) and "not valid" not in str(e.value)
    with pytest.raises(ConfigurationError):
        parse_env_text("1BAD=x")


def test_loading_never_returns_or_prints_values(tmp_path, capsys):
    f = tmp_path / ".env"
    f.write_text(f"TOKEN={SECRET}\n")
    out = load_env_file(f, {})
    captured = capsys.readouterr()
    assert out == ["TOKEN"] and SECRET not in captured.out + captured.err and SECRET not in repr(out)


def test_create_app_loads_the_file_only_when_no_settings_are_passed(tmp_path, monkeypatch):
    import app.main as main

    calls = []
    monkeypatch.setattr(main, "load_env_file", lambda *a, **k: calls.append(1) or [])
    from app.core.config import Settings
    main.create_app(Settings(data_dir=tmp_path, db_path=tmp_path / "x.db", cors_origins=("http://localhost:5173",)))
    assert calls == []  # explicit settings: deterministic, no file access
    monkeypatch.setenv("CHUNKVAULT_DATA_DIR", str(tmp_path / "d"))
    main.create_app()
    assert calls == [1]


def test_alembic_and_setup_script_use_the_same_loader():
    assert "load_env_file()" in (BACKEND_DIR / "migrations" / "env.py").read_text(encoding="utf-8")
    assert "load_env_file()" in (BACKEND_DIR.parent / "scripts" / "setup_app_role.py").read_text(encoding="utf-8")


def test_a_subprocess_inherits_the_test_opt_out_so_real_credentials_are_never_used(tmp_path):
    code = ("from app.core.envfile import load_env_file; import sys; "
            "print(load_env_file(None, {'CHUNKVAULT_NO_DOTENV': '1'}))")
    r = subprocess.run([sys.executable, "-c", code], cwd=BACKEND_DIR, capture_output=True, text=True, timeout=60)
    assert r.stdout.strip() == "[]"
