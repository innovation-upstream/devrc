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
- **Rank 1 COMMITTED AND PUSHED — no PR yet.** In `~/workspace/devrc-scaffold` on
  `fix/audit-ladder-scaffolding-gaps`: `b1263772` *test(handoff_doc): close the audit ladder's
  filed scaffolding gaps (#1093, #1115)* — the three files the last session left uncommitted
  (`claude/skills/handoff/SKILL.md`, `scripts/tests/mutants-handoff-cap.sh`,
  `scripts/tests/test_handoff_doc.py`; +115/−9). The branch was **23 behind** `origin/main`,
  not the 9 this doc previously recorded; it is **rebased onto `093a63db` and force-pushed**,
  so the tree under test IS the merged tree. `SKILL.md` = **25,498 B** against the enforced
  25,500 (2 B headroom, as predicted).
- 🔴 **Nothing on those 23 commits touches `handoff_doc.py`, the handoff `SKILL.md`, or either
  test file** — measured with `git log HEAD..origin/main -- <the four paths>`, empty. So the
  #1046/#962 shape (two PRs appending to one `main()`, no conflict marker) does NOT recur here.
- **IN FLIGHT — the gate.** `nix build …#checks.x86_64-linux.pytests` was running at handoff
  time; `nodetests` NOT started (they must run **one at a time**, #1088). Logs:
  `<scratchpad>/pytests.{out,err}`.
- **IN FLIGHT — the mutation battery, written but NOT run.**
  `<scratchpad>/battery.sh` re-runs every mutant #1093 and #1115 record as surviving, because
  #1115's closing condition requires the PR body to record each result. Rows: the three
  exit-code renumbers, one EXIT_ constant silently vanishing, rule (i) broadened to
  `not currency.stale`, `doc_shape(shown.out)` → `doc_shape("")`, and the round-4
  `replaces_mainline_doc` regression asserted to fail with the CORRECT message (#1093.1).
  Carries a green baseline, a positive control, a must-SURVIVE reword control,
  `PYTHONDONTWRITEBYTECODE=1` and a `cmp -s` did-it-apply guard on every row.
- **No duplicate in flight** — swept 32 open devrc PRs; none touches any of the three files.
- Also merged in the prior session: **ZacxDev/homelab-infra#460** (squash `26d98f1b`) and
  **#519** (subsystem-store-api `0.5.0` → `0.6.0`).
- **subsystem-store-api is DEPLOYED and VERIFIED**: pod `…-97gnp`, image `0.6.0`,
  `_audit_lock` count **3** (was 0), `server.py` 226,998 B (was 222,147), `/healthz` 200,
  0 restarts. devrc#996's fix is live.
- **Claim:** this item is held as `devrc-1093-1115-scaffolding` (a reworded slug — the
  canonical `handoff-doc-stale-base-guard-1` is still free and was deliberately NOT minted, to
  avoid two refs for one item). Release it when the PR merges.

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
1. **Finish the gate and open the PR** for `fix/audit-ladder-scaffolding-gaps` (devrc).
   Remaining: read the `pytests` tier's own `RESULT:` line (never a piped exit code), then run
   `nodetests` **separately**, then `bash <scratchpad>/battery.sh`, then re-sweep
   `gh pr list` and `gh pr create`. The PR body must record **each** battery row — that is
   #1115's stated closing condition, not a nicety.
   forcing: gate — both required checks (`tekton/devrc-pytests`, `tekton/devrc-nodetests`)
   block the merge, and `strict: false` means a green branch check is a claim about the branch.
2. **Apply the staged dnsmasq fix** — `sudo ~/workspace/devrc/nix/system/apply-dnsmasq-docker-io-pin.sh`.
   Only the operator can run it.
   forcing: incident — measured 2026-08-29: the LAN router pins `registry-1.docker.io` with a
   487-day TTL and two of those IPs were reassigned to other AWS customers, so every
   `docker build` fails TLS. Worked around once with `--add-host`; unfixed.
3. **Fix `subsystem-audit.py`'s `EVICTABLE` classifier** — it verifies the target EXISTS and
   labels that "its content has a home". Measured on both of `devrc/tests.md`'s evictable
   bullets: one's commit carried none of the 3 later additions; the other's named sha carried
   **0 of 9** markers. 8 more so-labelled bullets store-wide.
   forcing: none
4. **Resume `claudedocs/handoff-subsystem-index-per-host.md` ranks 3, 5, 6, 7** — index-write
   protocol reconciliation; decide the store-api pod; decide replication; file the
   `FORGED_actor` flake. Rank 4 (store hygiene) is partly done: `devrc/tests.md` 40,166 →
   39,061 B.
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

## How to verify
```bash
# 1. #1046 landed by CONTENT (a squash is never an ancestor)
git -C ~/workspace/devrc show origin/main:scripts/lib/handoff_doc.py | grep -E "^EXIT_STALE_BASE = "

# 2. the branch under test IS the merged tree (must print `0<TAB>2`)
git -C ~/workspace/devrc-scaffold fetch origin main
git -C ~/workspace/devrc-scaffold rev-list --left-right --count origin/main...HEAD

# 3. BOTH sandbox tiers, ONE AT A TIME (#1088) — read each runner's own RESULT: line
nix build ~/workspace/devrc-scaffold#checks.x86_64-linux.pytests   --no-link --print-build-logs
nix build ~/workspace/devrc-scaffold#checks.x86_64-linux.nodetests --no-link --print-build-logs

# 4. the four #1115 mutants + the two #1093 ones stop surviving
bash <scratchpad>/battery.sh          # ends with BATTERY_RESULT: PASS
bash ~/workspace/devrc-scaffold/scripts/tests/mutants-handoff-cap.sh   # rule-(i) rows: killed, not DID NOT APPLY

# 5. the store-api fix is RUNNING, not merely merged
KUBECONFIG=$KC_HOMELAB kubectl -n subsystem-store exec \
  $(KUBECONFIG=$KC_HOMELAB kubectl -n subsystem-store get pod -l app=subsystem-store-api \
    -o jsonpath='{.items[0].metadata.name}') -- \
  grep -c _audit_lock /app/scripts/subsystem-store-api/server.py   # must be 3
```
