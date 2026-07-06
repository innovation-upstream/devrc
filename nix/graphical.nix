# Graphical (X11 / i3) home-manager config: the i3 window-manager config and the
# i3status-rust status bar. Split out of home.nix to keep that file focused on the
# headless-safe bits. Guarded on isNixOS (these only make sense on the NixOS hosts);
# NOT gated on serverMode — the workbench runs a graphical desktop even though
# ~/.server-mode is present (serverMode there only silences dunst/espanso), so
# gating the bar on serverMode would wrongly disable it.
#
# isLaptop (host discriminator, threaded in from home.nix via _module.args):
#   laptop    -> battery block + backlight brightness bindings, no rig block
#   workbench -> rig-control (⚙) block + yad float rule, no battery
#
# The i3 config is written verbatim via xdg.configFile."i3/config".text (raw string
# from ./i3/config.nix) rather than the HM i3 DSL — Zach hand-maintains it.
# NOTE: writing ~/.config/i3/config is INERT until the system stops forcing
# `i3 -c /etc/i3.conf` (that cutover is a separate sudo nixos-rebuild step,
# staged in nix/system/apply-i3-to-hm.sh).
{ config, pkgs, lib, isNixOS ? false, isLaptop ? false, ... }:

let
  home = config.home.homeDirectory;
  scriptsDir = "${home}/.config/i3status-rust/scripts";

  # Built-in blocks (order = left → right on the bar).
  memoryBlock = {
    block = "memory";
    format = " $icon $mem_used_percents ";
    warning_mem = 80;
    critical_mem = 92;
    interval = 10;
  };
  diskBlock = {
    block = "disk_space";
    path = "/";
    format = " $icon $available ";
    info_type = "available";
    interval = 60;
  };
  # net: NO `device` set on purpose. i3status-rust auto-follows the default-route
  # interface, so the one config is correct on BOTH hosts (workbench is wired on
  # eth1 — wlp15s0 is down; laptop is wireless). Pinning a device name reproduced
  # the exact pre-migration bug where wifi was pinned to the laptop's wlp170s0 and
  # rendered nothing on the workbench.
  netBlock = {
    block = "net";
    format = " $icon ↓$speed_down ↑$speed_up ";
    interval = 5;
  };
  cpuBlock = {
    block = "cpu";
    format = " $icon $utilization ";
    interval = 2;
    # info_cpu defaults to 30 → the block goes blue (Info) at any moderate load.
    # Pin it to warning so CPU stays neutral until it actually needs attention:
    # neutral <85, warning 85-95, critical >95.
    info_cpu = 85;
    warning_cpu = 85;
    critical_cpu = 95;
  };
  # temperature: per-host chip. Workbench is AMD (k10temp; Tctl = CPU package temp).
  # Laptop is Intel (coretemp). Validated on workbench via `sensors -u k10temp-*`.
  temperatureBlock = {
    block = "temperature";
    format = " $icon $average ";
    interval = 10;
    # Thresholds are UPPER bounds (temp ≤ idle → Idle/neutral, ≤ info → Info, …).
    # AMD Tctl idles ~55-65°C, so idle must sit above that or the block reads Info
    # (blue) at rest. Neutral ≤78, blue 78-88, yellow 88-95, red >95 (throttle zone).
    good = 20;
    idle = 78;
    info = 88;
    warning = 95;
  } // (if isLaptop then {
    chip = "coretemp-*";
  } else {
    chip = "k10temp-*";
    inputs = [ "Tctl" ];
  });
  batteryBlock = {
    block = "battery";
    format = " $icon $percentage ";
    interval = 10;
  };
  # Volume indicator (default clicks: right = mute, scroll = up/down).
  soundBlock = {
    block = "sound";
    driver = "auto";
    format = " $icon $volume ";
  };
  vpnBlock = {
    block = "custom";
    command = "${scriptsDir}/i3status-vpn-status";
    json = true;
    interval = 30;
    signal = 10;
    click = [
      { button = "left"; cmd = "${scriptsDir}/i3status-vpn-menu"; }
      { button = "right"; cmd = "alacritty --class float,float -e ${scriptsDir}/vpn-detail"; }
    ];
  };
  timeBlock = {
    block = "time";
    interval = 10;
    format = " $icon $timestamp.datetime(f:'%a, %b %d | %H:%M') ";
    click = [
      { button = "left"; cmd = "yad --calendar --width=200 --height=200 --undecorated --fixed --close-on-unfocus --no-buttons"; }
    ];
  };
  # rigcontrol: workbench only. Reuses scripts/i3blocks-rigcontrol for the ⚙ render
  # (plain-text stdout, json defaults false). The click opens the yad panel directly
  # — i3status-rust custom blocks do NOT set $BLOCK_BUTTON, so the click must live
  # here, not inside the render script.
  rigcontrolBlock = {
    block = "custom";
    command = "${scriptsDir}/i3blocks-rigcontrol";
    interval = "once";
    click = [
      { button = "left"; cmd = "setsid -f ${home}/workspace/devrc/scripts/rig-control.sh gui"; }
    ];
  };

  blocks =
    [ memoryBlock diskBlock netBlock cpuBlock temperatureBlock ]
    ++ lib.optional isLaptop batteryBlock
    ++ [ soundBlock vpnBlock timeBlock ]
    ++ lib.optional (!isLaptop) rigcontrolBlock;
in
lib.mkIf isNixOS {
  programs.i3status-rust = {
    enable = true;
    bars.top = {
      theme = "gruvbox-dark";
      # JetBrainsMono Nerd Font (declared below) provides the glyphs, so use the
      # Material-Design nerd-font icon set + the theme's default powerline separators.
      icons = "material-nf";
      inherit blocks;
    };
  };

  # Nerd font for the bar glyphs (block icons + powerline separators). fontconfig
  # makes the home.packages font discoverable by pango / i3bar.
  home.packages = [ pkgs.nerd-fonts.jetbrains-mono ];
  fonts.fontconfig.enable = true;

  # i3 config — raw string. INERT until the system cutover stops forcing /etc/i3.conf.
  xdg.configFile."i3/config".text = import ./i3/config.nix { inherit isLaptop; };

  # Custom-block scripts, symlinked beside the generated TOML. vpn-detail is landed
  # under the name the menu script expects (${SCRIPT_BASE}/vpn-detail). vpn-sudo is
  # deliberately NOT symlinked here — it must stay at the stable, sudoers-trusted
  # /etc/nixos/i3blocks-scripts/vpn-sudo path (a nix-store path would break NOPASSWD).
  home.file.".config/i3status-rust/scripts/i3status-vpn-status" = {
    source = ../scripts/i3status-vpn-status;
    executable = true;
  };
  home.file.".config/i3status-rust/scripts/i3status-vpn-menu" = {
    source = ../scripts/i3status-vpn-menu;
    executable = true;
  };
  home.file.".config/i3status-rust/scripts/vpn-detail" = {
    source = ../scripts/i3blocks-vpn-detail;
    executable = true;
  };
  home.file.".config/i3status-rust/scripts/i3blocks-rigcontrol" = {
    source = ../scripts/i3blocks-rigcontrol;
    executable = true;
  };
}
