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

  # mosh — keeps a long-lived SSH-style session ALIVE across a link that goes
  # dark, once ssh has managed to connect. 🔴 It does NOT help you *initiate* a
  # session over a link that is failing during connect: mosh bootstraps by
  # running `ssh <host> mosh-server new`, parses the key and port out of THAT
  # ssh session, and only then switches to UDP — so the TCP/SSH handshake is
  # unchanged. An earlier wording of this comment claimed the opposite.
  # ⚠ `via: inference` — mosh was never installed while a blackout was live, so
  # nothing here demonstrates it against that fault.
  #
  # This entry is the CLIENT ONLY. The server side is a system-level change,
  # hand-edited once at the console of the host you want to mosh INTO:
  #
  #     programs.mosh = { enable = true; openFirewall = false; };
  #     networking.firewall.interfaces."nebula.mesh".allowedUDPPortRanges =
  #       [ { from = 60000; to = 61000; } ];
  #
  # 🔴 `openFirewall` DEFAULTS TO TRUE and must be turned off — it opens 1001
  # UDP ports (60000-61000) on EVERY interface, WAN included. The
  # interface-scoped range is the narrow replacement, but "scoped to the mesh"
  # is not "reachable only from this laptop": inside the mesh the gate is
  # nebula's own `firewall.inbound`, whose group list is PER-HOST and differs
  # between hosts. Read the TARGET host's own inbound rules; do not carry a
  # count from somewhere else.
  # Without the server half, `mosh <host>` hangs at "Connecting..." with no
  # diagnostic naming the firewall.
  #
  # Why there is no script for that edit, and the two audit lessons behind this
  # entry: the `devrc/nix` subsystem-index entry (`cairn recall --repo .`).
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
