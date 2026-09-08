# Handoff: i3-game-mode — 2026-09-08

## Run this first — the index, one command
```bash
cairn recall --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Stop i3 swallowing keys while gaming. `nix/i3/config.nix` sets `set $mod Mod1` — **$mod is
ALT** — so i3 holds a global X11 grab on ~60 Alt combos (Alt+Tab, Alt+1..0, Alt+Shift+1..0,
Alt+f/d/e/a/b/n/r/h/j/k/l/space/Return/grave/minus/equal). A grab means the keypress reaches
i3 and **never reaches the focused window**, so inside a game every one of those keys is
dead. The user noticed it as "$mod+d pops rofi over my game"; that is one symptom of a
~60-key class.

## State now
- **PR #1345 MERGED** 2026-09-06T18:19:37Z, squash commit **`580b4848`**. Verified by
  CONTENT (not ancestry — a squash never makes the head an ancestor): `mode "game"` and
  `gamemodeBlock` are in `origin/main`, `scripts/i3status-gamemode` exists there.
  Re-confirmed 2026-09-08: `580b4848` IS an ancestor of `origin/main` (now `97ec9912`).
- **Shipped to BOTH hosts** via `scripts/ship.sh` — workbench + laptop both landed
  `580b4848`, every per-host line `✅ VERIFIED`, no skips. Laptop fast-forwarded
  `8e9428ef -> 580b4848`.
- **Consumer restarted** (`i3-msg restart`) — this is the step that makes the pill appear;
  a switch alone leaves it absent on a host reporting a fully successful deploy.
- **Re-verified live 2026-09-08** (two days after ship, after other sessions had switched
  this checkout to another branch): pill still in `~/.config/i3status-rust/config-top.toml`,
  `mode "game"` still in `~/.config/i3/config`, block script resolves into `/nix/store`,
  renders `{"text": " \U000f0297 ", "state": "Idle"}` rc 0, binding state `default`.

Files (all on `origin/main`): `nix/i3/config.nix`, `nix/graphical.nix`,
`scripts/i3status-gamemode`, `scripts/tests/test_i3_game_mode.py`,
`scripts/tests/test_bar_status.py`, `scripts/tests/test_no_real_launchers.py`,
`claude/skills/bar/SKILL.md`, `claude/skills/i3/SKILL.md`.

**How it works.** `mode "game"` is an EMPTY i3 binding mode — emptiness is the mechanism.
Entering a mode ungrabs every default-mode binding and grabs only that mode's. Its only
bindings are the two escapes:
```
mode "game" {
        bindsym Pause       mode "default", exec --no-startup-id pkill -RTMIN+18 i3status-rs
        bindsym Scroll_Lock mode "default", exec --no-startup-id pkill -RTMIN+18 i3status-rs
}
```
Way IN is the bar pill (gamepad glyph, both hosts, left-click → `i3status-gamemode --toggle`);
way OUT is the pill again or Pause/Scroll_Lock. There is deliberately **no default-mode
keybind to enter** — that would be one more Alt grab for no gain.

## Open investigations — live diagnosis state

### Does i3 actually release the grabs to a game? — UNVERIFIED, and it is the whole claim
- **Symptom + exact repro:** unknown — never exercised against a real game. Everything
  shipped proves the *mode switches* and the *pill tracks it*; nothing proves a game
  RECEIVES Alt+Tab while the mode is active.
- **Observed (with values):** toggle works end to end on the live WM —
  `{"name":"default"}` → `--toggle` → `{"name":"game"}` → render
  `text=' \U000f0297 GAME ' state='Critical'` → `--toggle` → `{"name":"default"}` →
  `text=' \U000f0297 ' state='Idle'`. Fail-safe confirmed live with an absolute
  interpreter and `PATH=/nonexistent-dir`: `{"text": "", "state": "Idle"}` rc 0, and
  `--toggle` prints nothing at all.
- **Ruled out:** "a guard script on the exec would do" — no; i3 has already eaten the key
  by the time the exec runs, so the game stays deaf either way. via: code
- **Ruled out:** "bind conditionally on the focused window" — `bindsym` takes no
  `[class=…]` criteria; i3 has no such mechanism. via: doc
- **Leading hypothesis:** it works — mode switching is i3's documented grab mechanism and
  4.24 is current. But this is a property of i3, not of this repo, and **no hermetic test
  can observe it**, which is why no test here asserts it.
- **Next probe:** launch Anno 1800 or Darktide, click the gamepad pill, and confirm Alt+Tab
  and Alt+1 reach the game rather than i3; then press `Pause` and confirm i3 takes them
  back. If a key still does not reach the game, check whether the title is running under
  gamescope (`gamescopeSession.enable = true` is set) — a nested compositor is the most
  likely thing to break the assumption.

### `test_tmux_reply_agent.py` is flaky — proven, unrelated to this work
- **Symptom + exact repro:** `nix build .#checks.x86_64-linux.pytests` intermittently fails
  `scripts/tests/test_tmux_reply_agent.py::test_the_EXACT_session_still_works` (line ~1533,
  `assert not err, err`).
- **Observed (with values):** `tmux did not report the new window's identity (got
  '%2\tscratch20')` — a *well-formed* answer (pane `%2`, session `scratch20`) that the
  parser rejected. **The same derivation hash** `v48qkchq18hlc32y1dhikyhril79mbs1-devrc-pytests.drv`
  — identical inputs — produced `RESULT: FAIL (exit=1)` and then `RESULT: PASS (exit=0)`
  (21931 passed / 0 failed). Same inputs, two outcomes.
- **Ruled out:** "PR #1345 caused it" — the PR touches no tmux code, and its `PATH`
  replacement (`test_i3_game_mode.py`, pointing PATH at a non-existent dir to exercise the
  pill's fail-safe) is a per-subprocess `env=` dict, not a global mutation, so it cannot
  reach another test file's tmux server. via: code
- **Ruled out:** "pre-existing on the base" — base `f58d2df0` built GREEN while the merged
  tree built RED, so base-vs-merged could NOT settle it; the same-derivation re-run did.
  via: measurement
- **Leading hypothesis:** real-process tmux test racing its own server. A
  `fix/three-real-process-test-flakes` branch already exists for this class.
- **Next probe:** loop the single test against a real tmux server and count failures:
  `nix develop ~/workspace/devrc -c python3 -m pytest scripts/tests/test_tmux_reply_agent.py -k test_the_EXACT_session_still_works -q` in a loop of 20.

## Next steps (ranked)
1. **Confirm the grabs actually release, in a real game** (workbench; no files — a live
   check). The one claim nothing here proves. Exact steps in the Open-investigations block
   above.
   forcing: user — the operator asked for this feature; it is shipped but unproven against
   its actual use case, so the shipped state is currently a claim rather than a result.
2. **Phase 2 — auto-flip game mode on window focus** (`~/workspace/devrc`, would touch
   `nix/graphical.nix` + a new i3-IPC watcher script + tests). BLOCKED on real `WM_CLASS`
   samples: run `xprop WM_CLASS _NET_WM_STATE` on a running Steam/Proton and Lutris game
   window first. 🔴 A game predicate ALREADY EXISTS at `scripts/cpu-monitor.sh:78`
   (`IGNORE=anno,logd`, `is_ignored()`) — consolidate onto it, do not grow a second list.
   Design constraint carried forward: the manual escape keys must survive, because a dead
   watcher plus no manual escape strands the operator with no window management.
   forcing: none
3. **Fix the flaky `test_the_EXACT_session_still_works`** (`~/workspace/devrc`,
   `scripts/tests/test_tmux_reply_agent.py` + whatever `AGENT.open_window` parses). Fits
   the existing `fix/three-real-process-test-flakes` branch.
   forcing: gate — it reddens `nix build .#checks.x86_64-linux.pytests` intermittently, so
   the sandbox tier cannot be read as a clean signal until it is fixed.

## Gotchas / decisions / dead-ends
- 🔴 **Adding a bar block is RESTART-class, not reload-class.** The sequence is
  `ship.sh` → **`i3-msg restart`** → confirm the pill renders. `i3-msg reload` is not
  enough: i3status-rs reads its toml once at startup. Skip the restart and the pill is
  simply absent on a host reporting a fully successful deploy.
- 🔴 **`pgrep -x i3status-rs` returns EMPTY even when the bar is running** — the kernel
  `comm` is `.i3status-rs-wr` (a nix wrapper). Use `pgrep -a i3status-rs` or
  `pgrep -f 'i3status-rs /home'`. A `-x` zero here reads as "the bar is dead" and is wrong.
- 🔴 **Testing the pill's fail-safe by clearing `PATH` breaks the SHEBANG, not the script.**
  `#!/usr/bin/env python3` needs `env` on PATH, so the probe exits 127 and proves nothing.
  Invoke through an absolute interpreter — which is exactly why the suite uses
  `sys.executable`, and why `test_no_real_launchers.py` required a `PINNED_PATH_CLOBBERS`
  justification for REPLACING rather than prepending PATH (i3-msg IS installed on the
  workbench, so prepending cannot make it unfindable and would query the live WM).
- 🔴 **zsh cannot interpolate the nf-md glyph in `$(...)`** — `character not in range`
  aborts the command mid-run. Redirect the block script's output to a file and decode it
  with python instead of capturing it in a shell variable. An `EXIT` trap calling
  `i3-msg mode default` is what stopped a half-finished toggle test stranding the operator
  in game mode.
- **`nix build --rebuild` does not work on a FAILED derivation** (`some outputs … are not
  valid, so checking is not possible`). A plain re-run does re-execute, because failed
  builds are not cached — that is what produced the same-hash FAIL/PASS pair above.
- 🔴 **`nix build … | tail` destroys the exit status** — `RC=0` there is `tail`'s. This
  cost a wrong "sandbox tier passed" reading in-session. Read the runner's own `RESULT:`
  line and counts; never the piped code.
- **Rejected: remapping `$mod` to Mod4 (Super).** It is the true root-cause fix — it kills
  the whole conflict class for all 79 bindings with no runtime machinery, since almost no
  game binds Super. Not chosen because it retrains muscle memory on every shortcut at once.
  Still the strongest option if game mode proves fiddly in practice.
- **Rejected: fully automatic detection with no manual escape.** A wrong class match or a
  dead watcher leaves the operator with no keyboard control of i3.
- **Signal 18** is the game pill's. 10–17 were already taken (`SIGNALS` in
  `bar-status-poll` plus 15 for notifs). It must stay consistent across
  `gamemodeBlock.signal`, the click handler, and the two `mode "game"` escape bindings —
  `test_i3_game_mode.py` pins all of them together.
- **This checkout is SHARED and moves under you.** During this effort
  `~/workspace/devrc` was switched to `feat/nct6683-fans-bar` by another session, and the
  base clone sat behind `origin/main`. Check `git branch --show-current` immediately before
  any write, and do handoff/doc commits from a worktree off `origin/main`.

## How to verify
```bash
# 1. the work is in main (CONTENT, not ancestry — it was a squash merge)
git -C ~/workspace/devrc cat-file -e origin/main:scripts/i3status-gamemode && echo present
git -C ~/workspace/devrc show origin/main:nix/i3/config.nix | grep -c 'mode "game"'   # -> 2

# 2. it is DEPLOYED on this host (readlink is the arbiter, never a diff)
readlink -f ~/.config/i3status-rust/scripts/i3status-gamemode    # -> /nix/store/...
grep -c 'mode "game"' ~/.config/i3/config                        # -> 2
grep -c i3status-gamemode ~/.config/i3status-rust/config-top.toml # -> 2

# 3. the pill renders (decode via python — zsh chokes on the glyph)
~/.config/i3status-rust/scripts/i3status-gamemode > /tmp/pill.json; echo "rc=$?"
python3 -c "import json;d=json.load(open('/tmp/pill.json'));print(d['text'],d['state'])"

# 4. the toggle really flips i3's mode (ALWAYS trap the restore)
trap 'i3-msg mode default >/dev/null 2>&1' EXIT
i3-msg -t get_binding_state                                       # {"name":"default"}
~/.config/i3status-rust/scripts/i3status-gamemode --toggle
i3-msg -t get_binding_state                                       # {"name":"game"}
~/.config/i3status-rust/scripts/i3status-gamemode --toggle

# 5. RESCUE if ever stranded with the bar hidden behind a fullscreen game
#    (from the laptop, nebula 10.42.0.30)
DISPLAY=:0 i3-msg mode default
```
