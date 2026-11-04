# ADR-0004: Local filesystem instead of object storage

**Status:** accepted · **Phase:** 1

**Decision.** Store chunk bytes as files at `data/chunks/ab/cd/<sha256>`, written via
`data/staging/` and an atomic same-volume rename. Two-level fan-out avoids huge directories.

**Consequences.** + No external service, easy to inspect and corrupt-test, rename gives atomic
publication. − One disk, no replication; one file per chunk means many small files and per-file
filesystem overhead. Durability across power loss relies on `fsync` before rename.
