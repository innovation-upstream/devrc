# Measured: what `scripts/tests` actually reads — and what decomposing it is worth

**Date:** 2026-08-30 · **Effort:** ci-speedup rank 2 · **Claim:** `ci-speedup-2`

## Why this exists

`claudedocs/handoff-ci-speedup.md` ranks "decompose `scripts/tests`" second and
attaches a 🔴 to its own input:

> The repo-wide-scanner classifier is NOT trustworthy. A regex for
> `git ls-files` / `REPO_ROOT.rglob` said 32 of 139 files (394s, 29%); positive
> and negative controls passed, but it over-classifies … The true always-run set
> is between ~8s and 394s and needs **empirical** measurement (change an
> unrelated file, see what fails), not another regex.

This is that measurement. It is not a regex and it is not a perturbation run —
it is a **read-set trace**: what each test file actually opens, lists, scans and
spawns, recorded by a `sys.addaudithook` while the suite runs.

## The answer

143 of 143 collectible test files measured (the other 4 files in the directory
are `conftest.py` and three `mutation_battery_*`/`mutants-*` harnesses, none of
them collectible tests). Timing measured in a **separate, untraced** run so the
hook's overhead is not in the numbers.

| bucket | files | seconds | share |
|---|---|---|---|
| **ALWAYS-RUN** — proven to read the tree | 22 | 163.1s | 12.6% |
| **OPAQUE** — read set UNKNOWN | 18 | 704.3s | 54.5% |
| **scoped** — proven bounded | 93 | 425.9s | 32.9% |

- **The proven always-run set is 163.1s.** The handoff bracketed it at "between
  ~8s and 394s"; it lands in the lower half, and far below the 394s the regex
  implied.
- **Best-case speedup from a perfect path→target mapping: 1.49x.** The handoff
  estimated rank 3 at "~1.7x alone but ~3.6x after". 1.49x is the measured
  ceiling *today*, and the 3.6x is not reachable while OPAQUE stands.
- Estimate history is now **3x → 1.7x → uncertain → 1.49x measured.**

### Cross-check on the timing

Summed per-test durations come to **1293.3s** visible, plus ~74s of sub-5ms
durations pytest hides (27,012 of them, capped at 0.005s each). That totals
~1367s — and the handoff independently quotes **1367s of work** for this suite,
derived by a different route. The timing instrument reproduces a number nobody
fed it.

## 🔴 The finding that reframes rank 3

**The blocker is not file layout. It is subprocess opacity.**

54.5% of the suite's time sits in 18 files that shell out to `bash -c` /
`python -c` with a repo-root cwd. The audit hook is per-interpreter and cannot
see a child process's reads, so those files' dependencies are genuinely
unmeasured — and a path→target mapping must fail safe and run them all. Two
files are most of it:

| file | time | bucket |
|---|---|---|
| `test_subsystem_store_api.py` | 313.3s | OPAQUE |
| `test_run_tests_floors.py` | 171.2s | OPAQUE |
| `test_run_tests_preconditions.py` | 77.3s | OPAQUE |
| `test_drift_check.py` | 62.7s | OPAQUE |

Resolving the top two alone adjudicates ~37% of total suite time. That is a
better next move than splitting directories, and it is cheap to state as a
target: make those tests' repo reads visible (declare them, or run the child
under a tracer), then re-run this measurement.

**`test_drift_check.py` is the specific file the handoff named as the regex's
false positive.** It is not ALWAYS-RUN here. It is OPAQUE — its repo-wide-ness
is *unproven*, which is a different and more honest answer than either the
regex's "repo-wide" or a bare "scoped".

## Why OPAQUE is its own bucket

Folding unknown into always-run would inflate a number that then reads as
measured; folding it into scoped would be unsafe. RULES.md is explicit that
UNMEASURED must not be folded into a clean count, so it is reported separately
and a consumer must treat it as must-run.

## The tools

- `scripts/testlib/readset_plugin.py` — the tracer. Opt in with
  `-p testlib.readset_plugin` and `DEVRC_READSET_OUT=<prefix>`; writes one JSON
  shard per xdist worker. Attributes **both** collection-time (module-level) and
  runtest-time reads, because several files in this corpus read `nix/home.nix`
  at import.
- `scripts/lib/readset_classify.py` — merges shards and assigns buckets.
- `scripts/tests/test_readset_classify.py` — 15 guards, every one pinning a bug
  this classifier actually shipped.

Reproduce:

```bash
DEVRC_READSET_OUT=/tmp/rs PYTHONPATH=$DEVRC/scripts \
  python3 -m pytest scripts/tests -q -p no:cacheprovider \
  -p testlib.nolaunch_plugin -p testlib.spool_plugin \
  -p testlib.gitenv_plugin -p testlib.nogit_plugin \
  -p testlib.readset_plugin -n 4 --dist loadfile
python3 scripts/lib/readset_classify.py /tmp/rs.*.json --json readsets.json
```

## What this cannot see — state these with any claim

- **Reads inside a subprocess.** The whole OPAQUE bucket. Argv and cwd are
  recorded; the child's own `open` calls are not.
- **A test whose outcome depends on a file it never reads** — e.g. one asserting
  a count someone else computed. No read-tracer can see that. Perturbation can,
  and has the complementary blind spot (an innocuous edit does not trip a
  scanner that only fails on violations). Neither method is sufficient alone.
- **One run.** These are single-observation read sets; a test with a
  data-dependent branch could read more on another run. The direction of that
  error is under-classification, which is the unsafe one.

## Building the classifier reproduced the very bug it exists to fix — four times

Worth recording, because the pattern is the point. Each draft matched
*characters* rather than *operations*, exactly as the original regex did:

1. Matched the bare tool name → `git init -q /tmp/x` scored as a repo scan.
2. Matched a read verb anywhere in argv → a stub binary invoked as
   `/tmp/.../bw --nointeraction status` scored as a repo scan.
3. Same → `bash -c '<script mentioning git log>'` scored as a repo scan, off the
   **script text**.
4. Took the first non-flag token as the git subcommand → `git -C scripts
   ls-files` read as subcommand "scripts" and was **acquitted**. This one is
   under-classification: it moved **15 files** out of ALWAYS-RUN. Fixing it took
   the count 7 → 22 and the always-run time 104.7s → 163.1s.

A mutation sweep found 1, 2 and 4; three of the guards were **unreachable** —
an earlier acquittal always won, so deleting the guard changed nothing and the
test that "covered" it stayed green. A `-C` branch was also found to be dead
code (never the sole acquitter) and deleted rather than left reading as
coverage.

**A classifier built to fix over-classification that itself over-classifies is
worse than none, because its number reads as measured.**
