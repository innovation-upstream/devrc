# DEVRC

This repo assumes you are running Ubuntu 20 LTS or NixOS, but it may work on 
other versions/distros if you are lucky.

## Installation

Follow these steps to initialize a fresh environment capable of building and 
running any Innovation Upstream repo.

1. `mkdir $HOME/workspace && cd $HOME/workspace` (Optional, see step 4)
2. Clone this repo
3. Run `cmd/install.sh`
4. Run `home-manager switch` build the env

(Optional) If you cloned devrc into a different directory, you will need to set the 
`DEVRC_DIR` environment variable in `~/.devrc`.

```sh
$ export DEVRC_DIR="$HOME/workspace-2/devrc"
$ source $DEVRC_DIR/.zshrc
```

## Customization

If you need to add/modify your shell profile, you can do so by
creating/modifying `~/.devrc`. (home-manager will tell zsh to source this file
if it exists)

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
   home-manager switch
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
- **Word replacements**: Auto-corrects common misheard terms (e.g., "knicks os" → "NixOS")
- **Reduced beam_size**: Faster transcription with minimal accuracy loss

Edit `WORD_REPLACEMENTS` in `scripts/dictate.py` to add your own corrections.
