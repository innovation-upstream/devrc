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

  # Python env for the decoupled bar-status poller (workbench systemd user timer):
  # psycopg2 for the homelab Postgres open-mail_actions count; clawgate + Alertmanager
  # go over stdlib urllib, so psycopg2 is the only non-stdlib dep.
  pollPyEnv = pkgs.python312.withPackages (ps: [ ps.psycopg2 ]);

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
  # nvidia_gpu: workbench only (RTX 5080). The block's state is TEMPERATURE-driven
  # (idle/good/info/warning are UPPER bounds; temp ≤ idle → neutral). Keep it CALM
  # like temperatureBlock: the 5080 idles ~45°C and sits ~65-78°C under sustained
  # load, so collapse idle/good/info onto ONE neutral ceiling (82) and only colour
  # yellow 82-88 / red >88 (edge temp; the throttle zone is higher still). Utilization
  # + power still render in the text at every load — only the COLOUR waits for heat.
  # Needs nvidia-smi on PATH (it is, in the graphical session).
  gpuBlock = {
    block = "nvidia_gpu";
    gpu_id = 0;
    format = " $icon $utilization $temperature $power ";
    interval = 5;
    idle = 82;
    good = 82;
    info = 82;
    warning = 88;
  };
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
  # Decoupled status-count blocks (workbench only). These NEVER query a remote
  # system per bar tick — they read a small JSON cache file written every ~45s by
  # the bar-status-poll systemd user timer (see below) and render it instantly, so
  # a slow/down source can never hang the bar. CALM: each is empty+invisible at
  # zero / stale / error, and only appears (icon + count, coloured) when >0. The
  # `signal` matches SIGNALS in bar-status-poll so the poller can `pkill -RTMIN+N
  # i3status-rs` to refresh exactly this block the instant it writes.
  alertsBlock = {
    block = "custom";
    # --red-above: neutral at/below the standing homelab backlog (~23), red only
    # when the firing count climbs ABOVE it (something new). Tune as the baseline drifts.
    command = "${scriptsDir}/i3status-alerts --red-above 30";
    json = true;
    interval = 30;
    signal = 13;
    click = [
      { button = "left"; cmd = "xdg-open http://grafana.homelab.lan"; }
    ];
  };
  # civitai DataPacket prod alerts — a SEPARATE block from the homelab alertsBlock
  # (Zach's request). Renders `civ <count>` so it reads distinctly on the bar; the
  # poller reaches the client cluster's Alertmanager through CIVITAI_KUBECONFIG.
  # Click opens the civitai Grafana.
  civitaiBlock = {
    block = "custom";
    # --red-above: neutral at/below the standing civitai-prod backlog (~312), red
    # only above it. Big client cluster, so the baseline is high; tune as it drifts.
    command = "${scriptsDir}/i3status-civitai --red-above 340";
    json = true;
    interval = 30;
    signal = 14;
    click = [
      { button = "left"; cmd = "xdg-open https://grafana-new.civitai.com"; }
    ];
  };
  mailBlock = {
    block = "custom";
    command = "${scriptsDir}/i3status-mail";
    json = true;
    interval = 30;
    signal = 12;
    click = [
      { button = "left"; cmd = "alacritty --class float,float -e ${home}/workspace/devrc/scripts/mail-triage"; }
    ];
  };
  clawgateBlock = {
    block = "custom";
    command = "${scriptsDir}/i3status-clawgate";
    json = true;
    interval = 30;
    signal = 11;
    click = [
      { button = "left"; cmd = "xdg-open http://192.168.50.250:30302"; }
    ];
  };
  # DND indicator (workbench only): a small muted glyph that appears ONLY while
  # dunst is paused (quiet mode), hidden otherwise — same calm hide-at-zero idea
  # as the count blocks. Reads `dunstctl is-paused` (instant, local). signal 15
  # is sent by the `$mod+Shift+n` toggle for instant feedback; the short interval
  # is a backstop.
  dndBlock = {
    block = "custom";
    command = "${scriptsDir}/i3status-dnd";
    json = true;
    interval = 5;
    signal = 15;
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
  # agent-ops: workbench only. Static dashboard glyph (plain-text render, like
  # rigcontrol). A bar click can't cleanly spawn a tmux display-popup, so the
  # left-click opens the mission-control dashboard in a FLOATING alacritty (the
  # `class="float"` i3 rule floats it), sized to fit the ~6-section frame.
  agentOpsBlock = {
    block = "custom";
    command = "${scriptsDir}/i3blocks-agent-ops";
    interval = "once";
    click = [
      { button = "left"; cmd = "alacritty --class float,float -o window.dimensions.columns=130 -o window.dimensions.lines=45 -e ${home}/.config/tmux/agent-ops"; }
    ];
  };

  blocks =
    [ memoryBlock diskBlock netBlock cpuBlock temperatureBlock ]
    ++ lib.optional (!isLaptop) gpuBlock
    ++ lib.optional isLaptop batteryBlock
    ++ [ soundBlock ]
    ++ lib.optionals (!isLaptop) [ alertsBlock civitaiBlock mailBlock clawgateBlock dndBlock ]
    ++ [ vpnBlock timeBlock ]
    ++ lib.optionals (!isLaptop) [ agentOpsBlock rigcontrolBlock ];
in
lib.mkIf isNixOS {
  programs.i3status-rust = {
    enable = true;
    bars.top = {
      theme = "gruvbox-dark";
      # JetBrainsMono Nerd Font (declared below) provides the glyphs, so use the
      # Material-Design nerd-font icon set + the theme's default powerline separators.
      # Icon set in TABLE form (not the `icons = "..."` shortcut) so we can override
      # a single icon — the shortcut + an [icons.overrides] table conflict and drop
      # ALL icons back to text. material-nf maps `gpu` to nf-md-monitor (a display);
      # the RTX 5080 is not a monitor → nf-md-expansion_card (a graphics card).
      settings.icons = {
        icons = "material-nf";
        overrides.gpu = "󰢮";
      };
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
  home.file.".config/i3status-rust/scripts/i3blocks-agent-ops" = {
    source = ../scripts/i3blocks-agent-ops;
    executable = true;
  };

  # Decoupled status-count block scripts (workbench blocks reference these by
  # scriptsDir path). They only read ~/.cache/bar-status/*.json — instant, never
  # network. The poller itself (scripts/bar-status-poll) is NOT symlinked here: it
  # is run from the repo working tree by the systemd unit below so it can resolve
  # its sibling scripts/mail-actions/_db.py (cf. mail-actions/run-archive.sh).
  # The clawgate/mail/alerts block scripts + poller are workbench-only, so their
  # symlinks are !isLaptop-gated too (they'd be dead files on the laptop otherwise).
  home.file.".config/i3status-rust/scripts/i3status-clawgate" = lib.mkIf (!isLaptop) {
    source = ../scripts/i3status-clawgate;
    executable = true;
  };
  home.file.".config/i3status-rust/scripts/i3status-mail" = lib.mkIf (!isLaptop) {
    source = ../scripts/i3status-mail;
    executable = true;
  };
  home.file.".config/i3status-rust/scripts/i3status-alerts" = lib.mkIf (!isLaptop) {
    source = ../scripts/i3status-alerts;
    executable = true;
  };
  home.file.".config/i3status-rust/scripts/i3status-civitai" = lib.mkIf (!isLaptop) {
    source = ../scripts/i3status-civitai;
    executable = true;
  };
  home.file.".config/i3status-rust/scripts/i3status-dnd" = lib.mkIf (!isLaptop) {
    source = ../scripts/i3status-dnd;
    executable = true;
  };

  # bar-status poller — WORKBENCH ONLY (!isLaptop). Every ~45s it queries clawgate
  # (pending Tasks), the homelab Postgres (open mail_actions), and Alertmanager
  # (firing alerts, homelab required + production best-effort) and writes a small
  # JSON status file per source to ~/.cache/bar-status/, then signals i3status-rs
  # to refresh the matching block. Fully fail-safe: a down source writes a 'stale'
  # marker (the block renders empty) and never wedges. Laptop is excluded: it is
  # nebula-only with no direct LAN path to these homelab endpoints, exactly like
  # mail-actions-archive / repo-cos in home.nix.
  #
  # A user service runs with a minimal env, so PATH must be explicit: the pinned
  # python (psycopg2) + kubectl (the mail + Alertmanager port-forwards) + procps
  # (pkill signals the bar) + coreutils. It resolves the kubeconfig/clawgate.env/
  # repo paths itself (no .zshenv handles under systemd).
  systemd.user.services.bar-status-poll = lib.mkIf (!isLaptop) {
    Unit = {
      Description = "Poll clawgate/mail/alerts/civitai → ~/.cache/bar-status for the i3 bar";
      After = [ "network-online.target" ];
      Wants = [ "network-online.target" ];
    };
    Service = {
      Type = "oneshot";
      # Hard ceiling so a half-hung kubectl (nebula up, API server not answering)
      # can't wedge the poller forever: systemd kills the cgroup (reaping any stuck
      # kubectl child) and the timer re-arms. Without this, Type=oneshot defaults to
      # TimeoutStartSec=infinity and OnUnitActiveSec only re-fires once inactive.
      TimeoutStartSec = 90;
      Environment = [
        # systemd -> systemd-run, which launches the edge-toast as a DETACHED
        # transient --user service so a clickable dunstify outlives this oneshot's
        # cgroup teardown. procps -> pgrep (borrow DISPLAY/DBUS from i3 for the
        # toast). bash/dunstify/xdg-open resolve from the user-manager PATH inside
        # that transient unit, so they need not be on the poller's own PATH.
        "PATH=${lib.makeBinPath [ pollPyEnv pkgs.kubectl pkgs.procps pkgs.coreutils pkgs.systemd ]}"
        "KUBECONFIG=%h/workspace/homelab-talos/homelab-kubeconfig"
        # civitai (CLIENT) prod cluster kubeconfig — the civitai alerts source
        # port-forwards through THIS, never the homelab KUBECONFIG above.
        "CIVITAI_KUBECONFIG=%h/workspace/civit/datapacket-talos/prod-kubeconfig"
        "DEVRC_DIR=%h/workspace/devrc"
        "HOMELAB_DIR=%h/workspace/homelab-talos"
        "HOME=%h"
      ];
      ExecStart = "${pollPyEnv}/bin/python3 %h/workspace/devrc/scripts/bar-status-poll";
      # Re-run the unit when the poller changes (cf. X-Restart-Triggers in home.nix).
      X-Restart-Triggers = [ "${../scripts/bar-status-poll}" ];
    };
  };

  # Timer: fire the poller ~every 45s. OnUnitActiveSec re-arms after each run so a
  # slow poll never overlaps itself; OnStartupSec gives one prompt run after login.
  # (No Persistent — it only applies to OnCalendar timers, not monotonic ones.)
  systemd.user.timers.bar-status-poll = lib.mkIf (!isLaptop) {
    Unit = {
      Description = "Periodic timer for the i3 bar-status poller";
    };
    Timer = {
      OnStartupSec = "20s";
      OnUnitActiveSec = "45s";
    };
    Install = {
      WantedBy = [ "timers.target" ];
    };
  };
}
