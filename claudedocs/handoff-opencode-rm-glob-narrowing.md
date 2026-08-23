# Handoff: opencode-rm-glob-narrowing — 2026-08-23

## Run this first — the index, one read-only command

```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo devrc
```

Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

> **No `clawgate-task:` field on purpose.** `clawgate_handoff.sh resolve` exited **5**
> (nothing resolved). An unknown session id answers `200` with an empty array, so that
> result cannot distinguish "this session touched no task" from "the id is wrong". Per
> the skill: no field written, and no task created to fill it.

## Goal

Close the `"*rm*-r*"` opencode permission glob — the same one-character defect #695 fixed
for `age`, and which #695's own commit message flagged as *"STILL OPEN, same defect class,
deliberately not fixed here"*.

## State now

**SHIPPED AND LIVE.** Both PRs merged 2026-08-23T20:04Z and the change is verified in the
running engine, not just deployed.

| PR | squash | state |
|---|---|---|
| **#744** the fix | `53cd03cc` | MERGED — verified by CONTENT on `origin/main` (squash makes ancestry permanently false) |
| **#753** this doc | `a9333c23` | MERGED |

- **Live, verified against the real consumer.** `opencode debug agent build --pure` on the
  running v1.18.18 engine resolves `"*rm -r*"` and `"*rm --recursive*"`, with **0** matches
  for `rm*-r`. Deployed store path moved `jaji6qnz…` → `afbiggmm…`.
- **Original symptom reproduced and gone:** `git log --format=oneline --reverse` and
  `terraform plan -refresh=false` now `allow` (were `ask` → auto-reject → dead run);
  `rm -r /repo/build` and `rm --recursive /repo/x` still `ask`.
- **`home-manager switch` exited 0** — but that is a claim about the DEPLOY. The check that
  settles it is the resolved array above, because this config is a `/nix/store` `home.file`
  COPY: a stale base clone would have deployed the old rules while every signal said success.
  The base clone was fast-forwarded (2 behind) BEFORE the switch for exactly that reason.
- **CI green on both heads** — `tekton/devrc-pytests` 15,199 passed / 0 failed,
  `tekton/devrc-nodetests` 1,149 pass / 0 fail.
- **Two adversarial audits ran**, the second re-auditing the first's fixes; both *safe to
  merge*. The public correction recording that the original PR body understated the change's
  cost is [#issuecomment-5384694287](https://github.com/innovation-upstream/devrc/pull/744#issuecomment-5384694287).
- Pre-squash history, since the squash flattens it: `a08ac952` the narrowing · `20988f91`
  audit round (disclosure fixes, target-first class pinned) · `360b54cf` delta round (two
  sentences the audit round itself falsified).
- **Cleanup done:** worktrees `/tmp/wt-devrc-rmglob`, `/tmp/wt-devrc-handoff`,
  `/tmp/wt744-base` removed by exact path; both branches deleted on merge.
- **Subsystem store:** `devrc/opencode.md` bullet flipped `OPEN:` → `RESOLVED 53cd03cc`
  once the merge+switch landed. `devrc/tests.md` keeps its `OPEN:` — that one is still open.

## Open investigations — live diagnosis state

### 🔴 The devrc pre-push gate CANNOT pass a push — confirmed, reproducible, unfixed

- **Symptom + exact repro:** any `git push` from a devrc checkout with `core.hooksPath`
  set runs the full suite (~20 min), the suite **passes**, and then the push dies with
  `PUSH_RC=141` (SIGPIPE). Nothing lands. Repro: `git -C <devrc-worktree> push origin HEAD:<branch>`
  and read the tail of the output.
- **Observed (with values):** two independent attempts, identical signature.
  Both logged `RESULT: PASS (exit=0)` with `TOTAL collected=15201 passed=15199 skipped=2
  failed=0`, then `pre-push: ✅ devrc test suite passed.`, then `PUSH_RC=141`, with the
  remote ref unmoved. **The cause is named in the log at line 21**, moments after the hook
  starts:

  ```
  Connection to github.com closed by remote host.
  ```

- **Ruled out:** an undrained hook stdin — `githooks/pre-push:41` does
  `STDIN_DATA="$(cat || true)"`, so git's ref lines are consumed. Also ruled out that it is
  a test failure: the gate reports PASS both times, and CI independently passes the same tree.
- **Leading hypothesis — now effectively confirmed by that log line:** git opens the SSH
  transport to the remote *before* invoking `pre-push`; the hook then runs for ~20 minutes;
  GitHub closes the idle connection; git finally writes the pack to a dead socket → SIGPIPE.
- **Consequence, which is the point:** the gate does not block bad pushes, it blocks **all**
  pushes, so the only workflow that works is `DEVRC_SKIP_TESTS=1`. That is the
  "permanently-red gate trains everyone to click through" failure mode. It plausibly explains
  why global `core.hooksPath` was **unset** when first measured this session (someone had
  already given up on it) and repo-local was set partway through by another session.
- **Next probe:** confirm the timeout attribution and pick a fix shape:

  ```bash
  GIT_TRACE=1 GIT_TRACE_PACKET=1 git -C /tmp/wt-devrc-rmglob push origin HEAD:refs/heads/tmp-probe 2>&1 | tail -40
  ```

  Fix shapes: move the suite **out** of the hook (run it before `git push`, gate on a
  recorded PASS), or make the hook fast (changed-files-scoped subset), or have the hook
  re-establish the connection. `githooks/tests-on-push.sh` already supports
  `TESTS_ON_PUSH=shadow`.

### 🟡 `guard_core` does not protect `$HOME` subpaths from a recursive delete

- **Symptom + exact repro:** an unattended opencode agent can recursively delete a home or
  system subdirectory with no friction at either layer.
- **Observed (with values):** `layered_verdict` on the PR head — `rm -f -r ~/.ssh`,
  `rm -f -r /etc/nixos`, `rm -f -r $HOME/workspace`, `rm --force --recursive /var/lib/postgresql`
  all **allow**. 🔴 **And on `main`, BEFORE this PR:** `rm -fr ~/.ssh`, `rm -Rf ~/.ssh`,
  `rm -R ~/.ssh` are **already allow**. `guard_core.py:988-1003` — fatal-target set is
  exactly `/`, `~`/`$HOME`, `.`/`..` and 17 top-level dirs, so a **subpath** never reaches it.
- **Ruled out:** that this PR opened the hole. It **widens** an already-open one (adding the
  flag-before-`-r` and flags-after-operand classes). Both halves are written into the config
  header and the characterization test, because either alone misleads.
- **Leading hypothesis:** the glob layer is documented as *friction*, not control
  (`opencode.jsonc` header), so the correct home for this is the argv-aware parser.
  `check_rm_rf_critical` already parses flags structurally at `guard_core.py:1024-1028`;
  what is narrow is the **target set**.
- **Next probe:** before widening, measure how often it would fire on legitimate work —
  that is the reason the PR deferred it, and its own docstring records it as an operator
  decision. Count recursive `rm` invocations under `$HOME` in real session transcripts, then
  extend `_RM_FATAL_DIRS` to `$HOME/**` and re-run `test_guard_core.py` (1,449 tests).

## Next steps (ranked)

1. **Fix the pre-push gate** — see the investigation below. It cannot pass a push; every
   push in this arc needed `DEVRC_SKIP_TESTS=1`. This is the highest-value item left.
2. **`guard_core` `$HOME`-subpath widening** — its own PR, with the firing-rate measurement
   the deferral was predicated on.
3. Nothing else. The narrowing is shipped, live and verified.

## Gotchas / decisions / dead-ends

- 🔴 **`DEVRC_SKIP_TESTS=1` was used for both pushes on this branch.** Justified — the gate
  had just passed on the byte-identical tree each time, and CI passed independently — but it
  **is** a bypass and should not be read as "the gate was satisfied". See investigation 1.
- 🔴 **The GUARD 10 red was a stale runner, not contamination.** A full-suite run on the
  pre-rebase tree reported `TOTAL … failed=0` with `RESULT: FAIL (exit=1)` from GUARD 10
  naming `devrc/.git/config`. I spent a while attributing it to concurrent sessions. The real
  answer: the branch was **5 commits behind `main`**, and `99b2636f` (#730) fixes exactly
  that guard. **Check whether the base moved before diagnosing a guard failure.**
- **`wildcard_match(text, pattern)` takes TEXT FIRST.** Reversing the arguments returns an
  all-`False` table — a reassuring zero that looks like "nothing matches".
- 🔴 **`pgrep -f '<pattern>'` in a wait loop matches the loop's own command line.** A
  `until ! pgrep -f 'run-tests.sh --set all /tmp/wt-devrc-rmglob'` spun forever and a Monitor
  armed on the same pattern timed out instead of firing. Both instruments were broken the
  same way; the only real signal came from reading the log.
- **A trailing `echo "EXIT=$?"` makes the harness report exit 0** regardless. The runner's
  own `RESULT:`/`PUSH_RC=` line is the authority.
- **Decision — land with disclosure, defer the structural fix.** Options weighed:
  (a) land + fix disclosure ✅ chosen; (b) add `*rm -f -r*`-style rules — rejected, unbounded
  *and* blind to the flags-after-operand class (there is no `rm -` prefix to match);
  (c) widen `guard_core` — right fix, needs its own measurement; (d) drop the PR — rejected,
  the false-positive class is real.
- **Not accepted from the delta re-audit:** it reported the infix control fails **6** tests
  naming two ledger tests. Measured on this tree it fails exactly **5** (3 `MUST_ALLOW` rows,
  the ask-count pin, the characterization test) and neither ledger test is among them.
  Recorded in `360b54cf` rather than deferred to.
- **Both audits caught real defects in my own prose**, which is the class this PR is about:
  the audit round falsified a sentence two lines from one it had just corrected
  ("closes three of these rows" → six), and its replacement characterisation was wrong in
  both directions (missed that `"*rm --recursive*"` carries its own surviving false-positive
  class — `pnpm --filter form --recursive build` is still `ask`).

### What the change actually is — moved here from `State now`, which is REPLACE-on-update

`"*rm*-r*"` had a `*` **between** "rm" and "-r", matching any command text containing "rm"
followed *later* by "-r". "rm" is a substring of fo**rm**at, terrafo**rm**, fi**rm**ware,
confi**rm**, platfo**rm**; "-r" covers `--reverse`, `--refresh`, `--reporter`, `--repo`,
`--replace`, `--recursive`. `opencode run` **auto-rejects** an ask, so a match killed the
run mid-task rather than costing a prompt.

🔴 **It is not the one-character fix the queueing handoff described.** `"rm --recursive"`
does not contain the literal `"rm -r"`, so dropping the inner `*` alone would have released
`rm --recursive <path>` — measured, plain `allow` at both layers. Hence **two** rules, each
pinned individually in `MUST_ASK`, each proven sole-decider for its own row.

(This block lived under `State now` in the original doc. `State now` is REPLACED on every
update, so a durable explanation there is deleted the first time anyone refreshes the status
— which this very update would have done. It is durable, so it belongs under an APPEND
heading. The tool's drop-detector did **not** flag it; that check is a floor, not proof.)

- 🔴 **A `/handoff` OPEN: marker written mid-session goes stale within the hour.** This
  session wrote `OPEN: PR #744 (unmerged)` into `devrc/opencode.md`, then merged and switched
  ~40 min later. Caught and flipped to `RESOLVED 53cd03cc` only because the next question
  prompted a re-check. The store's own docs record a 22-day instance of this. **If you write
  an `OPEN:` marker and then finish the work in the same session, go back and flip it.**
- 🔴 **`home-manager switch --flake` builds from the WORKING TREE, so merging is not enough.**
  The base clone was 2 commits behind after the merges; a switch at that point would have
  deployed the OLD glob and exited 0. `git -C <repo> merge --ff-only origin/main` first, then
  switch, then verify the resolved array — three separate claims, all of which must hold.
- **An empty `grep` over the engine dump is not evidence.** `grep '"pattern": "\*rm'` returned
  nothing after a successful switch and looked like "the rule is gone"; the pattern was simply
  wrong. The positive control — 238 `"pattern"` matches in a 32 KB dump — is what distinguished
  a real absence from a bad query. Never read a reassuring zero without one.
- **Merge order note:** #753 (the doc) was merged before #744 (the fix) as asked. Neither was
  stacked — both branched off `main` directly and touched disjoint files — so `--delete-branch`
  was safe on both. Had they been stacked, deleting the parent's branch would have auto-closed
  the child PR with no way to reopen it.

## How to verify

```bash
# 1. LIVE — the resolved array in the running engine. This is the authoritative check.
opencode debug agent build --pure 2>/dev/null | grep -a '"pattern"' | grep -a rm
#    want: "*rm -r*", "*rm --recursive*", the three "*rm -rf …*" denies — and NO "*rm*-r*"

# 2. the original failing symptom, against the deployed config
python3 - <<'PY'
import sys; sys.path.insert(0, '/home/zach/workspace/devrc/scripts/tests')
import test_opencode_config as T
for c in ['git log --format=oneline --reverse', 'terraform plan -refresh=false',
          'rm -r /repo/build', 'rm --recursive /repo/x']:
    print(f'{T.effective_bash_action(c, None):>5}  {c}')
PY
#    want: allow, allow, ask, ask

# 3. the merges, BY CONTENT — ancestry is permanently false after a squash
git -C /home/zach/workspace/devrc show origin/main:scripts/opencode/opencode.jsonc | grep -n '^      "\*rm'

# 4. the suite
cd /home/zach/workspace/devrc && PYTHONDONTWRITEBYTECODE=1 \
  python3 -m pytest scripts/tests/test_opencode_config.py scripts/opencode/tests -q
```
