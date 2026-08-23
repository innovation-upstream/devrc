# Handoff: proactivity-gate — 2026-08-23

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Turn Zach's three-line proactivity proposal (safe+unblocked → proceed; blocked → ask then
proceed; destructive → block/warn/ask) into a rule that survives its own failure modes, and
tie it to clawgate only where clawgate can actually carry it.

## State now
- **Branch/PR: DONE.** PR #726 **MERGED** 2026-08-23T17:21:58Z, squash `1bc16f01`.
  Branch `rules/proactivity-gate` deleted. 10 commits (1 initial + 9 fix rounds).
- **Deployed and VERIFIED live on BOTH hosts.** `scripts/ship.sh` converged workbench +
  laptop; `readlink -f ~/.claude/RULES.md` → `/nix/store/i5msfdqxmi8n5bvqscq7g7qfnwkda1yd-hm_RULES.md`,
  identical on both, and `grep -c "Default to PROCEEDING"` = 1 there **and** in
  `~/.config/opencode/AGENTS.md`. All four branch labels present in the deployed file.
  (Merged ≠ deployed here: both paths are `home.file` copies, so the merge alone changed nothing live.)
- **What shipped** (all in `claude/RULES.md` → "Deterministic Over Prose; Push Back Before Acting"):
  a four-branch trigger tree replacing TWO deleted bullets ("Flag BEFORE acting",
  "User-facing micro-decisions"). Branches: Out of scope → file a task, don't ask · Fork →
  one question that buys the run · Outward-facing/irreversible/high-blast-radius → flag with
  blast radius · Named hazard → don't do it AND don't ask. Plus: an ANSWER buys the run but
  an APPROVAL covers only its step; classify blast radius before starting; trust a DERIVED
  safety claim below a STATED one.
- `claude/RULES-ARCHIVE.md` → new `## proactivity-gate` anchor (evidence + the clawgate route facts).
- `claude/skills/clawgate/reference/hooks.md` → "What clawgate can and cannot carry for the
  proactivity gate" (coverage table + the auto-approve-all fatigue hazard).
- `scripts/tests/test_rules_size.py` → `MAX_BYTES` 39,200 → **40,300**, with the ledger entry.
- **Filed, not fixed:** clawgate task **#346** — three pre-existing clawgate-skill files
  contradict each other about who can set `complete`. Out of scope for #726; recorded on the
  PR as a comment.
- **Nothing in flight.** Working tree carries only pre-existing dirt that is NOT mine
  (`scripts/session-analysis/initiative-scan.py` + 4 untracked files, present at session start).

## Open investigations — live diagnosis state
### Pre-existing: 3 `session-analysis` test failures come from an uncommitted local edit
- **Symptom + exact repro:** `nix develop ~/workspace/devrc --command bash -c 'cd ~/workspace/devrc && python -m pytest scripts/session-analysis/tests -q'`
  → `3 failed, 475 passed`. Failing: `test_cluster_merges_dated_variants_newest_wins`,
  `test_cluster_distinct_slugs_stay_separate`, `test_cluster_dateless_doc_uses_mtime`.
- **Observed (with values):** `KeyError: 'resolved'` at `scripts/session-analysis/initiative-scan.py:793`.
  That file is **modified in the working tree** (`67 insertions, 5 deletions`) and was already
  dirty at session start — not written by this session.
- **Ruled out:** *not* caused by PR #726 — a pristine worktree at `origin/main` runs the same
  target at **478 passed**. Measured, not inferred.
- **Leading hypothesis:** the uncommitted `initiative-scan.py` change added a `resolved` key to
  one code path and the cluster helpers read it unconditionally.
- **Next probe:** `git -C ~/workspace/devrc diff scripts/session-analysis/initiative-scan.py | head -80`
  — decide whether that WIP is worth finishing or reverting. ⚠ `ship.sh` deployed
  `origin/main` **+ this WIP** to the workbench; the laptop is clean.

### Pre-existing: a test mutates the REAL repo's `.git` during the suite
- **Symptom + exact repro:** running `scripts/run-tests.sh` in the shared checkout yields
  `DEVRC-GITENV-VIOLATION` teardown ERRORs (1–5, varying).
- **Observed (with values):** first seen from
  `test_subsystem_store_api.py::TestScopeRevision::test_a_real_scope_repo_yields_its_HEAD_sha`;
  "What moved" named `CHANGED /home/zach/workspace/devrc/.git/config` and
  `CREATED .git/refs/remotes/origin/fix/reap-check-zombie`. Damage was benign (a fetched
  remote-tracking ref + a config line); remote URL, `core.hooksPath` and identity all intact.
- **Ruled out:** not caused by #726 — reproduces on untouched `origin/main`.
- **Leading hypothesis:** TWO distinct causes wear the same symptom. (a) a genuine leak in
  that one test file; (b) **concurrent git activity by other sessions/agents** in this shared
  git dir — later runs named `worktree-agent-*` refs and one named *my own commit* landing
  while the suite ran.
- **Next probe:** run the suite from a `.git`-free extract —
  `git -C ~/workspace/devrc archive origin/main | tar -x -C <tmp> && cd <tmp> && PYTHONPATH=$PWD/scripts python -m pytest scripts/tests -q`.
  Audits measured **zero** violations that way, which isolates (b) from (a).

## Next steps (ranked)
1. **Nothing is required.** The rule is merged, deployed and verified. Everything below is optional.
2. **Decide the fate of the uncommitted `initiative-scan.py` WIP** — it is deployed to the
   workbench and it reds 3 tests. Finish or revert; do not leave it.
3. **clawgate task #346** — reconcile the three contradictory `complete`-capability claims,
   and consider fixing the root: clawgate's own `internal/taskstatus/taskstatus.go:29-31`
   comment is wrong about its own package.
4. **Watch the rule in use.** It is prose with no enforcing gate (deliberately — the one
   deterministic candidate, clawgate, fails open). The honest test is whether the Out-of-scope
   branch actually produces filed tasks over the next few weeks.

## Gotchas / decisions / dead-ends
- 🔴 **`approve-with-comment` is NOT an answer channel.** `clawgate-hook.sh:171-174` logs the
  comment and returns a bare `allow`/`deny`. I claimed the opposite from the feature's NAME;
  `hooks.md` already said so in bold 23 lines above where I wrote the contradiction. **Read
  the code, not the feature name.**
- 🔴 **Scope clawgate status claims to the ROUTE, not the verb.** `AllowedForAgent` gates only
  the in-devpod AGENT surfaces. The hook-token machine route (`clawgatectl task status`, i.e.
  the LOCAL pickup path), the operator route + its tool, `POST /tasks/merge`, and the
  unauthenticated session route all set `complete` deliberately. Got this wrong in BOTH
  directions across three rounds; the archive entry now states **no count** and says to
  enumerate from `notes.SetStatus`'s call sites at the moment of need.
- 🔴 **A grep for a WORD is a spelled guard.** Two commit messages claimed "repo-wide grep
  confirms no copy survives"; three copies survived, spelled differently. Sweep for the
  CLAIM's shape, not its wording.
- 🔴 **Don't restate derived numbers in prose that is edited alongside what they measure.**
  Three consecutive rounds stated the remaining byte slack and each round's own edit
  invalidated it. Fixed structurally: figures removed, gate output is the authority. Same move
  later for a route count and an audit count.
- 🔴 **Ceiling sizing: leave enough slack to absorb the PR's OWN review round.** +900 left
  186 B; restoring audit-found scope cost 130 B ⇒ 56 B. Had to bump again inside the same PR.
- 🔴 **A closed list is NARROWER than the shape it samples.** Bit this PR twice — a `🔴 marks
  never` predicate silently excluded `git add -A`/`reset --hard`/`sudo nixos-rebuild`, and a
  two-file list excluded skills. Both now shapes.
- 🔴 **The shared checkout is actively contended.** Another session moved HEAD and *switched
  my branch to `main`* mid-session. `git branch --show-current` immediately before every
  commit is what caught it. Commit from a worktree here.
- **bash-guard blocks a commit whose MESSAGE quotes `git reset --hard`.** Use `git commit -F <file>`.
- **Audit economics:** a cold read caught 1 scope loss; audits caught 8 more, including one
  wrong since the first commit that 4 rounds read past. Findings converged on the *documentation
  of* the work, not the rule — `RULES.md:45` was stable from round 3 on.

## How to verify
```bash
# the rule is live on this host (readlink is the arbiter, never a diff)
readlink -f ~/.claude/RULES.md            # must end in /nix/store/...-hm_RULES.md
grep -c "Default to PROCEEDING" ~/.claude/RULES.md ~/.config/opencode/AGENTS.md   # 1 and 1

# both hosts agree
ssh zach@10.42.0.100 'readlink -f ~/.claude/RULES.md'   # same store path

# the size gate still passes and can still go red
nix develop ~/workspace/devrc --command bash -c \
  'cd ~/workspace/devrc && PYTHONPATH=$PWD/scripts python -m pytest scripts/tests/test_rules_size.py -q'

# the merge landed BY CONTENT (a squash is never an ancestor)
git -C ~/workspace/devrc show origin/main:claude/RULES.md | grep -c "Default to PROCEEDING"
gh pr view 726 --repo innovation-upstream/devrc --json state,mergeCommit
```
