# DEVRC

Personal development environment configuration using NixOS, home-manager, and flakes.

## Architecture

```
flake.nix                  # Pinned nixpkgs + home-manager inputs
flake.lock                 # Auto-generated dependency lock
nix/
  home.nix                 # Home-manager entry point (services, file symlinks)
  sessionVariables.nix     # Environment variables (EDITOR, FZF, LSP paths)
  pkgs/
    default.nix            # Core packages (coreutils, search, git)
    lang/default.nix       # Language runtimes + LSPs (Go, Python, Rust, Node, etc.)
    tools/default.nix      # Dev tools (docker-compose, lazygit, k9s, nemo)
    tools/tmux-fuzzyclaw.nix  # Custom Go binary (local build)
  programs/                # Per-program home-manager modules
    neovim/ zsh/ bash/ tmux/ git/ fzf/ direnv/ alacritty/ ranger/
scripts/                   # Tmux activity tracking, task dashboard, bar/status tooling
cmd/install.sh             # Activation script
```

## Installation

1. `mkdir -p ~/workspace && cd ~/workspace`
2. Clone this repo
3. First-time setup (no home-manager in PATH yet):
   ```sh
   nix run github:nix-community/home-manager -- switch --flake ./devrc --impure
   ```
4. Subsequent rebuilds:
   ```sh
   cd devrc && ./cmd/install.sh
   # or directly:
   home-manager switch --flake . --impure
   ```

The `--impure` flag is required because the config references local paths outside the flake (tmux-fuzzyclaw source, optional `~/.devrc`).

If you cloned devrc somewhere other than `~/workspace`, set `DEVRC_DIR`:
```sh
export DEVRC_DIR="$HOME/other-path/devrc"
```

For the secrets/env files a host needs (paths, key names, where to obtain each)
and the new-host bootstrap order, see [`SECRETS.md`](./SECRETS.md).

## Updating

```sh
# Update nixpkgs and home-manager to latest
nix flake update

# Rebuild
home-manager switch --flake . --impure
```

## Customization

Create `~/.devrc` to add shell customizations without editing the repo. Home-manager sources this file automatically if it exists.
