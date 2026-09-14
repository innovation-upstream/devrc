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
- Branch / PR: workbench checkout was on `main`, in sync with origin/main; this doc + the 13 staged files are on branch `feat/the-algorithm-skill` (staged work UNCOMMITTED; doc committed separately by the handoff gate). PR: not yet opened.
- DONE this session:
  - Measured the over-guarding (all live 2026-09-14): last 6w = 1,326 non-merge commits, 407 guard (31%), 287 touching NO product file, guard churn +352k lines; 268 of 408 test files added in 6w; 414k test LOC vs 126k product LOC (3.3:1); ~30 of 251 `scripts/tests/` files are meta-guards (doc ceilings, prose pins, mutation batteries for ceilings).
  - Telemetry (Claude transcripts, devrc, 14d): 97 sessions, 21.5B cache-read tokens; **41.2% of 20,913 Bash calls are test/gate runs** (8,616); 32% of edits touch guard files. Fleet 14d: 495 sessions, 81.9B cache-read, 681 AskUserQuestion, 91 interruptions.
  - Shipped `claude/skills/the-algorithm/SKILL.md` (tier A in `claude/skill-tiers.json`, two-way pin satisfied).
  - Paid the two listing ratchets per their printed playbooks: mechanism prose cut from 9 descriptions (browser, i3, opencode, signal, tekton, clickup, prune-index, session-manager, initiative-scan — zero trigger phrases or disambiguation clauses touched); `LISTING_TOTAL_CEILING_CHARS` re-pinned 11,170→10,832 in `scripts/tests/test_skill_descriptions.py`; `MEASURED_*` constants in `scripts/tests/test_skill_tiers.py` copied from the failure's printed values (36 / 23 / 7,510 / 7,695 / 11,011).
  - Workbench deployed via `home-manager switch --impure` — VERIFIED live: `readlink -f ~/.claude/skills/the-algorithm/SKILL.md` → `/nix/store/mygfci9zjv14z4g868kyi9dip2324df2-devrc-claude-skills/the-algorithm/SKILL.md`; `~/.config/opencode/commands/the-algorithm.md` generated.
- IN FLIGHT: the 13 staged files (skill + ledger + 9 description cuts + 2 test re-pins) — UNCOMMITTED, branch `feat/the-algorithm-skill`. Laptop NOT converged (post-merge `ship.sh`). `sync-skill-tiers.py` NOT applied on any host (operator act, dry-run default; drift-check reports NOT ADOPTED, no rc, until applied).
- Verify status (honest): ledger gates 61/61 green; corpus guards 297/297; census run 401 passed with 1 PRE-EXISTING red (client-hostname leak below — unrelated to this diff). Full scoped sweep abandoned >15 min under box load — NOT run to completion; no full-suite pass is claimed.

## Open investigations — live diagnosis state

### test_no_client_hostnames red: client subdomain committed to this PUBLIC repo (as-of 2026-09-14)
- as-of: 2026-09-14
- **Symptom + exact repro:** `nix develop ~/workspace/devrc -c python3 -m pytest scripts/tests/test_no_client_hostnames.py::test_no_client_subdomain_literal_is_committed -q` → FAILED; blocks `scoped-tests.sh`'s ledger-check (`STOPPED at the repo-census guards`, rc 1).
- **Observed (with values):** assertion names `claudedocs/handoff-cairn-oss-multi-instance.md:778: cairn.civitai.com` — "a CLIENT subdomain is committed to a PUBLIC repo — this is internal topology"; the line reads "from day one**; **`cairn.civitai.com`, CF-proxied**; **Zach the sole token admin for now**".
- **Ruled out:** this session's diff — the named file is not among the 13 staged paths and the failure predates them. via: code
- **Leading hypothesis:** a prior cairn session committed the real client hostname into the doc prose.
- **Next probe:** scrub to a `*.example.test` form in the doc, then `git -C ~/workspace/devrc log --all --oneline -S 'cairn.civitai.com'` to size history exposure — the content gates read `git ls-files` and are BLIND to git history (see `SECRETS.md` → "Dead credentials in reachable history"); if the hostname is in reachable history, history-rewrite-vs-accept is an operator call.

### laptop/opencode DEAD (as-of 2026-09-14)
- as-of: 2026-09-14
- **Symptom + exact repro:** `python3 ~/workspace/devrc/scripts/collector/deadman.py` → exit 1, row `laptop opencode baseline=35 p99gap=83 budget_h=13.8 silent_h=37.3 DEAD`.
- **Observed (with values):** last laptop opencode row in ClickHouse = `2026-09-09 16:50:25` UTC (14d `max(ts)` query); all other 9 laptop pairs ok in the same run; fleet-wide newest event 2 min old.
- **Ruled out:** a general laptop telemetry outage — every other laptop pair measured ok in the same deadman run. via: measurement
- **Leading hypothesis:** laptop opencode genuinely stopped (plugin/tailer) or has simply been unused since 09-09 — budgets are ACTIVE-time, so 37.3h of active silence is a long real absence either way.
- **Next probe:** `ssh zach@10.42.0.100 "systemctl --user list-units --all 'opencode*' --no-pager; journalctl --user -u 'opencode*' -n 30 --no-pager; ls -la ~/.local/state/activity/spool/ | tail -5"`

## Next steps (ranked)
1. Branch + PR + merge the 13 staged files (the-algorithm skill, ledger entry, 9 description cuts, 2 test re-pins), then `scripts/ship.sh`. IN FLIGHT: devrc branch `feat/the-algorithm-skill` (staged, uncommitted).
   forcing: user
2. Scrub `cairn.civitai.com` from `claudedocs/handoff-cairn-oss-multi-instance.md` and size history exposure (investigation above).
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
