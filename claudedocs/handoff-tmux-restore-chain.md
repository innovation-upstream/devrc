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
### Merged in this arc (durable — carried forward across updates)
| PR | merge sha | what was broken |
|---|---|---|
| #1297 | `56c68cc7` | `@resurrect-hook-post-save` is not a valid resurrect hook kind — the save side had **never** run |
| #1309 | `cc409f82` | continuum's `status-right` autosave interpolation clobbered by a later `set -g status-right` |
| #1314 | `dcaeb408` | zero `workspace … output` directives against a declared dual-head layout |
| #1311 | `d9f0836c` | window→conversation binding by 145-file grep instead of the per-pane ledger |
| #1317 | `946d9038` | the staleness gate counted POWERED-OFF time against the plan |
| #1344 | `1ecc03c1` | no instrument existed to read a reboot; adds `tmux-restore-observe.sh` |
| #1351 | `9353d958` | the unit MANUFACTURED a tmux server systemd then killed; adds the no-server REFUSAL |
| #1375 | head `10570f92` | 81-row gate inventory + `scripts/check-gate-inventory.py` |
| #1415 | `176f412b` | the wide-tmux-kill guard — **LIVE and verified on both hosts** |
| #1376 | `e55533ea` | socket-activation trigger — **LIVE and verified on both hosts** |
| #1383 | `a4d9d083` | timestamped plan generations + the four round-1 audit fixes |
| #1464 | `82219263` | retires the staged OOM script (operator decision after a round-0 audit) |
| #1460 | `f3412fc1` | a `nix/home.nix` comment measured false in BOTH halves |
| #1443 | `cdfd14ab` | handoff update |
| #1467 | `cd556159` | transcript-push caps were tuned to an UNDEPLOYED server; push was 413-dead fleet-wide |

- 🔴 **THE REBOOT — 2026-09-06 18:01:46** is still the only time the BOOT path has ever been
  exercised, and it ran the **OLD timer design**. #1376 REPLACED the trigger, so the mechanism now
  deployed **has never fired at boot on either host.**
- 🔴 **OPERATOR DECISION: THE REBOOT IS ON HOLD.** Recommended and explicitly deferred — do not run
  one, and do not re-propose it unasked.
- 🔴 **OPERATOR DIRECTIVE: PROCEED REGARDLESS OF LOAD.** Do not hold merges for load.
- 🔴 **THE LOCAL FULL-SUITE RITUAL IS RETIRED** (CLAUDE.md changed 2026-09-09 mid-session). CI is
  ADVISORY, ~42% of reds are noise — act on the failing TEST, not the colour. Run a change-scoped
  subset: `nix develop ~/workspace/devrc -c python3 -m pytest <paths> -q`.

### Verified LIVE on both hosts (by reproducing behaviour, not by grep)
- The guard DENIES the incident command against the **deployed** artifact, with controls proving it
  is not a blanket denier (`echo hello`, `tmux -L my-probe-$$ …`, `kill-pane` all ALLOWED).
- `tmux-session-restore.path` **active**, `.timer` **inactive**, `ConditionPathExists` present.
- Generations migration FIRED: the plan is a symlink into `restore-plans/`, stamps are **UTC**
  (`20260910T143956` against a local mtime of 09:39 — the UTC-5 offset).
- transcript-push returns **HTTP 200** on both hosts; the laptop's user manager is back to
  `running` (was `degraded`).
- `ship.sh` now reaches the laptop over nebula (#1439) and can say **"2 hosts compared"**.

### Closed without merging (durable — do not re-open or re-derive)
- **#1459** fixed a MEASURED data-loss path in the staged OOM script (a re-run whose
  `nixos-rebuild` failed restored a STALE backup over `/etc/nixos/configuration.nix`, taking
  unrelated operator edits with it). It was **opened and then CLOSED** by the operator's decision
  to delete the script entirely — the fix was correct work on something that should not exist.
  The reasoning, including the counter-argument, is on #1459 and in #1464's body.

### Open PRs from this session
- **#1480** — finding 5 (the initiative-scan skill never mentioned generations). **OPEN, NOT MERGED.**
- **#1481** — the measured 1 MiB proxy limit + the tail-bytes-vs-wire-body correction. OPEN.

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

### ✅ ANSWERED 2026-09-06 — supersedes "The boot unit races continuum…" above, which is RETIRED
🔴 **The retired block's `Next probe` ("a count well above the plan's entry count is the
race") is WRONG and must not be run — the count was 54 vs 54 and reads CLEAN.** Its
`Ruled out` note ("continuum runs first, this unit runs after — it cannot, because on a cold
boot nothing else starts a tmux server, so the script's own `new-session` is what starts it")
was **CORRECT and is the actual answer**; #1351's doc rewrite deletes it — do not let that land.

- **Symptom + exact repro:** reboot. Windows come back; **conversations do not.** Operator
  sees ~54 bare shells. Reproduced twice on 2026-09-06 (the real reboot, and again when the
  server was killed at 22:31).
- **Observed (with values):**
  - boot `18:01:46`; unit `Starting` `18:02:48.681` → `Finished` `18:02:49.031` — **0.35s wall**.
  - 43 × `→ … claude --resume` logged at `18:02:49.015`.
  - `Started tmux child pane N launched by process 22781` — **17 lines**, all `18:02:48`.
    The unit's own python was `22749`, so **22781 is a tmux server the UNIT ITSELF started**
    via `scripts/tmux-session-restore.py:817` `tmux new-session -d`.
  - `Started tmux child pane N launched by process 25957` — **56 lines**, `18:03:13` … `22:07`.
    A **different, later server**.
  - `systemctl --user show tmux-session-restore.service` → `Type=oneshot`,
    **`RemainAfterExit=no`**, `KillMode=control-group` (`nix/home.nix:4868-4877`).
  - Post-restore pane census after the reboot: **53 zsh, 1 claude** against 43 sends.
  - Zero occurrences of the sent text in any pane's scrollback.
- **Ruled out:** the predicted duplication race against continuum — continuum's replay was
  *perfect*: 54/54 windows, correct `(session, index)` set, zero extras, zero missing.
  via: measurement
- **Ruled out:** 🔴 **"the sends landed in panes that had no shell yet and were discarded"** —
  this was the session's FIRST diagnosis and **#1351 IS BUILT ON IT.** It is wrong. The keys
  were delivered *successfully* into server 22781; that server was then destroyed. The
  scrollback-is-empty evidence is consistent with BOTH stories and cannot separate them.
  via: measurement
- **Ruled out:** that a fixed timer can be tuned to fix this — the unit does not race a
  duration, it manufactures a server that cannot outlive it. via: measurement
- **Leading hypothesis — CONFIRMED BY EXPERIMENT, not inference:** on a cold boot no tmux
  server exists; the script's own `new-session` starts one **inside the unit's cgroup**; the
  sends land in it; ExecStart returns; systemd tears the cgroup down and takes the server and
  every claude process with it. Isolated proof, private `-L` socket, real server untouched:

  | unit config | server after ExecStart returns |
  |---|---|
  | `Type=oneshot`, no `RemainAfterExit` | **gone** — `no server running on …` |
  | `Type=oneshot` + `RemainAfterExit=yes` | **survives** — `probeB: 1 windows` |

- **Next probe:** none needed for the diagnosis — it is closed. The open question is the
  REMEDY. Candidates, none yet implemented: (a) **refuse when `tmux has-session` fails** so
  the unit never manufactures a server it cannot keep; (b) trigger on the tmux **socket
  appearing** rather than `OnActiveSec=45s`; (c) `RemainAfterExit=yes` — measured to work,
  but with `KillMode=control-group` the unit would then own the operator's server and
  stopping it would kill the workspace. **(a)+(b) is the direction the evidence supports.**

### 🔴 #1351 does not fix the defect it names — audit round 1, unresolved
- **Symptom + exact repro:** trace `cmd_restore` against the confirmed mechanism above.
- **Observed (with values):** with no server, `wait_for_workspace_to_settle` bails at 10s
  `(False, 10.0)`; `cmd_restore` **sends anyway**, creating the same doomed server;
  `_verify_sends` polls *that* server and reports **all 43 landed**; `not settled and sent`
  → **exit 1** → `OnFailure=notify-failure@%n` toast; unit exits; systemd kills the server.
  Operator still finds bare shells.
- **Observed:** `nix/home.nix:712-714` — "any unit that can fail on a STANDING condition
  breaches [the DND bypass] again". Exit 1 here is a standing condition under this host's
  actual boot pattern. via: code
- **Ruled out:** that the PR is worthless — the settle-wait and verification are sound in
  isolation and the observe arm is a real improvement; it converts a *silent* total failure
  into a *loud* one. via: measurement
- **Leading hypothesis:** #1351 needs rework, not merging: make "no tmux server" a REFUSAL
  rather than a proceed, correct the mechanism prose in the three comments that assert
  "discarded by the pane" (`tmux-session-restore.py:507`, `:855`,
  `tmux-restore-observe.sh:598`), and restore rank 3 instead of superseding it.
- **Open 🟡 from the same audit, unfixed:** `tmux-restore-observe.sh:589` tests
  `[ "$live" = UNMEASURED ]` but production emits `UNMEASURED reason=…`, so the arm reads
  CLEAN with an integer-expected error (fix: `case "$live" in UNMEASURED*)`); both new
  exit-1 branches in `cmd_restore` are **untested — mutants SURVIVE**;
  `claude_panes_live` is a whole-host count that includes hand-started and skipped panes;
  four tests assign `tsr.pane_fingerprint` without `monkeypatch` teardown.

### ✅ CLOSED — "🔴 #1351 does not fix the defect it names" (audit round 1) is RESOLVED
🔴 **The block by that name above is RETIRED — do not re-derive it, and do not
re-run its `Leading hypothesis` as work.** Every item it listed is done, in
commits `7056a0fa` and `3b348542`. Recorded here because the retired block reads
as live diagnosis and its "Leading hypothesis: #1351 needs rework, not merging"
is exactly the kind of sentence a resuming session actions again.

- **Independently re-confirmed before acting** (not taken on the doc's
  authority): traced `cmd_restore` on the PR head against the live unit config.
  `systemctl --user show tmux-session-restore.service` → `Type=oneshot`,
  `RemainAfterExit=no`, `KillMode=control-group`; `Environment=` carries `PATH`
  and `HOME` only, **no `TMUX_TMPDIR`** (which independently confirms rank 3).
  `run()` swallows failures and returns `""` (`:130-135`), so with no server the
  fingerprint is empty, the settle wait bails `(False, 10.0)`, and `cmd_restore`
  **fell through into the loop**, whose `tmux new-session` manufactured the
  doomed server. Audit round 1 was correct.
- **Fixed:** the refusal, ahead of the loop.
- **Fixed:** `[ "$live" = UNMEASURED ]` → `case "$live" in ''|UNMEASURED*)`.
- **Fixed:** both exit-1 branches now pinned, with a settled-and-landed control
  so an unconditional `return 1` fails.
- **Fixed:** the verdict prints that `claude_panes_live` is a WHOLE-HOST count.
- **Fixed:** four `tsr.pane_fingerprint` assignments → `monkeypatch`.
- **Ruled out:** that closing #1351 and re-cutting was the better route — ~250
  lines (the settle wait, `_verify_sends`, the observe arm, 167 lines of tests)
  survive the mechanism correction with their behaviour intact, and a squash
  merge rewrites the misleading title anyway. via: code
- **Next probe:** none for the diagnosis. The open work is the two gate tiers.

### The rework is UNGATED — both tiers outstanding
- **Symptom + exact repro:** not a defect; a missing measurement. No tier has
  reported on the reworked tree.
- **Observed (with values):** dev-host tier started and still in its pytest leg
  at the time of writing; nix sandbox tier not started. Prior head `151a334f`
  was gate-green, but against a base **9 commits older**, and the PR then went
  `mergeable: CONFLICTING` — so that green says nothing about this tree.
- **Ruled out:** running the two nix check derivations concurrently to save
  time — documented in CLAUDE.md to produce false failures via store
  contention; a combined RED is untrustworthy. via: doc
- **Leading hypothesis:** it will pass — the 130 tests in the two affected files
  pass locally and the mutation battery's control was green — but that is a
  claim about two files, not about ~14k cases or the sandbox tier.
- **Next probe:** read `scripts/gate.sh --tier both`'s exit status (authoritative;
  90 = could-not-vouch, read the log), then the two `nix build` derivations
  **one at a time**.

### ✅ ANSWERED 2026-09-07 — opencode session restore is ~80% already built; one hardcoded string blocks it
Recon done this session. **The previous handoff's note — "opencode does not write to
`~/.claude/projects`; the `opencode` skill owns that surface" — is TRUE but led to the
wrong conclusion.** Opencode does not need that surface: it has its own, and the devrc
ledger has been writing to it all along.

- **Symptom + exact repro:** after a reboot, opencode TUI windows come back as bare
  shells. `OC | Run opencode session handoff/kickoff` was never recovered on 2026-09-06.
- **Observed (with values):**
  - `pane_current_command` for an opencode pane is literally `opencode` — the pane
    detector extends with one token.
  - **25 `~/.cache/agent-ledger/opencode-p<N>.json` records already exist**, written by
    `scripts/opencode/plugin/ledger.js` (writer 2). Schema is IDENTICAL to claude's, with
    `runtime: "opencode"` already discriminating; `transcript_path` is `null` (correct —
    opencode has no JSONL).
  - `agent_ledger.py` already declares `RUNTIMES = ("claude", "opencode", "clawgate")` and
    `pane_filename(runtime, pane_id)` is already parameterised.
  - 🔴 **`tmux-session-restore.py:235` hardcodes `_AL.pane_filename("claude", pane_id)`** —
    so those 25 records are written and never read. That one string is the blocker.
  - Sessions live in SQLite `~/.local/share/opencode/opencode-stable.db` (2.4 GB). The
    `session` table carries `id`, `directory`, `title`, `parent_id`.
  - All 25 ledger ids resolve in the DB; **0 have a `parent_id`; 0 lack a `directory`.**
  - End-to-end: live pane `%41` (scratch20:5, cwd `~/workspace/devrc`) → ledger
    `ses_f835f662…` → DB row `directory=/home/zach/workspace/devrc`, `parent_id` empty.
  - Resume verb is `opencode --session <id>` (also `-c/--continue`).
- **Ruled out:** that opencode needs a transcript-file surface like claude's — it does not;
  `session.directory` is a DIRECT cwd comparison, stronger than claude's encoded-name
  match. via: measurement
- **Ruled out:** a pass-2 content-grep fallback for opencode — claude's greps JSONL; the
  analogue is content-searching a 2.4 GB DB, and with "send nothing when unbound" chosen it
  buys nothing. via: code
- **Leading hypothesis:** none outstanding — the mechanism is measured. What remains is the
  build, and it is gated on #1351 merging (same four functions).
- **Next probe:** none. `opencode --session <id>` was VERIFIED on a private `-L` socket
  (see the gotcha below); the chain is proven end to end.

### 🔴 THE LAPTOP'S RECOVERY CHAIN IS DEAD, and the fix is deployed-but-inert — measured 2026-09-07
- **Symptom + exact repro:** on the laptop (`ssh zach@192.168.50.155`), the chain produces
  nothing. It would have lost every conversation on its next reboot, exactly as the
  workbench did on 2026-09-06.
- **Observed (with values):**

  | | workbench | laptop |
  |---|---|---|
  | `status-right` carries `continuum_save.sh` | YES | **NO** |
  | tmux server started | Sep 6 22:31 (after #1309) | **Aug 28 18:39** (before #1309) |
  | `~/.tmux/resurrect/last` | today 18:47 | **Aug 14** — 24 days stale |
  | `~/.config/initiatives/restore-plan.json` | today 18:47 | **DOES NOT EXIST** |
  | live claude panes at risk | 45 | **9** |

  - Laptop unit is `Type=oneshot` / `RemainAfterExit=no` / `KillMode=control-group` —
    **identical** to the workbench, so #1351 and the socket trigger apply there unchanged.
  - `@continuum-save-interval=15`, `@continuum-restore=on`, `@continuum-boot` empty —
    the settings are right; the interpolation is missing.
  - The CORRECTED config IS on disk: `~/.config/tmux/tmux.conf` →
    `/nix/store/…-home-manager-files/…` → `/nix/store/7v2b1lbdxbwhd39g6a9fk2a9kvzsgqav-hm_tmuxtmux.conf`,
    symlink stamped Sep 7 18:01, containing 9 `continuum` references.
- **Ruled out:** that the laptop is missing the deploy — the unit is `linked`, the timer is
  `enabled`, and the fixed tmux config is on disk. This is NOT a `ship.sh` gap. via: measurement
- **Ruled out:** that `~/.tmux.conf` is the config — it **does not exist** on the laptop, so
  an earlier grep against it returned empty. 🔴 That empty result meant "wrong file", not
  "fix absent" — the documented grep trap, hit live. The real path is
  `~/.config/tmux/tmux.conf`. via: measurement
- **ROOT CAUSE (single, explains both symptoms):** tmux reads its config **only at server
  start**. The laptop's server predates #1309, so it still runs the CLOBBERED `status-right`
  → continuum never autosaves → the resurrect post-save hook never fires → no plan is ever
  written. The fix is deployed and inert. This is CLAUDE.md's own sequence failing at its
  last step — *merge → pull → switch → **restart the consumer*** — where the consumer is the
  tmux server, and restarting it is precisely what must not be done.
- **Next probe:** none for the diagnosis. The REMEDY is the open question, and the naive one
  is destructive: restarting the laptop's tmux server would kill 9 live conversations, the
  same harm this arc already caused once. The safe move is a surgical live re-arm —
  append the `continuum_save.sh` interpolation back onto the running server's `status-right`
  (no restart). Awaiting operator direction as of this writing.

### ✅ RESOLVED 2026-09-07 — the laptop's dead chain is FIXED and VERIFIED END-TO-END
🔴 **The block above ("THE LAPTOP'S RECOVERY CHAIN IS DEAD") is RESOLVED — do not re-run
its remedy.** Both settings were applied to the laptop's LIVE server; no restart.

- **What was wrong (both bugs live at once — the server predated BOTH fixes):**
  - `@resurrect-hook-post-save-all` = **empty**, while the INVALID-kind
    `@resurrect-hook-post-save` = `~/.config/tmux/tmux-post-save.sh` — i.e. the pre-#1297
    state, so the hook was attached to a kind resurrect never invokes.
  - `status-right` carried no `continuum_save.sh` — the pre-#1309 clobber.
- **The fix, applied live (no server restart, 9 conversations untouched):**
  ```
  tmux set -g  @resurrect-hook-post-save-all "~/.config/tmux/tmux-post-save.sh"
  tmux set -ag status-right "#(/nix/store/nhgxbv11…-tmuxplugin-continuum-…/scripts/continuum_save.sh)"
  ```
  Rollback copy of the original `status-right` is at `~/.cache/status-right.pre-rearm.bak`
  ON THE LAPTOP.
- **VERIFIED END-TO-END, before/after:**

  | | before | after |
  |---|---|---|
  | `~/.tmux/resurrect/last` | `…20260814…` (24 days stale) | `…20260907T185838` |
  | `restore-plan.json` | **absent** | present, 18:58:42, **9 entries** |
  | plan bind quality | n/a | 9/9 have a session_id — **8 ledger, 1 fuzzy**; `ledger_reason` 8 `ok`, 1 `transcript-missing`; 4 distinct cwds |

  ⚠ **Attribution note, stated because it matters:** the forced-save command in that check
  **FAILED** (`rc 127` — a path extraction returned empty so it ran `./scripts/save.sh`).
  That attempt is NOT the evidence. The evidence is the before/after around it: continuum
  autosaved on its own within seconds of the re-arm. Do not cite the forced save.
- **Durability:** no follow-up needed. The on-disk config already carries BOTH settings
  (`tmux.conf` lines 55 and 325), so a future server start applies them itself; the live
  `set` only bridges the gap until then.
- **Ruled out:** restarting the laptop's tmux server as the remedy — it would have
  destroyed 9 live conversations, the exact harm this arc already caused once on the
  workbench. via: code

### ✅ ANSWERED 2026-09-08 — the crash was MY OWN SUBAGENT, not OOM
🔴 **The OOM hypothesis is REFUTED. Do not re-derive it.** I told the operator memory
exhaustion was the leading candidate; that was wrong and I retracted it.

- **Cause, from the transcripts:** at `02:54:15.802Z` = **21:54:15.802 CDT**, subagent
  `agent-a178eec65278c1fe6` (dispatched by THIS session to fix #1376's audit findings) ran:
  ```
  TMUX_TMPDIR=$SCRATCH/run tmux kill-server
  ```
  42 `tmux-spawn-*.scope` units tore down 1.2s later. `TMUX_TMPDIR` does not isolate a
  client — a run inside a pane reads `$TMUX`, whose socket path wins.
  🔴 **That agent had MEASURED the non-isolation 72 seconds earlier** (empty TMUX_TMPDIR
  dir, no socket, yet `tmux list-sessions` listed the operator's real sessions `(attached)`)
  and did not read its own output as the warning it was. Its brief warned about this exact
  hazard in capitals. **A written warning did not hold; a structural guard is the fix.**
- **Ruled out — kernel OOM kill.** ZERO `oom-kill:`/`Killed process` lines for the incident
  boot, with a **positive control**: the same grep matches real kernel OOM kills from
  2026-08-28. The pattern can fire and did not. via: measurement
- **Ruled out — systemd-oomd.** Not installed at all, so `ManagedOOMPreference` would have
  been decoration. via: measurement
- **Ruled out — tmux crashed.** Core limit `unlimited`, `core_pattern` pipes to
  systemd-coredump, no tmux core exists. via: measurement
- 🔴 **Ruled out — the scary memory numbers.** I cited peaks of 57.7G/38.2G/29.8G as
  evidence. **Those are systemd per-scope LIFETIME high-water marks printed at teardown,
  over 7–23h wall clocks** — not concurrent usage and not summable. I read a teardown
  accounting line as a snapshot. via: measurement
- **Ruled out — the V8 `FatalProcessOutOfMemory`.** It was `tsserver.js` hitting its own
  per-process heap cap in a different scope **24ms** after the real cause. Coincidence —
  a different process, a different cgroup, and it postdates the kill-server by 24ms rather
  than preceding it. via: measurement
- **Next probe:** none. The guard PR is the remedy.

### ✅ ANSWERED 2026-09-08 — how 46 of 47 conversations were recovered
Recorded because the METHOD is reusable and the next incident will need it.

- 🔴 **The plan was destroyed by the save side before recovery began.** A continuum
  autosave at 22:09:40 overwrote the 47-entry plan with the degraded 10-entry post-crash
  workspace, cheat-sheet included, no backup. **#1383 exists to fix exactly this.**
- **THE JOIN THAT WORKED, and it is deterministic, not a guess:** tmux-resurrect's `window`
  lines carry the tmux LAYOUT STRING, and every cell ends with that pane's numeric id
  (`b7fd,312x62,0,0,0` → pane `%0`). The agent-ledger writes one record PER PANE keyed on
  `pane_id`, carrying `session_id`, and records from the pre-crash server are identified by
  `tmux_pid` (`627687`). **Join on the pane id.**
- 🔴 **It carried its own POSITIVE CONTROL:** 24 windows had independently recorded their
  own `claude --resume <id>` in the resurrect pane line. The join reproduced **21 of 21
  where both sources spoke, zero disagreements**. (The other 3 had no ledger record — a
  coverage gap, not a contradiction. An early version of the check conflated the two and
  printed "METHOD NOT PROVEN" wrongly.)
- **For panes the ledger could not answer:** resurrect restores pane CONTENTS, so the old
  conversation text is in the window's scrollback. Extract the `❯ ` prompts, match with
  `find-session.py`, and disambiguate by grepping candidate transcripts for a line unique to
  one — **always with a positive control that the term exists somewhere**, so a zero means
  "not this session" rather than "my grep is broken".
- **Ruled out — ranking candidates by transcript mtime.** Worthless here: the restore run
  touched all 474 transcripts, so recency measured MY OWN recovery, not the operator's work.
  Transcripts carry no summary field either (0 of 474). via: measurement
- **Evidence preserved:** `~/.cache/restore-rescue-2026-09-07/` — both pre-crash resurrect
  saves, the degraded plan, and a 264-record ledger snapshot.
- **Residual:** ONE window unrecovered — `scratch6:2`, which was `claude · resume` in
  `~/workspace/homelab-talos`, i.e. itself mid-resume when the crash hit, so it never had a
  session bound. No ledger record, no distinctive scrollback. Not guessable.

### The round-2 delta audit of #1415 is BLOCKED on a malformed claims block
- **Symptom + exact repro:** `python3 scripts/audit-dispatch.py 1415 --round 2` exits **2** and
  refuses to emit, with `no audit-claims block in any of the 2 comment(s) read`.
- **Observed (with values):** a comment IS present on #1415 and IS an issue comment (so
  `gh pr view --json comments` sees it — I confirmed by querying that same field). It begins
  `## audit-claims — round-2 fix pass on #1415`. **It is a markdown HEADING, not the fenced
  block the parser wants**, which is:
  ```
  ```audit-claims round=<n> audited=<sha>..<sha>
  1. <what was claimed fixed>
  ```
  ```
- **Ruled out:** that it was posted as a REVIEW comment and is therefore invisible — the
  dispatcher warns about exactly that case, but the block is retrievable through
  `--json comments`, which returns issue comments only. via: measurement
- **Ruled out:** that the round-1 audit simply never happened — it ran, produced 2 BLOCKER +
  6 SHOULD-FIX + 5 NIT, and its findings were fixed in `a8c47b05`. via: measurement
- **Next probe (verbatim):**
  ```bash
  python3 ~/workspace/devrc/scripts/audit-dispatch.py 1415 --round 1 --emit-claims --audited 24c77099
  ```
  That run refuses its own brief and STILL prints the block — the two halves are independent.
  Post the printed block as an ISSUE comment, then re-run `--round 2`.
  ⚠ Or run round 2 as an explicit first, full audit with no `--round` — cheaper, and it is
  what the operator's "proceed regardless" most plausibly authorises.

### ✅ CLOSED — "The round-2 delta audit of #1415 is BLOCKED on a malformed claims block" is RESOLVED
🔴 **The block by that name above is RETIRED — do not re-run its `Next probe`.** It is fixed, and
its diagnosis was correct: the comment was a markdown HEADING, not a fenced block.
- **Fixed:** a fenced ```audit-claims block restating the round-1 fix pass was posted as an ISSUE
  comment. `python3 scripts/audit-dispatch.py 1415 --round 2` then exited 0 and emitted a
  17,630-byte brief.
- **Ruled out:** that `--emit-claims` prints a ready-to-post block — it prints a TEMPLATE with
  `<one line per thing…>` placeholders, so the claims still have to be written by hand from the
  prose comment. The handoff's "that run … STILL prints the block" is true but easy to misread as
  "prints the filled-in block". via: measurement
- **Next probe:** none. The lesson is in Gotchas.

### 🟡 #1415's staged sudo script silently reverts `/etc/nixos/configuration.nix` on a re-run
Found by the round-2 delta audit. **In the STAGED half — merging #1415 does not run it.**
- **Symptom + exact repro:** run `nix/system/apply-tmux-oom-protection.sh` once successfully;
  edit `/etc/nixos/configuration.nix` by hand; re-run the script and have `nixos-rebuild switch`
  fail for ANY reason. The ERR trap restores the FIRST run's backup over the live config.
- **Observed (with values):** measured against the real script with only its environment
  couplings patched (paths, the `$EUID` check, the selector pre-flight, `nixos-rebuild`) — the
  backup/trap/`restore()` logic untouched:
  ```
  RUN 1 (rebuild succeeds) -> import wired, configuration.nix.bak.tmux-oom created, rc=0
  operator hand-edits configuration.nix (an unrelated line)
  RUN 2 (already wired; rebuild FAILS)
    "FAILED — restoring …/configuration.nix from …/configuration.nix.bak.tmux-oom"
    import present in CFG: 0      <- the live, correctly-applied import is GONE
    operator edit present: 0      <- the unrelated edit is GONE
    CFG identical to the pre-wiring backup: YES
  ```
  Mechanism: `BACKUP="${CFG}.bak.tmux-oom"` is a FIXED name, but `cp -a "$CFG" "$BACKUP"` runs
  only inside the `else` branch that does the wiring. A re-run takes the already-wired path and
  creates no backup, while `restore()` — armed by `trap restore ERR` for the whole script,
  including the closing `nixos-rebuild switch` — still finds the PREVIOUS run's file. `cp -a`
  restores the old mtime too, removing the obvious tell.
- **Ruled out:** that the previous timestamped-backup spelling had this bug — it did not. There,
  a re-run's `$BACKUP` names a file that was never created, so `restore()` correctly does
  nothing. NIT 7's fix traded a cosmetic lie for a data-loss path. via: code
- **Ruled out:** that the existing test covers it.
  `test_restore_does_not_claim_to_restore_a_backup_that_does_not_exist` asserts the literal
  `-f "$BACKUP"` appears inside `restore()` and that the string `nothing to restore` is present.
  That is a guard on the SPELLING of the source, and the spelling it pins is exactly what makes
  the stale restore fire — the `-f` test passes *because* a stale backup exists. via: measurement
- **Leading hypothesis:** gate `restore()` on whether THIS run took the backup (a flag set beside
  the `cp -a`), not on whether the file exists.
- **Next probe:** none for the diagnosis — it is measured and closed. The open work is the fix,
  which must be pinned BEHAVIOURALLY (run 1 succeeds, run 2's rebuild fails, assert the config
  still carries the import), never by grepping the source again.

### 🟢 #1415's staged script leaves a backup behind on a refusal
- **Symptom + exact repro:** trigger the `could not find an 'imports =' list` exit.
- **Observed (with values):** `cp -a "$CFG" "$BACKUP"` has already run by then, so a refusal that
  modified nothing still deposits `configuration.nix.bak.tmux-oom` into a directory the script's
  own comment notes already holds 18 such files.
- **Ruled out:** that it is purely cosmetic — it also ARMS the SHOULD-FIX above for the next run.
  via: code
- **Next probe:** fold into the same fix.

### ✅ CLOSED — the two open PRs of rank 1 and rank 3 are MERGED, SHIPPED and VERIFIED
🔴 **Retires "The three PRs still open" and "THE GUARD IS NOT LIVE" above — do not re-derive them.**
- **#1415** merged `176f412b`, **#1376** merged `e55533ea`. Both verified by content in
  `origin/main` (never by ancestry — a squash merge makes `--is-ancestor` false forever).
- **Ruled out:** that Tekton's green was sufficient — #1415's legs were green on a base **11
  commits stale**, and gating the merged tree is what found the break below. via: measurement
- **Ruled out:** that #1376's fixes were unpushed, as the previous handoff said — they were 4
  commits in `.claude/worktrees/agent-a91d846562d761c79`, and the push was a genuine
  fast-forward (`4fbe8440..83e10200`, **0 commits discarded**). via: measurement
- **Next probe:** none. Rank 7 (`TMUX_TMPDIR=%t` on the unit) is **SUBSUMED by #1376** — measured
  present at `nix/home.nix:4814`. Do not do it again.

### ✅ ANSWERED — #1415 and #1376 were each green ALONE and RED MERGED, on disjoint files
- **Symptom + exact repro:** merge `origin/main`+#1415 with #1376 and run
  `test_guard_core.py -k "kill_server_call_site or shell_text_writes_a_kill"`.
- **Observed (with values):** two failures —
  `added: ['scripts/tests/test_tmux_restore_trigger.py']` from the two-way
  `_KILL_MENTION_LEDGER`, and `shell text this guard denies, outside the named files:
  [('scripts/tests/test_tmux_restore_trigger.py', <the wide-kill spelling>)]`. #1415 added the
  ledger; #1376 added a file whose class docstring names that command in PROSE. **Zero file
  overlap between the two diffs.**
- **Ruled out:** that a clean `git merge` said anything about it — the merge was textually clean
  and `mergeable: CLEAN`. via: measurement
- **Ruled out:** that it was a defect in either PR — the ledger is two-way BY DESIGN so a new file
  must be classified by a human. via: code
- **Fixed** in `27b94ffe`: classified in `_KILL_MENTION_LEDGER` and named in the shell-text
  scanner's allowlist. Red-at-base confirmed: 2 failed → 3 passed, scanner's positive control
  green throughout.
- **Next probe:** none. The lesson is in Gotchas: this is the RULES.md "disjoint files are not
  safety" case, and the trigger was the base MOVING.

### ✅ ANSWERED — `ship.sh` could not reach the laptop, and #1439 fixed it (verified)
- **Symptom + exact repro:** `bash scripts/ship.sh` → laptop leg `ssh: connect to host
  192.168.50.155 port 22: Connection timed out`, `[laptop] converge exited 255`, final verdict
  `cross-host agreement NOT COMPARED`. Hit TWICE today; both times the laptop was converged BY
  HAND over nebula (`zach@10.42.0.100`).
- **Observed (with values):** after #1439 merged, the same command printed
  `ship: zach@192.168.50.155 did not answer — falling back to zach@10.42.0.100 for laptop.` and
  ended `converged + verified — 2 hosts compared, both at 605b29ac (local=workbench remote=laptop)`.
- **Ruled out:** that the laptop was unreachable — nebula answered throughout; only the LAN
  address was silent. via: measurement
- **Next probe:** none. ⚠ Note the failure mode this closed: a `NOT COMPARED` verdict is a SKIP,
  not a pass, and nothing else converges the laptop.

### ✅ ANSWERED — the restore stack DOES operate on the laptop, end to end
- **Symptom + exact repro:** operator asked whether the chain works on `zach@10.42.0.100`.
- **Observed (with values):** save side alive — `status-right` carries `continuum_save.sh`,
  `@resurrect-hook-post-save-all` set, `~/.tmux/resurrect/last` and `restore-plan.json` both
  written **5 minutes before the check**, plan = **12 entries, 12/12 with a session_id** (11 `ok`,
  1 `transcript-missing`), 4 distinct cwds. Restore side ready — path unit active,
  `ConditionPathExists` satisfied, script running from the working tree; a live `--dry-run`
  resolved all 12 and correctly reported `would relaunch 0 windows, skipped 12` (claude already
  running in each).
- **Ruled out:** that the laptop's tmux socket is in `/tmp`, which `nix/home.nix` asserted — the
  server's own `/proc/<pid>/environ` carries `TMUX_TMPDIR=/run/user/1000`, the socket is at
  `/run/user/1000/tmux-1000/default`, and `/tmp/tmux-1000` **does not exist**. via: measurement
- **Ruled out:** the failure that comment PREDICTS for that arrangement ("silently reports zero
  windows") — measured over the exact non-interactive-ssh path `session-manager` uses,
  `TMUX_TMPDIR` is present and `tmux list-windows -a` returned **29 windows**. via: measurement
- **Leading hypothesis:** it works because `TMUX_TMPDIR` is in the ssh session environment from an
  UNDECLARED source — `grep -c TMUX_TMPDIR ~/.zshenv` on the laptop is **0**, so devrc is still
  not what sets it. The hazard is MASKED, not absent. **#1460 corrects the comment.**
- **Next probe:** none. ⚠ The laptop's tmux server dates from **Aug 28 18:39**, so its save side
  is alive only because a previous session applied two settings to the LIVE server; the on-disk
  config carries both, so a future server start re-applies them.

### 🟡 The laptop's `transcript-push.service` is FAILING, with a specific cause
- **Symptom + exact repro:** `systemctl --user is-active transcript-push.service` → `failed` on
  the laptop; it is what makes the user manager report `degraded` on every switch.
- **Observed (with values):** `transcript-push: push not accepted (HTTP '413'): 413 Request
  Entity Too Large — nginx/1.29.4`, then `Main process exited, code=exited, status=5`.
  Last run 2026-09-09 14:32:08 CDT.
- **Ruled out:** that it is a missing binary — `status=5/NOTINSTALLED` is systemd's label for
  exit code 5, not a statement about installation. via: code
- **Leading hypothesis:** the laptop's transcript payload exceeds `client_max_body_size` on the
  clawgate ingress. The fix is either that ingress limit (which lives in **homelab-talos**, a
  different repo) or a cap/chunk in the pusher.
- **Next probe:** decide which side owns it before editing anything — this was reported rather
  than fixed, deliberately, to avoid widening scope into another repo unasked.

### 🔴 #1383 is audited and needs FOUR fixes before merge — findings recorded so they are not re-derived
First full audit, 2026-09-09. Verdict **merge after fixing 🔴 1**. Line numbers in
`scripts/tmux-session-restore.py` at PR head `2ece276c`.
- **🔴 1 · `free_generation_stamp` silently overwrites a generation — `:505-558`.** The anchor
  parses a LOCAL-time stamp, so when the clock repeats (DST fall-back) or steps back >60s the
  anchor lands behind `now`, `stamp > newest` can never be satisfied, and `:558` returns the last
  candidate **with no occupancy check**. Measured on this host's zone (`America/Winnipeg`), 4/4
  fixtures, in the production call sequence: a bound session id and its cheat-sheet destroyed,
  **rc 0, nothing printed**, `prune_generations` reporting `0 pruned`. The docstring calls this a
  "bounded, VISIBLE loss" — it is not visible. Fix: stamp in UTC (`time.gmtime`), or at minimum
  refuse to return an occupied stamp.
- **🟡 2 · the concurrent-save race the docstring names is not closed — `:580`, `:595`.**
  `scripts/tmux-post-save.sh:21` launches `save` backgrounded and disowned with **no lock**; the
  implemented test covers only the SEQUENTIAL same-second case. Two processes in the same second
  use **fixed temp names**: measured `FileNotFoundError` out of `os.replace`, a generation holding
  one process's bytes under the other's rename, and `FileExistsError` from `os.symlink`. Fix:
  per-process temp names (`.{pid}.tmp`) + `O_EXCL`.
- **🟡 3 · `prune_generations` reports stamps it failed to delete — `:639-664`.** `:660-663`
  swallows the unlink `OSError` then appends to `removed` unconditionally. Measured:
  `2 kept (max 1), 1 pruned` while **nothing was pruned**.
- **🟡 4 · the shrink report can name a file the same save just deleted — `:739` vs `:757-765`.**
  `protect=(stamp,)` does not protect `previous_gen`, the file the recovery command names.
  Measured at `KEEP_GENERATIONS=1`: report names a path that `exists() == False`. Fix:
  `protect=(stamp, previous_stamp)`.
- **Ruled out:** a semantic conflict with #1376, which was the main worry — `cmd_save` hangs off
  the continuum `post-save-all` hook, not the socket-triggered unit; the plan is read at `:959`
  **before** `wait_for_tmux_server()` at `:969`, so a save cannot swap the plan under a running
  restore; the no-server refusal writes nothing so it cannot rotate or prune. via: code
- **Ruled out:** that #1383 re-breaks the kill-mention ledger — it adds no new file and no
  wide-kill prose; merged tree `e142d06d` ran **1714 passed** including that ledger with its own
  positive control green. via: measurement
- **Ruled out:** that its regression tests are vacuous — **12 of the 15 new tests are RED at base
  `c5e425c7`**, failing on the data loss itself with their own messages, and the `-L` guard was
  killed by two independent mutants. via: measurement
- **Next probe:** fix 1–4 in one commit, re-run the change-scoped subset, merge.

### 🟡 A — one bad filename in the generations dir BRICKS every future save, permanently and silently
Found by #1383's round-2 delta audit. **Introduced by my own round-1 fix 1b** (the new `raise`).
- **Symptom + exact repro:** put a well-shaped but impossible stamp in the generations dir —
  `restore-plan_20260931T000000.json` (September 31st) — beside one real generation, then save.
- **Observed (with values):** same fixture, both trees:
  ```
  BASE  -> returned '20260910T233348'   (save proceeds)
  HEAD  -> RuntimeError: could not claim a free generation stamp newer than
           '20260931T000000' ...        (SAVE FAILS)
  ```
  Any `\d{8}T\d{6}` name that sorts above today's UTC stamp and fails `strptime` does it — one
  corrupted digit (`…T990000`, `…0932T…`) is enough.
- **Ruled out:** that it is self-healing — `prune_generations` deletes from the **older** end, so
  the poison file is the one thing pruning can never reach. via: code
- **Ruled out:** that anything surfaces it — `tmux-post-save.sh` backgrounds and disowns, so the
  traceback goes to `~/.cache/tmux-session-restore.log` and the plan silently stops being
  refreshed. Only `plan_staleness_hours` notices, much later. via: code
- 🔴 **The comment two lines above it is now FALSE**: the `except ValueError` arm still reads
  *"Leave the base alone and let the step loop do what it can rather than crash the save."*
- **Leading hypothesis:** make the raise conditional on having TRIED real candidates, or advance
  `base` past an unparseable `newest` instead of leaving it.
- **Next probe:** none — measured. This is the first follow-up to open.

### 🟡 B — the `calendar.timegm` half of the UTC fix has NO guard, and it is the half that matters here
- **Symptom + exact repro:** revert the anchor at `tmux-session-restore.py:590` to `time.mktime`
  while keeping the UTC stamper, and run the suite.
- **Observed (with values):** mutant **SURVIVED** with `187 passed`. Its actual effect, six
  consecutive saves in five zones:
  ```
  America/Winnipeg  20260911T041853 → … → 20260912T051858   final +25.00h
  UTC / Asia/Tokyo / Pacific/Auckland                        final  +0.00h
  ```
  Runaway **+5h per save** on the operator's own zone (any host west of UTC), clamped to zero east
  of it. The shipped `timegm` version is +0.00h in all five.
- **Ruled out:** that the existing DST test covers it — that test calls `generation_stamp`
  directly and never exercises the anchor; the end-to-end one is self-labelled non-deterministic.
  via: measurement
- **Next probe:** add a guard that exercises the ANCHOR across saves, not just the stamper.

### 🟡 C — `free_generation_stamp` is no longer a query: a failed save leaves a zero-byte "generation"
- **Symptom + exact repro:** interrupt between the `O_CREAT|O_EXCL` claim and `_write_atomic`.
- **Observed (with values):**
  ```
  file created by the claim : restore-plan_20260911T041824.json  size 0
  list_generations sees it  : ['20260911T041824']
  prune(keep=2)             : the EMPTY one is RETAINED as a generation
  restore --plan <empty>    : JSONDecodeError -> uncaught at cmd_restore:1384
  ```
  It **is** counted toward `KEEP_GENERATIONS`; it is **not** selected as `previous_gen`, **not**
  named by the shrink report, **not** auto-restored. `read_plan` copes (returns `None`), but
  `cmd_restore:1384` uses a bare `json.loads`.
- **Ruled out:** that it needs a SIGKILL — `adopt_pre_generation_files:708` claims the stamp
  BEFORE `PLAN.read_text()`. Measured with a non-UTF-8 legacy plan: `UnicodeDecodeError`, and a
  0-byte file left behind and listed as a generation. via: measurement
- **Ruled out:** that it is live today — 129 plan files on the workbench, **0 of size 0**.
  via: measurement
- **Next probe:** none — measured. Second follow-up to open.

### 🟢 Open, lower severity (all measured, none live today)
- **D** — two thirds of round-1 claim 4 ship unguarded: mutants dropping the failure-unlink and
  dropping the pid from `_point_at` both SURVIVED a 187-green suite.
- **E** — per-PID temp names converted one bounded leftover into **unbounded per-PID litter that
  nothing reaps**: `prune_generations(keep=0)` reaps none of six simulated orphans, and
  `list_generations()` correctly returns `[]`, so nothing will ever see them.
- **F** — `test_a_concurrent_save_cannot_take_a_stamp_another_save_claimed` does not pin `O_EXCL`;
  it pins the side effect. Dropping `O_EXCL` was killed by a DIFFERENT test. The docstring reads
  as the atomicity guard and is not one.
- **G** — the DST parametrisation leaks libc's timezone into the rest of the pytest process
  (`monkeypatch.setenv` restores the var but never re-runs `tzset()`). Latent under xdist; a
  deliberate hunt for a victim found none (1752 passed).
- **Round-1 🟢 6, 7, 8 remain open**: `read_plan` has no element-type guard (`:775`), the two
  pointers still move non-atomically (`:833-834`), `_write_atomic` has no `fsync`.

### ✅ ANSWERED — the mixed local/UTC generation ordering on disk is SAFE, and it is not luck
- **Observed (with values):** measured at five offsets (−6, 0, +5.75, +9, +12) with three legacy
  local-time generations meeting three new UTC ones: in every zone the legacy stamps land on the
  **oldest** end and `prune(keep=1)` deletes the correct end. The anchor
  `max(base, timegm(newest)+1)` is what does it.
- **Ruled out:** that a pre-existing local stamp could sort above a new UTC one and misdirect
  pruning. The only cost east of UTC is one-time cosmetic misdating, self-healing as wall-clock
  catches up. via: measurement
- Live workbench state is consistent: 129 files, `20260910T012436` (local) through
  `20260911T041039` (UTC), monotonic, no gaps.

## Next steps (ranked)
🔴 **Prefer a TOPICAL claim slug over a rank-derived one** — ranks were renumbered twice in this
arc, and `claim-work --slug-for <doc> <rank>` derives the slug FROM the rank, so re-ranking
silently re-points live claims.

1. **Fix 🟡 A — the bricked save chain.** `scripts/tmux-session-restore.py:591-597`, `:612`. Make the
   raise conditional on having tried real candidates, and correct the now-false `except ValueError`
   comment in the same change.
   forcing: regression — introduced by #1383's own round-1 fix, merged and shipped to both hosts;
   one corrupted filename silently stops the restore plan being refreshed.
2. **Fix 🟡 C — the claim that creates state.** A failed save leaves a zero-byte file that
   `list_generations` counts and `cmd_restore:1384` tracebacks on.
   forcing: regression — same source; reachable without a kill via `adopt_pre_generation_files`.
3. **Fix 🟡 B — guard the anchor.** A mutant reverting `timegm` → `mktime` survives a green suite
   and costs +5h per save on this host's zone.
   forcing: regression — the fix is load-bearing exactly where the suite is blind.
4. **Merge #1480** (finding 5) and **#1481** (the measured proxy limit). Both open, both verified.
   IN FLIGHT: innovation-upstream/devrc#1480, #1481
   forcing: gate — #1480 closes a round-1 audit finding that is still open on `main`.
5. **Round 3 on #1383** after 1–3 land. Round 2 returned findings, so the ladder continues; the
   first clean round ends it.
   forcing: gate — the ladder's stop rule is keyed on findings, and round 2 produced seven.
6. **Implement opencode restore** (design decided: full auto-parity; an UNBOUND opencode pane sends
   NOTHING and is listed; the save-side tally must distinguish "no opencode panes existed" from
   "existed and I bound zero").
   forcing: user — operator asked for opencode restore parity on 2026-09-07 and chose the design.
7. **Work clawgate task 526** (drift-check chain-liveness arm) — fix its `ZacxDev/devrc` repo field
   first; this repo's origin is `innovation-upstream/devrc`.
   forcing: incident — 2026-09-07, the laptop's chain was found dead only because a human looked.
8. **Reboot to confirm the boot path.** 🔴 **ON HOLD BY THE OPERATOR.** Recorded because it remains
   the only thing that can verify this arc.
   forcing: none
9. **`--assume-empty` for `restore --dry-run`.**
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

- **This arc's audit ladders keep finding defects the previous round's fix introduced** —
  #1311 took 4 rounds, #1317 took 4, and in both, rounds 1–3 each found a real defect
  **introduced by the previous round's fix**. #1344 took 3 (round 1 refuted its whole
  discriminator; round 2 found two 🔴 and three survivors in its own mutation sweep).
  #1297/#1309/#1314 merged with no audit (operator's call, recorded). **Budget for several.**
- 🔴 **`TMUX_TMPDIR` DOES NOT ISOLATE A TMUX CLIENT. Only `-L <socket>` does.** I set
  `TMUX_TMPDIR` to a temp dir, ran two probes, and both reported the operator's REAL session —
  the contradiction was in my own output and I ran `tmux kill-server 2>/dev/null` anyway,
  destroying 22 sessions / 54 windows / 43 live claude panes. The `2>/dev/null` hid it.
  **Every tmux command in a probe must carry `-L <unique-socket>`, and never redirect the
  error stream of a destructive one.** The re-run with `-L` was clean and the real server was
  provably untouched throughout.
- 🔴 **An empty result cannot distinguish two mechanisms.** "Zero occurrences of the sent text
  in any pane's scrollback" is equally consistent with *keys discarded by an unready pane* and
  *keys delivered into a server that was then destroyed*. I picked the one I already believed
  and built a PR on it. The discriminator was in the journal all along — two distinct
  `launched by process` cohorts.
- 🔴 **A `count=1` text replace hits an occurrence you did not picture — twice in one session.**
  A mutation aimed at `_verify_sends` hit the identically-spelled skip-guard in `cmd_restore`
  and died to two unrelated tests (scored INVALID, redone). A floor edit aimed at the
  `TARGET_FLOORS` entry would have rewritten the *comment recording the previous raise*;
  asserting the occurrence COUNT first is what caught both.
- **The two gate tiers disagree structurally here** — the dev host has a live tmux server, the
  nix build sandbox has none. That difference alone produced two defects in #1351 (a 120s
  full-timeout block and an empty plan returning 1). **Run both tiers.**
- **`tmux-session-restore.py` runs from the WORKING TREE**, so editing it in the base clone is
  a live, unreviewed change to the deployed script. Work in a worktree.
- **`rerere` recorded this branch's resolution** of the `ACKNOWLEDGED_UNSTUBBED["systemctl"]`
  conflict (both #1334 and #1344 append to the same set literal and the same justification
  string). It will replay automatically — check it if that ledger conflicts again.
- **The unit's exit code is currently meaningless**: `tmux send-keys` exits 0 for keystrokes
  that go nowhere, and the unit exited 0 having started nothing.
- **Durable history, carried forward:** the boot unit **exited 1 on every boot from 2026-08-04
  until #1297+#1309** landed. Relevant because `Result` is systemd's verdict on the UNIT while
  `ExecMainStatus` is the PROCESS's exit code, and for a `Type=oneshot` they disagree
  routinely — measured on this host mid-session: `Result=success` with `ExecMainStatus=1`.

- 🔴 **A FAILED `[ x -lt y ]` IN SH DOES NOT ABORT — IT EVALUATES FALSE AND RUNS
  YOUR `else`.** The round-1 audit called the `UNMEASURED` bug "an
  integer-expected error", which understates it. Measured by reverting the fix
  and reading the output: the shell prints `integer expected`, the test is
  false, and control lands in the `else`, which printed
  `resumes: UNMEASURED reason=no-tmux-server-responding pane(s) running claude
  vs 43 send(s) logged` and returned **RC_CLEAN**. The arm whose entire job is
  to say "I do not know" returned a confident PASS. **A shell error on a
  comparison is not a loud failure; it is a silent branch flip** — which is why
  a reassuring zero from that arm was indistinguishable from a working check.
- 🔴 **A GUARD'S EXIT CODE IS A PRODUCT DECISION, NOT A CORRECTNESS ONE, WHEN AN
  `OnFailure=` IS ATTACHED.** The refusal exits **0** on purpose. Exiting 1
  would be the "more correct" reading of "we did not do the work" and would fire
  `notify-failure@%n`, which bypasses DND, on a condition that is GUARANTEED on
  every cold boot until the socket trigger lands — i.e. a standing condition,
  which `nix/home.nix` names as re-breaching the bypass. **Ask what the non-zero
  exit is WIRED TO before choosing it.** Pinned by
  `test_a_restore_with_no_tmux_server_REFUSES`, whose message names the reason,
  so a future "fix" to exit 1 fails with the argument attached.
- 🔴 **A TEST FIXTURE SPELLING A SENTINEL MORE TIDILY THAN PRODUCTION IS BLIND
  BY CONSTRUCTION.** `test_an_unmeasured_pane_count_is_not_read_as_zero_resumes`
  passed throughout while using `live_claude="UNMEASURED"`; the emitter writes
  `UNMEASURED reason=no-tmux-server-responding`. The test asserted the tidy
  form, the code matched the tidy form, and production emitted neither. **Pin
  the value the emitter ACTUALLY writes** — copy it from the emitter, do not
  retype it.
- **Every new `cmd_restore` test monkeypatches the server predicate** rather than
  reading real tmux, because the dev-host tier HAS a live server and the nix
  sandbox has NONE. A test that reads the real thing asserts a different branch
  in each tier and is structurally incapable of failing in one of them — this
  arc has already shipped two defects through exactly that gap.
- **The mutation battery is at**
  `/tmp/claude-1000/-home-zach-workspace-devrc/5542cd95-4967-4463-8fe2-0f0a75194e9d/scratchpad/mutate.sh`
  (scratch — will be GC'd). 6 mutants, positive control green, each killed by
  its OWN named assertion, run under `PYTHONDONTWRITEBYTECODE=1`. Worth
  re-creating in-repo if this shape recurs.
- **`git worktree add` on a branch already checked out elsewhere REFUSES**, and
  `worktree list` is how you find where. The prior session's worktree
  `/home/zach/workspace/devrc-bootrace` was clean and on the PR head, so the
  rework continued there. ⚠ `git -C … worktree add … | tail -5` printed
  `rc=0` for a run that had just failed — the documented `| tail` trap, hit
  again.

- 🔴 **`opencode --session <id>` IGNORES THE LAUNCH CWD AND USES THE SESSION'S OWN
  RECORDED `directory`.** MEASURED 2026-09-07 on a private `-L` socket: launched with
  `cd ~/workspace/devrc && opencode --session <id>` for a session whose DB row reads
  `directory=/home/zach/workspace/homelab-talos`, and the TUI came up IN homelab-talos.
  (a) The `cd <cwd> &&` prefix the claude path uses is **inert** for opencode. (b) 🔴 The
  cross-repo guard is therefore STRICTLY MORE important here than for claude: a mis-bound
  claude session resumes the wrong conversation in the PANE's cwd, but a mis-bound opencode
  session **silently relocates the agent into another repo** while the window, title and
  cwd all still look right. `session.directory == pane cwd` is MANDATORY, and an unreadable
  DB must leave the pane UNBOUND rather than bind on an unverified claim.
- 🔴 **`tmux -t =<name>` IS NOT THE EXACT-MATCH FORM — it matches nothing.** The `=` prefix
  wants `=<session>:` WITH the colon; a bare `=p` gives `can't find pane: =p`. My first
  opencode probe sent into nothing and polled `pane_current_command` back EMPTY twelve
  times — an observable **identical** to "opencode failed to start". The only discriminator
  was the `can't find pane` line printed BEFORE the send, which survived only because the
  destructive-probe rule forbids `2>/dev/null` on tmux calls. 🔴 Fix the CLASS: capture the
  pane id at creation (`new-session -P -F '#{pane_id}'`) and address `-t "$PANE"` — a pane
  id cannot be prefix-matched. 🔴 And give a probe its OWN positive control (send `sleep 4`,
  watch the command become `sleep`) — without it a wrong target is indistinguishable from
  the negative result you are measuring. This is the SAME empty-observable error this arc
  already made once, reproduced inside the instrument built to avoid it.
- 🔴 **A feature that BINDS NOTHING MOST OF THE TIME is the shape that rots undetected.**
  Measured ratio on the workbench: **1 opencode pane against 45 claude panes**, and the
  operator confirms that is typical. This chain was already silently broken for ~30 days
  with every link looking healthy. So the opencode work must make the save-side tally say
  "no opencode panes existed" versus "opencode panes existed and I bound zero of them" — a
  bare `0` is indistinguishable from the reader being wired to nothing.
- **`nix build path:<worktree>` copies the worktree's `.git` FILE** (a pointer to the real
  git dir), which defeats the sandbox's git-config isolation and fails `exit=2` BEFORE any
  test runs. Extract with `git archive HEAD | tar -x -C <dir>` and build from there. Used
  successfully this session; verified the extract had no `.git` and did contain the fix.
- **A green `nix build` prints NO test output to stdout** — the log goes to the daemon. A
  derivation that ran zero tests exits 0 identically. Read `nix log <drv>` and count the
  runners' own `RESULT:`/`TOTAL` lines; never quote the build's exit code as a test result.

- 🔴 **A HOST CAN BE BYTE-IDENTICAL TO `origin/main` WITH A COMPLETELY DEAD RECOVERY CHAIN,
  AND NOTHING DETECTS IT.** `drift-check.sh` checks git parity, dangling managed symlinks,
  package source currency and `settings.json` key drift — none of which sees a tmux server
  running a config from before a fix. The laptop sat 24 days in that state looking healthy.
  🔴 **The general shape: a fix that lands in a CONFIG FILE is inert until its CONSUMER
  restarts, and "deployed" checks the file, not the consumer.** Same family as the
  `home-manager switch` → restart-the-service rule, but the consumer here is a long-lived
  process nobody restarts on purpose. A drift arm asserting *"a plan exists and is fresh, and
  the live `status-right` carries the autosave hook"* would have caught this on day one.
- **`tmux-session-restore.py` RUNS FROM THE WORKING TREE**, so `git merge --ff-only` on the
  base clone IS the deploy for it — no `home-manager switch`. Confirmed after #1351 merged:
  the refusal is live on the workbench immediately. The inverse of the usual trap, and it
  cuts both ways — a host that has not pulled is running the OLD script.
- 🔴 **zsh does not word-split**, so `L="ssh -o … host"; timeout 30 $L 'cmd'` passes the whole
  string as ONE command name and dies `No such file or directory` — it does not run ssh with
  arguments. Write the command out, or use `${=L}`. Hit live this session.

- 🔴 **`pane_current_command` CANNOT distinguish a picker from a live session** — the picker
  IS the claude process, so the field reads `claude` either way. I built a safety guard on
  that field and it could never have worked. It failed SAFE (typed nothing), but it was
  answering a question the observable cannot answer. **Read `capture-pane` content.**
- 🔴 **`C-c` does NOT dismiss Claude Code's resume picker — `Escape` does** (its own footer
  says so). Verified on one window before touching eleven others.
- 🔴 **A picker-detection grep over `capture-pane` output matches STALE SCROLLBACK.** Four
  windows were flagged as stuck at a picker; all four were live sessions. `"Esc to cancel"`
  persists in history after a picker is dismissed. Limit the check to the visible tail, and
  read the screen before acting.
- 🔴 **`git log` ordering is TOPOLOGICAL, not causal — do not infer "came before" from it.**
  I nearly reported the integration gate as a false green because `#1272` listed *below*
  `#1392`, which reads as earlier and is not. `merge-base --is-ancestor` is the answer.
- **A red main can make attribution EASIER**, not harder: with a measured baseline of known
  failures, any ADDITIONAL failure is attributable. Record the baseline explicitly.
- 🔴 **`main` moved ~10 commits during one merge attempt**, including a toolchain fix that
  changed test outcomes (#1392 re-keyed the age tamper verdict that nixpkgs' age 1.3.2
  broke). **Re-check for movement immediately before merging, and re-gate if anything
  material landed** — a merged-tree gate is a claim about the tree it ran on.

- 🔴 **`audit-dispatch.py` wants a FENCED ```audit-claims block, not a `## audit-claims`
  heading.** A well-formed-looking heading refuses the next round. When briefing a fix agent
  to "post an audit-claims block", say FENCED and give the exact first line.
- 🔴 **Adding `ssh` to `_WRAPPERS` does NOT fix `ssh host tmux kill-server`, and would make
  things worse.** MEASURED by the fix agent: `_peel_variants` reads a wrapper's first non-flag
  token as the command, which for ssh is the HOST — `ssh laptop tmux kill-server` peels to
  `['laptop','tmux','kill-server']`, so the check still misses **while every other check starts
  evaluating hostnames**. A targeted ssh arm is the fix; it is in `a8c47b05`.
- 🔴 **`pgrep -x tmux` MATCHES NOTHING** — `-x` is exact on `comm`, and tmux's server `comm` is
  `tmux: server`. A staged OOM script shipped on that selector would have printed
  `adjusted 0`, exited 0, and been reported `active (exited)` by systemd every 2 minutes
  forever. Its own header quoted `tmux: server` four lines away. A test **pinned** the broken
  `-x` while reading as coverage; a mutation to `-x zzz-no-such-proc` SURVIVED.
- 🔴 **A guard's own test can certify the bug.** That is the second instance this arc (the
  other: an observe arm gated on `sends != 0` that reported RC_CLEAN on a refused boot). When a
  test pins a FLAG SHAPE, say so in its docstring so nobody reads it as pinning behaviour.
- **`-L "$sock"` now DENIES** (fix for SHOULD-FIX 3: a literal must survive in the socket's last
  path component). The repo's real callers write that form — but they are Python
  `subprocess.run([...])` lists, which the hook, gating Bash-tool TEXT, structurally never sees.
  **That reasoning is UNAUDITED; it is the thing a round-2 delta should check first.**
- **#1375 merged while this session worked** — another session picked up task 525, inventoried
  **81** gates (31 pytest targets, not the ~30 my task card guessed; 2 of the 16
  `claude-hooks/*.py` are not hooks), and returned **KEEP 49 / TIER 32 / DROP 0**, 67 rows with a
  named catch. **The safety-theatre hypothesis is largely refuted; the value is TIERING.**
- ⚠ **Both clawgate tasks I filed (525, 526) name `ZacxDev/devrc`; this repo's origin is
  `innovation-upstream/devrc`.** 525's worker flagged it rather than chasing a second remote.
  Fix 526's body before someone works it.

🔴 **THREE CLAIMS ARE STILL HELD BY THE PREVIOUS SESSION AND WILL RETURN rc 10 TO ANYONE
ELSE.** They map to ranks 1–3 below. A resuming session on the SAME host+worktree gets rc 12
(carry on); a different one gets rc 10 (STOP). **Release each as its item lands:**
`claim-work --release tmux-server-oom-protection` (rank 1, #1415) ·
`claim-work --release tmux-restore-chain-2` (rank 2, #1376) ·
`claim-work --release tmux-restore-plan-generations` (rank 3, #1383).

- 🔴 **`scripts/gate.sh` needs the flake devshell — a bare run EXITS 3 HAVING RUN NO TESTS.**
  From an ordinary shell the pytest tier refuses with `FATAL — required tool(s) missing from
  PATH: logrotate dash` (the suites would SKIP those tests and go green while testing less).
  That is a MISSING ENVIRONMENT, not a code failure. Run it as
  `nix develop /home/zach/workspace/devrc --command bash scripts/gate.sh --tier … <root>`.
  The node tier is unaffected, so a combined run shows `PASS node / FAIL pytest exit=3` — which
  reads like a real pytest failure and is not.
- 🔴 **`--emit-claims` prints a TEMPLATE, not a filled-in block.** The previous handoff's "that
  run refuses its own brief and STILL prints the block" is true, but the block it prints carries
  `<one line per thing this round's fixes CLAIM to have addressed>` placeholders. The claims must
  be written by hand from the prose comment. Budget for that rather than expecting a copy-paste.
- 🔴 **A `| tail` swallowed a failed `git worktree add` AGAIN this session** — `WT_RC=0` printed
  for a create that had actually failed on `fatal: 'refs/heads/integration' exists; cannot create
  'refs/heads/integration/merge-1415-…'`. A branch named `<x>` blocks every `<x>/<y>`. Use a FLAT
  integration branch name, and never read the status through a pipe.
- 🔴 **A trailing `echo "RC=$?"` in a BACKGROUNDED bash command makes the harness report exit 0
  for a red run.** The task notification said `completed (exit code 0)` for a gate whose own
  content said `GATE: RESULT=FAIL exit=1`. The `RESULT:`/`GATE:` line in the CONTENT is the
  authority — the same lesson `gate.sh`'s own header documents, hit through a new door.
- **A guard whose test pins the SOURCE SPELLING can be made to fail by the very condition it
  claims to check.** `test_restore_does_not_claim_to_restore_a_backup_that_does_not_exist`
  asserts `-f "$BACKUP"` is present; a stale backup makes that check TRUE and the restore wrong.
  When the artifact is a shell script, pin BEHAVIOUR by executing it with its couplings patched —
  that is how this was found, and it took about five minutes.
- **Patching a staged sudo script's couplings is a cheap, high-yield audit technique for this
  repo.** Replace the paths, the `$EUID` test, any pre-flight probe and the `nixos-rebuild` call;
  leave the trap/backup/control flow untouched; then run the real multi-run scenarios. Two of the
  script's three audit findings across rounds 1 and 2 were only visible by executing it.
- **`clawgate_handoff.sh resolve` returned rc 5 for this session** — 0 tasks, with the positive
  control showing the board reachable. Per its own instruction no `clawgate-task:` field was
  written, and that 0 is NOT a clean bill of health: a wrong session id answers 200 with an empty
  array too.

- 🔴 **THE LOCAL FULL-SUITE RITUAL BEFORE EVERY MERGE IS DELETED — CLAUDE.md changed mid-session.**
  It used to say "run BOTH tiers yourself before merging"; that requirement is gone, because the
  runs produced 27–50 concurrent full-suite runs on one 24-core box and the dev-host tier
  repeatedly hit its own 3600s cap and produced **no verdict at all** — strictly worse than not
  running it. What replaces it: **read CI** (advisory, ~42% of reds measured to be noise — act on
  the failing TEST, not the colour) and run a **change-scoped subset**
  (`nix develop ~/workspace/devrc -c python3 -m pytest <paths> -q`). I hit the deleted ritual's
  exact failure twice today before the doc changed: both full gates died on the cap, on a
  DIFFERENT target each time — which is the tell for load, since a failed assertion inflates one
  test and load inflates the whole run.
- 🔴 **`gate.sh` outside the flake devshell EXITS 3 HAVING RUN NO TESTS** — `FATAL — required
  tool(s) missing from PATH: logrotate dash`. A combined run then prints `PASS node / FAIL pytest
  exit=3`, which reads like a real pytest failure and is not.
- 🔴 **`no tests ran in 0.12s` IS A BROKEN SELECTOR, NOT A PASS.** I passed
  `scripts/claude-hooks/tests/test_tmux_session_restore.py`; the real path is
  `scripts/session-analysis/tests/`. pytest reported a cheerful zero. Corrected run: 1738 passed.
- 🔴 **`git checkout -- <file>` TO CLEAN UP A MUTATION ALSO REVERTS THE FIX YOU JUST WROTE.** I ran
  a negative control on the kill-mention ledger, cleaned up with `git checkout --`, and committed
  and pushed a tree that deleted the script while the ledger still named it — red, exactly as my
  own control had just demonstrated. Caught by reading the STAGED diff against what I intended.
  Restore a mutated file from a `cp -a` copy, not from the index.
- 🔴 **A trailing `echo "RC=$?"` in a BACKGROUNDED command makes the harness report exit 0 for a
  red run** — the task notification said `exit code 0` for a gate whose content said
  `GATE: RESULT=FAIL exit=1`, and again for a `ship.sh` that exited 255. Read the `RESULT:`/per-host
  lines in the CONTENT.
- 🔴 **`PIPESTATUS` IS A BASHISM — zsh uses `pipestatus`.** `echo "MERGE_RC=${PIPESTATUS[0]}"`
  printed empty for a `gh pr merge`, telling me nothing. Verify a squash landed by CONTENT in
  `origin/main`, never by `--is-ancestor` (false after every squash, forever).
- 🔴 **A `| tail` swallowed a failed `git worktree add` again** — `WT_RC=0` for a create that had
  failed on `fatal: 'refs/heads/integration' exists`. A branch named `<x>` blocks every `<x>/<y>`;
  use a FLAT integration branch name.
- 🔴 **The deployed guard BLOCKS YOUR OWN COMMIT MESSAGE if it quotes the incident command** — the
  hook parses heredoc lines as real commands. That is correct behaviour and it names the escape
  hatch: write the message to a file and `git commit -F <file>`. Unplanned end-to-end proof the
  guard fires through the Bash tool.
- 🔴 **`--is-ancestor` OVER A LIST OF BRANCHES IS A FALSE-POSITIVE MACHINE.** I used it to hunt
  stranded work on the laptop and got ~22 "UNMERGED" branches; nearly all were squash-merged, for
  which `--is-ancestor` is false forever by construction. Do not report that list as stranded work.
- **A laptop sha that is in NO clone is usually another session mid-flight, not a diverged host.**
  I measured the laptop at `31f60d81`, an object absent from this clone. `git reflog` on the
  laptop settled it in one command: another session checked out a feature branch at 14:20,
  committed at 14:24, and returned to `main` at 14:30. My probe landed in the gap.
- **Round 0 of `/audit-pr` earned its keep on its first real use.** It is the only round that can
  conclude *close this PR*, and it did: it attributed the requirement to a verbatim operator ask
  issued 23 minutes after the incident under a diagnosis that was refuted the next day, then
  measured that the payload is inert against 4/4 recorded OOM events (all `CONSTRAINT_MEMCG`,
  tmux unreachable from the victim cgroup). Record for its trial: `ran: 1 · changed the outcome: 1`.
- ⚠ **`CLAUDE.md` says `nix/system/` "already holds nine of these"; it holds 22 today** (21 after
  #1464). Not corrected here — flagged so the next reader does not trust the count.
- **The `scripts/tests` collected-test floor has ~710 of headroom** (13756 collected vs floor
  13026), measured before deleting 20 tests in #1464. Check this before any deletion — floors are
  per-target minimums and a deletion can breach one.

- 🔴 **A FIX ROUND INTRODUCES THE NEXT FINDING — THIS ARC HAS NOW DONE IT SIX TIMES.** #1383's
  round 2 found that ALL THREE new 🟡s were created by round 1's own fixes: the `raise` (A), the
  `O_EXCL` claim (C), and the untested anchor (B). Budget for round 3.
- 🔴 **I MERGED #1383 WITH FIVE OF NINE FINDINGS OPEN AND REPORTED "FOUR".** The audit returned
  🔴1 + 🟡2,3,4,5 + 🟢6,7,8,9. I fixed 1–4 and described the set to the operator as "the 🔴 plus
  three 🟡" — omitting 🟡 5 entirely. Count the findings in the audit, not the ones you fixed.
- 🔴 **AND I THEN TOLD THE ROUND-2 AUDITOR THAT 5 WAS "FIXED AFTERWARDS IN #1480" — IT IS NOT
  MERGED.** The auditor checked `gh pr view` and corrected me. A PR is not a fix until it lands;
  do not brief a later round on the basis of an open PR.
- 🔴 **`git checkout -- <file>` TO CLEAN UP A MUTATION ALSO REVERTS THE FIX YOU JUST WROTE.**
  Committed and pushed a tree that deleted a script while the ledger still named it — red, exactly
  as my own negative control had just demonstrated. Restore a mutated file from a `cp -a` copy.
- 🔴 **`no tests ran in 0.12s` IS A BROKEN SELECTOR, NOT A PASS.** I passed
  `scripts/claude-hooks/tests/test_tmux_session_restore.py`; the real path is
  `scripts/session-analysis/tests/`.
- 🔴 **A MUTANT THAT "SURVIVED" MAY NEVER HAVE RUN.** One scored SURVIVED because the mutating
  `python3` was not on PATH and the `command not found` was not read. Verify the mutant DIFFERS
  from the original before scoring it.
- 🔴 **A FIXTURE CAN MAKE A GUARD UNREACHABLE.** Two of mine did: a DST epoch constant a day off
  the transition (both parametrisations passed at base), and an exhaustion fixture occupying a
  contiguous run of stamps — which cannot exhaust the search, because the anchor JUMPS PAST
  `newest`. Derive fixture constants; do not guess them.
- 🔴 **A TEST DOUBLE THAT ACCEPTS WHAT PRODUCTION REFUSES IS NOT A TEST.** #1467: the stub server
  accepted any number of sessions, so a client tuned above the real cap passed every test and
  failed every tick in the field for ~19 hours. The double now enforces the deployed cap.
- 🔴 **`merged` IS NOT `deployed`, IN THE PRODUCER DIRECTION TOO.** #1408 raised the client push
  caps to match a clawgate server that was never shipped. Verify the RUNNING server before tuning
  a client to it — the app names its own cap in the refusal.
- 🔴 **TWO HOSTS, TWO ROUTES, TWO DIFFERENT ERRORS.** workbench → NodePort (no proxy, hit the APP's
  session cap); laptop → nebula (nginx in path, hit the PROXY's byte limit). Diagnosing from one
  host alone would have been wrong.
- 🔴 **`MAX_PUSH_BYTES` BOUNDS TAIL BYTES, NOT THE WIRE BODY** — 900,000 produced 922,643 B on the
  wire, 88% of the MEASURED 1 MiB proxy ceiling. Measure a proxy limit with a DELIBERATELY INVALID
  token: under the limit → 401 from the app, over it → 413 from nginx, and nothing is ever stored.
- 🔴 **`PIPESTATUS` IS A BASHISM** — zsh uses `pipestatus`. Verify a squash landed by CONTENT in
  `origin/main`, never by `--is-ancestor` (false after every squash, forever).
- 🔴 **`--is-ancestor` OVER A BRANCH LIST IS A FALSE-POSITIVE MACHINE** — it reported ~22 laptop
  branches as "UNMERGED"; nearly all were squash-merged.
- 🔴 **A trailing `echo "RC=$?"` in a BACKGROUNDED command makes the harness report exit 0 for a
  red run** — seen for a gate that said `RESULT=FAIL` and a `ship.sh` that exited 255.
- 🔴 **A failed `cd` in a compound command runs the REST IN THE WRONG DIRECTORY.** A refused
  `worktree add` left `cd` failing, and the following `git merge origin/main` ran in the BASE CLONE
  on `main`. Harmless here (a pure fast-forward), but use `git -C <path>`, which this repo's
  CLAUDE.md already mandates.
- **A laptop sha in NO clone is usually another session mid-flight, not a diverged host** —
  `git reflog` on that host settled it in one command.
- **Round 0 of `/audit-pr` earned its keep on first real use**: it attributed the OOM script's
  requirement to a verbatim operator ask issued 23 minutes after the incident, under a diagnosis
  refuted the next day, and measured the payload inert against 4/4 recorded OOM events. Trial
  ledger: `ran: 1 · changed the outcome: 1`.

## How to verify
```bash
# 1. the guard is LIVE — reproduce the SYMPTOM, do not grep
python3 - <<'PY'
import importlib.util, os
p = os.path.expanduser("~/.claude/hooks/guard_core.py")
s = importlib.util.spec_from_file_location("g", p)
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
print("incident ->", "DENIED" if m.evaluate("TMUX_TMPDIR=/tmp/x tmux kill-server","claude-code") else "ALLOWED")
print("control  ->", "DENIED" if m.evaluate("tmux kill-pane -t %1","claude-code") else "ALLOWED")
PY

# 2. socket trigger live on BOTH hosts
systemctl --user is-active tmux-session-restore.path tmux-session-restore.timer
ssh zach@10.42.0.100 'systemctl --user is-active tmux-session-restore.path tmux-session-restore.timer'

# 3. generations: UTC stamps, plan is a symlink, no zero-byte files (🟡 C's tell)
ls -l ~/.config/initiatives/restore-plan.json
find ~/.config/initiatives/restore-plans -name 'restore-plan_*.json' -size 0 | wc -l   # must be 0

# 4. transcript-push actually succeeds (not just "deployed")
systemctl --user start transcript-push.service && \
  journalctl --user -u transcript-push.service -n 3 --no-pager -o cat | grep -E 'pushed|not accepted'

# 5. both hosts converged AND compared
bash ~/workspace/devrc/scripts/ship.sh     # read every per-host line, not the verdict

# 6. change-scoped tests — NOT the full gate
nix develop ~/workspace/devrc -c python3 -m pytest \
  scripts/session-analysis/tests/test_tmux_session_restore.py \
  scripts/tests/test_tmux_restore_observe.py \
  scripts/tests/test_tmux_restore_trigger.py -q

# 7. the recovery method + evidence, if another incident needs it
ls ~/.cache/restore-rescue-2026-09-07/
```
