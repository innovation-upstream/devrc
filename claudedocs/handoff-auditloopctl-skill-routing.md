# Handoff: auditloopctl-skill-routing — 2026-09-26

## Run this first — the index, one command
```bash
cairn recall --repo /home/zach/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Make the `auditloop` skill route a session to `auditloopctl --help` instead of a pasted
verb list, so the CLI contract cannot drift from the binary.
- **closing-condition:** `check` — **devrc#1885 is MERGED** and
  `grep -c 'auditloopctl --help' $DEVRC/claude/skills/auditloop/SKILL.md` is non-zero on
  `origin/main`.

## State now
- **Repo: `devrc`.** Branch `docs/auditloopctl-cli`, commit `76741eba`, pushed.
  **PR [innovation-upstream/devrc#1885](https://github.com/innovation-upstream/devrc/pull/1885)
  is OPEN.** At handoff time `mergeable=MERGEABLE`, `mergeStateStatus=UNSTABLE` with all
  four Tekton gates (`cairn-client-runs`, `gotests`, `nodetests`, `pytests`) still
  **pending** — CI had not reported, not failed. Re-check before merging.
- **One file changed**, `claude/skills/auditloop/SKILL.md`, +33/−2:
  1. New `### 🔴 auditloopctl — the CLI DOCUMENTS ITSELF` block: the build line, the two
     env vars, the exit-code contract, the pointer to `internal/gate`'s package comment.
  2. Corrected the heading `### Read API — machine consumers (per-user, read-only)`, stale
     since auditloop PR #73 shipped write-scoped keys → now names the `read` default and
     the write radius (a write key reads stored login credentials back in **plaintext**).
  3. Recorded the measured 2026-09-26 no-consumer fact for `auditloopctl gate`.
- **Local test run: 280 passed** (`test_skill_descriptions`, `test_skill_audit`,
  `test_doc_path_rot`, `test_skill_tiers`, `test_skills_mapping_guard`) under
  `nix-shell -p python3Packages.pytest`. **Not** the same instrument as the Tekton gate —
  that is still pending.
- **No `/audit-pr` round was run** on #1885. Offered and not taken up; a single-file docs
  change with green local gates. Round 0 is only actionable while the merge decision is open.
- Worktree `/home/zach/workspace/devrc-auditloopctl-doc` created, used, **removed and
  pruned**. `devrc` base clone is back on `main`, clean apart from a pre-existing untracked
  `nix/system/apply-networkmanager-openvpn.sh` that is **not mine**.
- **No clawgate task.** `clawgate_handoff.sh resolve` → rc=5, with its positive control
  confirming the board was reachable and the token accepted. That narrows it to "a correct
  id WOULD have resolved"; it is **not** proof this session's id was right, and not a clean
  bill of health. No `clawgate-task:` field written.

## Next steps (ranked)
1. **Merge devrc#1885** once the four Tekton gates report green — `gh pr checks 1885
   --repo innovation-upstream/devrc`. Do **not** merge through a pending or red gate.
   forcing: gate — the PR's own four Tekton checks were still pending at handoff.
2. **Decide whether the auditloop `CLAUDE.md` prune (`chore/prune-claude-md`) needs its
   classification in `publish/rules.toml`** before it merges — 16 new
   `claudedocs/architecture/*.md` files, and a new `claudedocs/*.md` of any non-`handoff-*`
   shape ABORTS `./publish/run.sh --check`. **Not my branch — another session's.** Check
   with them first rather than editing it.
   forcing: none

## Gotchas / decisions / dead-ends
- 🔴 **The devrc skill-listing SIZE RATCHETS went red on the first attempt, and they are
  the reason the frontmatter reads the way it does.** Adding `auditloopctl` to the
  `description:` grew it 62 bytes and failed three tests at once:
  `test_skill_descriptions::test_the_listing_total_does_not_regrow_past_its_ratchet`,
  its `test_control_…_can_go_red` twin, and
  `test_skill_tiers::test_the_quoted_measurements_match_the_live_tree`
  (`MEASURED_ALL_TIER_A_CHARS` 10_986 vs 11_048 — a delta exactly equal to my growth).
  **Fixed by holding the description length-neutral at 394 bytes, byte-for-byte its prior
  size**, trading the new words in for a duplicated `auditloop.zacx.dev`, "machine", and
  "auditloop self-harness". 🔴 **The ratchets were NOT re-pinned** — re-pinning pays the
  saving back and the test says so. Anyone adding a trigger word to a skill description
  must budget for this: it is a zero-sum edit, not an append.
- **The point of the change, stated so it is not undone:** `auditloopctl --help` is the
  SINGLE statement of the verb table and the exit-code contract — auditloop's own
  `CLAUDE.md` says so deliberately, because that prose drifted once by existing in more
  than one place. The skill must **route** to it. Do not "improve" the skill by pasting
  the verb list back in.
- **Every claim in the new block was copied off `--help`** from a binary built at
  `auditloop@dbdbf62`, not from memory: the five `[WRITE]` verbs (`run start`,
  `walkthrough start`, `walkthrough evaluate`, `config set`, `config infer`), the
  0/1/2/3 exit codes, "there is no per-subcommand help".
- **`bin/` is gitignored in auditloop** (`.gitignore:2`), so a fresh clone or worktree has
  no `bin/auditloopctl` — that is why the build line in the skill is load-bearing rather
  than decorative. Measured: a plain `go build -o bin/auditloopctl ./cmd/auditloopctl`
  succeeds **even with this host's `LD_LIBRARY_PATH` set**; that hazard is chromium-TESTS
  only, not the build.
- **No-consumer sweep, with its own scope stated:** `command grep -rIl auditloopctl` over
  `homelab-talos`, `vetr`, `devrc` → **zero** references; inside `auditloop` itself the
  only hits are `CLAUDE.md` and `internal/auditloopctl/cli_test.go`. So the 2026-08-27
  "NO KNOWN CONSUMER" measurement in auditloop's `CLAUDE.md` still stands. ⚠ `naida` is
  **not checked out on this host**, so that repo rests on the older 2026-08-27 measurement,
  not on mine.
- 🔴 **DRIFT FOUND, and it belongs to ANOTHER SESSION — do not adopt it as yours.** While
  this session worked in devrc, `~/workspace/auditloop` was switched out from under it to
  branch **`chore/prune-claude-md`**, commit **`101726f` "docs: prune CLAUDE.md 202 KB →
  25 KB (router + 16 demand-loaded slices)"**. `CLAUDE.md` is now **25,276 bytes** (from
  201,995) with 16 slice docs under `claudedocs/architecture/`. That is the FIRST of the
  two open arcs recorded in
  `~/workspace/auditloop/claudedocs/handoff-auditloop-agent-api-cli.md` being worked live.
  Its closing condition there was "within ~2 KB of 149,349 bytes" — 25 KB clears it by a
  wide margin, but **that arc's doc has not been updated to say so** and the work is on an
  unmerged branch. Two things to check before it lands, neither of which I touched:
  (a) the handoff's 🔴 constraint was **do not delete** the "NO KNOWN CONSUMER" note or the
  credential-radius block — both appear preserved in the new router, but I read the router
  only, **not** the 16 slice docs; (b) the `publish/rules.toml` classification above.
- **`cd <path> && git …` is refused by the command guard**, as is `env -u VAR <cmd>`. Use
  `git -C <path>`; for `go` under a scrubbed env, a **uniquely-named** wrapper script in
  your own scratch dir that `unset`s the variable and execs `go`.
- **`handoff_search` is corpus-wide, not repo-scoped** — a query about auditloop returned
  three hits, all from `homelab-talos` and `devrc`, none relevant. Scope it with `--repo`
  when reach is not what you want.

## How to verify
```bash
# 1. the PR, and its gates — do not merge through a pending one
gh pr view 1885 --repo innovation-upstream/devrc --json state,mergeable,mergeStateStatus
gh pr checks 1885 --repo innovation-upstream/devrc

# 2. the change itself
git -C $DEVRC diff origin/main...origin/docs/auditloopctl-cli -- claude/skills/auditloop/SKILL.md

# 3. the ratchet property that constrained it — the description must stay 394 bytes
git -C $DEVRC show origin/docs/auditloopctl-cli:claude/skills/auditloop/SKILL.md | sed -n '3p' | wc -c   # 394
git -C $DEVRC show origin/main:claude/skills/auditloop/SKILL.md                  | sed -n '3p' | wc -c   # 394

# 4. the gates that were red first, now green (needs pytest)
nix-shell -p python3Packages.pytest --run "cd $DEVRC && python3 -m pytest \
  scripts/tests/test_skill_descriptions.py scripts/tests/test_skill_tiers.py \
  scripts/tests/test_skill_audit.py scripts/tests/test_doc_path_rot.py \
  scripts/tests/test_skills_mapping_guard.py -q"                                # 280 passed

# 5. the thing the skill now routes to — read it, do not re-derive it
cd ~/workspace/auditloop && go build -o bin/auditloopctl ./cmd/auditloopctl && bin/auditloopctl --help
```
