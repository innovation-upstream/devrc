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

- Branch `fix/budget-warning-repo-aware` @ `ff82a791`, worktree
  `/home/zach/workspace/devrc-budget-repo-aware`. **PR `devrc#1714` is OPEN and has NOT
  been audited** — `/audit-pr 1714` round 0 was offered at PR-create and not run.
- Two files: `scripts/lib/handoff_doc.py` (the predicate + the branch + the call site) and
  `scripts/tests/test_handoff_doc.py` (three new tests, one helper signature change).
- **458 passed** across `test_handoff_doc.py` + `test_handoff_doc_size.py` under
  `PYTHONDONTWRITEBYTECODE=1`. No existing assertion was weakened: the `_budget` helper
  defaults to `gated=True`, which is the devrc answer every pre-existing assertion assumed.
- **No `clawgate-task:` field.** `resolve` exited **5** — 0 tasks, and an unknown session id
  also answers `200` with an empty array, so that zero cannot distinguish "touched no task"
  from "wrong id". Not a clean bill of health. None written, none invented.
- This work came out of `civitai/cli#618`'s round-0 audit (finding F2), not from a devrc
  session. The cli arc that surfaced it is CLOSED.

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
- **Measured cost before the fix**, in `civitai/cli` 2026-09-14/15: **five evictions in two
  days, 35,517 B moved to `claudedocs/refs/`, and a heading-delimited slice that removed
  27,991 B — an entire ranked list — one step before a commit.** None required by any gate.
- **Ruled out:** *"compare the repo path against a known devrc root"* — **via: code**. In a
  WORKTREE the gate's own `REPO_ROOT` is the worktree, not the base clone, and this repo's
  agents work in worktrees constantly. The predicate is derived instead: a repo is gated iff
  it ships `scripts/tests/test_handoff_doc_size.py`.
- **Ruled out:** *"give `gated` a default"* — **via: code**. A default is exactly how the
  false claim survived; every caller silently received the devrc answer. It is keyword-only
  with `Parameter.empty`, and a test asserts that so a future default fails loudly.
- **Next probe:** `/audit-pr 1714` round 0, then merge. Nothing about the code is open.

## Next steps (ranked)

🔴 Numbering stable — rank is half a `claim-work` slug's identity.

1. **`/audit-pr 1714` round 0, then the nine axes, then merge.** It changes a warning every
   session reads, so a wrong branch is silently load-bearing.
   forcing: gate — an unaudited change to the line that tells everyone whether a gate failed.
2. **Consider making the premise TRUE for the repos that want it** — register more repos in
   the gate's corpus, or add a per-repo ceiling — rather than only removing the false claim.
   `handoff_index.default_repos()` is `[devrc, homelab-talos, civit/datapacket-talos,
   civit/civitai]`; `civit/cli` is in neither it nor `REPO_ENV_HANDLES`. **A separate effort
   by the closing condition above.** forcing: none
3. **Remove the two worktrees when `#1714` merges** — `devrc-budget-repo-aware` and
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
