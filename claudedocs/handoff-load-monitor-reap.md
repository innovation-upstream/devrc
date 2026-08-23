# Handoff: load-average bar pill (shipped + deployed) + auto-reap (dropped) — 2026-08-20

Restructured onto the canonical `State now` / `Next steps` / `How to verify` headings so
future `/handoff` runs can MERGE into it. The previous version used freehand headings, so
an update appended a second status section instead of replacing the stale one and the doc
briefly said both "NOT deployed" and "deployed". Nothing was dropped in the restructure.

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo devrc
```
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY. `scope-absent`/
`scope-empty` means nothing recorded yet: ordinary, not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal

A load-average pill on the i3status-rust bar, calm by default (hidden until load is
actually interesting). The `cpu-monitor.sh` auto-reap explored alongside it was dropped.

## State now

- Branch `main`. PRs **#590** (the pill, squash-merged as `1d2bc985`) and **#597** (the first
  version of this doc), both merged. Verified by CONTENT, not ancestry — a squash merge is
  never an ancestor, so `merge-base --is-ancestor` returns false forever and means nothing.
- **DONE, DEPLOYED and VERIFIED on both hosts.** `scripts/ship.sh` converged both to
  `origin/main` (`e7ceb1f`) and switched: workbench 488 managed artifacts / laptop 449,
  **0 dangling, 0 stale, no host skipped** (every per-host line read, not the verdict).
- The workbench had *already* been switched before that — another session's switch picked
  up `main` including the pill.
- Workbench bar restarted (`i3-msg restart`); the live `i3status-rs` now starts AFTER its
  config mtime, so it has parsed the block. Before the restart it predated its own config
  by **26 hours** — deployed but not live, exactly the failure this doc warned about.
  Desktop state recorded and unchanged across the restart (same window, same workspace).
- **Auto-reap: dropped by decision.** Not shelved. Tree is clean (`grep -c AUTO-REAP` → 0).

### Verified, with the values

```
bar's exact command : i3status-load --warning 48 --critical 72
  at live load 4.34 : {"text": "", "state": "Idle"}      <- correct: hides below 48
  forced Warning    : {"icon":"cogs","text":"4.3",...,"state":"Warning"}
  forced Critical   : {"icon":"cogs","text":"4.3",...,"state":"Critical"}
  bad threshold     : {"icon":"cogs","text":"?",...,"state":"Warning"}
  `cogs` present in the icon set the RUNNING binary loads: 1 hit
LAPTOP (the host the original bug would have broken):
  readlink -f -> /nix/store/spgql8n5z94bd8ma3v8m8vgzgq9ig328-hm_i3statusload
  ICON = "cogs" | bar config refs: 1 | renders Warning when forced | nproc = 8
```

🔴 **NOT verified: the pill has never been SEEN on the bar.** At load 4.34 against a
threshold of 48 it renders hidden — by design — so there is nothing to look at. What is
proven is that the bar re-parsed the config and that the exact deployed command produces
correct output in every state. Seeing it lit needs a genuinely loaded box.

⚠ Scope of the deploy claim: verified at `e7ceb1f`. `origin/main` has since moved
(`ff26d43`), so the hosts are a couple of commits behind again — ordinary churn from other
sessions, not a regression in this work.

## Next steps (ranked)

1. **Nothing is required.** Shipped, deployed, verified on both hosts.
2. Opportunistic: next time the workbench is genuinely loaded (>48), glance at the bar and
   confirm the pill shows yellow, red above 72. That is the only unverified claim.
3. Consider whether the **laptop** threshold should be per-host. A flat 48 on a 4C/8T box is
   ~6x oversubscription, so the pill is effectively unreachable there while still spawning
   every 15s. It follows `CPU_MON_THRESHOLD`, itself unconditional — changing one without
   the other reintroduces the drift `loadWarnAbove` exists to prevent.
4. Unrelated, noticed while here: two orphaned `i3status-rs` processes from **Aug 4**
   (`ppid 1`, dead tmux scope, NOT parented by `i3bar`). Deliberately not killed —
   attributing and reaping stray processes was outside this task.

## How to verify

```bash
# the artifact resolves into the store (readlink is the arbiter, never a diff)
readlink -f ~/.config/i3status-rust/scripts/i3status-load
grep -c i3status-load ~/.config/i3status-rust/config-top.toml     # must be > 0

# run exactly what the bar runs, then force the states you cannot otherwise see
CMD=$(grep -o '.*/i3status-load[^"]*' ~/.config/i3status-rust/config-top.toml | head -1)
eval "$CMD"                                                        # hidden below 48
~/.config/i3status-rust/scripts/i3status-load --warning 0.1 --critical 999999   # Warning
~/.config/i3status-rust/scripts/i3status-load --warning nan --critical 9        # ? pill

# is the RUNNING bar serving the current config? (a switch does NOT restart i3status-rs)
#   comm is `.i3status-rs-wr` (a nix wrapper) — `pgrep -x i3status-rs` finds NOTHING.
#   The real bar is the one whose PARENT is `i3bar`; the others are orphans.
ps -eo pid,ppid,lstart,comm,args | grep i3status-rs | grep -v grep
stat -c '%y' ~/.config/i3status-rust/config-top.toml   # the bar must start AFTER this
```

## Gotchas / decisions / dead-ends

### From the deploy

- 🔴 **`ship.sh` succeeding says nothing about the CONSUMER.** The workbench was fully
  deployed while the running bar still held a 26-hour-old config. A new block needs
  `i3-msg restart`; the switch alone leaves a host reporting complete success with no pill.
- 🔴 **`pgrep -x i3status-rs` returns NOTHING while the bar is running** — `comm` is
  `.i3status-rs-wr`, a nix wrapper. An earlier check "found no bar" for this reason and was
  an instrument failure, not a state reading. Match `/bin/i3status-rs` and identify the real
  bar by `parent == i3bar`. `pgrep -f` additionally matches the checking shell itself.
- The deploy was deliberately HELD one round: `ship.sh` does `git checkout main` per host,
  and `~/workspace/devrc` was on another session's espanso branch. Nothing would have been
  lost, but it would have moved a live session onto `main` — where a commit breaks this
  repo's 🔴 never-commit-to-main rule. It resolved when that session landed. Worth
  re-measuring rather than assuming: `claude/skills/resume/SKILL.md` went from "identical to
  main, carries across" to "differs, would block" within the hour, because #594 touched it.

### What the two audits caught (three were invisible to a green suite)

1. 🔴 **The icon was not an i3status-rust icon key.** `utilities-system-monitor` is an XDG
   desktop-icon name. An unknown icon does not degrade to a missing glyph — i3status-rust
   renders the whole block as a red `Failed to render full text`. Measured against
   i3status-rs 0.36.1: `cpu` → ` 󰓅 9.9 `, `utilities-system-monitor` → the error string,
   `cogs` → ` 󰒓 9.9 `. Both VISIBLE states carried it, so the only payload that worked was
   the invisible one. The suite could not see it because the test asserted
   `out["icon"] == ICON` against a constant copied from the implementation — an expectation
   derived from the code under test never disagrees with it.
2. 🔴 **A false parity claim.** The block warned at the core count while `CPU_MON_THRESHOLD`
   is a flat **48**, raised deliberately on 2026-08-05 to cut toasts from 123-267/day to
   11-32/day. Keying the pill off `nproc` would have warned at 24 on a workbench that idles
   in the twenties. Now single-sourced as `loadWarnAbove`/`loadCritAbove` and test-pinned.
3. **Two ways it would have shipped dead**, both now gated: the block was ungated while its
   `home.file` was `mkIf (!isLaptop)`, and the script was never `git add`ed (a flake omits
   untracked files silently — confirmed against the derivation source).
4. **A guard walkable by a comment**: `"--warning ..." in nix` was satisfied by the flags
   sitting in a `#` comment beside a command that no longer passed them.
5. **A gate that went red on a legitimate reformat** — nested `++` inside a gated
   `lib.optionals`. A permanently-red gate is worse than no gate.

### Known limitations (accepted, not bugs to re-discover)

- **The icon guard's strongest half is inert in the authoritative gate.** Inside the nix
  sandbox `/nix/store` holds only the derivation closure, so i3status-rust is unreachable
  and the in-file allowlist is the whole check. It warns when it falls back, so a weak green
  is distinguishable from a strong one. Making it strong means adding i3status-rust to the
  check's inputs — not done.
- **The laptop pill is effectively unreachable** (see Next steps 3).
- **i3status-rust ships a native `load` block.** Rejected only because it has no
  hide-below-threshold behaviour. If it ever grows one, delete `scripts/i3status-load`.

### Why the auto-reap was dropped

Recorded so it is not rebuilt from the same premise. A dry run of its selection path against
the live process table picked **Brave, a running game, and an in-progress vitest run** —
none protected by `is_protected()`, which covered only root-owned processes plus an exact
comm list. Its ignore-list safety valve was defeated by `top`'s COMMAND truncation
(`Farthes+` vs `FarthestFronti`), so a game the operator had explicitly ignore-listed could
still be killed. It had no off switch — `CPU_MON_REAP_KILLS_PER_DAY=0` meant *unlimited*,
not disabled. And the premise was weaker than assumed: the trigger was 2x cores = 48 against
a measured 15-minute baseline of 30 on 24 cores, i.e. 63% of the way there during ordinary
work.

Decision: the three existing cpu-monitor triggers (sustained load, runaway process, thermal)
already alert; a killer added risk without adding information.
