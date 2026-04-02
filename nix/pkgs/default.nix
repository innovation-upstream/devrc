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
  gotop
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

  # Dictation (speech-to-text)
  sox
  xdotool
  libnotify
  pulseaudio
  zlib
  ffmpeg

  # Browser automation
  playwright-driver.browsers
]
++ (import ./lang { inherit pkgs; })
++ (import ./tools { inherit pkgs workspace; })
