# Handoff: agent-overguarding — 2026-09-14

## Run this first — the index, one command
```bash
cairn recall --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading. Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Apply the 5-step algorithm (question requirements → delete → simplify → accelerate → automate) to devrc's measured guard over-engineering, and ship `the-algorithm` as an in-the-moment skill so new guards/tests get challenged at creation instead of by bulk retroactive sweeps.
- **closing-condition:** `check` — the PR carrying this work is merged, `scripts/ship.sh` has converged BOTH hosts, and on both hosts `readlink -f ~/.claude/skills/the-algorithm/SKILL.md` terminates in `/nix/store` AND `scripts/sync-skill-tiers.py` (dry-run) reports no diff.

## State now
- Branch / PR: **`feat/the-algorithm-skill` → PR #1699 OPEN** (this doc + the 13 files, committed and pushed). Sibling security PR: **#1700** on `fix/scrub-client-subdomain`, off `main`, one file.
- DONE this session:
  - Measured the over-guarding (all live 2026-09-14): last 6w = 1,326 non-merge commits, 407 guard (31%), 287 touching NO product file, guard churn +352k lines; 268 of 408 test files added in 6w; 414k test LOC vs 126k product LOC (3.3:1); ~30 of 251 `scripts/tests/` files are meta-guards (doc ceilings, prose pins, mutation batteries for ceilings).
  - Telemetry (Claude transcripts, devrc, 14d): 97 sessions, 21.5B cache-read tokens; **41.2% of 20,913 Bash calls are test/gate runs** (8,616); 32% of edits touch guard files. Fleet 14d: 495 sessions, 81.9B cache-read, 681 AskUserQuestion, 91 interruptions.
  - Shipped `claude/skills/the-algorithm/SKILL.md` (tier A in `claude/skill-tiers.json`, two-way pin satisfied).
  - Paid the two listing ratchets per their printed playbooks: mechanism prose cut from 9 descriptions (browser, i3, opencode, signal, tekton, clickup, prune-index, session-manager, initiative-scan — zero trigger phrases or disambiguation clauses touched); `LISTING_TOTAL_CEILING_CHARS` re-pinned 11,170→10,832 in `scripts/tests/test_skill_descriptions.py`; `MEASURED_*` constants in `scripts/tests/test_skill_tiers.py` copied from the failure's printed values (36 / 23 / 7,510 / 7,695 / 11,011).
  - Workbench deployed via `home-manager switch --impure` — VERIFIED live: `readlink -f ~/.claude/skills/the-algorithm/SKILL.md` → `/nix/store/mygfci9zjv14z4g868kyi9dip2324df2-devrc-claude-skills/the-algorithm/SKILL.md`; `~/.config/opencode/commands/the-algorithm.md` generated.
- IN FLIGHT: **two open PRs awaiting merge — #1699 (the skill) and #1700 (the scrub)**; nothing uncommitted. Laptop NOT converged (post-merge `ship.sh`). `sync-skill-tiers.py` NOT applied on any host (operator act, dry-run default; drift-check reports NOT ADOPTED, no rc, until applied).
- Verify status (honest): ledger gates 61/61 green; corpus guards 297/297; census run 401 passed with 1 PRE-EXISTING red (client-hostname leak below — unrelated to this diff). Full scoped sweep abandoned >15 min under box load — NOT run to completion; no full-suite pass is claimed.

## Open investigations — live diagnosis state

### ✅ CLOSED 2026-09-14 — client subdomain scrubbed from HEAD; history exposure sized and left to the operator
- as-of: 2026-09-14
- 🔴 **DO NOT SPELL THE HOSTNAME IN THIS DOC.** The first version of this entry quoted the
  literal three times while documenting the leak, which re-committed the same client topology
  to the same public repo — and would have carried it to `main` through the PR that closes
  rank 1. Refer to it as "the client subdomain"; the scan matches prose, not just code.
- **Was:** `test_no_client_hostnames::test_no_client_subdomain_literal_is_committed` FAILED on
  `main` — a client subdomain was committed to this PUBLIC repo at
  `claudedocs/handoff-cairn-oss-multi-instance.md:778`, where it recorded an operator answer.
  It also blocked `scoped-tests.sh` at its repo-census stage (rc 1) on every branch.
- **Fixed by:** PR #1700 — the guard's own playbook: a decision record that nothing in tracked
  source opens, so the substance stays ("a DEDICATED subdomain on the client apex, CF-proxied")
  and the literal goes. Controls run rather than assumed: scanner on `HEAD` content → 1 hit,
  on the scrubbed tree → 0. `test_no_client_hostnames.py` + `test_handoff_doc_size.py` 27 passed.
- 🔴 **STILL OPEN — operator call, not a defect:** the literal remains in REACHABLE HISTORY.
  Exactly one carrying commit is an ancestor of `origin/main` (`38cb5d86`, landed 2026-09-14,
  so the exposure is hours old, not years); `bac48ff0` and `b34c3372` are on unmerged branches.
  All four content gates enumerate `git ls-files` and are structurally blind to history
  (`SECRETS.md` → "Dead credentials in reachable history"), so the scrub stops the leak
  GROWING and does not remove it. History-rewrite-vs-accept on a public repo whose `main` is
  also a deploy target has NOT been decided.

### laptop/opencode DEAD (as-of 2026-09-14)
- as-of: 2026-09-14
- **Symptom + exact repro:** `python3 ~/workspace/devrc/scripts/collector/deadman.py` → exit 1, row `laptop opencode baseline=35 p99gap=83 budget_h=13.8 silent_h=37.3 DEAD`.
- **Observed (with values):** last laptop opencode row in ClickHouse = `2026-09-09 16:50:25` UTC (14d `max(ts)` query); all other 9 laptop pairs ok in the same run; fleet-wide newest event 2 min old.
- **Ruled out:** a general laptop telemetry outage — every other laptop pair measured ok in the same deadman run. via: measurement
- **Leading hypothesis:** laptop opencode genuinely stopped (plugin/tailer) or has simply been unused since 09-09 — budgets are ACTIVE-time, so 37.3h of active silence is a long real absence either way.
- **Next probe:** `ssh zach@10.42.0.100 "systemctl --user list-units --all 'opencode*' --no-pager; journalctl --user -u 'opencode*' -n 30 --no-pager; ls -la ~/.local/state/activity/spool/ | tail -5"`

## Next steps (ranked)
1. **PR #1699 OPEN** — the 13 files are committed and pushed on `feat/the-algorithm-skill`
   (the-algorithm skill, ledger entry, 9 description cuts, 2 test re-pins). Remaining: merge,
   then `scripts/ship.sh` to converge BOTH hosts (laptop has never been converged).
   ⚠ A follow-up commit on that branch fixes a trap worth knowing: the skill file sat `AM`, so
   `git commit` wrote the INDEX's 430-char description while the 61-passed gate run read the
   WORKING TREE's 336-char one. The first commit was red (3 failures, proven in a detached
   worktree at that sha) against a 0-headroom ceiling. **A green gate run describes the tree
   pytest read — check `git status` for an `AM` before quoting it for a commit.**
   forcing: user
2. ✅ **DONE — PR #1700 open.** Client subdomain scrubbed from `handoff-cairn-oss-multi-instance.md`
   (investigation above). History exposure sized: one commit reachable from `main`, hours old.
   The remaining half — rewrite vs accept — is an operator decision and is NOT a work item
   until someone makes it.
   forcing: security
3. Diagnose laptop/opencode DEAD (investigation above; deadman rc=1 measured 2026-09-14).
   forcing: incident
4. Apply the ledger to hosts: `scripts/sync-skill-tiers.py` (dry-run → `--apply`) on workbench + laptop, post-merge. Until then hosts pay full descriptions.
   forcing: none
5. Algorithm step-2 deletions (now owned by the skill, per-change, not bulk): prose-pinning tests (`test_ci_claim_matches_reality`, `test_doc_path_rot`'s 310-path pin), mutation batteries for meta-guards, doc-size ceilings except RULES.md's, per-target floors the scoped runner already suspends. Expect ≥10% add-back (keep RULES.md ceiling + gate exit-truthfulness).
   forcing: none

## Defects (batched)
- None this session. (The client-hostname leak is ranked #2 above as security-forced work, not a finding awaiting a batch.)

## Gotchas / decisions / dead-ends
- Demoting a skill to tier B does NOT move `LISTING_TOTAL_CEILING_CHARS` — that constant sums raw description text and never reads the ledger (`test_skill_descriptions.py:278-281`); it only moves `TIER_A_CEILING_CHARS` in `test_skill_tiers.py`. Both demotions tried for the eviction (check-clickup-addressed, civitai-app-fleet) were REVERTED.
- `civitai-app-fleet` must NOT be demoted: `test_skill_descriptions.py:288-306` records Zach's deliberate 2026-09-08 ceiling raise for it — symptom-routed on purpose ("why was my submit refused").
- Adding a skill reds BOTH ratchets by design (headroom 0 by choice); the failing test prints its own playbook — follow it, and copy the printed `MEASURED_*` values verbatim, never recomputed.
- Run `scoped-tests.sh` inside the devshell: `nix develop ~/workspace/devrc -c bash scripts/scoped-tests.sh` — a bare invocation died FATAL on missing `logrotate dash luajit`. Under box load the full scoped sweep can exceed 15 min; the census stage alone is ~4.
- clawgate `/handoff` resolve returned RC=5 (board reachable, 9-link positive control, 0 tasks for this session) — no `clawgate-task:` field recorded, per the tool's instruction.

## How to verify
- Skill live (workbench now, laptop post-ship): `readlink -f ~/.claude/skills/the-algorithm/SKILL.md` → `/nix/store/...`; opencode: `ls ~/.config/opencode/commands/the-algorithm.md`.
- Ledger gates: `nix develop ~/workspace/devrc -c python3 -m pytest scripts/tests/test_skill_descriptions.py scripts/tests/test_skill_tiers.py -q` → 61 passed.
- Corpus guards: `nix develop ~/workspace/devrc -c python3 -m pytest scripts/tests/test_doc_path_rot.py scripts/tests/test_skills_mapping_guard.py scripts/tests/test_no_captured_text.py scripts/tests/test_no_captured_markup.py -q` → 297 passed.
- Deadman: `python3 ~/workspace/devrc/scripts/collector/deadman.py` → rc 1 with `laptop/opencode` DEAD until that item closes; rc 0 is the all-clear.
