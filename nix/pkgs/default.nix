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
  # fix — nebula and tailscale black out in the SAME second for the same
  # duration while a far endpoint of ours that is not at home stays 0/140 over
  # the same wifi, router, ISP and distance, so the fault is upstream of both
  # hosts. The write-up is claudedocs/handoff-laptop-airvpn-tunnel.md, and
  # `main`'s copy now AGREES with the paragraph above: PR #1866 MERGED as
  # b7a30bc3, so that doc carries the two-mechanism correction and its old
  # single-cause line reads "SUPERSEDED, kept for the record". Verified against
  # origin/main 2026-09-24 — read it for the long version. ⚠ The measurement
  # stays restated here rather than cited: a citation asserting a PR's STATE
  # expires (this comment carried two wrong ones), a merge commit does not.
  #
  # 🔴 THIS ENTRY IS THE CLIENT, AND IT IS DELIBERATELY USER-LEVEL. home-manager
  # installs it on both hosts with no sudo, which is what the laptop needs to
  # *initiate* a session. The SERVER side is a system-level change — the
  # `programs.mosh` NixOS module (for the utempter setgid wrapper, without which
  # mosh cannot write utmp and `who` misses the session, and for mosh-server on
  # the system PATH) plus a UDP range on the mesh interface, because
  # mosh-server binds one ephemeral port in 60000-61000 on the machine you
  # connect TO and neither host's networking.firewall opens that range. That
  # half is staged as nix/system/apply-mosh-nebula-firewall.sh — both halves, or
  # mosh hangs at "Connecting..." with no diagnostic.
  #
  # 🔴 The module's `openFirewall` defaults to TRUE and the staged script turns
  # it OFF: it would open 1001 UDP ports on EVERY interface, WAN included. The
  # interface-scoped range is the narrow replacement — but "scoped to the mesh"
  # is not "reachable only from the admin laptop": inside the mesh the gate is
  # nebula's own firewall.inbound, which allows any/any from every listed GROUP
  # (measured 2026-09-23 on the laptop: three — lighthouse, admin, workbench —
  # plus icmp from any). Read the target host's own inbound rules before
  # assuming the exposure is smaller.
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
