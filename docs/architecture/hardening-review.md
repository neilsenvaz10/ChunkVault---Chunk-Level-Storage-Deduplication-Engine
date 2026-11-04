# Phase 2.5 Hardening Review

Scope: re-examine the Phase 2 storage engine before building the Phase 3 UI. All results below
were produced by running the code (commands and counts are in the Phase 3 report).

## 1. CDC correctness — no defect found, algorithm unchanged

- **Polynomial** `0x3DA3358B4DC173`: degree 53. For a prime degree `n`, `P` is irreducible over GF(2) iff `x^(2^n) ≡ x (mod P)` and `P` has no root. The test computes `x^(2^53) mod P` by repeated squaring (result `x`) and checks the constant term is 1 (no root at 0) and the term count is odd (no root at 1). Both hold, so the polynomial is irreducible.
- **Rolling fingerprint vs independent recomputation:** `tests/test_cdc.py` chunks data with the rolling implementation and with a naive implementation that recomputes the whole window polynomial at every position (two parameter sets, windows of 16 and 32 bytes). Boundaries are identical.
- **Read-buffer independence:** identical chunk sizes for read sizes 1, 7, 4095, 4096, 65 537 and 1 MiB, and for a stream that returns short reads.
- **Parameters unchanged:** 4 KiB minimum, 16 KiB target, 128 KiB maximum, 64-byte window.
- **Why observed averages differ from 16 KiB.** The expected length of a chunk is `min + Geometric(1/D)` truncated at `max`, with `D = 12 288`: theoretical mean ≈ **16 384 B**, standard deviation ≈ 12 KiB. The mean of a *small* sample therefore varies a lot (the Phase 2 report's 21.4 KiB came from 49 chunks, standard error ≈ 1.8 KiB — about 2.7 σ, and it included the short final chunk). Measured over 8 × 4 MiB of random data (2 029 full chunks, final chunks excluded): **mean 16 472 B**, standard error ≈ 273 B — consistent with the theory. The Phase 2 wording suggesting a systematic shift was wrong; the 16 KiB target is a design parameter that real data (especially highly repetitive data, which hits the forced 128 KiB cut) need not reproduce.

## 2. Upload resource limits — defect found and fixed

**Defect:** the 64 MiB limit was enforced only inside the endpoint, *after* the multipart parser had already received the entire body into memory/temporary disk. An oversized or malicious request could therefore consume unbounded temporary storage.

**Fix:** a pure-ASGI `BodyLimitMiddleware` (`backend/app/api/limits.py`) runs before body parsing:
- declared `Content-Length` above the limit → `413 upload_too_large`, nothing read;
- malformed `Content-Length` → `400 invalid_content_length`;
- bytes are also counted as they arrive, so chunked requests and clients that lie about the length are cut off immediately after crossing the limit (tested: reading stops within one message of the limit);
- `POST /api/files` may carry `max_upload_bytes` + 1 MiB multipart overhead; every other request body is limited to 1 MiB; the exact file-size limit is still enforced while copying to staging;
- the middleware sits inside the CORS middleware so a browser can read the 413.

Streaming after parsing is unchanged. No new dependencies. Remaining limitation (documented): while a ≤ 64 MiB upload is in flight it exists once as a multipart temporary file and once as the staging file.

## 3. Recovery and deletion safety

| Property | Evidence |
|---|---|
| Interrupted uploads never leave a completed file referencing missing chunks | crash after N publishes, crash in the final transaction, crash while receiving; audit finds leftovers, no `complete` row |
| Reconciliation never deletes chunks referenced by completed files | **Defect found:** with a tampered `ref_count = 0` on a referenced chunk, reconcile aborted on the foreign-key constraint (no data lost — the constraint protected it — but the repair crashed). **Fix:** reclamation queries now also require that no manifest row uses the chunk (`NOT EXISTS`), so the chunk is simply left and reported as a `ref_count_mismatch`. Two regression tests failed before the fix. |
| Shared chunks survive deletion | tests incl. tampered counters (a deleted file's drifted counter can no longer remove a chunk another manifest uses) |
| Reference counts consistent after rollback | failed duplicate upload leaves all existing counters unchanged; failed fresh upload leaves no chunk rows |
| Multiple server processes cannot bypass the lock | OS-level lock test with a child process **and** two real processes uploading concurrently into one store (8 files, consistent counters, healthy audit) |
| Interrupted deletion/cleanup retryable | crash between commit and unlink, then reconcile twice (second run is a no-op) |
| Read-only audits never mutate | database file bytes (after checkpoint) and all chunk files identical before/after, query-only connection refuses writes |

## 4. Report alignment

The System Design Report and Feasibility Report are **not in the repository** and were not
supplied. Alignment with them cannot be claimed. The implementation was compared only with the
Phase 1–3 briefs. Deviations from those briefs are listed in the Phase 2 and Phase 3 reports
(one-level chunk directories; verified temp-file restore instead of streaming; audit endpoint
path; `ADOPT` decision instead of `REPAIR`). **Action needed:** provide the two reports for a
real comparison.
