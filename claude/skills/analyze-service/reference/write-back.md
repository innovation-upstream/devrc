# `/analyze-service` write-back — the opt-in, confirm-gated index update

Loaded on demand. The recon itself (`scripts/lib/service_recon.py`) is
**read-only and has no write path at all**; this document is the whole of the
mutation half, and it is executed by you, not by the script. Schema, location,
resolution and store safety are in `index-store.md`.

Recon stays **read-only by default** — the index is mutated only when a run
surfaces something notable AND the user confirms, shown as a **diff first**.
Never silent-mutate.

1. Run the recon brief (read-only) as usual.
2. **After** the brief, evaluate whether it surfaced anything **notable** (below).
3. Nothing notable → **do nothing**, say `index unchanged`.
4. A proposed change → present it as a **unified diff** against the current index file (or "new file" for first-ever), one compact block, and ask a single yes/no: *"append this to the index? (y/N)"*.
5. **Write only on explicit confirm.** On confirm, re-read the file (so a concurrent append isn't clobbered), re-apply the change to current bytes, then plain Write to `~/.claude/analyze-service-index/<scope>/<slug>.md` (creating the dir/file if first-ever; use `<slug>.<kind>.md` **only** when a same-slug entry of another kind already exists, and say why in the diff). On a **first-ever** file, stamp `created_by: analyze-service` in the front matter (schema in `index-store.md`) — on an append, leave whatever is there. On decline, discard. The write is local and final — **nothing leaves the machine**: no remote, no push. But it is **not** outside git; committing the scope repo is the store's own concern (an out-of-band autocommit), never this command's, so **write the file and run no git command** (🔴 **Store safety** in `index-store.md`).

## Notable — append-worthy

Matches the "Gotchas" spirit + the `MEMORY.md` "durable lesson, not status" bar:

- A **gotcha**: non-obvious behavior, a lying/misleading status condition, an ephemeral-vs-durable trap, a wrong-looking-but-correct error string.
- A **revert or bump** found in `git log` that explains *why* someone was looking. The brief marks these `⚠ MOVED`.
- An **incident tie-in**: the recon connected the service to a firing alert / a known `MEMORY.md` slug / a handoff — record the pointer.
- A **new pointer** discovered (a `manage-*` skill or slug the index didn't yet reference).
- 🔴 A **structural finding the brief itself surfaced**, which the old hand-run recon could not see: a `MULTI-DIRECTORY` note (the service is an umbrella, or is split across app/chart/container directories) is durable and belongs in `## What it is`.

**NOT notable — never append:** routine healthy state, config values, or anything a pointer target already captures. These are the "Bloat discipline" rules below, applied at the append decision.

## Auto-discovered pointers

Propose in the diff, still confirm-gated — a bad match must be rejectable.
Curate the starting set: **propose at most ~5-7 candidates, never a raw match
list** — a dump is unusable even though the human confirms each.

- `manage-* skill`: match the service name against skill names/descriptions in `.claude/skills/*/SKILL.md` (e.g. `redis`→`manage-redis`).
- `MEMORY.md slug`: **filename-match first** — propose slugs whose *filename* contains the normalized service token (or an `aliases` entry), e.g. `*redis*.md`; those are the slugs actually ABOUT the service. **Only if that yields <3**, fall back to content-grep of the memory dir, but **rank by mention density and propose only the top few**, never the raw `grep -il` list (it is far too broad: `redis` returns ~90 slugs vs ~15 actually redis-centric).
- `claudedocs handoff`: same — **prefer filenames containing the normalized token**; density-rank a content-grep fallback only if that is too thin, and cap the count.

## Bloat discipline

Mirrors the `MEMORY.md` memory-hygiene rules.

- **Pointers, not copies** (schema in `index-store.md`) — domain detail stays in the skill/slug/handoff it points at.
- **NEVER persist live status** — pod counts, Ready/NotReady, canary phase, event tails, current image tag/replica values. Re-derived every run — the single most important anti-bloat rule.
  - 🔴 This now has a mechanical tell: **anything in the recon brief's `config:` or `live:` blocks is re-derived every run and must not be persisted.** Those two blocks are exactly the forbidden set.
  - **No live probe ⇒ persist the DERIVATION, not the reading.** For a process/ritual entry ("is this still being followed?") there is no `kubectl` two seconds away — so record *how to take the reading and what a stale one looks like*: "liveness = mtime of the exclusions file vs. the timer's last fire; stale ⇒ mtime predates the last two fires." The method is durable; the answer it gave ("last followed 2026-08-01") is live status exactly like a pod count, and stays forbidden.
- **Dated nuance bullets, newest-first, ≤2 lines each.**
- **Prune-on-resolve** — when a gotcha is fixed / incident closed / revert superseded, **remove** the bullet (its durable form lives in the slug/handoff it points to). The index is a live pointer sheet, not an append-only log.
