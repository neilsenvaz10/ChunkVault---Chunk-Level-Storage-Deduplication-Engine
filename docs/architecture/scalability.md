# Capacity Model and Scalability

**The capacity simulator is a mathematical model, not a measurement and not a load test.** Every
output is a pure function of assumptions the user states; it reads nothing from the database, the
chunk store or the benchmarks, and executes nothing. Implementation: `backend/app/simulation/model.py`
(`POST /api/simulations`); UI: *Capacity Simulation*.

## Model
Inputs: files **N**, average file size **S**, average chunks per file **C** (default `ceil(S / 16 KiB)`),
duplicate fraction **d** (share of chunk references that repeat existing content), metadata bytes per
file **Mf**, per chunk reference (manifest row) **Mr**, per unique chunk **Mc**, monthly growth **g**,
retention **T** months, horizon **H**.

```
chunk references   R = N · C
unique chunks      U = R · (1 − d)
average chunk size a = S / C
logical storage    L = N · S
physical chunks    P = U · a            (= L · (1 − d))
storage savings      = (L − P) / L      (= d)
dedup ratio          = L / P            (undefined if P = 0)
metadata           M = N·Mf + R·Mr + U·Mc
total disk           = P + M
ingested(t) = N · (1 + g)^t ;  active(t) = ingested(t) − ingested(t − T)   (T > 0)
```

### Assumptions (stated by the simulator with every result)
- `d` applies to chunk references and stays constant as data grows; average chunk size is `S / C`.
- Metadata sizes are user estimates (defaults: 300 / 150 / 80 bytes), not measurements.
- Growth is compound and monthly; retention expires whole months of the oldest files; reclamation is perfect and immediate.
- No filesystem block rounding, SQLite page slack, backups, replicas, CPU cost or lookup latency is modelled.

### Example projections (model output for the stated assumptions; **not measured**)
100,000 files × 5 MiB (logical 488.3 GiB), default metadata sizes, 24-month horizon at 5% monthly growth:

| Assumed duplicate content | Estimated physical chunks | Estimated metadata | Estimated total disk | Metadata share |
|---|---|---|---|---|
| 0% | 488.3 GiB | 6.9 GiB | 495.2 GiB | 1.4% |
| 30% (default) | 341.8 GiB | 5.5 GiB | 347.3 GiB | 1.6% |
| 90% | 48.8 GiB | 2.9 GiB | 51.7 GiB | 5.5% |
| 99.9% (1 MiB files, 100,000 files) | 0.1 GiB | 0.5 GiB | 0.6 GiB | **83.8%** |

With the default assumptions and no retention the model projects 1,120 GiB total disk after 24 months (322,510 active
files); with 12-month retention it projects 496 GiB. The 99.9% row illustrates a real structural point: at very high
duplication the manifest, not the payload, dominates storage.

## Current architectural bottlenecks (qualitative; the benchmarks measure the first and third on one machine)
| Area | Implication |
|---|---|
| Hash and CDC computation | Pure-Python chunking measured ≈ 2–4 MiB/s end to end; CPU-bound, single-threaded. |
| SQLite write concurrency | One writer at a time plus ChunkVault's own writer lock: write throughput does not scale with clients or CPUs. |
| Single-machine filesystem | One disk, no replication; capacity bounded by that disk; many small chunk files add block and inode overhead. |
| Metadata growth | One row per file, per unique chunk and per reference; grows even when payload deduplicates fully. |
| Chunk lookup | Primary-key lookup plus a disk read to verify the existing chunk (measured `dedup_read`: a few ms per chunk file on this machine); becomes disk-bound when the index no longer fits in memory. |
| Garbage collection / orphans | Reclaimed on delete; crash leftovers need an explicit reconciliation; the model assumes this is perfect. |
| Backup / disaster recovery | None implemented; one damaged shared chunk affects every file using it. |

## Possible future scaling options (conceptual only; none implemented)
| Option | Gains | Costs / trade-offs |
|---|---|---|
| PostgreSQL for metadata | concurrent writers, replication, richer indexing | a service to operate; new transaction and consistency story with the chunk store |
| Object storage for chunks | replicated, effectively unbounded capacity keyed by the existing SHA-256 | loses atomic rename and local fsync semantics, so the publication protocol changes |
| Asynchronous workers | parallel chunking/hashing, queued uploads | concurrency control around the shared chunk index; job and failure model |
| Caching | cheaper lookups and re-verification | invalidation and memory limits |
| Distributed storage | scale-out and fault tolerance | consensus, partial failure, rebalancing: a different system |

ChunkVault is a single-machine, sequential prototype ([ADR-0005](../decisions/0005-sequential-processing.md)); it does not
implement or claim any distributed capability.
