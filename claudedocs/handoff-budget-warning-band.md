<!-- delta: State now / Next steps / Defects / Gotchas / How to verify — Goal and Open investigations omitted and therefore untouched -->

## Run this first — the index, one command
```bash
$DEVRC/scripts/cairn-ops/read.sh recall --repo "/home/zach/workspace/devrc"
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Stop sessions burning tokens fighting the handoff-document size ceiling. **The operator
reported this repeatedly and said earlier fixes "either hadn't landed or weren't working".
Five had landed; the diagnosis is that the newest one is arithmetically unable to fire.**

🔴 **A NEW ARC, AND ITS NEIGHBOUR IS `claudedocs/handoff-handoff-doc-prune-exit.md` —
WHICH MUST NOT BE RE-OPENED.** That arc gave the append-only sections an **EXIT** from the
refusal (`--prune`, rule (q)/(r), then `--archive-write` and `--autoevict`). This one is the
other half: **the session should never reach the refusal**, because by then the content is
already composed and the second pass is the cost. Same subsystem, different mechanism,
different closing condition. The related docs — `handoff-handoff-resume-prune.md` (CLOSED),
`handoff-evictable-note-ungated.md`, `handoff-skill-listing-budget.md`,
`handoff-skill-prune-and-gate-floor.md` — are each their own arc too; **do not fold this into
any of them, and do not mint a sixth slug for the same ground.**

- **closing-condition:** `check` — all three, in one session:
  ```bash
  # (a) the fix is on main
  git -C $DEVRC cat-file -e origin/main:scripts/lib/handoff_budget.py && \
    git -C $DEVRC show origin/main:scripts/lib/handoff_budget.py | grep -c 'BUDGET_NEAR_BYTES'
  # (b) the band fires BEFORE a median append can blow the budget, and stays silent below it
  #     (MAX-STEP+1 warns; MAX-STEP is silent) — see `## How to verify` for the exact probe
  # (c) the digest tells a session its budget before it composes: a BUDGET block appears in
  #     `resume-state.sh` output, and flags a doc inside the band
  ```
  ADDRESSED ⇒ arc CLOSED. 🔴 **FROZEN AT ROUND 1.** The `--autoevict`-by-default question
  and the two follow-ups below are **NOT** part of this line; they are separate arcs.

## State now
- 🔴 **THE ARC IS CLOSED. All three clauses of the closing condition were run and are GREEN
  in one session.** devrc#2001 **MERGED** 2026-10-04T01:13:00Z, squash **`3fed10fb`**, from
  head `26bb403c`. Verified by **CONTENT, not ancestry** — a squash never makes the branch
  head an ancestor of `main`, so `--is-ancestor` would read "not merged" forever.
  - **(a)** `origin/main:scripts/lib/handoff_budget.py:121` → `BUDGET_NEAR_BYTES = GRANDFATHER_STEP`
    (grep count **2**, measured **0** immediately before the merge — the pair is the evidence,
    not the 2 alone).
  - **(b)** `49,151 silent · 49,152 silent · 49,153 WARNS · 54,938 WARNS · 39,152 silent`,
    and `BUDGET_NEAR_BYTES == GRANDFATHER_STEP` → `True`. 🔴 **Run against a tree where
    `handoff_budget.py`, `handoff_doc.py` and `resume-state.sh` are all CLEAN at
    `origin/main`** — `git status --porcelain` on those three paths was empty, so this is
    evidence about the COMMITTED SOURCE and not about a dirty working copy. (The base clone
    *is* dirty — see the `flake.lock` gotcha — which is exactly why the three paths were
    checked individually.)
  - **(c)** the `BUDGET` block appears between `INVESTIGATIONS` and `DOD`, and it was watched
    in **BOTH** directions rather than only reassuring: `handoff-budget-warning-band.md`
    18,852 B → `✅ clear of the warning band.`; `handoff-mention-review-tui.md` 63,599 B →
    `⚠ INSIDE the warning band — a routine append can land this over, and the refusal
    arrives AFTER the text is composed.` **A `✅` alone is indistinguishable from a block
    wired to nothing**, which is why the second reading is quoted.
- 🔴 **THE GATE WAS GREEN ALL ALONG, AND THE `pending` BADGES WERE A BROKEN REPORTER —
  CI's non-report was never a verdict about the tree.** PipelineRun **`devrc-ci-8pjct`**
  (`revision=26bb403c…`, `supersede-key=pr-2001`, created 2026-10-03T21:29:02Z) printed
  `ALL LEGS PASS`: pytests `collected=25056 passed=25048 skipped=8 failed=0` (floor 21193),
  nodetests `tests=1720 pass=1720 fail=0` (floor 1613), gotests `pass=481 fail=0` (floor 302),
  cairn-client-runs pass. **All eight gate steps `exit=0`, `verdict` included** — which is the
  tekton skill's own discriminator for *a verdict* rather than *a kill*.
- ⚠ **THE PIPELINERUN NONETHELESS READS `Failed`, AND THAT IS THE `report` TASK ONLY.**
  `step-report-status` exit 1. The run's own condition is
  `Tasks Completed: 3 (Failed: 1, Cancelled 0)` — **read WHICH TaskRun failed before
  reading a PipelineRun verdict as a verdict about the diff.**
- ✅ **THE MERGED-TREE QUESTION IS CLOSED, NOT DEFERRED.** At merge time `origin/main`
  (`3e7725bc`) was an **ancestor** of `26bb403c` — `git rev-list --count 26bb403c..origin/main`
  = **0** — so the tree the gate tested IS the post-merge tree. Re-measured immediately before
  the merge, not inherited from the survey that motivated it.
- ✅ **THE VERDICT IS ON THE PR RECORD**, because a merged commit whose statuses read
  `pending` otherwise carries no readable verdict and "it merged, therefore it was green"
  would be unfounded: comment `#issuecomment-5975287909` on devrc#2001 quotes the gate
  summary, the reporter's false FATAL, and the three later runs that refute it.
- ✅ **THE BASE CLONE WAS RE-SYNCED** `3e7725bc → 3fed10fb` with `merge --ff-only`, after
  checking that none of the nine merged paths intersects the dirty `flake.lock` or the two
  untracked `scope-chief-*` files. `flake.lock` is **still dirty afterwards**, confirmed —
  the other session's work was not disturbed.
- ⚠ **NO `clawgate-task:` FIELD, AND THE ZERO IS STILL NOT A CLEAN BILL.** Re-run this
  session: `clawgate_handoff.sh resolve` exit **5** (`NOTHING RESOLVED — 0 tasks`),
  `field <doc>` exit **1**. An unknown session id answers 200 with an EMPTY ARRAY, so it
  cannot distinguish "touched no task" from "wrong id". No field written, none created.
- ⚠ **`tests/leakscan.py` DOES NOT EXIST IN devrc, SO THIS DELTA GOT THE SAME PASS-BY-ABSENCE**
  as the doc's first write. Hand-scanned: no IPs, no credentials, no prohibited names; the
  only hostnames are `github.com` and the public `apps/tekton-homelab` URL.

## Open investigations — live diagnosis state

### 🔴 OPEN (operator decision, not work) — should `--autoevict` become the DEFAULT rather than a flag discovered after a refusal?
- as-of: 2026-10-03
- **Symptom + exact repro:** the write is refused at `size-ratchet` (exit 14); the agent must
  then pick an exit (`--prune`, `--archive-write`, `--autoevict`) and re-run. That re-run is
  the second pass, and the second pass is what costs tokens. Making `--autoevict` the default
  would remove it.
- **Observed (with values) — it would NOT remove the second pass.** In its own trial
  `--autoevict` reached **3 of 5** docs; **two have zero evictable candidates and still hit
  exit 14**. It **freezes rather than heals** (~20–30 more sessions on the worked doc before
  the problem returns), and the **archive destination is governed by the same ceiling**, so it
  relocates the pressure rather than removing it. `via: measurement`
- **Observed — the module already records four independent reasons against it**, in
  `scripts/lib/handoff_doc.py`: it would make the module the actor for a judgement rule (p)
  itself says it cannot make (*"nothing in this rule can tell a deletion from an eviction —
  the arithmetic is identical"*); a refusal is a no-op while an eviction **writes and commits
  two files**; the proposal run exists to put the diff in the transcript; and rule (r)
  requires an editorial note precisely because a generated one is worse than none.
  `via: code`
- **Ruled out: that this is the cheapest next experiment.** #2001 attacks the cause upstream
  — the session not knowing in time — without auto-deleting anything, so its effect must be
  measured first or an auto-delete default is being justified against a baseline that no
  longer exists. `via: measurement`
- **Leading hypothesis:** the answer is **no**, and the recommendation of both the
  implementing agent and the dispatching session is no.
- **Next probe:** instrument the **outcome distribution of ratchet-refused writes** — second
  pass vs `--override-size-ratchet` vs abandoned record; only the first is the cost. Then
  measure the **human approval rate** of `autoevict_selection` run in proposal mode over the
  corpus, plus the residual it cannot clear. **Run both AFTER #2001 has soaked.**

### 🔴 OPEN — whether the wider band actually changes session behaviour is UNMEASURED, and the thing it fixes is a feedback loop
- as-of: 2026-10-03
- **Observed:** the mechanism is verified (the band fires at 75% instead of 93.75%, both
  directions, and the digest prints the headroom) but **no session has yet run under it**.
  The claim "sessions stop burning tokens" is a behavioural claim and this is a mechanism
  change. `via: measurement`
- **Observed — the failure mode this replaces is on record and is exactly this shape.**
  `handoff_doc.py` already documents that the #1648 pre-write warning *"has been printed on
  every over-budget write since #1648 and the mechanism it names went on regardless: MEASURED
  on one arc, a document a prune had landed UNDER the ceiling more than DOUBLED inside a
  week"*. **A warning that is ignored is the precedent, not the exception** — so a wider
  warning is necessary and may not be sufficient. `via: doc`
- **Ruled out: that the fleet is already safe.** Re-derived over 108 docs: **11 over
  `MAX_BYTES` but all grandfathered (0 over their own allowance)**, and **14** were silent
  while within one p90 append of the ceiling — 7 non-grandfathered
  (`index-store-claims-accuracy` 59,764 · `mention-picker-instrumentation-and-tui` 59,007 ·
  `bridge-unbounded-waits` 58,054 · `handoff-search-index` 57,572 · `mention-detection`
  57,526 · `handoff-resume-skill-trace` 55,915 · `analyze-service-index-backup` 53,242) plus
  7 grandfathered. All 14 now warn. `via: measurement`
- **Leading hypothesis:** the band is necessary; the digest block is the half more likely to
  change behaviour, because it arrives *before* composing rather than at write time.
- **Next probe:** after #2001 merges, pick the next session that touches a doc in the band
  and read whether it evicted *before* composing or after being refused. One observation is
  not a trend — name the session and the doc.

## Next steps (ranked)
1. ✅ **DONE — devrc#2001 MERGED** (`3fed10fb`, 2026-10-04T01:13:00Z) and all three closing
   clauses verified green. Kept at rank 1 rather than renumbered: the rank is half a
   `claim-work` slug's identity, so re-ranking re-points every live claim on this doc.
   Claim `budget-warning-band-1` was taken and **released**.
   forcing: user — the operator authorised the merge on the measured gate result; done.
2. **ANSWER THE `--autoevict`-BY-DEFAULT QUESTION.** Evidence is in the investigation block
   above; the recommendation is **no**. This is a decision, not work. 🔴 Its own "run both
   AFTER #2001 has soaked" precondition is now **satisfiable** — #2001 is on `main`.
   forcing: user — it is an operator call about an irreversible auto-delete default, and no
   further measurement separates the options until the merge has soaked.
3. **DOCUMENT THE `BUDGET` BLOCK IN THE RESUME SKILL — blocked on an eviction first.**
   `claude/skills/resume/SKILL.md` has **4 bytes** spendable before breaching
   `MIN_HEADROOM_BYTES` (21,596 of 22,400; headroom 800), so this needs a content eviction
   into `reference/` — a judgement call deliberately excluded from #2001. ⚠ **Re-derive the
   4 bytes before acting**: #2001 did not touch `SKILL.md`, but other merges since may have.
   *Closing condition (mechanical): `SKILL.md` names `BUDGET` in its block list and
   `test_resume_skill_size.py` exits 0 with it present.*
   forcing: none
4. **THE `scripts/tests` COLLECTED FLOOR IS NEAR ITS HARD DRIFT CEILING.** 🔴 **The figure
   in the previous round is now STALE and the direction is reassuring: the merged gate run
   measured `collected=25056` against `floor: 21193 = sum of 30 per-target floors`**, not the
   16,137/16,282 pair this doc recorded — those were a different target set. **Re-derive
   before acting; do not quote either number.** Re-pinning conflicts across ~40 open PRs, so
   it is left alone deliberately.
   *Closing condition: a run reports `collected` above its printed ceiling, at which point
   the gate forces the bump and prints the replacement number.*
   forcing: gate — the gate itself will force it; until then a bump conflicts with every open PR.

## Gotchas / decisions / dead-ends
- 🔴 **FIVE FIXES HAD ALREADY LANDED BEFORE THIS ONE — "it hasn't landed" was wrong and "it
  isn't working" was right.** The chain: the pre-write warning (#1648) → rule (p), an
  over-budget doc may not GROW (#1871) → three exits from that refusal, `--prune` (#1960),
  `--archive-write` (#1963), `--autoevict` (#1966). **They gave the wall a door; nothing told
  a session it was approaching the wall.** Do not re-derive this chain — `git log` on
  `scripts/lib/handoff_budget.py` and `scripts/lib/handoff_doc.py` is the record.
- 🔴 **`BUDGET_NEAR_BYTES = 4_096` WAS A BARE CONSTANT.** The long comment above it justifies
  why the warning does not *refuse*; **nothing anywhere justified 4,096.** The replacement is
  derived from the append distribution and says so at the constant, in ONE place, with the
  command that re-derives it.
- 🔴 **THE IMPLEMENTING AGENT SHIPPED A WRONG MEDIAN AND THEN CORRECTED IT** (`54dc2cea`):
  242 is **even**, so the median is the mean of the two middle values (65 and 66) = **65.5**,
  not 66. Rounding first overstates the median append by 40 B — **in the paragraph written to
  stop exactly that.** The trap is now named at the constant.
- ⚠ **THREE OF THE DISPATCHING SESSION'S OWN FIGURES DID NOT REPRODUCE**, and the agent
  re-derived rather than inheriting them: 299/254 commits → **285/242**; median 60 → **65.5**;
  p90 196 → **193**. The window moved between measurements. The conclusion is unchanged and
  robust (4,096 < ~5,271 either way) — but **re-derive before acting on a number this doc
  states.**
- 🔴 **THE BUDGET PROBE MUST NOT IMPORT `handoff_doc` — IT CALLS `cairn_pin.ensure()`.** So
  reading a byte budget required the **pinned cairn client on PATH**, and without it the
  digest reported the budget as an UNKNOWN gap **on exactly the hosts the fallback exists
  for**. Control: `handoff_budget` is stdlib-only and imports fine in the same stripped env.
  `BUDGET_NEAR_BYTES`/`is_handoff_doc`/`gate_enforces` moved there; `handoff_doc` re-exports,
  the precedent `BUDGET_GATE_RELPATH` already set. ⚠ **Found by another file's
  `assert not gaps(out)`, not by anything written for this change.**
- ⚠ **A LEDGER KEY TAKEN FROM A BASENAME INSTEAD OF GIT HANDED ONE DOC ANOTHER'S ALLOWANCE.**
  A fabricated `claudedocs/<basename>` gave a 99,998 B `docs/handoff-<topic>.md` another
  doc's **180,224 B** allowance and a confident ✅. Mutation-killed 1 of 25, with the guard's
  own assertion and a checksum-verified restore.
- ⚠ **`printf "%'d"` IS LOCALE-DEPENDENT** (`LC_ALL=C` → `65536`; UTF-8 → `65,536`).
  Formatting moved to Python; the suite now pins **both** locales.
- ⚠ **A MULTI-LINE TRACEBACK BROKE THE LINE-ORIENTED GAP CHANNEL**, and `_SANDBOX_TOOLS`
  lagged the script so those tests silently measured "no python3". Both fixed.
- ⚠ **THE BRIEF'S `-name '*.py'` READER SWEEP WAS STRUCTURALLY BLIND TWICE** — it missed the
  **shell tier** (`test_resume_state.sh`, a `SHELL_TESTS` target no `--files`/`--targets` can
  name) and the resume skill's own docs. Widen the enumeration repo-wide, not to `*.py`.
- ⚠ **Three ledgers fired correctly and had to be updated**: block order, the `$HANDOFF`
  path-use ledger (which failed on **grow AND shrink**), and `PINNED_PATH_CLOBBERS`.
- 🔴 **devrc's BASE CLONE CARRIES ANOTHER SESSION'S UNCOMMITTED `flake.lock`** — it moves
  **three** inputs (cairn, home-manager, nixpkgs), i.e. somebody's full `nix flake update`.
  Not to be committed, reverted or stashed. Two untracked `claudedocs/scope-chief-*.md` from
  2026-09-19 are stranded there too. **`refs/stash` has 2 pre-existing entries, which proves
  the stack is shared — never `git stash` in this repo.**

- 🔴 **CARRIED FORWARD OUT OF `State now` BEFORE THE REPLACE ATE IT — THE ARC'S ROOT-CAUSE
  MEASUREMENT, which lived only under a REPLACE heading and would have been deleted by this
  very update.** `BUDGET_NEAR_BYTES` was **4,096**, and `budget_warning` was therefore
  **completely silent below 61,441 B (93.75% of the 65,536 ceiling)** — bisected empirically,
  not reasoned. Median handoff append ≈ **5,271 B** (median **65.5** net lines × 80.4716 B/line
  over 285 commits touching a `handoff-*.md`, of which **242 grew one**:
  `git log origin/main -300 --numstat -- claudedocs/`). So a median write that ENTERED the band
  jumped clean over it and the first signal was the **refusal**, after the content was composed
  — the second pass being the token burn. The replacement is **`GRANDFATHER_STEP` = 16,384**,
  ≈ the measured **p90 append (15,531 B, 853 B of slack)**, moving the first warning
  **61,441 B (93.75%) → 49,153 B (75%)** and docs warned fleet-wide **10 → 24**.
  ⚠ **Re-derive before quoting any of these** — the window moves (see the three-figures bullet
  below).
- ⚠ **CARRIED FORWARD for the same reason: `--session` IS BLIND TO EXACTLY THE SESSION THAT
  FOLLOWED THE RULES, AND THIS SESSION IS THE SECOND MEASUREMENT.** The first write of this doc
  took THREE windows (`--session` on devrc refused `transcript cwd does not match`; `--session`
  on cairn returned 100% of paths outside the session cwd, the subagent-worktree blind spot;
  `--pr 174,175` resolved 46 paths and was the only window that saw a subagent's work).
  **This session reproduced the first failure exactly** — `--session` with `--repo devrc`
  refused `transcript cwd does not match: session … ran in /home/zach/workspace/cairn … 1
  outside it`, and the tool itself said *"USE A DIFFERENT SOURCE, not a different uuid: the
  session ran elsewhere, so this repo's git window is empty too."* `--pr 2001` then resolved 9
  paths. 🔴 **Do NOT fall back to the git window on a cwd mismatch** — it is a second source
  that structurally cannot answer. Two points, both measured: a well-delegated or
  cross-repo-driven session is the one `--session` sees least of.
- 🔴 **A PIPELINERUN'S OWN `Failed` CONDITION IS NOT A VERDICT ABOUT THE DIFF — THE GATE AND
  THE REPORTER FAIL INDEPENDENTLY, AND ONLY ONE OF THEM IS ABOUT YOUR CODE.** `8pjct` reads
  `Failed` with a fully green gate. The discriminating read is per-TaskRun, never the
  PipelineRun:
  `kubectl -n tekton-ci get taskrun -l tekton.dev/pipelineRun=<run> -o json` → which
  `pipelineTask` is `False`, and the step exit codes under it. **`gate` Succeeded + `report`
  StepFailed ⇒ a lost verdict, not a red change.**
- 🔴 **FOUR LEGS STUCK ON `pending` FOREVER HAS AT LEAST THREE CAUSES AND THE STATUS TIMELINE
  IS WHAT SEPARATES THEM** — `gh api repos/<r>/commits/<sha>/statuses` with `.description`,
  because `gh pr checks` shows only the current state. Measured here: **one** `pending` row
  per context and nothing after, which **rules out** the two-runs-race shape (that posts
  DUPLICATED `pending` pairs seconds apart, then overwrites all four with
  `superseded … not validated`) and the held-gate shape (`NO CAPACITY`, state `error`). What
  was left was a reporter that ran and could not post.
- ⚠ **A `pending` leg on devrc did NOT block the merge, and that is a change of state rather
  than a property.** `mergeStateStatus=UNSTABLE`, not `BLOCKED`, because required status
  checks are currently disabled on `main` (see Defects). **Check protection at the moment you
  merge** — do not infer gating from the skill, or from this line.
- 🔴 **`merge --ff-only` INTO A DIRTY BASE CLONE IS SAFE ONLY AFTER CHECKING THE INTERSECTION,
  AND THE CHECK IS ONE COMMAND:** `git diff --name-only HEAD origin/main` against
  `git status -s`. Here the nine merged paths and the dirty `flake.lock` were disjoint, so
  the other session's uncommitted `nix flake update` survived the sync untouched — verified
  AFTER the merge, not assumed from before it. **Never stash to make room** (`refs/stash`
  holds 2 pre-existing entries, which proves the stack is shared).
- ⚠ **A doc that lives only in a linked worktree makes a handoff PATH in a kickoff message
  wrong, and the reconciler re-anchors silently.** The kickoff named
  `/home/zach/workspace/devrc/claudedocs/handoff-budget-warning-band.md`, which **does not
  exist** — the only copy is in `/home/zach/workspace/devrc-ho-budget` on
  `docs/handoff-budget-warning-band` (devrc#2006), because the doc has never been on
  `origin/main`. `resume-state.sh` resolved the worktree copy anyway and reported
  `handoff-read: working-tree copy — not on origin/main`. **Read that line; it is what names
  which copy is authoritative.**
- ⚠ **`handoff_search --exclude-slug` PARSED BUT DID NOT MATCH, and the pair is how you know.**
  Scope line read `excluded=budget-warning-band` with `in_scope_docs=567 == indexed_docs=567`
  — because this doc is not on its repo's mainline and is therefore deliberately never
  indexed. **`excluded=` proves the flag parsed, never that it matched.**

## How to verify
```bash
# (a) the fix is on main — CONTENT, never ancestry (a squash is not an ancestor)
git -C $DEVRC show origin/main:scripts/lib/handoff_budget.py | grep -n 'BUDGET_NEAR_BYTES'
gh pr view 2001 --repo innovation-upstream/devrc --json state,mergedAt,mergeCommit

# (b) the band fires before a median append, and stays SILENT below it — BOTH directions,
#     because a guard that fires on everything is as useless as one that fires on nothing.
#     First prove the three files are CLEAN, or this measures a dirty working copy:
git -C $DEVRC status --porcelain scripts/lib/handoff_budget.py scripts/lib/handoff_doc.py \
  scripts/resume-state.sh   # must be EMPTY
nix develop $DEVRC --command python3 - <<'PY'
import sys; sys.path.insert(0, "/home/zach/workspace/devrc/scripts/lib")
import handoff_budget as hb, handoff_doc as hd
MAX, NEAR = hb.MAX_BYTES, hb.BUDGET_NEAR_BYTES
warns = lambda n: bool(hd.budget_warning("claudedocs/handoff-x.md", "x"*n, "x"*(n-1), gated=True))
for n in (MAX-NEAR-1, MAX-NEAR, MAX-NEAR+1, 54938, 39152):
    print(f"{n:>7,} ({100*n/MAX:5.1f}%) warns={warns(n)}")
# expect: silent, silent, True, True, silent
print("band == one GRANDFATHER_STEP:", NEAR == hb.GRANDFATHER_STEP)
PY

# (c) the digest states the budget BEFORE composing — and the POSITIVE CONTROL is the half
#     that matters: a `✅ clear` alone cannot distinguish a working block from a dead one.
bash $DEVRC/scripts/resume-state.sh "<a doc UNDER 49,152 B>"  | grep -A4 '^BUDGET'  # ✅ clear
bash $DEVRC/scripts/resume-state.sh "<a doc OVER  49,152 B>"  | grep -A4 '^BUDGET'  # ⚠ INSIDE
# find one of each:
for f in $DEVRC/claudedocs/handoff-*.md; do printf '%s %s\n' "$(stat -c%s "$f")" "$f"; done | sort -rn | head

# (d) the gate's own verdict, when GitHub shows `pending` and you need the real answer
export KUBECONFIG=$KC_HOMELAB
kubectl -n tekton-ci get taskrun -l tekton.dev/pipelineRun=<run> \
  -o custom-columns='TASK:.metadata.labels.tekton\.dev/pipelineTask,STATUS:.status.conditions[0].status,REASON:.status.conditions[0].reason'
kubectl -n tekton-ci logs <run>-gate-pod -c step-verdict | tail -20
gh api repos/innovation-upstream/devrc/commits/<sha>/statuses \
  --jq '.[] | "\(.created_at) \(.context) \(.state) \(.description)"' | sort
```
## Defects (batched)
- 🔴 **The Tekton `report` task's "the tekton-homelab GitHub App is not installed on
  innovation-upstream/devrc" FATAL is reachable on a TRANSIENT lookup failure, where it is
  FALSE and actively misdirecting.** Refuted in one read: three later runs posted four
  `success` statuses through that same App within two hours — devrc#2004 22:25Z, #2002
  23:20Z, #2006 23:59Z. The message ships an install link, so it sends the next reader to
  install an already-installed App. Lives in `homelab-infra`'s
  `clusters/homelab/apps/tekton-pipelines/triggers/devrc-ci-pipeline.yaml` report step.
  **An error message is a claim too** — this one asserts a cause it did not measure.
- ⚠ **The `tekton` skill's gotcha 9 is STALE in the permissive direction.** It states devrc
  `main` requires `tekton/devrc-pytests` + `tekton/devrc-nodetests`; measured this session,
  `GET /repos/innovation-upstream/devrc/branches/main/protection/required_status_checks`
  returns **404 `Required status checks not enabled`**, and #2001 read `UNSTABLE` rather than
  `BLOCKED` with four `pending` legs. The skill already warns that bit moved twice in one
  day; it has moved again. **Nothing currently gates a devrc merge.**
