"""Secrets must never be reachable from the browser bundle: no VITE_* variable may carry one, and no frontend source may name them."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
FORBIDDEN = re.compile(r"SERVICE_KEY|ADMIN_TOKEN|DATABASE_URL|SERVICE_ROLE|service_role|SUPABASE_SERVICE", re.I)
VITE_SECRETISH = re.compile(r"VITE_[A-Z0-9_]*(SECRET|TOKEN|KEY|PASSWORD|DATABASE|SERVICE)", re.I)


def _sources():
    for base in (FRONTEND / "src", FRONTEND / "index.html", FRONTEND / "vite.config.js"):
        if base.is_file():
            yield base
        elif base.is_dir():
            yield from (p for p in base.rglob("*") if p.is_file() and p.suffix in {".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".json"})


def test_frontend_sources_never_reference_backend_secrets():
    hits = [str(p.relative_to(ROOT)) for p in _sources() if FORBIDDEN.search(p.read_text(encoding="utf-8", errors="ignore"))]
    assert hits == []


def test_no_secret_looking_vite_variables_anywhere():
    files = list(_sources()) + [p for p in FRONTEND.glob(".env*")] + [ROOT / ".env.example"]
    hits = [str(p.relative_to(ROOT)) for p in files if p.is_file() and VITE_SECRETISH.search(p.read_text(encoding="utf-8", errors="ignore"))]
    assert hits == []
