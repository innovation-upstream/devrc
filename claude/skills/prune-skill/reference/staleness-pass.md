# Staleness pass — axes, and the traps that manufacture false findings

Routed from the prune-skill core (§0) — `~/.claude/skills/prune-skill/SKILL.md`, source `~/workspace/devrc/claude/skills/prune-skill/SKILL.md`. Load this before running the pass. 🔴 **Read §"False
findings" first — measured over four passes, this produced roughly SIX false findings for every
ONE real in-skill finding.** Run without that discipline and it is a defect generator: two of its
false findings would have "corrected" two *correct* metric names into wrong ones.

## Why the pass exists

A prune preserves rot **by construction**. Verbatim line-range slicing is what makes content
survival structural — and it copies stale content exactly as faithfully as good content. No path
gate proves a claim is TRUE; it only proves a path EXISTS. So a prune makes a skill cheaper to
load and no more correct.

## Deterministic axes (scriptable, ~6 tool calls)

- **Live object names** — deployments, namespaces, nodes, CronJobs vs the real cluster/system.
- **Node/host names** vs live. 🔴 Use python, not `grep -E`: `(?:…)` is not portable ERE and
  fails toward FALSE-CLEAN (one corpus reported 26 names under one grep and **3** under another).
- **Cross-repo paths** — an in-repo path gate checks only its own repo, so `<other-repo>/…`
  references are unchecked. This is a top rot class.
- **The skill's own helper scripts** still exist.
- **Metric / series names** against the live metrics backend.

## Judgement axes (need a read, not a probe)

- Every **load-bearing** claim: a limit, a retention figure, a version pin, an arming state, a
  "this is impossible". Those are what a reader will *plan on*.
- **Internal contradictions** — one prune surfaced three different image tags for one component
  in a single file. Do not invent a winner; state that the doc cannot be trusted and give the
  live read.

## 🔴 False findings — confirm before you "fix"

**An empty result cannot distinguish two mechanisms.** Name the rival mechanism and run a
discriminator before calling anything stale. Measured cases:

| Looked stale | Actually |
|---|---|
| a metric with no series | a **histogram base name** — only `_bucket`/`_count`/`_sum` exist |
| a metric absent from prod | correctly named, **opt-in flag off** — present in the source, unset on the Deployment |
| a dead path | the **checker's own regex truncating** a `$VAR` form |
| a dead file reference | a deliberate `git log -p <sha> -- <path>` reading a **deleted** file out of history |
| stale node names | rows explicitly marked **free/retired**, i.e. correct history |
| a service returning `HTTP 000` | the probe **racing a port-forward**, not a 404 |

Rules that fall out of those:
- **Go to the DEFINING surface**, not a derived one — grep the app source for a metric's
  registration; read the flag off the live object.
- **Carry a positive control** so a zero is distinguishable from a broken query.
- **Suspect your own instrument first.** Three of the six false findings above were the
  checker's fault, not the doc's.
- 🔴 **A DOCUMENTED FILTER BEATS A PROBE.** Where the doc defines how its own quantity is
  computed, that definition *is* the instrument. One pass probed around it with a bare
  `sum(metric)` and got a plausible, wrong number (~$117.3k against a true ~$114.6k); the
  correct filter was written in the doc all along.

## 🔴 Fix EVERY copy

After a split a claim exists in the core, in the sidecar you sliced it into, and often in an
always-loaded file. **A prune multiplies the number of copies of every claim.** One campaign
corrected a figure in the core and the always-loaded file, shipped it, and left the stale figure
in the sidecar the core routes readers to — so following the core's own pointer landed on the
wrong number, while the core's claim about *where* the remaining wrong copy lived was itself
false. Grep the corrected token across the whole skill dir and the always-loaded files before
calling a staleness fix done.

## Where the yield actually is

Across four passes the skills measured clean on nearly every deterministic axis. The real
findings were in the **always-loaded file** and in the **notes about the skills**: a spend figure
42% stale, a wrong provider count, a wrong claim in a project memory, and a backlog item warning
of a hazard that did not exist. Budget the pass for refuting stale *notes* as much as for finding
stale *docs*.

## Disposition

A finding that needs a **decision** is FLAGGED, not silently fixed. Findings go in the commit
message so the next pass re-derives rather than re-litigates.
