{ pkgs, workspace }:

with pkgs; [
  # Core utilities
  coreutils
  gnused
  zsh
  oh-my-zsh
  bash
  tmux
  vim
  fzf
  btop          # vitals-block left-click (float terminal) — memory/cpu/temp/gpu
  ncdu          # disk-block right-click — ncdu on the fullest mount
  wget
  gcc
  bat

  # Search
  gnugrep
  ripgrep
  fd

  # Git
  git
  lefthook

  # Nix
  nix-direnv

  # VCS
  tig

  # Desktop utilities (X automation, notifications, audio control)
  xdotool
  libnotify
  pulseaudio     # pactl — volume keybinds

  # Browser automation
  playwright-driver.browsers

  # Media download router — the yt-dlp path for HLS/DASH sources, which a
  # plain download listener cannot save (there is no single file to save).
  # Invoked by scripts/dl-router/fetcher.py as an argv list, never a shell
  # string. See the `dl-router` skill.
  yt-dlp
]
++ (import ./lang { inherit pkgs; })
++ (import ./tools { inherit pkgs workspace; })
