# Handoff: tmux-restore-chain — 2026-09-06

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
Make the post-reboot restore of the tmux/claude workspace actually work. It had never
worked: every link in the save→plan→restore chain was broken, silently, for ~30 days.

## State now
- Branch/PR: `main`, clean. Five PRs merged, shipped to both hosts, live-verified.

| PR | merge sha | what was broken |
|---|---|---|
| #1297 | `56c68cc7` | `@resurrect-hook-post-save` is not a valid resurrect hook kind — the save side had **never** run |
| #1309 | `cc409f82` | continuum's `status-right` autosave interpolation clobbered by a later `set -g status-right` — no save in 30 days |
| #1314 | `dcaeb408` | zero `workspace … output` directives against a declared dual-head layout |
| #1311 | `d9f0836c` | window→conversation binding by 145-file grep instead of the deterministic per-pane ledger that already existed |
| #1317 | `946d9038` | the staleness gate counted POWERED-OFF time against the plan |

- **Live, measured 2026-09-06 10:47** (chain healthy, unattended): layout `10:46:45` →
  plan `10:47:23` (38s later); `plan_staleness_hours()` = `0.1646h basis=liveness`
  (limit 2.0h); plan 46 entries, **45 bound** (`ledger 39 · fuzzy 6 · unbound 1`).
- `@continuum-save-last-timestamp` moved for the first time since 2026-08-05.
- The boot unit's exact invocation (`restore --dry-run --staleness-check 2`) exits **0**;
  it exited 1 on every boot from 2026-08-04 until #1297+#1309.
- **NOT VERIFIED: no reboot has occurred.** `uptime -s` = 2026-08-04 14:51:40. Every
  power-off scenario is fixture mtimes + an injected uptime, never an actual shutdown.
- #1311 took 4 audit rounds, #1317 took 4. In both, rounds 1–3 each found a real defect
  **introduced by the previous round's fix**. #1297/#1309/#1314 merged with no audit
  (operator's call, recorded).

## Open investigations — live diagnosis state

### The boot unit races continuum, and on a cold boot the race is the guaranteed path
- **Symptom + exact repro:** unverifiable without a reboot. On a cold boot the restore
  unit and continuum build the tmux workspace concurrently; expected damage is duplicated
  or misplaced windows, not data loss.
- **Observed (with values):**
  - `systemctl --user show tmux-session-restore.service -p After --value` →
    `graphical-session.target tmux-session-restore.timer app.slice`. **Nothing
    tmux-related, nothing continuum-related.**
  - Timer is `OnActiveSec=45s`, `WantedBy=timers.target`.
  - `tmux show-options -gqv @continuum-boot` → **empty (UNSET)**. So continuum does not
    auto-start tmux at boot.
  - i3 config: `grep -cE '^exec.*(alacritty|tmux)'` → **0**. `.zshenv` → 0. **Nothing
    starts tmux on a cold boot.**
  - `scripts/tmux-session-restore.py:740,743` — the script itself runs
    `tmux new-session -d` / `new-window` for anything missing.
  - Layout to replay: 50 `pane` lines / 50 `window` lines; `pane_contents.tar.gz` = 17 MB.
- **Ruled out:** the 17 MB tarball is the bottleneck — `tar -xzf` of it measured
  **0.25s wall, 50 files**. via: measurement
- **Ruled out:** "continuum runs first, this unit runs after" (the unit's own
  `Description` asserts it) — it cannot, because on a cold boot nothing else starts a
  tmux server, so the script's own `new-session` is what starts it, which sources
  `tmux.conf`, which loads `continuum.tmux`, which sees `just_started_tmux_server` and
  fires its restore in the background. via: code
- **Leading hypothesis:** 45s is *usually* enough for tmux to replay 50 panes, so this
  has probably never bitten — but it is an unguarded timing assumption with no ordering,
  and the trigger order is inverted from the design.
- **Next probe:** reboot, then immediately:
  `journalctl --user -u tmux-session-restore.service -b` and
  `tmux list-windows -a | wc -l` — a count well above the plan's entry count is the race.

### The liveness term is inert at every boot (`uptime <= limit`) — documented, not closed
- **Symptom + exact repro:** a chain frozen across a reboot restores a stale plan.
  Reproduce with the live module: plan+layout both aged 1400h and 30s apart, uptime
  injected as `45/3600` → `(0.0125, 'liveness')`, rc 0 → PASSES.
- **Observed (with values):** `live = min(since, uptime)` and refusing needs
  `> limit`, so whenever `uptime <= limit` the term cannot contribute. The unit passes
  `--staleness-check 2` and fires at boot+45s ⇒ `live <= 0.0125h`, **160× under**.
- **Ruled out:** using the previous boot's end as the discriminator —
  `journalctl --list-boots` reports **two** boots whose ranges do not abut (boot `-1`
  ends 2026-07-14, boot `0` begins 2026-08-18) while `uptime -s` says 2026-08-04; the
  journal had rotated the intervening boots away. Least trustworthy on exactly the
  long-lived host where a frozen chain is likeliest. via: measurement
- **Ruled out:** that the damage is unbounded — a frozen chain froze **both** artefacts,
  so resurrect's layout is equally stale and `@continuum-restore on` restores that old
  layout regardless. The gate cannot prevent the stale workspace, only decide whether to
  populate it. via: measurement
- **Leading hypothesis:** correctly scoped rather than closed. What is genuinely lost is
  the *diagnostic* — the old wall-clock refusal is how the 2026-08-05 freeze was noticed.
- **Next probe:** none pending. Documented in `plan_staleness_hours`'s docstring with the
  missing case rows. Revisit only if a boot produces a wrong restore.

### Bare-Alacritty `claude` sessions have no crash/close protection
- **Symptom + exact repro:** operator accidentally closed several i3 windows 2026-09-06.
  Windows running `claude` **outside** tmux vanished; their conversations survived only
  because Claude Code persists transcripts.
- **Observed (with values):**
  - tmux was untouched — 22 sessions / 55 windows / 45 claude panes, 19 detached.
  - ClickHouse `activity.events`, `source=zsh kind=command`, non-tmux sessions
    (`position(session,':')=0`), last 12h: **4 shells, one command each, all `claude`
    from `/home/zach`** at 11:49 / 13:54 / 16:45 / 20:04 local.
  - Those four transcripts are **1846 bytes, 6 lines, identical** — `mode`,
    `permission-mode`, 3 `user`, `last-prompt`, **zero assistant turns**. Empty sessions.
  - `source=i3 app=Alacritty` titles recovered two real ones, resumed successfully:
    `586dd050-2d7e-48c5-a599-be46f95f5088` (cwd `/home/zach/workspace/devrc`) and
    `19f9e90b-08a3-4cc6-b4a7-1f599ef347e6` (cwd `/home/zach/workspace/homelab-talos`).
- **Ruled out:** shell history as a recovery route — `~/.zsh_history` mtime is
  **2026-08-27**, i.e. not being written; no `INC_APPEND_HISTORY`/`SHARE_HISTORY` found.
  via: measurement
- **Leading hypothesis:** the telemetry pipeline (zsh `preexec` + `i3-source`) is a
  reliable recovery path, but only because those sources happen to be running. A window
  inside tmux needs no recovery at all.
- **Next probe:** decide whether bare `claude` windows should exist. `OC | Run opencode
  session handoff/kickoff` was NOT recovered — opencode does not write to
  `~/.claude/projects`; the `opencode` skill owns that surface.

## Next steps (ranked)
1. **Reboot and observe.** The only test that closes both open investigations above;
   everything else is simulation. **The pre-reboot baseline is already on disk** —
   captured 2026-09-06 16:14Z at `~/.cache/tmux-restore-observe/pre-latest.txt`
   (`boot_time=2026-08-04 14:51:40`, layout 56 windows / 56 panes, plan 46 entries /
   45 bound, live 22 sessions / 56 windows). After the reboot, run **one command**:
   ```bash
   ~/workspace/devrc/scripts/tmux-restore-observe.sh post   # rc 0 clean · 1 race · 3 could-not-decide · 4 windows missing
   ```
   It captures the live state plus the unit's journal for that boot and diffs it
   against the baseline. 🔴 Its rc-0 wording says so itself: a clean boot is ONE
   negative sample of an unguarded timing assumption, not a closure. A copy of the
   script also sits at `~/.cache/tmux-restore-observe/tmux-restore-observe.sh` so a
   branch switch cannot take it away. It is a READER — it adds no boot-path unit, on
   purpose: three defects in this arc came from changing a boot path nobody had observed.
   forcing: none
2. **Run bare `claude` windows inside tmux** so a closed window detaches instead of
   vanishing. Touches no repo file — an operator habit, or an i3 binding in
   `devrc/nix/i3/config.nix`.
   forcing: incident — 2026-09-06, the operator accidentally closed i3 windows; two live
   claude conversations survived only via transcript archaeology through ClickHouse, and
   one opencode window was never recovered.
3. **Order the boot unit against continuum**, or set `@continuum-boot` so tmux starts
   before the unit fires. `devrc/nix/home.nix` (the `tmux-session-restore` unit) and
   `devrc/nix/programs/tmux/default.nix`. Do this only AFTER rank 1 — three defects in
   this arc were introduced by fixing a boot path nobody had observed.
   forcing: none
4. **`--assume-empty` for `restore --dry-run`**, so a pre-reboot dry run exercises the
   send path instead of only the skip branch. `devrc/scripts/tmux-session-restore.py`.
   Reported as gap #4 at the start of this arc and never closed.
   forcing: none

## Gotchas / decisions / dead-ends
- 🔴 **The handoff this arc started from never existed.** `/resume` was given
  `devrc/claudedocs/handoff-pre-reboot-validation.md`; it is in no ref. `resume-state.sh`
  reported it as a `!` gap rather than a clean bill of health, which is the only reason
  the work started from measurement instead of a fabricated summary.
- 🔴 **i3 layout restore was researched and REJECTED**, not skipped. i3 matches swallow
  criteria on five properties; all 5 Alacritty windows share `class`/`instance`, report
  `window_role=None`, and sit on one `machine` — leaving `title`, which is
  agent-generated and already contains duplicates. Per i3's docs swallowing outranks
  assignment rules, so a stale placeholder **hijacks an unrelated future window**.
  `i3-resurrect` is pinned in nixpkgs to a 2022 release; `i3-restore` is not packaged and
  would race tmux-resurrect. All are snapshot-with-no-verifier — the exact class that
  caused two of this arc's outages. #1314 (3 stateless lines) is the whole i3 answer.
- **`plan_age_hours` is gone**, replaced by `plan_staleness_hours()` returning
  `(hours, basis)` with basis in `layout` / `liveness` / `skew` / `wall`. A consumer
  written against a three-arm contract will `KeyError` in the refusal path.
- **`tmux-session-restore.py` runs from the WORKING TREE** (`tmux-post-save.sh:11`,
  and the unit's `%h/workspace/devrc/...`), so `ship.sh`'s fast-forward *is* the deploy
  for that file — no `home-manager switch` needed. The inverse of the usual trap.
- **Do not build the two nix check derivations in one invocation** — store contention
  produces false failures. A combined green is trustworthy; a combined red is not.
- **`grep` can return 0 for a string that is present** when the source splits it across
  lines. Verifying #1317's merged message required *executing* the module and reading the
  rendered string, not grepping the file.

## How to verify
```bash
# 1. the chain is alive (layout, then plan seconds later, then the hook log)
ls -t ~/.tmux/resurrect/*.txt | head -3 | xargs -I{} stat -c '%y {}' {}
stat -c '%y' ~/.config/initiatives/restore-plan.json ~/.cache/tmux-session-restore.log

# 2. the boot unit's exact invocation exits 0
python3 ~/workspace/devrc/scripts/tmux-session-restore.py restore --dry-run --staleness-check 2

# 3. the send path actually fires (a plain dry run only exercises the skip branch)
python3 - <<'PY'
import json,os,pathlib
p=pathlib.Path(os.path.expanduser('~/.config/initiatives/restore-plan.json'))
plan=json.loads(p.read_text())
for e in plan: e['session']='POSTBOOT-'+e['session']
pathlib.Path('/tmp/sim-plan.json').write_text(json.dumps(plan))
PY
python3 ~/workspace/devrc/scripts/tmux-session-restore.py restore --dry-run --plan /tmp/sim-plan.json | tail -1
# expect "would relaunch N windows, skipped 0"; then confirm nothing was created:
tmux list-sessions -F '#{session_name}' | grep -c POSTBOOT   # must be 0

# 4. binding quality
python3 -c "
import json,os,pathlib,collections
p=json.loads(pathlib.Path(os.path.expanduser('~/.config/initiatives/restore-plan.json')).read_text())
print(len(p),'entries,',sum(1 for e in p if e['session_id']),'bound',
      dict(collections.Counter(e.get('bind_source','') for e in p)))"

# 5. the unit tests
nix develop ~/workspace/devrc -c python3 -m pytest \
  ~/workspace/devrc/scripts/session-analysis/tests/test_tmux_session_restore.py -q -p no:cacheprovider
```
