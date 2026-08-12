# Handoff: claude-config-modernization — 2026-08-11

## Goal
Bring the global Claude Code config (RULES.md, skills, commands, hooks, settings) in line with
current upstream mechanics and 2026 evidence, then ship it to both hosts. Secondary and larger
in impact: the audit exposed a live deploy bug that had been silently breaking the laptop.

## State now
- **Branch:** `main` @ `d4429e6`, clean except 4 pre-existing untracked items
  (`.envrc`, `.opencode/`, `claudedocs/proposed-rules-cut/`, `nix/system/apply-nebula-443.sh.LOCAL-preserved-2026-08-02`).
- **The commit-to-main guard's fail-open is CLOSED, deployed, and verified at the
  consumer on both hosts** (2026-08-11 20:4xZ). See "The guard fail-open" below —
  that investigation is finished, not open.
- **Deploy: DONE and verified at the consumer**, not just merged. `scripts/ship.sh` reports
  `[workbench] 341 checked, 0 dangling, 0 absent` / `[laptop] 304 checked, 0 dangling, 0 absent`,
  both `✅ VERIFIED … + switched`. Independently confirmed: `~/.claude/commands/` gone on both
  hosts, 33 skill dirs resolving, `~/.claude/skills/close-the-loop/STATE.md` writable,
  `guard_core.py` carries `check_git_commit_to_main` on both.
- **Gate green on main**, read from content: `collected=7434 passed=7433 skipped=1 failed=0
  (floor: 6966 = sum of 17 per-target floors)`; nodetests `1024/1024`. 0 timeout panics.
  Measured on `d4429e6` — i.e. on the tree that has #395 + #396 + #398 together, not on any
  single PR branch.
- **`drift-check.sh` rc=15**, unchanged and expected: both hosts clean, on `main`, at
  `d4429e66`, `dangling=0` (workbench 156 examined, laptop 151). The only drift is the
  `settings.json` key-set parity item still open below.

### Merged this session (11)
| PR | sha | What |
|---|---|---|
| #376 | `e0cf5d2` | blocking guards: commit-to-main (trunk allowlist), `pkill -f` |
| #377 | `60e80c7` | all 17 commands → skills; listing budget −21%; close-the-loop write path |
| #380 | `db4a324` | regression gate for junk accreting into `permissions.allow` |
| #382 | `9f17cfc` | 7 rules retired, measured by ablation |
| #383 | `77d33df` | SuperClaude agent archive committed (was the only copy, untracked) |
| #384 | `58dd9e2` | **removed the `ship.sh` rsync** + post-switch `verify_managed_artifacts` |
| #391 | `52fd995` | `drift-check.sh`: git parity ≠ host parity (rc14 dangling, rc15 divergence) |
| #392 | `f380936` | dev-host tier repaired (Bun 8192B pipe truncation + `logrotate` PATH) |
| #397 | `e1219d4` | truthful gate exit status + per-target floors replacing the literal |
| #396 | `3a5e32c` | guard: judge the tree the command acts on; heredoc body no longer blinds the scanner |
| #395 | `d4429e6` | guard: a `-C` target that is not a repo must not suppress the commit-to-main check |

(#398 `f6ef634`, collector changed-paths, also landed on `main` mid-session from another
session. It is not part of this initiative, but it IS in the tree the gate numbers above
were measured on.)

### Host-level changes (NOT in git — `~/.claude/` is per-host)
- `settings.json` `permissions.allow` **248 → 210** on workbench (32 YAML fragments, a heredoc
  body, `Bash(EOF)`, 3 credential-bearing entries, stale `SlashCommand(/sc:research …)`).
  Laptop had **no `permissions` key at all** — genuine divergence, nothing removed there.
- `~/.claude/agents/` (17 SuperClaude files) **deleted on both hosts**; archived to
  `claudedocs/superclaude-agents-removed-2026-08-10/` and committed in #383.
- `pyright-lsp` installed + enabled on the laptop; plugins now AGREE across hosts.
- Backups left in place: `~/.claude/settings.json.pre-cleanup` (workbench),
  `~/.claude/settings.json.bak.1786422732` + `*.bak-pyright-20260811-001956` (laptop).

## CLOSED: the commit-to-main guard fail-open (#395 + #396)

Both PRs landed and are deployed. Recording it here because the *shape* of the defect and of
the near-miss is the reusable part.

- **The fail-open, measured — three cases, not one.** Built an independent 13-case probe
  (deliberately NOT the PRs' own matrices) with real `git init` fixtures **carrying remotes**,
  because `_commit_to_main_reason` fails open on a repo with no remotes and a fixture without
  one makes the entire matrix vacuously green. `origin/main` and the then-deployed hook both
  scored **10/13**, failing the same three, all with cwd on a blocked branch:
  ```
  ALLOW  git -C <an-ordinary-directory> commit -m x                     (cwd on trunk)
  ALLOW  git -C <feature-repo> --git-dir=<an-ordinary-directory> commit  (mixed named set)
  ALLOW  git -C <an-ordinary-directory> commit -m x                     (cwd on main)
  ```
  Mechanism: naming a repo hands the whole verdict to that repo; `_resolve_dir` accepts any
  directory that merely *exists*, and `_commit_to_main_reason` then fails open on "no branch",
  so the real cwd was never evaluated. Now **13/13** on `main` and at the consumer on both hosts.
- 🔴 **A `-C` target that DOESN'T EXIST was never the bug — and the original repro said it was.**
  An unresolvable path already fell back to the cwd and blocked. The handoff's own suggested
  probe used `cwd:"/tmp"`, and `/tmp` is not a repo, so it returned ALLOW **for a second,
  unrelated reason**: nothing was on a blocked branch to begin with. A probe whose cwd cannot
  produce a deny cannot distinguish "fail-open" from "correctly allowed" — the empty-result
  trap, hit inside the very investigation aimed at it. The real defect is a path that resolves
  *fine* and simply is not a repo.
- 🔴 **Two PRs, each `mergeable=CLEAN` against main, conflicted with each other in two hunks.**
  #396 rewrote the exact line #395 patches and deleted `_candidate_dirs` outright, and the
  common tail referenced `unresolved`, a symbol only #396 defines — so taking either side alone
  was a `NameError` or a silent re-open. GitHub judged each PR against a main the other had not
  landed on. The integration-branch test-merge is what surfaced it; nothing in either PR's own
  review could have.
- 🔴 **Neither PR subsumed the other.** After #396 merged (by ZacxDev, mid-session, while
  verification was running), the probe against the new `main` still scored **10/13 — the same
  three**. #396's fix is for `unresolved` (a path the guard cannot turn into a directory at all,
  e.g. a shell variable); #395's is for `not_worktrees` (a path that resolves and is not a repo).
  They are different causes and the resolution keeps them apart, including in the deny message,
  so a deny naming a repo the user never typed now says which of the two fallbacks fired.
- **Verification actually performed** (not the PR bodies' claims — re-derived): red-at-base
  matrix in one tree with only `guard_core.py` swapped, giving a delta of exactly 3
  (`S6`, `S6b`, `test_is_git_worktree_can_return_both_verdicts`); 6 further reds in that scratch
  tree failed with **both** cores and were artefacts of a partial copy, not regressions. Three
  mutations, each `assert`ed to have applied: restoring the fail-open kills S6/S6b/S6d;
  `any` for `all` kills S6b alone; deleting the deny-message branch kills S6d alone. Consumer
  probe on both hosts through the real `bash-guard.py` adapter, reading **stdout JSON**, with a
  blind-stage positive control and a `git status` negative control so the probe is shown to move
  in both directions.
- 🔴 **The guard blocked this very session's merge commit — a genuine false positive of the
  class #396 fixes.** `git -C "$WT" commit` from a shell whose cwd was `~/workspace/devrc` (on
  `main`) denied, naming a repo the command never touched. Worked around with an absolute path
  and `-F <file>`. #396 fixes this for assignments visible in the same command text; a variable
  set in an *earlier* tool call is still unreadable by design. **Use absolute paths with
  `git -C` when committing from an agent shell.**

## Open investigations — live diagnosis state

### Three tests pass or fail depending on the AMBIENT cwd's branch — NEW, unfixed
- **Observed (values):** running `pytest scripts/claude-hooks/tests/test_guard_core.py` from a
  shell sitting in `~/workspace/devrc` (which is on `main`) gives **3 failed, 1364 passed**;
  from a cwd on a feature branch, **1367 passed**. Same tree, same commit.
- **Mechanism:** those three call `gc.evaluate(cmd)` with **no `cwd` argument**, so the guard
  falls back to `_safe_getcwd()` — the process's own directory — and correctly denies a
  `git commit …` case the test expects to be allowed. The tests are asserting a property of
  whatever directory the runner happens to stand in.
- **Ruled out:** not caused by #395/#396 — reproduced identically with `origin/main`'s core and
  with the fixed core. Invisible in CI because the nix build sandbox's cwd is not a git repo.
- **Why it matters:** this is the config-blind-suite class from RULES.md. The suite is
  structurally blind to cwd-dependent bugs, and the failure mode is a test that goes red for an
  environmental reason a reader will attribute to their own change.
- **Next probe:** the three are `test_the_one_real_quoting_false_positive_is_a_heredoc_body` and
  the two `…neighbours_stay_allowed_under_claude_code[git commit -m 'document why …']` params.
  Fix by passing an explicit `cwd=` (a tmp_path non-repo) rather than by widening the harness.

### Host `settings.json` key sets still diverge (drift-check rc=15)
- **Observed (values), live today:**
  ```
  [parity] only on workbench: permissions theme
  [parity] only on laptop:    effortLevel voice
  [parity] enabledPlugins AGREE.
  ```
- **Ruled out:** not a symlink/deploy problem — both hosts report `dangling=0`
  (workbench 156 examined, laptop 151).
- **Leading hypothesis:** benign historical drift. `permissions` on the laptop is a deliberate
  non-import (out of scope, never copied). `effortLevel`/`voice` on the laptop are unexplained.
- **Next probe:** decide intent per key rather than blindly syncing. `settings.json` is per-host
  and unmanaged by nix — see the "do not naively nix-manage it" gotcha below.

### opencode `$ARGUMENTS` substitution for skill-sourced commands — UNVERIFIED
- **Symptom:** #377 deleted `claude/commands/`, so `home.file.".config/opencode/commands"` could
  no longer evaluate and its mapping was removed. Claude Code documents `$ARGUMENTS` in skills;
  **opencode was never tested**.
- **Observed:** the body reaches opencode's template intact and skills are slash-invocable
  (`GET /command`, `"source":"skill"`), but a skill's `hints` array is empty where a command's
  was populated.
- **Ruled out:** "leave the mapping in place" was never an option — the source path is gone, so
  nix evaluation fails.
- **Leading hypothesis:** bounded degradation. Worst case `/analyze-service redis` reaches the
  model with a literal `$ARGUMENTS` and it infers the arg from context; plus a TUI autocomplete
  regression. opencode-only.
- **Next probe:** in opencode, run a migrated command with an argument (e.g. `/analyze-service redis`)
  and check whether the rendered prompt contains the literal string `$ARGUMENTS`.

## Next steps (ranked)
1. **Fix the three cwd-dependent tests** (investigation above). Small, and it removes a red that
   will otherwise be misattributed to somebody's change.
2. **Run `/doctor` in a session.** The only authoritative read of the skill-listing budget.
   Direct measurement of the deployed set: **33 entries, 10,881 chars (−19.7% from 13,554)**,
   largest entry 451 vs the 1,536 per-entry cap, none over.
3. **Reclaim stale worktrees** — `git worktree list` shows **48, of which 36 are `agent-*`**
   (up 4 since the last count; this session added and removed 2 of its own). Several hold
   branches at superseded commits (`feat/commands-to-skills` is still locked at `7ba46b5` vs
   origin `191a336`). Prune with `git worktree prune` + explicit removal; check each for
   uncommitted work first.
4. **Optional: bump Claude Code 2.1.220 → 2.1.226** via the flake input. Auto-update is correctly
   disabled (`DISABLE_AUTOUPDATER`). Missing: subagent text streaming (2.1.224), workspace-trust
   prompt (2.1.226).
5. **~2026-08-25: measure whether any of this worked.** Nothing shipped is proven to have
   *improved* anything — only to have removed things that were false, dead, or unenforced.
   Re-run the turn analysis: did skill auto-invocation rise? did the guards fire on anything real?

## Gotchas / decisions / dead-ends

- 🔴 **"Shorter CLAUDE.md → better adherence" is folklore.** Two 2026 studies found NO size
  effect up to ~500 lines: McMillan (arXiv 2605.10039, 1,650 sessions, affirmative Bayes factors
  0.05–0.10) and ETH Zurich (arXiv 2602.11988: +2.4%, p=0.21, while costing >20% more inference).
  Cut rules for being **false / dead / already deterministic** — never for length. Note RULES.md
  (~8.4k tokens) sits ABOVE the tested range, so the null does not strictly cover it.
- 🔴 **Two rules were retired for being FACTUALLY FALSE**, not stale: `sleep N && cmd` is NOT
  blocked (verified: `sleep 2 && echo` runs), and zsh does NOT abort on unmatched globs
  (`nonomatch` is set). Nobody had re-checked them since they were written.
- 🔴 **The "test wrapper swallows exit codes" belief was WRONG.** The runners always exited
  truthfully. The status was destroyed by **the pipes callers write** (`| tail`), because the gate
  emits ~6,000 lines. Four agents and I all misdiagnosed it. #397 fixes both sides:
  `RESULT: FAIL (exit=1)` from one writer behind EXIT/TERM/INT traps, plus `scripts/gate.sh`
  (48-line summary, no reason to pipe, exits **90 = could-not-vouch** on disagreement).
- 🔴 **`rerere.enabled=true` silently wrote a WRONG `MIN_TESTS`** — it replayed a resolution from
  a *different* merge, stamping a four-way total onto a two-way tree. rerere matches conflict-hunk
  TEXT, not tree membership, so on any base-dependent constant it is reliably wrong while looking
  like a clean auto-resolve. #397 removed the literal; the hazard generalises beyond this repo.
- 🔴 **`find -xtype l` is useless on the laptop** — busybox `find` has no `-xtype` and rejects it
  by printing usage to stderr and **exiting 0**, so `find -xtype l | wc -l` reports a confident
  `0 dangling` on the very host with 46 broken links. Both #384 and #391 hit this independently.
  Same class: BusyBox `find` has no `-printf` over ssh.
- **`gate.sh` / `run-tests.sh` must be invoked through a proper entry point.** Run directly from
  a bare shell they abort at GUARD 1 (`logrotate` missing) or GUARD 2 (`pytest` not importable).
  That is CORRECT — they refuse to go green while testing less. Use
  `nix build .#checks.x86_64-linux.pytests` or the pre-push nix-shell. If you must add a tool,
  **APPEND to PATH** — `nix-shell -p <all REQUIRED_TOOLS>` prepends and shadowed the host's
  opencode 1.18.4 with an ambient 1.18.13, producing a phantom failure.
- **Do NOT naively bring `~/.claude/settings.json` under `home.file`** — upstream issue #78162:
  atomic writes fail with EROFS when the file is a symlink-to-a-symlink, which is exactly what
  home-manager produces. Use an activation script writing a real file if you ever manage it.
- **Deliberately NOT done:** the Harbor `admin:admin` credential was removed from the allow-list
  but **not rotated** (explicitly out of scope). It is still valid on `harbor.homelab.lan`.
- **Squash merges never make the branch head an ancestor of main** — verify landing by CONTENT
  (`git diff <tested-tree> origin/main` empty), never `merge-base --is-ancestor`.
- **Process note:** I merged #397 with `--admin` onto a tree its author never tested (main moved
  between their control and my merge). It turned out green, but that skipped the merged-tree gate
  that caught #377 breaking main earlier the same day. Don't repeat it.

## How to verify
```bash
# 1. Gate on main — read CONTENT, never the exit code
cd ~/workspace/devrc
nix build .#checks.x86_64-linux.pytests --no-link -L 2>&1 | grep -E 'RESULT:|TOTAL'
# expect: TOTAL collected=7434 passed=7433 skipped=1 failed=0 ... RESULT: PASS (exit=0)

# 2. Hosts converged + artifacts actually resolve (consumer, not deploy)
bash scripts/drift-check.sh          # rc=15 expected (settings key parity only); dangling MUST be 0
bash scripts/ship.sh                 # every per-host line must show "managed artifacts … N checked" with N>0

# 3. The migration landed on both hosts
ls ~/.claude/commands 2>&1                                  # must be: No such file or directory
ls -d ~/.claude/skills/*/ | wc -l                           # 33
ssh zach@10.42.0.100 'head -c1 ~/.claude/skills/standup/SKILL.md >/dev/null && echo OK'

# 4. The guard is live (READ STDOUT JSON — this hook always exits 0)
printf '{"hook_event_name":"PreToolUse","tool_name":"Bash","cwd":"%s","tool_input":{"command":"git commit -m x"}}' ~/workspace/devrc \
  | python3 ~/.claude/hooks/bash-guard.py     # expect permissionDecision:"deny"

# 5. The fail-open is CLOSED. 🔴 The cwd MUST be a repo on a blocked branch, or an
#    ALLOW proves nothing — that is what made the original repro unreadable.
mkdir -p /tmp/plain-dir
printf '{"hook_event_name":"PreToolUse","tool_name":"Bash","cwd":"%s","tool_input":{"command":"git -C /tmp/plain-dir commit -m x"}}' ~/workspace/devrc \
  | python3 ~/.claude/hooks/bash-guard.py     # expect deny, naming /tmp/plain-dir as "not a git worktree"
# negative control — same command from a cwd that is not a repo must ALLOW:
printf '{"hook_event_name":"PreToolUse","tool_name":"Bash","cwd":"/tmp","tool_input":{"command":"git -C /tmp/plain-dir commit -m x"}}' \
  | python3 ~/.claude/hooks/bash-guard.py     # expect no output at all
```
