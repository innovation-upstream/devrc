# `/analyze-service` write-back — what is worth recording

Loaded on demand. The recon itself (`scripts/lib/service_recon.py`) is
**read-only and has no write path at all**; this document decides *whether* a run
has anything worth recording, and it is executed by you, not by the script.
Schema, location, resolution and store safety are in `index-store.md`.
**How the write itself is performed is NOT in this file** — see step 4.

Recon stays **read-only by default** — the index is mutated only when a run
surfaces something notable. Never silent-mutate.

1. Run the recon brief (read-only) as usual.
2. **After** the brief, evaluate whether it surfaced anything **notable** (below).
3. Nothing notable → **do nothing**, say `index unchanged`. Declining on content is a normal, frequent outcome.
4. Something notable → **follow the write half of `claude/skills/subsystem-index/SKILL.md`** (deployed `~/.claude/skills/subsystem-index/SKILL.md`), then come back here for the two caller facts below. That file is the ONE append protocol for this store, and it is the same one `/handoff` follows.

<!-- one-append-protocol:begin — pinned VERBATIM (normalised) by scripts/tests/test_index_append_protocol.py. This block is exactly where a second, forked protocol would grow back, so it is pinned whole rather than by keyword: any reword fails on purpose. Editing it means re-reading BOTH doors and updating the pin in the same commit. -->
🔴 **THE APPEND PROTOCOL IS NOT RESTATED HERE, DELIBERATELY.** Two documents
describing one write is how they come to disagree. Until 2026-08-31 this file
gated the append behind *"append this to the index? (y/N)"* and told you to use a
plain `Write`, while `subsystem-index/SKILL.md` declared that prompt retired and
mandated `Edit` anchored on `## Nuance / work-history`. Both read as *the*
protocol, neither named a winner, and what they disagreed about was the one
operation that can destroy another session's bullet.

🔴 **Operator decision, 2026-08-31 — the fork is closed: the y/N is retired
EVERYWHERE, and `subsystem-index/SKILL.md` is the single protocol.** A whole-file
retype is MEASURED to lose a concurrent append silently, so `Edit` anchored on
`## Nuance / work-history` was the mandated mechanism. 🔴 **Both halves of that
sentence are HISTORY now, and the second half became a DEFECT.** Since the
2026-09-01 Cairn cutover every write goes to the pod, and since 2026-09-03 a
first-ever file is `cairn create`. The retired carve-out said `Write` "stays
correct **only** for a first-ever file, which has no prior content to lose" —
true while the local tree WAS the store, and false the moment reads moved to the
pod cache: a locally-created entry is then dark to every reader on every host
(measured 2026-09-02, five of them). **Do not
re-add a protocol paragraph to this file.** What belongs here is what is specific
to `/analyze-service` — what counts as notable, the auto-discovered pointers, the
bloat discipline — plus the two caller facts the shared protocol deliberately
leaves to its caller:

🔴 **You are the `analyze-service` caller, and the shared protocol hardcodes no
writer.** On a **first-ever** file,
stamp `created_by: analyze-service` in the front matter (schema in `index-store.md`);
on an append, leave whatever is there.
`scripts/lib/subsystem_touch.py --template` refuses to print a template without
`--writer`, so pass `--writer analyze-service` — the value is the CALLER's id,
never the tool's. `--census` reads that split back, which is the only reason the
stamp exists.

**Where the entry goes:** it is addressed as `<scope>/<slug>` **on the pod** —
`cairn create --scope <scope> --ref <slug> --file <scratch>` for a first-ever
entry, which makes the scope directory there too. 🔴 **Never write it into
`~/.claude/analyze-service-index/`**: that tree is a read-only mirror, reads come
from the synced cache, and a file written there reaches nobody. Use
`<slug>.<kind>.md` **only** when a same-slug entry of another kind already
exists, and say why in the diff — ⚠ note the write routes cannot address a
kind-qualified ref today, so that case is still a `put` on an existing file.
<!-- one-append-protocol:end -->

## Notable — append-worthy

Matches the "Gotchas" spirit + the `MEMORY.md` "durable lesson, not status" bar:

- A **gotcha**: non-obvious behavior, a lying/misleading status condition, an ephemeral-vs-durable trap, a wrong-looking-but-correct error string.
- A **revert or bump** found in `git log` that explains *why* someone was looking. The brief marks these `⚠ MOVED`.
- An **incident tie-in**: the recon connected the service to a firing alert / a known `MEMORY.md` slug / a handoff — record the pointer.
- A **new pointer** discovered (a `manage-*` skill or slug the index didn't yet reference).
- 🔴 A **structural finding the brief itself surfaced**, which the old hand-run recon could not see: a `MULTI-DIRECTORY` note (the service is an umbrella, or is split across app/chart/container directories) is durable and belongs in `## What it is`.

**NOT notable — never append:** routine healthy state, config values, or anything a pointer target already captures. These are the "Bloat discipline" rules below, applied at the append decision.

## Auto-discovered pointers

Propose in the diff — the diff is where a bad match has to be visible, because
nothing downstream asks about it. Curate the starting set: **propose at most ~5-7
candidates, never a raw match list** — a dump is unusable, and now that the diff
is shown rather than answered, an unreviewable dump is simply an unreviewed one.

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
- **Prune-on-resolve — MARK first; EVICT only once the pointer target HOLDS it.**

  🔴 **This bullet used to say "when a gotcha is fixed, **remove** the bullet", and that was a live contradiction.** The newer `OPEN:` / `RESOLVED <sha>:` schema in `index-store.md` says *mark* it, don't remove it. Both rules were live and both were followed, so `RESOLVED` bullets accumulated forever while nothing applied pressure — measured 2026-08-21: 77 entries / 359,395 B, 29 `RESOLVED` and 26 `OPEN` bullets among 518. The store's value is **recency-ordered selection**, which is exactly what degrades as entries grow, so this is not tidiness. The reconciled lifecycle, in order:

  1. **On resolve, MARK — never delete.** Rewrite the bullet's head as `RESOLVED <sha>:`, naming the commit that closed it. A sha-less `RESOLVED:` parses but is *unverifiable*, so it can never become evictable — write the sha.
  2. 🔴 **An `OPEN:` bullet ALWAYS STAYS.** Never an eviction candidate, at any age or size, and it never counts toward reclaiming bytes. It is the one thing in the store that cannot be re-derived by re-running recon.
  3. **A `RESOLVED` bullet becomes an eviction CANDIDATE only once its content has a HOME** — i.e. it names a target (a `claudedocs/` path, a commit sha, a PR/issue ref) that is verified to EXIST. Then the durable form genuinely does live elsewhere, and the index goes back to being a pointer sheet rather than an append-only log.
  4. 🔴 **A `RESOLVED` bullet with NO reachable home is `NO HOME — write the record first`, and is NEVER evictable.** Its bullet is the only copy of the finding, so cutting it deletes the finding. Write the record, re-run the audit, *then* evict.
  5. **Nothing is ever auto-evicted.** `scripts/subsystem-audit.py` is READ-ONLY: it reports the three classes with their denominators and names the target it verified. The cut itself is a DELETION, not an append, so it does **not** run the append protocol in step 4 above: it goes through the **`prune-index` skill**, whose own confirm-gated, diff-first contract is in `~/.claude/skills/prune-index/reference/writing-and-safety.md`. That gate survives on blast radius — a cut removes bytes that may be their content's only copy — and is a separate decision from the append prompt retired 2026-08-31.
