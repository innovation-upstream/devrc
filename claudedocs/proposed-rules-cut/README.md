# proposed-rules-cut — a 2026-08-06 SNAPSHOT proposal. 🔴 DO NOT APPLY VERBATIM.

These two files are a **draft rewrite** of `claude/RULES.md` and `claude/PRINCIPLES.md`
proposed on 2026-08-06, cutting RULES.md from **33,561 B → 7,103 B** (~79%). They were
found untracked in the workbench working tree on 2026-08-15 and are committed here so the
thinking is not lost to a stray `git checkout`. They were **never applied**, and they are
**not a candidate to apply now**.

## Why not

The proposal is a cut of the RULES.md that existed on 2026-08-06. Rule families added
since are simply absent from it, so applying it verbatim is a **deletion**, not a cut.
Measured 2026-08-15 (`grep -ci`, against a positive control — `Verification` appears in
both, so the zeros below are real absences and not a broken pattern):

| term | in the proposal | in current `claude/RULES.md` |
|---|---|---|
| `stash` | 0 | 10 |
| `worktree` | 0 | 10 |
| `repo-GLOBAL` | 0 | 2 |
| `mutation` | 0 | 3 |
| `positive control` | 0 | 1 |
| `squash` | 0 | 1 |
| `Verification` (positive control) | 2 | 3 |

The `git stash` / worktree-isolation family alone is the one that cost a corrupted
`.sops.yaml`; the mutation-testing and positive-control families are the ones several
later sessions were caught by. None of it existed in the tree this proposal was cut from.

## What it is still good for

The **shape** of the cut, not its content: eleven sections, one screen, trigger lines and
a ✅/❌ pair per family. `claude/RULES.md` has since grown to 34,268 B against a 38,400 B
ceiling (`scripts/tests/test_rules_size.py`, which owns the constants). If a future cut is
wanted, read this for the structure it argued for and re-derive the content from the
CURRENT file — never by restoring these bytes.

Related: `#382` ("retire 7 rules the model no longer needs told — measured, not assumed")
is the direction that *did* land, and is the better precedent — it evicted by measurement
rather than by wholesale rewrite.
