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

  # age — file encryption for the /analyze-service index off-machine backup
  # (scripts/analyze-service-index/backup.py). The store is client-confidential
  # and every scope README says the content never leaves the machine; encrypting
  # to the operator's EXISTING SOPS age identity before upload keeps that
  # literally true, so the homelab MinIO tenant holds ciphertext it cannot read.
  # Provides both `age` and `age-keygen` (the backup derives its recipient from
  # the identity file rather than hardcoding one, so the key it encrypts to and
  # the key it can decrypt with cannot drift apart).
  age

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

  # Hardware/system profiling (CPU-Z equivalents on Linux)
  inxi            # comprehensive system info (CPU/GPU/RAM/disks/network in one shot)
  cpu-x           # CPU-Z GUI clone — cache speeds, clocks, motherboard, BIOS
]
++ (import ./lang { inherit pkgs; })
++ (import ./tools { inherit pkgs workspace; })
