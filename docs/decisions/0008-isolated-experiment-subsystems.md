# ADR-0008: Fault injection and benchmarking run in isolation, bounded and opt-in

**Status:** accepted · **Phase:** 5

## Problem
Demonstrating reliability and performance honestly requires breaking things and timing them, but the application's real store must never be endangered, benchmark code must not slow or destabilise request handling, and no experiment may run unexpectedly.

## Alternatives considered
- **Inject faults into the live store** (or a copy of it): realistic but unsafe, and a bug could damage user data.
- **Run benchmarks inside the server process**: simple, but long runs block or slow requests, and the stage timers (which wrap module-level functions) would also time real requests.
- **Hard-coded or pre-recorded results**: safe, but they would be fabricated rather than measured.
- **Isolated, bounded, opt-in experiments that run the real code** (chosen).

## Decision
- **Fault lab:** every experiment creates its own temporary store, runs the real upload/restore/delete/recovery code with one injected failure (through a no-op `Uploader.checkpoint` seam or instance-level replacement on its private services), verifies the outcome with the real auditor and restores, and deletes the store. It refuses to run if the store could overlap the application's data, only accepts nine built-in scenario ids, runs one at a time, and is **disabled unless `CHUNKVAULT_ENABLE_FAULT_LAB=1`**.
- **Benchmarks:** each runs in a separate worker process on a private temporary store, one at a time, with bounded file size, iterations, processing budget and a hard time limit; the request returns immediately and the job can be cancelled (the process is terminated and its store removed). Results are saved as JSON under `data/benchmarks/` (git-ignored) with their parameters.
- **Capacity simulation:** a pure function with no storage access, labelled as a mathematical model.

## Advantages
- Experiments cannot touch user data and cannot block the API; a crashed experiment is cleaned up.
- Results are real measurements or real observed behaviour, reproducible from a seed.

## Disadvantages
- The lab proves behaviour for the scenarios it injects, not for every possible failure; it simulates process death, not power loss or disk faults.
- The production upload path gained a (no-op) test seam.
- A worker process adds start-up cost (about 0.5 s) and an extra moving part; timings are specific to the machine and the pure-Python chunker.

## Consequences
Faults are never available from a request outside the built-in list, and the real-store page offers no fault controls. Stage timers patch module-level functions, which is safe only because they run in the worker process. Windows needed a retrying atomic file replace for progress reporting (found by a real run).

## Scalability implications
None intended: these are single-machine, sequential tools. A larger system would need distributed fault testing and a benchmark harness with load generation; neither is implemented or claimed.
