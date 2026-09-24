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
  # Measured 2026-09-23 from off-LAN: the path to home dropped for ~6.5-20 s at a
  # time, which ssh experiences as a frozen terminal and a banner-exchange
  # timeout. mosh's UDP datagram protocol carries session state across the gap.
  # ⚠ `via: inference`, NOT measurement — mosh was never installed while the
  # fault was live, so nothing here demonstrates it rides that specific outage.
  #
  # 🔴 THIS IS THE CLIENT ONLY, AND THAT IS THE WHOLE CHANGE ON PURPOSE.
  # home-manager installs it on both hosts with no sudo, which is what a laptop
  # needs to *initiate* a session. The SERVER side is NOT shipped here: a
  # devrc#1865 branch carried a ~600-line staged /etc/nixos editor for it, and
  # that was CLOSED UNMERGED once the diagnosis changed — the fault turned out
  # to be an episodic, location-specific path problem (an external host reached
  # the same machine at 0/300 while this laptop lost 45%), so the script was
  # hardening a sudo rewrite of a remote host's config to mitigate something
  # that had already stopped. What it automated is two lines, hand-edited once,
  # at the console of the host you want to mosh INTO:
  #
  #     programs.mosh = { enable = true; openFirewall = false; };
  #     networking.firewall.interfaces."nebula.mesh".allowedUDPPortRanges =
  #       [ { from = 60000; to = 61000; } ];
  #
  # 🔴 `openFirewall` DEFAULTS TO TRUE and must be turned off: it opens 1001 UDP
  # ports on EVERY interface, WAN included. The interface-scoped range is the
  # narrow replacement — but "scoped to the mesh" is not "reachable only from
  # the admin laptop": inside the mesh the gate is nebula's own
  # firewall.inbound, which allows any/any from every listed GROUP (measured
  # 2026-09-23 on the laptop: three — lighthouse, admin, workbench — plus icmp
  # from any). Read the target host's own inbound rules before assuming the
  # exposure is smaller. Without the server half, `mosh <host>` hangs at
  # "Connecting..." with no diagnostic naming the firewall.
  #
  # The long version, including why the staged script was dropped, is the
  # `devrc/nix` entry in the subsystem index and
  # claudedocs/handoff-laptop-airvpn-tunnel.md.
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
