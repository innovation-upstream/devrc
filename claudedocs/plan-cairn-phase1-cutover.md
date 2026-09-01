# Cairn — the write-through cutover (criterion 9), designed and staged

**Status: STAGED, NOT APPLIED.** Nothing in this document has been executed against the
hosted store or either host's local store. `scripts/cairn-cutover.py` is dry-run by
default and the operator runs it. This doc is the argument; the script is the procedure.

🔴 **This repo is PUBLIC and the store is client-confidential.** No entry content, no
filenames and no scope names appear below. Scopes are referred to by count and role. The
script prints the real names at run time, on the operator's terminal — that is deliberately
the only place they appear.

---

## 1. What criterion 9 is, and why it is the load-bearing one

Today an append to the store is an `Edit` against `~/.claude/analyze-service-index/…`. That
is **local disk on one host**, and it has three consequences that compound:

1. the two hosts drift, permanently and invisibly;
2. a bullet appended through the hosted API lives **only** in the served copy, and the next
   `seed.sh` from any host replaces that entry with a file that never had it;
3. so "the write path works" and "the bullet survives" are different claims, and the second
   is false until this lands.

Criterion 9 makes the pod the authority: writes go through it, and local disk becomes a
read-through cache. That is what makes an API write **durable**.

## 2. The measured starting state

Re-measured 2026-09-01 from the workbench, the laptop (over ssh) and the served copy.

| | entries | scopes |
|---|---|---|
| served copy (pod) | 132 | 15 |
| workbench local store | 147 | 15 |
| laptop local store | 49 | 12 |
| **union** | **195** | **22** |

- The pod's 15 scopes are exactly the workbench's 15. **7 scopes exist only on the laptop**
  and are absent from the pod entirely.
- **5 scopes exist on both hosts.** In them the laptop holds 17 entries, of which **16 have
  names the pod does not** — pure addition, no decision.
- **Exactly one entry name is held by both hosts with different bytes.** It is the only
  genuine host-vs-host merge in the whole migration.
- The pod is a **snapshot of the workbench alone**, taken `2026-08-29T20:34:38Z`. Since then
  the workbench has moved: **42 of its entries differ from the served copy**, and 15 of
  those have the pod holding at least one bullet line the host does not.
- **Exactly ONE of those bullets carries the API attribution trailer** — i.e. exactly one
  entry in the whole store holds bytes that exist nowhere else. That number is what makes
  the rule in §3 cheap instead of a 15-way hand merge.

## 3. THE MERGE RULE

Stated generally, because it has to survive the next migration and not just this one. It is
implemented in `plan_delta()` and each clause has a test named after it.

> **1. Present on one side only → take it.** Additive; no decision exists.
>
> **2. Present on both, byte-identical → skip.** This clause is what makes a re-run a no-op
> rather than a second push, i.e. what makes the whole operation idempotent.
>
> **3. Present on both and divergent, pod vs a host** → decided by one question, asked over
> **every line** and not only the bullets: *does the served copy hold any line this host does
> not?*
>
> > **3a.** any such line carries the API attribution trailer (`[cairn: actor/session]`) →
> > **hand merge**. It was written through the pod and exists in exactly one place.
> > **3b.** there is no such line at all → **supersede**, and this is the strongest case in
> > the rule: the host's copy is a superset, so nothing can be lost. A plain append lands
> > here, as do a trailing newline and a CRLF difference.
> > **3c.** such lines exist, none attributed → **supersede**. Lines the host has since
> > edited away. The count that are not bullet openers rides along in the reason but does
> > **not** change the verdict — see below.
>
> **4. Present on both HOSTS and divergent → always a hand merge.** Never last-write-wins,
> whatever the mtimes say.
>
> **5. A hand merge is cleared ONLY by a human-authored file** at the same relative path
> under `--merged`. Nothing else clears it; an unresolved one refuses the whole run.

### Why rule 3 is a lineage argument, not an mtime one

The pod's tree was **produced from** a host's tree by `seed.sh`. So for any entry the pod
did not itself change, the host's copy contains everything the pod's does — the pod's is a
lagging derivative. mtime cannot say this: every file on the pod carries the tar extract's
time, and every file on a host carries its last local edit, so mtime ranks the *copies* and
not the *lineage*.

The attribution trailer is precisely the marker of **the pod having changed one itself**.
`server.render_bullet` puts it on every API-appended bullet and nothing else in the store
produces it. So it is the exact discriminator rule 3 needs, and detecting it is the whole
safety of the rule rather than a nicety.

**Measured on the real trees:** of 42 divergences, 27 have the host copy as a strict
superset of the pod's bullets; 14 more have the pod holding bullet lines the host lacks that
are *supersessions* — an `OPEN:` line the host has since rewritten as `RESOLVED <sha>:`, or
a claim the host has since retracted — with **no** attribution; **1** has an attributed
bullet. Treating all 15 as hand merges would have made this a 15-file manual operation for
14 files where the host copy is simply the newer one.

### 🔴 A stricter version of 3c was tried and REVERTED, on measurement

The obvious refinement is: *a pod-only line that is not a bullet opener must be front matter
or `## Pointers`, i.e. outside what the attribution rule can see, i.e. a `cairn put` — so
refuse it.* That argument sounds more careful and is wrong. **A store bullet WRAPS**, and a
wrapped bullet's continuation lines do not begin `- `. Rewriting one bullet therefore
produces "non-bullet lines only the pod has" as a matter of course.

Measured on the real trees: the stricter rule turned **1 hand-merge into 10** — nine ordinary
edits demanded operator attention. That is the permanently-red-gate direction, reached by the
version of the rule that reads as most conservative. The count is reported and not gated.

⚠ **The rule's limit, stated rather than left to be met.** Rule 3 is a claim about *how the
pod's copy got there* — `seed.sh` produced it from a host, and the only thing that can have
changed it since is the API, whose every change is attributed. `cairn put`, added by this
same change, breaks that: it rewrites whole files with no attribution, so once it is in use a
PUT-modified entry is genuinely indistinguishable from a seed-time snapshot. **The freeze is
what closes that window** — after the cutover the hosts are caches and host-vs-pod divergence
cannot arise. Until then, the non-bullet count in each SUPERSEDES reason is there so an
operator who *has* run `cairn put` can see which entries to look at.

### 🔴 There are TWO collision AXES, and an early framing collapsed them into one

This effort was briefed with *"exactly ONE filename collision in the whole migration"*. That
measurement was real and it was **scoped to the host-vs-host axis only**. The pod-vs-local
axis was never enumerated at entry level, so "one collision" quietly became "one merge
decision in the migration", which is false. Both axes, counted:

| axis | what a collision means | how it was counted | count |
|---|---|---|---|
| **host vs host** | the same entry name on both hosts with different bytes; neither is a derivative of the other | entry names present in both hosts' manifests whose sha256 differ | **1** |
| **pod vs local** | the served copy holds content that exists nowhere else — i.e. an API-appended bullet carrying the attribution trailer | for every entry the pod and a host both hold and disagree on, the pod's lines not present in the host's, filtered to those matching the attribution pattern | **1** |

🔴 **The pod-vs-local count rests on TWO claims, and only the first is a proof.** An earlier
version of this paragraph said the count was *"provably complete, whatever it diverges by"*.
That is wider than the instrument supports, and this document's own rule-3 commentary says
so: the attribution trailer is written by the **append** route only, and a whole-file
replace — `PUT /api/v1/entry/<scope>/<ref>`, or `cairn put` — writes no trailer at all. An
entry rewritten that way would diverge pod-vs-local and carry nothing for the filter to
find. So, stated as two claims with two different warrants:

1. **For the ATTRIBUTED class the count is complete by construction.** Exactly **one of 132**
   served entries carries an attribution trailer at all, so no second entry of that kind can
   exist regardless of what else it diverges by. That is the regex over the whole served
   copy, not a scan of the divergences.
2. **For the UNATTRIBUTED class the count is empty because nothing has exercised a
   whole-file write.** `cairn put` is added by *this* change and therefore cannot have run
   before now.

⚠ **Claim 2 has a gap I did not close.** The server's `PUT` route has existed since criteria
1–7 shipped, so a hand-rolled `curl` PUT against the pod is *possible* even though no client
existed. I did **not** check the audit log for one. If you want claim 2 airtight before the
cutover, query Loki for a `method=PUT` audit line over the store's lifetime; a zero there
closes it, and the pod-log grep does not (it holds only the current pod's history — the
reassuring-zero shape this effort has already been bitten by).

🔴 **AND THE GUARANTEE EXPIRES THE FIRST TIME ANYONE RUNS `cairn put` AGAINST THE POD.**
After that, pod-vs-local completeness needs a different instrument than the trailer filter —
the trailer will still be absent and will no longer mean "nothing unique here". A reader who
inherits the word *provably* will not re-check this at exactly the moment it stops being
true, which is why the warrant is written out rather than the conclusion alone.

⚠ **The two axes need different evidence and neither substitutes for the other.** Host-vs-host
needs a manifest from the *other* machine (`--manifest` over ssh); pod-vs-local needs the
*served* bytes (`cairn sync`). A run with no `--peer-manifest` is structurally blind to axis
1 and says so in as many words; a run that never syncs is blind to axis 2 and refuses.

### The two hand merges

Measured with the corrected rule, the workbench's plan is **88 SAME · 15 ADD · 42
SUPERSEDES · 2 MERGED · 0 NEEDS_MERGE** — 59 entries shippable. The two hand merges are one
per axis, which is why both are staged:

**(i) The host-vs-host divergence (rule 4).** Resolved as the **union**: both `aliases:`
lists, both `## Pointers` sets, and every `## Nuance` bullet from both copies interleaved
newest-first. **Neither copy was discarded and no bullet was reworded.** Each side carried
something the other did not — one holds a set of deployment pointers and a Flux-parentage
correction, the other holds newer prose and a whole outbound design — so "take the newer
file" would have silently dropped a documented design.

**(ii) The pod-attributed bullet (rule 3a).** Exactly one entry in the whole store holds an
API-appended bullet that exists nowhere else. Resolved mechanically: the host's copy, with
that one bullet inserted **in date order** under `## Nuance / work-history` — its date is
older than the host's newest, so putting it at the very top would have broken the store's
newest-first convention. The bullet is carried **verbatim**, including its known
double-date defect (`- <date>: <date>: …`, from a caller that date-prefixed text the server
already stamps). A merge that silently repairs a writer's text is a merge that changed
meaning; it is flagged here instead.

Both merged files are staged at

    ~/.local/share/cairn-cutover/merged/<scope>/<entry>.md      (mode 0600)

**outside the repo, deliberately** — it is client-confidential and this repo is public. It
Both have been validated with the writer's own parser (`subsystem_touch --validate`):
*OK — 2 of 2 entry file(s) parse, 0 malformed*.

⚠ **One advisory it carries forward unchanged.** One bullet inherited from one side writes
its `RESOLVED <sha>:` marker with a parenthetical between the sha and the colon, which the
grammar scores as a **near-miss**: it declares nothing and shows no badge. It was left
exactly as written, because a merge that silently rewords a writer's marker is a merge that
changed meaning. Fixing it is a one-line edit for whoever next touches that entry.

## 4. Ref collisions — including the ones the merge INTRODUCES

A ref that resolves to two entries in one scope raises `AmbiguousRefError`; the write route
answers that **400**, so **both entries become unwritable**. Merging two hosts' scopes can
create such a pair out of two entries that were each unambiguous at home. This is the
failure mode filename comparison cannot see, and it has to be checked on the **union**.

### The precedence, measured rather than assumed

`resolve_ref_tiered` consults the FILENAME tier first and reaches the ALIAS tier **only if
the filename tier returned zero hits**. Measured against the real store: a ref that is both
one entry's filename stem and another entry's alias resolves to the **file**, `tier=filename`.
So there are two classes, and conflating them would make the check a permanently-red gate:

- **LIVE** — a filename collision, or an alias collision on a ref no filename answers. The
  resolver raises; the entries are unwritable. **This blocks.**
- **LATENT** — an alias shadowed by a filename. It changes nothing today and becomes live
  the day that file is renamed or removed. **Reported, does not block** — refusing here
  would refuse the migration over a defect that is already present and already harmless.

### What is actually there

Measured over the union (195 entries), using the resolver's own `normalize_ref` **and its
own `split_kind`**:

| | count | where they come from |
|---|---|---|
| LIVE | **3** | 1 **introduced by the merge**; 2 pre-existing on one host and newly *served* |
| LATENT | **4** | 2 pre-existing; **2 introduced by the merge** |

⚠ **An earlier count of these same numbers was a FLOOR, not a count**, and the correction is
worth keeping. The checker first split `<slug>.<kind>.md` with a hand-rolled
`stem.split(".")` instead of importing `split_kind`, which registered only kind-less files
under their slug. But `resolve_ref_tiered` matches a bare ref against `e.slug` with **no
kind constraint**, so `svc` reaches `svc.md` *and* `svc.process.md` and raises — a live
ambiguity making both entries unwritable, and the checker returned nothing for it.
Re-measured after importing the resolver's own splitter: the union happens to hold no such
pair, so the table above is unchanged — but it is now a count rather than a lower bound, and
the difference was invisible until the splitter was shared.

- **The introduced LIVE one** is an alias claimed by an infra-scope entry on one host and by
  a differently-named infra-scope entry on the other. It exists on neither host today and
  appears only in the union. **Resolution: the entry that also declares the alias of the
  underlying CRD keeps it; the other drops the alias.** That entry's alias set names the
  specific mechanism the ref denotes, which the other's does not — an argument from the
  entries' own declared vocabulary rather than from outside knowledge. The script still
  requires the operator to state the decision explicitly with
  `--alias-owner <scope>:<ref>=<filename>`; it will not pick.
- **The two pre-existing LIVE ones** are both in one host-exclusive scope and are *not* new
  — but they are newly *served*, because that scope reaches the pod for the first time here.
  They must be resolved by the same mechanism before those two entries are writable.
- **The two introduced LATENT ones** are the same shape in the other direction: one host's
  entry carries an alias that spells the *filename* of an entry the other host brings. They
  are inert (the filename tier answers first) and are reported, not blocked.
- **The pre-existing LATENT one named in the brief** — an alias in this repo's own scope
  claimed by two entries where one of them is also the filename — was **confirmed by
  measurement** to be unreachable: the ref resolves to the file, `tier=filename`, and the
  second entry's alias can never be chosen. Recommendation: drop the dead alias, because a
  latent collision becomes live silently the day the file is renamed. Not a blocker and not
  part of this cutover.

### The missing-`aliases:` decision

Two laptop entries carry no `aliases:` field at all. **Decision: migrate them as-is.**

The reason is a measurement, not a preference: **13 of the 132 served entries already carry
no `aliases:` field**, and 14 of 147 on the workbench — mostly scope `README`s, which
legitimately have none. A missing `aliases:` is the store's normal state, not an anomaly;
the entry template ships that line commented out. Adding aliases to exactly these two would
be inventing a rule nothing else follows, and — worse — speculatively adding aliases is
precisely how the LIVE collisions above came to exist. Their filename stems remain
resolvable, which is what matching actually needs.

## 5. The write-through contract, and its failure modes

`cairn append` and `cairn put` are the write verbs. **The pod is the authority; the local
cache has none.**

| situation | behaviour | exit |
|---|---|---|
| append lands | prints `appended` + the new revision | 0 |
| the same text re-sent | prints `duplicate`, file untouched | 0 |
| store unreachable | **refused, loudly. Not queued, not written locally.** | **7** |
| the request is wrong (bad bullet, unknown ref, unseen scope) | refused, with the server's own sentence | 6 |
| the entry moved under the edit (`If-Match`) | refused; re-sync and re-apply | 8 |
| the running image has no write path (405 `read-only`) | refused, and named as an **operator** problem | 6 |

🔴 **A write during an outage is REFUSED, not queued. That is the accepted cost**, chosen
deliberately (decision 8 of `plan-cairn-integration.md`). A spool would be invisible, and a
caller told "queued" believes the record exists. One refused write costs a retry; one
silently-local write costs the bullet at the next seed.

🔴 **READS KEEP WORKING OFFLINE.** `/resume`, `cairn recall`, `cairn search` and
`subsystem_recall.py` all read the local cache and continue to serve during an outage,
stating that they are serving stale bytes. The asymmetry is the design:

- a **read** must never report an outage as an empty store, so it degrades and says so;
- a **write** must never report a failure as a success, so it refuses.

The write exit codes (6/7/8) are disjoint from every read code (0/2/3/4/5) precisely so a
caller cannot read *"nothing was displayed"* as *"the bullet landed"*. A test asserts the
disjointness as a set, and a mutant that collides them is killed.

### What criterion 9 does NOT close — read this before believing the cutover is complete

1. **There is no CREATE route.** `POST` and `PUT` both resolve an *existing* ref; a ref that
   resolves to nothing is 404. So a brand-new subsystem's first entry cannot be written
   through the API at all. The freeze therefore targets entry **files** (0444) and leaves
   scope **directories** writable, so a new file can still be created locally and pushed.
   That is a deliberate asymmetry with a known cost, not an oversight.
   *Closing condition: a merged devrc PR adding a create route to `server.py`, deployed, and
   `cairn` gaining the verb that uses it.*
2. **The read/write allowlist split is designed and NOT implemented.** Criterion 9 owns it,
   and today it buys nothing: there is exactly one credential and one tenant, so a
   read-only token has no holder. The design is a fourth field on a mapped token row listing
   *write* scopes, defaulting to the read scopes when absent — backward-compatible by
   construction, so the existing row keeps its current authority. Implementing it means
   changing `server.py`, building an image, and a homelab-infra secret change; none of that
   can be verified without a deploy.
   *Closing condition: a merged devrc PR adding the field to `load_tokens` with its
   negative controls, plus a homelab-infra commit adding write scopes to the row, verified
   by a read-only credential receiving 405/403 on a write and 200 on a read.*
3. **The protocol change is NOT in this PR.** The `subsystem-index` skill still instructs an
   `Edit` against the local path. It is deliberately not edited here: a skill change ships
   at the next `scripts/ship.sh`, which would land **before** the operator runs the cutover,
   and appends would then 404 for every entry the pod does not yet hold. The exact change is
   in §8 step 7, to be made as its own PR after the cutover verifies.
   *Closing condition: a merged devrc PR changing `claude/skills/subsystem-index/SKILL.md`'s
   write step, shipped, and one `/handoff` observed appending through `cairn append`.*

### What else touches the local store, and what the freeze does to each

Checked, because a freeze is only safe if you know every writer. Three timers/tools reach
that tree:

| toucher | what it does | effect of a 0444 entry file |
|---|---|---|
| `analyze-service-index-commit.timer` (hourly) | `git init` / `git add` / `git commit` per scope | **unaffected.** It writes only into `<scope>/.git/`, which stays 0755, and it rewrites no working-tree byte (measured, and stated in the skill's own reference). Reading a 0444 file is what `git add` needs. |
| `analyze-service-index-backup.timer` (6-hourly) | reads the tree, encrypts, uploads | **unaffected** — read-only over the tree. |
| the `subsystem-index` / `analyze-service` append protocol | `Edit` on an entry | **EACCES.** Intended: that is the hazard being closed. It moves to `cairn append` in §8 step 7. |
| the `prune-index` skill | evicts RESOLVED bullets — a whole-file rewrite | **EACCES.** Not covered by step 7's append change: pruning is a `cairn put`, and that skill needs its own follow-up. Named here so it is not discovered as a mystery permission error. |

🔴 **`prune-index` is the writer most likely to be forgotten**, because it runs rarely and
its failure will arrive weeks later looking like a broken store rather than a completed
migration. Its closing condition is the same shape as step 7's: a merged devrc PR routing
its rewrite through `cairn put`, shipped, and one prune observed landing on the pod.

## 6. The ordering argument: criterion 9 BEFORE criterion 8

`claudedocs/handoff-cairn-phase3.md` ranks the laptop re-seed (criterion 8) at rank 3 and
this cutover (criterion 9) at rank 4. **Running them in rank order is wrong**, and PR #1187
records the correction in that doc.

`seed.sh` pushes `rsync -a --delete` SOURCE→STAGE, then tars STAGE→pod. The tar adds and
overwrites but never deletes — so running it from a host **overwrites every entry that host
also holds with that host's copy**, destroying any API-appended bullet that exists only in
the served copy. Today exactly one such bullet exists. Running 8 first means relying on the
backup to recover it; running 9 first means the overwrite has nothing unique left to
destroy, because by then the served copy holds everything and the hosts are caches of it.

**A destructive operation made safe beats a destructive operation made recoverable.**

This is also why the cutover script does **not** hand a whole store to `seed.sh`. It builds
a **curated delta tree** holding only the entries §3 classified as shippable, and points the
unmodified `seed.sh` at that. `seed.sh` is destructive because of *what it is given*, not
because of what it does. One push path, one set of guards, one verdict format — and after
the cutover, criterion 8 reduces to a verification rather than a second push.

## 7. Rollback, per phase

| phase | what it changes | rollback |
|---|---|---|
| P0 preconditions | **a run directory (0700) holding a full copy of the store, and one non-mutating POST** — see below | delete the run directory |
| P1 plan | writes only into the run directory | delete the run directory |
| P2 collisions | nothing | — |
| P3 push | adds and overwrites entries on the pod | `cairn-cutover.py --rollback-push <run-dir> --apply` re-PUTs the pre-push bytes saved for every entry the push was about to overwrite. **Partial by construction**: an ADD has no pre-image and the API has no delete verb. |
| P4 verify | nothing | — |
| P5 freeze | records every entry's mode, then `chmod 0444` | `cairn-cutover.py --unfreeze --apply` restores **the recorded modes**, not a normalised 0644 |
| protocol change (§8 step 7) | a skill body | revert the commit, `ship.sh` |

⚠ **"A dry run changes nothing" is not literally true, and the two exceptions are worth
knowing.** P0 runs on every non-`--freeze` invocation, including a dry run, and it (a)
creates `~/.local/share/cairn-cutover/runs/<ts>/cache/` — **mode 0700, and holding a full
plaintext copy of the client-confidential store** — which accumulates one per run with no
cleanup, and (b) sends one `POST` to the live pod. That POST cannot write anything (§9), but
it is metered and it lands in the audit log. Nothing on either host's store, and nothing
already on the pod, is modified.

🔴 **The backup precondition gates the CUTOVER, not every mode of this script.** It lives in
the P0 block, which `--freeze`, `--unfreeze`, `--rollback-push` and `--manifest` all skip.
That is right for three of them — `--manifest` reads, and the two rollbacks are what you
reach for *after* something went wrong. It is a real gap for **`--freeze --apply`**, which
chmods every entry file in the store with no backup check, no store comparison and no
acceptance run. It is reversible (`--unfreeze --apply`, from the mode ledger the freeze
writes) and it is a documented single-phase escape hatch — but do not read §9's table as
covering it.

Beneath all of it: the daily backup CronJob (homelab-infra#551, 03:45 UTC, 90-day ILM,
credential with no `s3:DeleteObject`).

🔴 **A TEST CAN DISABLE THIS ROLLBACK, AND ONE DID — read this before adding a `--freeze`
test.** `--unfreeze` with no explicit `--mode-ledger` takes the **newest** ledger under
`~/.local/share/cairn-cutover/runs/`. Five tests called `--freeze --apply` without
`--run-dir`, so their synthetic ledgers landed in the operator's real run root — 64 of them —
and the newest was always one of those. After any test run the documented P5 rollback
therefore selected a fixture ledger, matched nothing, and left the store frozen while
reporting every entry as "created after the freeze". A test poisoning the recovery path of
the thing it tests is the worst shape available, and it is silent from both ends.

Two changes close it, and the next `--freeze` test needs both: each ledger now **names the
store it was taken from** and is refused against any other, and an autouse fixture redirects
`DEFAULT_RUN_ROOT` so a test that forgets `--run-dir` still cannot reach the real path. Pass
`--run-dir` anyway — the flag documents intent; the fixture only makes forgetting safe.

⚠ **`--unfreeze` REQUIRES the mode ledger and refuses without it.** `chmod 0444` destroys
the originals, so a restore with nothing to restore *to* can only invent a mode — and
inventing 0644 for an entry that was 0600 is a permission **widening** on
client-confidential content, performed by the recovery path and called a rollback. The
freeze therefore writes `<run-dir>/.cairn-cutover-modes.json` (0600) before it changes
anything, and an entry created *after* the freeze — possible, since scope directories stay
writable — is reported and **left alone**, never guessed at.

⚠ **The rollback for a failed FREEZE is automatic and is not optional.** If the freeze is
applied and any entry file still accepts a write, the script restores every mode bit to
0644 and exits 16. A store that *looks* frozen and is not is worse than one that plainly is
not — an agent would keep appending locally and believe it durable.

## 8. The operator runbook

Every step is a command to run and a thing to read. **Read the output, not the exit code
alone** — several of these print a verdict their status cannot carry.

```bash
D=~/workspace/devrc

# 1. LAPTOP FIRST — produce its manifest, so a host-vs-host divergence is DETECTED
#    rather than silently superseded. Without this, merge rule 4 is UNCHECKED and the
#    dry run says so in as many words.
ssh zach@10.42.0.100 "python3 ~/workspace/devrc/scripts/cairn-cutover.py --manifest" \
  > ~/.local/share/cairn-cutover/laptop-manifest.json
#    expect: one JSON object, sha256 + aliases per entry, NO bullet text.

# 2. DRY RUN on the workbench. Changes nothing. Reports the plan and every refusal.
python3 $D/scripts/cairn-cutover.py \
  --peer-manifest ~/.local/share/cairn-cutover/laptop-manifest.json
#    expect, in order:
#      P0 local store: <n> entries / 15 scopes
#      P0 backup OK — subsystem-store/subsystem-store-backup last succeeded <ts>
#      P0 cairn sync -> rc 0 … 132 entries
#      P0 the write route IS deployed — … refused 400 …
#      P1 plan over <n> local entries: {'ADD': …, 'SAME': …, 'SUPERSEDES': …,
#         'MERGED': 2, 'NEEDS_MERGE': 0}
#      measured on the workbench today: SAME 88 · ADD 15 · SUPERSEDES 42 ·
#      MERGED 2 · NEEDS_MERGE 0, i.e. 59 entries shippable
#      P2 union … LIVE ref collisions=3 LATENT…=4    <- and it will REFUSE at rc 15
#    🔴 rc 15 on the first run is EXPECTED. Step 3 is what clears it.

# 3. RESOLVE THE 3 LIVE COLLISIONS. The dry run named the scope, the ref and both
#    claimants. For each, edit the LOSING entry's `aliases:` line to drop that ref —
#    in the local store on the host that holds it — then acknowledge the decision.
#    ⚠ The losing entry may live on the LAPTOP; edit it there and re-run step 1.

# 4. DRY RUN AGAIN, with the decisions. Read the plan before applying it.
python3 $D/scripts/cairn-cutover.py \
  --peer-manifest ~/.local/share/cairn-cutover/laptop-manifest.json \
  --alias-owner '<scope>:<ref>=<winner>.md' \
  --alias-owner '<scope>:<ref>=<winner>.md' \
  --alias-owner '<scope>:<ref>=<winner>.md'
#    expect: rc 0, "DRY RUN — <n> entr(ies) would be pushed", and the exact seed.sh
#    command it would run. NEEDS_MERGE must be 0 and MERGED must be 2 (the
#    two staged hand merges). A NEEDS_MERGE it names is a file you must author.
#    ⚠ A stale entry under --merged is reported as `SAME … a STALE hand-merge
#    … was IGNORED` rather than pushed — read those lines, they are how you
#    learn a resolution from an earlier round is still lying around.

# 5. APPLY, from the WORKBENCH. Pushes the delta, verifies, then freezes.
export SUBSYSTEM_STORE_TOKEN_FILE=<path to a file holding just the token>
python3 $D/scripts/cairn-cutover.py --apply \
  --push subsystem-store/subsystem-store-api \
  --peer-manifest ~/.local/share/cairn-cutover/laptop-manifest.json \
  --alias-owner … (the same three)
#    expect: seed.sh's own "seed: OK all N staged entries are present on the pod",
#    then "P4 acceptance … missing=0", then verify-byte-identity.sh's per-scope
#    PASS lines, then "P5 WATCHED EACCES: all N entry file(s) refused an append".
#    ⚠ If $SUBSYSTEM_STORE_TOKEN_FILE is unset it says the byte-identity half is
#    UNMEASURED and continues. That is not the same as clean — set it.

# 6. THE LAPTOP HALF (criterion 8). Its local store still holds the 7 host-exclusive
#    scopes and they are NOT yet on the pod. Repeat step 5 there, WITHOUT --push if
#    you want to see the plan first:
ssh zach@10.42.0.100 "python3 ~/workspace/devrc/scripts/cairn-cutover.py \
  --peer-manifest /tmp/workbench-manifest.json"
#    (produce that manifest with --manifest on the workbench and scp it over)
#    then the same --apply --push from the laptop.
#    🔴 This is where the ordering pays off: by now every shared entry on the pod
#    already matches, so the delta is the 7 scopes plus 16 names and NOTHING is
#    overwritten.

# 7. THE PROTOCOL CHANGE — a separate PR, after 5 and 6 verify. In
#    `claude/skills/subsystem-index/SKILL.md`, the write step becomes:
#        cairn append --scope <scope> --ref <entry> --session <session-id> \
#          --text '<the bullet, ONE line, no leading "- ", no leading date>'
#    with the `OPEN:`->`RESOLVED` rewrite becoming `cairn put`. Keep the "show the
#    diff first" rule; drop the `Edit`-anchored-on-the-heading rule, which the API's
#    per-entry flock replaces. Then merge -> pull -> `scripts/ship.sh`.

# 8. VERIFY THE WHOLE THING from a fresh session, on BOTH hosts:
cairn sync && cairn recall --scope <a scope> | head -5
python3 $D/scripts/cairn-cutover.py \
  --peer-manifest ~/.local/share/cairn-cutover/laptop-manifest.json
#    expect rc 0 and, in the P1 line, NEEDS_MERGE 0 with an EMPTY delta:
#      "P1 plan over <n> local entries: {'ADD': 0, 'SAME': <n>, 'SUPERSEDES': 0,
#       'MERGED': 0, 'NEEDS_MERGE': 0}"
#      "DRY RUN — 0 entr(ies) would be pushed …"
#    🔴 A DRY RUN DOES NOT REACH P5 — it returns at the end of P3, by design, so
#    it prints no P5 line at all. An earlier version of this step said to expect
#    `P5 "already applied"`, which no dry run can emit; the obvious "fix" is to
#    add --apply, which re-enters the push path. Check the freeze separately,
#    read-only:
python3 $D/scripts/cairn-cutover.py --freeze   # dry run of phase 5 ALONE
#    expect: "P5 every entry file already refuses a write — the freeze is
#             already applied. This is the idempotent re-run.", rc 0
```

**Rollback, if step 5 or 6 goes wrong:**

```bash
# restore each entry to the mode the freeze RECORDED (not a normalised 0644):
python3 $D/scripts/cairn-cutover.py --unfreeze --apply
#   it uses the newest ~/.local/share/cairn-cutover/runs/*/.cairn-cutover-modes.json;
#   pass --mode-ledger <path> to pick one. With NO ledger it REFUSES rather than
#   inventing a mode — that refusal is correct, not an obstacle.

# re-PUT the served bytes of every entry the push overwrote:
python3 $D/scripts/cairn-cutover.py --rollback-push \
    ~/.local/share/cairn-cutover/runs/<timestamp> --apply
#   PARTIAL BY CONSTRUCTION: an ADD has no pre-image and the API has no delete
#   verb, so this undoes overwrites only. It derives a fresh If-Match, so it
#   REFUSES rather than clobbering a third party's later write.
```

⚠ **What the acceptance check does NOT cover.** `comm -23` compares **this host** against
the served copy. An entry only the *other* host holds is not checked for strandedness by a
run on this one — which is why step 6 runs the whole script on the laptop too, and why the
peer manifest is worth producing even though the plan could proceed without it.

### 🔴 An incident from building this, because the mechanism generalises

While this branch was being written, a **background mutation sweep** was rewriting
`scripts/cairn-cutover.py` in place — mutate, run the suite, restore, repeat. A `git add`
landed inside one of those windows and **committed a mutant**: the P5 empty-walk refusal
shipped as `if False:`, disabled, in the very commit whose message explains why it exists.

There was no error. `git log` showed exactly what was expected, and the working tree looked
correct seconds later once the sweep restored it. **The suite could not catch it** — the
sweep restores, so every later run is green over a red commit. What caught it was comparing
the *committed blob* against the sweep's own pre-mutation backup (`git show HEAD:<path>` vs
the `cp` aside); that artifact was the only thing that could have seen it.

**The generalisation:** a tool that rewrites your tree is a **concurrent writer**, and
`git add` is a read of whatever is there at that instant. `claude/RULES.md` already says to
re-check *which branch* before committing because another session may have moved it — this
is the same hazard one level down, in the CONTENT, moved by a process you started yourself.
A mutation sweep is the worst instance, because its whole job is to produce plausible edits
that survive a diff review.

⚠ **Bounding it honestly, in both directions.** Every commit on the branch was scanned for
the strings the sweep can write: exactly one was contaminated, and it is reverted; HEAD's
blob is byte-identical to the verified backup. And the contaminated commit was never going
to become `main`'s state under any merge method. devrc's recent practice is **squash** —
the twelve most recent first-parent commits on `main` all carry a `(#N)` squash suffix — and
a squash discards intermediates entirely. But merge commits are permitted and **common in
this repo's history: 49 on first-parent at the time of writing**, of which 30 are GitHub PR
merges and 19 are *local* merges (`Merge branch 'main' of …`, `Merge remote-tracking branch
…`) — a different animal, and worth not lumping together. Under any of them the merge's
TREE is still the branch head's content. So the blast radius is this branch's history, not
the mainline.

🔴 **AND THE COUNTER-BOUND, so the paragraph above cannot be read as "we were fine": the
same race would have shipped the disabled guard had it landed on the FINAL commit instead of
an intermediate one. Only the ordering prevented that, not the mechanism.** We were fine by
luck.

⚠ **That merge count was WRONG in this document's first draft, and the way it was wrong is
the same class of defect as the incident it describes.** It read "five exist in history",
taken from `git log --merges --oneline origin/main -5` — a command whose `-5` *is the
number that came back*. The length of a list truncated by your own flag is not a population
count, and it reads as measured precisely because it is specific. The same shape as
`gh pr view --json files` silently capping at 100, and of a `grep | head` whose zero means
"the pattern was not in the first N". **Ask what bounded the output before quoting its
size.**

**The operational rule:** never run a mutation sweep concurrently with anything that stages,
builds or tests. It contends with all three and only one of them fails loudly.

## 9. The instruments, and how each was validated

Every verdict below comes from something that was shown to be capable of the opposite
answer. A green from an unvalidated instrument is a claim about the instrument.

| instrument | negative control | positive control |
|---|---|---|
| `verify-byte-identity.sh` | one entry mutated by a single character → **FAIL, naming the scope** | identical stores → **PASS**, printing `raw-diff-lines`/`store-root-lines` beside the verdict |
| the backup precondition | absent CronJob, absent `kubectl`, unparseable JSON, unparseable timestamp, and a `lastSuccessfulTime` that is **absent** → all refuse with their own sentence | a fresh timestamp passes and prints the measured age beside the ceiling; the boundary is measured from **both** sides (35.5 h passes, 36.5 h refuses) |
| the write-route probe | a `405 read-only` server → reported as an **operator** problem | a `400 bad-request` server → reported as deployed. The probe body is `{}`, refused by the server's validator *before* any ref is resolved, so it cannot write |
| the freeze | `probe_writable` returns `writable` at 0644 and `refused` at 0444, on the same file, twice | a partially-applied freeze (one file left writable) exits 16 **and** restores every mode bit |
| the mutation sweep | baseline green before any mutant is scored; a known-fatal mutant is killed | **28/28** killed, each by the **named** test; originals restored and re-verified green |

⚠ **The sweep took five rounds, and three of them found a defect in the sweep or in the
guards rather than in the code.** Recorded because the pattern is the norm here, not bad
luck: round 1 found two guards that could not be reached (one unreachable behind an earlier
return — labelled, not counted; one predicate inseparable from its neighbour — **deleted**);
round 3 found the sweep's own parser matching `0 failed for another reason` out of the
script's stderr instead of pytest's summary line, scoring a killed mutant as SURVIVED; round
4 found two guards whose tests pinned membership rather than behaviour (a status table row
could point at the wrong exit code, and `main` could discard the probe's own code, both
invisibly). Only round 5 returned clean, which is what ended it.

🔴 **The backup precondition refuses on every way of NOT getting an answer**, not only on a
stale answer. devrc PR #1132 exists because fifteen places in this repo asserted this
store's backup state and were wrong about it, in both directions across its life. A missing
`status.lastSuccessfulTime` is reported as **COULD NOT MEASURE**, never as "0 hours ago".

⚠ **What the backup gate does NOT establish, said out loud:** that the backup is
*restorable*. `lastSuccessfulTime` is the CronJob controller's record that a Job exited 0.
Restore-testing is homelab-infra#551's business; this is a liveness gate, not a drill.

## 10. Two documents corrected

**(a) `plan-cairn-integration.md` said "No tenancy, no auth, no age-out, no sharing exist in
any form."** The auth half is **false and was false when written**. Measured with both
controls against a read route: no token → **401**, wrong token → **401**, the real host
credential → **200**. Per-token identity, server-side scope authorization and the
enumeration property all shipped, and the last unrestricted credential was retired
2026-08-31. Corrected in place, with the measurement.

**(b) There are two live phase numberings, and they cross.** The plan document numbers
*phases of the plan*; clawgate #371, the handoff docs and every commit message number
*delivery milestones*. They are not the same axis and neither can be renamed — one is in
commit messages, the other is the planning vocabulary.

| delivery label (commits, cg#371, handoff docs) | what shipped | plan-document phase |
|---|---|---|
| "phase 1" | seed + read-only hosted API | (precursor to plan phase 1) |
| "phase 2" | `cairn`, the read-through client | (precursor to plan phase 1) |
| "phase 3", criteria 1–7 | per-token identity, scope authorization, the write path | **plan phase 2** |
| "phase 3", criterion 10 | retiring the unrestricted credential | **plan phase 2** |
| "phase 3", criteria 8 + 9 | the re-seed and **this cutover** | **plan phase 1** |

🔴 **So "phase 3 criteria 8/9" is plan PHASE 1 work carrying a phase-3 label**, which is
exactly why it reads as further along than it is. The rule going forward, recorded in the
plan document: **the plan document's phases are canonical for planning; the delivery labels
are historical and are not renamed; any new work item names both.**

## 11. What is left after this

In the order they should be done, each with the condition that closes it:

1. **The protocol change** (§5 item 3) — a devrc PR editing the `subsystem-index` skill's
   write step, shipped, and one `/handoff` observed appending through `cairn append`.
2. **A create route** (§5 item 1) — a merged devrc PR adding it to `server.py`, deployed,
   and `cairn` gaining the verb.
3. **The read/write allowlist split** (§5 item 2) — a merged devrc PR plus a homelab-infra
   secret change, verified by a read-only credential getting a refusal on a write and 200 on
   a read.
4. **Route `prune-index` through `cairn put`** (§5's table) — a merged devrc PR, shipped, and
   one prune observed landing on the pod. Lowest urgency and highest surprise value: it runs
   rarely, so its EACCES will arrive weeks after the cutover looking like a broken store.
5. **Drop the dead aliases** — the 4 LATENT collisions. Each is a one-line edit; each
   becomes live the day its shadowing filename is renamed.
