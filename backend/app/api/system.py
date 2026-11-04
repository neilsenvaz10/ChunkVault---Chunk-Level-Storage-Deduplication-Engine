"""Read-only system information and a bounded chunking demonstration."""

from __future__ import annotations

import hashlib
import io
import random
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_services
from app.chunking.rabin import POLY, POLY_DEGREE
from app.core.config import KIB
from app.core.errors import InvalidParametersError
from app.core.services import Services
from app.schemas.common import ErrorResponse
from app.schemas.system import (
    CdcConfigInfo,
    ChunkingDemoResponse,
    DemoChunk,
    SchemeResult,
    SystemConfigResponse,
)

router = APIRouter(prefix="/system", tags=["system"])

MAX_DEMO_SIZE = 512 * KIB


@router.get("/config", response_model=SystemConfigResponse, summary="Chunking parameters and limits in effect")
def system_config(svc: Services = Depends(get_services)):
    c = svc.settings.cdc
    return SystemConfigResponse(
        cdc=CdcConfigInfo(
            min_chunk_size=c.min_chunk_size, avg_chunk_size=c.avg_chunk_size, max_chunk_size=c.max_chunk_size,
            window_size=c.window_size, boundary_divisor=c.boundary_divisor,
            boundary_remainder=c.boundary_divisor - 1, polynomial=hex(POLY), polynomial_degree=POLY_DEGREE),
        max_upload_bytes=svc.settings.max_upload_bytes,
        chunk_path_layout="chunks/<first 2 hex digits of SHA-256>/<SHA-256>",
    )


def _digest(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _scheme(name: str, original: list[bytes], modified: list[bytes]) -> SchemeResult:
    known = {_digest(c) for c in original}

    def place(chunks, flag):
        out, off = [], 0
        for c in chunks:
            out.append(DemoChunk(offset=off, size=len(c), reused=(_digest(c) in known) if flag else None))
            off += len(c)
        return out

    mod = place(modified, True)
    reused = [m for m in mod if m.reused]
    total = sum(m.size for m in mod)
    return SchemeResult(
        scheme=name, original_chunks=place(original, False), modified_chunks=mod, reused_chunks=len(reused),
        reused_bytes=sum(m.size for m in reused),
        reuse_percent=(100.0 * sum(m.size for m in reused) / total) if total else None)


@router.get("/chunking-demo", response_model=ChunkingDemoResponse, responses={422: {"model": ErrorResponse}},
            summary="Run the real chunker and a fixed-size chunker on seeded generated data with an insertion")
def chunking_demo(
    seed: Annotated[int, Query(ge=0, le=2**31 - 1)] = 7,
    size: Annotated[int, Query(ge=16 * KIB, le=MAX_DEMO_SIZE)] = 192 * KIB,
    insert_at: Annotated[int, Query(ge=0)] = 100,
    insert_len: Annotated[int, Query(ge=1, le=4096)] = 37,
    fixed_size: Annotated[int, Query(ge=KIB, le=128 * KIB)] = 16 * KIB,
    svc: Services = Depends(get_services),
):
    """Bounded and side-effect free: the input is generated from the seed in memory, chunked, compared and discarded."""
    if insert_at > size:
        raise InvalidParametersError("insert_at must not exceed size")
    rng = random.Random(seed)
    original = rng.randbytes(size)
    inserted = random.Random(seed + 1).randbytes(insert_len)
    modified = original[:insert_at] + inserted + original[insert_at:]

    def cdc(data: bytes) -> list[bytes]:
        return [c.data for c in svc.chunker.chunk_stream(io.BytesIO(data))]

    def fixed(data: bytes) -> list[bytes]:
        return [data[i:i + fixed_size] for i in range(0, len(data), fixed_size)]

    return ChunkingDemoResponse(
        seed=seed, original_size=len(original), modified_size=len(modified), insert_at=insert_at,
        insert_len=insert_len, fixed_chunk_size=fixed_size,
        cdc=_scheme("content-defined (Rabin-style)", cdc(original), cdc(modified)),
        fixed=_scheme(f"fixed-size ({fixed_size} B)", fixed(original), fixed(modified)),
        note="Computed on seeded generated data by the implemented chunker, not stored. "
             "Results describe this input only; CDC does not guarantee better reuse for every dataset.")

