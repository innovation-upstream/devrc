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

## 🟡 KNOWN, MEASURED, NOT FIXED
The abandoned fetch worker does **NOT self-terminate**. `resp.read(n)` blocks on a
`BufferedReader` until n bytes or EOF, so with `READ_CHUNK=64 KiB` the wall-clock re-check
is never reached for bodies ≥64 KiB (**measured: 6 fetches → 6 live threads, +12 FDs, none
reclaimed**). Bounded today only because the real payload is ~16 KB. **Fix = `read1()`.**

Also: serve-stale has **NO upper age bound** (clawgate down → last good map served
forever), and `group_by_slug` keys on **slug ALONE, not (repo, slug)** — zero collisions
measured, still latent.
