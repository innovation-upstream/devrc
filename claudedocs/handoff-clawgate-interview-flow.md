# Handoff: clawgate interview flow + skill drift — 2026-08-22

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
Evaluate the `clawgate` skill, fix what had drifted, then add an **alignment interview**
to clawgate task CREATION — plus a `flows/` convention so future per-flow steps cost no
always-on context.

## State now
- Branch / PR: everything below is **merged**. No open PRs from this session.
- 🔴 **No `clawgate-task:` front matter deliberately.** The resolver returned #321 AND
  #322; this session **authored** both and picked up neither, so recording either would
  make the doc reconcile forever against work nobody did. The link is recorded by a
  session *touching* a card at all — it is not evidence of work.

**DONE — devrc (all shipped to both hosts via `scripts/ship.sh`)**
- `#618` — clawgate skill drift. Three places claimed the idle reaper destroys tasks and
  agent pods; false since **0.7.96** (`cf529d41`), where `reapIdleTasks` switched to
  `AddTags`+`AddComment`. Route counts corrected as *instances*: deployed 120 / 23 `/api/*`
  vs trunk 121 / 24.
- `#631` — the interview flow itself: `claude/skills/clawgate/flows/task-authoring.md`,
  `scripts/claude-hooks/clawgate-task-interview-guard.py`, registrar wiring, the `flows/`
  convention in CLAUDE.md. SKILL.md held **net −10 bytes**.
- `#638` — `task create --help` was denied (creates nothing). Fail-closed, scoped to
  `clawgatectl` by name.
- `#691` (`b8e8843b`) — the deny message called a client-side convention "structural".
- `#702` (`4ea2405d`) — CLAUDE.md's "githooks ships uninstalled" was measured false.

**DONE — homelab-infra** `#366` (reaper comment), `#367` (rescued arr-stack gotchas from
an un-pushed local commit), `#371` (Cilium L2 into git), `#374` (ignore agent scratch +
land two stranded docs).

**Deploy/verify status — honest:** all devrc work is merged AND shipped; the corrected
deny message was live-fired on **both** hosts. homelab `#366` is merged but **deploys
nothing** — the image pin is unchanged, so it reaches a container only at the next
clawgate release.

## Open investigations — live diagnosis state

### Pre-push gate can REWRITE the branch it is pushing (clawgate task #322)
- **Symptom + exact repro:** `git push -u origin <branch>` from a worktree hangs ~2 min,
  then the branch HEAD is fixture commits and the real commit is gone. Reproduced **twice**,
  deterministically, pushing a two-file docs change.
- **Observed (with values):**
  - HEAD after: `<sha> m` / `<sha> autocommit: 1 change(s) in the some-scope analyze-service index`
  - Tree left holding fixtures `a.md`, `alpha.md`, `beta.md`, `claude/skills/ghost/SKILL.md`;
    index wrecked (nearly every tracked file reading untracked). Working files survived on disk.
  - Push output contained `Terminated  python -m pytest ...` — the hook was running the suite.
  - At that moment: `git config --local --get core.hooksPath` → `/home/zach/workspace/devrc/githooks`.
  - **Hours later, same session, unset everywhere.** `githooks/install.sh` sets the key
    `--global`, so the `--local` value came from something else.
- **Ruled out (with the evidence that killed each):**
  - *The hourly `analyze-service-index-commit.timer`* — real and active (last fired
    19:07:25), but it processes 10 NAMED scopes (`devrc`, `homelab-talos`, `kubeclaw`, …)
    and logs `scope X: committed <sha>`. The polluting commits name **`some-scope`** with
    `alpha.md`/`beta.md` — values that exist only in `_seed()` in
    `scripts/tests/test_analyze_service_index_commit.py`.
  - *The suspect test in isolation* — ran `test_analyze_service_index_commit.py` alone in a
    worktree: **88 passed, HEAD unmoved, tree clean**.
  - *The default-set gate* — a full `run-tests.sh` run (**14,376 passed**) left HEAD
    byte-identical.
- **Leading hypothesis:** `githooks/tests-on-push.sh` runs the fuller **`--set all`**, while
  the manual gate runs the default set. Something in the `--set all`-only portion resolves
  `git` upward into the enclosing worktree. Note `scripts/analyze-service-index/commit.sh`
  both `git init`s its scope (:771) **and** has a "not its own repo — refusing to commit"
  guard (:719) — how that guard is bypassed is the open question.
- **Next probe (verbatim):**
  ```bash
  W=/tmp/wt-probe; git -C ~/workspace/devrc worktree add $W -b probe/set-all origin/main
  B=$(git -C $W rev-parse HEAD)
  nix develop "$W" --command bash "$W/scripts/run-tests.sh" "$W" --set all >/tmp/setall.log 2>&1
  echo "before=$B after=$(git -C $W rev-parse HEAD)"
  ```
  If HEAD does not move, the hypothesis is **wrong** and the trigger is elsewhere in the
  hook's environment — say so and stop.

### Interview-guard false positives — three from ONE rule (clawgate task #321)
- **Symptom + exact repro:** the unseeable-body rule denies commands that create nothing.
  1. `clawgatectl task create --help` — **FIXED** (#638).
  2. `cat >> f <<'EOF'` whose heredoc *quotes* a create — still denied. Blocks writing docs
     or tests about the guard itself.
  3. 🔴 `clawgatectl task create --body-file $VAR/path` — **still denied**, and this is the
     worst: a well-formed body WITH `## Acceptance criteria` is rejected purely because the
     path came from a shell variable. Only a literal absolute path works.
- **Observed:** deny reason for (3) is
  `"CANNOT SEE THE BODY (the argument is a shell substitution this gate cannot evaluate)"`.
- **Ruled out:** *the body was malformed* — the identical body posted fine with a literal
  path and read back `hasAC=true`.
- **Leading hypothesis:** three misfires from one rule suggests fixing where the gate
  **resolves inputs**, rather than adding a third allowlist. #321's criteria are
  deliberately scoped to the heredoc path only — widening is a decision, not a detail.
- **Next probe:** decide (2) vs (3) first. (3) hits the happy path and is arguably the
  higher-value fix even though (2) is what #321 specifies.

### Write-back guard fires on AUTHORED cards (no task filed)
- **Symptom:** the Stop hook demanded a write-back for #321 and again for #322 — both cards
  this session **authored** and never picked up.
- **Observed:** both times the correct action was the `--dismiss` escape hatch; its warning
  text was accurate and the main path would have pushed a false completion.
- **Leading hypothesis:** the trigger is "read a card, then do real work", which cannot
  distinguish authoring-then-continuing from pickup. Any session that files a task and keeps
  working hits this. **Not filed** — it is a design question about the trigger; #321 is the
  natural home if it turns out to be worth fixing.

## Next steps (ranked)
1. **Run #322's next probe** (above) to confirm or refute the `--set all` hypothesis before
   any fix. It is minutes of work and it decides the whole shape of the fix.
2. **Fix #322 in the harness, not the test** — `GIT_CEILING_DIRECTORIES` or explicit
   `GIT_DIR`/`GIT_WORK_TREE` for the run, so the *next* such test fails instead of corrupting.
3. **Decide #321 (2) vs (3)** — see above.
4. **Exercise the interview flow from a FRESH session** — it has only ever been run by its
   own author, so the routing claim (a session discovers `flows/` only via the hook's deny
   message) is unproven.

## Gotchas / decisions / dead-ends
- 🔴 **`core.hooksPath` is VOLATILE, not merely per-clone** — it changed twice within this
  session with no action by anyone here. Re-measure at the moment you act; a reading from
  earlier in the session is not evidence. Now documented in CLAUDE.md (#702).
- 🔴 **Nothing server-side reads `## Acceptance criteria`.** Measured: zero Go/TS/SQL files
  under `containers/clawgate` contain the phrase (13 contain "acceptance", none as that
  phrase; grep validated by `StatusAllowedForAgent` → 3 files). It is enforced by SKILL.md's
  pickup ritual and the hook — nothing else. The genuinely structural fact is narrower: a
  dispatched devpod agent cannot set `complete` at all (`notes.StatusAllowedForAgent`).
- **`flows/` ships for free** — `nix/home.nix:108` is `cp -R ${../claude/skills} "$out"`, the
  whole tree. A new flow file needs no nix change; a new *hook* does.
- **A `flows/` file does not auto-fire** the way a skill description does. The hook is what
  routes to it — that pairing is the design, and neither half works alone.
- **Dead end:** trying to write tests for the guard with `cat >> f <<'EOF'` — the guard blocks
  it (false positive 2). Use the `Write` tool.
- ⚠ **The base clone `~/workspace/devrc` was left on a DETACHED HEAD by another session**,
  with a staged `CLAUDE.md` differing from `origin/main`. Untouched deliberately. Three other
  sessions' handoff docs also sit untracked in `claudedocs/`.

## How to verify
```bash
# the interview guard is live and CORRECT on this host (want: 1 then 0)
f=$(readlink -f ~/.claude/hooks/clawgate-task-interview-guard.py)
grep -c "convention the pickup ritual and this hook enforce" $f; grep -c "structurally forces" $f

# it denies a create with no criteria, allows one with, and does not over-block a read
printf '%s' '{"tool_name":"Bash","tool_input":{"command":"clawgatectl task create --body \"x\""}}' | python3 $f   # deny
printf '%s' '{"tool_name":"Bash","tool_input":{"command":"clawgatectl task ls"}}' | python3 $f                    # silent

# the reaper claim matches the code (want: tags, not dismissTask)
grep -A3 "func (s \*Server) reapIdleTasks" $HOMELAB/containers/clawgate/internal/api/server.go

# the full gate, by hand — devrc has NO CI
nix develop <wt> --command bash <wt>/scripts/run-tests.sh <wt>   # read RESULT:, not an exit code
```
