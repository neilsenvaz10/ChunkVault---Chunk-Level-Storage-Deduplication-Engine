"""Documentation must not drift from the running system (Phase 6).

Checks documented API routes against the generated OpenAPI schema, the schema
document against real table columns, and the CDC specification against the
configured parameters. Failures name the stale statement.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from app.core.config import CDCConfig

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"


def _text(rel: str) -> str:
    return (DOCS / rel).read_text(encoding="utf-8")


def _openapi_pairs(client) -> set[tuple[str, str]]:
    spec = client.get("/api/openapi.json").json()
    return {
        (m.upper(), p)
        for p, ops in spec["paths"].items()
        for m in ops
        if m in {"get", "post", "delete", "put", "patch"}
    }


def _norm(path: str) -> str:
    # ignore parameter names: /api/files/{id} == /api/files/{file_id}
    return re.sub(r"\{[^}]+\}", "{}", path)


def test_documented_endpoints_exist(client):
    served = {(m, _norm(p)) for m, p in _openapi_pairs(client)}
    documented = set()
    for line in _text("api/planned-contracts.md").splitlines():
        m = re.match(r"\|\s*(GET|POST|DELETE)\s*\|\s*`(/api/[^`?]*)", line)
        if m:
            documented.add((m.group(1), _norm(m.group(2))))
    assert documented, "no endpoints parsed from the API contract"
    missing = sorted(documented - served)
    assert not missing, f"documented but not served: {missing}"


def test_every_served_endpoint_is_documented(client):
    served = {(m, _norm(p)) for m, p in _openapi_pairs(client)}
    documented = set()
    for line in _text("api/planned-contracts.md").splitlines():
        m = re.match(r"\|\s*(GET|POST|DELETE)\s*\|\s*`(/api/[^`?]*)", line)
        if m:
            documented.add((m.group(1), _norm(m.group(2))))
    ignorable = {p for p in served if p[1].startswith("/api/docs") or p[1].startswith("/api/openapi")}
    undocumented = sorted(served - documented - ignorable)
    assert not undocumented, f"served but undocumented: {undocumented}"


def test_sequence_and_flow_docs_use_real_routes(client):
    served = {_norm(p) for _, p in _openapi_pairs(client)}
    for rel in ("architecture/sequences.md", "architecture/data-flow.md", "architecture/hld.md", "architecture/lld.md"):
        for m in re.finditer(r"(?:GET|POST|DELETE)\s+(/api/[A-Za-z0-9_/{}-]+)", _text(rel)):
            assert _norm(m.group(1)) in served, f"{rel} mentions unknown route {m.group(1)}"


def test_schema_doc_lists_real_columns(tmp_path):
    from app.database.connection import connect  # type: ignore
    schema_sql = (ROOT / "backend" / "app" / "database" / "schema.sql").read_text(encoding="utf-8")
    db = sqlite3.connect(":memory:")
    db.executescript(schema_sql)
    doc = _text("database/schema.md")
    for table in ("files", "chunks", "file_chunks", "operations"):
        cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
        assert cols, table
        for col in cols:
            assert re.search(r"(?<![A-Za-z_])" + col + r"(?![A-Za-z_])", doc), f"database/schema.md does not mention {table}.{col}"
    assert connect is not None


def test_cdc_spec_matches_configuration():
    cfg = CDCConfig()
    spec = _text("architecture/cdc-specification.md")
    assert hex(0x3DA3358B4DC173)[2:].upper() in spec.upper()
    digits = re.sub(r"[\s  ,]", "", spec)
    assert str(cfg.boundary_divisor) in digits
    assert str(cfg.window_size) in digits
    assert (cfg.min_chunk_size, cfg.avg_chunk_size, cfg.max_chunk_size) == (4096, 16384, 131072)


@pytest.mark.parametrize("adr", sorted((DOCS / "decisions").glob("0*.md")), ids=lambda p: p.name)
def test_adrs_have_required_sections(adr):
    text = adr.read_text(encoding="utf-8")
    for section in ("Problem", "Alternatives considered", "Decision", "Consequences"):
        assert f"## {section}" in text, f"{adr.name} lacks '## {section}'"


def test_no_stale_phase_markers():
    stale = re.compile(r"not implemented in Phase 1|interface[- ]only|\(Phase [67]\)", re.I)
    offenders = []
    for path in list(DOCS.rglob("*.md")) + [ROOT / "README.md"]:
        if "verification" in path.parts:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if stale.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{n}")
    assert not offenders, offenders


def test_every_document_is_reachable_from_the_docs_page():
    page = (ROOT / "frontend" / "src" / "pages" / "DocsPage.jsx").read_text(encoding="utf-8")
    listed = set(re.findall(r"'([a-z0-9/_.-]+\.md)'\]", page))
    present = {p.relative_to(DOCS).as_posix() for p in DOCS.rglob("*.md")}
    assert present - listed == set(), f"not linked from the Documentation page: {sorted(present - listed)}"
    assert listed - present == set(), f"linked but missing: {sorted(listed - present)}"
