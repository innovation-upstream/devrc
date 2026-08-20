# Handoff: load-average bar pill (shipped) + auto-reap (dropped) — 2026-08-20

Supersedes the 2026-08-19 version of this file, which described the auto-reap as
DONE and the bar block as ready to commit. Both claims are now wrong.

## Where things stand

**Shipped:** the load pill — PR #590, merged to `main` as `1d2bc985`. Verified by
CONTENT, not ancestry (a squash merge is never an ancestor): `scripts/i3status-load`
exists on `origin/main` with `ICON = "cogs"` and `loadWarnAbove = 48`.

**Dropped, by decision:** the `cpu-monitor.sh` auto-reap. Not shelved — deliberately
discarded. The working tree is clean of it (`grep -c AUTO-REAP` → 0).

**NOT deployed.** This is the one open action.

## 🔴 The open action: deploy

The change is on `main` and on **neither host**. `home-manager switch` is what makes
a `home.file` real; `git pull` changes nothing nix manages.

It was deliberately NOT shipped on 2026-08-20: `ship.sh` runs `git checkout main`
per host, and `~/workspace/devrc` was on `espanso/ssh-reachability-and-three-snippets`
with another session live on it (an `espanso/rebase-tmp` worktree sat at the same
commit). Nothing would have been lost — the other session's uncommitted
`claude/skills/resume/SKILL.md` edit is identical between its HEAD and `main`, so a
checkout carries it across — but it would have moved a live session onto `main`, and
a session on `main` that commits breaks this repo's 🔴 never-commit-to-main rule.

**When that session has landed:**
```bash
scripts/ship.sh                 # read EVERY per-host line, not the final verdict
i3-msg restart                  # 🔴 REQUIRED — see below
```

🔴 **`ship.sh` alone is not enough for a NEW block.** A switch does not restart
`i3status-rs`; the bar holds the config it parsed at startup, so the pill will be
absent on a host reporting a fully successful deploy. `claude/skills/bar/SKILL.md`
documents this; the original handoff omitted it and the first PR body did too.

## Verification still owed

Nobody has seen this block render on a real bar. Pre-deploy state was measured, so
there is a genuine before/after: `~/.config/i3status-rust/scripts/i3status-load` did
not resolve, and the live `config-top.toml` had **0** references to it.

After deploying:
```bash
readlink -f ~/.config/i3status-rust/scripts/i3status-load   # must land in /nix/store
grep -c i3status-load ~/.config/i3status-rust/config-top.toml   # must be > 0
~/.config/i3status-rust/scripts/i3status-load --warning 0.1 --critical 999999
```
The last line forces the pill visible regardless of ambient load — the honest way to
confirm it renders, since at the shipped threshold (48) it is invisible most of the day.

## What the audits caught (two rounds, both on #590)

Worth reading before touching this again — three of these were invisible to a green suite.

1. 🔴 **The icon was not an i3status-rust icon key.** `utilities-system-monitor` is an
   XDG desktop-icon name. An unknown icon does not degrade to a missing glyph —
   i3status-rust renders the whole block as a red `Failed to render full text`.
   Measured against i3status-rs 0.36.1: `cpu` → ` 󰓅 9.9 `, `utilities-system-monitor`
   → the error string, `cogs` → ` 󰒓 9.9 `. Both VISIBLE states carried it, so the only
   payload that worked was the invisible one. The suite could not see it because the
   test asserted `out["icon"] == ICON` against a constant copied from the
   implementation — an expectation derived from the code under test never disagrees.
2. 🔴 **A false parity claim.** The block warned at the core count while
   `CPU_MON_THRESHOLD` is a flat **48**, raised deliberately on 2026-08-05 to cut
   toasts from 123-267/day to 11-32/day. Keying the pill off `nproc` would have warned
   at 24 on a workbench that idles in the twenties. Now single-sourced as
   `loadWarnAbove`/`loadCritAbove` and pinned by a test.
3. **Two ways it would have shipped dead**, both now gated: the block was ungated while
   its `home.file` was `mkIf (!isLaptop)`, and the script was never `git add`ed (a
   flake omits untracked files silently — confirmed against the derivation source).
4. **A guard walkable by a comment**: `"--warning ..." in nix` was satisfied by the
   flags sitting in a `#` comment beside a command that no longer passed them.
5. **A gate that went red on a legitimate reformat** — nested `++` inside a gated
   `lib.optionals`. A permanently-red gate is worse than no gate.

## Known limitations (accepted, not bugs to re-discover)

- **The icon guard's strongest half is inert in the authoritative gate.** Inside the
  nix sandbox `/nix/store` holds only the derivation closure, so i3status-rust is not
  reachable and the in-file allowlist is the whole check. It now emits a warning when
  it falls back, so a weak green is distinguishable from a strong one. Making it strong
  there means adding i3status-rust to the check's inputs — not done.
- **The laptop pill is effectively unreachable.** The threshold is a flat 48 on both
  hosts (following `CPU_MON_THRESHOLD`, itself unconditional), so on a 4C/8T laptop it
  is ~6x oversubscription away. It still spawns every 15s. Make both per-host if that
  is wrong.
- **`i3status-rust ships a native `load` block.** It was rejected only because it has
  no hide-below-threshold behaviour. If it ever grows one, delete `scripts/i3status-load`.

## Why the auto-reap was dropped

Recorded so it is not rebuilt from the same premise. A dry run of its selection path
against the live process table picked **Brave, a running game, and an in-progress
vitest run** — none protected by `is_protected()`, which covered only root-owned
processes plus an exact-comm list. Its ignore-list safety valve was defeated by
`top`'s COMMAND truncation (`Farthes+` vs `FarthestFronti`), so a game the operator
had explicitly ignore-listed could still be killed. It had no off switch —
`CPU_MON_REAP_KILLS_PER_DAY=0` meant *unlimited*, not disabled. And the premise was
weaker than assumed: the trigger was 2x cores = 48, against a measured 15-minute load
baseline of 30 on 24 cores, i.e. 63% of the way there during ordinary work.

Decision: the three existing cpu-monitor triggers (sustained load, runaway process,
thermal) already alert; a killer added risk without adding information.
