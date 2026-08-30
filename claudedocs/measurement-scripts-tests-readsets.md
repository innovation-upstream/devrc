# Measured: what `scripts/tests` actually reads — and why decomposing it buys ~8%

**Date:** 2026-08-30 · **Effort:** ci-speedup rank 2 · **Claim:** `ci-speedup-2`

## Why this exists

`claudedocs/handoff-ci-speedup.md` ranks "decompose `scripts/tests`" second and
attaches a 🔴 to its own input:

> The repo-wide-scanner classifier is NOT trustworthy. A regex for
> `git ls-files` / `REPO_ROOT.rglob` said 32 of 139 files (394s, 29%); positive
> and negative controls passed, but it over-classifies … The true always-run set
> is between ~8s and 394s and needs **empirical** measurement (change an
> unrelated file, see what fails), not another regex.

This is that measurement — a **read-set trace**, not a regex and not
perturbation: a `sys.addaudithook` records what each test file opens, lists,
scans and spawns while the suite runs.

## The answer

**All 144 test files in `scripts/tests` classified.** Timing comes from a
separate, untraced run so the hook's overhead is not in the numbers.

🔴 **TWO DENOMINATORS, NAMED — they are not the same number.** 144 files are
classified; only **133** have timing pytest will show, because `--durations`
hides everything under 5 ms. An earlier draft of this table quoted the timing
denominator's file counts under the classification's total and published
`22 + 18 + 93 = 133` against a claimed 143. Both halves are given below.

| bucket | files | of which timed | seconds | share of timed |
|---|---|---|---|---|
| **ALWAYS-RUN** — proven to read the tree | 27 | 27 | 185.5s | 14.3% |
| **OPAQUE** — read set UNKNOWN | 61 | 60 | 1010.2s | 78.1% |
| **scoped** — proven bounded | 56 | 46 | 97.6s | 7.5% |
| total | **144** | **133** | 1293.3s | |

- **Best-case ceiling for a perfect path→target mapping: 1.08x.**
- Estimate history: **3x → 1.7x → uncertain → 1.49x → 1.08x measured.** The
  1.49x was this document's own first answer and it was wrong; see below.
- The handoff estimated rank 3 at "~1.7x alone but ~3.6x after". Neither is
  reachable. **Only 7.5% of this suite's time is provably skippable today.**

### 🔴 The recommendation this produces: DO NOT BUILD RANK 3 YET

A path→target mapping built on today's tree buys **8%**. The work that raises
its ceiling is making the opaque subprocesses legible — until then the mapping
is a large mechanism guarding a rounding error, and every unmeasured file it
inherits is a chance to skip a test that should have run.

## 🔴 Subprocess opacity is the whole story

**78.1% of suite time** is 61 files that spawn a child process at a repo cwd
whose reads this tracer cannot see. The audit hook is per-interpreter; a child's
own `open` calls are invisible. We record argv and cwd, and a consumer must
treat such a file as must-run.

The biggest single contributors are `test_subsystem_store_api.py` and
`test_run_tests_floors.py`. Making the top few legible — by declaring their repo
reads, or running the child under a tracer — decides more than any directory
split, and is the prerequisite for rank 3 being worth building.

`test_drift_check.py`, the file the handoff named as the regex's false positive,
is **OPAQUE** here — not ALWAYS-RUN. "Unproven" is a different and more honest
answer than either the regex's "repo-wide" or a bare "scoped".

## Why OPAQUE is its own bucket, and why it is the DEFAULT

Folding unknown into always-run inflates a number that then reads as measured;
folding it into scoped is unsafe. RULES.md is explicit that UNMEASURED must not
be folded into a clean count.

🔴 **Opacity is the fall-through, not an enumerated list.** The first version
named the interpreters it considered opaque (`bash`, `python3`, …) and keyed on
the literal token `-c`. That implied everything unnamed was transparent, which
is backwards: a nested `python3 -m pytest <repo dir>` and a
`bash <repo script> <REPO_ROOT>` — the corpus's two most common opaque shapes —
matched neither branch and were published as "scoped, proven bounded". Only a
**recognised, adjudicated** command may now be scored clean.

## What this cannot see — state these with any claim

- **Reads inside a subprocess.** The entire OPAQUE bucket.
- 🔴 **Stat-only dependencies.** CPython raises **no audit event** for
  `os.stat`/`os.lstat`/`Path.exists()`/`Path.is_dir()`/`os.path.exists`
  (measured on 3.12.14, the gate's interpreter). A guard whose only dependency
  is `(REPO_ROOT / "scripts" / "x.sh").exists()` records an **empty** read set
  and classifies as bounded — so adding or deleting the very file it polices
  re-runs nothing. The corpus contains this shape. An earlier version listed
  `os.stat` in the traced-event set, where it raised nothing while *reading as*
  coverage.
- **Symlinks are not followed** — `_rel` avoids `resolve()` so the hook cannot
  re-enter, so a read reached through a symlink is attributed to the link path.
- **A test whose outcome depends on a file it never reads** — e.g. one asserting
  a count someone else computed. No read-tracer can see that; perturbation can,
  and has the complementary blind spot (an innocuous edit does not trip a
  scanner that only fails on violations). Neither method suffices alone.
- **One run.** Single-observation read sets; a data-dependent branch could read
  more next time. That error direction is under-classification, the unsafe one.

## The tools

- `scripts/testlib/readset_plugin.py` — the tracer. Opt in with
  `-p testlib.readset_plugin` and `DEVRC_READSET_OUT=<prefix>`; one JSON shard
  per xdist worker. Attributes **both** collection-time (module-level) and
  runtest-time reads, because several files here read `nix/home.nix` at import.
  It is inert unless that `-p` is passed.
- `scripts/lib/readset_classify.py` — merges shards, assigns buckets.
- `scripts/tests/test_readset_classify.py` — 27 guards, each pinning a defect
  one of these two files actually shipped.

Reproduce:

```bash
DEVRC_READSET_OUT=/tmp/rs PYTHONPATH=$DEVRC/scripts \
  python3 -m pytest scripts/tests -q -p no:cacheprovider \
  -p testlib.nolaunch_plugin -p testlib.spool_plugin \
  -p testlib.gitenv_plugin -p testlib.nogit_plugin \
  -p testlib.readset_plugin -n 4 --dist loadfile
python3 scripts/lib/readset_classify.py /tmp/rs.*.json --json /tmp/readsets.json
```

## This classifier reproduced the bug it exists to fix — seven times

The pattern is the deliverable's real lesson. Every one matched *characters* or
an *enumeration* rather than the operation actually performed:

1. Matched the bare tool name → `git init -q /tmp/x` scored a repo scan.
2. Matched a read verb anywhere in argv → a stub invoked as
   `/tmp/.../bw --nointeraction status` scored a repo scan.
3. Same, off **script text** inside `bash -c '<script mentioning git log>'`.
4. Took the first non-flag token as the git subcommand → `git -C scripts
   ls-files` read as subcommand "scripts" and was **acquitted**.
5. Keyed OPAQUE on the literal `-c` → nested `pytest <repo dir>` and
   `bash <repo script>` scored "proven bounded".
6. Acquitted on argv[0] being an absolute path outside the repo → real
   `/nix/store/.../git ls-files` scans of this tree acquitted.
7. Required cwd to be exactly `.` → a scan run with `cwd=scripts` acquitted.

Findings 1, 2 and 4 were caught by a mutation sweep that also showed **three
guards were unreachable** — an earlier acquittal always won, so deleting the
guard changed nothing while its "covering" test stayed green. Findings 5, 6 and
7, plus the `os.stat` and two `_rel` defects, came from an adversarial audit
that reproduced them on **real corpus files**, not invented argv.

🔴 **And the fix round introduced its own regression, in the unsafe direction.**
Switching the operand check to a separator-terminated prefix made REPO_ROOT
itself compare as outside the repo — `/…/devrc` does not start with `/…/devrc/`
— silently acquitting `git -C <REPO_ROOT> ls-files`, the most common way this
corpus spells a scan. ALWAYS-RUN fell 22 → 9 and it was caught only by diffing
the two classification runs against each other, not by any test.

Two pre-existing tests had to have their assertions **inverted**: they asserted
that an absolute-path binary was acquitted, and that a same-directory fixture
was not a dependency. Both passed. Both were wrong.

**A classifier built to fix over-classification that itself misclassifies is
worse than none, because its number reads as measured.** That is why every
number above is stated with its denominator and its blind spots.
