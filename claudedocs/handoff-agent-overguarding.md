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
- ✅ **ARC COMPLETE — the goal's closing-condition is MET, all four clauses measured 2026-09-14.**
  #1699 (skill) `37b3bd07`, #1700 (scrub) `c48016dc` and #1705 (history determination) `e9f87f6e`
  are all MERGED; `ship.sh` converged BOTH hosts to `e9f87f6e` (no host skipped, "2 hosts
  compared"); `readlink -f ~/.claude/skills/the-algorithm/SKILL.md` terminates in `/nix/store`
  on both, same store hash; and `sync-skill-tiers.py` dry-run reports **`nothing to do — already
  in sync`** on BOTH hosts. The ledger IS APPLIED (13 overrides per host, backups written) —
  drift-check confirms `matches the ledger` on both and sets no rc 22.
  ⚠ Hosts converged to `e9f87f6e`, which was main's tip AT SHIP TIME; main has moved since.
  That is ordinary churn, not a failed ship — re-read `drift-check.sh` rather than this line.
  🔴 **Claude Code reads `settings.json` at STARTUP**, so the listing saving lands in NEW
  sessions only; a long-running session must be restarted before measuring it.
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

### ✅ CLOSED — laptop/opencode NO LONGER REPRODUCES (re-measured 2026-09-14, later same day)
- **Now:** `laptop opencode baseline=38 p99gap=332 budget_h=48.0 silent_h=2.0 **ok**`. The
  entry below recorded `silent_h=37.3 DEAD`; that was a hypothesis about 09-09, and the source
  has since produced events. **Nothing to diagnose — do not re-open it from the prose below.**
- **The lesson, which outlives the item:** this was ranked `forcing: incident` and would have
  been worked from the doc. Re-running the one-line repro cost seconds and deleted the task.
  Re-measure a remembered symptom BEFORE working it.
- (Historical, for shape only: symptom was `deadman.py` exit 1 with the laptop/opencode row DEAD
  at 37.3h active silence, last ClickHouse row `2026-09-09 16:50:25` UTC, every other laptop
  pair ok in the same run.)

### ⚠ DIAGNOSED, awaiting an OPERATOR MODEL DECISION — three WORKBENCH GUI sources read DEAD (2026-09-14, NEW — not the item above)
- as-of: 2026-09-14
- **Symptom + exact repro:** `python3 ~/workspace/devrc/scripts/collector/deadman.py` → exit 1,
  `rows=14788 evaluated=20 dead=3`.
- **Observed (with values):** `workbench/browser silent 28.8h`, `workbench/i3 silent 16.8h`,
  `workbench/keys silent 32.7h`, each against a 2.0h budget. Budgets are ACTIVE-time, so this
  is not idleness.
- **Ruled out — a host-wide workbench outage:** every NON-GUI workbench pair is ok in the SAME
  run (`claude` 0.1h, `tmux` 0.0h, `zsh` 0.1h, `browser-bridge` 0.0h, `mentions` 0.6h,
  `tool` 0.1h, `opencode` 0.0h). via: measurement
- **Ruled out — a defect in the three sources themselves:** the laptop's `browser`, `i3` and
  `keys` are all ok at 0.0h silence in the same run. via: measurement
- **Ruled out — this session's two `home-manager switch`es:** the silences (16.8–32.7h) predate
  the session. via: measurement
- 🔴 **DIAGNOSED — NOTHING IS BROKEN. This is ABSENCE, and the alarm is a MODEL defect.**
  An earlier draft of this entry guessed "whatever starts the workbench's GUI capture is not
  running". **That is REFUTED** — do not re-derive it.
- **Ruled out — the units are down:** all three are `active running` on the workbench.
  `keylog` restarted `2026-09-14 20:59:52 CDT` (that was a `home-manager switch` from THIS
  session — the `activity` skill documents that a switch restarts these daemons),
  `i3-source` up since `09-11 12:28:35` with no `i3 connection closed` since,
  `browser-activity-receiver` since `09-06`. via: measurement
- 🔴 **Ruled out — the emit path is broken. POSITIVE CONTROL, which is what makes this a
  diagnosis rather than a guess:** both `i3` and `browser` emitted AFTER the cliff — `i3`
  4 rows on 09-12, `browser` 2 rows on 09-11. source → spool → collector → ClickHouse
  demonstrably still works. ⚠ `keys` has **NO post-cliff positive control** (last row
  `09-09 20:03:49`), so its path is inferred from its siblings, not proven.
- **Observed — a clean cliff on 2026-09-09, all three at once** (daily row counts):
  through 09-09 `keys` 2,075/d · `i3` 1,097/d · `browser` 295/d; then 09-10 nothing,
  09-11 `browser` 2 + `i3` 5, 09-12 `i3` 4, nothing after.
- **Ruled out — a fleet-wide GUI-source defect:** the LAPTOP's `keys`/`i3`/`browser` are live
  right now (0 / 2 / 14 minutes ago). A human types on one keyboard at a time. via: measurement
- **Conclusion:** the operator moved to the laptop around 2026-09-09 and has not driven the
  workbench's X session since. The workbench's graphical stack is healthy and idle.
- 🔴 **So why does the deadman still convict it? Because workbench ACTIVE TIME keeps
  advancing without a human.** Silence is counted in active buckets, and
  `deadman.PRESENCE_SOURCES` = `keys` `i3` `tmux` `zsh`. On this box `tmux` (4,843 rows/3d)
  and `zsh` (211) are driven by the AGENT sessions running in tmux — so the workbench's
  human-presence clock runs at full speed with nobody there, and its three genuinely-idle GUI
  sources burn through their 2.0h budgets. **This is the 2026-08-11 failure mode in a narrower
  shape:** that fix converted presence to an allowlist and removed the obviously-agent sources
  (`claude`/`tool`/`opencode`/`browser-bridge`), but `tmux` and `zsh` remain agent-drivable and
  were left in.
- 🔴 **DO NOT "fix" this by widening a budget or adding an exception.** The `activity` skill is
  explicit that nothing in this checker is hand-listed and that an exception table is precisely
  what it exists to avoid — and `the-algorithm` says the fix for over-guarding is never another
  guard. The open question is a MODEL one and is the operator's: should a bucket count as
  human-present only when marked by a source an agent CANNOT drive? Answering yes costs
  detection latency on a box where the human genuinely is present, which is the trade the
  skill's COST section already documents.
- **Until it is answered:** the deadman stays rc 1 and the `tlm` pill stays red on a fleet where
  nothing is wrong — a standing false alarm, which is its own cost.

## Next steps (ranked)
1. ✅ **DONE — #1699 MERGED (`37b3bd07`) and SHIPPED to both hosts.**
   ⚠ The trap it produced, which outlives the item: the skill file sat `AM`, so `git commit`
   wrote the INDEX's 430-char description while the 61-passed gate run read the WORKING TREE's
   336-char one. That first commit was RED (3 failures, proven in a detached worktree at that
   sha) against a 0-headroom ceiling. **A green gate run describes the tree pytest read —
   check `git status` for an `AM` before quoting it for a commit.** Fixed in `068120f9`.
2. ✅ **DONE — #1700 MERGED (`c48016dc`).** Client subdomain scrubbed from
   `handoff-cairn-oss-multi-instance.md`; 0 occurrences on `main`.
   The history half is also CLOSED, as a decision rather than a task: **#1705 (`e9f87f6e`)**
   records the operator's determination in `SECRETS.md` — ACCEPT, do not rewrite (topology not
   a credential; one commit reachable from `main`, hours old; a rewrite would force-push a
   public repo that is also a two-host deploy target and would unpublish nothing).
3. ✅ **CLOSED — laptop/opencode no longer reproduces** (now `ok`, 2.0h silent / 48h budget).
   **SUPERSEDED BY A DIFFERENT INCIDENT:** the deadman is still rc 1, for three NEW workbench
   GUI sources — see the OPEN investigation above. Do not read "deadman rc 1" as this item.
   forcing: incident
4. ✅ **DONE — ledger APPLIED on both hosts** (13 overrides each, backups written). Dry-run now
   reports `nothing to do — already in sync` on both; drift-check says `matches the ledger`,
   no rc 22. Hosts no longer pay full descriptions — in NEW sessions (startup-read).
5. Algorithm step-2 deletions (now owned by the skill, per-change, not bulk): prose-pinning tests (`test_ci_claim_matches_reality`, `test_doc_path_rot`'s 310-path pin), mutation batteries for meta-guards, doc-size ceilings except RULES.md's, per-target floors the scoped runner already suspends. Expect ≥10% add-back (keep RULES.md ceiling + gate exit-truthfulness).
   ⚠ **This is a POSTURE, not a queued task** — the skill applies it per-change. Do not work it
   as a bulk sweep; that is the failure mode the skill exists to replace.
   forcing: none
6. 🔴 **NEW — `drift-check.sh` rc 17: the laptop builds a STALE `clawgatectl`.**
   `homelab-talos/containers/clawgate` is 8 commits behind `origin/trunk` on the laptop
   (repo-wide 68 behind); the workbench is clean. `nix/pkgs` builds from that SUBTREE and
   **`ship.sh` is scoped to `~/workspace/devrc`, so it structurally cannot converge this.**
   **Closes when** that subtree is at parity with its own upstream on the laptop AND a
   `home-manager switch` has run there, verified by `drift-check.sh` no longer emitting rc 17
   for that scope. Not done here: it means pulling another repo's working tree on a host
   nobody is driving, which can collide with uncommitted work there.
   forcing: none
7. ⚠ **`main` has NO green authoritative verdict since `60b89766`** — every commit after it is
   `superseded by a newer run` or `pending`. My merges introduced no failure (`37b3bd07`'s own
   verdict was `failed=0` — the pre-existing `test_guard_core.py` drift ceiling that #1704 then
   fixed). **"main is green" is UNPROVEN, not established.** The 4-hourly `main-green-check`
   deadman is what settles it; do not assert main's health from this doc.
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
- Deadman: `python3 ~/workspace/devrc/scripts/collector/deadman.py` → rc 1 with **three
  WORKBENCH GUI sources** (`browser`, `i3`, `keys`) DEAD until that item closes; rc 0 is the
  all-clear. `laptop/opencode` is now `ok` and is NOT what the rc 1 means any more.
  🔴 **Capture the rc directly — `deadman.py | tail; echo $?` reports TAIL's status, not the
  deadman's.** That trap read `rc=0` over a `dead=3` table in this very session. Redirect to a
  file and read `$?`, or read the `dead=N` count in the header.
- Ledger adoption: `python3 ~/workspace/devrc/scripts/sync-skill-tiers.py` (dry-run) → `nothing
  to do — already in sync` on both hosts. This is the goal's 4th closing-condition clause, and
  it must be checked with THIS command — `drift-check.sh`'s rc 22 is a different instrument
  agreeing, not the same claim.
