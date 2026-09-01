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
> **3. Present on both and divergent, pod vs a host → the host copy supersedes**, *unless*
> the pod holds a bullet the host lacks that carries the API attribution trailer
> (`[cairn: actor/session]`).
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

⚠ **The rule's limit, stated rather than left to be met.** Rule 3 is a claim about *how the
pod's copy got there*. It holds while `seed.sh` and the API are the only writers. A third
route that edited the served copy would break the premise, not merely the implementation —
which is why NEEDS_MERGE is the fail-safe direction and an unrecognised divergence ends in a
refusal, not a push.

### The one hand merge

The single host-vs-host divergence is resolved, by hand, as the **union**: the union of both
`aliases:` lists, the union of both `## Pointers` sets, and every `## Nuance` bullet from
both copies interleaved newest-first. **Neither copy was discarded and no bullet was
reworded.** Each side carried something the other did not — one holds a set of deployment
pointers and a Flux-parentage correction, the other holds newer prose and a whole outbound
design — so "take the newer file" would have silently dropped a documented design.

The merged file is staged at

    ~/.local/share/cairn-cutover/merged/<scope>/<entry>.md      (mode 0600)

**outside the repo, deliberately** — it is client-confidential and this repo is public. It
has been validated with the writer's own parser (`subsystem_touch --validate`): *OK — 1 of 1
entry file(s) parse*.

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

Measured over the union (195 entries), using the resolver's own `normalize_ref`:

| | count | where they come from |
|---|---|---|
| LIVE | **3** | 1 **introduced by the merge**; 2 pre-existing on one host and newly *served* |
| LATENT | **4** | 2 pre-existing; **2 introduced by the merge** |

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
| P0 preconditions | nothing | — |
| P1 plan | writes only into the run directory | delete the run directory |
| P2 collisions | nothing | — |
| P3 push | adds and overwrites entries on the pod | `cairn-cutover.py --rollback-push <run-dir> --apply` re-PUTs the pre-push bytes saved for every entry the push was about to overwrite. **Partial by construction**: an ADD has no pre-image and the API has no delete verb. |
| P4 verify | nothing | — |
| P5 freeze | `chmod 0444` on entry files | `cairn-cutover.py --unfreeze --apply` |
| protocol change (§8 step 7) | a skill body | revert the commit, `ship.sh` |

Beneath all of it: the daily backup CronJob (homelab-infra#551, 03:45 UTC, 90-day ILM,
credential with no `s3:DeleteObject`). The script **refuses to run** without a recent
successful one — see §9.

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
#         'MERGED': 1, 'NEEDS_MERGE': 0}
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
#    command it would run. NEEDS_MERGE must be 0 and MERGED must be 1.

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
python3 $D/scripts/cairn-cutover.py            # must print P1 … NEEDS_MERGE 0 and
                                               # P5 "already applied", rc 0
```

**Rollback, if step 5 or 6 goes wrong:**

```bash
python3 $D/scripts/cairn-cutover.py --unfreeze --apply          # local disk writable again
python3 $D/scripts/cairn-cutover.py --rollback-push \
    ~/.local/share/cairn-cutover/runs/<timestamp> --apply       # re-PUT the overwritten bytes
```

## 9. The instruments, and how each was validated

Every verdict below comes from something that was shown to be capable of the opposite
answer. A green from an unvalidated instrument is a claim about the instrument.

| instrument | negative control | positive control |
|---|---|---|
| `verify-byte-identity.sh` | one entry mutated by a single character → **FAIL, naming the scope** | identical stores → **PASS**, printing `raw-diff-lines`/`store-root-lines` beside the verdict |
| the backup precondition | absent CronJob, absent `kubectl`, unparseable JSON, unparseable timestamp, and a `lastSuccessfulTime` that is **absent** → all refuse with their own sentence | a fresh timestamp passes and prints the measured age beside the ceiling; the boundary is measured from **both** sides (35.5 h passes, 36.5 h refuses) |
| the write-route probe | a `405 read-only` server → reported as an **operator** problem | a `400 bad-request` server → reported as deployed. The probe body is `{}`, refused by the server's validator *before* any ref is resolved, so it cannot write |
| the freeze | `probe_writable` returns `writable` at 0644 and `refused` at 0444, on the same file, twice | a partially-applied freeze (one file left writable) exits 16 **and** restores every mode bit |
| the mutation sweep | baseline green (67) before any mutant is scored; a known-fatal mutant is killed | 16/16 mutants killed, each by the **named** test; originals restored and re-verified green |

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
4. **Drop the dead aliases** — the 4 LATENT collisions. Each is a one-line edit; each
   becomes live the day its shadowing filename is renamed.
