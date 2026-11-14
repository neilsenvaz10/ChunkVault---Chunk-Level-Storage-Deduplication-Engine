"""Minimal ``backend/.env`` loader for local development.

Rules (deliberately small):
* Only ``backend/.env`` is read (never a path taken from the environment or a request).
* A variable that already exists in the operating-system environment ALWAYS wins; the file only fills the gaps.
* Values are never printed, logged, returned or put into error messages. Parse errors name the line number only.
* Format: ``KEY=value`` per line, ``#`` comment lines, optional ``export `` prefix, optional matching single/double quotes
  around the value. Inline ``# comments`` are NOT stripped (a ``#`` can be part of a URL or password).
* ``CHUNKVAULT_NO_DOTENV=1`` disables loading (the tests set it so a developer's real credentials are never picked up).

Called explicitly by the entry points (the FastAPI app factory, Alembic's ``env.py`` and the setup scripts), never at import
time and never from ``Settings.from_env`` -- code that passes its own mapping stays fully deterministic.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from app.core.errors import ConfigurationError

BACKEND_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BACKEND_DIR / ".env"
DISABLE_VARIABLE = "CHUNKVAULT_NO_DOTENV"
_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_env_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        key, sep, value = stripped.partition("=")
        key = key.strip()
        if not sep or not _KEY.match(key):
            raise ConfigurationError(f"backend/.env line {number} is not KEY=value")  # the content is never quoted
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def load_env_file(path: Path | None = None, environ=None) -> list[str]:
    """Fill missing variables from the file. Returns the NAMES that were set (never values). No file = nothing to do."""
    environ = os.environ if environ is None else environ
    if environ.get(DISABLE_VARIABLE) == "1":
        return []
    path = ENV_FILE if path is None else Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return []
    except OSError:
        raise ConfigurationError("backend/.env exists but cannot be read") from None
    loaded = []
    for key, value in parse_env_text(text).items():
        if key not in environ:  # the operating system's value always takes precedence
            environ[key] = value
            loaded.append(key)
    return loaded
