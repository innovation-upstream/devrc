# Decision record — the generalized "subsystem" store was proposed and REJECTED

**Status:** rejected on evidence, 2026-08-11. Supersedes an unlanded proposal draft.
**What shipped instead:** PR #361 (version the recon index + autocommit) and PR #362
(four schema repairs to `/analyze-service`).

Read this before proposing a subsystem/knowledge store again. The idea is reasonable
and was investigated properly; it lost on measurement, not on taste.

---

## What was proposed

Generalize the `/analyze-service` index — a per-service markdown *document + journal*
sheet at `~/.claude/analyze-service-index/<scope>/<service>.md` — into a "subsystem" store
covering any durable thing (code, data, infra, process, organization, vendor, personal),
with a deterministic CRUD (`subsys new/ls/show/journal/link/graph/search/…`), a thin skill,
a `type:` field driving per-class sections and recon recipes, a `depends_on` dependency
graph, and `/handoff` records migrated into per-subsystem journals.

## Why it was rejected

Four independent evaluations (architecture, verification, adversarial-skeptic, and a
hand-authored strain test) converged. The corpus numbers are the core of it — measured
after the index had been in real use for eight weeks:

| Measurement | Value |
|---|---|
| Index entries | 20 |
| Entries of a non-infra `type:` | **0** |
| Distinct scopes in use | **1** |
| `depends_on` edges populated | **0** |

So `type:` — the field the proposal called "the mechanism that makes generality real" —
would have had exactly one value, and `graph` — called "the payoff grep cannot produce" —
would have had no edges.

**The strain test settled it.** Rather than design for hypothetical classes, one `process`
and one `org` entry were hand-authored in the *current* schema. Results:

- `type:` **selected nothing.** Both non-infra entries used the same three sections the 20
  infra entries use. The type-selected extras were infra sections: empty for `org`, and for
  `process` "Operate" would swallow the entry rather than structure it. Recon recipes turned
  out to be per-*subsystem*, not per-*class* — which `## Pointers` already covers.
- `depends_on` drew **zero honest edges.** The candidate edges meant runtime-requires,
  hosted-in, contains, and consumes-output-of. One `contains` edge from an org node returns
  that org's entire subsystem list for any blast-radius query.
- **Addressing collided at n=1.** One slug was the natural address for both a code subsystem
  and a ritual about it; aliasing around it *shadowed* the future entry, because the resolver
  matched aliases before declaring a miss.
- **The sharpest result:** the `org` entry's three most valuable sections came out empty for
  want of **evidence**, not structure. Nothing in the workspace sourced those facts.
  Designing a schema for them was premature by exactly one step.

**Adoption was the second problem.** Counted across all transcripts: six of seventeen
slash-commands have never been invoked once — including one built specifically to detect
that failure mode, and a deterministic gate, which is the class `subsys` would have joined.
`/analyze-service` itself *did* stick. The refined lesson is not "opt-in fails" but
**opt-in survives when it rides an existing ritual and dies when it *is* the ritual.**
A 13-verb CRUD is the second kind.

**The `/handoff` migration was the most expensive part and the least justified.**
`session-analysis/initiative-scan.py` consumes `claudedocs/handoff-*.md` at seven sites,
two of which are path-containment security guards that fail *silently* when handed a path
outside the repo. The filename-derived base slug is also the stability guarantee behind
`(repo, slug)` primary keys in the initiatives store, so moving handoffs would have caused
initiative identity churn — orphaned archive rows and orphaned paid LLM recaps.

## What shipped instead

- **PR #361** — the one real defect the proposal identified: the index was unversioned,
  unsynced and unbacked-up. It is now a **per-scope git repo** with an hourly `systemd --user`
  autocommit, no remote, under `ProtectSystem=strict` / `ProtectHome=tmpfs` / `PrivateTmp` /
  `PrivateNetwork` / `NoNewPrivileges` / `InaccessiblePaths=/dev/shm /dev/mqueue`.
- **PR #362** — four schema repairs that came from the strain test, not the proposal:
  optional kind-qualified filenames plus an ambiguity-errors-never-shadows resolver rule;
  a `sensitivity:` field with a fail-safe default; `repo:` → `scope:` with non-repo scopes
  permitted and `namespace:` optional; and a **liveness convention** — persist the
  *derivation method* and what a stale answer looks like, never the current reading.

## Errors in the original draft — do not re-derive them

Recorded because each was believed, argued from, and then measured false:

1. Handoff *authored dates* do **not** feed initiative momentum. `doc_touch_epoch` has zero
   call sites; the scanner states explicitly that doc freshness must never set `last_touch`.
2. There are **two** doc loaders, not one — the second globs all `claudedocs/*.md`.
3. Killing handoff writes would not have made cards `undocumented`; doc-less handoff clusters
   are dropped entirely and never become cards. The real risk was slug stability (above).
4. "854 handoffs" conflated all tracked `claudedocs/` files with handoffs. Actual: **207**.

## The falsifiable gate for revisiting

🔴 **SUPERSEDED 2026-08-13 — the original gate TRIPPED, and it was the wrong question.**
Both of its conditions are now met, so leaving it stated as-is would have a reader check a
gate that has already fired and conclude nothing had changed. The original text was:

> Reopen the design when the index holds **≥5 entries outside its current single scope** or
> **≥5 entries of a non-infra type**. Both counters read zero, and have for the life of the
> store. At that point there is a corpus to design against instead of a hypothesis.

Measured by `subsystem_touch.py --census` on 2026-08-13 — **29 entries across 5 scopes**
(`civitai` 1 · `civitai-app-starters` 1 · `datapacket-talos` 25 · `devrc` 1 ·
`homelab-talos` 1), of which **8 carry `created_by: handoff`**. Against the anchor recorded
before any second writer existed — **21 entries · 1 scope · 21 unstamped** — that is 4
scopes that did not exist and 8 entries no infra-recon command could have written.

**Why the original gate was the wrong question.** Its "no demand" premise was CIRCULAR: the
only writer at the time was `/analyze-service`, an infra-recon command pointed at two
cluster repos, so only infra entries in one scope *could* exist. "Nothing non-infra exists"
is not evidence of no demand when nothing is able to create one. The counters were measuring
the writer, not the demand.

**The question that actually binds is COVERAGE** — does the index cover the repos where work
happens? That was the constraint the original measurement found: of 290 path-carrying
sessions, 12 were in the one indexed scope, and 7 of those resolved — a 58% hit rate *inside*
covered scope, against near-total blindness outside it. Coverage has moved from 1 of ~12
active repos to **5**. The corpus the gate was waiting for now exists.

**This does not reopen the rejected design.** The narrower rejections stand on their own
evidence and are unaffected by any of the above: no `type:` taxonomy, no dependency graph,
no opt-in multi-verb CRUD. What is refuted is only the "no demand" half. Re-read "What the
strain test actually found" before proposing any of the three.

## What replaced the premise: derived session association

The proposal assumed a store you *type into*. The replacement is a store that is *populated
from telemetry you already collect* — which answers the adoption objection structurally and
supplies the evidence the `org` strain test found missing.

Decisions taken:

- **Association is derived, not tagged.** Map session → subsystem by matching the path
  components of `kind=session-summary`'s **`changed_paths`** against the entry's slug and
  `aliases:`. This deliberately does **not** persist a location, preserving the index's rule
  that location is always re-derived live.

  🔴 **CORRECTION — the original wording of this bullet was wrong when written.** It said
  the mapping works from "what the collector already emits (`kind=session-summary` carries
  git commits/pushes)". `session-summary` carried integer **counts** — `git_commits`,
  `git_pushes`, `files_modified` — and never a commit, never a path. There was nothing to
  match path components *against*; the derivation was asserted with no data behind it, and
  anyone building P0 against that sentence would have got to the resolver and found its
  input did not exist. Recorded rather than quietly edited: the failure was believing a
  field name implied a payload, which is the same mistake as reading a type declaration as
  a code path.

  The paths exist as of **PR #398**, which added the `changed_paths*` block to both sources
  and fixed the opencode extractor — and they are **populated, not merely defined**: #398 is
  deployed to both hosts, and 585 historical opencode sessions have been backfilled, turning
  362 files / 94 commits / 37,311 lines that read as a hard zero into real rows. P0 therefore
  has a corpus to resolve against on day one rather than only forward-going data.

  Two properties the resolver must be built against, both measured, neither optional:
  `changed_paths` is `null` when the file set could not be observed — **not** an empty list,
  and a `null` read as "touched nothing" reintroduces exactly the silent zero #398 removed;
  and it carries only paths under the session cwd, **~1 in 7** of the modified paths (470 of
  3,290, 2026-08-11), with the remainder counted in `changed_paths_outside_cwd`. So a
  subsystem's session count is a **lower bound**, which the liveness convention has to state
  rather than round away.

  ⚠ Backfilling again is not a one-liner: `opencode/backfill.py` clears the message-tailer
  state as well as the summary state, and message rows carry no `argMax`-on-read dedupe
  contract, so a naive re-run re-emits ~6,100 duplicate message rows. The 585-session
  backfill cleared **only** the summary state deliberately.
- **The edge lives in ClickHouse**, as a new `kind=session-subsystem` event — one row per
  `(session, subsystem)`. Do **not** extend `session-summary`: consumers dedupe it with
  `argMax(<field>, ingested_at) GROUP BY session`, and a session touches N subsystems.
- **The markdown store holds nothing new.** A subsystem's session history is a query. No
  cache to go stale, and no increase in the exfiltration surface — transcripts are ~3.6 GB.
- **Orthogonal to initiatives.** A session gets an initiative (thread of work) *and*
  subsystems (durable things touched). Governing distinction: **a subsystem is a durable
  thing that exists; an initiative is a thread of work on it.**

🔴 **The resolver must share its normalization with the command** (the `_`→`-` fold added in
PR #362). Two implementations of one predicate regenerate the same bug at both sites, and
here the failure is silent — associations simply do not match, and a zero reads as
"this subsystem had no sessions."

Phasing: **P0** the resolver as a pure, source-agnostic function with tests → **P1** emit the
edge from the existing 5-min `claude-activity-source` timer → **P2** surface it in the recon
brief. Layer B (`kind=session-insight`) inherits the dimension when it is built.

**Open, and a correctness prerequisite rather than a nicety:** the edge is Claude-Code-first
because opencode's session path is a separate tailer over a SQLite store. If a material share
of work happens in opencode, Claude-only association **systematically under-counts**, and a
subsystem worked on mostly via opencode looks *dormant* — which would poison the liveness
convention above. **Measure the opencode session share before wiring anything to liveness.**

## Pointers

- `claude/commands/analyze-service.md` — the schema and write-back protocol, as repaired.
- `~/.claude/analyze-service-index/<scope>/README.md` — store safety rules (no remote, no
  stash/reset/clean), authoritative for the store itself.
- `scripts/analyze-service-index/commit.sh` + the `analyze-service-index-commit` units.
- `scripts/session-analysis/initiative-scan.py` — the handoff consumers described above.
