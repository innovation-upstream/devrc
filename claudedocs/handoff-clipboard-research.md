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

## State now — updated 2026-08-29 on resume
- **Research session (00:37–00:53):** report completed, no code changes. This doc
  landed on `handoff/clipboard-research` → PR #1014 (it could not be pushed to
  `main`: protected branch, 2 required checks).
- **Resume session:** both open decisions resolved, and a bug the research missed
  was found, fixed and gated.
  - `unnamedplus` — **declined**, no change.
  - `"+y` dead with no `DISPLAY` — **fixed** on `fix/nvim-osc52-clipboard`
    (`.config/nvim/lua/config/native.lua` + `scripts/tests/test_nvim_clipboard_osc52.py`,
    5 tests, no surviving mutants).
- 🔴 **Deploy: this file needs NO `home-manager switch`.** `~/.config/nvim/init.lua`
  is a store copy, but the `init.vim` baked into it sources everything through
  `$DEVRC_DIR` (set in `nix/graphical.nix:630`) — i.e. straight out of the
  `~/workspace/devrc` **working tree**. So the nvim change goes live on `git pull`
  alone. Measured, not inferred: the red/green test drove it by pointing
  `DEVRC_DIR` at a worktree. `readlink -f` cannot answer this one — the path is
  reached through an env var, not a symlink.

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

### Neovim clipboard — ONE REAL BUG (found on resume), ONE DECLINED IMPROVEMENT

🔴 **The original claim on this line was wrong, and the correction is the whole
value of this section.** It read "`"+y` / `"+p` work without config", full stop.
That is true **only while `DISPLAY` is set**. Measured 2026-08-29 on nvim 0.12.5
at two independent points — `--headless` with the real config, and a pty with
`-u NONE` — with no `DISPLAY` (any ssh session, any bare TTY):

```
clipboard: No provider. Try ":checkhealth" or ":h clipboard".
```

Neovim ≥0.10 ships an OSC 52 provider but does **not** auto-enable it here, so
`"+y` was dead off-display and took `:Absc` (`lua/config/native.lua`, which does
`setreg('+')`) down with it. The research missed it because every probe ran on
the local X11 session — **one measurement point, and the failure lives at the
other one.**

- **FIXED** — `.config/nvim/lua/config/native.lua` now installs an OSC 52
  provider when `DISPLAY` is absent. Red→green on the exact failing path.
- 🔴 **Paste is served from a local cache, deliberately.**
  `vim.ui.clipboard.osc52.paste` queries the terminal and waits 1s + 9s before
  giving up, and alacritty is configured `osc52 = "OnlyCopy"` so it never
  answers. Wiring it straight through — **which is what `:h clipboard`'s own
  example does** — hangs 10s on every `"+p`. The mutant that does exactly that
  took the suite from 2.7s to 12.7s.
- Guarded on `DISPLAY` being absent: with a display, neovim's xclip
  autodetection already works *and* supports a real paste, so it is left alone.

**`set clipboard=unnamedplus` — DECLINED 2026-08-29.** The doc framed the cost as
"vim muscle memory", which understates it: `unnamedplus` routes **every** delete
and change (`d`, `c`, `x`, `s`) through the `+` register, so `dd` clobbers the
system clipboard and the copy-from-browser → edit → paste workflow breaks. Zach
works via agents rather than long interactive nvim sessions, so the payoff is
small and the cost is not. `"+y`/`"+p` stay explicit.

### Espanso + clipboard — ALREADY WORKING, no changes
- `{{clip}}` variable reads X CLIPBOARD via espanso's own `espanso-clipboard` module
- No conflicts with tmux OSC 52 path
- Gotcha: `{{clip}}` reads whatever is in CLIPBOARD at expansion time — potential race with concurrent writes

## Open investigations — live diagnosis state

None open. ⚠ **But note what this section said before, and why it was wrong:**
*"None. Research is complete. The findings above are conclusive."* It was written
after a survey that measured only the local X11 session, and it read as a clean
bill of health for a setup with a dead clipboard register over ssh. **A research
doc that names no open question is making a claim, not reporting an absence** —
the honest version names the dimension it did not vary. Here that dimension was
`DISPLAY`.

## Next steps (ranked)
1. ~~**Decision:** adopt `set clipboard=unnamedplus`~~ — **DECLINED 2026-08-29**,
   reasoning in the neovim section above. No further action.
2. ~~**Decision:** install a clipboard manager for history?~~ — **investigated
   2026-08-29, nothing installed.** Details below; this is a decision on
   evidence, not the original doc's unexamined "NOT NEEDED".
3. **If migrating to Wayland:** replace `xclip` with `wl-clipboard`, evaluate
   `cliphist` + `wl-clip-persist`. **Not applicable today** — measured
   `XDG_SESSION_TYPE=x11`, `WAYLAND_DISPLAY` unset, `wl-copy` absent.

### Clipboard managers — investigated on resume, still NOT installed

The original section concluded "NOT NEEDED — tmux OSC 52 + xclip covers the use
cases". That reasoning was about **transport** and did not address
**persistence**, which is a real and measured gap:

```
set CLIPBOARD from a process   ->  [OWNERSHIP-PROBE]
kill the owning process        ->  Error: target STRING not available
```

X11 selections are owned by the source client, and no manager is running to
hold them (verified: none of clipcat/copyq/greenclip/clipmenu/diodon/parcellite
is installed or running). Close the app you copied from and the clipboard is
empty. So the original conclusion was right by luck, not by argument.

**Nothing installed anyway, and the reason is NOT security.** An earlier draft
of this section argued against `greenclip` because its only exclusion mechanism
is `blacklisted_applications` (per-application, verified against the real
binary's generated config), so a vault paste out of Brave cannot be excluded
without excluding Brave itself, leaving up to 50 plaintext secrets in
`~/.cache/greenclip.history`. 🔴 **That argument is RETRACTED as overweighted**:
anyone who can read that file can already read `~/.ssh/id_*`, the age
identities and the `$KC_*` kubeconfigs in the same home directory, and
`~/.cache` is conventionally excluded from backups. The marginal exposure is
small. The per-application fact is true; the conclusion drawn from it was not.

The actual reason is **no measured need**. The probe above demonstrates the
MECHANISM; nothing establishes the FREQUENCY, and no instance of actually
losing a clipboard was observed. Against that: Zach works entirely via agents
and the standing direction is to modernize the agent-facing layer rather than
interactive-CLI ricing — a history picker on `$mod+Shift+v` is the latter — and
`MEMORY.md`'s "do we need it before hardening" entry exists because a 145 KB
webhook listener that had never run once was shipped and later retired.

Contrast with the neovim fix above, which shipped precisely because it was
**not** speculative: `"+y` was reproducibly dead on every ssh session.

**If it is ever wanted, the pick is `greenclip`** (4.3.1 in the pinned nixpkgs,
attribute `haskellPackages.greenclip` — NOT top-level), because it reuses the
rofi already bound to `$mod+d` and solves persistence AND history for the same
machinery, which strictly dominates a persistence-only daemon. `$mod+Shift+v`
is free. The trigger to revisit is an OBSERVED lost clipboard, not this note.

## Gotchas / decisions / dead-ends
- OSC 52 supersedes tmux-yank for this setup — no reason to install the plugin
- Wayland clipboard is a different ecosystem — `wl-clipboard` is the equivalent of `xclip`
- Espanso's clipboard access is separate from tmux's OSC 52 but reads the same X CLIPBOARD selection
- No clipboard manager installed — see the section above for the evidence, and
  note the retracted security argument so it is not re-derived
- 🔴 `greenclip` has NO top-level nixpkgs attribute; `nix ... nixpkgs#greenclip`
  fails. It is `haskellPackages.greenclip`. A version check that falls back to
  that attribute will report 4.3.1 and read as if the top-level one existed

## How to verify
- tmux clipboard: copy in tmux copy-mode → paste in another app (OSC 52 path)
- 🔴 **Neovim off-display — the one that was broken.** From the *other* host:
  `ssh <host>` → `nvim <file>` → `"+yy` → paste locally. Before the fix this
  printed `clipboard: No provider`; it should now paste. **Verify from a real
  ssh session, not by unsetting `DISPLAY` locally** — same observable, and only
  the real path exercises the terminal that has to honour the escape.
- Neovim on the local X11 session must be UNCHANGED: `:lua print(vim.g.clipboard)`
  → `nil` (xclip autodetection, untouched by the fix).
- Espanso: type `:clip` → clipboard contents expand inline
