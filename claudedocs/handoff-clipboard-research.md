---
# No clawgate task — session had no CLAUDE_CODE_SESSION_ID
---
# Handoff: clipboard-research — 2026-08-29

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Research modern best practices for clipboard and terminal clipboard interaction on Linux/NixOS/i3wm. Determine what's current, what's optimal, and what (if anything) needs changing.

## State now
- Branch: `main`, behind `origin/main` by 5, ahead by 1
- **DONE this session:** research report completed — subagent dispatched, findings delivered
- **NOT DONE:** no code changes, no config changes — pure research
- Deploy/verify status: N/A — research only, no changes to apply

## Research findings — clipboard/terminal clipboard best practices (2025-2026)

### tmux clipboard — ALREADY OPTIMAL, no changes
- `set -s set-clipboard on` makes copy-mode emit OSC 52 to the outer terminal
- `set -ga terminal-features '*:clipboard'` advertises capability regardless of TERM
- Works across SSH and nested tmux — no external tools needed
- tmux-yank plugin is superseded (pops to `xsel`/`xclip` locally; OSC 52 is better)

### Alacritty OSC 52 — ALREADY OPTIMAL, no changes
- `osc52 = "OnlyCopy"` is the correct security posture (prevents remote paste injection)
- All modern terminals (Kitty, WezTerm, Ghostty) support OSC 52 fully

### X11 clipboard tools — ALREADY OPTIMAL, no changes
- `xclip` installed and sufficient for X11 scripting
- `xsel` is an alternative with slightly different CLI but no advantage
- If migrating to Wayland: switch to `wl-copy`/`wl-paste` (2.4k stars, gold standard)

### Clipboard managers — NOT NEEDED
- `cliphist` (1.5k stars) is the modern gold standard but Wayland-only
- On X11/i3, tmux OSC 52 + xclip covers the use cases
- `clipcat` (X11) or `CopyQ` available if history is wanted, but optional

### Neovim clipboard — ONE OPTIONAL IMPROVEMENT
- Currently: implicit xclip detection (auto-detected, works out of box)
- `"+y` / `"+p` (system clipboard) work without config
- **Optional improvement:** add `set clipboard=unnamedplus` to `native.vim` to make `y`/`p` use system clipboard by default
- Trade-off: convenience vs. vim muscle memory where `y` only yanks to a register

### Espanso + clipboard — ALREADY WORKING, no changes
- `{{clip}}` variable reads X CLIPBOARD via espanso's own `espanso-clipboard` module
- No conflicts with tmux OSC 52 path
- Gotcha: `{{clip}}` reads whatever is in CLIPBOARD at expansion time — potential race with concurrent writes

## Open investigations — live diagnosis state

None. Research is complete. The findings above are conclusive.

## Next steps (ranked)
1. **Decision:** adopt `set clipboard=unnamedplus` in neovim? (optional, affects `y`/`p` muscle memory)
2. **Decision:** install a clipboard manager for history? (optional — `clipcat` for X11, `cliphist` if/when migrating to Wayland)
3. **If migrating to Wayland:** replace `xclip` with `wl-clipboard`, evaluate `cliphist` + `wl-clip-persist`

## Gotchas / decisions / dead-ends
- OSC 52 supersedes tmux-yank for this setup — no reason to install the plugin
- Wayland clipboard is a different ecosystem — `wl-clipboard` is the equivalent of `xclip`
- Espanso's clipboard access is separate from tmux's OSC 52 but reads the same X CLIPBOARD selection
- No clipboard manager was installed — current tooling is sufficient

## How to verify
- tmux clipboard: copy in tmux copy-mode → paste in another app (OSC 52 path)
- Neovim (if `unnamedplus` adopted): `y` in neovim → paste elsewhere
- Espanso: type `:clip` → clipboard contents expand inline
