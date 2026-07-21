# =============================================================================
# Host AirVPN WireGuard tunnel — SYSTEM nix block  (PREPARED, NOT YET APPLIED)
# =============================================================================
# This module wires up the HOST-level AirVPN tunnel that the workbench status-bar
# `airvpn` block drives (Connect/Disconnect/switch-server/verify). It REPLACES the
# decommissioned host Mullvad wg-quick block. It is SEPARATE from the qBit-pod
# AirVPN sidecar (the `media` block) — that lives in the cluster, untouched.
#
# 🔴 PHASE 2 — Zach applies this by hand. This file is committed for review only;
#    NOTHING here has been applied to the host. The staged apply script
#    nix/system/apply-airvpn-host.sh AUTOMATES steps 2-4 idempotently (with backups):
#      1. Generate a NEW-device AirVPN WireGuard config (airvpn.org → Config
#         Generator → Linux → WireGuard → NEW device) and drop it at
#         /etc/wireguard/airvpn.conf  ← Zach's secret; never in git / the nix store.
#      2. Then run:  sudo bash nix/system/apply-airvpn-host.sh
#         (locks the conf 0600, appends the PostUp/PreDown hooks below, installs
#          airvpn-sudo + airvpn-updown to /etc/nixos/i3blocks-scripts/, copies this
#          module to /etc/nixos/airvpn-host.nix + adds it to configuration.nix's
#          imports, then `nixos-rebuild switch`.)
#      3. Toggle ON from the bar (left-click the AirVPN pill → Connect) and run the
#         live-verification checklist. DEFAULT-OFF: the rebuild does NOT bring the
#         tunnel up (there is no wg-quick auto-start unit — see below).
#
# DEFAULT-OFF by construction: we do NOT declare `networking.wg-quick.interfaces`
# (which would generate an ENABLED wg-quick-airvpn.service that auto-starts on
# boot). Instead the tunnel is a plain on-demand `wg-quick up airvpn` reading
# /etc/wireguard/airvpn.conf, invoked only by the bar's airvpn-sudo helper. So a
# rebuild / reboot leaves the host on its DIRECT route until Zach connects.
#
# KILLSWITCH + SPLIT-TUNNEL live in the conf's PostUp/PreDown (→ scripts/airvpn-updown),
# armed ONLY while the tunnel is up. Split-tunnel bypass ROUTES: VPN endpoint via the
# original gateway, the LAN router/DNS + LAN 192.168.50.0/24 direct, and the nebula
# lighthouse. The KILLSWITCH is fail-closed but POLICES ONLY THE PHYSICAL UPLINK
# (`oifname != <phys> drop-by-default`): loopback, the tunnel, the nebula overlay
# (nebula.mesh) and the k3s CNI (cni0/flannel.1 → pods/services) egress non-physical
# ifaces and are always allowed — this host runs k3s + is reached over nebula, so an
# enumerate-every-internal-net allow-list was fragile (it dropped the overlay, then
# the cluster). Uplink-only policing is leak-equivalent and needs no rule per new net.
#
# ---- PostUp/PreDown to append to /etc/wireguard/airvpn.conf [Interface] -------
#   PostUp   = /etc/nixos/i3blocks-scripts/airvpn-updown up %i
#   PreDown  = /etc/nixos/i3blocks-scripts/airvpn-updown down %i
# (AirVPN's generated conf already sets PrivateKey/Address/DNS + the [Peer]
#  PublicKey/PresharedKey/Endpoint. The bar's "switch server" rewrites only the
#  [Peer] Endpoint via `wg syncconf` — no rebuild per switch.)
# =============================================================================
{ config, lib, pkgs, ... }:

{
  # wireguard-tools (wg, wg-quick) + nftables (the killswitch) on the system PATH.
  environment.systemPackages = [ pkgs.wireguard-tools pkgs.nftables ];

  # NOPASSWD sudoers rule for the bar's privileged AirVPN helper, at the STABLE
  # sudoers-trusted path (a nix-store path would not match the rule + would change
  # every rebuild). Analogous to the retired Mullvad `vpn-sudo` rule. The helper
  # (scripts/airvpn-sudo) validates every arg strictly before acting as root.
  security.sudo.extraRules = [
    {
      users = [ "zach" ];
      commands = [
        {
          command = "/etc/nixos/i3blocks-scripts/airvpn-sudo";
          options = [ "NOPASSWD" ];
        }
      ];
    }
  ];

  # The tunnel bring-up races the network being online — no auto-start unit exists,
  # but if Zach ever chooses to declare a wg-quick unit, keep it OFF by default:
  #   systemd.services."wg-quick-airvpn".wantedBy = lib.mkForce [ ];
  # (Left commented: with the plain /etc/wireguard/airvpn.conf approach there is no
  #  such unit to gate — documented here so a future `networking.wg-quick` addition
  #  stays default-OFF.)

  # IP forwarding / rp_filter: AirVPN WG on a single host needs no special sysctl
  # beyond stock. The killswitch + bypasses are route/nftables-based (airvpn-updown),
  # not sysctl, so nothing else is required here.
}
