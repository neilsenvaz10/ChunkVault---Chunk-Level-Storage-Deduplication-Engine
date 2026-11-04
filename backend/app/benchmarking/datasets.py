"""Deterministic, seeded benchmark datasets. No external data is needed; the same (workload,
size, seed) always yields byte-identical files."""

from __future__ import annotations

import random

EDIT_LEN = 61  # bytes inserted / deleted / modified
REPEAT_BLOCK = 20_000


def random_bytes(seed: int, size: int) -> bytes:
    return random.Random(seed).randbytes(size)


def repeated_bytes(seed: int, size: int, block: int = REPEAT_BLOCK) -> bytes:
    unit = random_bytes(seed, min(block, size))
    return (unit * (size // len(unit) + 1))[:size]


def insert_near_start(data: bytes, seed: int, at: int = 100, n: int = EDIT_LEN) -> bytes:
    return data[:at] + random_bytes(seed + 1_000_003, n) + data[at:]


def delete_near_start(data: bytes, at: int = 100, n: int = EDIT_LEN) -> bytes:
    return data[:at] + data[at + n:]


def modify_middle(data: bytes, n: int = EDIT_LEN) -> bytes:
    mid = len(data) // 2
    return data[:mid] + bytes(b ^ 0xFF for b in data[mid:mid + n]) + data[mid + n:]


def workload_versions(workload: str, size: int, seed: int) -> list[tuple[str, bytes]]:
    """The files a pipeline workload uploads, in order."""
    if workload in ("small", "medium", "large", "random"):
        return [("v1", random_bytes(seed, size))]
    if workload == "repeated_content":
        return [("v1", repeated_bytes(seed, size))]
    base = random_bytes(seed, size)
    if workload == "identical":
        return [("v1", base), ("v2-identical", base)]
    if workload == "partial_modified":
        return [("v1", base), ("v2-modified", modify_middle(base))]
    if workload == "inserted_bytes":
        return [("v1", base), ("v2-inserted", insert_near_start(base, seed))]
    if workload == "deleted_bytes":
        return [("v1", base), ("v2-deleted", delete_near_start(base))]
    raise ValueError(f"unknown workload {workload}")


def comparison_cases(size: int, seed: int) -> list[dict]:
    """The seven CDC-vs-fixed cases, built from the same seeded data for both chunkers."""
    base = random_bytes(seed, size)
    rep = repeated_bytes(seed + 17, size)
    other = random_bytes(seed + 29, size)
    return [
        {"id": "original", "title": "Original file", "versions": [base],
         "note": "A single random file: nothing to deduplicate against."},
        {"id": "identical_duplicate", "title": "Identical duplicate", "versions": [base, base],
         "note": "The same bytes uploaded twice."},
        {"id": "insert_start", "title": "Small insertion near the beginning", "versions": [base, insert_near_start(base, seed)],
         "note": f"{EDIT_LEN} bytes inserted at offset 100: every later byte shifts."},
        {"id": "delete_start", "title": "Small deletion near the beginning", "versions": [base, delete_near_start(base)],
         "note": f"{EDIT_LEN} bytes removed at offset 100: every later byte shifts."},
        {"id": "modify_middle", "title": "Modified content in the middle", "versions": [base, modify_middle(base)],
         "note": f"{EDIT_LEN} bytes changed in place: nothing shifts, so this is NOT a case CDC is expected to win."},
        {"id": "repeated_content", "title": "Repeated-content dataset", "versions": [rep, insert_near_start(rep, seed)],
         "note": f"A {REPEAT_BLOCK}-byte block repeated, then the same with an insertion near the start."},
        {"id": "random_content", "title": "Random-content dataset", "versions": [base, other],
         "note": "Two unrelated random files: no scheme can find shared content."},
    ]
