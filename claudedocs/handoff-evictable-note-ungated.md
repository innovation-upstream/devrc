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
- ✅ **THIS ARC IS CLOSED. Verdict: ADDRESSED.** The closing condition was run on BOTH machines
  on 2026-09-21 and returned **MET** on each — it asserts all three clauses at once (the note
  prints, no deficit wording, no prescription).
  - (a) **devrc#1826 MERGED** — squash `62e811e9`, verified by CONTENT on `origin/main` (both
    `over_by=0` call sites present); a squash never makes the head an ancestor, so ancestry is
    not the check. All four Tekton legs green at the final head `a1f59620`.
  - (b) **`scripts/ship.sh` converged both hosts** to `e6bd3592` — every per-host line read, not
    the verdict: workbench and laptop each `✅ VERIFIED — on branch main at origin/main +
    switched`, 620 / 579 managed artifacts resolving, 0 dangling, 0 stale.
- **Re-verified 2026-09-23**: both PRs still `MERGED`, the two `over_by=0` call sites still on
  `origin/main`, and **nothing has touched `handoff_doc.py` or `test_handoff_doc.py` since the
  merge**. ⚠ The laptop is now 1 commit behind `origin/main` — a LATER, unrelated commit
  (`#1853`), not drift in this work; it needs an ordinary ship and does not reopen this arc.
- **Shipped behaviour:** the ungated over-budget arm passes `over_by=0`, so its note reports what
  has closed and asserts no shortfall against a ceiling that block says nothing enforces. The
  gated arm keeps its shortfall. Two ladder-pointers deleted from `evictable_note` itself.
- 🔴 **REACH IS 13 DOCS, NOT 31** — measured through `budget_warning`, the only caller. `31` is
  the `evictable_note`-non-empty set; the other 18 are UNDER budget, so `budget_warning` returns
  `""` for them before and after. The operator approved F2 on a brief quoting **370,563 B**; the
  withheld set was 13 documents. No byte total is recorded in the source — it is corpus-volatile.
- **Audited rounds 0–2, ladder CLOSED on the attribution gate**, not on a clean round. Rounds 1
  and 2 each found only false claims in the previous round's own prose and changed **zero
  executable payload lines** — two consecutive, which is the gate. No round found a 🔴. The
  payload was correct from round 1 and never moved again.

## Next steps (ranked)
✅ **The former rank 1 (`ship.sh` + the closing-condition check) is DONE** — completed 2026-09-21,
result above. It is struck from the queue rather than left listed: an item that has landed but is
still advertised is what makes a second session re-do it, and the claim lock cannot catch that
because `--release` is called exactly when the item completes. This arc's own predecessor
(`handoff-handoff-resume-prune.md`) carried two such items for two days.

1. **One corpus pass, at the ~2026-10-04 deletion trigger**, covering both open questions — they
   need the same measurement and are one item, not two. (a) Feed the trigger inherited from
   `handoff-handoff-resume-prune.md`: *"if neither moves, P1′ informed nobody and should be
   deleted."* 🔴 **Evaluate it against 13 docs, not 31** — round 0 flagged that #1826 widened
   `evictable_note`'s blast radius before its own deletion trigger fired. (b) While the corpus is
   loaded, sweep for the same leak class elsewhere: what other text does an UNGATED repo's author
   see, and does any of it name a devrc-only artifact?
   `git -C $DEVRC grep -n "playbook\|refs/\|ladder" -- scripts/lib/handoff_doc.py`, then check each
   hit against which `gated` arm reaches it.
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
- 🔴 ~~**TWO NUMBERS, BOTH CORRECT, ABOUT DIFFERENT THINGS — do not copy either forward.** The
  deciding handoff quoted **370,563 B** for homelab-talos; measuring through this code gives
  **289,805 B across 31 of 70 docs**.~~ 🔴 **RETRACTED — THE SECOND NUMBER IS NOT "CORRECT
  ABOUT A DIFFERENT THING", IT IS WRONG FOR THE ONLY THING IT WAS USED FOR, AND THIS BULLET'S
  "both correct" FRAMING IS WHAT MADE IT LOOK SETTLED.** `289,805 B / 31 docs` was measured by
  looping `evictable_note()` directly; the quantity that matters is what `budget_warning`
  newly PRINTS, which is **13 of homelab-talos's 70 docs**. The other 18 are under budget, so
  `budget_warning` returns `""` for them whether gated or not. Refuted by #1826 round 0. The
  rest of the bullet stands: 370,563 is `handoff-audit.py`'s gross over ALL FOUR buckets while
  `evictable_note` reports step 1 (`resolved` + `done`) only, and no byte total belongs in the
  source because the corpus moves. **Re-derive — and re-derive THROUGH THE CALLER, not through
  the function you happen to be reading.** Kept struck rather than deleted: a wrong reading a
  session acted on is the record, and this one reached the operator.
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

- 🔴 **THE MEASUREMENT THAT DECIDED THE ARC WAS TAKEN THROUGH THE WRONG FUNCTION, AND THE COMMENT
  CLAIMED OTHERWISE.** I looped `evictable_note()` directly over the corpus, then wrote a source
  comment saying *"MEASURED HERE, through this very function"* — meaning `budget_warning`. 2.4×
  overstatement (31 → 13), caught by round 0 re-deriving it. **When a comment names the function
  it measured through, that is a CLAIM — check the loop actually called it.**
- 🔴 **THE DEFICIT WAS THE REAL DEFECT, AND MY OWN NEGATIVE ASSERTION PASSED OVER IT.** The first
  implementation passed the true overage, rendering *"does NOT clear the 260,108 B you are over
  by"* — a shortfall against a ceiling the same block says nothing enforces. That is the
  `civitai/cli#618` pressure shape with the prescription removed and the false-consequence framing
  kept. My test forbade four PHRASES and was green throughout, because the text names no playbook
  and no ladder. **Forbid the bare WORDS; a phrase-list only catches the wordings you imagined.**
- 🔴 **A REQUIREMENT I INVENTED CAUSED IT.** *"Both arms print the SAME measurement"*, justified by
  "one rule, one place" — a rule about a predicate duplicated across call sites, not about two
  rendering arms. It was the only thing forcing the overage through. Deleted with its test.
  **Round 0's highest-value question is "who authored this requirement"; mine had no author.**
- 🔴 **I CITED A RULES.md TRAP IN A DOCSTRING AND THEN WALKED INTO IT.** I wrote
  *"🔴 SCOPED TO THE NOTE, AND THE SCOPE IS LOAD-BEARING"* over a negative loop that **cannot fail
  while the assertion above it passes** — that assertion only passes at `over_by == 0`, and at
  `over_by == 0` the forbidden strings cannot be emitted. RULES.md's *"an earlier check always
  wins so the guard never executes"*. A reviewer trusting the 🔴 would trim the assertion that
  actually kills a revert and keep the decoration. The docstring now says which line is the guard.
- 🔴 **THREE OF MY COMMENTS ASSERTED THE OPPOSITE OF A PASSING TEST IN THE SAME FILE.** They said
  the ungated arm *"names no threshold"*; its head line renders `over by {N} B`, and
  `test_EVERY_branch_that_names_the_gate_is_repo_aware` has always pinned that with
  `assert "over by 1 B" in ungated_over`. Both kept passing. **A comment is a claim: grep the
  suite for what it asserts before writing the invariant down.**
- 🔴 **FIX BY CLAIMING LESS, NOT BY CLAIMING DIFFERENTLY.** Rounds 1 and 2 each found only defects
  in the previous round's prose — five separate over-claims about coverage, reachability and
  position. The ones that ended it were deletions: a docstring that now asserts **nothing** about
  what reaches an arm beats one carrying a freshly-counted number the next edit stales. Four
  non-reproducing positional/volume counts were produced across three rounds.
- 🔴 **THE ATTRIBUTION GATE'S UNIT IS EXECUTABLE LINES, AND GETTING IT WRONG DISARMS THE GATE.** My
  round-1 claims block recorded `payload=54`, counting COMMENT lines in the payload file. Under
  that unit every prose fix round scores non-zero forever and the gate can never fire — which is
  how these ladders reach 12 and 24 rounds. The honest count was **0 for both rounds 1 and 2**.
  Recorded as a correction on the PR rather than re-decided silently mid-ladder.
- ⚠ **`NO CAPACITY` is the `error` class, not a failure.** #1827's four legs came back
  *"the gate never started (queued past its deadline). Not a code failure."* Do not debug a diff
  against it.
- ⚠ **A squash merge takes its message from the PR BODY.** The retracted 31 / 289,805 B figure
  would have landed in `main`'s history with the correction living only in a discarded commit. The
  body was struck through and annotated, and the squash body written explicitly.
- ⚠ **`scoped-tests.sh` refuses this diff by design** (`scripts/lib/**` is a shared surface, exit
  4). Blast radius was established directly instead: `budget_warning` has one non-test caller;
  the other two repo hits for these names are an unrelated function and a comment.

- 🔴 **I SHIPPED THE EXACT DEFECT I HAD JUST WRITTEN A RULE AGAINST, IN THIS DOC, ONE SECTION
  APART.** The closeout of the predecessor arc's queue (devrc#1855) added the rule *"closing an
  item means editing the queue, in the same session that finishes it"* — while THIS doc's rank 1
  had been complete for two days and its `State now` still read *"the closing condition is NOT yet
  met"*. Found only because the operator asked whether anything was outstanding. **Writing the
  rule is not applying it: sweep YOUR OWN artifacts for the shape in the same pass, because the
  document you are editing is the one you are least likely to re-read.**

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
