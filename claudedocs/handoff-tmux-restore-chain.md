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
| #1309 | `cc409f82` | continuum's `status-right` autosave interpolation clobbered by a later `set -g status-right` — no save in 30 days |
| #1314 | `dcaeb408` | zero `workspace … output` directives against a declared dual-head layout |
| #1311 | `d9f0836c` | window→conversation binding by 145-file grep instead of the deterministic per-pane ledger |
| #1317 | `946d9038` | the staleness gate counted POWERED-OFF time against the plan |
| #1344 | `1ecc03c1` | no instrument existed to read a reboot; adds `tmux-restore-observe.sh` |

- 🔴 **THE REBOOT HAPPENED — 2026-09-06 18:01:46**, from 32 days of uptime. It is
  what answered this arc and refuted the hypothesis the arc was built on; the
  ANSWERED block below carries its values. Carried forward because it is the only
  real exercise this path has ever had.
- **#1344 shipped:** `scripts/tmux-restore-observe.sh` (`pre`/`post`/`verdict`/
  `extract`), 36 tests. Its baseline lives at
  `~/.cache/tmux-restore-observe/pre-latest.txt` with a copy of the script beside
  it — that baseline is what a post-reboot `post` run compares against, so do not
  clear it before rank 4.
- 🔴 **#1351 HAS BEEN REWORKED onto the confirmed mechanism — it is no longer
  the PR the previous handoff said not to merge.** Branch
  `fix/tmux-restore-boot-race`, worktree `/home/zach/workspace/devrc-bootrace`,
  **13 commits ahead of `origin/fix/tmux-restore-boot-race` and NOT PUSHED.**
  Head `3b348542`. The two rework commits are `7056a0fa` (the refusal + prose +
  three of the four 🟡s) and `3b348542` (the UNMEASURED consequence correction).
- **Merge conflict with `main` RESOLVED** (`3c5cda1f`), by taking main's copy of
  `claudedocs/handoff-tmux-restore-chain.md` **wholesale**. The PR no longer
  touches the handoff doc at all, so main's corrected ANSWERED block survives.
  This is what the previous handoff meant by "do not let that land".
- **What the rework contains:**
  - `no_tmux_server_to_restore_into()` in `scripts/tmux-session-restore.py`,
    checked **before** the send loop — the loop's own `tmux new-session` is the
    destructive step.
  - The refusal **exits 0**, deliberately. See the Gotchas entry.
  - `wait_for_workspace_to_settle` and `_verify_sends` are **kept**, re-described
    as a secondary guard and a detector rather than as the fix.
  - Refuted-mechanism prose corrected at **four** sites (the previous handoff
    said three): `tmux-session-restore.py` settle docstring + `_verify_sends`
    stderr, and `tmux-restore-observe.sh` arm comment + `RC_RACE` message.
  - All four round-1 🟡s fixed (see the retired investigation block below).
- 🔴 **NEITHER GATE TIER HAS COMPLETED — this is UNVERIFIED.** The dev-host tier
  (`scripts/gate.sh --tier both`) was still running in the pytest leg when this
  doc was written; the nix sandbox tier was **never started**. No merge decision
  may be taken on this branch until both are green on the MERGED tree, and the
  claim must name the tier and the base sha.
- **Deploy status:** nothing deployed. Note `tmux-session-restore.py` runs from
  the WORKING TREE, so this branch's edits are live **in the worktree only**;
  the base clone `~/workspace/devrc` is untouched and still runs main's copy.

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

## Next steps (ranked)
1. **Finish gating the reworked #1351 and push it.** Both tiers on the merged
   tree, one `nix build` at a time; then `git push` (the branch is 13 commits
   ahead and unpushed, so nothing is visible to anyone yet) and update the PR
   title+body — the current title, "the resumes were sent 24s before the panes
   existed", still states the refuted mechanism. Body drafted at
   `/tmp/claude-1000/-home-zach-workspace-devrc/5542cd95-4967-4463-8fe2-0f0a75194e9d/scratchpad/pr-body.md`
   (scratch — copy it somewhere durable before relying on it).
   IN FLIGHT: innovation-upstream/devrc#1351
   forcing: gate — an unpushed 13-commit branch is invisible, and the PR as
   published still asserts a refuted mechanism.
2. **Implement the socket trigger — the half that actually restores.** 🔴 The
   rework STOPS THE DESTRUCTION; it does NOT make cold-boot restore work. After
   it lands, a cold boot refuses and the operator re-runs by hand. Trigger the
   unit on the tmux socket appearing rather than `OnActiveSec=45s`:
   `devrc/nix/home.nix` (`tmux-session-restore` unit + timer). 🔴 Do NOT reach
   for `RemainAfterExit=yes` alone — measured to work, but with
   `KillMode=control-group` the unit would then own the operator's server.
   🔴 When this lands, revisit the refusal's exit-0: it is exit 0 **because**
   no-server is a standing cold-boot state, and the socket trigger is what stops
   it being one.
   forcing: incident — 2026-09-06, a reboot left 43 conversations dead and the
   unit reported `Result=success`.
3. **Pin `TMUX_TMPDIR=%t` on the unit.** Re-confirmed live this session: the
   unit's `Environment=` carries `PATH` and `HOME` only. Its two siblings already
   pin it (`nix/home.nix:3678`, `:3891`) and both have tests.
   forcing: regression — the unit works today only because the user-manager
   environment happens to carry the variable.
4. **Reboot again to confirm whatever fix lands.** Nothing but a real reboot has
   ever exercised this path, and it has now overturned two successive hypotheses.
   `~/workspace/devrc/scripts/tmux-restore-observe.sh pre` → reboot → `… post`.
   forcing: none
5. **Run bare `claude` windows inside tmux** so a closed window detaches instead
   of vanishing.
   forcing: incident — 2026-09-06, the operator closed i3 windows; two
   conversations survived only via transcript archaeology and one opencode window
   was never recovered.
6. **`--assume-empty` for `restore --dry-run`** so a pre-reboot dry run exercises
   the send path instead of only the skip branch.
   `devrc/scripts/tmux-session-restore.py`.
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

## How to verify
```bash
# 0. WHERE THE WORK IS — the branch is unpushed; the base clone does NOT have it
git -C /home/zach/workspace/devrc-bootrace log --oneline -3
git -C /home/zach/workspace/devrc-bootrace status -sb    # expect: ahead 13

# 1. the refusal, in both directions (this is the fix)
nix develop ~/workspace/devrc -c python3 -m pytest \
  /home/zach/workspace/devrc-bootrace/scripts/session-analysis/tests/test_tmux_session_restore.py \
  /home/zach/workspace/devrc-bootrace/scripts/tests/test_tmux_restore_observe.py -q
# expect: 130+ passed

# 2. the mechanism is unchanged on this host (re-measure; do not trust the doc)
systemctl --user show tmux-session-restore.service \
  -p Type -p RemainAfterExit -p KillMode -p Environment
# expect: Type=oneshot  RemainAfterExit=no  KillMode=control-group
#         Environment has NO TMUX_TMPDIR  (that is rank 3)

# 3. 🔴 BOTH GATE TIERS, on the MERGED tree — NEITHER HAS RUN YET
nix develop ~/workspace/devrc -c bash /home/zach/workspace/devrc-bootrace/scripts/gate.sh --tier both
nix build ~/workspace/devrc#checks.x86_64-linux.pytests  --no-link   # ONE AT A TIME
nix build ~/workspace/devrc#checks.x86_64-linux.nodetests --no-link
# gate.sh's exit status is authoritative; 90 = could-not-vouch, read the log.
```
