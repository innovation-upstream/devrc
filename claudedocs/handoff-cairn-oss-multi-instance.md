# Handoff: cairn-oss-multi-instance — 2026-09-06

## Run this first — the index, one command
```bash
cairn recall --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Spin `cairn` out into a public OSS repo that both a personal and a **civitai team**
instance build from, then stand up that second instance so client notes live on client
infrastructure. Decided by the operator over three rounds of questions; the full design
is the PRIVATE proposal, not this doc.
- **closing-condition:** `check` — a SECOND instance exists and serves client notes from
  client infrastructure: a `cairn`-family store reachable at a civitai-side endpoint,
  answering `cairn recall --scope <a civitai scope>` with entries written by a civitai
  session, with the personal instance unaffected. ⚠ **FROZEN — this is the ORIGINAL ask.**
  ✅ **MET 2026-09-18 by phase D** (see `State now`): two entries written by a civitai session,
  byte-identical on the PVC, served to BOTH hosts; personal unaffected **on entry counts** — which
  cannot see the routing change phase D also made, recorded in `State now`.
  🔴 **ONE QUALIFIER ON *THIS CONDITION*, not on the arc (several items remain open — see
  `Next steps`): the route came from a `$CAIRN_ROUTES` OVERRIDE, NOT THE SHIPPED TABLE**, which is
  still all-`personal` by a deliberate tested invariant. Met as WORDED; NOT yet reproducible from
  committed config, since a session without that override gets **rc 11**, correctly. Making it
  durable is **phase E**.

## State now

- ✅ **2026-09-18 — PHASE D IS CLOSED. THE STORE SERVES CLIENT NOTES WRITTEN BY A CIVITAI SESSION,
  BYTE-IDENTICAL, TO BOTH HOSTS. The arc's `## Goal` is MET as worded** (read its qualifier —
  routing is an override, not committed config). Claim `cairn-oss-multi-instance-phase-d` RELEASED.
  - **Written:** scope `civitai-developer-docs`, two entries — `developer-docs-site`
    (`revision=85779666cbc89bd1`, 4,746 B) written this session, and `apps`
    (`revision=232cbe6db29bf18a`) **rescued from the frozen pre-cutover mirror**, where it was
    orphaned in no live store. Both byte-identical against `/data`. 🔴 **A `revision` IS the leading
    16 hex of the file's own sha256** — checkable, not an opaque id.
  - 🔴 **WHY THAT SCOPE, AND WHY IT IS NOT A MIGRATION.** Of the token's **14** allowlisted scopes,
    exactly two (`civitai-developer-docs`, `civitai-app-requests`) were absent from BOTH the routing
    table and the personal store — verified against a positive control — so it has no readers and no
    prior live copy. **A brand-new scope was NOT available:** the allowlist is a snapshot with no
    wildcard, and an unlisted scope reads back byte-identically to one that does not exist.
  - **Durability:** a manual run of the (suspended) backup CronJob over real content —
    `census: scopes=1 entries=2`, `cairn-backups/daily/cairn-20260918T030002Z.tar.gz`, round-trip
    verified AND restore-checked; the #1551 mount fix confirmed live first. **Both CronJobs remain
    suspended** — unsuspending them is an open item.
  - ⚠ **BOTH HOSTS NOW CARRY `instances/civitai.env`** (0600, 281 B, token fingerprint
    `d9904a784be2` on each — matching `civitai/talos-infra:clusters/production/apps/cairn/README.md:343`,
    an independent check that the right row was taken). `len(instances) > 1` is now TRUE on both, and
    it has **two** consequences, not one:
    - cosmetic: every `recall` banners `cairn[personal]:` / `cairn[civitai]:` where it said `cairn:`.
    - 🔴 **BEHAVIOURAL, AND THIS IS THE ONE THAT BITES: A SCOPE THE TABLE DOES NOT NAME NOW REFUSES
      AT rc 11 ON BOTH HOSTS. It used to resolve to `personal`.** `Routing.alias_for` returns
      `DEFAULT_ALIAS` for an unnamed scope **only** `if not self.multi_instance`; at two instances it
      raises `UnroutedScope`. So the FIRST write to any scope not in the 25-row table — a new repo, a
      new subsystem — now fails, and unblocking it is a devrc PR editing `claude/cairn-routes.json`
      plus a `home-manager switch` on both hosts. **Measured 2026-09-18: latent, not firing** — live
      scopes 25, table 25, symmetric difference EMPTY on *both* hosts, so no existing scope is
      affected. It fires on the next NEW one.
      ⚠ Phase D changed the CLIENT's routing behaviour, and "personal unaffected" was measured on
      ENTRY COUNTS, which cannot see that. Both claims are true; they are about different things.
  - ⚠ **STILL ORPHANED: `civitai-app-requests/app-requests.md`** (1,803 B) exists only in the frozen
    mirror. Surfaced by `doctor`'s `personal/token-scopes` PROBLEM, which is **pre-existing** and is
    the check earning its keep. Moving it is a decision, not a cleanup.

- ✅ **2026-09-17/18 — PHASE C IS CLOSED. A SECOND INSTANCE IS DEPLOYED ON CIVITAI PRODUCTION
  INFRASTRUCTURE AND THE RESTORE DRILL PASSED END TO END.** Four PRs, all merged and verified BY
  CONTENT (a squash makes ancestry false forever): `ZacxDev/cairn` **#36** `0d9d3fa` (publish the
  server image), `civitai/talos-infra` **#1542** `40bd630dd` (the manifests), **#1548** `ec5db6fde`
  (backup bucket + credential), **#1551** `6834ac0f2` (the mount fix + gate 23). Claim
  `cairn-oss-multi-instance-phase-c` RELEASED.
  ⚠ **SUPERSEDED 2026-09-18 by the phase-D block above** — its closing "Goal STILL NOT ADDRESSED /
  `scope-absent`" was true when written and is now false. Phase **E** is what remains.

- 🔴 **THE LIVE INSTANCE — the facts a next session needs and should not re-derive.**
  Namespace `cairn` on the civitai production cluster; pod Ready, 0 restarts, on `talos-avt-y6z`;
  PVC `cairn-data` Bound on `linstor-nvme`; Service port **8102**; image **digest-pinned** to
  `ghcr.io/civitai/cairn-store@sha256:171ba281450ea48048ce01358f9f3ea4d001bd3eb8fd89798250c03926f15b08`
  (tag `sha-5d048dd55f1d716d64d2cccfb69b0153eafbd5a4`). Both CronJobs remain **suspended** — until
  phase D seeds, this store has one node and no copy, and that is deliberate.
  **Probe it from inside the pod so the token never leaves it:**
  `kubectl -n cairn exec deploy/cairn -- sh -c 'T=$(cut -d" " -f1 /run/secrets/cairn/token); wget -SqO- --header="Authorization: Bearer $T" http://127.0.0.1:8102/api/v1/recall/<scope>'`
  → `401` without the token, `200` + `X-Store-Status:` with it.

- 🔴 **THE IMAGE PULL NEEDS A PACKAGE-LEVEL GRANT NO API CAN SET.** `cairn-store` has
  `repository: null`, so a classic PAT inherits nothing and `ghcr-cred` 403s until `civitai-deploy`
  is invited with Read **through the GitHub UI** (org → Packages → cairn-store → Package settings);
  done 2026-09-17. 🔴 **A re-mirror under a new name needs it AGAIN, and it presents as
  `ImagePullBackOff`, not as an auth error.** The mirror stays MANUAL — public CI pushing to the
  civitai org would put a client credential in a public repo — and `skopeo copy` by DIGEST is what
  makes "both clusters pull the same image" checkable.

- 🔴 **THE RESTORE DRILL FOUND A STRUCTURAL DEFECT: THE BACKUP COULD NEVER RUN.** `readOnly: true` on
  the **claim reference** made the CSI driver mount the block device `-o ro` while the server held it
  **rw** on the same node; ext4 refuses (`would change RO state`). **`ReadWriteOnce` is NOT the
  cause** — both pods are on one node and RWO is per-node. Fix: `readOnly` belongs on the container's
  `volumeMounts`, never the claim. ⚠ Probable, stated as probable: a LINSTOR block device does not
  tolerate a ro co-mount where a hostpath does. 🔴 **The shape worth keeping: this job could not have
  gone green AT ALL, so alerting on backup success would have fired only once someone depended on it
  — and it ships suspended, so it would have surfaced only after phase D seeded.** Now pinned
  cross-file by gate 23 (`ro-pvc-comount`), watched RED against the pre-fix manifests.
  ✅ **EXERCISED at phase D over real content** — a manual run of the fixed CronJob completed,
  round-trip verified and restore-checked.

- ✅ **THE DRILL PASSED, and "a restore, not a green CronJob" was the closing condition.** Canary
  written → backed up → **destroyed** (store reported `scope-empty`, so the loss was real) →
  restored **byte-identical** (`c4de5e4e…`) and served again. **Drill artifacts removed** —
  re-verified 2026-09-18, `/data` holds only `civitai-developer-docs/`. The whole procedure, the
  `mc cat` extraction, the entry-shape trap, and why `backup.py`'s in-job restore-check cannot
  supply this evidence, are all in
  `civitai/talos-infra:clusters/production/apps/cairn/README.md` — read it there, not here.

- 🔴 **THE BACKUP CREDENTIAL IS SCOPED AND CANNOT DELETE — verified on the live policy, not the
  manifest.** `cairn-backup-write` grants `ListBucket/ListBucketMultipartUploads/GetBucketLocation`
  on `cairn-backups` and `AbortMultipartUpload/GetObject/ListMultipartUploadParts/PutObject` on
  `cairn-backups/*`. **No `s3:DeleteObject`**, one bucket only. ILM `daily/` 90 days. Scoping also
  proven by exercise with a negative control (`mc ls` against another bucket → Access Denied).

- 🔴 **FOUR OPERATOR DECISIONS, 2026-09-17, NOT TO BE RE-LITIGATED.**
  1. **Residency (decision 4) is NO LONGER BINDING** — "don't care". A local copy of client notes is
     now permitted in principle. This removes the strongest objection to mirroring; the others stand.
  2. **The PAT exposure is ACCEPTED**, no rotation. Do not raise it again.
  3. **`civitai-app-playable-collections` → `civitai`; `cairn` → `personal`** (the `kubeclaw`
     precedent in decision 5: tooling you own stays personal even when it touches client work).
     Both were in the live routing table and assigned by NO ledger.
  4. **The expired `DIAGNOSTIC_WINDOW_EXPIRES` on talos-infra trunk: ignore.** Since resolved by
     someone else (`3a9ed6c1e`).

- 🔴 **ROUTING IS DETERMINISTIC ALREADY; THE GAP IS THAT NOTHING CHECKS THE *ASSIGNMENTS*.** Both
  hosts: 25 entries, **all → `personal`** — that half stands. ⚠ **Its "no `instances/` dir" half is
  STALE since phase D**: both hosts now report `instances: personal, civitai`.
  So every scope resolves personally and the civitai server has **no client pointed at it**. The
  two-way pin grades *presence* (is every live scope named? does every named scope exist?) and never
  **"is each scope pointing at the RIGHT instance?"** — which is the axis that decides where durable
  writes land. Evidence it is a real hole: the table holds 25 scopes, §3's ledger covers 23, and the
  two extras were assigned by nobody until decision 3 above.
  🔴 **AND THE PHASE-E CUTOVER ORDER IS LOAD-BEARING AND UNWRITTEN.** `alias_for` row 3 REFUSES a
  table entry naming an alias the host has no config for — **even at one instance**. The two halves
  are asymmetric: the table flip is ONE git commit reaching both hosts at once, while
  `instances/civitai.env` is manual, per-host and untracked. **Config on BOTH hosts first, verify
  `cairn routes` reports two instances on both, and only then flip the table.** Miss one host and
  ~110 entries' worth of scopes refuse on it. §7 step 6 does not state this order.

- ✅ **DECIDED 2026-09-18 — (a) FAIL-LOUD ROUTING + FRESHNESS OBSERVABILITY; mirroring REJECTED.
  This UNBLOCKS PHASE E.** The decision itself lives in `## Gotchas / decisions / dead-ends` (an
  APPEND section, so it outlives this one). **Its NAMED EVIDENCE — three phase-D measurements, each
  one a reason (b) lost:**
  1. **"Which copy is current" is ALREADY answered, automatically, in one line.** With the civitai
     store unreachable and the cache warm, `recall` returns **rc 0** and banners
     `⚠ cairn[civitai]: cached — <url> unreachable: [Errno -2] … — SERVED FROM CACHE, cache 3m old`.
     Reads survive an outage, and the answer carries its own provenance AND age. The operator's
     stated worry — *"not knowing WHICH copy is current"* — is a solved problem in (a), and
     duplication makes it harder, not easier. via: measurement
  2. 🔴 **THE PREMISE THIS ITEM WAS WRITTEN ON IS PARTLY REFUTED. `cairn put` is NOT an
     unconditional rewrite.** `scripts/subsystem-store-api/server.py:233-234` — the SERVER contract,
     in this repo — states `PUT /api/v1/entry/<scope>/<ref>` *"replaces the whole file behind a
     **REQUIRED `If-Match`**; a stale revision is a **412** and the file is untouched"*, and `create`
     refuses an existing ref. So two writers do not silently clobber; the second gets a 412. A merge
     rule is still absent, but the failure it would have to cover is **fail-loud, not silent loss**,
     which removes (b)'s strongest argument. 🔴 **That contract predates the premise it refutes** —
     so the doc asserted an unconditional rewrite while the repo already said otherwise, and the
     check was one `grep` away the whole time. ⚠ Still NOT exercised: no live 412 was provoked.
     ⚠ And the server scopes its attribution guarantee to `POST /bullets` — **a PUT writes the
     caller's bytes verbatim**, so `[cairn: actor/session]` does not self-populate on that path.
     via: code
  3. **A two-instance `cairn doctor` works and reports per-instance** — every `civitai/*` check OK
     (reader-resolution, cache-stamp, pod, cache-vs-pod, token-scopes), caches are siblings
     (`~/.cache/subsystem-store-civitai`), personal untouched. via: measurement
  🔴 **WHAT (a) COMMITS THE NEXT PHASE TO, so nobody re-derives it:** every mechanism (a) needs
  already exists and has been watched working — `UnroutedScope` refusals at **rc 11** carrying a
  remedial message, cache-age labelling, If-Match writes, per-instance `doctor`. Phase E therefore
  adds **no new machinery**; it re-points scopes in `devrc:claude/cairn-routes.json` and replaces
  the all-`personal` invariant with the (a)-shaped one: *every value names an alias this host
  configures*. 🔴 **THE GUARD PHASE E MUST EDIT, NAMED HERE SO IT IS NOT MET AS A SURPRISE RED:**
  `devrc:scripts/tests/test_cairn_routes.py:120`
  `test_every_scope_routes_to_the_default_instance_today`, whose docstring says *"Do not 'finish the
  migration' by editing this file"* and whose assert message states the precondition — a scope may
  be re-pointed only once some host carries `instances/<alias>.env`. **That precondition is now
  satisfied on both hosts.**

- ⚠ **Carried forward (durable — a REPLACE would drop these):** 🔴 **the ROUTING DURABILITY decision
  is in `## Gotchas / decisions / dead-ends`** — an APPEND heading, so it survives a REPLACE without
  anyone re-carrying it; this bullet is itself inside `State now` and is a reminder, not a mechanism.
  The fork decision stands,
  **CONSOLIDATE ONTO THE PIN**, operator 2026-09-08, **not to be re-asked**. `m_index_store` still
  restores `sys.path` on the success path only — closes when a test asserting `sys.path == before`
  after a successful call is shown RED against today's conditional `finally` and GREEN after.
  Rank 4 and rank 26 each retain an open residual on the reading surface; rank 29's body is the
  **contract phase E edits**, not closed history — keep it.
  🔴 **ROLLBACK POINTS, fresh 2026-09-17: workbench generation 778 (roll back to 777), laptop 647
  (roll back to 646).** An aged rollback number is useless for the one job it has — re-derive.

- ⚠ **No clawgate task is recorded for this session and none was invented.**
  `clawgate_handoff.sh resolve` exited **5** (`NOTHING RESOLVED — 0 tasks`), which cannot
  distinguish "this session touched no task" from "the id is wrong". Not a clean bill of health.

- ⚠ **Size: the allowance is `98_304` and the authority is `scripts/lib/handoff_budget.py:62`, never
  a sentence in this doc** — one here read "114,688" (that is `handoff-nix-disk-cleanup.md`'s row)
  until 2026-09-18. The 2026-09-17 cut (devrc #1757 `53c9b981d`) moved 107 KB **verbatim** to
  `claudedocs/refs/cairn-oss-multi-instance.md`. **The ranked list was deliberately NOT rewritten**:
  it predates the `forcing:` requirement, so a rewrite would have meant inventing forcing functions.

## Open investigations — live diagnosis state

### Two ledger guards in cairn are narrower than their own sentences — left OPEN by decision
- **Symptom + exact repro:** read `tests/test_subsystem_store_api.py:20239` and `:20271`
  against their docstrings.
- **Observed (with values):** (a) the fail-closed raise-site walk is
  `if isinstance(exc, ast.Call) and exc.args:` — `raise TokenError`, `raise ValueError()`
  and a bare re-raise are still **silently dropped**, while the docstring says an unreadable
  message is "reported as UNCLAIMED rather than dropped". (b) `_EMITTER_ATTRS` matches
  `.write`/`.writelines` on **any receiver**; `server/server.py:2287` and `:2338` are
  **binary** `fh.write(data)` calls one module-level caller away, so a future module-level
  startup helper that writes would make the ledger demand `reload_safe` on bytes.
- **Ruled out:** that either is a hole in the property the ledger guards — a message-less
  raise cannot echo a field value, and nothing reaches the binary writers today. via: code
- **Leading hypothesis:** both are the same class the whole audit ladder was about (a
  description claiming coverage the body does not provide), one notch smaller, and neither
  ships a defect. Recorded on cairn PR #1 as open-by-decision so they read as open, not absent.
- **Next probe:** none needed. Narrow `_EMITTER_ATTRS` to named sinks (`sys.stdout`/`sys.stderr`)
  and extend the fail-closed arm to non-`Call` raises, in one commit, when someone is next in
  that file.

### Whether the opencode exporter's artifact is USEFUL as receipts — never judged
- **Symptom + exact repro:** not a bug; an unclosed question the shipped work deliberately
  did not answer.
- **Observed (with values):** tool parts carry their payload — **0 of 21,749** tool parts
  store-wide have `text`, and **21,749 of 21,749** have `_data`. A real session exports to 210
  records / 1.13 MB, two runs byte-identical. via: measurement
- **Ruled out:** that `text` alone suffices — measured false at 58× the sample the task
  assumed. via: measurement
- **Leading hypothesis:** `_data` carries enough, but nobody has read an artifact end to end
  and said so.
- **Next probe:** export one real session and read it. That is a human judgement over named
  evidence, not a command.

### EVICTED 2026-09-14 — the cairn full-suite intermittent and the nine-round ladder (CLOSED)
🔴 **CLOSED and MERGED as `ZacxDev/cairn` #3 `8e4ef84`**; the intermittent is recorded as NOT
REPRODUCING and is **not** claimed fixed. Block evicted for size to pay for rank 29, per the
2026-09-07 convention; it had already superseded an earlier 2026-09-13 eviction — do not
resurrect either from git history and re-derive its probes.
🔴 **The lesson that survives, and it is about EVIDENCE not rate:** ≈43 runs with 1 failure
closed **nothing**, because that one failure has no traceback and never will — it was read
through `pytest -q | tail -1`, so the transcript holds only the short summary and which of
three branches fired is unknowable. **A run count cannot substitute for a captured failure.**
Four residuals were left open **in the code** (an irreducible over-credit inside `send_signal`;
a redundant `was_running` whose mutant survives; a suite-level property no test in the suite
can assert about itself, `filterwarnings = error` weighed and REJECTED; and historical comment
figures no round can re-check) — read them at `8e4ef84`, not here. **Next probe: none.**

### EVICTED 2026-09-16 — the OSS client fork AND the cost of consolidating (rank 3, DELIVERED, #1508 `44bd8b0e`)
🔴 **Two lessons outlive these, and nothing else does.** (a) **A raw line count of a file under
active edit RESTALES INSIDE ITS OWN PR** — the figures were falsified by a rebase before they
merged, and nothing asserts on a count in prose, so no test can ever catch one; derive it and
name the rev, or do not quote it. (b) **The METHOD that made the fork diffable:** render both
copies docstring-free before diffing, and watch BOTH controls (a file against itself → 0 lines;
one renamed identifier → 4) — an instrument that cannot go red, and cannot see a rename, returns
the same reassuring number. Full text is in `claudedocs/refs/cairn-oss-multi-instance.md`. (The
decision — CONSOLIDATE ONTO THE PIN, 2026-09-08 — is in `State now`, not to be re-asked.)
**Next probe: none.**

### EVICTED — the SECOND cairn intermittent (CLOSED, `ZacxDev/cairn` #5 `9213726`)
🔴 **The lesson:** the failing assertion was a test's POSITIVE CONTROL ABOUT ITSELF and was RIGHT
to refuse — one shared budget bounded both the samplers and the reload driver, so the deadline
could starve the control the test existed to police. The fix reads `ATOMICITY_MIN_RELOADS` in
BOTH the loop and the assertion so they cannot drift, **and gates the SAMPLERS on it too** —
gating only the driver satisfies the minimum after every observer has stopped, which makes the
verdict vacuous. **Next probe: none.**

### EVICTED — rank 12, leakscan's coverage was an enumeration (CLOSED, `ZacxDev/cairn` #6 `9d58f02`)
🔴 **The lesson:** a scanner whose coverage is a hand-written suffix ENUMERATION prints
`0 findings across N file(s)` where N is files SCANNED, never files present — so nothing in the
output distinguishes *clean* from *did not look*. The fix was to DERIVE coverage
(`partition_tracked_files()` buckets every enumerated file, so `scanned | skipped` equals the
enumeration by construction). Full block — suffix census, 8/8 mutation battery, regression matrix
— is on the PR. **Next probe: none.**

### `cairn validate` is NO LONGER SILENT, and it is STILL NOT the write-protocol check — the CLASS is open
🔴 **THE LIVE IMPERATIVE: the mandated post-write check stays pointed at `cairn-validate`, the
launcher — never at the packaged client's `cairn validate`.** This doc's own `State now` still
records *"this verb is the mandated post-write check"*, and **that clause is false**; acting on
it routes the check back at a client that does not run it and re-opens the 🔴 that #1406's
round-1 audit closed. Fix the sentence wherever it appears.
- **The correction history, because it is the point and not incidental.** First reading
  (2026-09-08): the packaged `validate` printed **nothing** — rc 0, 76 B, **0 of 4** contract
  blocks, against the in-repo fork's 5,842 B. cairn **#11** then made it print a parse count, so
  the second reading (2026-09-09, pin `cairn-c84c142`) is **56 B, rc 0, 0 of 3 blocks** against
  the launcher's 6,119 B and **3 of 3**. 🔴 **#11 removed the SILENCE, not the BLINDNESS** — the
  `dropped lines:` advisory, whose non-zero means content is **ALREADY LOST**, still never runs
  on the packaged client, and neither do the other two. **A check that was silent becoming one
  that looks like it worked is strictly harder to notice**, which is why the sentence matters
  more than the bug would.
- **Ruled out: that the differing totals (33 vs 32) indicate a defect.** The packaged client
  syncs live; the launcher reads the local cache. Different stores. via: measurement
- **Ruled out: that the exit-code change 3 → 5 is a regression.** `3` is
  `EXIT_UNREACHABLE_NO_CACHE` and `5` is `EXIT_CORRUPT` in the client's own table — the fork was
  leaking the writer's namespace and the packaged code is more coherent. via: code
- 🔴 **WHAT IS NOT RESOLVED IS THE CLASS, AND IT IS RANK 16.** An audit measured **5 of 6** verbs
  byte-identical to the fork, so `validate` was the only diverging verb TODAY and **nothing in
  devrc's gate would notice the next one.**
- **Next probe:** none for `validate` itself. For the class, run rank 16's check —
  `nix build github:ZacxDev/cairn/<rev>#cairn`, exercise each verb against a fixture cache and
  diff against `scripts/cairn`. **If someone wants ONE binary again, the closing condition is
  the packaged `validate` emitting all three blocks — measure it, do not read a changelog.**
- Both superseded narratives moved verbatim to
  `claudedocs/refs/cairn-oss-multi-instance.md` § DEMOTED 2026-09-17 (pass 2).

### EVICTED — the client's missing FOCUS WINDOW (CLOSED, `ZacxDev/cairn` #9 `a3c84db`)
🔴 **Two lessons.** (a) The client's fallback asserted *"no handoff doc to read a path window
from"* when the doc was right there — **a fallback that explains itself is making a claim about
the world, and that claim can be false.** (b) This block's "next probe" named a `/tmp` log:
**a next probe pointing into `/tmp` expires silently and reads as actionable forever.** Closure
verified two ways, neither ancestry — `focus_window`/`focus_paths`/`focus_source` occur 3× in
`origin/main:cairn` against a control of 3, and a live `recall` prints `resolved via claudedocs/…`
rather than the `most-recent fallback` that was the symptom. **Next probe: none.**

### EVICTED 2026-09-14 — the three reds only the MERGED tree could find (CLOSED)
🔴 **Fixed in `29f16402`, gate green on `48bb44e3` from two runners.** Evicted for size per the
2026-09-07 convention. **The three lessons, which is all that outlives it:** (a) a SEAM LEDGER
that fails when the router set GROWS as well as shrinks is doing its job, not obstructing —
a new reader must not quietly start answering "where do I read?" for itself; (b) a guard can
be STRUCTURALLY INCAPABLE of passing in one of two tiers and dev-host green is what hides it —
an assertion on STDOUT broke where the sandbox `$HOME` has no cache and the tool takes its
not-found path, printing to STDERR; the implementing round wrote *"I believe they are
sandbox-safe, but that is reasoning, not a measurement"*, and it was wrong; (c) a scan hit is
fixed by pinning a RELATIONSHIP, not by allowlisting a string. **Next probe: none.**

### EVICTED 2026-09-14 — round 1 and round 3's guards, the mutation evidence (CLOSED)
🔴 **Every matrix is on PR #1406's round-2 and round-3 comments** — read them there. Evicted for
size to pay for rank 29. **The three transferable shapes, kept because each cost a round:**
(a) a decoy must carry the STRING the assertion looks for — `pkgs.hello` alone was killed, but
`pkgs.hello` **plus** a second binding naming the real package SURVIVED while the deployed
symlink was dangling (home-manager's `insertFileEntry` `ln -s`s unconditionally, so a broken
pin BUILDS); (b) a bracket walk that never returns to depth 0 silently widens `header` to the
WHOLE FILE, and a symbol occurring twice in the body then passes the assertion vacuously — a
false RED fixed into a path to a false GREEN; (c) the round-3 mutant that SURVIVED was
**unreachable**, not wrong: an earlier fixture closed correctly so the branch never ran, and
only a case no earlier assertion rejects killed it. **Next probe: none.**

### STILL OPEN by decision — five round-2 🟡s the operator chose not to block the merge on
- 🟡2 `count == 1` false-reds two legal nix spellings, and a one-line
  `{ cairnPackage = real; } // { cairnPackage = pkgs.hello; }` override **still walks it**
  (8 passed, deploying `${pkgs.hello}/bin/cairn`). The multi-line form IS killed.
- 🟡5 `SECRETS.md:26` states the pinned package **is** the deployed client and `scripts/cairn`
  is "no longer deployed" — false until both hosts switch. Same false tense at
  `claude/skills/cairn/SKILL.md:89-92`.
- 🟡7 `claude/skills/subsystem-index/SKILL.md` is **41,591 B** against `HARD = 40_960`
  (`scripts/skill-audit.py:168`). Round 3 cut 678 B; under the cap is **arithmetically
  unreachable** from round 1's block alone — the file was 31 B over before round 1 touched it.
  🔴 `HARD` is exercised only against tmp fixtures, **never against the tree**, so no gate
  will ever go red on this. ⚠ Merge-brought: `claude/skills/handoff/SKILL.md` sits at
  **34 B of headroom** against an enforced ratchet — the next commit to touch it reds a gate
  that will blame the wrong change.
- 🟡8 the pinned package's own `🔴 MALFORMED —` remedy prints ``check a file with `a writer
  --validate <path>` `` (the extraction scrub). Lives in `ZacxDev/cairn`, not devrc.
- 🟢 the prose pin cannot distinguish "mandated" from "merely mentioned" — demoting the
  command to an aside **while keeping the full string intact** survives it.
- **Next probe:** these are rank 18's successors. None blocks anything today.

### CLOSED 2026-09-09 — the CI intermittent is `SERVER_BLOCKED_IN_FSYNC`, named by the instrument built for it
🔴 **THIS SUPERSEDES BOTH EARLIER READINGS IN THIS DOC** — "attributed to the TIER, not the
tree, root cause unknown", and the block that offered the missing cairn-#3 port race as a
"plausible contributor". **The port race is NOT the mechanism. That hypothesis is RETRACTED**;
it remains a real unfixed gap on its own merits, and nothing more. Both superseded blocks were
EVICTED 2026-09-13; the two facts worth carrying out of them are here:
- 🔴 **THE UNFIXED GAP, CARRIED FORWARD: devrc's fork never received cairn #3.**
  `scripts/tests/test_subsystem_store_api.py`'s `_free_port()` is the **pre-#3** version —
  binds port 0, reads the number, closes the socket, returns, with **no retry, no
  `SPAWN_ATTEMPTS`, no `_lost_the_port_race`**. `ZacxDev/cairn` closed that TOCTOU in **#3
  (`8e4ef84`)**. Its known signature is EADDRINUSE surfacing as connection *refused*, which is
  NOT this failure (an established connection that never answers), so it is a gap to close on
  its own merits and **not** a diagnosis. via: code
- **The earlier tier attribution stands as an attribution:** #1417 changed exactly one markdown
  file and failed on the identical assertion, so the failure is in the tier, not the tree.
  via: measurement
- **Symptom + exact repro:** `TestARefusedWriteIsIndistinguishableFromAnAbsentOne::test_POSITIVE_CONTROL_the_APPEND_comparison_CAN_see_the_difference`
  fails in the Tekton `pytests` tier. **Four occurrences**: #1406 at `f98be263`, #1417
  (docs-only), #1425 at `f3bdca9e`, #1425 at `9a5b883d`.
- 🔴 **THE VERDICT, IDENTICAL IN BOTH LOGS I PULLED BEFORE THE PRUNER TOOK THEM:**
  `MECHANISM = SERVER_BLOCKED_IN_FSYNC (handler threads=1 [Thread-815
  (process_request_thread)=SERVER_BLOCKED_IN_FSYNC], accept loop parked=True)`.
  via: measurement
- 🔴 **THE INSTRUMENT ALREADY EXISTED AND NOBODY HAD READ ITS OUTPUT.**
  `_why_the_server_did_not_answer()` (`scripts/tests/test_subsystem_store_api.py:460`) emits
  that `MECHANISM =` line *precisely* so a CI log can be grepped for it without a human
  reading stacks — its own docstring says the store-api hang "stayed open for weeks" because
  **a client-side read timeout is the observable the most mechanisms share, so on its own it
  identifies none of them.** Three occurrences were spent re-deriving that ambiguity. **Grep
  the log for `MECHANISM =` FIRST.** via: code
- **The mechanism, from that docstring:** `server.py:_replace_bytes` issues **two** `fsync`s —
  the file, then the parent directory — **inside the request and before the response is
  written**. `fsync` blocks in uninterruptible D-state, is bounded by nothing, and **burns no
  CPU**. The handler's `timeout = 15` does not bound it: that is a SOCKET timeout and does not
  reach a syscall. So the write path stalls on disk and the client's read times out.
- 🔴 **Ruled out: general CPU load — and the ruling-out is CONSISTENT with the mechanism, not
  in tension with it.** Wall-time discriminator, CI-to-CI: the failing runs' `scripts/tests`
  took **818.69 s** and **1091.87 s**, and `scripts/collector/tests` **11.21 s** and
  **14.27 s** — *faster* than the dev host that passed (1181.72 s / 36.03 s). Nothing was
  inflated. That is exactly what an `fsync` stall looks like: it consumes no CPU, so it cannot
  appear in a CPU-shaped measurement. via: measurement
- 🔴 **Ruled out: that it is caused by any diff.** #1417 changed **exactly one markdown file**
  and failed identically. via: measurement
- **Precondition corroborated:** the cluster is saturated. `talos-xr6-r7p` — the single node
  both pipelines `nodeSelector`-pin to — is emitting `Insufficient cpu`, `FailedScheduling`
  and `Preempted`, and **`main`'s OWN gate is `KILLED`** (`the gate pod died at or after step
  pytests`). Disk contention on that node is the load this test cannot tolerate.
  via: measurement
- ⚠ **NOT established:** the disk-level numbers. I did not measure `talos-xr6-r7p`'s device
  utilisation or PSI-io at the moment of failure, so "disk contention" is inferred from the
  fsync park plus the node's scheduling state, not read off a disk metric.
- **Next probe — and it is NOT a re-run.** Three options, none of them "run it again":
  (a) bound the write path so a stalled `fsync` fails fast instead of hanging past the client
  timeout; (b) raise this test's client timeout, which trades a red gate for a slow one and
  does not make the server correct; (c) unpin the CI pipelines from one node so the disk is
  not shared. 🔴 **Re-running to green is what `claude/RULES.md` calls training everyone to
  click through, and with `enforce_admins: true` on devrc a permanently-red required check
  blocks everyone.** Whichever is chosen, `MECHANISM =` is now the first thing to grep.

### EVICTED 2026-09-17 — `test_check_sops_enc_payloads.py`'s single `homelab-infra` CI failure (CLOSED, no repro)
One failure observed by a round-2 audit of `ZacxDev/homelab-infra#786` at head `993643baa`;
**52 of 52 in six separate full-suite runs** on this host, including at that same head. Leading
hypothesis: contention between two concurrent full-suite runs (the audit's ran 18:50–19:33Z
against mine 19:00–19:32Z), which is a documented evidence-corruption shape on this box.
🔴 **If it recurs, capture whether another suite was running at that instant BEFORE re-running
anything** — that observation is the only thing that separates the two mechanisms, and it is
unrecoverable afterwards. Body moved verbatim to
`claudedocs/refs/cairn-oss-multi-instance.md` § DEMOTED 2026-09-17. **Next probe: none.**

### EVICTED 2026-09-17 — both of `#1508`'s round-1 blockers came from measuring the environment in the wrong shell (CLOSED)
🔴 **Every environment claim in `#1508`'s body was measured from a shell that has `cairn` on
PATH, and the three environments that decide whether this repo's SCHEDULED work runs do not** —
which is how two deploy-blockers sat under `collected=22153 failed=0` plus three green Tekton
legs. The fix is `CAIRN_LIB=${cairnPackage}/libexec/cairn/lib` in each unit's `Environment`, NOT
a widened PATH (`analyze-service-index-backup.service` sets `ProtectHome=tmpfs`, so
`%h/.local/bin` does not exist inside its namespace). 🔴 **The durable check: when a change adds
a hard import-time requirement, enumerate every SCHEDULED consumer (systemd unit, cron,
container ENTRYPOINT) and re-run the import under that consumer's OWN environment** — read the
PATH from `systemctl --user show <unit> -p Environment`, never from `nix/home.nix` and never
from your own shell. Body moved verbatim to
`claudedocs/refs/cairn-oss-multi-instance.md` § DEMOTED 2026-09-17. **Next probe: none.**

### `--emit-claims`, and why a delta round can be structurally impossible
- **Symptom + exact repro:** `audit-dispatch.py <pr> --round 2` REFUSES when no parseable
  `audit-claims` block exists on the PR.
- **Observed:** round 1 produced no block because `--emit-claims` was never run, so round 2 had
  to be unblocked by posting one by hand. 🔴 It must be an **ISSUE** comment:
  `gh pr view --json comments` does not return REVIEW comments, so a block posted as a review is
  invisible to the script while looking perfectly present to a human. via: command
- **Next probe:** none. Run `--round N --emit-claims --audited <the tip that round READ>` as part
  of closing every round, not as a separate remembered step.

### What four audit rounds actually caught — the ONE 🔴 class, and the ten sentences
🔴 Keep this: it is the argument for running the ladder at all, and for what to point it at.
- **Symptom + exact repro:** not a bug — the record of an audit ladder on a 47-file, −7,493-line
  consolidation that was gate-green when the ladder started.
- **Observed (with values):** round 1 returned **2 🔴 + 7 🟡**; rounds 2/3/4 returned
  **0 🔴 and 0 logic defects**, with 6, 4 and 3 findings respectively, essentially all prose or
  rendered strings. Round 4 could not falsify any claim the delta made about code. via: measurement
- 🔴 **Ruled out: that a green suite bounds the risk.** Both round-1 🔴s shipped under
  `collected=22153 failed=0` plus three green Tekton legs. The root cause was one sentence:
  **every environment claim in the PR body was measured from a shell that has `cairn` on PATH,
  and the three environments that decide whether this repo's SCHEDULED work runs do not.**
  Three systemd units set a CLOSED `Environment=PATH=` with no `cairn` in it and `ExecStart` the
  WORKING-TREE copy — so the break lands on `git pull`, before any `home-manager switch`.
  via: measurement
- 🔴 **Ruled out: that the guard covering that environment was doing so.**
  `_unit_shaped_env` carried the docstring *"`env -i` plus exactly what nix/home.nix sets … NOT
  `dict(os.environ)`"* over a body reading `{"PATH": os.environ["PATH"], …}` — the one dimension
  that decided the outcome was a pass-through. **A description wider than its implementation, on
  the only probe claiming to model that environment.** via: code
- **Leading hypothesis, and it held for three rounds:** once the logic is right, the remaining
  defects are the SENTENCES the fixes write about themselves. Worked examples: a comment naming a
  mutation failure mode that **cannot occur** (the sentinel is structurally unable to arrive once
  the patch is deleted — that absence IS the mechanism); a retraction that fixed one blanket claim
  and left its sibling seventeen lines up; a `grep` figure invalidated by the very edit that
  asserted it; a rendered page whose headline number contradicted the paragraph beneath it.
- **Next probe:** none. Scope a late round to SENTENCES explicitly — round 3 was, and it worked.

### The audit range is WRONG at every round on a rebased branch, and both auditors caught it
- **Symptom + exact repro:** `audit-dispatch.py --round N` derives its range from the
  previously-audited tip. On a branch rebased between rounds that tip is **not an ancestor** of
  the head, so the range silently spans main's movement.
- **Observed (with values):** round 2's literal range would have attributed **41 files / +3,803
  lines** to a round whose true delta was **14 / +645**; round 4's would have pulled
  `claude/skills/activity/SKILL.md` in. Counterparts were resolved BY SUBJECT and verified with
  `merge-base --is-ancestor`: `acc9ee6a`→`bfbebcff`, `0a331066`→`e992dba4`,
  `15b17ae3`→`c0a55a32`. via: measurement
- 🔴 **Ruled out: that the author's own ancestry check settles it.** The implementer reported
  "ancestry checked, not assumed → YES" for `15b17ae3`; measured against the real PR head it was
  **false** — it had rebased again after checking. Its NUMBERS were right, only the ancestry
  statement was stale. via: measurement
- **Next probe:** resolve the counterpart by subject and verify with `merge-base --is-ancestor`
  against the **PR head**, not against a worktree HEAD, every round.

### EVICTED 2026-09-17 — the `FAILING:` line is a 140-char status description, not a failure list (CLOSED)
`gh pr checks <n>` truncates it. On `#1525` it named ONE test while the same line's own counts
gave **2 failed**, with the second name cut mid-token. Three bites in one session: it hid a live
failure; it is why `handoff-gate-flake-store-api.md` rank 7's closing condition must **not** key
on "no test appears in a `FAILING:` line" (a rename, skip or deselect satisfies that with nothing
fixed); and it produced a regression in another session's PR (`#1522`'s `480b014f` removed two
correct ledger rows on the premise "a mention that does not exist" — reasonable against a
truncated line, false against the file). 🔴 **Read the file, not the status line.** Body moved
verbatim to `claudedocs/refs/cairn-oss-multi-instance.md` § DEMOTED 2026-09-17.
**Next probe: none.**

### EVICTED 2026-09-17 — `#1522`'s double kill-guard red (CLOSED; #1522 MERGED 2026-09-12 `05:50:05Z`)
🔴 **THE ONE LIVE CONSTRAINT, AND IT BINDS ANYONE EDITING THIS DOC: the wide-kill verb is ELIDED
here on purpose, and elision ALONE was not enough.** An earlier revision quoted it literally and
made THIS doc an offender, red on `origin/main`; eliding it dropped 2 failures to 1 and the doc
**still** matched at two sites written by OTHER sessions documenting the same breakage. So this
doc IS ledgered in **both** allowlists (`_KILL_MENTION_LEDGER` and `quoting_is_the_point`), and
the elision stays as the cheap half — **do not add the sixth, seventh and eighth mention while
the row already covers you.** The class itself was closed structurally by devrc #1561
(`c0bbd6d9`, `_PROSE_ONLY_PREFIXES = ("claudedocs/",)`), which is what retired this block's own
next probe. Body moved verbatim to `claudedocs/refs/cairn-oss-multi-instance.md` § DEMOTED
2026-09-17 (pass 2). **Next probe: none.**

### EVICTED 2026-09-14 — the kill-ledger treadmill (CLOSED)
🔴 **Closed STRUCTURALLY by devrc #1561 (`c0bbd6d9`)**: `_PROSE_ONLY_PREFIXES = ("claudedocs/",)`
makes the scanners skip tracked prose. Block evicted for size per the 2026-09-07 convention.
**The lesson that survives:** three PRs of per-instance classification were the WRONG ALTITUDE,
and the design fix landed while they were still being written. When you find yourself writing
the third PR that classifies instances of one class, the class itself is the bug.
**Next probe: none.**

### An unexplained backup archive at 2026-09-17T21:03:27Z that no surviving Job accounts for
- as-of: 2026-09-17
- **Symptom + exact repro:** `mc ls --recursive nvme/cairn-backups/` lists TWO archives —
  `cairn-20260917T210327Z.tar.gz` and my drill's `cairn-20260917T232209Z.tar.gz`. Only the second
  is accounted for.
- **Observed (with values):** `kubectl -n cairn get jobs` shows exactly one job,
  `cairn-backup-drill-2` (mine, started 23:22:05Z). Both CronJobs are `suspend: true`. My first
  drill job `cairn-backup-drill-1` never mounted (20 × `FailedMount`) and I deleted it. So the
  21:03 archive was written by something whose Job object no longer exists. via: measurement
- **Ruled out: that a CronJob fired on schedule.** Both are suspended and the schedules are
  `45 3 * * *` / `0 4 * * *`, neither near 21:03. via: measurement
- **Ruled out: that drill-1 eventually succeeded.** It never mounted; its pod stayed `Pending`
  through 20 FailedMount events and was deleted while still Pending. via: measurement
- **Leading hypothesis:** the implementing subagent tested its own mount fix with a hand-patched
  Job and cleaned it up, and its report omitted saying so. That would *also* mean the fix WAS
  exercised before I merged it — which its report did not claim. Inference, not measurement.
- **Next probe:** ask that agent directly, or read its transcript for a `kubectl create job`
  between 20:31 and 21:05. If neither shows it, the open question is what else can write to
  `cairn-backups` — the credential is `PutObject`-capable and lives in the namespace.

## Next steps (ranked)

🔴 Numbering is STABLE and is half a claim's identity (`claim-work --slug-for <this doc>
<rank>`). Items are marked done IN PLACE; new items APPEND.

🔴 **CLOSED-AND-DEMOTED LEDGER — ranks 1, 2, 3, 4, 12, 23, 24 and 26.** Their bodies moved
verbatim 2026-09-17 to `claudedocs/refs/cairn-oss-multi-instance.md` § DEMOTED 2026-09-17.
**Numbering is UNCHANGED and the gaps below are deliberate** — a `claim-work --slug-for` slug
still resolves to the same rank. Merge shas, so a reader can still find each one:
**1** `ZacxDev/cairn` made public (no PR) · **2** `ZacxDev/homelab-infra#714` `ed2c4a0db` ·
**3** `ZacxDev/cairn#4` `218b6c1` + devrc #1381 `baa664e4` / #1406 `9300f234` / #1508 `44bd8b0e` ·
**4** `civitai/talos-infra#1414` `c9b1c4e03` · **12** `ZacxDev/cairn#6` `9d58f02` ·
**23** devrc #1583 `c1ecc830` + `ZacxDev/cairn#17` `a2661371` · **24** devrc #1621 `df09a6c2` ·
**26** devrc #1657 `0808a820`.
🔴 **PASS 3, 2026-09-18 — ranks 7, 13, 15, 16, 17, 19 and 22 joined them**, bodies moved VERBATIM to
that same refs file § DEMOTED 2026-09-18 (pass 3), each stamped with its own sha256 and verified
byte-identical there against a case-mutant control. **Those seven keep an in-place pointer AND their
durable lesson** — read the lesson here, the narrative there. Recovered 8,231 B, which is what made
room for phase D; the doc had 159 B of headroom before this pass.

**What did NOT close with them, kept HERE on purpose — do not read the ledger line as "all
done":**
- **From rank 4 — `ZacxDev/cairn` still owes `CONTRIBUTING.md` + issues enabled.** **Closes
  when** both exist on `origin/main` and the file names the leak gate (`tests/leakscan.py`) and
  the test command — land it BEFORE advertising the repo, so a first contributor meets a
  documented gate rather than a surprising one. 🔴 And the scrubbed client subdomain is still in
  this repo's **REACHABLE HISTORY** (three commits); the content gates enumerate `git ls-files`
  and are structurally blind to history, so the scrub stopped the leak GROWING and did not remove
  it. **Rewrite-vs-accept is an operator call and has not been made.**
- **From rank 26 — the #1657 ledger pins the hook against the nix DECLARATION, and a declaration
  is not an EXPORT.** Both consumers existence-guard (`exportIf "-d"`/`"-f"`). Measured:
  `~/.kube/homelab-nebula.yaml` is absent, `$KC_NEBULA` is UNSET, and the hook nudges
  `KUBECONFIG=$KC_NEBULA` anyway. Pre-existing, not introduced by #1657. **Closes when** the hook
  resolves paths from `os.environ` and a test shows it emitting NO suggestion for a
  declared-but-unexported handle, RED before and GREEN after.
- **From rank 3, two live imperatives that outlive the closed item:** `cairn-who` and
  `cairn-validate` KEEP `mkOutOfStoreSymlink` — only `cairn` moved into the store, both are
  devrc-only and absent from the OSS package, so **do not "tidy" the deploy modes into agreement
  in either direction.** And every `cairn who` spelling in this doc, in
  `handoff-cairn-task-linkage.md` and in `proposal-cairn-session-capture.md` is the DEAD spelling
  and exits 2 — **do not copy a command out of them.**

5. ✅ **DONE 2026-09-06 — `ZacxDev/cairn` #2, `c8aee7203`.**
   forcing: none — done

6. ✅ **DONE AND MERGED 2026-09-07 — `ZacxDev/cairn` #3, `8e4ef84`.**
   forcing: none — done

7. ✅ **CLOSED 2026-09-10 — `ZacxDev/homelab-infra` #787 `936692ec7`.** Body demoted.
   🔴 **Lesson:** which behaviour an image has is answered by the RUNNING CONTAINER, never by a
   comment's age.
   forcing: none — done

8. **Session capture — DESIGNED AND DECIDED, NOT BUILT.**
   `claudedocs/proposal-cairn-session-capture.md`, `e16f9609a`. Read §10 first.
   **Closing condition:** none yet — the first implementation PR would earn one.
   forcing: none

9. ✅ **DONE 2026-09-06 — clawgate #511, devrc `f58d2df04`.** ⚠ The module has NO CALLER.
   forcing: none — done

10. ✅ **DONE AND MERGED 2026-09-08 — `ZacxDev/cairn` #5, `9213726`.**
    forcing: none — done

11. 🔴 **OPERATOR ACTION — add a `cairn` scope to the store token's allowlist.**
    **RE-VERIFIED LIVE 2026-09-08, still refused:** `cairn create --scope cairn --ref
    rank11-probe --file <f>` → **rc 6**, `🔴 cairn: the store REFUSED the write [not-found]`.
    Nothing was written. The local cache still holds **23** scopes with `cairn` absent, and
    `subsystem_recall.py --repo ~/workspace/cairn` reports `status=scope-absent`.
    ⚠ This is what made THIS session's `/handoff` step 4 dead-end: the rank-12 lessons could
    not be recorded under a `cairn` scope and live in this doc's Gotchas instead — the exact
    "cairn-repo lessons keep landing elsewhere" cost this item names.
    ⚠ Note the invocation: `--file` is REQUIRED, and omitting it exits **2** (argparse) —
    which is NOT the refusal and must not be read as one.
    🔨 **DECIDED AND IN A PR 2026-09-09 — `ZacxDev/homelab-infra` #785**, `tekton/gitops-validate`
    **pass**. Adds `cairn` to the token's scope allowlist: 23 → 24 scopes, all 23 originals
    still present (sorted set difference `removed: []` / `added: [cairn]`), decrypted
    before/after diff a SINGLE line. Live pod and the tracked secret agreed on the before
    state, so this was not a git-only claim.
    ⚠ **A `sops` trap worth keeping:** `sops` resolves `.sops.yaml` from the INVOKING CWD, not
    from the file path. Run from another checkout it loads that repo's rules and dies with
    `no matching creation rules found` on a file this repo's catch-all covers perfectly well.
    Pin it with `--config`, do not `cd`.
    ✅ **CLOSED 2026-09-10 — CONDITION EXERCISED, NOT INFERRED.** After #785 merged and
    the pod rolled, `cairn create --scope cairn --ref ci-leg --file <f>` returned
    `created scope=cairn ref=ci-leg revision=dc4d8212`, **rc 0** — it had returned rc 6
    `[not-found]` for this item's entire life. The rc was CAPTURED, not piped (a pipe
    returns `tail`'s status and reads a refusal as a write). The scope now holds a real
    first entry, verified round-tripping from the pod: `1 of 1 entry in cairn/`.
    forcing: none — done

13. ✅ **CLOSED 2026-09-10 — `ZacxDev/cairn` #8 `3167e44`; published `…:0.8.0`.** Body demoted.
    🔴 **Three lessons, each a SHAPE, not a fact about #8:** (a) a raw diff cannot characterise an
    EXTRACTION — strip comments+docstrings and diff the executable token stream, with a
    file-against-itself control; (b) a publish control must assert the BEHAVIOUR, not just the
    artefact — an image with no filesystem reports the same reassuring zero; (c) 🔴 **never probe
    harbor with `docker manifest inspect` from this host** — it reported the LIVE, currently-deployed
    tag ABSENT, so the reassuring `safe to publish` beside it carried NO information.
    forcing: none — done

14. ✅ **DONE AND MERGED 2026-09-08 — `ZacxDev/cairn` #5, `9213726`** (same PR as rank 10).
    forcing: gate — it turned the public repo's only CI gate red on 2 of its first 26 runs

15. ✅ **DONE AND LIVE 2026-09-09 — `ZacxDev/cairn` #7 `059ec17`, reaching this host via devrc
    #1433 and a `home-manager switch`.** Body demoted.
    forcing: none — done
16. ✅ **DONE — devrc #1433 `4a362c8d`.** `checks.cairn-client-runs` builds the pinned package and
    RUNS it; closing condition exercised, and RED with the client stubbed to `exit 0`. Body demoted.
    🔴 **What it found on its FIRST run, which is why the item was worth doing:** the pinned client's
    `validate` printed NOTHING on a clean store and exited 0 — and that verb is the post-write check
    the index protocol MANDATES, so every store write validated by the packaged client was passing
    vacuously. **cairn's OWN CI did not catch it** (1709 tests green while the verb was inert). That
    is the empirical answer to "won't upstream catch a broken client": no, it did not.
    ⚠ It is an OUTPUT, not yet a GATE — see rank 20.
    forcing: gate

17. ✅ **DONE AND MERGED 2026-09-09 — `ZacxDev/cairn` #10 `934ec38e`.** Body demoted.
    ⚠ **Lesson:** a first draft explained the history by NAMING the removed symbols, which fixed the
    defect while making the mechanical check report it UNFIXED — a false negative manufactured by
    the fix. The explanation survives without the spelling.
    forcing: none — done
18. **Three deferred findings from #1406's round-1 audit, none blocking.** (a)
    `nix/sessionVariables.nix` hardcodes `.claude/analyze-service-index`, a SECOND `.nix`
    spelling of `subsystem_touch.DEFAULT_STORE_ROOT`, which `test_store_root_ledger.py`
    structurally cannot see because its own residuals section puts `.nix` out of scope. (b)
    The packaged `lib/host_identity.py` honours `CAIRN_HOST`; devrc's copy does not — dormant
    today (nothing sets it), and it would make `cairn recall`'s host banner disagree with the
    writer's. (c) `claude/skills/cairn/SKILL.md`'s "consolidated in a later slice" names no
    owner and no mechanism. (a) and (b) both disappear if rank 3 slice 3 lands.
    🔴 **THAT LAST SENTENCE IS HALF WRONG, MEASURED AT `origin/main` AFTER SLICE 3 MERGED
    (2026-09-12).** Slice 3 was this item's stated closing condition, so the item would have been
    closed unread. Re-measured:
    - **(b) IS closed.** `scripts/lib/host_identity.py` is ABSENT — devrc deleted its copy, so the
      only `host_identity` in play is the packaged one that honours `CAIRN_HOST`. The two copies
      can no longer disagree because there is only one copy.
    - **(a) IS NOT closed, and slice 3 could not have closed it.**
      `nix/sessionVariables.nix:36` still reads
      `CAIRN_MIRROR_ROOT = "${homePath}/.claude/analyze-service-index"`. Slice 3 deleted duplicated
      PYTHON modules; this is a `.nix` literal — a different surface — and
      `test_store_root_ledger.py` still cannot see it for the reason this item already gives.
      **A closing condition that names another PR closes only what that PR's diff actually
      touched**, which is not what "both disappear if X lands" predicted.
    **Closing condition (revised):** a PR that addresses (a) and (c) explicitly; (b) is done.
    forcing: none


19. ✅ **DONE, MERGED AND LIVE 2026-09-09 — `ZacxDev/cairn` #9 `a3c84db1`.** Body demoted.
    🔴 **Lesson:** the client's fallback asserted *"no handoff doc to read a path window from"* when
    the doc was right there — **a fallback that explains itself is making a claim about the world,
    and that claim can be false.**
    forcing: none — done
20. **Wire `checks.cairn-client-runs` into CI — it currently runs only on demand.**
    `devrc-ci-pipeline.yaml` (in the infra repo, NOT devrc) hardcodes exactly two legs:
    `LEG` ∈ {`pytests`, `nodetests`}, built as `.#checks.x86_64-linux.${LEG}`. Measured
    2026-09-09: **2** static `value:` assignments, no `nix flake check`, no loop — so a third
    output is never built by CI and the check cannot fail a PR. The check itself says this in
    `flake.nix` rather than reading like a gate it is not.
    ⚠ **Deliberately not bundled into #1433:** that pipeline lives in a GitOps-reconciled repo
    where committing to the mainline IS deploying, which is an operator decision, not a rider
    on a devrc PR. Cost is not the obstacle — the check rebuilds in **~1.4 s** and the cairn
    package is the same derivation home-manager already builds, so it adds no build.
    🔨 **DECIDED AND IN A PR 2026-09-09 — `ZacxDev/homelab-infra` #786.** The measurement above
    was re-verified before building: still exactly 2 static `LEG` values, still 0 references to
    `cairn-client-runs`, and both `nix flake check` occurrences in the file are comments.
    🔴 **IT WAS NOT A ONE-LINE CHANGE, and the description above under-sold it.** The legs are
    STEPS inside one `devrc-ci-gate` Task, so a third leg also needs a context param on notify,
    on report and on the Pipeline, threaded from the TriggerTemplate, plus the verdict loop and
    `post_leg` — twelve sites, not one `value:`.
    🔴 **AND IT DERIVES ITS VERDICT DIFFERENTLY, WHICH IS THE PART THAT WOULD HAVE SHIPPED
    BROKEN.** The other two legs parse a `RESULT: PASS|FAIL` line their runners emit; this
    check is a `runCommandLocal` that emits no such line, so copying their logic scores every
    GREEN run `error`. The build's exit status is the verdict, and `unknown` (the `.rc` file
    absent ⇒ the step was killed) stays the separate `error` third state.
    🔴 **THE REPO'S OWN TESTS CAUGHT A REAL DEFECT: 54 of 119 went red on `CAIRN_CTX: unbound
    variable`**, because the report harness builds the Task's env and did not know about the
    third var. Fixed in the harness, never by weakening the fail-closed guard. The leg ledger
    now asserts SET EQUALITY over three legs, so it fails when the set GROWS as well as shrinks.
    ⚠ **Deliberately NOT a required check.** Making a brand-new leg required the day it lands
    would let its first infrastructure hiccup block every merge on a repo with
    `enforce_admins: true`. Promoting it in branch protection is a later, reversible operator
    action needing no change to the file.
    🔨 **MERGED 2026-09-10 (squash `4c890c7ac`) AND LIVE IN-CLUSTER**: `devrc-ci-gate`'s
    steps are now `clone capture-etc seed-nix pytests nodetests cairn-client-runs
    verdict`, and `devrc-ci-notify` carries `cairn-context`. Flux applied
    `trunk@4c890c7ac`.
    🔴 **THE CLOSING CONDITION IS STILL ONLY HALF MET, AND THE REMAINING HALF NEEDS A
    REAL RUN.** Wired is not gating: the leg must be SEEN on `gh pr checks <a devrc
    PR>` and must go RED when the pinned client is stubbed to print nothing. The first
    devrc PR to run after this merge is the one that answers half one.
    ⚠ And the leg has never executed in-cluster: every measurement across six audit
    rounds is one dev host plus a local `nixos/nix:2.24.15` container.
    ✅ **HALF ONE IS MET, OBSERVED 2026-09-12 — and the "never executed in-cluster" caveat
    above is RETIRED.** `tekton/devrc-cairn-client-runs` reported on PR **#1583** with
    `pass` and its own verdict text — *"the pinned cairn client ran: validate and doctor
    both produced output"* — alongside `devrc-nodetests` and `devrc-pytests` in a
    3-check rollup. So it is visible on `gh pr checks`, it executes in-cluster, and it
    reports a real verdict rather than a placeholder.
    ⚠ **Do NOT read this as "the first PR to answer it".** #786 merged 2026-09-10 and
    devrc has merged many PRs since, so earlier runs almost certainly exist; this is an
    observation, not a first. The sentence above predicting "the first devrc PR to run
    after this merge" was written before any of them and nobody recorded the answer.
    🔴 **HALF TWO IS STILL UNMET and is the half that matters:** the leg must be shown
    to go **RED when the pinned client is stubbed to print nothing**. A leg that has only
    ever been watched pass is a leg whose red path is unproven — `claude/RULES.md`'s
    "a verdict you have never watched go red is a claim about your command line".
    **Closing condition:** stub the pinned client to emit nothing, push to a throwaway
    branch, and watch THIS leg report `fail`; record the run name.
    forcing: none

21. **`analyze-service-index-commit.service` is VESTIGIAL and fails on every firing — 603
    failures in 3 days.** It tries to `git config` inside the local mirror, which the cairn
    cutover deliberately FROZE (`555` on scope dirs, `444` on entries), so it gets
    `could not lock config file .git/config: Permission denied` per scope and exits 1. Since
    the cutover the POD is the authority and does its own versioning, so a local job
    committing a read-only mirror can never succeed and has nothing to commit.
    ⚠ **Not data loss and not caused by any switch** — first seen 2026-09-06, timer-triggered;
    a `home-manager switch` merely REPORTS the already-failing unit ("Failed services: …"),
    which is easy to misread as switch fallout. It is cutover fallout.
    🔴 "Delete the unit" vs "point it at the pod" is a DECISION, not a cleanup — the second
    only makes sense if anything still wants local versioning, and nothing obviously does.
    **Closing condition:** the unit is removed from the home-manager config, OR its next timer
    firing exits 0.
    forcing: none

22. ✅ **CLOSED 2026-09-12 — the store-api fsync flake. `#1458` `ce9b55c3`, verified by the flake
    RATE (named in **0 of 99** verdicts on heads carrying the sha against **12 of 298** without).**
    Body demoted. 🔴 **The zero is not what establishes the fix — the mechanism being gone is.**
    🔴 **THE DIAGNOSIS IS THE DURABLE HALF:** `server.py:_replace_bytes` issues TWO `fsync`s — the
    file, then the parent directory — **inside the request and before the response is written**.
    `fsync` blocks in uninterruptible D-state, is bounded by nothing and **burns no CPU**, so it is
    invisible to every CPU-shaped metric; the handler's `timeout = 15` is a SOCKET timeout and does
    not reach a syscall. **Grep `MECHANISM =` FIRST on any recurrence** — the instrument already
    exists and three occurrences were spent re-deriving what it prints.
    🔴 **RETRACTED, AND WORTH KEEPING RETRACTED: devrc's Tekton checks are NOT required and
    `enforce_admins` is FALSE.** Earlier revisions of this item asserted the opposite for its whole
    life, which inflated the urgency of every gate item in this doc. A red gate is still the *other*
    hazard — the one nobody is forced to look at. ⚠ A protection setting is point-in-time: re-read
    it, do not cite this line.
    forcing: gate — advisory, not blocking

25. **The repo-handle `~/workspace/<handle>/…` sites the kubeconfig arm deliberately deferred.**
    #1621 armed the `~` spelling for **kubeconfig** handles only — an operator decision, on the
    asymmetry that `$KC_*` names a FILE while a repo handle names a DIRECTORY and the `~` family
    carries no path tail. That leaves the repo-handle half of the `~` class untouched.
    🔴 **MEASURED AT `origin/main` AFTER #1621, AND THE POPULATION IS NOT WHAT A GREP SUGGESTS —
    read this before scoping the sweep.** The corpus holds **177** `~/workspace/…` tokens, but only
    **129** of them name a repo that HAS a handle: `~/workspace/devrc` **100** and
    `~/workspace/homelab-talos` **29**. The other **48 HAVE NO HANDLE AND THEREFORE NO REMEDY** —
    `clawgate-extension` 18, `homelab-infra` 6, `tmux-fuzzyclaw` 5, `kubeclaw` 5, `scratch` 3, and
    a tail. Arming the gate against all 177 would block those 48 with nothing to offer them, which
    is the permanently-red gate `claude/RULES.md` forbids; it is the same shape as
    `claude/skills/auditloop/reference/ui-and-meta-run.md:48`, the one site #1621 left alone for
    exactly this reason. **Scope the sweep to the 129, or add handles first.**
    🔴 **The second subtlety, because it makes this NOT a blanket rewrite either:** `~` is the
    **CORRECT** spelling for a Read-tool target — `$VAR` does not expand there — so each of the 129
    has to be classified, not rewritten. A sweep that treats every `~/workspace/<handle>` as a
    violation will break the Read-tool sites it "fixes".
    **Closing condition:** EITHER the gate arms `~` for repo handles with an explicit, tested
    carve-out for Read-tool targets and the sites are cleared — merged, and watched red-then-green
    on a planted violation — OR a decision is recorded in this doc that `~/<suffix>` is an
    accepted spelling, in which case the gate's docstring must stop implying otherwise.
    forcing: none

27. **`scripts/tests/test_doc_path_rot.py` carries the same stale-census defect twice, over the
    corpus its sibling gate asserts is IDENTICAL.** `CORPUS_DOC_FLOOR = 40  # measured 80` against
    a real **99**, and `REFERENCE_FLOOR = 155  # measured 310` against a real **593** — and each
    figure is restated in a failure message, so it is **4 sites**, not 2. Neither breaks anything
    — floors are minimums, so a real count above them passes — but they are the numbers a
    maintainer reads while debugging a corpus collapse, and the 310→593 gap is wide enough to
    make a genuine collapse look survivable.
    ⚠ **That module is arguably the more honest of the two:** its figures are explicitly
    ref-scoped where the sibling's are bare. The defect is staleness, not the convention.
    **Closing condition:** each figure re-derived from the module's own corpus/reference builders,
    with the comment AND the failure message updated together — or the counts deleted where they
    add nothing — merged.
    forcing: none

28. **`shell-env-nudge.py` is cwd-BLIND, so its relative-path arm can nudge the WRONG CLUSTER.**
    Filed by operator decision during #1657's round-2 audit rather than fixed there — the remedy
    changes `analyze()`'s signature and the core matching of a hook that fires on **every Bash
    call**, which deserves its own PR and its own audit rounds.
    **Measured at #1657's head:** `KUBECONFIG=./production-kubeconfig` → `$KC_PROD` and
    `KUBECONFIG=some/other/tree/prod-kubeconfig` → `$KC_DPPROD`, from any cwd. `KC_BASENAMES` is
    `{basename: handle}` and nothing resolves the path, so a relative kubeconfig in the wrong
    directory is nudged to a handle naming a **different cluster** — and `$KC_PROD` (homelab) and
    `$KC_DPPROD` (datapacket) really are different clusters.
    ⚠ **Pre-existing in KIND** (`KC_HOMELAB`/`KC_WORKBENCH` already behaved this way); #1657 added
    `KC_PROD`, which made the `production-kubeconfig` spelling newly reachable. #1657 closed the
    ABSOLUTE arm only — an absolute path is no longer matched by basename.
    🔴 **Do NOT justify this with "the empty handle silently takes the default context"** — that
    sentence is RETRACTED in `scripts/tests/test_absolute_handle_paths.py` and needs a precondition
    this host does not meet. The real harm is the case that needs no precondition: where the
    wrongly-named handle IS exported, the command runs against the wrong cluster with no error.
    **The remedy, named so it is not re-derived:** PostToolUse payloads carry `cwd` —
    `scripts/claude-hooks/bash-guard.py`, `git-add-provenance-nudge.py` and `lib/guard_core.py`
    all already read it. Resolve `os.path.realpath(os.path.join(cwd, norm))` against `KC_VARS` and
    `KC_BASENAMES` becomes unnecessary, closing both arms exactly.
    ⚠ **Frequency is UNMEASURED** — the corpus holds one relative-kubeconfig instance and it is the
    counter-example. Measure before deciding this is worth the change; `claude/opencode-addendum.md`
    already forbids the spelling outright, which is an argument for deleting the arm instead.
    **Closing condition:** EITHER the hook resolves relative paths against the payload's `cwd` and a
    test shows `KUBECONFIG=./production-kubeconfig` from a non-`homelab-talos` cwd producing NO
    `$KC_PROD` suggestion — RED before, GREEN after, merged — OR the basename arm is deleted and the
    hook's own suite updated, OR a decision is recorded here that a cwd-blind relative nudge is
    accepted, in which case the guard comments in `shell-env-nudge.py` must stop implying otherwise.
    forcing: none

29. ✅ **PHASE B — CLOSED 2026-09-16. Both halves merged and LIVE on both hosts.** `ZacxDev/cairn`
    **#24** `baee2f0` (mechanism) + `innovation-upstream/devrc` **#1726** `5783b46f` (the table).
    Claim RELEASED. Closing evidence is in `## State now` — content checks at `origin/main`, then
    the artifact read live on each host (identical `…-cairn-baee2f0` store path, identical
    `routes.json` md5, `cairn routes` → `instances: personal`, exactly ONE each), plus phase B's own
    claim proven by EXERCISE: `recall` returns `ALL 32 entries` on both and an unregistered scope
    still answers `scope-absent` rather than refusing.
    🔴 **KEEP THE REST OF THIS ITEM — it is the CONTRACT phase E edits, not closed history.**
    The table is an INPUT — `~/.config/subsystem-store/routes.json`, or `$CAIRN_ROUTES`, a flat
    JSON object of `scope → alias`, every other shape refused. `env` stays the `personal` alias;
    extra instances are `instances/<alias>.env`; caches are SIBLINGS (`…/subsystem-store-<alias>`),
    the default instance keeping `DEFAULT_CACHE_ROOT` byte-for-byte. 🔴 **LABELLING activates on
    `len(instances) > 1`, NOT on the table's presence — but ROUTING is a SECOND predicate and the
    earlier wording here conflated them.** It read *"dropping a table onto a one-instance host
    changes nothing"*, and that is OVER-BROAD in the direction that ships a defect: `alias_for`
    consults the TABLE FIRST whatever `multi_instance` says, and its **row 3 — an entry naming an
    alias this host has no config for — REFUSES at one instance exactly as at many.** The correct
    sentence is **"an UNREGISTERED scope changes nothing at one instance"**. Following the old one
    means shipping the FINAL ledger (`storage-resolver: civitai`) at phase B, which breaks every
    read of those scopes on both hosts the moment it deploys. **That is why the phase-B table
    routes every scope to `personal`; the cutover to `civitai` is phase E** (§9), not this item.
    🔴 **THE PIN THAT PHASE C/E STILL OWES, and why it is not what §5.2 says.** §5.2 specifies a
    two-way pin where "a scope with no entry **fails the suite**, and an entry naming no scope
    **fails it too**". **Neither half can happen at one instance**, which is why B shipped a
    FIXTURE pin instead: **direction two was deliberately demoted to a NOTE upstream in cairn
    #24**, on a measurement in `Routing.check`'s own docstring — a snapshot ships entry FILES, so an
    empty scope and a retired one are indistinguishable, and grading it at exit 11 made
    `routes --check` refuse every pre-registered scope; nothing in devrc can restore it. And
    **direction one is a NON-DEFECT at one instance by design** — `alias_for` resolves an unnamed
    scope to the sole instance, so `check` appends nothing. **When a second configured instance makes
    direction one a real refusal, the pin moves OVER REALITY and the deferred `[routes]` drift-check
    arm becomes reachable.** That is the work §5.2 was actually describing. ✅ **THAT TRIGGER FIRED AT
    PHASE D, NOT AT E** — this line said "At phase C/E"; both hosts are multi-instance as of
    2026-09-18, so the arm is reachable NOW and `nix/home.nix`'s note parking it against "the first
    host to configure a second instance" is discharged.
    ⚠ **Proposal §5.2 still states the impossible version.** Superseded in practice, deliberately
    NOT amended — operator call 2026-09-16, to avoid a second repo's review cycle. Read this item,
    not §5.2.
    ⚠ Declared and NOT closed by #24: `tests/parity/README.md` difference 8 — Go's READ verbs
    refuse at exit 11 on a multi-instance host rather than routing. 🔴 **"Unreachable today (no such
    host)" IS NOW FALSE — both hosts went multi-instance at phase D.** The ONLY thing still holding
    this back is that `packages.cairn` is the Python client; swapping it in makes difference 8 live
    immediately, not at phase E.
    forcing: none — but C/D/E sit behind it, so it gates the rest of the arc

## Gotchas / decisions / dead-ends

### ✅ 2026-09-18 — DECISION: routing durability is (a) FAIL-LOUD. Mirroring REJECTED.
🔴 **Operator decision, NOT to be re-litigated.** Per-scope bidirectional mirroring is rejected;
routing fails loud and freshness is observable. **Recorded HERE, in an APPEND section, because that
is what "recorded in this doc's decisions" required** — the fuller block in `State now` sits under a
REPLACE heading and will not survive the next `/handoff`. The three measurements behind it, and what
phase E therefore does NOT have to build, are in that block while it lasts; the decision itself is
this paragraph.

### 2026-09-10 — SIX AUDIT ROUNDS ON `homelab-infra#786`, AND WHAT ENDED THEM (body DEMOTED)
🔴 **A CLASSIFIER GRADED BY READING WILL BE REWRITTEN UNTIL SOMETHING EXECUTES IT.** The cairn
leg's ~20 lines of verdict shell went through FOUR rewrites, and each fix shipped the OPPOSITE
defect of the one before:

| round | change | defect it shipped |
|---|---|---|
| 1 | every non-zero rc → `fail` | a broken gate blamed on the author |
| 2 | marker-less non-zero → `error` | a broken PIN excused as infrastructure |
| 3 | bare drv-name match | nix ANNOUNCES the build before any outcome, so it matched every run that built |
| 4 | markers-first | nix emits `unable to download` at WARNING level while successfully RETRYING |

Rounds 1–3 were each verified by careful reading. The fix was not a fifth reading: a 17-row
table that lifts the SHIPPED shell out of the YAML and runs it under a real `sh`, asserting
VERDICT AND DETAIL, with all four historical classifiers replayed into the pipeline and caught.
**The transferable tell: when a fix and its predecessor keep swapping which direction they are
wrong in, the missing thing is EXECUTION, not care.**

📁 **The 849-line body — ~90 dated gotchas covering the whole cairn extraction arc, not just
#786: the audit ladders and their stopping criteria, mutation-battery traps, leakscan and nix
packaging, the shared-clone write hazards (`home-manager switch --flake`, `handoff_doc.py
--push`), and the instrument traps (`[ahead N]`, `docker manifest inspect`, `sops --config`) —
is MOVED VERBATIM to `claudedocs/refs/cairn-oss-multi-instance.md` § DEMOTED 2026-09-17.** It is
closed history from merged PRs. Read it there before re-deriving anything about this arc.

### 🔴 2026-09-13 — `gh pr merge --auto` MERGES IMMEDIATELY here (body DEMOTED)
**`--auto` waits on REQUIRED checks; devrc's `tekton/devrc-*` are commit statuses that are not
required, so there is nothing to wait on and the request degenerates to an immediate merge.** It
fails BY MERGING, at `rc 0` with no output. The after-the-fact tell is one read:
`gh pr view <n> --json autoMergeRequest,state` → `autoMergeRequest=null` **and** `state=MERGED`.
**Do instead:** poll the checks to terminal yourself and assert a **minimum check count** so an
unregistered rollup cannot settle instantly; never reach for `--auto` as a safety. **Recovery:**
the gate is re-ordered, not lost — `git worktree add --detach /tmp/x origin/main` and run the
gates on the MERGED tree. Worked example (#1635) moved verbatim to
`claudedocs/refs/cairn-oss-multi-instance.md` § DEMOTED 2026-09-17 (pass 2).

### 🔴 2026-09-13 — NO local pre-push hook runs in this clone, so the delta gates are CI-only here
- **Measured:** `/home/zach/workspace/devrc/.git/hooks/pre-push` **does not exist** and `core.hooksPath` is **unset** (global and local). Two independent sessions' agents reported the same thing while pushing to `fix/skills-absolute-checkout-paths` and `docs/handoff-rank24-closed`.
- **Consequence:** every push in this arc was **locally ungated** — the doc-rot / skill-path / handle-path gates did not evaluate any change before it left the machine. Whatever Tekton posts is the only check, and per the gotcha above a Tekton status can be *bypassed at merge time* and can also register *after* a merge.
- **Not diagnosed:** whether the hook was never installed in this clone, or was installed and later lost. `scripts/install-hooks.sh` exists and is the documented one-time install; nobody ran it here. **Closing condition if picked up:** `git -C <repo> config core.hooksPath` resolves, or `.git/hooks/pre-push` exists, AND a deliberately-bad push is watched to be REFUSED locally — a hook that exists but never fires is the same as none.

### 2026-09-13/14 — the rank-24 arc's own process notes (body DEMOTED)
Rank 24 is itself demoted now. Its ~14 process notes — the `claim-work` slug derived for a rank
that was not yet numbered, `audit-dispatch.py` resolving the PR against the CWD's repo, the
`--emit-claims` issue-vs-review-comment trap, the `isolation: "worktree"` cross-repo override,
and the instrument-failure catalogue (`grep -cF` splitting a multi-line pattern, a revert patch
that silently no-longer-matched, a latin-1 fixture that was never written, the wrong pipelinerun
read) — moved verbatim to `claudedocs/refs/cairn-oss-multi-instance.md` § DEMOTED 2026-09-17
(pass 2). 🔴 **The two rules worth carrying on the reading surface:** *before "fixing" a table
that omits an entry, `grep` for a test that ASSERTS the omission* — a deliberate exclusion and an
oversight look identical in the table itself; and *a brief naming ONE instance of a mechanical
defect is naming a SAMPLE, not a population* — rank 23(c) described one scrubbed remedy and there
were twelve.

### 2026-09-15 — a ONE-TIME certification is not a GATE (`/the-algorithm`, operator-prompted)
I raised a 🔴 that `tests/routing_mutants.py` and `tests/unchanged_output_capture.py` are run by
**no CI job** (measured: 0 mentions each in `.github/workflows/ci.yml`, against 3 for
`parity/harness.py` as the positive control) and recommended wiring them in. **RETRACTED one
message later, under step 1 of the algorithm: the maker was me, minutes earlier.** Both are
one-time instruments. The battery answers *"is this guard real?"* when the guard is written;
re-running it forever re-answers a settled question at 39 mutants × two languages per CI run.
The (e) capture is worse as a gate and it is checkable rather than arguable: `--base` defaults to
`origin/main` and `lib/README.md` drives it at `--base 2301876`, **this branch's first commit** —
so its claim is "output did not move across THIS PR", and once merged there is no "before". Its
own recorded base had already gone stale five times, once per rebase. **The tests are the gate;
the battery is what proved the tests work** — and the 1924-test, 98-case parity and conformance
jobs are all wired in. Kept both harnesses in-repo, hand-run by design. **Do not re-file this.**

### 2026-09-15 — a REBASE poisons a delta-audit range, and the range still looks ordinary
`audit-dispatch.py --round N` builds `<prev-tip>..<head>`. After a rebase those ends sit on
different bases, so the range sweeps in every upstream commit the rebase brought — round 2's
literal `732fdb5..08b293a` pulled in #26 and #27, both already in `main`. Nothing errors and a
wider range reads like an ordinary delta. **The auditor isolated it with
`git range-diff <old-base>..<old-tip> <new-base>..<new-tip>`; use that whenever the branch was
rebased between rounds**, and say in the dispatch which commits the range actually spans.

- 🔴 **`xargs -0 command grep` EXITS 127 AND PRINTS NOTHING — a false zero.** `command` is a shell
  builtin xargs cannot exec. Five separate arcs hit this and each wrote it into their own handoff;
  none reached `RULES.md` until now (devrc #1732 `6ead5e43`), so each paid the discovery cost again
  and one lost a real measurement. **The rule INDUCED it**: `RULES.md` says `grep` is a function and
  prescribes `xargs -0 grep`, so a careful reader "hardens" it to `command grep`. Under `xargs` the
  function never applies — plain `grep` is already the binary.
- 🔴 **`gh pr diff` SERVES A STALE PATCH.** It rendered an entire withdrawn 120-line arm at a head
  where `git grep` counted **0** of every token against a positive control of 15. Verify a PR's
  contents from the FETCHED head (`git fetch origin refs/pull/<n>/head:<ref>`), never `gh pr diff`.
- 🔴 **A 140-char CI status line truncates NUMBERS mid-token, not just failure lists.**
  `collected=2386` was really `23860`; a partial number reads as a plausible smaller one and nearly
  sent a session hunting a 10× collection shortfall.
- 🔴 **RUN A SUITE INSIDE `nix develop` OR IT MANUFACTURES DEFECTS.** A bare `python3 -m pytest` over
  27 pinned-lib consumers reported **9 failures**; **8 were the harness** (five
  `run-tests: FATAL — required tool … gateTools`, three `DECRYPT-DEPS-MISSING`). Inside
  `nix develop`: **270 passed**. One real failure, eight phantoms.
- ⚠ **RETRACTED: "the PR's verification ran against the OLD pin".** Measured both ways —
  `cairn_pin.py` resolves the old pin in a BARE shell and the NEW pin inside `nix develop`, and every
  count had been taken inside `nix develop`. **The real gap was the FILE SET** (`test_subsystem_read_store.py`
  had never been run), and the remedies differ: re-running in another shell would have changed
  nothing and left the hole open.
- 🔴 **An ABSENCE claim passes a doc-rot gate exactly when it goes false.** Gate 0 checks paths
  EXIST; a "what is deliberately absent" list is green while wrong. Found in this arc's own
  `secrets/kustomization.yaml`, which still named the secret that had just been added.
- ⚠ **`ship.sh` deploys from the WORKING TREE.** The base clone was behind `origin/main`; shipping a
  minute earlier would have deployed a tree with no routing table **and reported success**.
  `git -C $DEVRC merge --ff-only origin/main` before any ship — it fast-forwards or refuses.
- 🔴 **Re-check the branch IMMEDIATELY before a write in a shared clone.** A `merge --ff-only` ran
  against another session's feature branch because the clone was verified on `main` earlier in the
  session and the fact was acted on later. `--ff-only` contained it (the branch had no commits of
  its own, so nothing was lost), which is exactly why it is the prescribed form.
- ⚠ **skopeo wraps auth failures in `"Error parsing image name"`.** A truncating `cut` removed the
  `unable to retrieve auth token: unauthorized` clause and made a valid negative control look like a
  usage error.

- 🔴 **RANK 26'S PREMISE WAS HALF FALSE, AND THE REFUTATION IS THE DURABLE OUTPUT** (rescued here
  from `State now`, which is a REPLACE section). It claimed "the handle table has TWO hand-maintained
  copies, and both are drifted". One is. `handoff_index.REPO_ENV_HANDLES` omitting `CIVITAI_CLI` is a
  **deliberate exclusion**, pinned in BOTH directions with its reason in source by
  `test_handoff_index.py::TestTheUnitEnvironmentMatchesTheHandlesTheIndexerReads`, which passes today.
  Implementing it as written would have deleted a documented decision, added a zero-doc repo to the
  corpus, and narrowed the hosts `--prune` can run from.
- 🔴 **THE DURABLE OUTPUT OF THE 2026-09-12 SESSION WAS NOT THE CODE — IT IS WHAT THREE AUDIT ROUNDS
  FOUND** (rescued from `State now` for the same reason). **Zero 🔴 in any round; every finding was a
  FALSE CLAIM ABOUT THE CODE, and two were in that session's own prose.** Round 0 refuted the PR's
  stated rationale, and also DELETED a guard that session wrote, on measurement — the pre-existing
  `test_cairn_flake_pin.py` already killed all three of its mutants behaviourally, in both tiers.
  **The fix rounds, not the original change, were where every finding lived.**

## How to verify

**Phase C is closed** — content at `origin/trunk`, then the live artifact:
```bash
git -C $DATAPACKET fetch origin trunk
git -C $DATAPACKET ls-tree -r --name-only origin/trunk -- clusters/production/apps/cairn | wc -l   # 13+
KUBECONFIG=$KC_DPPROD kubectl -n cairn get pods -o wide
KUBECONFIG=$KC_DPPROD kubectl -n cairn get cronjobs -o custom-columns='NAME:.metadata.name,SUSPEND:.spec.suspend'
```
Expect a Ready pod on `talos-avt-y6z`. ⚠ **Both CronJobs are still suspended AFTER phase D** —
unsuspending them is an open item, not a leftover.

**The store answers, and enforces auth** (token never leaves the pod):
```bash
KUBECONFIG=$KC_DPPROD kubectl -n cairn exec deploy/cairn -- sh -c 'wget -qO- http://127.0.0.1:8102/healthz'
```
Expect `ok`. ⚠ **Probe a SEEDED scope — `civitai-developer-docs`, not `civitai`**, which was never
seeded and returns `scope-absent` whether or not anything works. The probe is below.

**Phase D is seeded; the TABLE is still not cut over** (that is phase E):
```bash
cairn routes | head -2   # instances: personal, civitai  — TWO since phase D
python3 -c "import json,collections;d=json.load(open('$HOME/.config/subsystem-store/routes.json'));print(len(d),dict(collections.Counter(d.values())))"
```
Expect `25 {'personal': 25}` — the shipped table is unchanged — and `instances/civitai.env`
present on BOTH hosts. 🔴 **The seeded scope is reachable only under a `$CAIRN_ROUTES` override
(the shipped table plus `"civitai-developer-docs": "civitai"`); without one, `cairn recall --scope
civitai-developer-docs` exits 11.** ⚠ **Expected FOR THIS SCOPE ONLY. `Routing.alias_for` raises
`UnroutedScope` on THREE paths and they need DIFFERENT remedies — read the message, it names the
right one:** a table entry naming an alias this host has no config for wants
`instances/<alias>.env` (**this is the one phase E creates**, at any instance count); ≥2 instances
with no table wants a table; ≥2 instances and a table lacking the scope wants a row. Do not assume
"rc 11 ⇒ add a table row".
```bash
python3 -c "import json,os;t=json.load(open(os.path.expanduser('~/.config/subsystem-store/routes.json')));t['civitai-developer-docs']='civitai';json.dump(t,open('/tmp/r.json','w'))"
CAIRN_ROUTES=/tmp/r.json cairn recall --scope civitai-developer-docs   # rc 0, banner cairn[civitai]
KUBECONFIG=$KC_DPPROD kubectl -n cairn exec deploy/cairn -- sh -c 'T=$(cut -d" " -f1 /run/secrets/cairn/token); wget -SqO- --header="Authorization: Bearer $T" http://127.0.0.1:8102/api/v1/recall/civitai-developer-docs 2>&1 | grep -E "X-Store-Status|X-Store-Snapshot"'
```
Expect `X-Store-Status: recalled`. ⚠ `entry-files=` rides **`X-Store-Snapshot`, not
`X-Store-Status`**, and is a **store-wide** total, not this scope's count — 2 at phase D, growing
with any later write anywhere in the store.

**Gate 23 exists and the mount defect cannot return:**
```bash
git -C $DATAPACKET grep -c 'ro-pvc-comount' origin/trunk -- scripts/ | head -3
```

---
🔴 **Everything below is the PRE-EXISTING verification set for the still-open ranks. It is
carried forward deliberately:** `How to verify` is a REPLACE section, and the tool's
durable-line classifier flagged 2 lines while this whole block — the checks for ranks 11, 20,
21 and rank 3 slice 3 — was in the dropped set. A silent classifier is not evidence.


🔴 **Verify a merge by CONTENT, never ancestry — a squash is never an ancestor.**

```bash
# the four operator-blocked merges (all MERGED; content checks, not ancestry)
gh pr view 785 -R ZacxDev/homelab-infra --json state,mergeCommit   # 37b5a71f8
gh pr view 786 -R ZacxDev/homelab-infra --json state,mergeCommit   # 4c890c7ac
gh pr view 787 -R ZacxDev/homelab-infra --json state,mergeCommit   # 936692ec7
gh pr view 1447 -R innovation-upstream/devrc --json state,mergeCommit  # 719519fa9

# rank 26 — IN FLIGHT. Not merged; do not report it as done.
gh pr view 1657 -R innovation-upstream/devrc --json state,mergeCommit,mergeStateStatus
gh pr checks 1657 -R innovation-upstream/devrc
#   🔴 an EMPTY rollup on a young PR means NOT YET REGISTERED, never "no CI" — watched flip
#   from `no checks reported` to three pending Tekton legs in one session.
# once merged, verify BY CONTENT, then release the claim:
git -C $DEVRC show origin/main:scripts/claude-hooks/shell-env-nudge.py | grep -c KC_PROD  # 1
git -C $DEVRC cat-file -e origin/main:scripts/tests/test_shell_env_nudge_handles.py       # rc 0
claim-work --release cairn-oss-multi-instance-26

# rank 26's REFUTED half — this must PASS, and it is why CIVITAI_CLI is absent by design
python3 -m pytest $DEVRC/scripts/tests/test_handoff_index.py \
  -k test_every_handle_the_indexer_reads_is_exported_by_the_unit -q      # expect 1 passed

# rank 13/7 — read the RUNNING container, never the manifest, and keep the control
KUBECONFIG=$KC_HOMELAB kubectl -n subsystem-store get deploy subsystem-store-api \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'          # expect 0.8.0
KUBECONFIG=$KC_HOMELAB kubectl -n subsystem-store exec deploy/subsystem-store-api -- \
  sh -c 'grep -rl SIGHUP /app | wc -l; grep -rc "def load_tokens" /app/server/server.py'
#   expect 1 and 1 — the second is the POSITIVE CONTROL; a bare 0 on the first without it
#   is indistinguishable from a grep that walked nothing. The `sh -c` is load-bearing.

# rank 11 — the scope is writable, and the entry is really there
cairn recall --ref ci-leg --scope cairn        # 1 of 1 entry in `cairn/`

# rank 20 — half one only
KUBECONFIG=$KC_HOMELAB kubectl -n tekton-ci get task devrc-ci-gate \
  -o jsonpath='{range .spec.steps[*]}{.name}{" "}{end}{"\n"}'
#   expect: clone capture-etc seed-nix pytests nodetests cairn-client-runs verdict
#   🔴 HALF TWO IS UNMET: nothing here shows the leg goes RED when the client is stubbed.

# rank 21 — still failing, re-verified 2026-09-10
systemctl --user show analyze-service-index-commit.service -p Result -p ExecMainStatus
#   expect Result=exit-code ExecMainStatus=1

# rank 3 slice 3 — MERGED (#1508 `44bd8b0e`). CORRECTED 2026-09-14: the line that used to sit
# here said "NOT started; all five modules still present", which contradicted `State now` and
# was wrong. The five READER modules are gone; the WRITER and `timeouts.py` stay by design.
for m in host_identity subsystem_resolver subsystem_recall cairn_doctor subsystem_read_store; do
  git -C $DEVRC cat-file -e "origin/main:scripts/lib/$m.py" 2>/dev/null && echo "$m PRESENT" \
    || echo "$m ABSENT"
done                                            # expect all five ABSENT
git -C $DEVRC cat-file -e origin/main:scripts/lib/subsystem_touch.py   # rc 0 — PRESENT by design
```
Expected: four MERGED shas; #1657 OPEN with three Tekton legs; the `REPO_ENV_HANDLES` ledger
passing; store on `0.8.0` with SIGHUP `1` and control `1`; the cairn scope holding one entry;
a seven-step gate Task; rank 21 still `ExecMainStatus=1`; five reader modules ABSENT and the
writer PRESENT.
