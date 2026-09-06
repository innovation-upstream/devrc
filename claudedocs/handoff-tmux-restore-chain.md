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
- ✅ **THE REBOOT HAPPENED — 2026-09-06 18:01:46.** No longer a simulation. It answered
  the first open investigation and REFUTED its predicted damage; see below.
- #1311 took 4 audit rounds, #1317 took 4. In both, rounds 1–3 each found a real defect
  **introduced by the previous round's fix**. #1297/#1309/#1314 merged with no audit
  (operator's call, recorded).

## Open investigations — live diagnosis state

### ✅ ANSWERED 2026-09-06 — the race is REAL but at a DIFFERENT SEAM than this doc predicted
- **The reboot happened.** boot `2026-09-06 18:01:46`, from an uptime of 32 days.
- 🔴 **The predicted damage did NOT occur.** continuum's window replay was *perfect*:
  54/54 windows, correct `(session, index)` set, **zero extras, zero missing**. There is
  no duplication race.
- 🔴 **What DID happen: all 43 `claude --resume` sends were silently discarded.**

| | |
|---|---|
| `18:01:46` | boot |
| `18:02:48` | `tmux-session-restore.service` starts — boot+62s (`OnActiveSec=45s`) |
| `18:02:49` | 43 × `claude --resume` sent — boot+63s |
| `18:03:13`–`18:03:15` | **resurrect creates all 54 pane shells** — boot+87s |

  A **24-second gap**. The journal names the mechanism outright:
  `Started tmux child pane <pid> launched by process 25957`, ×54, in one batch, *after*
  the sends. Keys delivered to a pane that is not ready are discarded — **zero
  occurrences of the sent text in any pane's scrollback**, not even as unexecuted text.
  The unit then exited **0** with `Result=success`, and the operator found 54 bare shells.
- 🔴 **THIS DOC'S OWN PROPOSED PROBE WOULD HAVE MISSED IT.** It said "a count well above
  the plan's entry count is the race". The count was **54 vs 54**. It reads clean.
- **Proof it is timing and not logic:** re-running the identical `restore` 7 minutes
  later, against the same plan, relaunched **42 of 43** (1 skipped as already running).
- **The 5 windows reported as MISPLACED** (`/home/zach` instead of their recorded cwd)
  are the same root cause — their cwd restore had not happened either.
- **Ruled out:** that a fixed delay can be tuned to fix this — the gap scales with pane
  count, disk speed and boot load. via: measurement (24s on a 54-pane workspace)
- **FIXED** in `fix/tmux-restore-boot-race`, two layers, because either alone is
  insufficient:
  1. **PREVENTION** — `wait_for_workspace_to_settle()` blocks until the pane
     fingerprint (`pane_pid` + `pane_current_command` for every pane) stops changing.
     Waits for the *observable*, not a guessed duration. An empty fingerprint does NOT
     count as settled, or a dead tmux server would read as ready.
  2. **DETECTION** — `_verify_sends()` polls each target until it is running `claude`.
     `tmux send-keys` exits 0 for keystrokes that go nowhere, so "sent" was only ever a
     claim about the process. The unit now exits **1** when the resumes did not land.
- **Also closed:** `tmux-restore-observe.sh` now captures `sends_logged` and
  `claude_panes_live` and reports `🔴 THE RESUMES DID NOT LAND`. Verified against the
  REAL post-capture from this boot (43 sends / 1 live → fires; 43/43 → quiet).

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
1. ✅ **DONE — rebooted 2026-09-06 18:01:46 and observed.** See the ANSWERED block above.
   The fix is in `fix/tmux-restore-boot-race`. What remains of this item:
   **ship that branch and reboot ONCE MORE to confirm the fix**, using the same command
   (the baseline is written by `pre`, and `post` now reports whether the resumes landed):
   ```bash
   ~/workspace/devrc/scripts/tmux-restore-observe.sh pre    # before
   ~/workspace/devrc/scripts/tmux-restore-observe.sh post   # after
   # rc 0 clean · 1 race/misplacement/resumes-lost · 2 usage · 3 could-not-decide · 4 windows missing · 5 no workspace
   ```
   🔴 The second reboot is not optional-in-spirit: the fix has been unit-tested and
   mutation-swept, but the ONLY thing that has ever exercised this path for real is a
   reboot, and the first one refuted the standing hypothesis outright.

2. **Run bare `claude` windows inside tmux** so a closed window detaches instead of
   vanishing. Touches no repo file — an operator habit, or an i3 binding in
   `devrc/nix/i3/config.nix`.
   forcing: incident — 2026-09-06, the operator accidentally closed i3 windows; two live
   claude conversations survived only via transcript archaeology through ClickHouse, and
   one opencode window was never recovered.
3. 🔴 **SUPERSEDED — do NOT do this as written.** It said: order the unit against
   continuum, or set `@continuum-boot`. The reboot showed ordering against continuum
   is not the fix: continuum's window replay was already complete and correct when the
   unit fired. What the unit races is resurrect's **pane-shell creation**, 24s later,
   and no systemd ordering expresses "wait until every pane has a live shell". The fix
   is in the script (settle-wait + send verification), not in the unit graph. Kept here
   because this entry would otherwise send the next session to rebuild the wrong thing.

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
