# Handoff: stt-voice-send-as-input — 2026-10-01

## Goal
Hold-to-talk voice input (`stt-voice`, `nix/pkgs/tools/stt-voice/`) transcribed correctly but send-as-input from the transcript TUI delivered only fragments of the transcript (or nothing) to the operator's window. Fix it so the send delivers the FULL transcript to the focused window, per the operator's spec: close the TUI, short delay, then type.
- **closing-condition:** `check` — `stt-voice --version` reports 0.1.1 on both hosts AND the TUI send click-path delivers a full (zero dropped characters) transcript into a target window. Both measured 2026-10-01 ⇒ arc CLOSED unless a future send misfires (then open a NEW arc).

## Run this first — the index, one command
```bash
$DEVRC/scripts/cairn-ops/read.sh recall --repo "<path>"
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading. `scope-absent`/`scope-empty` means nothing is recorded yet. Non-blocking.

## State now
- Branch / PRs: landed straight to main via squash PRs — **#1946** (`b5dd64a6`: close-first send via detached helper) and **#1948** (`8f734885`: single-process xdotool focus+type chain). Both MERGED; remote branches auto-deleted.
- Deployed: `scripts/ship.sh` converged workbench + laptop to `8f734885`; `stt-voice --version` = 0.1.1 on this host (`/nix/store/pqjccvx…-stt-voice-0.1.1/`).
- DONE this session: root cause found and fixed; `waitTuiGone` poll; `send` subcommand; `SpawnSend`/`TypeInto` in effects; version bump 0.1.0→0.1.1; stale `$mod+m` comments corrected to `$mod+equal` (nix/pkgs/tools/default.nix, main.go header). Worktree removed, branch deleted.
- Verified against the real path: yes, on the laptop — real TUI (`stt-voice tui --entry <id> --target <catcher>`), pressed `t` via xdotool, FULL 44-char transcript landed in the catcher, TUI closed, focus on target. Pre-fix baseline was 7/44 (one run) and 0/44 (another). The REAL hold-to-talk flow (mic → release → operator presses t) not yet driven by the operator himself.
- Honest note: sub-ms mouse-cross race remains theoretically possible between the X focus pin and typing — window shrunk from 100ms+ to ~0, not to literal zero.

## Next steps (ranked)
1. Operator drives the real flow once: `$mod+equal` hold-to-talk → release → press `t` in the TUI → confirm the text lands in his focused window.
   - forcing: user
2. If any send misfires with a moving mouse during the sub-ms window (text partially landing or landing elsewhere), open a NEW arc with a repro before touching code — the fix is measured, do not re-tune blind.
   - forcing: none

## Defects (batched)
- None open.

## Gotchas / decisions / dead-ends
- 🔴 ROOT CAUSE: typing while the TUI is open loses text — i3 defaults `mouse_warping middle` + `focus_follows_mouse yes` (no override in config) steal focus back to the floating, screen-centered TUI when the send focuses the target; `xdotool type` (XTEST) then types the transcript INTO the TUI where bubbletea interprets it as keypresses (`t` re-sends, `c` copies). Close-first is the structural fix; the helper (`stt-voice send`) polls `xdotool search --class ^stt-voice$` until the window is GONE before focusing/typing.
- 🔴 X input focus ≠ i3's `_NET_ACTIVE_WINDOW`: they diverge under WM_TAKE_FOCUS async transfer and live mouse movement. `getactivewindow` reads i3's model; XTEST delivers to the X server's input focus. Hence `TypeInto` pins X focus itself (`windowfocus N`) in the SAME xdotool process as the type — a two-exec focus→type gap (100ms+) is where a live mouse crossing lands.
- 🔴 Leading keystrokes drop after a focus transfer: probed 'CHAINED' arriving as 'e CHAINED' (CH landed nowhere); the chained `sleep 0.1` between windowfocus and type fixed it (full delivery).
- Dead-target leak guard is structural: a failed `windowfocus` (BadWindow) aborts the xdotool chain before the type runs — probed rc=1, nothing typed.
- 🔴 THE TEST HARNESS EATS ITS OWN EVIDENCE (burned 3×): a pty catcher that flushes only at exit loses everything if the window is killed first, and a quiesce-deadline reader exits early when it receives nothing. Incremental flush (write+flush per read) is the only readable catcher. Re-derive it from `scripts/`-style pty capture, never from `stty`/`dd` (stty needs a controlling terminal — fails under `setsid`; alacritty children have no controlling tty).
- `setsid alacritty … &` spawns fine from the bash tool but the X server aggressively recycles window ids (same id observed across three different alacritty instances) — never treat a window id from an earlier run as still-meaning-the-same-window; re-resolve by class each time.
- devrc is PUBLIC: never commit real transcript text (captured speech). This doc, the commits and the PR bodies describe shapes and counts only.
- The opencode-dispatch-style i3 test etiquette applies: scratch windows get UNIQUE classes per run, cleanup via `i3-msg '[class="…"]' kill`, record/restore PREV_WIN + PREV_WS before/after; expect the live operator's mouse to perturb focus between probes.
- The TUI's send keymap: `t` or `ctrl+enter`; target<=0 refuses in words ("no target window was captured") — that guard is TUI-side and unchanged.

## How to verify
```bash
stt-voice --version   # 0.1.1 on both hosts
```
Real click-path: hold `$mod+equal`, speak, release; in the floating transcript TUI press `t`; expect the TUI window to close and the full transcript to appear in the previously-focused window. Programmatic variant: spawn a raw-mode pty catcher (incremental flush), run `stt-voice tui --entry <newest id> --target <catcher id>` inside an alacritty of class `stt-voice`, focus it, `xdotool key t`, expect the full transcript incrementally in the catcher file and `xdotool search --class ^stt-voice$` to find nothing.
Tests: `nix develop ~/workspace/devrc -c bash scripts/run-go-tests.sh` (floor: cmd/stt-voice, internal/effects, internal/ui all pinned) and `nix develop ~/workspace/devrc -c python3 -m pytest scripts/tests/test_i3_stt_voice.py -q`.
