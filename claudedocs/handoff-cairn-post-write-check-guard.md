# Handoff: cairn post-write-check guard — 2026-09-17

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
Two skills mandate one post-write command and spelled it two different ways;
`claude/skills/cairn/SKILL.md` said `cairn-validate --scope <scope>` while
`subsystem-index` said `cairn sync && cairn-validate --scope <scope>` — and the bare form
silently parses the PRE-WRITE bytes. Fix the spelling and pin it mechanically so it cannot
drift again.
- **closing-condition:** `check` — `#1738` merged to `main`, `grep -c 'cairn sync &&
  cairn-validate' claude/skills/cairn/SKILL.md` >= 1 on `origin/main`, and
  `python3 -m pytest scripts/tests/test_cairn_skill_verb_ledger.py` green there.
  **MET 2026-09-17** — merged `d45612d5`, grep returns **2**, 34 passed.

## State now
- **MERGED.** `#1738` squash-merged to `main` as **`d45612d5`** at 2026-09-17T07:04:43Z,
  branch deleted. Five commits: `a6b3e9fe` (original) → `979990f8` → `bb23fe14` →
  `8241724e` → `06d33a7a`.
- Verified **by content, never ancestry** (a squash makes `--is-ancestor` false forever):
  all six touched files on `origin/main` byte-identical to the PR head; merge commit
  exists; the kickoff's closing condition returns 2.
- CI green on `06d33a7a` — all four Tekton commit **statuses** (`devrc-pytests`,
  `-nodetests`, `-gotests`, `-cairn-client-runs`). Note there are **0 check-runs**; the
  only gate here is a commit status, so a rollup rule must expect >= 1 status, not >= 1
  check-run.
- Closes the follow-up recorded on `#1736` (`b03551f9`).
- **Nothing is in flight.** Claim `cairn-skill-post-write-check-1738` released; my four
  worktrees (`/tmp/wt-1738`, `-fix`, `-flake`, `/tmp/wt-main-flake`) removed; temp branch
  and `refs/remotes/origin/pr-1738` deleted.

**What shipped, across four audit rounds.** The payload — the prose spelling fix in
`claude/skills/cairn/SKILL.md` — was correct from `a6b3e9fe` and never became a finding.
Everything after round 0 was the *guard*:

| round | found | change |
|---|---|---|
| 0 | requirement questioned; 3 mutants surviving | guard derives its site list instead of hand-listing 2 files |
| 1 | 4 🟡 | matches the `--scope` INVOCATION; every tracked `SKILL.md` scanned (33 → 36); stale measurement corrected |
| 2 | 3 🟡 | 0-bytes sweep finished (6 carriers, not the 3 claimed); regex tempered; exemption bounded |
| 3 | 5, one blocking | decision logic had ZERO coverage; keyword heuristic replaced with a sentinel; 18 fixtures added |

Final guard shape (`scripts/tests/test_cairn_skill_verb_ledger.py`): four properties over a
`git ls-files`-derived set of all 36 tracked `SKILL.md` — (1) the derived set equals
`POST_WRITE_SITES ∪ READ_TIME_EXEMPT`, GROWS and SHRINKS asserted separately; (2) each
post-write site names it the declared number of times; (3) every occurrence carries
`cairn sync && `; (4) no skill spells it with the PACKAGED client. Counter-examples are
exempted by the literal sentinel `<!-- not-a-mandate -->` in the same block within 120
chars — **not** by keyword sniffing.

## Open investigations — live diagnosis state

### `cairn-validate` is instance-blind while `cairn sync` is not
- as-of: 2026-09-17
- **Symptom + exact repro:** the mandated `cairn sync && cairn-validate --scope <scope>`
  refreshes EVERY configured instance on the left and validates only the DEFAULT
  instance's cache on the right. On a multi-instance host, a scope routed to a
  non-default instance would be validated against a cache that does not hold it — which
  re-opens the exact pre-write-bytes hazard the `cairn sync &&` exists to close.
- **Observed (with values):** `cairn sync` → `cmd_sync` refreshes every instance,
  `scope=None` always. `scripts/cairn-validate:80-86` prepends
  `--store subsystem_read_store.read_store_root()` — the DEFAULT instance's root —
  while sync writes per-instance roots via `cache_root_for(alias)`, a SIBLING directory
  for a non-default alias. `via: code`
- **Ruled out:** "this is live today" — `claude/cairn-routes.json` maps every scope to
  `personal`, so there is exactly one instance and the two roots coincide. Inert right
  now. `via: code`
- **Ruled out:** "#1738 introduced it" — the same spelling is on `main` at
  `subsystem-index/SKILL.md:164` from before this PR. Pre-existing. `via: command`
- **Leading hypothesis:** genuinely latent, and it becomes live the moment a second
  instance exists — which is what the separately-claimed `cairn-oss-multi-instance-phase-c`
  work stands up.
- **Next probe:** on a host with two instances configured, `cairn sync && cairn-validate
  --scope <a-scope-routed-to-the-non-default-instance>` and check whether the validate
  reads the right cache root; read `cache_root_for` against `read_store_root` first.

## Next steps (ranked)

1. **The final commit `06d33a7a` carries NO independent audit.** Rounds 0–3 audited
   everything below `8241724e`; `06d33a7a` IS the fix for round 3's findings, so nothing
   but its own mutation sweep vouches for it. The sweep is real (10 mutants incl. a
   positive control, 3 re-run at the previous tip and watched to SURVIVE there) but it is
   not independent. If you want closure, `/audit-pr 1738 --round 4` over
   `8241724e..06d33a7a` — the PR is merged, so audit the range, not the PR.
   forcing: none

2. **`scripts/tests/test_tmux_reply_agent.py:1797` is a live ~1-in-60 flake that red-lights
   `tekton/devrc-pytests` on unrelated PRs.** It red-lit this one and cost a full
   discrimination cycle. `claude/RULES.md`: a permanently-red gate trains everyone to click
   through. Fix is to remove the timing dependency in `scripts/tmux-reply-agent:736-751`
   (bound the `#{pane_current_path}` re-read, or poll it) rather than re-running.
   Evidence and the discriminators are under Gotchas.
   forcing: gate — `tekton/devrc-pytests` went red on `#1738`'s head `a6b3e9fe` at
   2026-09-17T03:02:44Z for a defect unrelated to that diff; it will do so again.

3. **`_declared_verbs()` grades the skill against a router its readers do not run.**
   `scripts/tests/test_cairn_skill_verb_ledger.py` reads `scripts/cairn`'s argparse
   parser, but `cairn` on PATH is the pinned OSS package
   (`~/.local/bin/cairn` → `/nix/store/…-cairn-baee2f0/bin/cairn`, per `nix/home.nix:1535`).
   Pre-existing, flagged by round 1, out of that delta's range. Touches
   `scripts/tests/test_cairn_skill_verb_ledger.py`.
   forcing: none

4. **The multi-instance `cairn-validate` cache-root gap** — see the Open investigation
   above. Inert while `cairn-routes.json` has one instance; becomes live with phase C.
   Touches `scripts/cairn-validate`, `claude/skills/{cairn,subsystem-index}/SKILL.md`.
   forcing: none

## Defects (batched)
- Round 2 nit, accepted: the literal `33 of 33 entry file(s)…` string now lives in
  `scripts/cairn-validate`, outside `test_index_append_protocol.py`'s carrier ledger,
  which scans `claude/skills/**/*.md` only. Deliberate scope, not an escape through it.
- Round 3 nit, accepted: `_Hit.block` indexes raw split segments including empty ones, so
  the number in a failure message is a split index rather than an ordinal paragraph.
- Round 3 nit, accepted: tempering on the literal `cairn` makes a <=40-char gap containing
  that lowercase word invisible. No reachable instance — neither live store root contains it.

## Gotchas / decisions / dead-ends

- 🔴 **`mergeable: MERGEABLE` is a CONFLICT check, not a CI verdict.** The kickoff called
  `#1738` "open, MERGEABLE" and its CI was RED at the same moment. Read
  `mergeStateStatus` (`UNSTABLE` here) and the SHA-pinned statuses, never `mergeable`.

- 🔴 **devrc `main` has NO `required_status_checks`** — `gh api
  repos/innovation-upstream/devrc/branches/main/protection` returns no such key, so CI does
  not mechanically gate a merge. This independently corroborates open PR `#1452`'s claim.
  A red check here blocks nothing; understand it rather than clicking through it.

- 🔴 **The `test_tmux_reply_agent.py:1797` flake — the full discrimination, so nobody
  re-derives it.** Failing assertion is the CONTROL half of
  `test_a_cwd_that_does_not_exist_is_REFUSED_not_opened_in_HOME`:
  `tmux did not report the new window's directory`. Three independent legs proved it is
  NOT attributable to a diff: (a) `scripts/tmux-reply-agent` and
  `scripts/tests/test_tmux_reply_agent.py` are **byte-identical blobs** at base and head
  (`82bc5128…` / `0903ea03…`); (b) watched it FAIL at `main` `b03551f9` locally — **1 of 60**
  runs, 0 of 20 at the PR head; (c) a CI re-run at `979990f8` went fully green with the
  identical test present. Mechanism is documented in the code it fails in
  (`scripts/tmux-reply-agent:736-751`): `#{pane_current_path}` is empty until the pane's
  process cwd is readable, one re-read then a hard refusal; a sibling race four lines up is
  annotated *"3 occurrences in ~12 runs"*. The fixture is safe to run locally — private
  `-L` socket and private `TMUX_TMPDIR`, it never touches the operator's server.

- 🔴 **A mutation battery that restores with `git checkout -- <dir>` DESTROYS uncommitted
  work in that dir, silently.** Hit here: the round-3 battery's `restore()` wiped a
  just-made edit to `claude/skills/subsystem-index/SKILL.md`, and the commit went out
  claiming a six-file sweep with five files in it. Caught only by diffing the commit before
  pushing. **Save with `cp -a` and restore by copying back**; the round-4 battery does, and
  diffs the file identical at the end. `claude/RULES.md` already bans `git stash` for the
  repo-global reason — this is the same class one step over.

- 🔴 **A measurement you observed and did not write down gets written down WRONG.** The
  `0 bytes to stdout` claim was propagated into a commit *after* `cairn validate --scope
  devrc` had been run in the same session and seen to print 55 B. Re-measured 2026-09-17,
  scope `devrc`, pinned rev `ZacxDev/cairn baee2f0`:
  `cairn validate --no-sync --scope devrc` → rc 0, stdout **55 B**, stderr 76 B;
  `cairn-validate --scope devrc` → rc 0, stdout **6,677 B** with `entry shape:`,
  `marker reachability:` and `dropped lines:`. The 0 was true before the pinned rev added an
  unconditional summary line to `cmd_validate`. **Corrected in all SIX carriers** —
  `claude/skills/{cairn,subsystem-index}/SKILL.md`, `scripts/cairn-validate`,
  `nix/home.nix:1558`, `scripts/tests/{test_cairn_skill_verb_ledger,test_subsystem_touch}.py`
  — each with a retraction note. The discriminator is WHAT each reports, not whether either
  is silent: `dropped lines:` has **no counterpart** in the package's output.

- 🔴 **`cairn validate --scope <scope>` is a REAL runnable command that even SYNCS by
  default** (`--no-sync` exists to skip it). That is what makes the binary swap survivable by
  eye, and it is why property (4) pins the invocation. Their exit tables also disagree:
  malformed entry is **3** from the writer, **5** from the package, whose own 3 means
  "unreachable, no cache".

- 🔴 **A keyword heuristic for "this is a counter-example" was walked SIX times across two
  audit rounds** — `❌ Do not skip it. Run <X>`, a `## ❌ Common mistakes` heading, a marker in
  the previous paragraph, then any non-period clause joiner (`— run …`, `: run …`), a tight
  list item, and a false positive from `e.g.`/`v1.2`. Each fix was a narrower guess about
  English. Replaced with the sentinel `<!-- not-a-mandate -->`: ordinary prose cannot produce
  it, it is invisible when rendered, and every exemption is greppable. `claude/RULES.md`
  called this in advance — *prefer deterministic/structural fixes over suffix/keyword
  heuristics.*

- 🔴 **A guard whose decision logic the CORPUS never exercises has no coverage, and a green
  suite cannot tell you.** Round 3 F1: the block split, the sentinel window and the `cairn`
  tempering could EACH be deleted with all 16 tests green — 7 surviving mutants against a
  positive control that did go red. Cause: no governed file contains a marker, so that branch
  never ran. Remedy is fixtures driving the scanner over SYNTHETIC strings
  (`TestTheOccurrenceScanner`, 18 cases), because the corpus cannot be relied on to keep
  containing the shapes.

- **A mutant that dies for the WRONG reason proves nothing.** The N-2 fence mutant tripped
  the COUNT test as a side effect at the previous tip, so it could not isolate property (4).
  The clean discriminator was probing `_VALIDATE_RE` directly:
  `"cairn-validate --validate F cairn validate --scope <scope>"` → 1 match `sep='-'` at
  `8241724e` (property 4 green, packaged client mandated), `sep=' '` after.

- **`audit-dispatch.py` REFUSES `--round 0 --emit-claims`** — "round 0 has no fixes to
  claim". Round 0 runs before any fix exists; a `round=0` block would let the next delta
  anchor on a tip that fixed nothing. Record round 0's verdict as prose; the claims block
  starts at the round that first FIXES something.

- **No PR comment was posted.** `innovation-upstream/devrc` is PUBLIC and the operator
  authorised an audit-then-merge, not a public post; the four commit messages carry the full
  record instead. That is why there is no `audit-claims` block on `#1738` — a round-4 delta
  would need a hand-assembled range, as rounds 1–3 did.

- **A full local `scripts/gate.sh --tier pytest` TIMED OUT at 3600s under concurrent load**
  (exit 124 → SIGTERM 143) and must not be read as a pass. Prefer targeted `python3 -m pytest
  <files>`; CI's own full run is the tier-wide evidence.

- **This session's pytest interpreter:**
  `/nix/store/pnjqar943wpzw1r76whl20cm646jc125-python3-3.12.14-env/bin/python3`, always with
  `PYTHONDONTWRITEBYTECODE=1` for mutation work (a same-length edit landing in the same second
  is invisible to CPython's bytecode cache).

- **`.envrc` is GITIGNORED in devrc** — a fresh worktree has no dev shell. Unlike
  `datapacket-talos`, where it is tracked. Use the nix store interpreter directly.

## How to verify
```bash
DEVRC=/home/zach/workspace/devrc
PY=/nix/store/pnjqar943wpzw1r76whl20cm646jc125-python3-3.12.14-env/bin/python3

# 1. the arc's closing condition — all three halves
git -C "$DEVRC" fetch origin main
git -C "$DEVRC" cat-file -t d45612d5                                    # expect: commit
git -C "$DEVRC" show origin/main:claude/skills/cairn/SKILL.md \
  | grep -c 'cairn sync && cairn-validate'                              # expect: 2
git -C "$DEVRC" worktree add --detach /tmp/vfy origin/main
(cd /tmp/vfy && PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest \
  scripts/tests/test_cairn_skill_verb_ledger.py -q)                     # expect: 34 passed

# 2. 🔴 the guard is not vacuous — break it and watch THIS guard's own assertion fail.
#    A green suite says nothing until one of these has been seen red.
(cd /tmp/vfy && sed -i 's/for i, block in enumerate(_BLOCK_BREAK.split(text)):/for i, block in enumerate([text]):/' \
   scripts/tests/test_cairn_skill_verb_ledger.py \
 && PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest scripts/tests/test_cairn_skill_verb_ledger.py -q \
    | tail -3)                                                          # expect: 3 failed
git -C /tmp/vfy checkout -- scripts/tests/test_cairn_skill_verb_ledger.py

# 3. the derived set is what the ledgers declare (36 tracked SKILL.md, 5 invocations)
(cd /tmp/vfy && PYTHONDONTWRITEBYTECODE=1 "$PY" -c "
import importlib.util
s=importlib.util.spec_from_file_location('m','scripts/tests/test_cairn_skill_verb_ledger.py')
m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
print('scanned', len(m._skill_bodies()))
for k,v in sorted(m._naming_skills().items()): print(' ', k, len(v))
")   # expect: scanned 36; cairn 2, resume 1, subsystem-index 2

# 4. the two clients still differ in WHAT they report (the property-4 rationale)
cairn validate --no-sync --scope devrc | wc -c      # ~55 B, one summary line
cairn-validate --scope devrc | grep -c 'dropped lines:'   # >= 1; absent from the package

git -C "$DEVRC" worktree remove --force /tmp/vfy
```
