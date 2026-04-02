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
    default.nix            # Core packages (coreutils, search, git, dictation)
    lang/default.nix       # Language runtimes + LSPs (Go, Python, Rust, Node, etc.)
    tools/default.nix      # Dev tools (docker-compose, lazygit, k9s, nemo)
    tools/tmux-fuzzyclaw.nix  # Custom Go binary (local build)
  programs/                # Per-program home-manager modules
    neovim/ zsh/ bash/ tmux/ git/ fzf/ direnv/ alacritty/ ranger/
scripts/                   # Tmux activity tracking, task dashboard, dictation
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

## Updating

```sh
# Update nixpkgs and home-manager to latest
nix flake update

# Rebuild
home-manager switch --flake . --impure
```

## Customization

Create `~/.devrc` to add shell customizations without editing the repo. Home-manager sources this file automatically if it exists.

## Speech-to-Text Dictation

Local speech-to-text using [faster-whisper](https://github.com/SYSTRAN/faster-whisper) with i3 integration.

### Setup

1. Run the setup script to create Python venv and install faster-whisper:
   ```sh
   ./scripts/setup-dictation.sh
   ```

2. Add keybinding to `/etc/nixos/i3config.nix`:
   ```
   bindsym $mod+s exec --no-startup-id ~/workspace/devrc/scripts/dictate
   ```

3. Rebuild:
   ```sh
   sudo nixos-rebuild switch
   home-manager switch --flake . --impure
   ```

### Usage

With `Alt+s`:
1. **Press once**: Start recording (notification shows "Recording...")
2. **Auto-stop**: Recording stops automatically after 1s of silence
3. **Optional**: Press `Alt+s` again to force immediate stop

- **First use**: Daemon auto-starts (~5-10s to load model on GPU)
- **Transcription**: ~0.3s on GPU with large-v3-turbo

| Option | Description |
|--------|-------------|
| `--model tiny.en` | Fastest, least accurate (~1GB RAM) |
| `--model small.en` | Fast, good balance (~2GB RAM) |
| `--model large-v3-turbo` | Default, 8x faster than large-v3 (~4GB VRAM) |
| `--model large-v3` | Best accuracy, slower (~10GB VRAM) |
| `--daemon` | Start daemon manually |
| `--stop-daemon` | Stop running daemon |
| `--rebuild-cache` | Rebuild nix library path cache |

### Developer Optimizations

The script includes optimizations for technical dictation:
- **Developer vocabulary prompt**: Guides Whisper toward technical terms (NixOS, TypeScript, React, etc.)
- **Word replacements**: Auto-corrects common misheard terms (e.g., "knicks os" -> "NixOS")
- **Reduced beam_size**: Faster transcription with minimal accuracy loss

Edit `WORD_REPLACEMENTS` in `scripts/dictate.py` to add your own corrections.
