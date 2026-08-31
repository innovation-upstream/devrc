# Handoff: handoff-doc-stale-base-guard — 2026-08-30

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

🔴 **No `clawgate-task:` field.** `clawgate_handoff.sh resolve` exited **5** — 0 tasks for
this session — with its positive control confirming the board answered 2 links for another
session. A wrong session id ALSO answers `200` with an empty array, so this is a real reading
of the board and **not** proof the id is right. No task was created.

## Goal
`handoff_doc.py` DETECTED the case where the doc is absent locally but present on the
mainline — "every section will merge as NEW and the committed document will be replaced by
this delta" — and exited **0**. That was survivable while a human answered a y/N; the prompt
was retired 2026-08-23, leaving the warning as the only thing between that diff and a pushed
commit. Turn it into a refusal.

## State now
- **devrc#1046 MERGED** — squash `e9437342`. Verified by CONTENT on `origin/main` (a squash
  never makes the head an ancestor): `EXIT_STALE_BASE = 9`, `test_no_two_exit_constants_share_a_value`,
  `test_a_STALE_BASE_in_a_REALISTIC_repo_is_not_reported_as_a_NEW_effort`, and rule (i)
  consulting `not currency.replaces_mainline_doc("")` are all present.
- **Rank 1 is DONE up to review: devrc#1146 is OPEN**, `fix/audit-ladder-scaffolding-gaps`,
  closing **#1093** and **#1115** (both linked — `closingIssuesReferences` confirms).
  Commits: `b1263772` the seven fixes · `afe8e190` the mutants moved into the tree ·
  `d42a353b` audit round 1. The branch was 23 behind `main`, not the 9 this doc used to say;
  rebased onto `093a63db` first, so **the tree that was gated IS the merged tree**.
- **Gate at `d42a353b`, both tiers ONE AT A TIME (#1088), read from each runner's own
  `RESULT:` line:** `pytests` PASS — collected=19533 passed=19531 skipped=2 failed=0
  (floor 18145); `nodetests` PASS — suites=5 files=41 tests=1441 pass=1441 fail=0
  (floor 1367). `mutants-handoff-cap.sh`: **47 rows, 0 failures**, baseline clean,
  0 DID-NOT-APPLY, 0 WRONG-KILLER.
- **The mutants now live IN THE TREE**, as three new sections of
  `scripts/tests/mutants-handoff-cap.sh` — not a second harness, because that file already
  owns the named-killer WRONG-KILLER check, the collected-test floor, the DID-NOT-APPLY diff
  and the restore. It needed a third pristine copy (`$T/suite.orig`): the round-4 negative
  control is the first row that mutates the SUITE.
- **Audit round 1 (`/audit-pr 1146`) returned 6 findings; all 6 are fixed in `d42a353b`.**
  The two that mattered were guards reading as coverage while providing less than their
  docstrings claimed: the prose test was SPELLED, not structural (rewriting
  `status=new-doc` (7) → (9) left all 297 tests GREEN, because `status=dated-topic` still
  spelled `(7)`), and `assert len(codes) == 9` sat ABOVE the injectivity loop, so a colliding
  constant died to `assert 10 == 9` and the collision remedy never rendered — **#1093.1's own
  defect recreated in a sibling test**. Both fixes were watched red by mutation.
- **A DELTA RE-AUDIT of `afe8e190..d42a353b` is still owed** — an audit fix resets the
  verification gate, and every delta round so far in this effort found something the previous
  round's fix introduced.
- Also merged in an earlier session: **ZacxDev/homelab-infra#460** (`26d98f1b`) and **#519**
  (subsystem-store-api `0.5.0` → `0.6.0`).
- **subsystem-store-api is DEPLOYED and VERIFIED**: pod `…-97gnp`, image `0.6.0`,
  `_audit_lock` count **3** (was 0), `server.py` 226,998 B (was 222,147), `/healthz` 200,
  0 restarts. devrc#996's fix is live.
- **Claim:** held as `devrc-1093-1115-scaffolding`. **Release it when #1146 merges.**

## Open investigations — live diagnosis state

### `test_live_cotenants_sees_another_process_in_the_repo` fails intermittently under full-suite load
- **Symptom + exact repro:** no reliable repro. `scripts/tests/test_git_repo_isolation.py`,
  assertion `assert live_cotenants([git_dir]) == []` fails with e.g. `['92981:git']` —
  "a brand-new tmp repo already has tenants?"
- **Observed (with values):** failed on 2 of 3 full sandbox runs on trees containing the
  branch; **passed on a re-run of the IDENTICAL tree at essentially the same load**
  (31.83 vs 32.89). 0 of 2 on `main`-only runs. Passes in isolation. Running the PR's
  128 `git`-spawning test calls alongside it at `-n 4`: **3/3 green**.
- **Ruled out:** *caused by this PR* — it touches 3 files, none in `testlib/gitenv.py` or
  that test; `_mkrepo` uses `subprocess.run`, which waits, so the "unreaped own `git init`"
  theory is dead. *Load alone* — it passes at equal load.
- **Leading hypothesis:** a race in the co-tenant probe itself; `live_cotenants` excludes own
  lineage and sibling xdist workers, then requires a foreign process's `cwd` to resolve INSIDE
  that tmp repo.
- **Next probe:** run `test_git_repo_isolation.py` alone in a loop under induced CPU pressure
  (`systemd-run --user --scope -p CPUQuota=25%`) rather than more full-suite runs — repeats of
  an intermittent are not a control.

### Tekton reported ~45 non-passing at `0c3d30d0`; never explained
- **Observed (with values):** `tekton/devrc-pytests` = `failure`, `FAILING: TestSkillDocsArePinned.test_the_pinned_docs_are_the_DEPLOYED_ones | TOTAL collected=18574 passed=18529`.
  Locally that test passes and the whole pinning file is 875 passed unfiltered.
- **Ruled out:** *a real defect in the change* — at `c6f64e4b` Tekton's numbers were
  **byte-identical** to the local sandbox tier (`18578/18576/2/0`), and both required checks
  passed through to the merge.
- **Leading hypothesis:** transient. The same check posted `error — COULD NOT RUN` twice the
  same day.
- **Next probe:** none open. 🔴 The failing run's log was **garbage-collected** before it could
  be read — Tekton retains ~14 pipelineruns and none was that sha. If it recurs, capture
  `kubectl -n tekton-ci logs <run>-gate` while the run still exists; the GitHub status is
  truncated at 140 chars and carries no `target_url`.

## Next steps (ranked)
1. **Land devrc#1146** — run the delta re-audit of `afe8e190..d42a353b` first; continue rounds
   only while a round produces a finding that needs fixing, and stop on the first clean one.
   Then merge and `claim-work --release devrc-1093-1115-scaffolding`.
   forcing: gate — `tekton/devrc-pytests` and `tekton/devrc-nodetests` are both REQUIRED with
   `enforce_admins: true`, and a Tekton status is not re-runnable: the `FORGED_actor` flake
   red-lit `afe8e190` and only a fresh push cleared it.
2. **Coordinate #1146 with #1144 before either merges** — #1144 (`a960d43a`) adds
   `EXIT_UNEVIDENCED = 10`, so the merged tree has 10 constants against #1146's
   `len(codes) == 9` and goes red by design. The byte-ceiling half of that warning is
   **RETRACTED**: 27,510 B was the CONFLICTED file. Union resolution is 25,861 B against a
   merged budget of 26,100 (#1144 raises `MAX_BYTES` to 27,000) — **+239 B, no eviction**.
   forcing: gate — whichever merges second fails a required check until the count is updated.
3. **Apply the staged dnsmasq fix** — `sudo ~/workspace/devrc/nix/system/apply-dnsmasq-docker-io-pin.sh`.
   Only the operator can run it.
   forcing: incident — measured 2026-08-29: the LAN router pins `registry-1.docker.io` with a
   487-day TTL and two of those IPs were reassigned to other AWS customers, so every
   `docker build` fails TLS. Worked around once with `--add-host`; unfixed.
4. **Fix `subsystem-audit.py`'s `EVICTABLE` classifier** — it verifies the target EXISTS and
   labels that "its content has a home". Measured on both of `devrc/tests.md`'s evictable
   bullets: one's commit carried none of the 3 later additions; the other's named sha carried
   **0 of 9** markers. 8 more so-labelled bullets store-wide.
   forcing: none
5. **Resume `claudedocs/handoff-subsystem-index-per-host.md` ranks 3, 5, 6, 7** — index-write
   protocol reconciliation; decide the store-api pod; decide replication; file the
   `FORGED_actor` flake — which is no longer hypothetical: it red-lit `afe8e190` and three
   other PRs in one window.
   forcing: none

## Gotchas / decisions / dead-ends
- 🔴 **`strict: false` means a green branch check is a claim about the BRANCH.** Eight audit
  rounds on #1046 could not see two defects that only appeared when `main` moved: #962 claimed
  `EXIT_DOC_PER_EFFORT = 7` against my `EXIT_STALE_BASE = 7` (the obvious "keep both sides"
  resolution ships two constants equal to 7 — the doc/code guard scrapes `status=` tokens and
  the exit-code pin stopped at 6, so **nothing** caught it); and #962's rule (i) **shadowed**
  the new refusal with **no conflict marker at all**, because both PRs appended returns to one
  `main()`. Gate the MERGED tree, by hand, every time.
- 🔴 **A fixture that is unrealistically empty is the blind spot.** Every test in the new class
  built a repo with NO other handoff docs, which is the only reason rule (i) never fired in
  them. Real repos always have others — talos-infra has 100+.
- 🔴 **The audit ladder's attribution gate is real and it fired.** Rounds 6 and 7 both changed
  ZERO payload lines ⇒ stop, file the rest. Rounds 1–8 produced 26 findings; rounds 3–5 each
  found the previous fix was right about one member of an equivalence class and wrong about
  another, including a **fail-open** hole (`bool(sections)`) invisible to 190 green tests.
- 🔴 **`nix build --rebuild` is not a re-run.** It verifies reproducibility of a SUCCESSFUL
  build and errors `outputs are not valid` after a failed one — a confident non-zero that reads
  exactly like a test failure. Cost one false "third failure".
- 🔴 **Build the two check derivations ONE AT A TIME** (main's #1088) — together they produce
  false failures.
- **An auditor's stop recommendation is not the stop condition.** Round 4's advised ending the
  ladder because its findings were not *caused by* the previous fix; the rule is a round with
  **no findings**. Round 5 then found a fail-open hole. Round 4 also filed the empty-mainline
  bug as prose-only; it was a payload defect.
- **`audit-dispatch.py --round N` derives `<from>` from the last posted claims block.** After a
  ladder closes and reopens (a merge), it generates a range spanning the whole merge — 52 of
  someone else's commits. Scope the dispatch by hand.
- **The bash guard resolves the repo from the session CWD, not `git -C`.** In a multi-worktree
  session it blocked a commit as "on main" when the target worktree was on a feature branch.
  `git commit -F <file>` is the documented way through, and is preferred over heredocs anyway.
- `SKILL.md` now has **2 bytes** of headroom against the enforced 25,500. The next addition
  needs an eviction and there is essentially nothing left to give.

- 🔴 **A handoff doc committed only to a feature branch in a WORKTREE is unfindable from the
  base clone, and `/resume` does not fail loudly on that.** This doc lives at
  `~/workspace/devrc-scaffold/claudedocs/…`, not `~/workspace/devrc/claudedocs/…`. Given the
  base-clone path, `resume-state.sh` reported NO SUCH FILE and **fell back to the newest of 90
  docs** (`handoff-mention-detection.md`) — it prints that as a `!` gap and withdraws the DRIFT
  all-clear, but the digest it produced was about a different initiative entirely. **Kickoff
  blocks for worktree-resident docs must name the WORKTREE path.**
- 🔴 **`claim-work` ownership is per GIT-DIR, so the same session gets rc 10 from its own
  worktree and rc 12 from its base clone.** This item was claimed 5h earlier from
  `/home/zach/workspace/devrc/.git`; checking from `devrc-scaffold` (whose git-dir is
  `…/devrc/.git/worktrees/devrc-scaffold`) returns **rc 10 DO NOT START**, which reads as a
  peer holding it. That is the strict sibling-worktree predicate working as designed. Resolve
  it by matching the ref's `owner-id:` trailer against `owner_id_for()` over
  `<common-dir>` + `<common-dir>/worktrees/*` rather than by guessing — the ref's `clone-id:`
  confirms same-clone-same-host first.
- ⚠ **The same item was claimed under a REWORDED slug**, so the canonical
  `claim-work --slug-for <doc> 1` came back FREE. The exact-slug match is the hard lock; the
  `--list` subject column is the only thing that catches this, and only if you read it.
- 🔴 **`nix build` writes the build log to STDERR, not stdout.** `pytests.out` was 0 bytes
  throughout a healthy run — that is not a stalled build.

- 🔴 **A guard's ORDER decides which defect it names, and the wrong order recreates the very
  bug you are fixing.** `assert len(codes) == 9` placed above an injectivity loop turned a
  constant COLLISION into `assert 10 == 9`, whose obvious remedy is to bump the literal —
  after which the real defect appears only on a second run. That is #1093.1 ("the assertion
  that runs first misdirects") reintroduced in a sibling test, in the same PR that fixed it.
  **When two assertions can both fire, the one that names the more specific defect goes first.**
- 🔴 **A prose guard that asserts a NUMBER without its SUBJECT is walkable.** `f"({const})"`
  asks only whether a digit appears somewhere in a 25 KB file, and any other status spelling
  the same number satisfies it. MEASURED: the exact drift the test existed to prevent left
  all 297 tests green. Bind the token to the value — `` `status=new-doc` (7) `` — not the value alone.
- 🔴 **`nix build` writes its log to STDERR.** A 0-byte `.out` during a healthy run is not a stall.
- 🔴 **zsh ate `$H:scripts/...` as a history modifier** (`:s`), so `git show "$H:path"` failed
  with `bad substitution` and one probe silently reported 0 constants. **Brace it: `${H}:path`.**
- 🔴 **A `cmp -s`/COULD-NOT-STAGE guard is what makes a broken control visible.** The round-4
  negative control's first regex matched ZERO times — the assertion message is an
  implicit-concat tuple whose last line carries a trailing comma. The harness reported
  `COULD NOT STAGE — the row above is UNVALIDATED` instead of passing, which is exactly
  #1115.1's original finding recurring in new code.
- 🔴 **Never grep a whole pytest run for a message.** pytest prints the failing test's SOURCE
  as traceback context, so a test whose COMMENTS quote the old message matches a naive grep
  and reports a misdirect that never happened. Read `^E ` lines only.
- ⚠ **A test-merge probe leaves rerere PREIMAGES.** Three were recorded by the #1144 probe;
  each has `preimage` and no `postimage`, so there is no stored resolution to replay. Worth
  checking after any probe merge, given this repo's recorded rerere incident.
- **The base clone `~/workspace/devrc` is SHARED and moves under you** — it was on
  `feat/memory-detail-click` at session start and on `main` hours later, switched by another
  session. It was 0 ahead of `origin/main` throughout, so `ship.sh` was never blocked.

## How to verify
```bash
# 1. #1046 landed by CONTENT (a squash is never an ancestor)
git -C ~/workspace/devrc show origin/main:scripts/lib/handoff_doc.py | grep -E "^EXIT_STALE_BASE = "

# 2. the branch under test IS the merged tree (must print `0<TAB>N`)
git -C ~/workspace/devrc-scaffold fetch origin main
git -C ~/workspace/devrc-scaffold rev-list --left-right --count origin/main...HEAD

# 3. BOTH sandbox tiers, ONE AT A TIME (#1088) — read each runner's own RESULT: line
nix build ~/workspace/devrc-scaffold#checks.x86_64-linux.pytests   --no-link --print-build-logs
nix build ~/workspace/devrc-scaffold#checks.x86_64-linux.nodetests --no-link --print-build-logs

# 4. every mutant #1093/#1115 recorded as surviving — now IN THE TREE, no scratch file.
#    Must run under the devShell: the harness calls bare `python3 -m pytest`, and
#    `.envrc` is `use opencode`, which does not provide it.
nix develop ~/workspace/devrc -c bash \
  ~/workspace/devrc-scaffold/scripts/tests/mutants-handoff-cap.sh   # 47 rows, 0 failures

# 5. SKILL.md is under its enforced ceiling (26,400 - 900 = 25,500)
wc -c ~/workspace/devrc-scaffold/claude/skills/handoff/SKILL.md     # 25,493

# 6. the store-api fix is RUNNING, not merely merged
KUBECONFIG=$KC_HOMELAB kubectl -n subsystem-store exec \
  $(KUBECONFIG=$KC_HOMELAB kubectl -n subsystem-store get pod -l app=subsystem-store-api \
    -o jsonpath='{.items[0].metadata.name}') -- \
  grep -c _audit_lock /app/scripts/subsystem-store-api/server.py   # must be 3
```
