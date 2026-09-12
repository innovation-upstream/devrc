# Handoff: mention-picker-instrumentation-and-tui — 2026-09-12

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
The mention picker (`scripts/mention-open.py`) shipped its plausibility ranking in #1509,
but nothing measures whether the ranking helps and the operator's usage is not being
recorded. Four operator asks: (1) record picks that are currently dropped, (2) center the
picker window, (3) ship click telemetry to `activity.events`, (4) open PRs in a TUI
instead of a browser.

## State now
- Branch: `main`, clean, synced with `origin/main` (this is a SHARED checkout — another
  session fast-forwarded it mid-session, from `b42ac7c3` to `db7bf3ff`).
- **Nothing of this arc is merged yet.** Three agents dispatched, work in worktrees.
- **Four claims HELD** (release them when their item lands):
  `mention-pick-recording-autoopen` · `mention-picker-center` ·
  `mention-click-telemetry` · `mention-pr-tui-research`
- Branch pushed so far: `feat/mention-picker-center` (agent 2). Agent 1's branch not yet
  on origin as of this writing. No PR numbers yet.
- Deploy/verify status: **nothing deployed, nothing verified live.** No `home-manager
  switch` and no `ship.sh` was run this session.
- 🔴 `main` was RED on two `test_guard_core.py` kill-scanner tests for most of this
  session and is **now GREEN** — measured `2 passed` at `db7bf3ff`. See the gotcha below;
  the fix was NOT the one the previous handoff predicted.

## Open investigations — live diagnosis state

### Tier B pick-learning recorded almost nothing despite heavy operator use
- **Symptom + exact repro:** operator reports "many picks on workbench, as recently as a
  few minutes ago". Measured: `~/.config/mention-open/picks.jsonl` ABSENT on the
  workbench (0 rows loaded via `load_picks()`); the laptop has 7 rows.
- **Observed (with values):** `ls -la ~/.config/mention-open/` shows only
  `known_ranges.json` (12912 B), `known_repos.json` (18153 B), `known_universe.json`
  (11629 B), newest mtime `2026-09-11 20:32:34`. No picks file at any mtime.
  `XDG_CONFIG_HOME` is unset, so `PICKS_PATH` resolves to
  `/home/zach/.config/mention-open/picks.jsonl` — the directory exists and
  `os.access(..., W_OK)` is True.
- **Ruled out:** the deployed artifact is stale / predates #1509 — the hint command is
  `/nix/store/aj7rn7ci3vizlr0byq28l8ki2lc8rlmv-alacritty-mention-open`, which `exec`s
  `/home/zach/workspace/devrc/scripts/mention-open.py` (the WORKING TREE), and that file
  contains 13 occurrences of `record_pick`. via: command
- **Ruled out:** the write path is broken — `record_pick("innovation-upstream/devrc",
  "1291", path=<tmp>)` wrote `{"n": 1291, "repo": "innovation-upstream/devrc", "t": ...}`
  correctly. via: measurement
- **Ruled out:** an unwritable home silently swallowing an OSError — parent dir exists and
  is writable, measured above. via: measurement
- **Ruled out:** the URL gate rejecting everything — `repo_of_github_url` returns the repo
  for both `/pull/N` and `/issues/N` github.com URLs (and correctly `''` for clawgate and
  ClickUp URLs, which are not repos). via: measurement
- **ROOT CAUSE (found, not hypothesis):** `scripts/mention-open.py:2439` is
  `return open_url(candidates[0]["url"])` — the single-candidate AUTO-OPEN path, which
  returns WITHOUT calling `record_pick`. The only `record_pick` call is at `:2507`, on the
  picker-selection path. So every unambiguous click — the common, fast path — teaches
  Tier B nothing. Confirmed by `grep -n "return open_url"`: exactly three sites, and only
  the `:2508` one is preceded by a record. via: code
- **Next probe:** none needed for diagnosis; the fix is dispatched (agent 1). The
  open question is now a MEASUREMENT one: whether the #1509 ranking actually helps, which
  cannot be answered until rank+class are recorded at pick time.

## Next steps (ranked)
1. **Land agent 1's instrumentation PR** — records BOTH paths tagged `auto`/`picker`,
   plus rank / plausibility class / total-offered, plus the `activity.events` emit.
   Files: `scripts/mention-open.py`, `scripts/collector/mention_scan.py`,
   `scripts/collector/emit/`, `scripts/tests/test_mention_open.py`,
   `scripts/tests/mutation_battery_mentions.py`. Repo: devrc.
   forcing: user — operator asked for instrumentation + telemetry directly this session.
2. **Land agent 2's PR on `feat/mention-picker-center`** — centers the picker float.
   Files: `nix/i3/config.nix`, `scripts/tests/test_i3_picker_centering.py`.
   IN FLIGHT: branch pushed, PR number not yet observed. Repo: devrc.
   forcing: user — operator reported the window opens pinned left.
3. **Act on the PR-TUI research** (agent 3, read-only) and decide whether to build.
   Scope is operator-set at FULL review AND merge. Repo: devrc.
   forcing: user — operator asked for a TUI instead of the browser.
4. **`ship.sh` once 1-3 land**, then re-verify the click path on the deployed wrapper.
   forcing: none
5. **Release the four claims** as each item lands: `claim-work --release <slug>`.
   forcing: none

## Gotchas / decisions / dead-ends
- 🔴 **A `record_pick` that exists is not a `record_pick` that runs.** The function, its
  tests, its constants and its docstrings were all correct and complete; the defect was a
  single missing call on the path that carries most of the traffic. "The feature is
  implemented" and "the feature observes anything" were independent claims, and only the
  live per-host file told them apart. A DTO field is not a guard; a defined function is
  not a code path.
- 🔴 **Do NOT add `move position center` to `nix/i3/config.nix:85`.** That rule is
  `for_window [class="float"] floating enable` and `class="float"` is shared by every
  other float in the system — centering there moves windows nobody asked to move. The
  picker spawns as `alacritty --class float,mention-open` (`PICKER_CLASS` at
  `mention-open.py:1282`), so the targeted discriminator is `instance="mention-open"`.
  The working precedent for centering is `config.nix:27` (the Yad "Rig Controls" rule).
- 🔴 **The telemetry privacy line, and it is already drawn in code.**
  `scripts/collector/mention_scan.py:180` makes the dedupe key
  `platform:raw[@owner/repo]` — so an owner/repo the operator MENTIONED already reaches
  the `activity.events` spool today. What must never reach it is the picker's OFFERED SET
  (the ~392-row universe in `known_universe.json`/`known_repos.json`), which holds private
  repo names and is the file behind the #1283 disclosure. **Emit what was touched, never
  what was offered.** Operator decision 2026-09-12: plain `owner/repo`, not hashed and not
  public-only.
- **Operator decision 2026-09-12, settled:** record auto-opens as pick signal but TAG them
  by path, so scoring can weight or exclude them later without losing the data.
- **Operator decision 2026-09-12, settled:** the PR TUI scope is FULL review AND merge.
  The blast radius of one-keystroke merge in a repo with no blocking gate was raised
  before the decision and accepted; the mitigation is a confirmation step on destructive
  verbs, not a narrower scope.
- 🔴 **Tier A (#1509 plausibility ranging) IS working — measured live, so nobody re-opens
  it.** Against the real 393-row `known_ranges.json` on the workbench: `#12` →
  68 PLAUSIBLE / 93 BELOW / 232 IMPOSSIBLE; `#1291` → 5 / 156 / 232; `#999999` →
  0 / 161 / 232. The candidate set genuinely collapses as N grows and degrades gracefully
  at the absurd end. ⚠ The `UNKNOWN=0` in that probe is an ARTIFACT of iterating the
  ranges table's own keys — every key has an entry by construction. The picker iterates
  the UNIVERSE, so that figure says nothing about the picker's row set.
- ⚠ **Ranking quality is still UNMEASURED.** Nothing records where in the offered list the
  chosen row sat, so "does the #1509 ordering help?" has no data behind it. That is why
  rank + class + total-offered were added to agent 1's brief after dispatch.
- **The laptop lacked `known_ranges.json`** because its
  `mention-known-repos-refresh.timer` last ran 2026-09-11 07:01, ~13h BEFORE #1509 merged
  at 20:28; next fire 07:01. Self-heals — not a defect, and not host drift.
- 🔴 **`main`'s kill-scanner red was fixed by a THIRD route, not the two PRs a previous
  handoff pointed at.** That doc framed it as needing `#1534` (rewords
  `handoff-tmux-webapp.md`) and `#1539` (drops the literal from its own text), each
  fixing one offender, neither greening `main` alone. What actually landed was
  `c0bbd6d9 fix(guard-core): stop the kill scanners reading claudedocs/ — SIX docs red-ed
  main in two hours and every fix was itself a doc`. Measured green at `db7bf3ff`
  (`2 passed`). **The lesson: every per-doc fix was itself a doc, so the fix rate could
  never catch the break rate — the scanner's SCOPE was the defect, not any document.**
  Do not re-derive the two-PR dependency; it is obsolete.
- **This session resolved no clawgate task** — `clawgate_handoff.sh resolve` exited 5
  (0 tasks). An unknown session id answers 200 with an empty array, so that is NOT
  evidence this session touched no task; no `clawgate-task:` field was written either way.
- ⚠ **This is a SHARED checkout and it moved under this session** — `origin/main` advanced
  from `622fc2d8` to `db7bf3ff` mid-session, and the working tree's one dirty file
  disappeared, by another session's action. Re-read `git status` before any write.

## How to verify
```bash
# Tier A ranging, headless, counts only — NEVER paste the rows (private repo names)
cd ~/workspace/devrc && nix develop . -c python3 -c '
import importlib.util,collections
s=importlib.util.spec_from_file_location("mo","scripts/mention-open.py")
mo=importlib.util.module_from_spec(s); s.loader.exec_module(mo)
r=mo.load_known_ranges(); print("rows",len(r))
for N in ("12","1291"):
    print(N, collections.Counter(mo.plausibility_class(N,r.get(k)) for k in r))'

# Tier B is recording (the whole point of this arc) — after agent 1 lands + a switch
wc -l < ~/.config/mention-open/picks.jsonl        # must GROW after clicking a mention
# and an auto-open must now appear, tagged, not only picker selections

# The two kill-scanner guards that were red (now green)
nix develop . -c python3 -m pytest scripts/claude-hooks/tests/test_guard_core.py \
  -k "every_kill_server_call_site_in_the_repo_is_classified or no_tracked_shell_text_writes_a_kill_this_guard_would_deny" -q

# Picker centering — RENDERED CONFIG ONLY, never raise a window
grep -n 'mention-open' nix/i3/config.nix

# Claims still held by this arc
claim-work --list | grep -E 'mention-(pick|picker|click|pr-tui)'
```
