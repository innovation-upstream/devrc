# Handoff: clawgate-resolve-and-ci-preemption — 2026-08-25

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

🔴 **SIBLING DOC — read it too, and do not merge the two.** `claudedocs/handoff-ci-flakes-and-misattribution.md`
covers the *test-isolation* flake family (#783 contamination, the `test_browser_agent.py:558`
family, the `rescue/initiative-scan-resolved-filter` fate). It was being updated by a
CONCURRENT session as this doc was written — **devrc#826, open** — so this session
deliberately did NOT update it: a `Next steps` REPLACE would have destroyed their decided
item 1. This doc carries what that one does not.

## Goal
Make `/handoff`'s clawgate association record the task a session actually WORKED, and stop
`devrc-ci` gate runs being killed so a required merge gate can be trusted.

## State now
- This doc landed as devrc**#831**, squash `3f3494ff`. `origin/main` has since moved on.
- 🔴 **THE HOSTS ARE NOT CONVERGED — the earlier "both hosts converged at `33fa7c00`" is
  SUPERSEDED.** `ship.sh` after #831: **laptop ff'd to `3f3494ff`, switched, verified**
  (495 managed artifacts / 0 dangling / 0 stale, clean tree). **Workbench SKIPPED, rc=7
  `cannot-fast-forward(local changes in the way)`** and was left exactly as found.
  `ship: cross-host agreement NOT COMPARED — 1 of 2 hosts reported a landed sha.`
- **The workbench is therefore behind and silently receiving nothing further** — the exact
  shape `CLAUDE.md` warns about, where a skipped host keeps looking healthy.
  `scripts/drift-check.sh` is the passive detector if it stays this way.
- Everything else in the table above is unchanged and still true: 13 PRs merged, both Tekton
  tiers required, preemption stopped, `#802` live in the running browser-bridge process.

## Open investigations — live diagnosis state

### Still open — homelab-infra #351, a `finally` reporter that strands a check permanently
- **Symptom:** a `finally` reporter finished ~4 min BEFORE its main task, posted `Failed` for
  a task that then succeeded, and was overwritten by a late `pending`.
- **Why it matters:** a stranded `pending` on a required context is unclearable — no re-run
  fixes it, only a fresh push — and `enforce_admins: true` means no admin override.
  The task-level timeout fix (2026-08-24) does **not** cover this path.
- **Observed:** report-pod health is otherwise good — 0 of 560 finished PipelineRuns lack a
  report TaskRun, 555/560 succeed, p50 7s, 0 over 300s. 3 checks stranded, all on already
  closed PRs, **0 blocking**. Real but rare.
- **Ruled out:** capacity on the new node (`uvh-gtj` 28% CPU requests / 47% usage — the
  earlier "86%/77%" reading was INVERTED); `#396` relocating the problem (its latency
  "regression" sat entirely inside a one-time cold-cache seed burst; at n=64 the bootstrap
  CIs contain each other's point estimates).
- **Next probe:** query TaskRun history for a second instance — reporter `completionTime`
  earlier than its main task's — BEFORE designing a fix. One observation is not a mechanism.

### `core.hooksPath` writer — still unidentified, watcher armed
- **Symptom:** a repo-local `core.hooksPath = <repo>/githooks` appears and disappears in the
  devrc checkout. Observed set/unset at least 5 times in one session. A pre-push gate that
  runs the suite IN the pushing worktree has previously come back with the branch REWRITTEN
  and `autocommit:` fixture commits on it (task #322).
- **Ruled out as writers:** `githooks/install.sh` — writes `--global` (verified, lines 21-39);
  the `security-guidance` plugin's `gitutil.py` — uses `-c core.hooksPath=/dev/null`, a
  per-invocation override that never touches `.git/config`. `test_git_repo_isolation.py:1197`
  DOES do a scope-less `git config core.hooksPath /tmp/elsewhere` (a local write, right
  shape) but the observed VALUE was `<repo>/githooks`, which no code path sets locally.
- **Instrument:** `hookspath-watch.service` (systemd --user, `Restart=always`, hand-placed in
  `~/.config/systemd/user/`, deliberately NOT home-manager so it is easy to delete). Log
  `~/.cache/hookspath-watch/events.log`. **Validated by positive control** on a scratch repo
  before arming. 🔴 **0 catches so far.** Delete the unit once it catches one.
- **Next probe:** read the log after the next occurrence — it records the transition plus a
  timestamped `ps` snapshot filtered to git/pre-push/pytest/claude.

### 🔴 BLOCKED — the workbench cannot fast-forward, and the blocker is ANOTHER session's live WIP
- **Symptom + exact repro:** `bash scripts/ship.sh` → workbench `rc=7`, laptop fine.
  Reproduce directly with `git -C ~/workspace/devrc merge --ff-only origin/main`.
- **Observed (with values):** two independent blockers, both in the workbench checkout.
  ```
  error: Your local changes to the following files would be overwritten by merge:
      scripts/run-node-tests.sh
  error: The following untracked working tree files would be overwritten by merge:
      scripts/discord-embed-ext/extension/embed_enlarge.js   (+ 8 more)
  ```
  - **All 9** `scripts/discord-embed-ext/**` files present locally **DIFFER** from the copies
    now on `origin/main` (`identical=0 differs=9`, compared with
    `git show origin/main:<f> | cmp - <f>` per file). They are **not** redundant leftovers of
    the landed rescue PR — they are a live working copy.
  - `scripts/run-node-tests.sh` local vs `origin/main`, one line, and it is COHERENT with the
    above: local `"scripts/discord-embed-ext/tests|3|50"` vs upstream
    `"scripts/discord-embed-ext/tests|2|113"` — i.e. someone is mid-edit on that suite (3 test
    files, floor 50) and moved the floor to match.
- **Ruled out:** *stale leftovers safe to delete* — refuted by the 9-way content compare above.
  *`git stash`* — banned repo-wide (`refs/stash` is in the common git dir; a concurrent
  session can pop it). *Committing them* — not this session's work, and devrc forbids
  committing to `main` in either host checkout.
- **Leading hypothesis:** an active session is mid-work on `discord-embed-ext` in the
  workbench checkout, after its first tranche landed (devrc#818).
- **Next probe:** identify the owner before touching anything —
  `git -C ~/workspace/devrc log --oneline -3 -- scripts/discord-embed-ext` and
  `gh pr list --repo innovation-upstream/devrc --state open --search discord-embed-ext`.
  Then: they commit or move it, and `ship.sh` again. 🔴 Do **not** delete or stash those files
  to unblock the ship — that is nine files of someone's unsaved work.

## Next steps (ranked)
1. 🔴 **Unblock the workbench** (devrc, workbench checkout only — no files in `main`).
   Owner-gated: someone must commit or relocate `scripts/discord-embed-ext/**` and the
   `scripts/run-node-tests.sh` line, then re-run `bash scripts/ship.sh`. Until then that host
   receives nothing and `drift-check.sh` is the only signal. Cheapest item, and the one whose
   silence is most misleading.
2. **homelab-infra #351** — the `finally` reporter that posted a verdict ~4 min before its main
   task finished and stranded a required check `pending`. Only remaining path that can
   permanently block a merge. Start with the TaskRun-history query, not a fix.
3. **Re-measure in 3–5 days; do not claim victory yet.** Baseline: 20 pass / 6 real failures /
   3 kills of 29 completed runs (~10% kill rate). Honest tests: does `pytests=255` stay
   absent, and does `test_the_throttle_path…` stop appearing.
4. **Decide the five conflicting PRs** — devrc `#646 #612 #480 #359 #355`.
5. **`clawgate-ci` requests ~2.4× inflated** — latent, has a **sidecar** so max-of-steps is
   unsound there. Do not spend without a symptom.
6. **`created` is TERMINAL upstream**, so file-then-work in one session lands in `resolve`'s
   no-worked branch. Recorded, not fixed.

🔴 **This list is a WORK QUEUE WITH NO LOCK.** Item 1 in particular is owned by whoever holds
that WIP — do not "helpfully" clear it. **Measured this session:** three concurrent sessions
touched files this one did (homelab-infra#401 re-tuned `gitops-validate` after #389; devrc#826
was open on the sibling handoff doc; and the `discord-embed-ext` WIP above). Check `git log`
for the file and `gh pr list` before starting anything here.

## Gotchas / decisions / dead-ends
- 🔴 **The auto-loaded `CLAUDE.md` is a session-start SNAPSHOT.** It caught this session
  twice — once directly (I reported the merge-gate paragraph as stale when it had already
  been fixed on `main`), once via a subagent making the identical error hours later.
  `git show origin/main:CLAUDE.md` before asserting anything about it.
- 🔴 **`ci-bulk` carries `preemptionPolicy: Never`, so "devrc preempted itself" is
  MECHANICALLY IMPOSSIBLE.** Asserted twice before anyone read the policy. Do not re-derive.
- 🔴 **Two locally-correct fixes that were NOT causal:** #389 and #399 removed genuine
  over-reservation but neither was why devrc died. `auditloop` overlapped a devrc gate for
  **11 minutes in 30 days** and was not running at the measured kill. Do not cite either as
  the preemption fix — #400 (the priority class) was.
- **`ERROR` ≠ `FAILURE` on a Tekton check.** `ERROR` means the gate never ran (preemption);
  re-running is recovery. `FAILURE` is a verdict; re-running that is laundering.
- **A green run is a claim about the tier that ran it.** A local `gate.sh --tier pytest`
  passed 15462/0 while CI failed — CI collects ~191 MORE tests.
- **A PR head read immediately after `gh pr update-branch` is unreliable** — seen twice, once
  appearing to lack a fix it actually contained. Re-measure after propagation.
- **Instruments that reported success while testing nothing, all found this session:** a
  drain loop whose timeout was indistinguishable from success; a coverage checker scoring a
  good and a deliberately-bad proposal identically (awk field-split bug); a mutation sweep
  scoring a *working* guard as SURVIVED (`-ne 99` on a 0/1 flag fires always, not never);
  `kubectl apply --dry-run=server` on a TriggerTemplate accepting a bogus field
  (`x-kubernetes-preserve-unknown-fields`). Build the negative control first.
- **A comment quoting a p50 as a max:** `devrc-ci-pipeline.yaml` claimed 2Gi was "2.4× a
  measured 823Mi peak" — 823Mi was the p50, real max 1520Mi, i.e. 1.35×. Fixed in #402.

- 🔴 **A `--repo` whose working tree LACKS the doc makes `handoff_doc.py` treat an UPDATE as a
  NEW doc.** Hit this immediately: `#831` merged to `origin/main`, but this checkout could not
  fast-forward (item 1), so `clawgate_handoff.sh field <doc>` returned **66 cannot read** and
  the merge tool would have written a duplicate over the merged copy. **Run step 5 against a
  worktree off `origin/main` when the base checkout is blocked** — the base comes from the
  working tree, and an ABSENT base is the severe form of the stale-base warning.
- 🔴 **`bash …/clawgate_handoff.sh resolve | tail -3; echo rc=$?` reports TAIL's status.**
  Printed `rc=0` for a run that actually exited **6**. Redirect to a file and read `$?` from
  the unpiped command — the same exit-code-through-a-pipe defect this session found in
  `gate.sh` and in the CI verdict step.
- **`ship.sh` rc=7 is a SKIP, not a failure** — the host is left exactly as found, which is
  correct behaviour and why the per-host lines must be read rather than the final verdict.

## How to verify
```bash
# 1. the clawgate resolve change, end-to-end on the DEPLOYED copy
bash ~/workspace/devrc/scripts/lib/clawgate_handoff.sh resolve
#    a session with one `read` link must print "NONE of them WORKED" and exit 6 —
#    the old count-based rule would have RECORDED that task into the handoff.

# 2. preemption is gone (the fix that mattered)
KUBECONFIG=$KC_HOMELAB kubectl -n tekton-ci get events --field-selector reason=Preempted
KUBECONFIG=$KC_HOMELAB kubectl get taskruns -n tekton-ci -o json | jq -r '
  [.items[]|select(.metadata.name|test("devrc-ci.*-gate$"))
   |{t:.metadata.creationTimestamp,s:([.status.steps[]?|"\(.name)=\(.terminated.exitCode // "-")"]|join(" "))}]
  | sort_by(.t) | .[-8:][] | "\(.t[11:19]) \(.s)"'
#    expect no 255 anywhere. 255 = SIGKILLed = preempted, NOT a test failure.

# 3. devrc gate pods run at the default priority
KUBECONFIG=$KC_HOMELAB kubectl get pods -n tekton-ci -o json | jq -r '
  .items[]|select(.metadata.name|test("devrc-ci.*gate-pod"))|"\(.metadata.name) prio=\(.spec.priority)"'
#    expect prio=0, never -10000.

# 4. the emitter fix is live in the RUNNING process, not merely deployed
grep -c '_spool_emit_lock' "$(readlink -f ~/.config/browser-bridge/server.py)"   # expect 3
systemctl --user show browser-bridge -p MainPID -p SubState --value
#    the hm switch restarts the unit; a MainPID older than the generation means stale code.
```
