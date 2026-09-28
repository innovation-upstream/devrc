# Handoff: tmux-osc8-hyperlinks — 2026-09-19

## Run this first — the index, one command
```bash
cairn recall --repo /home/zach/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. Non-blocking: if it exits
non-zero, print the stderr line and carry on.

## Goal
URLs wrapped across lines inside tmux were only clickable on their first line (click opened a truncated URL). Fix shipped: OSC 8 hyperlink forwarding + a copy-mode open binding. Remaining: live validation on the operator's real URLs, and attributing one measured-but-unexplained behaviour.

closing-condition: judgement — Zach hovers and clicks a wrapped URL from his real workflow in tmux: the underline spans the wrap and the click opens the full URL (effort closed), or the residual is named plain-text and the DIY capture-pane -J opener is explicitly accepted or declined.

## State now
- **PR `innovation-upstream/devrc#1622` MERGED** — squash `c794c9a7`; `ship.sh` converged BOTH hosts and they agreed on `c794c9a7` (594/553 managed artifacts, 0 dangling/stale). Claim `tmux-osc8-hyperlinks` released, branch deleted. `main` has since moved (other sessions: #1784–#1796); nothing of this effort is in flight.
- **Shipped code**: `.tmux.conf` gains `set -as terminal-features ',*:hyperlinks'` + `bind -T copy-mode{,-vi} o` opening `#{copy_cursor_hyperlink}` via xdg-open (empty format → "no hyperlink under cursor", not a silent no-op). Guard test: `scripts/tests/test_tmux_hyperlink_open.py` (6 tests, real throwaway tmux server, `execvp` pty harness). Ledger row for its three `kill-server` fixture sites added to `_KILL_MENTION_LEDGER` in `scripts/claude-hooks/tests/test_guard_core.py`.
- **Deployed but NOT active on the running server**: the deployed `~/.config/tmux/tmux.conf` (nix store, verified) is not loaded by the live server until a `source-file` or restart. Measured live server pre-activation: 0 `copy_cursor_hyperlink` keys, no hyperlinks feature.
- **Honest status of the fix's halves**: the copy-mode `o` binding is the measured additive half. The `terminal-features` line is belt-and-braces on THIS host — see open investigation 1: OSC 8 emission appears to be unconditional on this build already, so the line may be inert here (harmless, correct for other tmux builds, pinned by tests).
- No clawgate task: `clawgate_handoff.sh resolve` → rc 5 NOTHING RESOLVED with a positive control (another session id resolved 11 links, so the board answered). Per the tool: not a clean bill of health — a wrong session id also answers 200/empty. No field written; no task created.

## Open investigations — live diagnosis state

### OSC 8 emission is UNCONDITIONAL on this host's tmux 3.7c — mechanism unattributed
- as-of: 2026-09-19
- **Symptom + exact repro:** headless probe — throwaway socket, server booted with NO config (`HOME=/nonexistent`), pane runs `printf '\033]8;;<81-char URI>\033\\LINK1\033]8;;\033\\'`, then a real `tmux attach` client under a python `pty.fork` harness; read the client-side byte stream.
- **Observed (with values):** OSC 8 emitted to the client for **every** TERM tested — `vt52`, `xterm-256color`, `alacritty` — with `tmux show -s terminal-features` **EMPTY**. One capture: first `\x1b]8;;` at byte 297 of 1461; the stream contains the full open+close pair (`…URI…\x1b\\` … `LINK1` … `\x1b]8;;\x1b\\`). `source-file` of the deployed config also fully activates emission for later clients, and re-applies it to the same attached client's next redraw (respawn-pane probe).
- **Ruled out:** tmux built-in default-features table granting alacritty/xterm hyperlinks — read `tty-features.c` at both `3.7` and `3.7c` tags: table is exactly mintty, tmux, rxvt-unicode, iTerm2, foot, WezTerm, XTerm, and XTerm's entry has NO hyperlinks. `via: code`
- **Ruled out:** system terminfo carrying `Hls` — `infocmp alacritty`/`infocmp xterm-256color` show none. (Note: this check was ALSO meaningless — `Hls` is a tmux-defined user capability that never lives in system terminfo; it can only come from the built-in table or the `terminal-features` option.) `via: command`
- **Not ruled out (leading hypothesis):** the nixpkgs tmux package carries a patch/build difference enabling hyperlink emission unconditionally; or a 3.7c-behaviour delta misread from the 3.7 tag's `tty.c` (`tty_hyperlink` :2592-2607 gates on `TTYC_HLS`, which `tty_putcode_ss` only emits when the term has the cap — the contradiction is real and unresolved). `via: code`
- **Next probe:** attribute the build: `nix-instantiate --eval -E '(import <nixpkgs> {}).tmux.patches'` (and `nix eval nixpkgs#tmux.version`) — look for an alacritty/hyperlinks patch in the nixpkgs tmux derivation. If the package is unpatched, re-read `tty-term.c`'s `tty_term_string_ss` for absent-cap behaviour on 3.7c.

### Are the operator's real wrapped URLs OSC-8 or plain text?
- as-of: 2026-09-19
- **Symptom + exact repro:** the original complaint — hover+click a URL spanning two lines in a tmux pane: only line 1 underlines, click opens a truncated URL. The shipped fix's click path covers **only URLs emitted as OSC 8**; a plain-text wrapped URL still truncates (Alacritty regex hints cannot span tmux's hard rows — upstream wontfix, alacritty#1705), and the `o` binding answers "no hyperlink under cursor" there.
- **Observed (with values):** emission side is a non-issue on this build (investigation 1) — so the discriminator is entirely on the URL SOURCE side. Daily drivers: `ls --hyperlink`, `rg --hyperlink-format`, gcc 10+ diagnostics emit OSC 8; `cat`/logs/claude output are plain text.
- **Next probe:** operator hover test on a wrapped URL from the REAL workflow (screen action, cannot be delegated). Underline spans both rows ⇒ OSC 8 present ⇒ click opens full. Still first-line-only ⇒ plain text ⇒ the residual stands.
- **Ruled out:** nothing yet — this investigation has not started. `via: assumed`
- **Leading hypothesis:** most of the operator's wrapped URLs are plain text (arbitrary command output), in which case the DIY route is the real closer. `via: assumed`

## Next steps (ranked)
1. **Activate without restart, then the operator's real-URL test.** `tmux source-file ~/.config/tmux/tmux.conf` — measured non-destructive on a throwaway socket against the deployed artifact: server option applies, bindings re-apply, hooks 1→1 (no `-ga` accumulation), sessions/clients untouched. Then verify `tmux show -s terminal-features | grep hyperlinks` and `tmux list-keys | grep -c copy_cursor_hyperlink` (=2), then the operator hovers/clicks a wrapped URL from their real workflow (open investigation 2).
    forcing: user — the click test is a screen action only the operator can do, and they asked "how can we validate".
2. **Attribute the unconditional-emission mechanism** (open investigation 1) — nixpkgs tmux package source first; outcome decides whether the `terminal-features` line's comment needs a correction on this repo (it currently cites tmux#4308's gating as the reason).
    forcing: none
3. **If the real URLs are plain text:** build the DIY opener — `capture-pane -J` (joins wrapped rows) + `capture-pane -H`-style extraction around the cursor, bound in copy mode. Prior art: tmux-plugins/tmux-open#14 is broken on wrapped URLs (xargs splits at the newline) — do not reuse it.
    forcing: none

## Gotchas / decisions / dead-ends
- 🔴 **tmux probe-method pins (all measured this session, each one silently produced a WRONG green/red first):** use `os.execvp`, never `os.execve` with a bare name (ENOENT → the "client" never attaches and the pty capture is buffered-print debris); `flush=True` before `pty.fork` (the forked child inherits and flushes the parent's stdout buffer into the capture); `capture-pane` needs `-p` (without it output goes to a paste buffer and stdout is silently empty); `tmux show -s` must be queried BEFORE `kill-server`; `send-keys -X` outside copy mode exits 0 and does nothing — enter `copy-mode` first; `list-keys -T <table> <key>` returns rc 0 with EMPTY output even for a bound key on 3.7c — filter the full `list-keys` output instead.
- 🔴 **The kill-mention ledger reds CI for any new `kill-server` mention** — `test_guard_core.py::test_every_kill_server_call_site_in_the_repo_is_classified` is a derived two-way ledger; this exact miss cost a CI round. Classify in `_KILL_MENTION_LEDGER` (ARGV rows: say the sites carry `-L`). CI failure arithmetic to read it: `collected - passed - skipped = failed` (one failing name was truncated mid-token in the summary line).
- 🔴 **Shared-checkout switcher struck TWICE this session** — the branch was checked out from under this session mid-run (found at commit time and again during CI polling). Re-verify `git branch --show-current` immediately before every write/commit.
- 🔴 **`tekton/devrc-main-*` statuses on a sha are NOT your PR's CI** — after a switch, `commits/{sha}/statuses` read the OTHER branch's pipeline signal; per-PR, `gh pr checks <n>` is the authority. Cost: several minutes mis-attributing main's CI to this branch.
- Reload spelling: `set -as terminal-features ',*:hyperlinks'` — leading comma is the tmux FAQ's documented append form (its empty pattern matches no terminal); `-s` because terminal-features is a SERVER option (the older clipboard line's `-g` is only tolerated; left alone — OSC 52 workflow's business).
- The wrapped-URL scope verdict (for context): tmux emits every pane row as a HARD line (CRLF between rows in full draws, CUP/CHA at wrap boundaries in incremental redraws — captured byte-level), so Alacritty's regex hints can never span a tmux wrap (matcher follows WRAPLINE, never set by tmux). OSC 8 links ride per-cell — that's why they're the only structurally sound fix.

## How to verify
1. Activation: `tmux source-file ~/.config/tmux/tmux.conf` then
   `tmux show -s terminal-features | grep hyperlinks` and `tmux list-keys | grep -c copy_cursor_hyperlink` → 2.
2. Suite: `nix develop ~/workspace/devrc -c python3 -m pytest scripts/tests/test_tmux_hyperlink_open.py -q` → 6 passed.
3. Deployed artifact: `readlink -f ~/.config/tmux/tmux.conf` → a `/nix/store` path containing both changes (grep `hyperlinks`, `copy_cursor_hyperlink`).
4. End-to-end (operator, screen action): in a tmux pane `ls --hyperlink /` in a narrow pane; hover — underline must span both rows; click the wrapped half — full URL opens; copy mode over the link — `o` opens, over non-link text — "no hyperlink under cursor".
