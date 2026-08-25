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
- devrc `main`, both hosts converged at `33fa7c00` (`ship.sh` rc=0; workbench 527 managed
  artifacts / 0 dangling / 0 stale, laptop 495 / 0 / 0). **Nothing in flight.**
- ⚠ Workbench prints `NOTE: tree is DIRTY` — another session's uncommitted
  `scripts/run-node-tests.sh` line plus `discord-embed-ext/`, `load_test_store.sh`.
  Deployed artifact ≠ commit on that host. Not ours to commit.
- **13 PRs merged, across two repos.**

| PR | squash | what |
|---|---|---|
| devrc #738 | `a662b48b` | `/handoff`'s clawgate `resolve` ranks links by `role` instead of counting them |
| devrc #751 | `db4fb3f4` | its seam ledger was *spelled*, not structural — a reworded re-read walked past it |
| devrc #758 | `c0cec452` | CLAUDE.md gained the coverage *mechanism*; fixed a false claim inside the drift-detector's own docstring |
| devrc #771 | `8f33523c` | five files still asserting a dead branch-protection state |
| devrc #796 | `2f6928df` | `skills_mapping`'s `nix-instantiate` 60s budget blew under CI contention |
| devrc #802 | `d09038d8` | 🔴 **production race**: browser-bridge silently DROPPED telemetry rows |
| devrc #491 #667 #658 | `c42d2b48` `324693fd` `33fa7c00` | stale handoff PRs, unblocked once the gate stopped killing runs |
| homelab-infra #389 | `0817bf6a` | `gitops-validate` reserved 4.65 CPU for a 1 CPU peak |
| homelab-infra #399 | `a7b04f80` | `auditloop` 5.50 → 2.85 CPU / 9728 → 6656Mi |
| homelab-infra #400 | `2acf9f4d` | 🔴 **the actual preemption cause** — removed `priorityClassName: ci-bulk` |
| homelab-infra #402 | `a580f38c` | closed the `nodetests` request IOU; corrected a p50-quoted-as-max comment |

- **Branch protection changed mid-session**, now correct in CLAUDE.md:
  `contexts = ["tekton/devrc-nodetests","tekton/devrc-pytests"]`, `strict: false`,
  `enforce_admins: true`. Both tiers required.
- **Preemption stopped.** 0 `pytests=255` across the last 8 gate runs; devrc#658 — killed
  twice on an unchanged tree — passed **first try** after #400.
- **Deployed and verified live**, not merely merged: `resolve` driven end-to-end on the
  deployed copy; `_spool_emit_lock` present in the running browser-bridge process.

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

## Next steps (ranked)
1. **homelab-infra #351** — the reporter/main-task ordering strand. Only remaining item that
   can permanently block a merge. Files: `clusters/homelab/apps/tekton-pipelines/triggers/`.
   Start with the history query above, not a fix.
2. **Re-measure in 3–5 days; do not claim victory yet.** Baseline recorded: 20 pass / 6 real
   failures / 3 kills of 29 completed runs (~10% kill rate). Honest tests: does `pytests=255`
   stay absent, and does `test_the_throttle_path…` stop appearing. #402's own "after" window
   is only n=30.
3. **Decide the five conflicting PRs** — devrc `#646 #612 #480 #359 #355`, 98–384 commits
   behind, all `CONFLICTING`; `#355` is `DO-NOT-MERGE-YET`. Several are handoff docs whose
   value is the prose, so cherry-picking text may beat rebasing.
4. **`clawgate-ci` requests remain ~2.4× inflated** (2.35 CPU / 3584Mi) — latent, and it has a
   **sidecar**, so the max-of-steps reasoning used for `auditloop`/`gitops-validate` is
   UNSOUND there. `auditloop` was the same shape and proved non-causal; do not spend on this
   without a symptom.
5. **`created` is TERMINAL upstream** (clawgate `taskstatus`), so a session that FILES a task
   and then works it stays `created` and lands in `resolve`'s no-worked branch. Recorded, not
   fixed. The fix, if it ever bites, is to rank `created`+`worked` co-occurrence — not to go
   back to counting links.

🔴 **This list is a WORK QUEUE WITH NO LOCK** — every `/resume` draws from it, so a *better*
list produces *more* duplicate work. Nothing above is in flight as of this writing. If you
start one, mark it `IN FLIGHT: <repo>#<pr>` here. **Measured this session:** two other
sessions worked the same files concurrently — homelab-infra#401 re-tuned `gitops-validate`
hours after this session's #389, and devrc#826 was updating the sibling handoff doc. Check
`git log` for the file and `gh pr list --search <path>` before starting.

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
