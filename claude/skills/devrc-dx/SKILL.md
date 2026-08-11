---
name: devrc-dx
description: "Audit and fix the devrc desktop/terminal setup — home-manager, NixOS, i3, tmux + fuzzyclaw, zsh/direnv, gruvbox theming — reporting cross-cutting drift then fixing it. Use for: audit or improve my shell/tmux/i3/desktop config, a keybinding or theme inconsistency, slow shell startup, config drift or dead config in devrc. Bar work -> `bar`."
argument-hint: "[all|tmux|i3|shell|theme|nix|tasks] [analyze|fix|verify|audit] — defaults to 'all analyze'"
allowed-tools: Bash, Read, Grep, Glob, Edit, Write, Agent
---

# devrc-dx — developer experience analyzer

Scope + action come from `$ARGUMENTS` (default `all analyze`).

| Scope | Covers |
|---|---|
| `all` | full stack (default) |
| `tmux` | tmux config, plugins, scripts, status bar, task management |
| `i3` | i3 config + i3status-rust bar + dunst (all home-manager), keybindings |
| `shell` | zsh, bash, direnv, session variables, PATH |
| `theme` | gruvbox consistency across all components |
| `nix` | home-manager and NixOS config hygiene |
| `tasks` | Claude Code task management (hooks, dashboard, state) |

| Action | Does |
|---|---|
| `analyze` | analyze and report issues (default) |
| `fix` | analyze then implement all fixes |
| `verify` | run the verification checks against live state |
| `audit` | lightweight check for common issues only |

**Reference files** (`~/.claude/skills/devrc-dx/reference/`, source
`~/workspace/devrc/claude/skills/devrc-dx/reference/`) — read on demand:
- `config-stack.md` — gruvbox palette, keybinding conventions, the 6 scratch slots,
  the fuzzyclaw task-management wiring, file ownership, the tmux idle-fade scale.
- `verification.md` — per-category check snippets (only when the one-shot block FAILs).
- `gotchas.md` — the traps that have actually bitten (popup `-S` vs `-s`, `rename-window`
  freezing tabs, picom vs NVIDIA, sed-based apply scripts, PATH duplication).

## 1. Discover configuration
Read the config sources — do NOT assume locations, discover them.

**home-manager (devrc at `~/workspace/devrc`):**
- `nix/home.nix` — services (espanso, dunst), packages, session variables, sessionPath, file symlinks
- `nix/programs/` — per-program configs (tmux, zsh, git, alacritty, direnv, fzf, ranger, bash, neovim)
- `nix/pkgs/` — language tooling (`lang/`) and system packages (`tools/`)
- `nix/sessionVariables.nix` — FZF, editor, LSP, Playwright env vars
- `nix/system/` — staged NixOS config changes + apply scripts
- `.tmux.conf` / `.zshrc` — extra config pulled in with `builtins.readFile` by
  `nix/programs/tmux/default.nix` and `nix/programs/zsh/default.nix`
- `scripts/` — tmux scripts (task-hook, task-resume, activity-receiver — thin wrappers over
  fuzzyclaw; scratch-picker, scratch-status, scratch-monitor, claude-counters; the `agent-ops`
  dashboard) + the i3status-rust bar scripts (`i3status-*` block scripts, `bar-status-poll`,
  `i3blocks-rigcontrol` / `i3blocks-agent-ops` launchers). (`tmux-initiatives.sh` +
  `dictation` were removed 2026-07.)
- `nix/pkgs/tools/tmux-fuzzyclaw.nix` — buildGoModule package for the fuzzyclaw Go binary

**i3 + status bar — home-manager since 2026-07 (PR #74), NOT `/etc/nixos`:**
- `nix/i3/config.nix` — the i3 config (raw string → `~/.config/i3/config`); keybinds incl. `$mod+i` → agent-ops
- `nix/graphical.nix` — the i3status-rust bar (`programs.i3status-rust`, gruvbox, nerd-font
  icons) + `services.dunst` + the `bar-status-poll` systemd user timer feeding the count blocks
- ⚠ `/etc/nixos/{i3config.nix,i3blocks.nix,i3blocks-scripts/}` are **RETIRED** — never edit the
  bar or i3 in `/etc/nixos`. Bar-specific work belongs to the **`bar`** skill.

**System (NixOS at `/etc/nixos/`):**
- `configuration.nix` — system packages, services, display-manager (lightdm `none+i3`, i3
  *enabled* but configured in home-manager), PipeWire audio, NVIDIA GPU, k3s, networking

For fuzzyclaw-internal development/debugging, use the `/fuzzyclaw` skill in the
`~/workspace/tmux-fuzzyclaw` repo — not this one.

## 2. Analyze each layer
Per scope, check these categories:

**Consistency** — keybinding style (vim hjkl vs arrow vs mixed) across i3/tmux/neovim;
gruvbox palette across tmux status, i3 bar, dunst, alacritty; naming conventions across nix
files; status emoji conventions (🔄 active, ⏸ paused, ✅ done, ● claude running).

**Performance** — per-pane shell commands that should run once (xset, PATH, env vars);
status bar scripts forking per-window instead of batching; dashboard render time
(<50ms for 50 windows); hook execution time (PreToolUse fires on *every* tool call);
pre-caching per-cwd not per-window for JSONL extraction; the two-pass search staying
two-pass (instant substring over cached fields + an async batch ripgrep deep scan —
~66ms across 1.2 GB via a single `rg -i -l --max-count=1 --glob=*.jsonl` over all cwds,
not one invocation per window).

**Correctness** — dead references (signals to a wrong process, unused packages); hardcoded
paths that should use `config.home.homeDirectory`; duplicate config (direnv hooks, bell
handlers, PATH entries); commented-out code; anything in the hook path calling
`tmux rename-window` (auto-rename must stay ON — see `gotchas.md`).

**Completeness** — missing keybindings for installed tools (flameshot, i3lock, rofi);
missing notification daemon or screen lock; missing `focus-events` / `automatic-rename`;
missing `xss-lock` for suspend/idle protection.

**Security** — no screen lock on suspend/idle; unprotected secrets in config files.

## 3. Report findings
Group by impact: **High** (workflow gaps, incorrect behavior, performance problems) ·
**Medium** (inconsistencies, duplication, missing polish) · **Low** (style, nice-to-haves).
Each finding: what's wrong and why it matters, which file(s), and a concrete fix.

## 4. Implement fixes (action `fix`)
- Edit devrc files directly for anything home-manager owns.
- `/etc/nixos/` changes: stage complete files in `nix/system/` with an apply script — no sudo
  is available, the user runs `sudo bash nix/system/apply-*.sh`. Write the **whole replacement
  file** and `cp` it; sed-based multiline edits are fragile.
- 🔴 A NEW file must be `git add`ed or the flake silently omits it from the deploy.
- Commit with a descriptive message; remind the user to run `home-manager switch` (or
  `scripts/ship.sh`) and/or `sudo nixos-rebuild switch`.

## 5. Verify (action `verify`)
One bash call, `PASS`/`FAIL` per line — do not fan out into 20 round-trips:

```bash
ok(){ printf '%-42s %s\n' "$1" "$([ "$2" = 0 ] && echo PASS || echo FAIL)"; }
# tmux
tmux show-option -g focus-events | grep -q on; ok "focus-events on" $?
tmux list-keys | grep -q fuzzyclaw; ok "Alt+F fuzzyclaw bound" $?
tmux list-keys -T root | grep -Eq "M-[gGvVpP]"; ok "6 scratch slot bindings" $?
# fuzzyclaw + hooks
command -v fuzzyclaw >/dev/null; ok "fuzzyclaw in PATH" $?
jq -e '.hooks' ~/.claude/settings.json >/dev/null 2>&1; ok "claude hooks configured" $?
# desktop
pgrep -x dunst >/dev/null; ok "dunst running" $?
! pgrep -x picom >/dev/null; ok "picom NOT running (NVIDIA)" $?
command -v rofi >/dev/null; ok "rofi installed" $?
# shell
grep -q CDPATH ~/.config/zsh/.zshrc 2>/dev/null; ok "CDPATH set" $?
# config deploy
grep -q "duration = 0" ~/.config/alacritty/alacritty.toml 2>/dev/null; ok "alacritty bell off" $?
```

Report the table. For any FAIL, drop into `reference/verification.md` for that category.

## Boundaries
**Will:** analyze all layers holistically · find cross-cutting issues (theme drift, binding
conflicts, duplication) · fix in devrc and stage system changes with apply scripts · verify
against live state · preserve existing workflows and keybinding muscle memory.

**Will not:** run `sudo` directly (stage files instead) · remove keybindings without an
alternative · change modifier keys (Alt for i3, Ctrl-A for tmux) without an explicit request ·
assume hardware (read the actual config for GPU, audio, display) · use `sed` for complex
multiline edits in apply scripts.
