# Handoff: object-leak-guard — 2026-08-25

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
Stop agent-created objects (ClickUp tasks, GitHub issues, PRs) accumulating unclosable.
The arc is named **`object-leak`**; the rule it produced is the **closing-condition rule**.
Full measurement record: `civitai/talos-infra:claudedocs/agent-object-leak-2026-08-23.md`.

## State now
- Branch/PR in flight: **devrc #821** `zach/gh-issue-closing-condition-guard`, head `6e7aa7a3`, OPEN, gate re-running.
- Worktree: `/tmp/wt-821fix` (do not delete — it holds the verified tree).
- **Merged today, all verified by content on their default branches:**
  talos-infra #1277 (stamp 3 CronJob producers), #1286 (Python `manual:<who>`), #1292 (producer lookup fixes);
  devrc #768 (ClickUp stamping), #772 (the rule), #786 (single definition + guard), #795 (query.mjs regression), #803 (`manual:<who>` + task-hygiene flow);
  civitai #4333 (`CLAUDE.md` § Filing follow-up work).
- **Live and behaviourally proven:** the CronJob stamping — talos-infra issue **#176** carries a real `claw:obj` marker written by a real job run.
- **Live but unproven:** the closing-condition rule reaches every session that loads it.
- **Not live:** the guard (#821) — it only takes effect on the next `home-manager switch`.
- Deploy honesty: talos-infra #1292 is Flux-reconciled and in the live ConfigMaps; its *behaviour* is unverified until the next producer run (capacity-sweep 08:00Z daily, reliability weekly Mon).

## Open investigations — live diagnosis state

### #821's crash-path test is vacuous — the fail-closed backstop is unexercised
- **Symptom + exact repro:** `test_the_crash_path_denies_a_create_shape` never runs `main()`; its final assertion is `assert payload`, a truthiness check on a non-empty JSON string, which cannot fail.
- **Observed (with values):** an `if False:` mutant on the crash path **SURVIVED all 251 tests**. Its own comment claims driving the import failure "is not possible from here".
- **Ruled out:** that it is merely weak — it is unreachable-as-coverage. The audit reproduced the surviving mutant independently.
- **Leading hypothesis:** the whole-hook fail-closed backstop has never been executed.
- **Next probe:** copy the hook into a tmp dir beside a `guard_core.py` that raises on import, run it as a subprocess with a real PreToolUse payload, assert a `deny` is emitted.

### Everything else the audit raised on #821 is CLOSED — verified hygienically
- **Observed (with values):** driving the real hook with per-run `mktemp` fixtures, **13/14 + 5/5** correct. B1 (override armed from inside the body) DENY ×2; B2 (unrelated heredoc rescues an unseeable body) DENY ×2; B3 (two creates, second unstated) DENY ×4 incl. the `$(cat <<'EOF' …)` spelling; B4 curl `--flag=value` DENY; B5 `--type` before the verb DENY; B7 `-f body=@path` DENY; B8 literal `\n` DENY; O1 attached `-b<good>` ALLOW; O2 body starting with `@` ALLOW. Suite **383 passed / 0 failed**.
- **Ruled out:** every "still broken" reading from earlier in the session — see Gotchas, they were all fixture defects.

## Next steps (ranked)
1. **Make the crash-path test real** (`devrc`, `scripts/claude-hooks/tests/test_gh_issue_closing_condition_guard.py`) — the only known real gap on #821. **IN FLIGHT: devrc#821**
2. **Delta re-audit #821** against `6e7aa7a3`, then merge. Given this session's record, treat the audit as the authority over ad-hoc probes. **IN FLIGHT: devrc#821**
3. After merge: `home-manager switch`, then verify the guard on the **deployed** `/nix/store` copy, not the checkout (`readlink -f` is the arbiter).
4. **Verify the #1292 producer fix behaviourally** (`talos-infra`): after the next reliability-sweep run, `agent/reliability-sweep-rightsize` should appear on issue **#176** for the first time — one query settles it.
5. The 30-day re-measure is clawgate task **#352** (2026-09-23). Do not run early; its comments carry the baseline and a discriminator to run first.

## Gotchas / decisions / dead-ends
- 🔴 **FIVE consecutive wrong conclusions this session, all from probe hygiene, none from the code.** Ad-hoc probes wrote fixtures to FIXED paths in shared `/tmp` (`/tmp/plan.md`, `/tmp/good-body-821.md`), so a leftover file was read by `--body-file` resolution and answered the guard's question. Also: a fixture using a literal `\n` — the exact over-acceptance B8 closes — made a correct DENY read as an ALLOW regression; and a B3 fixture gave *both* creates a good body while labelled "2nd unstated". **Every fixture goes in a per-run `mktemp -d`, and verify the fixture encodes the case its label claims.** The guard's own suite already does this (`/tmp/ghccg-test-<random>/`).
- 🔴 **A subagent that finishes without emitting a report is not a stuck subagent.** The #821 fix agent notified three times with `"Waiting."` and was stopped as looping; it had in fact completed every fix. Check the tree before believing the silence.
- **Subagents DO inherit `~/.claude/RULES.md`.** An earlier claim that they do not was a false zero: **transcripts do not record the system prompt**, so grepping them for rule text can never find it. Do not repeat that measurement.
- **The failure is salience, not delivery.** Blind test, private solo repos so the outward-facing rule could not confound: unbriefed subagent filed **6 issues / 0 closing conditions**; the same rule pasted into the brief gave **10/10**. Both had the rule.
- Semantic compliance is weaker than form compliance: in the briefed arm all 10 had the heading, most wrote the *remedy* under it rather than an end-state.
- **Duplication was never the problem** (~2% GitHub, 2.6% ClickUp, zero exact duplicate titles). A dedup-first design was drafted and cut on the measurement.
- `bash-guard.py` matches raw command text and cannot tell quoting from executing — it blocked a `grep` whose *pattern* contained a blind-stage command. Write such strings to a file with the Write tool.
- Not done deliberately: no stamping wrapper for session-filed `gh issue create` (the guard supersedes the need); MetaAgent is not ours to tune (no producer in any repo we control); CI node capacity is tracked as talos-infra **#1205**.

## How to verify
```bash
# the guard, end-to-end, with hygienic fixtures (per-run mktemp):
python3 <scratchpad>/probe-clean.py /tmp/wt-821fix/scripts/claude-hooks/gh-issue-closing-condition-guard.py
python3 <scratchpad>/probe-b3.py    /tmp/wt-821fix/scripts/claude-hooks/gh-issue-closing-condition-guard.py
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  /tmp/wt-821fix/scripts/claude-hooks/tests/test_gh_issue_closing_condition_guard.py -q -p no:cacheprovider
# expect: 13/14 + 5/5 correct, 383 passed. Clear /tmp fixtures first or you will measure your own leftovers.
```
