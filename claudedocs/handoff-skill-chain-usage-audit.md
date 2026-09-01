# Handoff: skill-chain-usage-audit — 2026-08-29

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Measure how the `/handoff` → `/resume` → clawgate skill chain is ACTUALLY used, and
whether sessions that resume a handoff end up recording their work. Ship a number that
survives adversarial re-derivation, and fix whatever it exposes.

## State now
- 🔴 **THE INITIATIVE'S HEADLINE MEASUREMENT, carried forward because it is durable and this
  section is REPLACED on every update.** Window 2026-08-15 → 08-29, both hosts, both runtimes
  (`find-session` stderr silent on every run ⇒ full coverage per its own contract):
  391 sessions match `"Canonical handoff (read first)"`; **256** carry a `/resume` kickoff as
  their genesis line; **253 graded** (98.8%). **231 / 253 = 91.3% RECORDED** — 206 updated the
  doc they resumed, 25 landed under a different topic (drift, not loss). **22 / 253 = 8.7%
  real loss** — 19 never invoked `handoff_doc.py`, 2 tool failures, 1 stopped at the confirm
  gate. Legitimate declines (`no-change`/`no-advance`): **ZERO**. Rank 1's end-state split of
  the 16 readable never-run losses: **8 cleanly-ended · 4 interrupted-at-end · 2
  context-exhausted · 2 never-started**, with a `Stop` hook already firing in **8 of 8** of the
  dominant bucket. Clawgate leg: of the 85 tasks that advanced past `open`, **73 (86%)** carry
  a write-back comment; **0** of 186 were agent-dispatched.
- 🔴 **EVERY PR OF THIS ARC IS MERGED. Work off `origin/main`; there is no branch.**
  **#1055** `8c108d8b` · **#1064** `f71ff648` · **#1198** `40d7fef2` · **#1204** `ae8e68d3` ·
  **#1207** `4162dab1` (2026-09-01T20:03:27Z). 🔴 **All verified by CONTENT on `origin/main`,
  never by ancestry** — `merge-base --is-ancestor` reads **false** forever after a squash.
- ✅ **RANK 5's WORKED EXAMPLE LANDED (#1207), and the ladder that graded it is CLOSED at 6
  rounds with round 6 CLEAN.** The doc went **80,397 → 64,837 B (−19.4%)**, 6.5x → **5.3x** the
  12,288 B target, and is **STILL OVER the 40,960 B hard cap**. 🔴 **The −28.7% the PR description
  claimed is WRONG and was corrected publicly** — every audit round correctly bought back bytes
  the eviction should not have taken.
- 🔴 **RE-DERIVE THE SIZE, NEVER QUOTE ONE FROM THIS DOC** — including the "5.3x" and "64,837"
  in the bullet above, which describe the tree as it stood at #1207 and **not** the one you are
  reading. Measured trail, each figure pinned to a **fixed sha** and none to a moving ref:
  `f71ff648` 80,397 · `21ee51d7` 57,356 · `1127e620` 60,964 · `ef49ac82` 62,298 · `34924645`
  64,397 · `384e1c37` 64,837 · `66eee1d7` **65,506** (this was `main` when rank 13 branched; #1217
  added 669 B after #1207). 🔴 **`384e1c37` was labelled "/`main`" here and that label went stale
  within the day** — a fixed number on a moving ref is the very trap this bullet exists to name.
  A figure quoted from a report that measured an EARLIER commit of the same PR is the other half
  of it, and is what this arc hit in its own verification step.
- ✅ **Ranks 4 and 11 DONE.** Rank 4 needed **no deploy** — the store copy of
  `claude/skills/clawgate/SKILL.md` was already byte-identical to `origin/main` (15086 B), so
  `home-manager switch` was deliberately skipped rather than run as a no-op riding another
  session's uncommitted `nix/programs/alacritty/default.nix`. Rank 11 forward-merged `main` into
  #1064 and its gate went green.
- **OPEN: ranks 5, 8, 10, 12 — FOUR of 13; DONE are 1,2,3,4,6,7,9,11,13 — NINE. Count the list, do
  not quote the tool.** `python3 scripts/handoff-audit.py --sections 1 <this doc>` prints
  **`10/13`**, and 10 + 4 = 14 ≠ 13, which is the tell. 🔴 **The extra one is rank 5, and the
  mis-read is structural, not a typo:** `DONE_MARK` matches `\bDONE\b` on an item's **first line
  only** (`scripts/handoff-audit.py:145`, `:339`), and rank 5 opens *"The WORKED EXAMPLE is DONE
  (#1207"* — whose very next clause is *"the CONTRACT is not"*. So the tool silently closes **the
  largest open item, the one whose own text documents that this tool's bucketing is broken.**
  Until that is fixed, this doc's `N/13` is a lower bound on open work, not a reading.
  (The `8/12` this line used to carry was one item and one denominator stale; the denominator was
  already 13 in the same commit that wrote `12`.)
- 🔴 **CARRIED FORWARD — durable specifics the condensed status above would otherwise drop.**
  *Ranks 2+7, the hook:* devrc**#1092** `ad891a5c`; `ship.sh` converged BOTH hosts to **`bd1572f3`**
  (verified by ancestry of the shipped sha PLUS a byte check of the deployed blob **`6d25558e`**);
  registered on both hosts as **1 `PostToolUse`, 1 `Stop`, 0 `SessionStart`**. *Rank 3's real
  finding:* **13 / 26** abandoned-and-uncommitted-since, **5** with no link from X to Y (that 5 is
  rank 12's read set). *The pre-#1211 CI history:* the symptom fix `8e33bf1d` (#1023,
  `HANG_TIMEOUT` 15→60 s) is live and was **insufficient** — which is why #1181/#1211 exist; the
  queue-depth attribution remains an **uncontrolled covariate**, never a demonstrated cause.
  *Store map:* `devrc/tests.md` = the stale-base-vs-environment discriminator + the `failed=N`
  lesson; `devrc/skills.md` = the derivation-command lesson, the `stat`/`stat -L` trap, and the
  6-round ladder ratio; `devrc/analyze-service-index.md` = recall *lists* vs *features*.
  *Gotcha:* `mergeable` reading `UNKNOWN` right after a push is GitHub computing it lazily — it
  settles to `MERGEABLE / CLEAN` minutes later and is **not** a conflict signal.
- **This session's clawgate link: NONE.** `clawgate_handoff.sh resolve` exited **5** — 0 tasks —
  with its positive control confirming the board was reachable. That 0 cannot distinguish
  "touched no task" from "wrong id", so **no `clawgate-task:` field was written**, and this is
  not a clean bill of health.

## Open investigations — live diagnosis state

### ✅ CLOSED — why the never-run losses ended without invoking `/handoff`
EVICTED 2026-09-01 (terminal). Verdict: **8 cleanly-ended · 4 interrupted-at-end · 2 context-exhausted · 2 never-started** of 16 — carried in `## State now`'s headline. 🔴 **That headline is NOT automatically durable:
`## State now` buckets as REPLACE, and a merge round-trip flags this very line among 9 durable
lines a replace would drop. It survives only because each session carries it forward BY HAND.**

### ⚠ MOSTLY CLOSED (open probe → rank 12) — the topic-drift sessions, and why the question as posed does not discriminate
EVICTED 2026-09-01 — **terminal EXCEPT its open probe, which is now rank 12** (it was wrongly labelled wholly terminal; caught by audit). Verdict: the question does NOT discriminate — **95.5%** of abandoned docs declare open work vs a **90.3%** control. Instrument `claudedocs/skill-chain-drift-audit.py`; see rank 3.

### 🔴 WALK-BACK — "queue depth is what moved" was UNDER-CONTROLLED, and the store already knew
**Corrected 2026-08-31, by reading the subsystem store instead of re-deriving.** This doc briefly
claimed that #1108's three gate attempts showed *"the variable that moved was queue depth, not the
diff"*. The three attempts are real and are recorded above:

| attempt | queue at fire | outcome |
|---|---|---|
| 1 `9cb3e56f` | 8 running / 3 pending | `FAILED: pytests` naming a test |
| 2 `a3f15123` | 7 running / 2 pending | `COULD NOT RUN` both legs, gate task's 60m budget expired |
| 3 `eb03676c` | **5 running / 0 pending** | **both legs green, 22m** |

🔴 **But `devrc/tests.md` in the subsystem store has carried the controlled version since
2026-08-27, and nobody in this arc consulted it** — *"Three runs of `tekton/devrc-*` on ONE
UNCHANGED commit (`4ea2ee71`, PR #937, retriggered by close/reopen): (1) both legs ERROR 'COULD NOT
RUN'; (2) nodetests pass, pytests FAILED with `failed=0`; (3) both pass, summary byte-identical to
(2)."* That is **the exact probe this doc kept listing as its open next step** — one unchanged
commit, three different verdicts — already run, days earlier, and it moves queue depth from
"demonstrated cause" to "uncontrolled covariate". Three attempts at three depths with three
outcomes is equally consistent with a gate that is simply nondeterministic.

**What survives and what does not.** The capacity MECHANISM stands — it is measured and written
down in-tree at `scripts/tests/test_subsystem_store_api.py:99-108`, and attempt 2's failure is
unambiguous (60m13s against a 60m task budget). What does NOT survive is the causal claim about
attempt 3 specifically: a single green at one depth is not an experiment. **Do not cite the
8→7→5 / 3→2→0 table as evidence that draining the queue fixes the gate.**

🔴 **The reusable lesson is about where to look, not about CI.** This arc spent days naming
"re-run on an unchanged sha" as its next probe while the answer sat in a store that
`subsystem_recall.py` prints in one read-only command — and this session DID run that command at
kickoff, but the recall featured `skills` in full and only listed `tests`, so the relevant bullet
was one `--ref` away and never opened. **When a doc names a probe as open, grep the store for it
before running it.** Related: `devrc#943` is open on the sibling defect (a failing status that
does not name which of ~28 targets failed), which is why attempt 1's named test and PR #937's
`failed=0` are different shapes and must not be merged into one story.

### ✅ CLOSED — the audit ladder on #1108, and what SIX rounds on a 21-line docs PR actually taught
EVICTED 2026-09-01 (terminal). Verdict: **five of six rounds found the PREVIOUS round's correction wrong** — and 🔴 **the zero-payload ATTRIBUTION GATE CANNOT FIRE on a docs-only PR**: the payload IS the `.md`, so every fix round is 100% payload by construction and **stopping was a JUDGEMENT CALL, not a gate**. (A clean round still ends a ladder — `claude/RULES.md`. This tombstone previously asserted the inverse on both counts; corrected after audit.) Durable in store `devrc/skills.md` and `claude/skills/audit-pr/reference/round-ladder-evidence.md`.

### ✅ CLOSED — the red `devrc-ci` checks are a CAPACITY problem, and the repo had already diagnosed it
EVICTED 2026-09-01 (terminal, and PARTLY REFUTED — that is why this line stays). Capacity is real and measured in `devrc/tests.md`, but it was **not the right class for #1055**: see the CORRECTION block below, which is KEPT.
🔴 Also carried out of that block, because it is a RETRACTION and this pass's own contract says a
correction must be tombstoned rather than deleted: **"An equal test COUNT is not an equal TREE."**
The "same `collected` count, different verdict" control was RETRACTED before write-up — two count
groups each held BOTH verdicts, and each run carried a distinct `refs/pull/N/merge` preview sha.

### Both PRs are red on tests neither one touches — environment-sensitive, not code
- **Symptom + exact repro:** `gh pr checks 1055` → `tekton/devrc-pytests FAILURE`; same for #1064.
- **Observed (with values):** the named failure DIFFERS per run and per PR —
  #1055 `test_handles_resolve_to_the_exact_expected_paths[repos-HOMELAB-…]`;
  #1064 first `test_espanso_detect.py::test_live_existing_resolutions_not_made_ambiguous`,
  later `test_a_hanging_fetch_is_BOUNDED_and_the_memo_spares_a_second_wait`.
  Three other open PRs (#1046, #1057, #1059) all failed one *shared* test,
  `TestSkillDocsArePinned.test_the_pinned_docs_are_the_DEPLOYED_ones`.
  Every one of these reads host state: `nix eval`, the deployed `~/.claude/skills/` copies,
  live espanso config, or wall-clock timing.
- **Ruled out:** *"my change broke it"* — for #1055 the test is about `nix/agent-handles.nix`
  resolving `$HOMELAB` against a synthetic `/home/testuser`, and the diff is markdown plus two
  `claudedocs/` scripts; **10 passed at `origin/main` AND 10 passed at head on the dev-host
  tier**, and `origin/main`'s sandbox tier is green with a real build log
  (`collected=18703 passed=18701 failed=0 · RESULT: PASS`).
  *"main is red on the sandbox tier"* — killed by that same log.
  *"the shared hostPath nix cache causes concurrent-eval interference"* — killed: if it did,
  #1046/#1057/#1059 would fail the nix-eval test, and they fail a filesystem one instead.
- ~~**Leading hypothesis:** the affected population is exactly "tests that read host state".~~
  🔴 **SUPERSEDED — AND SO IS THE "loopback socket starved by the scheduler" FRAMING THAT REPLACED
  IT. THE MECHANISM IS DIAGNOSED AND IT IS DISK, NOT CPU: READ `scripts/ci-repro/README.md`
  FIRST.** `server.py:_replace_bytes` fsyncs the file and then the parent dir **INSIDE the request,
  before the response is written**, and `devrc-ci` is pinned to one node — so stacked runs contend
  on **one disk**. The README ships a compiled reproducer and names the code sites; the durable
  pointer is the 2026-09-01 bullet in store `devrc/tests.md`.
  🔴 **It hits PRs whose diff CANNOT REACH IT, docs-only included** — measured at 4 reds across 3
  DIFFERENT tests, every one passing 3/3 locally in ~5 s. So a red gate here is NOT evidence about
  your diff, and "the node is oversubscribed" sends you at CPU/capacity — i.e. at rank 10, which is
  LOCKED (`ci-speedup-7`). ⚠ An earlier version of this line asserted the loopback framing and
  cited `devrc/tests.md` as carrying it; that store file contains **no loopback bullet at all**
  (measured, with a positive control). The socket timeout is the SURFACE; the fsync-in-request is
  the CAUSE. The blocks after this one are kept only for the reasoning they rule out.
- 🔴 **PARTIALLY MITIGATED 2026-09-01 by #1211 `1a4350f3` (merged 19:30:17Z) — and the unit of
  "partially" is the CALL SITE — not the file, and not the fixture either (this line said
  "FIXTURE" until round 4; the table below is the authority).** #1211 sites the store on tmpfs so fsync cannot stall.
  Measured on `origin/main` **at `b59b0475`** (anchor it — bare "origin/main" moves), where
  `api.build_server` has **exactly three caller files**, and inside the one file #1211 touched there
  are **two** store fixtures:

  | site | sited on tmpfs? | `with running(...)` sites | `tmpfs\|/dev/shm\|store_siting` hits |
  |---|---|---|---|
  | `test_subsystem_store_api.py` `store` (`:393`, calls `_tmpfs_dir()`) | **yes** | 133 | 44 (file total) |
  | `test_subsystem_store_api.py` `scoped_store` (`:9097`, `_build_store(tmp_path / "store", …)`) | **NO** | 110 | — |
  | same file, **UNFIXTURED** `running(served\|stage\|root\|tmp_path/"absent")` | **NO** | 13 | — |
  | `test_cairn_write.py` | **NO** | — | **0** |
  | `test_cairn_cli.py` | **NO** | — | **0** |

  🔴 **The unit is the CALL SITE, and that third row is the one nobody has counted.** Of **256**
  real `with running(...)` sites in that file, **123 are disk-backed** — 110 in `scoped_store` and
  **13 that belong to no fixture at all**, built straight from `tmp_path` (`:1481, :1501, :3938,
  :3959, :4011, :4087, :4134, :4360, :4377, :9968, :11755, :11852, :12584`). **Three of them —
  `:11755, :11852, :12584` — drive `post_bullet`/`PUT`, i.e. `_replace_bytes` itself**, the exact
  in-request fsync this block is about. 🔴 **#1219 does NOT reach them:** it re-sites the two
  *fixtures*, and these are not fixtures — so "the file is done once #1219 merges" will be wrong in
  the same way "the file is done because #1211 landed" was. This is the third granularity this arc
  has been wrong at: file → fixture → call site. Assume there is a fourth.
  ⚠ **Derive this partition with an AST walker, and beware that the naive grep is RIGHT BY
  CANCELLATION.** `grep -c 'running(store'` reads **133**, which is the correct answer reached by
  two equal and opposite errors: it **misses 3 line-wrapped** `running(\n store, …)` sites
  (`:8764, :8792, :8832`) and **over-counts 3 lines inside a `textwrap.dedent("""…""")` string**
  (`:8017, :8033, :8300`). Full partition of the 272 lines containing `running(`: **256** real
  sites · 4 comments · **11 inside string literals** · 1 `def running(`.
  🔴 **This paragraph previously published 136/259 and claimed a walker produced them — both were
  wrong, and the mechanism is the joke of the arc:** a *text* walker that resolved the wrap but not
  the string counted a `textwrap` block as production code, and that block is **another walker's
  positive-control fixture**, sitting under the comment *"the detector must be able to SEE a
  misplaced call, or the empty list above is a fact about the walker and nothing else."* The
  instrument was fooled by the fixture built to test exactly that. Use `ast.walk` over
  `With`/`AsyncWith` items — it cannot see comments **or** strings, and resolves wraps for free.

  `scripts/testlib/store_siting.py` — the shared siting #1219 introduces — **does not exist at that
  ref**, which is the one-command check that #1219 has not landed yet.
  The cairn half is demonstrated too, not just inferred from a zero: the first PR gated after #1211
  merged (#1213 `063b02be`, pending 19:37:09Z) went red at 20:00:31Z on **`TestAppendLands`** in
  `test_cairn_write.py:250`, against a diff of one markdown file.

  🔴 **So a red on `test_subsystem_store_api.py` IS still very possibly this mechanism** — do not
  send yourself off for a fresh diagnosis on the strength of the file's name. The live instance:
  **#1216 `1e5942a8`, a diff of ONE markdown file, went `devrc-pytests FAILURE` on
  `TestTheActorComesFromTheTOKEN`** — a class that runs on `scoped_store`, i.e. on the contended
  disk, and the same class `scripts/ci-repro/README.md:79,83` uses as its reproducer. #1219's own
  code says this in as many words (`22dd33df:scripts/tests/test_subsystem_store_api.py:9033-9037`:
  *"It was missed when #1211 sited `store` alone"*).
  🔴 **And "sited" is not "proven sited on the CI node":** `_tmpfs_dir()` falls back to `tmp_path`
  (disk) on four conditions, and #1211's own message notes CI's `/dev/shm` is the container's mount,
  64Mi by default and **possibly absent**. Nobody has verified the probe engages on
  `talos-xr6-r7p`, so read #1211 as removing the mechanism *wherever the probe succeeds*, which is
  unmeasured where it matters. The consolidation is devrc**#1219** (`fix/consolidate-store-siting`),
  **OPEN as of 2026-09-01** — re-derive its state before quoting any of this.
  ⚠ `scripts/ci-repro/README.md` — which this block sends you to, and which remains correct about
  the mechanism, the code sites and the reproducer — carries **no mitigation note at all**
  (grepped `tmpfs|1211|mitigat|shm` at `origin/main`: **zero hits**), so read on arrival there as
  describing a failure mode still live on **123 of that file's 256 `with running(...)` sites, plus
  both cairn suites** — not on all of it, and not on none.

### Was the devrc-ci outage fully closed?
- **Symptom:** 2026-08-29 ~22:23–22:48Z every devrc-ci gate leg reported
  `COULD NOT RUN: the gate stopped before this leg reported`; 8 PRs blocked.
- **Observed:** `pods "devrc-ci-…-gate-pod" is forbidden: violates PodSecurity "baseline:latest":
  hostPath volumes (volume "nix-cache")` — `6bec075e` gave the gate a hostPath cache and the ns
  had no PSA exemption, so the pod was rejected at ADMISSION and never created (hence notify+report
  pods but no gate pod).
- **Ruled out:** congestion/preemption (the `tekton` skill's documented 255 signature) — this is
  `PodAdmissionFailed`, a different reason string entirely.
- **Resolved by someone else:** `686d6ff0` at 22:48Z added
  `pod-security.kubernetes.io/enforce: privileged` to ns `tekton-ci`. Verified by the boundary:
  **5 `PodAdmissionFailed` before, 0 after**, across 14 devrc-ci TaskRuns.
- **Next probe (the part still open):** the residue is ordinary node congestion —
  `ExceededNodeResources`, `talos-xr6-r7p` at 89% CPU requests. The gate pod requests **4250m**
  while three sampled running pods used **1m / 4m / 544m**. That is a lead, not a licence:
  measure PEAK over a full run before proposing a right-size, and note the `tekton` skill records
  four *other* fixes as already-rejected-with-measurements.

### Both PRs of this arc are red on tests neither one touches — one datapoint AGAINST, not a resolution
- **New evidence (2026-08-30):** devrc**#1092** — a diff that touches `scripts/run-tests.sh`,
  `nix/home.nix`, `register-nudge-hook.py` and four hook test files — came back
  `tekton/devrc-pytests SUCCESS` **and** `tekton/devrc-nodetests SUCCESS` on the first run,
  with no retry. So the gate is not unconditionally red for this arc's PRs.
- **What that does and does not license.** It is consistent with the leading hypothesis
  (environment/timing-sensitive tests that read host state, made worse by node congestion) and
  it refutes nothing about #1055/#1064 specifically — their failing tests are still
  unattributed and were never re-run on an unchanged SHA. 🔴 **Do NOT record this as "the CI
  problem is fixed".** One green run at one moment cannot distinguish "the runner is fine now"
  from "the queue happened to be empty", which is the same empty-result trap the rest of this
  doc is about.
- **Next probe, UNCHANGED and now cheaper to interpret:** re-run #1055's gate on an unchanged
  SHA and see whether the SAME test fails. A different test ⇒ environment/timing; the same test
  ⇒ a real runner-vs-local divergence worth fixing in the test. #1092's green makes the
  "different test" reading more likely, not proven.

### The local suite's 76 failures are NOT this arc's, and the control says so
- **Symptom + exact repro:** `python3 -m pytest scripts/tests -q` in a worktree of
  `zach/handoff-write-guard` → **76 failed, 10368 passed, 1 skipped** in 15m28s.
- **Observed (with values):** the SAME command in a clean worktree of `origin/main`
  (`4ca8d662`) → **76 failed, 10368 passed, 1 skipped** in 16m19s, and
  `diff <(grep ^FAILED base) <(grep ^FAILED branch)` is **EMPTY — the two failure sets are
  byte-identical**. A positive control was run on the comparison itself (delete one line from
  one side; `diff` moves), so the empty diff is a measurement and not a comparator wired to
  nothing. The four suites that read the files this diff touches
  (`test_run_tests_floors.py`, `test_run_tests_preconditions.py`, `test_run_tests_timing.py`,
  `test_nogit_isolation.py`) give **49 failed / 38 passed on BOTH** sides.
- **Ruled out:** *"the new target's floor entry broke the two-way pin"* — the floors suite fails
  identically without this diff. *"the new HERMETIC_TARGETS entry broke collection"* — same.
- **Leading hypothesis:** these suites need the nix dev shell (`nix develop`), and a bare
  `python3 -m pytest` is missing tools they probe for; `assert None is not None` is the shape.
  The in-cluster gate, which DOES build that shell, is green on the same tree.
- **Next probe:** run one of them under `nix develop ~/workspace/devrc -c python3 -m pytest …`
  and see whether it goes green — that would close it. It is NOT this arc's problem either
  way; it is recorded so the next session does not re-derive the attribution.

### Rank 6 RE-MEASURED 2026-08-30: the tekton skill was edited TODAY and is still wrong
- **Why this is worse than "still open".** Another session landed devrc**#1096** (`abdd44b7`,
  *"docs(tekton): two ADMISSION-class causes of a red devrc-ci check that are NOT your diff"*)
  on `main` today. It is **+21 lines, 0 deletions** to `claude/skills/tekton/SKILL.md` — rank 6's
  exact target — and it **did not touch any of the stale claims rank 6 exists for.** So the file
  now carries a today's-date edit alongside content that is wrong, which is the strongest
  possible false signal of freshness for the next reader.
- **Observed (with values), read off `origin/main` at `55fca8ff`:** the claims are still there,
  with line numbers — `nodeSelector` node-pinning at **39**, the `nix-store-cache` PVC at **41**,
  the "cold cache ≈ 3 min" tradeoff at **45**, burst-stacking at **48**, and a second
  `talos-xr6-r7p` + `nix-store-cache` description at **430**. Measured 2026-08-29 against the
  live gate: **no `nodeSelector`**, and a per-node **hostPath** at
  `/var/lib/mnt/disk-1/devrc-ci-nix-cache`.
- **Ruled out:** *"#1096 already did rank 6"* — killed by `git show --stat abdd44b7`: one file,
  21 insertions, **0 deletions**. An additive edit cannot have corrected a claim.
- ✅ **CLOSED 2026-08-30 — and the re-measure REFUTED the item, not the skill.** The live gate
  is `nodeSelector kubernetes.io/hostname=talos-xr6-r7p` with `nix-cache` →
  `persistentVolumeClaim: nix-store-cache` (30Gi Bound on that node; `gitops-validate` has its
  own `nix-store-cache-2` on `talos-uvh-gtj`), read off `devrc-ci-vchxk-gate-pod`. **Not one of
  lines 39/41/45/48/430 needed changing.** homelab-infra `6bec075e` (08-29T22:09Z) introduced
  the hostPath and `7839ef54` (08-30T00:29Z) reverted it — a **2h20m** window, and the 08-29
  measurement landed inside it.
- 🔴 **THE TRANSFERABLE LESSON, and it inverts the one this block was written to teach.** The
  block above warned that an ADDITIVE edit is a false FRESHNESS signal. The symmetric error is
  the one that actually happened: **a correct live measurement is a false STALENESS signal once
  the thing it measured moves back.** A doc-correction item carries a measurement whose subject
  is mutable, so the item decays exactly like the ranked backlog it sits in — and here it decayed
  in under 24 hours, into an edit that would have INTRODUCED the error it was filed to remove.
  "Re-measure before editing" was in the item and is what saved it; **write that clause into
  every doc-correction item whose evidence is a live reading, not a git fact.**
- **What the re-measure DID find, shipped as devrc#1108:** gotcha 6(a) closes on *"the narrow fix
  is a baseline-compatible cache"* and never records that the hostPath was reverted, nor why it
  cannot simply return — `DirectoryOrCreate` is created by kubelet as **root**, so `seed-nix`
  fills it and nothing in the sandbox can take the nix DB lock (`big-lock: Permission denied`,
  **75 occurrences over 42 tests, on every devrc PR**, byte-identical on two branches, against a
  pre-change run of `failed=0` over 18,557 passed). That is a SECOND blocker, independent of
  PodSecurity, and merging the two into one story is the empty-result trap this doc keeps naming.

### The CI investigation now has evidence from ANOTHER session — read it before re-deriving
- **New (2026-08-30):** devrc#1096 documents **two ADMISSION-class causes of a red `devrc-ci`
  check that are NOT your diff.** That is the same class this doc's CI block has been circling,
  measured independently. **Read `claude/skills/tekton/SKILL.md` around line 253** (`baseline`
  PSA forbidding hostPath, `6bec075e`'s cache swap, `PodAdmissionFailed`) before running any new
  probe on #1055/#1064's failures.
- **What it does NOT settle:** #1055's and #1064's specific failing tests are *not* admission
  failures — they are tests that read host state and that this doc already attributes to
  environment/timing. The admission class explains a gate leg that never ran; it does not explain
  a test that ran and failed. 🔴 **Do not merge the two into one story** — that is the
  empty-result trap (an absence has many causes, and confirming one proves nothing about
  another). #1092 going green on both legs is consistent with both readings and settles neither.
- **Next probe (unchanged):** re-run #1055's gate on an unchanged SHA. A *different* test ⇒
  environment/timing; the *same* test ⇒ a real runner-vs-local divergence worth fixing.

### Rank 9's "quiet queue" is a ONE-NODE CPU-REQUEST CEILING of five gate pods, not a queue that drains
- **Symptom + exact repro:** devrc-ci gate pods sit `Pending` for minutes while other work runs, so
  rank 9's stated signal (`0 Pending pods`) is rarely observable. Reproduce:
  `KUBECONFIG=$KC_HOMELAB kubectl -n tekton-ci get pods --field-selector=status.phase=Pending`
- **Observed (with values), all 2026-08-31 ~16:50–17:05Z on the homelab cluster:**
  - 🔴 **The cluster is `$KC_HOMELAB`, NOT dp-prod.** This session's default `KUBECONFIG` is
    `prod-kubeconfig`; `kubectl -n tekton-ci get pipelineruns` there returns
    `No resources found in tekton-ci namespace.` — a confident empty that is a fact about the
    wrong cluster. Only `KC_HOMELAB` has the namespace (`tekton-ci Active 45d`).
  - Scheduler event on both pending gate pods, verbatim: `0/4 nodes are available: 1 Insufficient
    cpu, 3 node(s) didn't match Pod's node affinity/selector.`
  - The pin is a **hard `nodeSelector`, not a soft affinity**:
    `kubectl -n tekton-ci get pod <gate-pod> -o jsonpath='{.spec.nodeSelector}'` →
    `{"kubernetes.io/hostname":"talos-xr6-r7p"}`; `.spec.affinity.nodeAffinity` is **empty**.
  - `talos-xr6-r7p`: allocatable **15950m**, requested **14320m (89%)**. Of the 14220m resolved
    across its 67 running pods, **11,250m (79%) is the five gate pods** (5 × `devrc-ci-*-gate-pod`
    at **2250m** each); 11,700m is the whole `tekton-ci` namespace share, which is the wider figure
    and was once mis-stated here as the gate's own. Non-CI baseline on the node is only ~2520m (largest single non-CI pod:
    `tekton-triggers-core-interceptors` at 350m).
  - Per-pod request is **2250m**, from two steps: `step-pytests=2` and `step-nodetests=250m`
    (`-o jsonpath='{range .spec.containers[*]}{.name}={.resources.requests.cpu}{"\n"}{end}'`).
  - Arithmetic ceiling: 5 × 2250 + 2520 ≈ 13770 fits; a 6th needs 2250m against ~1630m free.
    **Measured state matched exactly: 5 gate pods `NotReady` (3/6 steps, i.e. running), everything
    beyond them `Pending`.**
- **Ruled out:**
  - *"The node is squeezed by unrelated workloads"* — **killed by the breakdown above**: **79%**
    (11,250m of 14,220m) of the node's requests are the gate's own five pods — not the 82% an
    earlier version of this line quoted, which is the whole `tekton-ci` namespace share. Saturation is self-inflicted, so it **does** drain and
    `0 Pending` **is** reachable; it just needs fewer than 5 devrc PRs in flight, and ~21 PRs are
    open with ~10 sessions pushing.
  - *"rank 10's 4250m premise"* — **REFUTED by measurement: the gate pod requests 2250m.** Whatever
    rank 10 proposes must be re-derived from 2250m, and from the fact that the binding constraint is
    the `nodeSelector`, not the request size alone.
- **Leading hypothesis:** the real unblock for rank 9 is un-pinning or right-sizing that pod — which
  is **already claimed by another session** as `ci-speedup-7` ("retry the devrc-ci node unpin behind
  a nix-store-ownership probe on a SCRATCH pipeline"). Rank 9 is therefore *waiting on* that work,
  not merely on a window. **Do not start it — it is locked.**
- **Next probe:** poll for the window rather than reasoning about it. This session armed
  `scratchpad/watch-queue.sh` (a 45 s poll emitting on 0-pending and on `szfm7` terminal); re-create
  it or run inline:
  `KUBECONFIG=$KC_HOMELAB kubectl -n tekton-ci get pods --field-selector=status.phase=Pending --no-headers | wc -l`

### #1064's named failure is WALL-CLOCK-BOUNDED — a mechanism for the capacity story, not just a covariate
- **Symptom + exact repro:** `gh pr checks 1064 --repo innovation-upstream/devrc` →
  `tekton/devrc-pytests fail — FAILED: pytests — FAILING:
  test_a_hanging_fetch_is_BOUNDED_and_the_memo_spares_a_second_wait | TOTAL collected=18718
  passed=18713 skippe…` (truncated by GitHub's 140-char status limit). nodetests: **1366/1366 pass**.
- **Observed (with values):**
  - The test is `scripts/tests/test_resume_state_skill_freshness.py:1003`. It asserts
    **`assert 20 <= elapsed < 60`** around a production `timeout 25`, and **`memo_secs=0`** computed
    from bash integer `$SECONDS`. **Two of its four assertions are wall-clock upper bounds**; the
    other two (`first=1`, `second=1`) are logical.
  - **#1064's diff cannot reach it.** `gh pr diff 1064 --name-only` → exactly three files:
    `scripts/README.md`, `scripts/handoff-audit.py`, `scripts/tests/test_handoff_audit.py`.
  - Estate-wide sweep of all 39 open PRs' `tekton/devrc-pytests` head status: **9 `failure`
    naming 7 DIFFERENT tests**, **6 `COULD NOT RUN`**, 9 `success`, 8 pending, 7 no-status. The only
    repeated signature is `TestTheActorComesFromTheTOKEN.test_a_FORGED_actor_in_the_body_is_DISCARDED`
    (#1126, #1087, #985). `test_a_hanging_fetch_is_BOUNDED…` appears on **#1064 alone**.
- **Ruled out:** *"#1064 broke something"* — the diff does not touch the failing test's module or the
  `resume-state` script it exercises.
- 🔴 **NOT established — say so rather than closing it.** I could **not** determine WHICH assertion
  fired. #1064's run is **pruned from the cluster** (only 240 pipelineruns retained; no
  `pr-1064` row survives) and the GitHub status carries **no `target_url`**, so there are no logs —
  this is devrc**#943**'s sibling defect biting again. A scatter of one-off failures across
  unrelated PRs is consistent with contention, but it remains a **sample, not a controlled
  demonstration**; the WALK-BACK block's caution stands unchanged.
- **Next probe:** on the next #1064 run, capture the pytest output **while the PipelineRun still
  exists** — `kubectl -n tekton-ci logs <gate-pod> -c step-pytests | grep -A20 'hanging_fetch'` —
  so the failing assertion is named before the run is pruned.

### 🔴 CORRECTION, same session — #1055 was NOT red on the capacity flake. It was 94 commits STALE, and a re-trigger could never have fixed it
**This supersedes both the doc's "Both are still RED on the capacity flake — neither caused it" and
this session's own earlier framing above, which treated #1055 and #1064 as one story. They are two
different failures and only one of them is environmental.** The earlier blocks are left in place
deliberately: the wrong reading is the point.

- **What broke the assumption:** `devrc-ci-szfm7` (the run in flight at kickoff) FAILED at 20m, and
  its logs were captured **before the PipelineRun was pruned** — the probe the block above lists as
  its next step, run for real. Verdict step, verbatim:
  `pytests fail FAILING: test_live_existing_resolutions_not_made_ambiguous | TOTAL collected=18695
  passed=18691 skipped=2 failed=2`
- **It reproduces DETERMINISTICALLY and locally, in 0.18 s — no cluster, no contention:**
  `cd <worktree-of-origin/zach/skill-chain-usage-audit> && python3 -m pytest
  scripts/collector/keylog/tests/test_espanso_detect.py -k live_existing_resolutions -q`
  →
  `AssertionError: search terms regressed: {'ask': (':acq', None, [':dacq', ':acq']),
  'clarify': (':acq', None, [':dacq', ':acq'])}` — the terms now match **two** snippets, so
  `_attribute` returns `None` (ambiguous) instead of `:acq`.
- 🔴 **THE CONTROL, and it is what settles it. Same test, same command, at `origin/main`
  (`c2daa65d`): `1 passed in 0.17s`.** Red on the branch, green on main ⇒ the defect is the
  branch's **staleness**, not the gate, not the node, not the queue.
- **The missing commit is named:** `31cd214d` *"fix(espanso): both snippets may spell \"ask\" —
  attribution gets a declared owner instead of the picker losing a route (#1060)"*, on `main`, not on
  the branch. `git rev-list --count origin/zach/skill-chain-usage-audit..origin/main` = **94**.
- **REMEDY APPLIED: `git merge origin/main` into the branch, then re-ran the same test → `1 passed
  in 0.12s`.** Red before, green after, on the exact failing path. A rebase/merge, never a
  re-trigger; a re-trigger would have failed identically forever.
- 🔴 **NOT claimed: that the gate will now go green.** The summary said **`failed=2`** and named
  **one** test — devrc**#943**'s defect, the status that will not say which of ~28 targets failed.
  The second failure was never named and I did not run the full 18,695-test suite. **Merging main
  fixes the failure I reproduced; it is not evidence about the other one.**
- **#1064 is the OPPOSITE case and the earlier block's reading survives for it.** Its named test
  `test_a_hanging_fetch_is_BOUNDED_and_the_memo_spares_a_second_wait` **PASSES on its own branch**,
  run locally at `origin/feat/handoff-audit` (`466a0938`): `1 passed, 41 deselected in 25.18s` —
  i.e. it costs ~25 s by construction and its assertion is `20 <= elapsed < 60`, so it passes with
  ~35 s of headroom on an idle machine and is exactly the shape contention eats. **One measurement
  on one machine, not a general claim.** #1064 is also **93 commits behind** main (as read that day; it was 124 when rank 11 ran — re-derive), so it should be
  merged forward too — but for it, that is hygiene, not the diagnosis.
- **Reusable tell, and it is the cheap one this arc kept skipping:** before attributing a red gate to
  capacity, **run the named failing test locally at the branch AND at `main`.** Two commands, sub-second
  here, and they separate "stale base" from "environment" without a cluster. The arc spent days on
  the queue-depth story with `git rev-list --count <branch>..origin/main` never run.

### ✅ CLOSED — #1055's `failed=2` residual IS `test_the_skill_did_not_grow`, and fixing the first failure is what named it
EVICTED 2026-09-01 (terminal). Verdict: `failed=2` was espanso + the skill ratchet; **a `failed=N` naming one test is N-1 UNKNOWN**. Durable in store `devrc/tests.md` (RESOLVED `eb1eb185`).

### ✅ VERDICT READ — #1055 is GREEN at `eb1eb185`, and `failed=2` is fully accounted for
EVICTED 2026-09-01 (terminal). Verdict: `failed=2 -> 1 -> 0`, one per fix; #1055 MERGED `8c108d8b`. In `## State now`.

### ✅ CLOSED — rank 4 needed NO deploy: the fix was already live, and `stat` nearly said otherwise
EVICTED 2026-09-01 (terminal). Verdict: no switch was needed or run. The two traps (`stat` vs `stat -L`; a no-op switch still ships a dirty tree) are durable in store `devrc/skills.md`.

### ✅ CLOSED — rank 11: #1064 is GREEN, and its diagnosis is now confirmed from BOTH arms
EVICTED 2026-09-01 (terminal). Verdict: #1064 green, MERGED `f71ff648`; the two-arm discriminator returned the OPPOSITE verdict to #1055, which is what shows it discriminates. In `## State now` + rank 11.

### ✅ CLOSED — the 6-round audit ladder on #1207, and the ratio that is the actual finding
- **Outcome:** round 6 returned **zero findings at any severity**; the ladder ended there and no
  round 7 was run (`claude/RULES.md`: a clean round ENDS a ladder, never confirm one).
- 🔴 **5 of 6 rounds found a defect created by the PREVIOUS round's fix** — the same ratio #1108
  measured. Totals: **3 🔴, 12 🟡, 10 🟢**. Ledger `round 6 · payload lines THIS round: 8 · since
  round 1: 151`. Cost ≈ **850k subagent tokens** to shrink one document by ~16 KB.
- 🔴 **All three 🔴 were CERTIFICATIONS, not omissions — that is the reusable shape.** (1) a
  tombstone stating the INVERSE of `RULES.md` and of the store; (2) a line CERTIFYING a superseded
  CI mechanism while citing a store file that contained no such bullet; (3) a shipped derivation
  command that **matched its own prose**, marking rank 8 done and hiding the arc's closing
  condition. The failure mode is *asserting* durability, not forgetting it.
- **Ruled out:** *"the attribution gate will terminate this ladder"* — it cannot; the payload is
  the `.md`, so every round is 100% payload by construction and the gate is structurally inert.
  What ended it was the stated criterion (no 🔴 · no blast radius past "a false sentence" · the
  recurring shape swept at EVERY site). via: measurement
- **Ruled out:** *"a docs-only PR is low-stakes"* — two of the 🔴 had real blast radius: one would
  have routed a reader at capacity work the same doc marks LOCKED (`ci-speedup-7`), the other
  corrupted the doc's own mandated derivation. via: measurement
- **Next probe:** none — closed. If a future ladder is opened on a prose PR, read
  `~/.claude/analyze-service-index/devrc/skills.md` (2026-09-01 bullet) first; it carries this
  ratio and the four reusable rules.

### ✅ CLOSED (rank 13) — CROSS-PR STALENESS, ALREADY BITING: the CI mechanism this doc documents was PARTIALLY MITIGATED 33 minutes BEFORE #1207 merged
🔴 **Heading corrected 2026-09-01 — it read "was MITIGATED", flat, and headings here are machine-read (`handoff-audit.py` buckets on the H3 alone). It still fires: see the fixture/call-site table in the CI block.**
- ✅ **CLOSED 2026-09-01 — caveat written; values in the CI block and rank 13, not repeated here.**
  🔴 **The one thing that belongs only here:** this item was filed because a sibling change (#1211)
  falsified the doc's claim — and the *item written to repair that* was itself falsified by a second
  sibling change before anyone worked it. 🔴 **The window is the finding, and it is far shorter than
  it feels: rank 13 was filed at 21:00:22Z (`66eee1d7`) and worked at 21:41:58Z (`afe68628`) — 41 m
  36 s — and #1219, which refutes its premise, was opened at 21:11:38Z, ELEVEN MINUTES after
  filing.** The mechanism still fires; only "should no longer fire" was wrong. **A staleness-repair
  item decays exactly like the claim it repairs**, and on this repo the half-life is minutes, not
  days — re-measure the REMEDY's premise, not just the original claim. That is rank 6's lesson
  ("re-measure before editing") arriving one level up, and rank 6 is where it was already written
  down. via: measurement
- **Symptom:** the doc's superseded-hypothesis block (the one round 4 corrected) tells a reader the
  `devrc-ci` red-gate mechanism is `server.py:_replace_bytes` fsyncing inside the request against a
  one-node pin, and to read `scripts/ci-repro/README.md`. That was correct when written.
- **Observed (with values):** **#1211 `1a4350f3` merged 2026-09-01T19:30:17Z** — *"site the store on
  tmpfs so the gate stops failing on disk contention"* — ~~i.e. the mechanism is **mitigated**~~
  (**superseded: PARTIALLY, at CALL-SITE granularity — see the fixture/call-site table in the CI block, `## Open investigations` → the PARTIALLY MITIGATED bullet**). #1207
  merged at **20:03:27Z**, 33 minutes LATER, carrying the un-caveated warning. Also landed since:
  **#1213 `793a2b8e`** (the store-api gate flake: tmpfs fix shipped, 8-red triage, a classifier that
  reads the checkout PATH) and **#1214 `07a22f14`**.
- **Ruled out:** *"the doc is simply wrong"* — it is not; the mechanism and the reproducer are real
  and `scripts/ci-repro/README.md` still documents them. What is missing is the **mitigation**, so a
  reader will expect a failure mode that ~~should no longer fire~~ **mostly still fires** (see the fixture
  table in the CI block — this line's premise is the one that was refuted). via: change
- **Leading hypothesis:** this is the arc's own recurring shape operating ACROSS PRs rather than
  within one — a claim true at write time, falsified by a sibling change, inside no audit round's
  range. Six rounds could not have caught it: #1211 was not in any range.
- **Next probe:** none — closed, and 🔴 **do not run the probe this bullet used to carry.** It said
  *"confirm the tmpfs siting, then add a one-line MITIGATED caveat"*, in the flat singular framing
  three audit rounds then refuted; following it now re-introduces the error. What was actually
  done, and what a reader should copy instead, is the fixture/call-site table in the CI block.
  🔴 Do NOT delete the mechanism text: the reproducer and code sites remain the durable content.

### ⚠ STILL UNATTRIBUTED — the whole-class `TestPushabilityCasesTheFetchVersionGotWRONG` failure
- **Symptom + exact repro:** one combined run of `scripts/tests/test_handoff_audit.py` +
  `scripts/tests/test_handoff_doc.py` produced **6 failures**, all in that one class.
- **Observed (with values):** the class contains **exactly 6 tests**, so this was the ENTIRE class,
  not 6 scattered flakes — the signature of a shared precondition. **13 clean runs against 1
  failure** (4 mine + 9 round 2's); base clean 5/5; failing run wall time **20.48 s** against a
  18–22 s norm, so **not load**. All 6 drive the `--push` success/refusal path through
  `git ls-remote` against a local bare remote; the fixture neutralises `GIT_CONFIG_GLOBAL`/
  `GIT_CONFIG_SYSTEM` but still inherits `os.environ`.
- **Ruled out:** *"my diff caused it"* — the diff is one markdown file and touches no fixture; the
  same command passed 4× on the same tree immediately after. via: measurement
- **Ruled out:** *"it is load"* — wall time was normal, and load inflates every test in a run, not
  one class. via: measurement
- **Leading hypothesis:** the same disk-contention family as the store-api flake (`#1181`/`#1211`)
  — that one is recorded as hitting *"PRs whose diff cannot reach it, docs-only included"*, 4 reds
  across 3 different tests, each passing 3/3 locally. 🔴 **NOT the same observation**: mine was a
  LOCAL run and that one is `devrc-ci`. It is a lead, not an attribution.
- **Next probe:** if it recurs, capture `/proc/loadavg` and `iostat -x 1 3` at failure time, and
  re-run the class alone vs combined to test the shared-precondition reading.

## Next steps (ranked)

🔴 **The numbering below is STABLE and load-bearing** — `claim-work --slug-for <this doc> <rank>`
makes the rank half of every claim's identity, so completed items are marked DONE in place
rather than removed and renumbered. New work is appended at the end.

1. ✅ **DONE (2026-08-29)** — the end-state split: 8 cleanly-ended vs 2 context-exhausted of 16.
   forcing: none
2. ✅ **DONE (2026-08-30)** — the handoff-write `Stop` hook. **MERGED as `ad891a5c`.**
   forcing: none
3. ✅ **DONE (2026-08-30)** — the question as posed does NOT discriminate: 95.5% of abandoned
   docs declare open work against **90.3% of maintained ones**. Instrument
   `claudedocs/skill-chain-drift-audit.py` (committed, 4 controls). Its one open thread is rank 12.
   forcing: none
4. ✅ **DONE (2026-09-01) — NO deploy was needed and none was run.** The store copy was already
   byte-identical to `origin/main` (15086 B, `preserve both` = 0).
   forcing: none
5. **Build the handoff-doc eviction contract.** 🔴 **The WORKED EXAMPLE is DONE (#1207
   `4162dab1`); the CONTRACT is not.** Four requirements it produced are recorded in this item's
   own text in the doc: tombstone-not-deletion · the rank NUMBER must survive · assert structure
   before writing · 🔴 **the measurement is NOT IDEMPOTENT** (`handoff-audit.py` buckets a resolved
   investigation on its **H3 heading alone**, so it still reports "8 resolved investigations" over
   8 one-line tombstones — an automated `EVICT_HISTORY` would re-evict tombstones and DELETE them).
   🔴 **Fix that signal before automating anything.** Before proposing a GATE, re-run the ratchet
   replay for handoff docs: the caps file for gate 11 records the general rule REFUTED over 365
   days / 901 commits. Files: `scripts/handoff-audit.py`, `claude/skills/handoff/SKILL.md`.
   forcing: none
6. ✅ **DONE (2026-08-31)** — premise REFUTED by the re-measure it demanded; six audit rounds, five
   of which refuted their predecessor. **MERGED as `57b010fb`.**
   forcing: none
7. ✅ **DONE (2026-08-30)** — merged, shipped to both hosts, registered, probed live.
   forcing: none
8. 🔴 **Grade the hook against the number it was built to move — THE ARC'S CLOSING CONDITION.
   STILL NOT CUT, and deliberately so.** Re-run rank 1's measurement on a post-2026-08-30T17:35Z
   window and compare the **8.7%** loss rate and the **8/16 cleanly-ended** bucket. As of
   2026-09-01 the post-window holds ~1 day against a pre-period that needed **14 days for 253**
   `/resume`-genesis sessions. Cutting now grades on tail samples — the defect that killed
   talos-infra #901.
   🔴 **RANK 1's CONTROLS AND BAND, restored here because rank 1's block is evicted and its
   classifier is GONE:** probe positive control **32 of 48** readable loser transcripts (so the 16
   zeros are absences, not a dead probe); compaction detector **weak, 20 of 5,961**; context peaks
   **944,856 (0.94)** and **965,819 (0.97)** against 1M, every other session 0.29–0.60; **three
   sessions have an unresolvable ceiling ⇒ the floor is `cleanly-ended ≥ 7`, NOT exactly 8**;
   denominator caveat — 3 loser transcripts are laptop-only and 29 sessions were dropped, so
   **treat the bucket SHARES as the finding, never the denominator**.
   🔴 **Treatment integrity: the deployed blob must still be `6d25558e` ACROSS THE WHOLE WINDOW**,
   not just at the end — any unrelated `ship.sh` replaces the generation. ✅ Verified intact
   2026-09-01 (deployed == `origin/main` == `6d25558e`), and rank 4 was skipped partly to keep it so.
   🔴 **Name the confound before cutting:** the guard changes the behaviour it measures, so a
   session that writes a handoff BECAUSE it was blocked is a success, not contamination — count
   blocks (`~/.cache/claude-handoff-write/s/*/fires-*`) and report them beside the rate.
   forcing: none
9. ✅ **DONE (2026-08-31)** — premise REFUTED twice; #1055 MERGED `8c108d8b`. The durable output is
   the two-arm local discriminator (branch vs `main`), not the PR.
   forcing: none
10. **Right-size the devrc-ci gate pod, or establish it cannot be.** 🔴 **Premise REFUTED: the pod
   requests 2250m, not 4250m**; the binding constraint is the hard `nodeSelector` pinning it to
   `talos-xr6-r7p`, not the request size. 🔴 **DO NOT START — LOCKED** by another session's claim
   `ci-speedup-7` (verified live 2026-09-01). 🔴 **And re-read the premise first: #1211 `1a4350f3`
   sited the store on tmpfs**, so the contention this item was motivated by may already be gone.
   ⚠ **CORRECTED 2026-09-01 — do NOT act on the sentence above without reading the CI block's
   fixture table first.** #1211 sited **one fixture of two** in one file of three; **123 of that
   file's 256** `running()` sites — the 110 in `scoped_store` PLUS 13 that belong to no fixture,
   three of them write-path — plus both cairn suites are still disk-backed, and the tmpfs probe is UNVERIFIED on
   `talos-xr6-r7p`. So the contention is **not** established as gone, and de-prioritising this item
   on that reading is the misroute rank 13 exists to prevent.
   forcing: none
11. ✅ **DONE (2026-09-01)** — `origin/main` merged forward into `feat/handoff-audit`, gate green,
   **#1064 MERGED as `f71ff648`**. It was 124 behind, not the 93 recorded — re-derive when you act.
   forcing: none
12. **Measure the RENAME-vs-SCOPE-MOVE split, then pick the drift remedy.** 🔴 Re-opened
   2026-09-01: this was a declared-open probe inside a block the eviction labelled `(terminal)`.
   Two candidate remedies — a `/handoff` step writing a `superseded-by:` pointer into X when the
   topic moves, or a `/resume` warning when the doc it opens has had no commit since the last
   session that read it. Which is right depends on whether drift is usually a **RENAME** or a
   **SCOPE MOVE**, and **that split has NOT been measured**. Read set = the 5 unlinked pairs named
   in the doc. Instrument `claudedocs/skill-chain-drift-audit.py` (4 controls, `DRIFT_CONTROLS=1`;
   a run whose control block is absent is void). CLOSES WHEN: the split is measured over those 5
   and one remedy shape is chosen in writing.
   forcing: none
13. ✅ **DONE (2026-09-01) — caveat shipped as PARTIALLY mitigated; the item's own premise ("should
   no longer fire") was REFUTED in the doing, and so was the FIRST version of the caveat.** Evidence
   in the CI block above. Mechanism text kept verbatim, as the item required. 🔴 The audit round
   caught that the first draft said "`test_subsystem_store_api.py` is now sited" — **false at
   fixture granularity** (`scoped_store`, 110 sites) **and then wrong again at fixture level —
   the unit is the CALL SITE, 123 of 256 disk-backed** — i.e. the fix for a
   one-of-three error repeated it one level down. That is the shape to expect, not a one-off.
   🔴 **Left undone deliberately — the next reader's, and it is a devrc-repo edit, not a doc edit:**
   `scripts/ci-repro/README.md` carries **no mitigation note** (zero hits for
   `tmpfs|1211|mitigat|shm` at `origin/main`), so the reproducer reads as fully live at the one place
   this doc sends people. 🔴 **CLOSES WHEN (a) ALONE — #1219 merging does NOT close it**, and the
   earlier draft of this item said it did. #1219 touches five files and **`scripts/ci-repro/README.md`
   is not one of them** (verified 2026-09-01), so its merge leaves this deliverable exactly as
   undone; it also leaves the *second* half of the caveat standing, because `store_siting.py`'s
   probe keeps a disk fallback and nobody has verified it engages on `talos-xr6-r7p`.
   **(a) = that README names `1a4350f3` AND says what is STILL unsited — and "still unsited" is
   three separate populations, so it is three greps.** 🔴 An alternation would certify all three by
   naming one, which is this arc's own recurring bug expressed as a regex:
   ```bash
   DEVRC=~/workspace/devrc
   git -C "$DEVRC" fetch origin main -q      # or a stale clone answers for a ref you do not have
   R=scripts/ci-repro/README.md
   git -C "$DEVRC" grep -c fsync        origin/main -- "$R"   # CONTROL first: must be 25
   git -C "$DEVRC" grep -c '1a4350f3'   origin/main -- "$R"   # (a1) the sha
   git -C "$DEVRC" grep -c scoped_store origin/main -- "$R"   # (a2) the unsited fixture
   git -C "$DEVRC" grep -c cairn        origin/main -- "$R"   # (a3) the two cairn suites
   git -C "$DEVRC" grep -cE 'unfixtured|11755' origin/main -- "$R"   # (a4) the 13 unfixtured sites
   ```
   The control runs INSIDE the block on purpose: `git grep -c` prints nothing and exits 1 on zero,
   so a copy-paste that omits the control returns four blanks that are indistinguishable from a
   broken probe. **(a) is met only when a1, a2, a3 AND a4 are all non-zero and the control reads 25.**
   🔴 **a4 was an advisory `⚠` until round 4 — i.e. the condition required three greps for FOUR
   populations, which is the same certify-by-naming-one bug as the alternation it replaced, one
   level further down and inside its own fix.** The population it omitted is the write-path one
   (`:11755, :11852, :12584`), the most on-mechanism of the four and the only one #1219 will not
   touch — so it was the worst possible one to leave optional.
   forcing: regression — the doc asserted a live failure mode that a merged change PARTIALLY
   mitigated, and it is read as authoritative at session start

## Gotchas / decisions / dead-ends

🔴 **This measurement took FOUR instrument corrections, and every uncorrected version
produced a confident, publishable, WRONG number. The corrections are the durable value
of this doc — re-read them before trusting any similar analysis.**

1. **Keyword search over skills is poisoned by the skill BODIES.** `task-pickup` matched
   183 sessions and `status=proposed` matched 325 — those count sessions where the SKILL
   was LOADED, not where the flow ran (the body contains its own keywords, and it is
   injected into the transcript on every trigger). **Search tool INPUTS and tool RESULTS,
   never prose**, when asking "did this tool run".
2. **`find-session` truncates `opened:` at ~120 chars** and the handoff doc path sits at
   the END. Keying the join on that path parsed **7 of 256** rows and returned a confident
   **100%**. Key on the TOPIC (early in the line) and resolve the full path from the
   transcript's untruncated first user message.
3. 🔴 **`git log` from HEAD missed 376 commits — 33% of the total.** These clones sit on
   unpredictable branches and run behind (`datapacket-talos` was 131 commits behind during
   this session). **Use `git log --all`** for any repo-wide historical count here. The same
   staleness also made 6 handoff docs look ABSENT from disk when they exist on
   `origin/trunk` — `git cat-file -e <upstream>:<path>` before believing a missing file.
4. **Topic drift breaks a filename join.** 25 sessions wrote a differently-named doc;
   demanding the same basename scored them as losses.
5. 🔴 **The end-state classifier's interrupt guard was SPELLED, not STRUCTURAL** (found
   2026-08-29 while doing item 1). Matching `Request interrupted` *anywhere* in a
   transcript scores a session interrupted at turn 40 that then ran 500 more turns and
   finished cleanly as "interrupted". **An interrupt anywhere is not an interrupted END** —
   record the row index and only count interrupts inside the final N conversational rows.
   Negative control: 2 sessions carry an early interrupt and must NOT bucket as interrupted.
6. 🔴 **An exhaustion ratio computed against an ASSUMED context ceiling is an artifact
   generator — and it announces itself with impossible values.** The transcript's model
   string is `claude-opus-5` and carries **no context tier**, so assuming 200k produced
   ratios up to **4.83** and scored **11 of 16** sessions as context-exhausted. The true
   figure is 2. A ratio above 1.0 is the tell; **infer the ceiling from evidence (a peak
   above 200k refutes the 200k tier) and mark it AMBIGUOUS where evidence is absent**,
   rather than defaulting a constant. Same class as gotcha 1: the number was wrong because
   the instrument's own parameter was never validated.

   Net effect of 2–4: the rate read **77%** before correction and **91.3%** after. Every
   error was in the instrument, none in the chain.

7. 🔴 **A DAY-GRANULARITY DATE COMPARED WITH `>` MAKES SAME-DAY WORK INVISIBLE — and in a
   corpus where most follow-up lands the same day, that is most of it** (found 2026-08-30
   doing rank 3). `docs.idx` is built with `--date=short`, so "did this doc get a commit
   after the session that abandoned it" was asked as `d > session_date` and every commit
   landing hours later on the same calendar day scored as **never**. The headline read
   **20 / 27**; with `>=` it is **13 / 26** — a **1.5×** overstatement, and the uncorrected
   version was internally consistent, plausible, and about to be written up. The tell was
   the `after` column reading **0 for literally every row**, including this arc's own doc,
   which had been committed four times that day. **A column that is constant across every
   row is a claim about your comparison, not about the world** — and prefer `>=` when the
   two directions are not symmetric: here it can only REMOVE docs from the finding.
8. 🔴 **THE OBVIOUS POSITIVE CONTROL WAS VOID, AND IT ANNOUNCED ITSELF AS "UNREADABLE"
   RATHER THAN AS FAILING.** Rank 3's predicate was to be positive-controlled on *this doc*
   — which declares open work by construction — and the control returned `UNREADABLE`,
   because the doc lives only on an unmerged branch and the resolver reads
   `origin/main|trunk|master`. A control that cannot see its subject is not a lenient
   control, it is **no control**, and a run that prints it beside three green ones reads as
   4/4. **Resolve controls FROM THE CORPUS** (scan for a doc matching the property) rather
   than pinning one by name: a pin rots the moment its items close, and it cannot notice
   that it was never read at all.
9. 🔴 **A PREDICATE SHOWN True ON ONE DOC AND False ON ANOTHER HAS ONLY BEEN SHOWN TO VARY
   WITH THE DOC.** The fix is one more control and it is cheap: take the doc that returned
   False, inject exactly the thing being detected, and watch it flip. Rank 3's
   `declares_open_work` was only trustworthy once `handoff-bridge-unbounded-waits.md` went
   `False ⇒ True` on a single injected un-done ranked item.
10. 🔴 **A DOC'S OWN STATUS PROSE GOES STALE INDEPENDENTLY OF ITS ITEMS — READ THE ITEMS.**
   Found *in the negative control*, not the population: `handoff-bridge-unbounded-waits.md`
   opens its ranked list with **"Only 5 and 6 are open"** while all seven items carry
   ✅ DONE. Items 5 and 6 were finished and the summary sentence was never touched. This is
   the staleness mechanism rank 3 went looking for, sitting in the instrument's own fixture
   — and it means any audit of these docs that keys on the header sentence measures a
   different, older document than the one keyed on the markers.

- **Dead end (do not re-derive): "handoff docs are written but never committed."** Checked
  across 154 repos — **zero** on-disk untracked handoff docs. The hypothesis is refuted.
- **Decision: no `clawgate-task:` field.** `clawgate_handoff.sh resolve` returned **rc 5,
  NOTHING RESOLVED**, with its positive control confirming the board was reachable and the
  token accepted. Per `/handoff` step 1 that is not a clean bill of health, it is an
  unresolved session — write no field, create no task.
- **Decision: worktree + PR, not an in-place push.** devrc's `CLAUDE.md` forbids committing
  to `main` in either host checkout (`ship.sh` converges with `merge --ff-only`, so a
  diverged host is skipped and silently stops receiving changes). `handoff_doc.py --push`
  would have pushed wherever the checkout sits, which was `main`.

- 🔴 **A LIST ITEM'S EXTENT MUST STOP AT THE NEXT HEADING — this class bit the same file THREE
  TIMES.** An H1 whose extent was the whole file; a 160-character "first line" bound that reached
  two lines into the body; and bullet/ranked-item walkers that ran to the end of the section,
  swallowing every H3 after them. The third booked **16,380 B for a bullet containing no
  retraction** and made one bucket **1.43× high**. Each time the *headline* survived and the
  *per-bucket* line did not. When you write a block walker, clip at the next heading AND exclude
  fenced lines from the predicate — the bytes belong to the block, the claim does not.
- 🔴 **DO NOT PIN ABSOLUTE CORPUS TOTALS IN A COMMENT — the corpus is live shared trees.** Three
  consecutive audit rounds each corrected the figure justifying one guard, and each correction was
  itself unreproducible by the next round. Measured across four rounds with nobody touching the
  code: **414→413→414 docs, advisory count 568→573→577.** The fix is structural — pin the quantity
  that is STABLE (the guard's own effect: 10 sections / 14,466 B / 0 + 0, identical every round)
  and tell the reader to re-derive corpus figures by running the tool.
- 🔴 **A borrowed matcher is only validated on the corpus it was validated on.** Reusing
  `skill-audit`'s `WORK_STATUS_HEADING` here keyed on a bare `\bsessions?\b`; a handoff doc's H1 is
  `# Handoff: <topic>` and topics like `session-makework-audit` contain the word, so entire
  documents scored as evictable history — **1,026,330 B / 343 blocks against a true 236,746 B / 119.**
- 🔴 **THIS CORPUS SHOUTS A TERMINAL STATUS, so case IS the discriminator.** Adding `re.I` to the
  resolved-heading matcher looked like fixing an inconsistency and was a pure regression: **10 of 10**
  new hits were inversions — `bounded, not closed`, `unresolved, and deliberately not resolved`,
  `was closed — the reusable lesson`, and `fail-closed` as a *term of art*.
- 🔴 **A COUNT assertion can survive its own inversion.** `assert len(x) == 1` passed with the guard
  inverted to scan only the wrong sections, because one item is booked either way; it died only to
  neighbours. Assert the booked item's TEXT.
- **Decision — stop an audit ladder on CONVERGENCE, not only on a clean round.** Four rounds; every
  one found something real, so the findings-keyed stop rule never fired. Executable payload per
  round ran **109 → 62 → 17 → ~15**, and all three of round 4's findings were about text the
  *previous* round wrote (a comment, a legend from that same commit, a commit message). Stopping was
  made safe by fixing the recurrence *class* (removing the volatile numbers) rather than its fourth
  instance.
- **Dead end (do not re-derive):** the shared-hostPath-nix-cache theory for the CI failures. Killed
  by the cross-PR table — the PRs that would have to fail the nix-eval test fail a filesystem one.
- **Decision — commit throwaway analysis scripts.** The previous pass left its classifiers in a
  session scratchpad as "throwaway", which cost rank 1 a full re-derivation (fresh population, fresh
  154-repo commit index, a rewritten classifier, two defects rediscovered). If a number will be
  quoted, its script belongs in the repo.

- **Decision — the guard's satisfaction anchor is each doc's FIRST READ, not the last work
  event, and that is the ONE place it diverges from its precedent.** `clawgate-writeback-guard.py`
  must anchor on work because its pickup ritual posts a "Starting" comment BEFORE the work, so a
  read anchor would be satisfied at pickup and a missing COMPLETION write-back would be
  unobservable. `/handoff` runs at the END, so there is no such degenerate case here — and a work
  anchor would import a false-positive class this record does not have: `write the doc → git
  commit → git push` leaves the doc's mtime BEFORE the last work event, and the guard would block
  a session that had just recorded everything. The cost is named rather than hidden: a session
  that writes its handoff early and then works for hours without updating it scores as recorded.
  That is exactly what the 91.3% measurement scored too, so the guard and the number agree.
- 🔴 **A FIXTURE WHOSE THREE VALUES NORMALISE TO ONE STRING CANNOT SEE A MUTANT, and this arc
  produced a clean example.** The test "one doc read three ways books one ledger slot" used three
  spellings that `os.path.normpath` collapses to the same string — so it passed with the key
  mutated from the BASENAME to the FULL PATH, the single survivor of a 27-mutant sweep. The fix is
  mechanical and general: assert the fixture CAN move (`len(set(spellings)) == 3`) before asserting
  the code does not. The replacement spellings are the ones a real session produces — the base
  clone's copy, the throwaway worktree's copy that `/handoff` actually writes, and a `..`-bearing
  relative resolution.
- 🔴 **A SELF-AUDIT ROUND ON THE DELTA FOUND A GUARD THAT ARMED NOTHING, and no test could have.**
  The hook's `Read` arm resolved its path against no base at all, so a RELATIVE `file_path` armed
  nothing — a silent blind spot, not a wrong answer. It was found by asking what `lib/subsystem_touch.py`
  (which reads the same payloads out of transcripts) records about that field: *"ABSOLUTE whenever
  the caller passed an absolute"*, i.e. a relative one reaches the payload verbatim. **The
  cross-check that found it was reading what ANOTHER module had already measured about the same
  field**, not re-deriving it.
- **Decision — the mutation sweep is COMMITTED**, as `claudedocs/handoff-write-guard-mutation-sweep.py`,
  parameterised on `HWG_TREE` and re-run from the committed copy before its number was quoted.
  This is the arc's own recorded lesson applied to itself: the previous pass left its classifiers in
  a session scratchpad as throwaway and it cost rank 1 a full re-derivation.
- **Dead end (do not re-derive): "the 76 local `scripts/tests` failures are the new floor entry."**
  Killed by an identical failure SET at `origin/main`. See the investigation block above.
- 🔴 **`clawgate_handoff.sh resolve` returned rc 5 again this session** (0 tasks, with its positive
  control confirming the board reachable and the token accepted). Per `/handoff` step 1 that is an
  UNRESOLVED session, not a clean bill of health: **no `clawgate-task:` field was written and no
  task was created.** Same outcome as the previous session — two consecutive sessions of this arc
  have failed to resolve, which is itself worth a look before a third assumes it is normal.

- 🔴 **`ship.sh` converged both hosts to a sha that is NOT the merge commit, and that is
  correct — verify by ANCESTRY of the SHIPPED sha, not by equality with your own.** The merge
  landed `ad891a5c`; `ship.sh` reported both hosts at `bd1572f3`, because other PRs landed in
  between. Reading "shipped != my merge commit" as a failed deploy is the available mistake;
  `git merge-base --is-ancestor ad891a5c bd1572f3` is the check. Pair it with the byte check on
  the deployed artifact (`git hash-object ~/.claude/hooks/<x>` vs
  `git rev-parse origin/main:<path>`) — the sha says WHAT SHIPPED, the hash says WHAT IS ON
  DISK, and only the second one is a statement about the running system.
- 🔴 **"Merged" and "registered" are two claims and only the second one makes a hook do
  anything.** `nix/home.nix` deploys the FILE; `register-nudge-hook.py` writes the ENTRY into
  each host's `~/.claude/settings.json`, per host, on switch. This repo's #452 is a hook that
  shipped to both hosts, reported a successful switch, and sat inert with nothing on screen to
  say so. The check that separates them is reading `settings.json` for the event names — here,
  1 on `PostToolUse`, 1 on `Stop`, **0 on `SessionStart`**, on BOTH hosts.

- 🔴 **AN ADDITIVE EDIT TO A DOC IS THE STRONGEST FALSE FRESHNESS SIGNAL THERE IS, and the
  ranked-backlog decay rule does not cover it.** The known rule is "a ranked backlog decays —
  re-measure each item before working it", and the expected decay is *the item got done*. This is
  the inverse: rank 6's target file was edited **today**, by a competent session, in a way that
  makes it *look* maintained while leaving every wrong line intact — because the edit was `+21/-0`
  in a different section. **`git log -1 <file>` is not evidence about a claim inside that file.**
  The discriminator is one command: `git show --stat <sha>` — an edit with **0 deletions** cannot
  have corrected anything. Check that before you downgrade a doc-correction item.
- **Decision — this session's third `/handoff` wrote only an APPEND delta.** The ranked list was
  deliberately left untouched: rank 6 already says "re-measure before editing", the new fact is a
  *warning attached to* that item rather than a change to it, and re-emitting all 8 items under a
  REPLACE heading to add one clause risks dropping a line for no gain. The finding is keyed to
  rank 6 by name in the investigation block above, which is where a reader working that item
  arrives anyway.

- 🔴 **THE WRITE GUARD'S DESIGN, MOVED HERE FROM `State now` SO A FUTURE REPLACE CANNOT EAT IT.**
  Whoever works rank 8 needs it to grade the thing. `scripts/claude-hooks/handoff-write-guard.py`
  is armed on a `/resume` **READ** — a `Read` of `claudedocs/handoff-*.md` / `*HANDOFF*.md`, or the
  `git show <ref>:claudedocs/…` form — and **never on `SessionStart`**. Three conditions: the read,
  REAL WORK after it, and no observable handoff write since. Three satisfaction routes are UNIONED
  (a `handoff_doc.py` run, a Write/Edit of ANY handoff doc, the resumed doc's own mtime); **two are
  session-level ON PURPOSE**, because the 25 drift sessions were scored RECORDED and a path-keyed
  guard would block every one of them. Ladder is `block, block, systemMessage, silent`; `MAX_DOCS 3`;
  dismissal leaves a tombstone; it **fails open** and has one exit, always 0. Registered as 1
  `PostToolUse` + 1 `Stop`, **0 `SessionStart`** — that is the design, not an omission.
  ⚠ Its pre-merge gates were green (`#1092`), which was the first PR of this arc ever seen green —
  a data point for the CI question, never a claim it was closed.
- 🔴 **`clawgate_handoff.sh resolve` returned rc 5 for the THIRD consecutive session of this arc**
  (2026-08-31), with its positive control confirming the board reachable and the token accepted.
  Per `/handoff` step 1 that is an UNRESOLVED session, not a clean bill of health: **no
  `clawgate-task:` field was written and no task was created.** The previous update said two in a
  row was "worth a look before a third assumes it is normal" — this IS the third. **Stop treating
  it as noise.** The rc-5 message is explicit that a wrong session id and a session that touched
  nothing are indistinguishable (both answer `200` with an empty array), so the next session should
  check whether `CLAUDE_CODE_SESSION_ID` is actually being set in this runtime before concluding
  anything about the board.
- 🔴 **THE SUBSYSTEM STORE HELD THE ANSWER TO THIS ARC'S OWN OPEN PROBE, AND `--session` COULD NOT
  SEE THIS SESSION AT ALL.** Two separate lessons from step 4, both measured:
  (a) `subsystem_touch.py --session` **refused** with `transcript cwd does not match` — this session
  ran in `datapacket-talos` while `--repo` was `devrc`, and it correctly told me **not** to fall
  back to the git window (empty for the same reason) but to use `--pr`. A hub session that lands
  PRs elsewhere is the normal shape here, and the session window is structurally blind to it.
  (b) The store's `devrc/tests.md` has carried *"three runs of `tekton/devrc-*` on ONE unchanged
  commit, three different verdicts"* since **2026-08-27** — the exact probe this doc kept listing
  as open. Recall was RUN at kickoff and *listed* that entry while *featuring* another, so it was
  one `--ref` away and never opened. **When a doc names a probe as OPEN, `--ref` the plausible
  entries before running it.**
- **Decision — the audit ladder STOPS on judgement when the PR is docs-only.** `/audit-pr`'s
  mechanical stop is two consecutive rounds of ZERO payload lines. On a docs PR the payload IS the
  `.md`, so every fix round is 100% payload by construction and **the gate can never fire**. Six
  rounds ran with it structurally silent. Say this out loud when opening a ladder on a docs PR.
- **Decision — re-derive every auditor finding before acting on it.** Three of the six rounds'
  headline numbers MOVED when checked first-hand (a byte size, a pipeline count, a "measured
  difference" that was measurably absent). Taking the reports at face value would itself have
  shipped errors.

## How to verify
- 🔴 **Every command cites a SHA or derives its value; no branch names survive** —
  `zach/skill-chain-usage-audit` was deleted after #1055 merged.
- **The #1055 stale-base diagnosis, the reusable two-arm control, in two commands:**
  ```bash
  DEVRC=~/workspace/devrc
  git -C "$DEVRC" worktree add --detach /tmp/ctl-main origin/main
  (cd /tmp/ctl-main && python3 -m pytest scripts/collector/keylog/tests/test_espanso_detect.py -k live_existing_resolutions -q)   # expect: 1 passed
  git -C "$DEVRC" worktree remove --force /tmp/ctl-main
  ```
  At the pre-merge sha **`f85b7444`** the same command FAILS with
  `{'ask': (':acq', None, [':dacq', ':acq']), ...}`. Red on the branch, green on `main` ⇒ stale
  base. Green on BOTH ⇒ environment. That pair is the arc's durable output.
- **The skill-ratchet half:** `git -C "$DEVRC" cat-file -s f85b7444:claude/skills/clawgate/SKILL.md`
  = **15491** (red, ceiling 15088) vs `origin/main` = **15086** (green).
  🔴 **Build `<rev>:<path>` with `printf '%s:%s'`** — zsh eats `$VAR:c…` as a history modifier and
  yields `f85b7444laudedocs/…`, an error naming a path that appears nowhere in your command. Hit
  twice this arc.
- **Doc size / open ranks, both DERIVED:**
  `(cd "$DEVRC" && python3 scripts/handoff-audit.py --sections 1 claudedocs/handoff-skill-chain-usage-audit.md)`
  → `N/<total> ranked items done` and the size line. Never quote a size from prose — and 🔴 **never
  quote the ranked-item count from the tool either: it over-counts** (see `## State now`, which
  carries the one copy of that caveat and the reason). The SIZE line is the trustworthy half.
- **The gate's node ceiling:**
  ```bash
  export KUBECONFIG=$KC_HOMELAB
  kubectl -n tekton-ci get pods --field-selector=status.phase=Pending
  kubectl get node talos-xr6-r7p -o jsonpath='{.status.allocatable.cpu}'
  ```
  🔴 The session default `prod-kubeconfig` answers `No resources found in tekton-ci namespace.` —
  a confident empty about the WRONG cluster.
