# devrc — NixOS / home-manager dotfiles

Personal dev-environment config (zsh, tmux, neovim, i3, scripts) for the workbench + laptop NixOS hosts. Managed by **home-manager via a flake**.

## Shell environment (read before running commands)
- **Bash tool runs NON-interactive zsh** (`zsh -c`) → sources `.zshenv` only, NOT `.zshrc`/`initContent`. Shell tweaks Claude needs at runtime must go in home-manager `programs.zsh.envExtra` (→ `.zshenv`). `unsetopt nomatch` lives there so unmatched globs pass through literally instead of aborting with "no matches found".
- zsh reserves `status` — use `rc=`/`out=`, never `status=$(...)`.
- Use `git -C <path>` and absolute paths — never `cd <repo> && …` (triggers approval prompts and can run untrusted hooks).

## Applying changes
- **Apply config:** `home-manager switch --flake ~/workspace/devrc --impure` (allowlisted). This is how you validate a Nix edit end-to-end.
- **Quick syntax check** before switching: `nix-instantiate --parse <file>.nix >/dev/null`.
- **NEVER `sudo nixos-rebuild` from Claude** — can't sudo non-interactively. System-level changes must be staged as an apply script for the user to run (see the `laptop` skill's `stage-system` pattern). home-manager (user-level) is fine.

## Server / headless mode
- `~/.server-mode` marker toggles graphical bits: `headless-mode` (disables dunst/espanso) vs `graphical-mode` (re-enables), both run a home-manager switch. A host may be in server mode — check for the marker before assuming a GUI.

## Layout
- `nix/` — home-manager modules (`programs/zsh`, tmux, nvim, i3, …). `flake.nix` at root.
- `scripts/` — utility scripts (prefer extending these over re-typing inline bash / heredocs).
- `.zshrc`, `.tmux.conf` etc. are read by the nix modules — read with offset/limit, they're large.

## Conventions
- Never `git add -A` — stage files individually.
- Commit + push so both hosts (workbench + laptop) can `git pull` then `home-manager switch`.
