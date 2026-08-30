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
- **IN FLIGHT — uncommitted, in `~/workspace/devrc-scaffold` on `fix/audit-ladder-scaffolding-gaps`**
  (branched off `main`, now 9 behind). Three files modified, **not committed, not pushed, no PR**:
  `claude/skills/handoff/SKILL.md`, `scripts/tests/mutants-handoff-cap.sh`,
  `scripts/tests/test_handoff_doc.py`. This closes issues **#1093** and **#1115**. 1,181 tests
  pass locally; both sandbox tiers NOT yet run on it.
- Also merged this session: **ZacxDev/homelab-infra#460** (squash `26d98f1b`) and
  **#519** (squash — subsystem-store-api `0.5.0` → `0.6.0`).
- **subsystem-store-api is DEPLOYED and VERIFIED**: pod `…-97gnp`, image `0.6.0`,
  `_audit_lock` count **3** (was 0), `server.py` 226,998 B (was 222,147), `/healthz` 200,
  0 restarts. devrc#996's fix is live.

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
1. **Commit + PR the uncommitted work in `~/workspace/devrc-scaffold`**, closing #1093 and
   #1115. Run BOTH sandbox tiers first (`nix build <wt>#checks.x86_64-linux.{pytests,nodetests}`,
   **one at a time** per #1088). Files: the three above.
   forcing: none
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

## How to verify
```bash
# 1. #1046 landed by CONTENT (a squash is never an ancestor)
git -C ~/workspace/devrc show origin/main:scripts/lib/handoff_doc.py | grep -E "^EXIT_STALE_BASE = "

# 2. the guard refuses in a REALISTIC repo (the shape that was shadowed)
nix develop ~/workspace/devrc -c python3 -m pytest \
  ~/workspace/devrc/scripts/tests/test_handoff_doc.py -q -p no:cacheprovider \
  -k "REALISTIC_repo or WHICH_predicate or share_a_value"

# 3. the store-api fix is RUNNING, not merely merged
KUBECONFIG=$KC_HOMELAB kubectl -n subsystem-store exec \
  $(KUBECONFIG=$KC_HOMELAB kubectl -n subsystem-store get pod -l app=subsystem-store-api \
    -o jsonpath='{.items[0].metadata.name}') -- \
  grep -c _audit_lock /app/scripts/subsystem-store-api/server.py   # must be 3
```
