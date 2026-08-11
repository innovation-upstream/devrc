# Linked clawgate tasks — the `initiative:<slug>` tag contract

PR #202, `origin/main` e3e7b07. Read this when a dispatched task does **not** show up
linked on its card, when changing `dispatch.py`'s tagging, or when touching the board's
clawgate fetch.

`tasks.py` joins initiatives → clawgate tasks on the `initiative:<slug>` tag. Rendered
**ONLY in the EXPANDED card detail** (the collapsed card stays two-line for scanning).

## 🔴 The join is EXACT / case-sensitive / VERBATIM
A plain dict lookup on the raw slug, **no case folding**. `initiative:Foo` keys `Foo` and
therefore **NEVER** matches the ledger slug `foo`. That is why repo-cos's `taggable_slug`
**drops** any non-lowercase slug rather than lowercasing it.

## Dispatch guard
⤴ dispatch is GUARDED: when linked tasks are open it arms a two-tap *"already has N open
task — dispatch anyway?"* via the pre-existing `armConfirm` (armConfirm is **NOT** from #202).

`OPEN_STATUSES = ("open","in_progress","ready_for_review")` — a closed ALLOW-list, so an
unknown status is non-blocking (fail-open).

⚠ clawgate has **NO `dismissed` task status**; the vocabulary is exactly
`{open,in_progress,ready_for_review,complete}` and dismissing DELETES. (`dismissed` exists
only in clawgate's unrelated *suggestions* table.)

## Tagging — fails open TWICE
`dispatch.py` tags what it creates (`initiative:<slug>`, exact-or-nothing):
`build_tags` emits `[]` on any doubt, then `post_task` retries ONCE untagged on **HTTP 400
only**.

⚠ its `normalize_tag`/`normalize_tags` are a **DELIBERATE DUPLICATE** of
`repo-cos/clawgate.py` (the documented source of truth) — **change both together**. They
have **ALREADY DRIFTED**: repo-cos gained `guard_tags` (per-tag, rejects
non-list/non-string input, #206); `dispatch.py` has no equivalent.

## Fetch shape
ONE fetch per **BOARD RENDER** (not per card — the board carries ~140 cards), **2.0s
WALL-CLOCK deadline**, **1 MiB cap**, performed **OUTSIDE `DataProvider._lock`**
(deliberate — keep it that way), 30s cache keyed on last **ATTEMPT**,
serve-stale-on-failure, single-flight. Silent best-effort: any clawgate failure logs to
stderr and the board renders unchanged.

## ✅ FIXED — abandoned-worker leak (`read` → `read1`)
The abandoned fetch worker used to **NOT self-terminate**: `resp.read(n)` blocks on a
`BufferedReader` until n bytes or EOF, so with `READ_CHUNK=64 KiB` the between-chunk
wall-clock re-check was **unreachable** for bodies ≥64 KiB (**measured then: 6 fetches → 6
live threads, +12 FDs, none reclaimed**). It was excused as "bounded today because the real
payload is ~16 KB".

🔴 **That bound expired.** Re-measured **2026-08-11 against clawgate 0.7.85**:
`GET /api/tasks` = **94,428 bytes for 10 tasks** — past the 64 KiB chunk, so the read spanned
2 chunks and the first blocked a full chunk past the 2.0s deadline. The leak was **live**,
bounded by **how long the peer took to deliver 64 KiB** (measured 2.09s past a 0.5s deadline
— NOT by `SOCKET_TIMEOUT`, which is per-operation and never fired). `Notes.List` has no
`LIMIT` and rows carry full task bodies, so the payload only grows — the excuse was always
going to expire.

Fixed by reading the body with **`read1()`** (returns whatever is already available instead
of blocking for a full `READ_CHUNK`), so the loop re-checks the clock promptly. The
`(max_bytes + 1) - total` size argument is unchanged — it is an over-read **by one** so the
cap *detects* an oversized body rather than silently truncating. Regression test:
`test_abandoned_worker_dies_promptly_on_a_chunk_spanning_body` (red on `read`, green on
`read1`; verified independently 2.09s → 0.00s past the deadline, on both `Content-Length`
and chunked bodies).

🔴 **This fixed the BODY phase only — termination is still NOT bounded.** The first version
of this section claimed the worst case was now one `read1` i.e. `SOCKET_TIMEOUT`; an
adversarial audit measured that false the same day. `read1` does nothing for DNS + connect +
**headers**, which remain a blocking `urlopen`, and `SOCKET_TIMEOUT` is per-OPERATION — so a
peer dribbling the response HEADER one byte at a time, every send inside the timeout, holds
the worker open indefinitely: **>60s past a 2.0s deadline at production constants, identical
before and after the fix.** That is the same `_slow_drip` shape `tasks.py` already documents
at +130s. The blackhole peer that sends nothing is the BEST case (~0.00s), not the worst.

## 🟡 KNOWN, MEASURED, NOT FIXED
- **Connect/header phase is unbounded** — see above. `read1` bounded the body only.
- **The single-flight guard does not hold this to one thread.** `LinkedTaskCache.get`
  releases `_refresh_lock` when `loader()` returns — at the DEADLINE, not when the worker
  dies — so any worker outliving the TTL lets the next refresh start another. **Measured
  2026-08-11: 7 concurrent `initiatives-clawgate-fetch` threads.** This is the sentence that
  used to make the residual sound harmless; it was false.
- Serve-stale has **NO upper age bound** (clawgate down → last good map served forever), and
  `group_by_slug` keys on **slug ALONE, not (repo, slug)** — zero collisions measured, still
  latent.
- `total > max_bytes` (`tasks.py`) is **not** pinned against an off-by-one: mutating to `>=`
  leaves the suite green, since no test drives a body of exactly `MAX_RESPONSE_BYTES` through
  the real socket path. Pre-existing, and in the safe direction (over-refusal).
