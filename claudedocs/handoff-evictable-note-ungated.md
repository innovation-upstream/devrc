# Handoff: evictable-note-ungated — 2026-09-21

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
Carry out **F2** — the operator's decision to stop withholding `evictable_note` from repos
that ship no handoff-size gate — and make the numbers-only note safe to print there.

🔴 **A NEW ARC. `handoff-handoff-resume-prune.md` is CLOSED, verdict ADDRESSED, and must
not be re-opened.** That doc's closing condition was re-verified live this session at
`origin/main` (not merely at the `b1bee35b` it was frozen against): #1815 `MERGED`
(`d9216e5e`), and `readlink -f ~/.claude/skills/handoff/SKILL.md` resolves to the same
`/nix/store` path at **20,176 B on both hosts**, byte-equal to `origin/main`'s blob. The
operator also **RATIFIED** that arc's mid-arc closing-condition correction, so its verdict
stands as written. F2 was recorded there as rank 2 with `forcing: user`, under a heading
reading *"NONE OF THESE BELONG TO THIS ARC — a session picking one up is starting a NEW
arc."* `--new-effort` was passed deliberately on that basis.

⚠ **The adjacent arc was checked and is NOT this one.** `handoff-budget-warning-repo-aware.md`
(devrc#1715, OPEN since 2026-09-15) is the effort that **introduced** the gated/ungated split
and `gate_enforces_budget()` — this one **removes part of that split** by the operator's later
decision. Two reasons it is not an update to that doc: its file does not exist in `origin/main`
at all (its PR is unmerged, so writing "in place" there is impossible from this tree), and its
finding — *the warning named a gate that cannot see most repos* — is the premise this work acts
ON rather than a question this work continues. 🔴 **If #1715 merges, do NOT retro-merge the two
docs**; note the relationship and leave both.

- **closing-condition:** `check` — **devrc#1826 is merged to `origin/main` AND
  `scripts/ship.sh` has converged both hosts** (read every per-host line, not the final
  verdict), so on BOTH machines this prints a note rather than silence:
  ```bash
  nix develop ~/workspace/devrc -c python3 -c "
  import importlib.machinery as m, importlib.util as u, pathlib
  p='/home/zach/workspace/devrc/scripts/lib/handoff_doc.py'
  l=m.SourceFileLoader('hd',p); s=u.spec_from_loader('hd',l)
  hd=u.module_from_spec(s); l.exec_module(hd)
  d=max(pathlib.Path('/home/zach/workspace/homelab-talos/claudedocs').glob('handoff-*.md'),
        key=lambda x:x.stat().st_size)
  w=hd.budget_warning('claudedocs/'+d.name, d.read_text(errors='replace'), '', gated=False)
  print('MET' if 'Evictable in THIS doc' in w else 'NOT MET')"
  ```

## State now
- **Branch / PR: `fix/evictable-note-ungated` → [devrc#1826](https://github.com/innovation-upstream/devrc/pull/1826), OPEN.** Commit `27ca55be`, pushed. Two files: `scripts/lib/handoff_doc.py`, `scripts/tests/test_handoff_doc.py` (+113/−14).
- **DONE — the guard is dropped in both numbers-only arms of `budget_warning`:** the
  ungated OVER arm now computes `evictable_note(merged_text, after - allowance)` and appends
  it; the NEAR arm's `note = evictable_note(merged_text, 0) if gated else ""` became
  unconditional.
- **DONE — two ladder-pointers deleted from `evictable_note` itself.** The shortfall line
  ended *"steps 2-4 of the playbook cover the rest"*; the zero case read *"before you need
  the ladder at all"*. See the gotcha below — this is the part worth reading.
- **DONE — measured, through the function, not asserted:** against homelab-talos's **70**
  live handoff docs, **31 now print a note, surfacing 289,805 B** of already-closed content
  their authors could not see.
- **IN FLIGHT: CI.** At hand-off time all four legs (`tekton/devrc-{pytests,nodetests,gotests,cairn-client-runs}`)
  were **pending**, and `gh pr view 1826 --json mergeable` still answered `UNKNOWN`.
- **IN FLIGHT: `/audit-pr 1826` has NOT been run.** Offered, not dispatched. Round 0 is the
  only round that can conclude *close this PR*, and that is actionable only while the merge
  decision is open — so it is rank 1 below.
- **NOT DEPLOYED, and deliberately stated separately from merged.** Both host clones are
  behind `origin/main` (workbench `98aa7b06`, laptop `8f95c342`, origin `3fa77f49`); nothing
  of this change runs anywhere until it merges and `ship.sh` converges.
- **No `clawgate-task:` field.** `clawgate_handoff.sh resolve` exited **5** — 0 tasks for
  this session. Its positive control passed (the same endpoint answered 1 link for another
  session), so the board is reachable and the token accepted; that narrows the reading but
  does NOT prove this session's id is right, because a wrong id also answers 200 with an
  empty array. Not a clean bill of health, and no task was created.

## Next steps (ranked)
1. **Audit, then merge devrc#1826, then `scripts/ship.sh`.** In that order: `/audit-pr 1826`
   worked as its ROUND 0 section first (requirements & deletion — it can conclude *close
   this PR*), then the nine correctness axes, then merge, then converge both hosts and run
   the closing-condition check above. Files: `scripts/lib/handoff_doc.py`,
   `scripts/tests/test_handoff_doc.py`. IN FLIGHT: devrc#1826.
   forcing: user — the operator decided F2 explicitly (ratify/reverse prompt, 2026-09-21)
   and this is the delivery of that decision.
2. **Sweep for the SAME leak class elsewhere: what other text does an UNGATED repo's author
   see, and does any of it name a devrc-only artifact?** This change found one instance by
   reading a diff; the question of whether `budget_warning`'s other branches, or any sibling
   warning on the `/handoff` write path, carry a second one was never asked. Start:
   `git -C $DEVRC grep -n "playbook\|refs/\|ladder" -- scripts/lib/handoff_doc.py` and check
   each hit against which `gated` arm can reach it.
   forcing: none

## Gotchas / decisions / dead-ends
- 🔴 **THE GUARD OUTLIVED THE THING IT GUARDED, AND THAT IS THE TRANSFERABLE SHAPE.** The
  `if gated` was specified when `evictable_note` ALSO carried the eviction ladder —
  prescription and numbers travelled together, so suppressing one suppressed both. #1821
  deleted the prescription surface outright, and nothing re-asked whether the guard still had
  a subject. What remained was a guard withholding a *measurement* on the strength of an
  argument (`civitai/cli#618`) that is entirely about being **TOLD to move bytes**. **When a
  feature is deleted, re-ask what its guards were protecting against** — a guard whose
  premise has been removed reads exactly like a guard that is still working.
- 🔴 **THE REAL FINDING: A NEGATIVE ASSERTION WRITTEN TO CATCH A LEAK PASSED WHILE THE LEAK
  WAS PRESENT.** My first draft asserted four PHRASES absent from the ungated arm
  (`"will go RED on \`main\`"`, `"demote dated evidence"`, `"Do NOT satisfy it by deleting"`,
  `"playbook prescribes"`) and was GREEN — while `evictable_note`'s shortfall line still
  ended *"steps 2-4 of the playbook cover the rest."* A phrase-list only ever catches the
  wordings you thought of. **Found by READING THE DIFF, not by any test.** Fixed by
  forbidding the bare words `playbook` and `ladder`, which cannot be said there at all. This
  is `claude/RULES.md`'s "a guard can be SPELLED rather than STRUCTURAL" in a new shape: the
  guard was spelled against four spellings of the hazard and the hazard had a fifth.
- 🔴 **TWO NUMBERS, BOTH CORRECT, ABOUT DIFFERENT THINGS — do not copy either forward.** The
  deciding handoff quoted **370,563 B** for homelab-talos; measuring through this code gives
  **289,805 B across 31 of 70 docs**. 370,563 is `handoff-audit.py`'s gross over ALL FOUR
  buckets; `evictable_note` reports step 1 (`resolved` + `done`) only. The code comment
  records the one this code can actually print, and says so. **Re-derive; the discrepancy is
  a definition, not a drift.**
- **Deleting the two ladder-pointers was a DELETION, not a rewrite, and it is why the note
  can travel.** In an ungated repo they named a test the repo does not ship — the
  `civitai/cli#618` failure in miniature, an authoritative-sounding step nobody's gate
  requires. In devrc they were a SECOND COPY of what the over-budget arm prints three lines
  above. One rule, one place: the ladder belongs to `test_handoff_doc_size.py`.
- 🔴 **One of the four new tests is an INVARIANT GUARD and is labelled as one in its own
  docstring** (`test_the_NEAR_arms_gate_specific_TAIL_still_differs_by_gatedness`). It is
  GREEN on pre-change code, because the behaviour it pins is the half of that arm which did
  NOT change. It is **not counted as regression coverage**; it was mutation-tested instead —
  collapsing `tail` to the gated wording killed it on its own `no gate enforces it here`
  assertion. The other three are the regression ones: **red at `3fa77f49`, green at HEAD.**
- **The gated/ungated split SURVIVES where it is still about a gate**, and that was a
  deliberate scope limit, not an oversight: the NEAR arm's `tail` (`red \`main\`` vs
  `nothing will go red`) and the GRANDFATHERED arm, which tells you to edit
  `handoff_budget.GRANDFATHERED` — a ledger that really does live only in devrc.
- ⚠ **`scoped-tests.sh` REFUSES this change by design** (exit 4): `scripts/lib/**` is a
  shared surface, and scoping it would be misleading rather than merely incomplete. Blast
  radius was verified directly instead — `budget_warning` has exactly **one** non-test caller
  (`handoff_doc.py:4540`), and the only other repo hits for these names
  (`scripts/browser-bridge/tests/test_server.py`, `scripts/handoff-audit.py:430`) are an
  unrelated function name and a comment. **503 passed** across `test_handoff_doc.py`,
  `test_handoff_doc_size.py`, `test_handoff_audit.py`. The full tiers were NOT run; CI is the
  signal, and it was pending at hand-off.
- **Mutation sweeps run under `PYTHONDONTWRITEBYTECODE=1`**, and both swapped files were
  restored from a `cp -a` copy and confirmed **byte-identical with `cmp`** — the
  same-second/same-length `.pyc` trap otherwise scores a mutant SURVIVED without executing it.

## How to verify
```bash
# (1) the regression matrix — red at the base, green at HEAD. 3 of 4; the fourth is the
#     invariant guard named above and is green at BOTH by construction.
WT=<a worktree of the branch>
git -C $DEVRC show 3fa77f49:scripts/lib/handoff_doc.py > "$WT/scripts/lib/handoff_doc.py"
PYTHONDONTWRITEBYTECODE=1 nix develop $DEVRC -c python3 -m pytest "$WT/scripts/tests/test_handoff_doc.py" \
  -q -p no:cacheprovider -k "UNGATED_over_budget_arm_CARRIES or SAME_TEXT_the_gated_arm \
  or NEAR_budget_arm_carries_the_note_when_UNGATED_TOO"        # expect: 3 failed
git -C "$WT" checkout scripts/lib/handoff_doc.py               # expect: 3 passed

# (2) the real path on a real ungated repo — must print numbers and name no
#     playbook / ladder / refs/
nix develop $DEVRC -c python3 -c "
import importlib.machinery as m, importlib.util as u, pathlib
l=m.SourceFileLoader('hd','$DEVRC/scripts/lib/handoff_doc.py')
hd=u.module_from_spec(u.spec_from_loader('hd',l)); l.exec_module(hd)
d=max(pathlib.Path('/home/zach/workspace/homelab-talos/claudedocs').glob('handoff-*.md'),
      key=lambda x:x.stat().st_size)
w=hd.budget_warning('claudedocs/'+d.name,d.read_text(errors='replace'),'',gated=False)
print(w); assert 'Evictable in THIS doc' in w
assert not any(k in w for k in ('playbook','ladder','claudedocs/refs/'))"

# (3) the three affected files, whole
PYTHONDONTWRITEBYTECODE=1 nix develop $DEVRC -c python3 -m pytest \
  $DEVRC/scripts/tests/test_handoff_doc{,_size}.py $DEVRC/scripts/tests/test_handoff_audit.py -q
```
