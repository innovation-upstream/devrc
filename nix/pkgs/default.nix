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
]
++ (import ./lang { inherit pkgs; })
++ (import ./tools { inherit pkgs workspace; })
