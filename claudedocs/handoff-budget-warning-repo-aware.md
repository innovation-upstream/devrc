# Handoff: budget-warning-repo-aware — 2026-09-15

## Run this first — the index, one command
```bash
cairn recall --repo /home/zach/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal

Stop `handoff_doc.py`'s size warning from asserting that a shared gate will fail, in
repos that gate structurally cannot read.

- **closing-condition:** `check` — `devrc#1714` is MERGED, and `budget_warning` on a
  handoff doc in a repo that ships no `scripts/tests/test_handoff_doc_size.py` prints
  "NO GATE ENFORCES THIS IN THIS REPO" rather than "will go RED on `main`". Both halves
  are one command (see **How to verify**). 🔴 Frozen at round 1: making the premise TRUE
  instead — registering more repos in the gate's corpus — is a DIFFERENT effort and does
  not close this one.

## State now

- Branch `fix/budget-warning-repo-aware` @ **`e2535bfb`** (was `ff82a791`), worktree
  `/home/zach/workspace/devrc-budget-repo-aware`. **`devrc#1714` round 0 HAS NOW RUN** —
  5 findings, all folded in. The nine correctness axes have NOT.
- **CI is not settled and is not merely slow:** the three Tekton checks
  (`devrc-pytests`, `devrc-nodetests`, `devrc-cairn-client-runs`) sat `PENDING` for ~10
  minutes with `startedAt=0001-01-01T00:00:00Z` — a zero timestamp, i.e. **queued or never
  picked up, not running**. `mergeable=MERGEABLE/UNSTABLE`. 🔴 Do not merge through checks
  that never reported; the `tekton` skill is the way in.
- `devrc#1715` (this doc) is OPEN and reports **no checks** on its branch.
- Two files: `scripts/lib/handoff_doc.py`, `scripts/tests/test_handoff_doc.py`.
  **460 passed** across `test_handoff_doc.py` + `test_handoff_doc_size.py`, under
  **`PYTHONDONTWRITEBYTECODE=1`** — kept because a mutation sweep against a stale `.pyc` scores
  SURVIVED without the mutant ever executing. No existing assertion was weakened: `_budget`
  defaults to `gated=True`, the devrc answer every pre-existing assertion assumed.
- **Provenance, carried forward:** this work came out of `civitai/cli#618`'s round-0 audit
  (finding F2), not from a devrc session. The cli arc that surfaced it is **CLOSED**.
- **No `clawgate-task:` field.** `resolve` exited **5** — 0 tasks, and an unknown session id
  also answers `200` with an empty array, so the zero cannot distinguish "touched no task"
  from "wrong id". Not a clean bill of health.

## Open investigations — live diagnosis state

### 🔴 RESOLVED in code, UNMERGED and UNAUDITED: the warning claimed a gate that cannot see the repo
- as-of: 2026-09-15

- **Symptom + exact repro:** run any `handoff_doc.py --update` that pushes a handoff doc
  over 65,536 B in a repo that is NOT devrc. It prints *"`test_no_handoff_doc_exceeds_its_budget`
  will go RED on `main`, and it fails for EVERYONE — the next unrelated PR inherits it."*
- **Observed (with values):** `budget_warning` gated on **relpath alone**
  (`relpath.startswith("claudedocs/") and "/handoff-" not in "/"+relpath`), and relpath is
  built repo-agnostically as `f"claudedocs/handoff-{args.topic}.md"`. The gate it names
  computes `REPO_ROOT = Path(__file__).resolve().parent.parent.parent` and enumerates
  `claudedocs/` under that root only. `civitai/cli` ships no such file, so the sentence is
  false there. Measured end-to-end after the fix: `civitai/cli` → *"NO GATE ENFORCES THIS IN
  THIS REPO"*; devrc → *"will go RED on `main`"*.
- **Cost before the fix**, in `civitai/cli` 2026-09-14/15 — 🔴 **stated at the precision each
  figure actually has, after `devrc#1714`'s round 0 re-derived them:**
  - **35,517 B moved to `claudedocs/refs/`** — MEASURED, exact: that is the byte size of
    `claudedocs/refs/external-issue-513-numeric-username.md`, committed in `e464652`.
  - **"five evictions"** — NARRATIVE, not measurement. It counts five *named items* in that
    doc's own prose, not five byte deltas. Per-revision the net over the two days is **+592 B**,
    with one large committed shrink (`e39250b`, 65,019 → 49,696 B). Do not quote it as a
    measurement, and do not infer a byte total from it.
  - **27,991 B near-loss** — SELF-REPORT, not re-derivable. Nothing was committed, so it exists
    only in the prose of the effort that made the claim — the same author as the requirement.
  - And the figure nobody claimed: after all of it the doc is **65,580 B, still 44 B OVER** the
    ceiling it was being evicted against. Which is the point — nothing enforced it.
- **Ruled out:** *"compare the repo path against a known devrc root"* — **via: code**. In a
  WORKTREE the gate's own `REPO_ROOT` is the worktree, not the base clone, and this repo's
  agents work in worktrees constantly. The predicate is derived instead: a repo is gated iff
  it ships `scripts/tests/test_handoff_doc_size.py`.
- **Ruled out:** *"give `gated` a default"* — **via: code**. A default is exactly how the
  false claim survived; every caller silently received the devrc answer. It is keyword-only
  with `Parameter.empty`, and a test asserts that so a future default fails loudly.
- **Next probe:** `/audit-pr 1714` round 0, then merge. Nothing about the code is open.

### 🔴 RESOLVED by round 0: the repo-awareness had landed on ONE of THREE branches
- as-of: 2026-09-15

- **Symptom + exact repro:** with `ff82a791` applied, call `budget_warning` on a doc in the
  NEAR band (under the ceiling, inside the warn window) with `gated=False`. It returns
  *"evicting what has CLOSED now is cheaper than doing it under a red `main`"* — the identical
  false claim the PR exists to delete.
- **Observed (with values):** `budget_warning` has **three** branches that name the gate, and
  only the OVER one had been made repo-aware. (1) NEAR band — and it fires **BEFORE** the OVER
  band, so it is the sentence an author meets FIRST; the `civitai/cli` doc sat at 64,988–65,580 B,
  inside or adjacent to it, all session. (2) the grandfathered-recovery branch, which tells you
  to edit `scripts/lib/handoff_budget.py` — a file an ungated repo does not have, reachable by a
  relpath COLLISION with devrc's ledger that also silently grants an allowance of up to
  245,760 B. (3) the OVER branch, already fixed.
- 🔴 **The deeper finding, and the better diagnosis than the PR's own:** relabelling the RED
  sentence was not the fix. **The remediation LADDER is what cost the bytes** — 35,517 B went to
  `claudedocs/refs/` because a step said to put it there, not because of the word "RED" — and
  outside devrc that ladder cites a playbook in a test the repo does not ship. The ungated OVER
  branch is now two lines: the size, and why it is not a gate.
- **Ruled out:** *"drop the size number outside devrc too"* — **via: measurement**. The 65,536 B
  threshold transfers: it fails **11.6%** of datapacket's corpus vs **9.2%** of devrc's. The
  number is defensible everywhere; only the prescriptions are devrc-local.
- **Ruled out:** *"the PR is the weaker half of a fix, and registering repos in the gate's
  corpus is the better one"* — **via: code**. `this_repos_corpus()` is `REPO_ROOT`-rooted by
  construction and runs in a `nix build` hermetic tier from a `cp -r ${./.}` store copy where no
  other repo exists and the `$DEVRC/$HOMELAB/$DATAPACKET/$CIVITAI` handles are all unset.
  Cross-repo enforcement can only be PER-REPO — each repo shipping its own gate — which is
  exactly what `BUDGET_GATE_RELPATH` already makes automatic.
- **Blast radius, measured:** 3 of the 4 registered repo handles are ungated, and **53 handoff
  docs across them already exceed 64 KiB** (homelab 13, datapacket 37, cli 3) against devrc's
  ~11. The ungated branch is the MAJORITY output, not the exception.
- **Next probe:** the nine correctness axes on `#1714`, once Tekton reports.

## Next steps (ranked)

🔴 Numbering stable — rank is half a `claim-work` slug's identity.

1. **Get `#1714`'s Tekton checks to actually RUN, then the nine axes, then merge.** They are
   `PENDING` with a zero `startedAt`, which is queued-or-dropped rather than slow. The `tekton`
   skill owns this. 🔴 **A check that never reported is not a check that passed.**
   forcing: gate — an unaudited, unverified change to the line that tells every session whether
   a gate failed.
2. **Consider making the premise TRUE where enforcement is wanted** — have a repo ship its own
   `scripts/tests/test_handoff_doc_size.py`, which `BUDGET_GATE_RELPATH` already honours. Round
   0 established that a central cross-repo corpus is NOT achievable (see the ruled-out above),
   so per-repo is the only shape. **A separate effort by this doc's closing condition.**
   forcing: none
3. **Remove the two worktrees when `#1714` merges** — `devrc-budget-repo-aware`,
   `devrc-handoff-budget`. forcing: none

## Gotchas / decisions / dead-ends

- 🔴 **A MUTANT "SURVIVED" BECAUSE MY `-k` FILTER DID NOT MATCH THE KILLING TEST'S NAME.**
  Sweeping `if gated else` → `if True else`, `pytest -k "budget or gate_enforces"` reported
  **6 passed** — and the one test that kills it,
  `test_the_RED_gate_claim_is_made_ONLY_where_the_gate_actually_READS`, contains neither
  substring, so it was never run. The mutant was fine; the INSTRUMENT was broken, and it
  failed in the reassuring direction. **`--collect-only` the filter and read the list before
  believing a SURVIVED.** Re-run with the test selected, it is red on its own assertion.
- 🔴 **A ONE-SIDED TEST OF A BRANCH PASSES WITH THE CONDITION IGNORED.** Asserting only that
  the ungated text appears would survive `if True else`. The guard asserts BOTH directions in
  one test — gated says "will go RED", ungated says it does not and says "NO GATE ENFORCES" —
  because only the pair is a claim about the branch.
- **The write-back guard triggers on a FIXTURE STRING.** It flagged
  `handoff-example-topic.md`, which is `DOC` in `scripts/tests/test_handoff_doc.py` — a test
  constant, not a document anyone read. Harmless here (this session had real work to record
  anyway) but worth knowing before treating such a trigger as evidence a doc was consulted.
- **`pytest` is not on the bare PATH on this host.** `direnv exec <worktree>` was not enough
  either; `nix develop <worktree> -c python3 -m pytest …` is what works. `ledger-check.sh`
  re-execs into the dev shell itself, which is why it does not hit this.

- 🔴 **THE WRITE-BACK GUARD FIRED TWICE THIS SESSION ON TEST FIXTURE STRINGS.**
  `handoff-example-topic.md` and `handoff-sample-topic.md` are both `claudedocs/handoff-*.md`
  literals inside `scripts/tests/test_handoff_doc.py` — a `DOC` constant and a fixture path.
  Neither is a document anyone read. The guard matches the NAME SHAPE, so editing the test file
  that exercises handoff docs looks exactly like reading one. Harmless both times (there was
  real work to record anyway), but **do not treat such a trigger as evidence a doc was
  consulted**, and do not let it push you into writing a handoff for an effort that has none.
- 🔴 **A PRE-EXISTING TEST BREAKING WAS THE MOST USEFUL SIGNAL IN THE FIX ROUND.**
  `test_the_fixture_DOES_trigger_the_warning_on_a_run_that_proceeds` builds a synthetic git repo
  that ships no gate, so under the fix it became correctly UNGATED and its anchor stopped
  firing. Its subject is the warning's ORDERING, not which branch — so the fix was to create the
  gate file in the fixture and assert `gate_enforces_budget(repo)`, making its gatedness
  EXPLICIT where it had been accidental, and keeping every pre-existing assertion verbatim
  rather than re-pointing it at whatever fires. **Re-pointing would have been the silent
  coverage loss.**
- **A "POSITIVE CONTROL" can be the implementation restated.** Round 0's F4:
  `assert (gated / BUDGET_GATE_RELPATH).is_file()` cannot fail once
  `gate_enforces_budget(gated)` has passed, because that expression IS the function body. A real
  control shows the answer FLIP on the one thing that differs — add the gate to the ungated
  fixture in place and re-ask.

## How to verify

```bash
# 1. the pair, in one command — both branches of the fix
cd /home/zach/workspace/devrc-budget-repo-aware
nix develop . -c python3 - <<'PY'
import importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location("hd", "scripts/lib/handoff_doc.py")
hd = importlib.util.module_from_spec(spec); spec.loader.exec_module(hd)
over = hd.handoff_budget.MAX_BYTES + 1
rel  = "claudedocs/handoff-example-topic.md"
ungated = hd.budget_warning(rel, "x"*over, "x"*10, gated=False)
gated   = hd.budget_warning(rel, "x"*over, "x"*10, gated=True)
assert "NO GATE ENFORCES THIS IN THIS REPO" in ungated and "will go RED" not in ungated
assert "will go RED on `main`" in gated
assert hd.gate_enforces_budget(Path(".")) is True
assert hd.gate_enforces_budget(Path("/home/zach/workspace/civit/cli")) is False
print("both branches OK")
PY

# 2. the suite, and the mutation — note the filter must NAME the killing test
PYTHONDONTWRITEBYTECODE=1 nix develop . -c python3 -m pytest \
  scripts/tests/test_handoff_doc.py scripts/tests/test_handoff_doc_size.py -q   # 458 passed
```
