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

  # mosh — ssh over a link that BLACKS OUT rather than one that is merely slow.
  # Measured 2026-09-23 from off-LAN: the path to home dropped every ~1–2.5 min
  # for ~6.5 s at a time, which ssh experiences as a frozen terminal and a
  # banner-exchange timeout. mosh's UDP datagram protocol carries session state
  # across the gap, so the same outage is invisible. It is a MITIGATION, not a
  # fix — the blackout is upstream of both hosts (see
  # claudedocs/handoff-laptop-airvpn-tunnel.md).
  # 🔴 Installing this is NOT sufficient by itself: mosh-server binds a UDP port
  # in 60000-61000 on the machine you connect TO, and neither host's
  # networking.firewall opens that range. That half is a /etc/nixos change and
  # is staged as nix/system/apply-mosh-nebula-firewall.sh — both halves, or mosh
  # hangs at "Connecting..." with no diagnostic.
  mosh

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
