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
bounded only by `SOCKET_TIMEOUT`. `Notes.List` has no `LIMIT` and rows carry full task
bodies, so the payload only grows — the excuse was always going to expire.

Fixed by reading the body with **`read1()`** (returns whatever is already available instead
of blocking for a full `READ_CHUNK`), so the loop re-checks the clock promptly. The
`(max_bytes + 1) - total` size argument is unchanged — it is an over-read **by one** so the
cap *detects* an oversized body rather than silently truncating. Worst-case termination
delay is now ONE `read1`, i.e. `SOCKET_TIMEOUT`, not "however long the peer takes to deliver
64 KiB". Regression test: `test_abandoned_worker_dies_promptly_on_a_chunk_spanning_body`
(red on `read`, green on `read1`).

## 🟡 KNOWN, MEASURED, NOT FIXED
Serve-stale has **NO upper age bound** (clawgate down → last good map served forever), and
`group_by_slug` keys on **slug ALONE, not (repo, slug)** — zero collisions measured, still
latent.
