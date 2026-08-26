---
clawgate-task: 358
---
# Handoff: dead-guard-scan — 2026-08-26

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal

Clawgate task #358: find guard branches that handle cases the corpus contains zero of,
delete them, and land a mechanical detector so the class stops recurring. **Task is
`complete`.** The detector shipped; the deletion premise did not survive contact.

## State now

- **All merged, nothing in flight.** Working trees clean of this work.
  - devrc **#842** → `ad8259cf` — the detector, registry and census
  - devrc **#871** → `89166956` — 73 flags resolved
  - homelab-infra **#409** → `86c807a5` — the other 13 flags
  - devrc **#879 / #880 / #883** + homelab-infra **#414** — the 27 planted positive
    controls (step 1 below), 2026-08-26
  - devrc **this PR** — the `exclude` registry status, and this doc's two corrections
- **CI green on both merges**: devrc `16,723 passed / 0 failed` across 28 targets;
  homelab-infra all 7 `gitops-validate` legs including `scripts/tests`.
- Verified **by content, not ancestry** — a squash merge makes
  `git merge-base --is-ancestor` permanently false, so that check is useless here.
- Branches deleted. Worktrees removed.

What shipped, in devrc:

| path | what it is |
|---|---|
| `scripts/dead-guard-scan.py` | CLI: `--repo` / `--self-test` / `--census` / `--registry` / `--python` |
| `scripts/lib/dead_guard.py` | ast branch enumeration + the justification hatch |
| `scripts/testlib/dead_guard_plugin.py` | stdlib `sys.settrace` pytest plugin |
| `scripts/data/dead-guard-registry.tsv` | what is measured, and what is NOT, with reasons |
| `scripts/data/dead-guard-census.tsv` | the measurement, all four repos |
| `scripts/tests/test_dead_guard_scan.py` | 76 tests |
| `scripts/tests/mutants-dead-guard.sh` | 44 mutants, each naming the test that must kill it |
| `scripts/tests/mutants-dead-guard-exclude.sh` | 15 mutants for the `exclude` status |

Live measurement, re-derived 2026-08-26 in clean worktrees off `origin/main` /
`origin/trunk` (never the primary clones — a stale tree measures a stale repo):

```
devrc          27 guards instrumented, 36 flagged, 34 justified, 2 unresolved  exit 1
homelab-infra   7 guards instrumented,  6 flagged,  6 justified, 0 unresolved  exit 0
talos-infra     0 instrumentable, 3 out-of-instrument rows                     exit 0
civitai         0 instrumentable, 2 out-of-instrument rows                     exit 0
```

🔴 **The PREVIOUS revision of that table recorded devrc as `53 flagged, 53 justified,
0 unresolved, exit 0`. The unresolved count was, and is, 2** — the table above is the
corrected one. Both sit in `scripts/tests/test_no_real_launchers.py` —
the `(stubs / "systemctl").unlink(missing_ok=True)` branch and the
`os.environ["PYTEST_XDIST_WORKER"] = prev` branch — `:674` and `:804` as of
`1ef86e06`, but #854 moved both by 20 lines the same day, so **read the line
from the census, never from this doc**. They are pre-existing and
deliberately left unclosed; they are in the committed census with an empty justification
column, which is how you find them. devrc's run is also **RED (5 environmental failures
on this box)** and the scan stamps that into the census itself — see "How to verify".

## Next steps (ranked)

1. ~~**Write positive controls for the 27 reporting branches.**~~ **DONE 2026-08-26.**
   19 in `devrc` + 8 in `homelab-infra`, all merged: devrc **#879 / #880 / #883** →
   `main`, homelab-infra **#414** → `trunk`. `grep -rn "no planted positive control"`
   now returns **0 source hits in both repos**, and **0 rows** carry that reason in
   `scripts/data/dead-guard-census.tsv`. Closing condition met per branch: a test that
   fails when the guard is removed.
   🔴 **The figure in this doc was 40 and that was wrong — the true count was 27**
   (19 devrc + 8 homelab-infra), verified at three refs and against the committed census.
   40 was never measured; it was carried forward from an earlier reading and repeated.
   *Regenerating the census to confirm the closure exposed a defect in the scan itself,
   fixed in the PR that corrected this doc:* homelab's one `instrument` selector
   (`scripts/tests/test_*.py`) swept up four `test_vetr_ci_*.py` files that are standalone
   docker/network harnesses, not pytest modules — and one of them calls `sys.exit(0)` at
   MODULE level, which pytest reports as `INTERNALERROR` during collection and which
   **aborts the entire session** (`no tests ran`, rc=3). The scan therefore traced
   **nothing** and reported **44 flags off a run in which no guard line executed**. The
   homelab files are correct for their two real callers (direct invocation, and
   `<homelab-infra>/scripts/tests/run-ci-suite.sh` driving them one at a time through
   `python3 -m unittest discover`); the registry now carries a third status, `exclude`,
   whose selector is `<path-glob> -- <reason>`, is parsed and validated at load time, and
   subtracts from both the traced target set and the pytest command line while writing
   every removed file into the census with its reason. `out-of-instrument` stays prose and
   was deliberately not reinterpreted.
2. **Decide whether the detector should gate anything.** It is advisory today and gates
   nothing (`scripts/dead-guard-scan.py` docstring says so). Wiring it into
   `scripts/run-tests.sh` or the pre-push hook is a deliberate, separate decision; the
   delta-only philosophy other devrc gates use would be the model.
3. **Extend to a second language, or decide not to.** ~180 of ~270 guards are bash / TS /
   Go and are recorded `out-of-instrument` with reasons in
   `scripts/data/dead-guard-registry.tsv`. bash tracing via `PS4`/`BASH_XTRACEFD` was
   tested and works but cannot discriminate a single-line `if …; then X; fi`.

## Gotchas / decisions / dead-ends

- 🔴 **The task's premise did not hold: ~0 of 86 flags were dead code.** The population is
  firing paths with no control (**27**, not the 40 this doc claimed for two revisions),
  8 `except` handlers, recognition branches, and a large block that was an artefact of the
  instrument. Do not restart this expecting deletions.
- 🔴 **A REGISTRY SELECTOR IS A CLAIM THAT ITS MATCHES ARE COLLECTABLE, AND NOTHING
  CHECKED IT.** `scripts/tests/test_*.py` is a shape, not a contract: a file can be named
  `test_*.py`, sit in the test directory, and still be a standalone script that exits at
  import. One such file aborted the whole pytest session, and the failure surfaced as
  **44 confident flags**, not as an error — the classic reassuring output from a probe
  wired to nothing. The tell was in the report all along: `pytest rc=3` printed on the
  same line as `instrumented : 11 guard file(s)`. **Read the rc, not the flag count.**
- 🔴 **THREE claims of mine were refuted by measurement and retracted. Do not re-derive
  them:**
  - *"No reachable input distinguishes `last = max(...)` from `last = first`"* — **false.**
    `global`/`nonlocal` emit no bytecode, so a body starting with one has an untraceable
    first line while the rest runs. It is now a fixture and a killed mutant.
  - *"`public_ip_scan.py:235` is a duplicated predicate that cannot fire"* — **false.**
    `repo_files()` has TWO paths; the `git ls-files` path does **not** filter, so that
    check is the only filter there. It never fires because SKIP_DIRS are gitignored.
    Deleting it would have removed a live defensive filter. Same at
    `client_host_scan.py:216`.
  - *"At least 19 of the 29 self-scan flags plainly execute"* — **measured: 6** in-process.
    I repeated an auditor's figure without re-deriving it, which is precisely the defect
    the tool exists to find.
- 🔴 **`scripts/dead-guard-scan.py` is `out-of-instrument` ON PURPOSE.** Its own tests
  drive the shipped CLI through `subprocess.run` (8 of them), and `sys.settrace` is
  per-interpreter, so tracing under-reports it wholesale. `lib/dead_guard.py` IS
  instrumented and measures clean. Re-registering the CLI would resurrect 29 bogus flags.
- 🔴 **`launcher_scan.py:101` is the ONE genuinely dead spelling and is KEPT deliberately.**
  Bare-name `environ`/`getenv`, zero corpus instances, attribute form caught three lines
  above. Its own docstring calls the over-width right "for a scan whose failure mode is a
  missed clobber". Operator decision 2026-08-26: do not narrow a launcher-clobber safety
  scan. Do not "clean this up".
- **Stated limits that are real, not TODOs:** a test that SAVES AND RESTORES the tracer is
  invisible to clobber detection (the slot looks right at every inspectable boundary);
  `run_traced` does not attach devrc's four guard plugins; `# pragma: no cover` overlaps
  coverage.py's own meaning (`dead-guard-ok` is the unambiguous alias); the hatch requires
  a reason but does **not** check its truth.
- **No follow-up task was filed for step 1**, deliberately. Per this repo's
  closing-condition rule I could name the mechanical check but not who owns it; minting an
  object nobody can close is the `object-leak` failure. It was written here instead — and
  it closed here, in four PRs, without an object ever existing. The doc was the queue.
- **Process failures worth not repeating:** I let a green **one-target** local run stand
  for a green **28-target** CI run, and pushed two rounds on top of a red gate I had not
  re-checked. The red gate turned out to be devrc `#855` on main (a random tmpdir in a
  parametrize ID broke xdist collection repo-wide) — not mine, but I could not have known
  without looking.
- **Five adversarial audit rounds ran, and most findings were in the PREVIOUS round's
  fixes** — including one where the idempotence fix deleted the RED-RUN warning the same
  round added, and one where the `else` fix broke `except` and silently invalidated four
  real justifications already in the committed census.

## How to verify

```bash
# the detector. 🔴 Scan a CLEAN WORKTREE, not the primary clone -- a clone hundreds of
# commits behind measures a repo nobody is running, and says nothing about it.
DEVRC=~/workspace/devrc
git -C "$DEVRC" worktree add --detach /tmp/dgv origin/main
python3 "$DEVRC"/scripts/dead-guard-scan.py --repo /tmp/dgv          # exit 1: 2 unresolved
git -C ~/workspace/homelab-talos worktree add --detach /tmp/dgh origin/trunk
python3 "$DEVRC"/scripts/dead-guard-scan.py --repo /tmp/dgh          # exit 0

# 🔴 READ `pytest rc=` ON THE `instrumented :` LINE BEFORE READING THE FLAG COUNT.
# rc=3 means the session aborted and the run traced nothing; the flag list is then an
# artefact, not a measurement. That is how 44 bogus homelab flags were once published.

# its own controls, both directions (7 of them)
python3 "$DEVRC"/scripts/dead-guard-scan.py --self-test

# the suite and the two mutation batteries
python3 -m pytest "$DEVRC"/scripts/tests/test_dead_guard_scan.py -q
bash "$DEVRC"/scripts/tests/mutants-dead-guard.sh          # ~15 min, mutates only a /tmp copy
bash "$DEVRC"/scripts/tests/mutants-dead-guard-exclude.sh  # 15 mutants for `exclude`
```

⚠️ **This box's devrc suite is red for environmental reasons** — the default `python3`
resolves to another repo's `.venv`, missing `logrotate`/`nix-instantiate`, giving ~68
pre-existing failures in `test_nogit_isolation`, `test_run_tests_floors`,
`test_activity_spool_isolation`, `test_claude_log_rotate`, `test_run_tests_preconditions`
and friends. **CI is the authority.** Compare base vs branch before calling anything a
regression, and never scope that comparison to one target.
