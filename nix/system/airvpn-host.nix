# =============================================================================
# Host AirVPN WireGuard tunnel — SYSTEM nix block  (PREPARED, NOT YET APPLIED)
# =============================================================================
# This module wires up the HOST-level AirVPN tunnel that the workbench status-bar
# `airvpn` block drives (Connect/Disconnect/switch-server/verify). It REPLACES the
# decommissioned host Mullvad wg-quick block. It is SEPARATE from the qBit-pod
# AirVPN sidecar (the `media` block) — that lives in the cluster, untouched.
#
# 🔴 PHASE 2 — Zach applies this by hand. This file is committed for review only;
#    NOTHING here has been applied to the host. To activate (see the repo PR's
#    "Phase-2 handoff" for the full ordered steps):
#      1. Generate a NEW-device AirVPN WireGuard config (airvpn.org → Config
#         Generator → Linux → WireGuard → NEW device) and drop it at
#         /etc/wireguard/airvpn.conf  (root:root, chmod 0600).  ← Zach's secret;
#         never in git / the nix store.
#      2. Append the PostUp/PreDown lines (below) to that conf's [Interface].
#      3. Copy scripts/airvpn-sudo   → /etc/nixos/i3blocks-scripts/airvpn-sudo   (0755 root)
#         Copy scripts/airvpn-updown → /etc/nixos/i3blocks-scripts/airvpn-updown (0755 root)
#      4. `import` this file from /etc/nixos/configuration.nix  (or paste its
#         attrs in), then:  sudo nixos-rebuild switch
#      5. Toggle ON from the bar (left-click the AirVPN pill → Connect) and run the
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
# so they are armed ONLY while the tunnel is up and replicate the old Mullvad
# bypasses exactly: VPN endpoint via the original gateway, 192.168.50.1 (LAN
# router/DNS), LAN 192.168.50.0/24 direct, and the nebula Hetzner lighthouse
# 5.161.118.55 (mail/mesh survives). Fail-closed: while up, non-tunnel egress that
# isn't an explicit bypass is dropped.
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
