# Plan: integrating Cairn into the workflow — 2026-08-31

Settled in an alignment session on 2026-08-31. Eleven decisions, all explicit; this doc
records them, what they cost, and the order to build in. **Nothing here is built yet
beyond what "Where it stands" measures.**

## What Cairn is — the definition that decides the tradeoffs

**A per-person agent re-entry cache. Not a shared knowledge base.**

The job is: a fresh session gets productive fast, on any host, without re-deriving what a
previous session already learned. Everything below follows from that. In particular it is
*not* primarily a document humans read — which is why terse pointers beat prose, and why
automatic capture beats curation.

The team-facing consequence is **multi-tenancy, one store per person** — N private caches
that can opt into sharing, rather than one pooled corpus. That resolves the
client-confidentiality problem by construction instead of by policy.

## The decisions

| # | Question | Decision | Why it cost something |
|---|---|---|---|
| 1 | Primary job | Agent re-entry speed, multi-tenant | Rules out optimising for human readability |
| 2 | Canonical store | Hosted pod; hosts sync from it | Introduces a network dependency for writes |
| 3 | Write trigger | Automatic on touching a subsystem | More low-value entries; needs a real bound |
| 4 | Rollout | Solo until the store is canonical | Delays team feedback |
| 5 | Cross-tenant reads | Opt-in sharing per scope or entry | Makes tenancy a security boundary, not a cache key |
| 6 | Tenant identity | Addressed by git `user.email` | Human-readable, but asserted — see #9 |
| 7 | Bound on growth | Age out untouched entries | Needs read signals that do not exist today |
| 8 | Local store's role | Read-through cache; writes go to the pod | One writer of record, so no merge problem |
| 9 | Authorisation | Pod-issued per-host token | Addressing and authorisation are separated |
| 10 | Age-out signal | Cache reports reads asynchronously | Small telemetry channel; offline reads are lost |
| 11 | Sharing gate | `sensitivity:` gates it — client-confidential can never be shared | Unmarked entries are unshareable until marked |

### Two conflicts the answers created, and how they resolved

Both were caught during the session rather than in code, and both are worth keeping
written down because each would have shipped something that *looked* like it worked.

**Decision 6 vs 5 — the tenant key could not carry the weight sharing put on it.**
`user.email` was recommended on the explicit grounds that it is "fine for a cache, not for
a security boundary". Opt-in sharing makes it a boundary: `git config user.email
<someone-else>` would read whatever they had shared and, worse, **write into their
tenant**, corrupting the store their agent re-enters from. Resolved by #9 — address by
email, authorise by a pod-issued token. Precedent to copy, not invent: the existing
clawgate hook token in `~/.claude/clawgate.env`.

**Decision 7 vs 8 — age-out and the read-through cache cancelled each other.**
Ageing out "untouched" entries needs read signals, but reads served from local disk are
invisible to the pod. "Untouched" would have silently degraded into "not *written*
recently", evicting exactly the stable, load-bearing entries that are read constantly and
rewritten never — backwards, and it would have looked like it was working. Resolved by
#10: the cache batches read events and reports them on the next sync, never in the read
path.

## Where it stands today — measured 2026-08-31, not recalled

🔴 Earlier figures in `claude/skills/subsystem-index/` and in the cairn handoff are from
**2026-08-27 and before**, and the store has grown since. Re-measure rather than quoting
either.

| | workbench | laptop |
|---|---|---|
| entries | **146** | **47** |
| scopes | **15** | **12** |

**22 distinct scopes across the two hosts: 5 exist on both, 10 only on the workbench, 7
only on the laptop.** The laptop-only count is unchanged from the 2026-08-27 measurement,
so that gap is persistent rather than a sync lag. This is the migration input for phase 1:
17 scopes are clean copies, 5 need a merge rule.

Also true today:
- **Two writers, conflicting protocols.** `/handoff` (via the `subsystem-index` skill)
  and `/analyze-service` (via its own `reference/write-back.md`) write the same store by
  materially different rules — one confirm-gates and anchors an `Edit`, the other does
  not. The skill file says reconciling them is open work. Automatic capture makes this
  worse, not better, so it is phase 0.
- **Hosted and local already drift.** `cairn recall` reads the pod; `subsystem_recall.py`
  reads local disk. Neither is wrong today, which is precisely the problem.
- **35 of 146 entries carry no `sensitivity:` marker** and fail-safe to
  `client-confidential`. Under decision 11 that makes them unshareable until marked.
- 🔴 **CORRECTED 2026-09-01. This line used to read "No tenancy, no auth, no age-out, no
  sharing exist in any form." The AUTH HALF WAS FALSE, and was false when written.**
  Authentication is live and enforced on the hosted store — measured against a read route
  with both controls: **no token → 401, a wrong token → 401, the real host credential →
  200.** Per-token identity, server-side scope authorization and criterion 3's enumeration
  property (a refused scope is indistinguishable from an absent one) all shipped as
  "phase 3" criteria 1–7, and the last unrestricted credential was retired 2026-08-31.
  What genuinely does not exist yet: **no tenancy** (one tenant, addressed by nothing —
  authorisation is by token, addressing by `user.email` is decision 6 and is unbuilt), **no
  age-out**, and **no sharing**. The distinction matters because "add auth" reads as
  available work and is not: the next authorisation work is the read/write allowlist split,
  which is a change to an existing mechanism.
- 🔴 **The store is now BACKED UP.** homelab-infra#551 shipped a daily CronJob (03:45 UTC,
  whole-tree tar including `.git`, its own bucket, 90-day ILM, a credential with no
  `s3:DeleteObject`). Several places in this repo still say it is not — see devrc#1132.
- **Zero entries carry task, PR or session front matter** — so nothing in the store can
  currently be joined to the work that produced it.

## 🔴 Two numberings are live, and they cross — read this before sequencing anything

The phases below number **the plan**. clawgate task #371, the `handoff-cairn-*.md` docs and
every devrc commit message number **delivery milestones**. They are different axes with the
same word, and neither can be renamed: one is written into commit history, the other is the
planning vocabulary. The crossing is real and has caused work to read as further along than
it is.

| delivery label (commits, cg#371, handoff docs) | what shipped | phase BELOW |
|---|---|---|
| "phase 1" | seed + the read-only hosted API | precursor to phase 1 |
| "phase 2" | `cairn`, the read-through client | precursor to phase 1 |
| "phase 3", criteria 1–7 | per-token identity, scope authorization, the write path | **phase 2** |
| "phase 3", criterion 10 | retiring the unrestricted credential | **phase 2** |
| "phase 3", criteria 8 + 9 | the laptop re-seed and the write-through cutover | **phase 1** |

🔴 **So "phase 3, criteria 8 and 9" is PHASE 1 work below, carrying a phase-3 label.** The
rule going forward: **the phases below are canonical for planning; the delivery labels are
historical and are not renamed; any new work item names both.** Criterion 9's design is
`claudedocs/plan-cairn-phase1-cutover.md`.

## The plan

Each phase is independently valuable and independently reversible. Nothing depends on a
later phase, so the sequence can stop at any point without leaving a half-built system.

### Phase 0 — reconcile the two writers
Pick one protocol and make both callers use it. Automatic capture (phase 3) multiplies
whatever inconsistency exists here, so this comes first even though it ships no feature.

*Done when:* one write path, exercised by both `/handoff` and `/analyze-service`, with a
test that fails if a second protocol reappears.

### Phase 1 — make the pod canonical for one tenant
Migrate both hosts into the pod, resolving the 5 overlapping scopes. Local disk becomes a
read-through cache; writes go to the pod. This alone fixes the measured drift and is the
precondition decision 4 names for touching teammates.

*Done when:* both hosts serve byte-identical content for every scope, and `cairn recall`
and `subsystem_recall.py` agree. *Falsified by:* a scope that reads differently on the two
hosts after a sync.

🔴 **The merge rule for the 5 overlapping scopes is the risk here**, not the transport.
Same-named entries on both hosts may have diverged. Decide the rule before migrating and
write it down; a silent last-write-wins would discard whichever host was not migrated
last, and the loss would be invisible.

✅ **The rule is now written down and implemented**, with the migration measured:
`claudedocs/plan-cairn-phase1-cutover.md` §3, `scripts/cairn-cutover.py::plan_delta`. It is
generalised past the one file that motivated it — additive where a name exists on one side;
a lineage argument where the pod's copy is a lagging derivative of a host's; and an
unconditional refusal where two HOSTS disagree, cleared only by a human-authored merge. The
script is dry-run by default and refuses rather than guessing.

### Phase 2 — tenancy and authorisation
Tenant addressed by `user.email`, authorised by a pod-issued per-host token. Still one
tenant, so nothing is shared yet — but the boundary exists before anything crosses it.

*Done when:* a request carrying no token, or another tenant's token, is refused for both
read and write. *Falsified by:* a client-side identity change reaching another tenant's
data — test this explicitly rather than assuming.

### Phase 3 — automatic capture and age-out, together
These must land in the same phase. Auto-write without a bound grows without limit;
age-out without read reporting evicts the wrong entries (see conflict 2). Shipping either
alone is worse than shipping neither.

*Done when:* a session that touches a subsystem records a pointer with no prompt, the
cache reports reads on next sync, and an entry untouched for the chosen window is evicted.
*Falsified by:* an entry that is read every week being evicted — the specific failure the
pairing exists to prevent, and therefore the thing to test first.

⚠ **Measure the write volume before enabling it.** The rate is currently unknown, and it
determines whether the age-out window is months or weeks.

### Phase 4 — opt-in sharing, then teammates
Sharing per scope or entry, mechanically gated by `sensitivity:`. Then onboard teammates.

*Done when:* a `client-confidential` entry cannot be shared by any action, and an
unmarked entry is treated as confidential. *Falsified by:* any path that publishes an
entry without an explicit sensitivity decision.

## Explicitly not doing

- **Not** building a shared team knowledge base. Decision 1 rules it out; sharing is
  opt-in on top of private stores.
- **Not** putting the store in git. Entries are client-confidential and this repo is
  public; a split would be needed and no one asked for versioned entries.
- **Not** keying tenancy on machine identity. That is what the current per-host store
  effectively does, and it is the bug phase 1 fixes.
- **Not** adding retries or a concurrency cap to CI as part of this (see below).

## Adjacent, not part of this plan

The CI gate is degraded in a way that will interfere with landing any of the above: PR-leg
pipeline pods preempt the main-leg pods, and queueing on a single pinned node cost **2 of
28 PR-gate runs (7.1%)** a required check on 2026-08-31. Three capacity outcomes and one
real defect currently post the **same** string, which is why the failures read as code
defects. That has its own diagnosis and ranked fixes; it is a prerequisite for smooth
delivery here but is not Cairn work.
