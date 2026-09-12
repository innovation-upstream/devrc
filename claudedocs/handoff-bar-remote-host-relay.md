# Handoff: bar-remote-host-relay — 2026-09-12

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
The operator sits at the laptop and SSHes to the workbench, so the on-screen i3 bar shows
the LAPTOP's state while the machine being worked on is invisible. Make the workbench's
state visible from the laptop.

## State now
🔴 **SHIPPED, LIVE AND VERIFIED ON BOTH HOSTS.** Not "deployed" — rendering on screen,
confirmed by screenshot.

- **Merged:** `innovation-upstream/devrc#1578`, squash **`ce4aee8e`** — verified by CONTENT on
  `origin/main` (a squash is never an ancestor). Branch deleted, claim `bar-remote-host-pill`
  released.
- **Shipped:** `LAPTOP_SSH=zach@10.42.0.100 scripts/ship.sh` → both hosts converged and
  **agreeing on one sha** `0e397c20` (that cross-host agreement is the claim that matters,
  not the per-host greens). 590/550 managed artifacts, **0 dangling, 0 absent, 0 stale** each.
- **Laptop bar RESTARTED** and rendering: PID `1532164 → 1532949`, parent `i3bar` (not an
  orphan), started 14:06:17 against a config written 14:01:29. Screen state recorded before
  and **restored after** — window `23068675`, workspace `1`, both verified equal.
- **Verified on screen** (screenshot of the live laptop bar): `wb 64.4` in the gold host
  colour (pango renders), `tlm 4`, `△30`, `△civ 35`, `234!11`; `mail` and `media` correctly
  INVISIBLE (hide-at-zero, measured and quiet); `🔔61` is the laptop's OWN notifs pill, not
  relayed.
- **Parity proven, not assumed:** all **6 of 6** global-service pills render BYTE-IDENTICAL
  on both hosts at the same moment — `clawgate 234!11|Critical`, `alerts 󰀪 29|Critical`,
  `telemetry tlm 4|Critical`, `mail |Idle`, `civitai 󰀪 civ 35|Warning`, `media |Idle`.
- **Pull is live:** `bar-remote-pull.timer` enabled AND active on the laptop; a forced run
  takes **3s**, rc 0, snapshot age 1s, 7 relayed blocks, 6 cache files installed.
- No clawgate task: `clawgate_handoff.sh resolve` → **rc 5 (NOTHING RESOLVED)**. An unknown
  session id answers 200 with an EMPTY ARRAY, so that zero cannot distinguish "touched no
  task" from "wrong id". **No field written; not a clean bill of health.**

### What it actually does
Blocks are split by **whose fact it is** (`RELAY_BLOCKS` / `NATIVE_BLOCKS` /
`LOCAL_ONLY_BLOCKS` in `scripts/bar-remote-snapshot`, pinned two-way against
`nix/graphical.nix`):
- **RELAY** (host-local: load, fans, airvpn, runaways, claude-runs, rigcontrol, scratchpads)
  travels in a snapshot pulled over nebula every 60s → one `wb` pill.
- **NATIVE** (global-service: clawgate, alerts, civitai, mail, telemetry, media) — the six
  blocks now deploy on BOTH hosts and the laptop renders them from a poller cache the same
  pull syncs. Same scripts, same bytes, same thresholds.
- **LOCAL_ONLY** (gamemode, notifs, and the `wb` pill itself).

## Next steps (ranked)
1. **Round 3 of the audit ladder on `#1578`.** The stop rule is findings-keyed and round 2
   returned findings that needed fixing, so round 3 is owed — and round 2's own fixes
   therefore **shipped unaudited** when the merge was authorised. This is not ceremony on
   this PR: round 1 found defects created by the feature, round 2 found defects created by
   round 1's fixes (a misplaced doc correction and a "made worse" schema guard). Delta range
   `381f7725..ce4aee8e`; round 2's `audit-claims` block is posted on the PR. Dispatch BLIND.
   forcing: gate — `audit-pr`'s stop rule: rounds continue while the previous round produced
   a finding that needed fixing; the first clean round is the last, and none has been clean.
2. **Decide the off-nebula behaviour.** Off nebula the laptop shows the six global pills as
   `?`/Warning plus `wb …?` — the mirror image of the permanently-red defect the design was
   reworked to remove. MEASURED with an empty cache. It is a knowing trade, it is named in
   `claude/skills/bar/SKILL.md`, and it was flagged three times without a decision. Options:
   accept (you genuinely cannot see those services off nebula), or hide the six when the peer
   has never been reachable.
   forcing: user — raised with the operator three times in-session; never answered.
3. **Click the `wb` pill once** and confirm the detail float opens. `remote-host-detail`
   renders correctly when invoked directly and the float pattern is the same one verified
   end-to-end for the scratch picker, but the click itself is unexercised. Opening a float is
   a screen action, so it was left to the operator.
   forcing: none
4. **Correct the stale rank-1 line in `claudedocs/handoff-tmux-scratchpad-bar-statusline.md`.**
   It says `drift-check.service` exits **17**; the last recorded exit is **16**, which
   CLAUDE.md defines as explicitly NOT drift ("ACTIONABLE (not drift)", the least severe code,
   only ever the verdict on an otherwise-clean run). Re-run before acting — this is a reading
   of the last recorded exit, not a fresh run.
   forcing: none

## Gotchas / decisions / dead-ends
- 🔴 **THE DESIGN'S CENTRAL IDEA: the gather runs the remote bar's OWN block commands, read
  verbatim out of its deployed `config-top.toml`.** Every red line already lives in two places
  (`graphical.nix` argv + the poller's toast env); re-deriving verdicts would make a third,
  and the one furthest from anyone's eyes. The verdict travels, the predicate does not — so
  the relayed pill CANNOT disagree with the bar it mirrors, and new blocks or retuned
  thresholds are picked up with no change here.
- 🔴 **THE ORIGINAL DESIGN WAS A CATEGORY ERROR, AND ROUND 0 CAUGHT IT.** The first version
  relayed all 15 custom blocks under a `wb` label. Six are not the workbench's facts at all —
  they are homelab/client-prod Alertmanager, the clawgate board, shared ClickHouse, a homelab
  qBittorrent pod — identical whichever host reads them. Because those backlogs are large and
  STANDING (measured: three Criticals, clawgate's stuck-toast latch unchanged for 24 days),
  the pill was **permanently red**, which RULES calls worse than no gate. The split by
  whose-fact-it-is is the fix, and it also delivers the operator's literal ask ("both bars on
  both hosts") for the six where that is meaningful.
- 🔴 **`json` IS NOT ASSUMED PER BLOCK, and that is measured.** i3status-rust's `custom` block
  defaults to `json = false`. On this bar 15 blocks are custom and only **14** declare
  `json = true` — `i3blocks-rigcontrol` prints a bare `☀` as plain text. Assuming JSON scored
  that healthy block `unparseable`/Warning, inventing an alarm for a block the remote bar
  renders fine.
- 🔴 **BOTH HOSTS ARE NAMED `nixos`.** `hostname` is `nixos` on the workbench AND the laptop,
  and `ACTIVITY_HOST` is unset non-interactively on both. A snapshot labelled from the hostname
  identifies neither — the same trap RULES records from the claim-lock's `uname -n`. Identity
  is DECLARED (`--label`, which nix passes); `label_source` records when it was guessed so a
  guess is reported as one.
- 🔴 **`POLLER_CACHE_DIR` must be spelled the CONSUMER's way.** Every `i3status-*` block
  hardcodes `~/.cache/bar-status` and does NOT read `XDG_CACHE_HOME`. An XDG-derived path is
  tidier and wrong: measured on a host with the variable set, the pull reported success, the
  files landed, and all four global pills sat on `?`. A sync that succeeds while the pills stay
  blank is the worst shape available.
- 🔴 **The six global blocks get NO CLICK HANDLERS on the laptop** (`click = lib.optionals
  (!isLaptop) [...]`). Measured: their targets are `grafana.homelab.lan`,
  `qbittorrent.workbench.lan` and `http://192.168.50.250:30302` — a LAN hostname, a LAN
  hostname and a LAN IP, none of which resolve from a nebula-only host; civitai and media also
  need per-host 0600 credential files that are not there. Dead buttons were the alternative.
- **PULL, NOT PUSH.** A push fires at a laptop that is asleep or roaming, every 60s, toasting
  and filling the workbench journal with the operator's own lid being shut. Pulling puts the
  schedule on the machine that knows whether it is awake. The unit exits **0** for an
  unreachable peer and has NO `OnFailure` toast; a stderr line is the only thing separating a
  transient outage from a permanently broken pull.
- **The `wb` pill does NOT hide at zero, alone on this bar.** It describes a machine nobody is
  looking at, so "quiet" and "not heard from in an hour" would both render as nothing.
- ⚠ **It is chronically YELLOW, and that is faithful.** The workbench's load pill warns above
  48 and the box measured **50.3–70.0 across 8 samples**. That is the workbench's own bar
  verdict relayed; retuning `loadWarnAbove` (pinned to `CPU_MON_THRESHOLD` by a test) is a
  separate decision.
- ⚠ **KNOWN GAP: `gpuBlock` is an i3status-rust BUILT-IN** (`block = "nvidia_gpu"`), so it has
  no `command` to run and cannot be relayed. The workbench's GPU is NOT visible on the laptop.
  Closing it would mean shelling out to `nvidia-smi` in the vitals gather, which
  `gather_vitals` is pinned NOT to do (it must stay /proc + statvfs so it cannot hang).
- 🔴 **DEPLOY ORDER MATTERS.** If the laptop switches before the workbench, the pull hits a
  workbench with no `bar-remote-snapshot` and the laptop shows `?` pills until the workbench
  converges. `ship.sh` does both — read every per-host line, not the final verdict.
- 🔴 **The workbench needs NO bar restart** for this change: its `config-top.toml` is
  byte-identical (only the click GATING changed, a no-op there). Only the laptop gains blocks,
  and adding a block is restart-class.
- 🔴 **CI CAUGHT TWO DEFECTS THAT CHANGE-SCOPED LOCAL RUNS STRUCTURALLY COULD NOT** — the
  "disjoint files are not safety" rule, twice in one session. (a)
  `test_claude_sessions.py::test_the_guard_evaluator_and_the_parsers_are_not_wired_to_nothing`
  is a positive control that NAMES `bar_freshness.py` as its gated example; widening that
  entry to both hosts made the example stop being an example. The file was never in the diff.
  (b) `test_runtime_shebangs.py` — the new test helper wrote `#!/bin/sh` inline at six sites;
  the repo's answer is `testlib.mockbin.write_exec`, which OWNS the shebang and rejects a body
  carrying one.
- ⚠ **A third CI red was NOT mine: a stale base.** `test_every_site_writing_its_OWN_runner_bound_is_in_the_ledger`
  flagged `test_scoped_tests_shared_surface.py`, a file not in the diff. Control: PASSES at
  current `origin/main`, FAILS at the branch head — because the branch was **25 commits
  behind** and the fix (`6f1867b1`, #1567) landed in between. `scripts/stale-base-triage.py`
  now exists for exactly this class; it arrived mid-session and was not used.
- 🔴 **`gate.sh --tier both` produced NO PYTEST VERDICT under load** — `RESULT: FAIL
  (exit=143)`, `pytest exit=124 (timeout after 3600s)`, at box load 59 with 24 concurrent full
  suites. It never reached the failing test. And piping `gate.sh` through `tail` destroyed the
  exit status, so the harness reported `tail`'s 0 — the documented trap, walked into after
  citing the rule earlier in the same session. **Read the runners' own `RESULT:` line.**
- ⚠ **`pgrep -x i3status-rs` returns EMPTY while the bar runs** (home-manager wraps it, so
  `comm` is `.i3status-rs-wr`). Confirm a restart by the **PID changing** plus a parent of
  `i3bar`, never by an `-x` count.
- **A `sleep` resolved via `readlink -f` lands on the coreutils MULTICALL binary**, which does
  not behave as `sleep`. Use `$(dirname "$(readlink -f "$(command -v sleep)")")/sleep`.

## How to verify
Run on the WORKBENCH unless stated. All passed 2026-09-12.
1. Deploy resolves: `readlink -f ~/.config/i3status-rust/scripts/bar-remote-snapshot` →
   a live `/nix/store` path on BOTH hosts; `i3status-remote-host` + `remote-host-detail`
   present on the laptop and **absent** on the workbench (`isLaptop`).
2. The pull: `ssh zach@10.42.0.100 'systemctl --user is-active bar-remote-pull.timer'` →
   `active`; `systemctl --user start bar-remote-pull.service` → rc 0 in ~3s.
3. 🔴 **Parity is the design's central claim** — run each NATIVE block on BOTH hosts and diff
   `text|state`. All six must MATCH. A difference means the relayed cache is stale or the
   observer is rendering its own opinion.
4. The cache holds EXACTLY the six NATIVE sources:
   `ls ~/.cache/bar-status/*.json` on the laptop → alerts, civitai, clawgate, mail, media,
   telemetry. `airvpn.json`/`runaways.json` appearing there is the round-2 defect regressing.
5. Bar restart (LAPTOP, takes the operator's screen — hand it over): `i3-msg restart`, then
   confirm the PID CHANGED and its parent is `i3bar`, and that the bar's start time is AFTER
   `config-top.toml`'s mtime.
6. 🔴 **Screenshot and LOOK** — the only check that proves pango renders the `wb` pill's
   coloured host label: `maim -g "${W}x34+0+0" /tmp/bar-top.png`.
7. The classification cannot silently grow:
   `nix develop ~/workspace/devrc -c python3 -m pytest scripts/tests/test_bar_remote_snapshot.py -q`
   → 64 tests; `test_every_custom_block_is_classified_exactly_once` fails on any unclassified
   custom block rather than defaulting it into the relay.
