# Handoff: repo-cos precision iteration — 3-week arc (structural roots fixed) — 2026-07-27

Supersedes `handoff-repo-cos-precision-iteration-2026-07-13.md`. Tracks the "does the weekly digest earn its place / iterate on precision" thread. Operate via the `repo-cos` skill.

## The arc: each week's noise pointed at a real instrument defect (not just cleared)
| Week | Digest result | Root cause found | Fix (all merged + workbench-deployed) |
|------|---------------|------------------|----------------------------------------|
| **07-13** | 0/5 actionable | 3 false-positive classes: rg/walk `.md` divergence, quoted-string-literal markers, JS conditional-skips | PR #106 (`.md` align + `_has_unquoted_marker`), PR #107 (`_is_conditional_js_skip`) |
| **07-20** | 1 actionable (+1 real-but-yours) — **verifier PASSED** | none new; the 1 actionable was a REAL coverage gap | PR #178 (homelab-talos: Postgres service in clawgate-ci.yml `go` job + `CLAWGATE_TEST_DATABASE_URL`) — **enabling the test uncovered + fixed a latent FK bug** |
| **07-27** | 0/4 actionable | two STRUCTURAL roots: (1) stale local checkouts, (2) Go/Python conditional-skips not suppressed | PR #170 (fetch-before-scan), PR #171 (`_is_conditional_go_skip`/`_is_conditional_py_skip`) |

## State now (all on `main`, workbench deployed + verified; laptop picks up on its next ship)
- **devrc HEAD `fbf5358`** (`ship.sh --no-laptop` after each merge; both PRs' helpers confirmed live in the checkout the weekly timer runs from).
- **PR #170 — fetch-before-scan (the trust fix):** prescan now `git fetch`es each repo and scans `origin/<default-branch>` in a **throwaway detached worktree** (system temp, cleaned in `finally`), NOT the drifted/dirty working copy. Working tree provably untouched (no pull/checkout/reset). Real repo basename preserved in evidence refs (`resolve_scan_root`/`scan_repo_fresh`, `repo_name` override). `stale_lock` deliberately reads the ORIGINAL working tree (real mtimes — a fresh checkout stamps mtime=now). Robust fallback to working-tree scan (no remote/offline/`--no-fetch`) + per-repo mode logged. 245 tests.
  - **Why it mattered:** `~/workspace/homelab-talos` was **181 commits behind `origin/trunk`** and missing merged PR #178 → the digest re-proposed already-done work (07-27 #2) and could cite stale line numbers. This is why 07-27 #2 recurred despite being fixed in wk2.
- **PR #171 — Go/Python conditional-skip suppression:** generalized #107's JS conditional-vs-disabled logic. `_enclosing_opener(lines, idx)` (nearest strictly-shallower preceding line) + drop `t.Skip`/`t.Skipf`/`t.SkipNow` inside `if …{`/`} else if`/`} else` and `pytest.skip(` under `if`/`elif`/`else:`. KEEPS bare body skips, `@pytest.mark.skip`, `allow_module_level=True`. Conservative (only if-family openers count; `for`/`with`/`func`/`def` nesting stays flagged). 257 tests. Verified: the real `pgstore_test.go` now yields **0 `go.skip` candidates** (all 6 `t.Skip` classify conditional).
- **`~/.config/repo-cos/exclusions.json` — 43 dismissals.** 07-27 added 17 (#1 devrc no-CI FP, #2 done-via-#178 with forward-looking `origin/trunk` line refs 23/94/166, #3+#4 civitai out-of-lane).

## The accepted tradeoff (state it honestly next week)
Conditional-skip suppression (#107 + #171) is a **recall/precision tradeoff**: prescan can't tell "real gap" (CI lacks the dep) from "already handled" (CI provides it) by reading the test file alone. **Wk2's #178 win — a real FK bug found by enabling a conditional-skip — would NOT surface under the new suppression.** That was a conscious call (Zach, 07-27): the FP is a *weekly* tax forever; the real-gap catch was a *one-time* win already banked. If a real conditional-skip CI gap ever needs catching, it won't come from repo-cos now — flag it another way.

## Next step — the verifier is 2026-08-03
Next Monday's digest tests BOTH 07-27 fixes at once:
1. **#170:** homelab-talos should scan `origin/trunk` (fresh) — no more re-proposing merged work, no stale line refs. Confirm the run log shows `fresh-ref (origin/<branch>)` per repo.
2. **#171:** the clawgate `pgstore` + devrc optional-dep conditional skips should NOT reappear.
If clean → the instrument has been meaningfully de-noised over 3 weeks and the marker/skip signals are trustworthy. If STILL mostly noise → the deeper move (deferred twice now, deliberately) is **down-weighting the string-match signals** (`marker`/`skipped_test`) relative to `churn`/`large_file`/`stale_lock`. Don't do it preemptively.

## Loose ends (not acted on, low priority)
- 07-20 #4 Tekton placeholder secrets (`github-app.enc.yaml`) — a REAL `TODO` blocked on **Zach** creating the org-level GitHub App. Not agent-dispatchable; a genuine reminder, left un-dismissed on purpose.
- `kubeclaw` exclusion gap: bare `~/workspace/kubeclaw` is a THIRD kubeclaw repo in `DEFAULT_REPOS`, distinct from the paused `kubeclaw-cloud`/`kubeclaw-embed`. If the whole family is paused, exclude bare `kubeclaw` too (surfaced 07-20 #5, not yet excluded).

## How to verify
```bash
cd ~/workspace/devrc && git log --oneline -3   # expect fbf5358 (#171), 359e0c1 (#170)
grep -n "_is_conditional_go_skip\|resolve_scan_root\|scan_repo_fresh" scripts/repo-cos/prescan.py
nix-shell -p 'python3.withPackages(p:[p.pytest p.requests p.psycopg2])' --run 'python -m pytest scripts/repo-cos/tests -q'   # 257
python scripts/repo-cos/scan.py --show-exclusions   # 43 dismissed
# prove fresh-ref scanning + untouched tree:
nix-shell -p 'python3.withPackages(p:[p.requests])' --run 'python scripts/repo-cos/scan.py --no-llm --repos "$HOME/workspace/homelab-talos" 2>&1 | grep -i "fresh-ref\|fallback"'
git -C ~/workspace/homelab-talos worktree list   # no leftover /tmp/repo-cos-wt-*
```
