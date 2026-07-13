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

  # Count-block red thresholds — SINGLE SOURCE for both the pill (--red-above) and
  # the poller's rising-edge toast (ALERTS_TOAST_ABOVE / CIVITAI_TOAST_ABOVE in the
  # systemd Environment below). Defining them once here stops the pill and its
  # toast from drifting apart (they did: pill 34 vs toast default 30 for alerts).
  alertsRedAbove = 34;
  civitaiRedAbove = 340;

  # Floating btop for the vitals-block left-clicks (memory/cpu/temperature/gpu).
  # `float,float` matches the existing i3 float rule so it opens as a float.
  # Explicit dimensions are REQUIRED — btop refuses to render ("terminal size too
  # small") in the default float size; matches the agentOps popup sizing idiom.
  btopCmd = "alacritty --class float,float -o window.dimensions.columns=160 -o window.dimensions.lines=45 -e btop";

  # Python env for the decoupled bar-status poller (workbench systemd user timer):
  # psycopg2 for the homelab Postgres open-mail_actions count; clawgate + Alertmanager
  # go over stdlib urllib, so psycopg2 is the only non-stdlib dep.
  pollPyEnv = pkgs.python312.withPackages (ps: [ ps.psycopg2 ]);

  # Built-in blocks (order = left → right on the bar).
  memoryBlock = {
    block = "memory";
    # Show RAM *used* as a size (e.g. "6.5GB"), NOT a percentage — a bare % here
    # collided visually with the cpu block's % (they read as two CPU items). A size
    # for RAM + a % for CPU are instantly distinct. warning/critical still key off %.
    format = " $icon $mem_used ";
    warning_mem = 80;
    critical_mem = 92;
    interval = 10;
    click = [
      { button = "left"; cmd = btopCmd; }
    ];
  };
  # Bar shows "/" only; left-click opens a rofi gauge list of all real filesystems
  # (disk-detail — mirrors the media/vpn detail idiom, not a raw df terminal dump).
  diskBlock = {
    block = "disk_space";
    path = "/";
    format = " $icon $available ";
    info_type = "available";
    interval = 60;
    click = [
      { button = "left"; cmd = "${scriptsDir}/disk-detail"; }
      # Right-click drills into the FULLEST real mount with ncdu (float terminal).
      { button = "right"; cmd = "alacritty --class float,float -e ${scriptsDir}/disk-explore"; }
    ];
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
    click = [
      { button = "left"; cmd = btopCmd; }
    ];
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
    click = [
      { button = "left"; cmd = btopCmd; }
    ];
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
    click = [
      { button = "left"; cmd = btopCmd; }
    ];
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
  # airvpn: the HOST-level AirVPN WireGuard tunnel (the whole workbench routes
  # through AirVPN). REPLACES the decommissioned host Mullvad block. Default-OFF,
  # toggled from the menu. Credential-free render (reads ~/.cache/bar-status/
  # airvpn.json written by the poller's `airvpn` source); signal 10 (inherited
  # from the retired vpnBlock). Distinct from mediaBlock (the qBit-pod AirVPN,
  # net_down icon) — this one uses net_vpn. CALM: dim `VPN off` when down, neutral
  # `AirVPN CC` when up+verified, RED on a leak, yellow on a down forwarded port,
  # soft-yellow `VPN?` on poller-stale. Left-click opens airvpn-menu (Connect/
  # Disconnect / switch server / verify exit-IP / forwarded-port / TUI); right-click
  # floats the airvpn-detail TUI. WORKBENCH-ONLY (the tunnel + poller are there).
  airvpnBlock = {
    block = "custom";
    command = "${scriptsDir}/i3status-airvpn";
    json = true;
    interval = 30;
    signal = 10;
    click = [
      { button = "left"; cmd = "${scriptsDir}/airvpn-menu"; }
      { button = "right"; cmd = "alacritty --class float,float -e ${scriptsDir}/airvpn-detail"; }
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
    # --red-above: neutral at/below the standing homelab backlog (~24-27 as of
    # 2026-07-11, all known noise), red only when the count climbs ABOVE it. Tune as
    # the baseline drifts. (civitai's stays low deliberately — its growth is real.)
    command = "${scriptsDir}/i3status-alerts --red-above ${toString alertsRedAbove}";
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
    command = "${scriptsDir}/i3status-civitai --red-above ${toString civitaiRedAbove}";
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
  # media (qBittorrent behind the gluetun AirVPN WireGuard sidecar). A SECOND VPN
  # pill, deliberately kept SEPARATE from vpnBlock: vpnBlock tracks the HOST
  # Mullvad tunnel, this one tracks the qBit AirVPN tunnel. Differentiated so they
  # aren't confusable — this pill uses the `net_down` icon (vs vpnBlock's net_vpn)
  # and reads `CA ↓.. ↑..` (the static SERVER_COUNTRIES=Canada label + qBit speed).
  # CALM: hidden when connected+idle; shows speeds while transferring; RED when the
  # tunnel is `firewalled` (forwarded port down); soft-yellow `qBit?` on poller-
  # stale. Left-click opens the media-menu rofi action launcher (open *arr UIs /
  # pause-resume / force-start / VPN reconnect / Whisparr search / float the live
  # `media-detail --watch` TUI); right-click opens the qBit WebUI directly. The menu
  # reads ~/.config/bar/media.env (0600) for creds — NOT baked into the store.
  mediaBlock = {
    block = "custom";
    command = "${scriptsDir}/i3status-media";
    json = true;
    interval = 30;
    signal = 16;
    click = [
      { button = "left"; cmd = "${scriptsDir}/media-menu"; }
      { button = "right"; cmd = "xdg-open http://qbittorrent.workbench.lan"; }
    ];
  };
  # Notifications bell (BOTH hosts) — merges the dunst DND state and the unseen-
  # notification badge into ONE calm pill (replaces the old DND-only dndBlock).
  # Reads `dunstctl history` / `is-paused` (instant, local — never network):
  #   DND paused        -> muted bell 󰂛 (neutral)
  #   unseen count N>0  -> 󰂚 N (red iff an unseen entry is CRITICAL, else neutral)
  #   nothing unseen    -> empty/invisible (hide-at-zero, like the count blocks)
  # "Unseen" = history ids above the ~/.cache/bar-status/notifs-seen marker; a
  # missing marker surfaces ALL history (so notifications suppressed during
  # fullscreen still show up). Left-click opens the notif-center rofi list
  # (toggle-DND / clear-all / history-pop a past toast); right-click toggles DND
  # instantly (mirrors the sound block's right-click idiom). signal 15 is
  # inherited from the retired dndBlock so the `$mod+Shift+n` keybind's
  # `pkill -RTMIN+15` and notif-center's mark-seen still refresh it. Purely local,
  # so it lives on BOTH hosts (the laptop runs dunst too + previously had no DND
  # indicator).
  notifsBlock = {
    block = "custom";
    command = "${scriptsDir}/i3status-notifs";
    json = true;
    interval = 5;
    signal = 15;
    click = [
      { button = "left"; cmd = "${scriptsDir}/notif-center"; }
      { button = "right"; cmd = "dunstctl set-paused toggle && pkill -RTMIN+15 i3status-rs"; }
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
  # agent-ops: workbench only. LIVE count of Claude-Code-in-tmux runs — renders
  # `󰕮 N` (N>0) / bare `󰕮` (N==0), always neutral (running agents are steady
  # state, not "blocked on you"). json render, recounts every 15s via a local
  # tmux+/proc scan (reuses agent-ops's tested detector) — NO poller/cache/signal
  # needed since it's local + cheap. The left-click still opens the mission-control
  # dashboard in a FLOATING alacritty (the `class="float"` i3 rule floats it).
  agentOpsBlock = {
    block = "custom";
    command = "${scriptsDir}/i3status-agent-ops";
    json = true;
    interval = 15;
    click = [
      { button = "left"; cmd = "alacritty --class float,float -o window.dimensions.columns=130 -o window.dimensions.lines=45 -e ${home}/.config/tmux/agent-ops"; }
    ];
  };

  blocks =
    [ memoryBlock diskBlock netBlock cpuBlock temperatureBlock ]
    ++ lib.optional (!isLaptop) gpuBlock
    ++ lib.optional isLaptop batteryBlock
    ++ [ soundBlock notifsBlock ]
    ++ lib.optionals (!isLaptop) [ alertsBlock civitaiBlock mailBlock clawgateBlock mediaBlock airvpnBlock ]
    ++ [ timeBlock ]
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

  # Host AirVPN block scripts (workbench-only), symlinked beside the generated TOML.
  # airvpn-sudo is deliberately NOT symlinked here — it must stay at the stable,
  # sudoers-trusted /etc/nixos/i3blocks-scripts/airvpn-sudo path (a nix-store path
  # would break the NOPASSWD rule + change every rebuild), exactly like the old
  # vpn-sudo. The credential-free render + menu + detail read the poller cache
  # (~/.cache/bar-status/airvpn.json) + the committed server manifest; no secret in
  # the store. The manifest is symlinked into scripts/data/ so airvpn-menu resolves
  # it relative to its own dir (MANIFEST = <script dir>/data/airvpn-servers.json).
  home.file.".config/i3status-rust/scripts/i3status-airvpn" = lib.mkIf (!isLaptop) {
    source = ../scripts/i3status-airvpn;
    executable = true;
  };
  home.file.".config/i3status-rust/scripts/airvpn-menu" = lib.mkIf (!isLaptop) {
    source = ../scripts/airvpn-menu;
    executable = true;
  };
  home.file.".config/i3status-rust/scripts/airvpn-detail" = lib.mkIf (!isLaptop) {
    source = ../scripts/airvpn-detail;
    executable = true;
  };
  home.file.".config/i3status-rust/scripts/data/airvpn-servers.json" = lib.mkIf (!isLaptop) {
    source = ../scripts/data/airvpn-servers.json;
  };
  home.file.".config/i3status-rust/scripts/disk-detail" = {
    source = ../scripts/disk-detail;
    executable = true;
  };
  # disk-explore: the disk block's right-click — ncdu on the fullest real mount.
  home.file.".config/i3status-rust/scripts/disk-explore" = {
    source = ../scripts/disk-explore;
    executable = true;
  };
  home.file.".config/i3status-rust/scripts/i3blocks-rigcontrol" = {
    source = ../scripts/i3blocks-rigcontrol;
    executable = true;
  };
  home.file.".config/i3status-rust/scripts/i3status-agent-ops" = {
    source = ../scripts/i3status-agent-ops;
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
  # notifications bell + its notif-center rofi list. BOTH hosts (NOT !isLaptop-
  # gated) — purely local via dunstctl, and the laptop gains a DND/notif indicator
  # it never had. notif-center loads i3status-notifs as a co-located sibling module
  # for the shared history/marker logic, so both MUST be symlinked together.
  home.file.".config/i3status-rust/scripts/i3status-notifs" = {
    source = ../scripts/i3status-notifs;
    executable = true;
  };
  home.file.".config/i3status-rust/scripts/notif-center" = {
    source = ../scripts/notif-center;
    executable = true;
  };
  # media block: the credential-free render script (reads ~/.cache/bar-status/
  # media.json) + its right-click detail popup. Both workbench-only. Creds/keys
  # for the popup live in ~/.config/bar/media.env (0600), NOT here / in the store.
  home.file.".config/i3status-rust/scripts/i3status-media" = lib.mkIf (!isLaptop) {
    source = ../scripts/i3status-media;
    executable = true;
  };
  home.file.".config/i3status-rust/scripts/media-detail" = lib.mkIf (!isLaptop) {
    source = ../scripts/media-detail;
    executable = true;
  };
  # media-menu: the right-click rofi action launcher (sibling of media-detail so it
  # can float `media-detail --watch`). Workbench-only. Reads creds from
  # ~/.config/bar/media.env (0600); no secret in the store.
  home.file.".config/i3status-rust/scripts/media-menu" = lib.mkIf (!isLaptop) {
    source = ../scripts/media-menu;
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
      Description = "Poll clawgate/mail/alerts/civitai/media/airvpn → ~/.cache/bar-status for the i3 bar";
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
        # /run/wrappers/bin first for the setuid `sudo` wrapper: the `airvpn`
        # source runs `sudo -n airvpn-sudo status` (read-only `wg show`, NOPASSWD)
        # to read the host tunnel state. iproute2 provides `ip` (link up/down probe).
        "PATH=/run/wrappers/bin:${lib.makeBinPath [ pollPyEnv pkgs.kubectl pkgs.procps pkgs.coreutils pkgs.systemd pkgs.iproute2 ]}"
        "KUBECONFIG=%h/workspace/homelab-talos/homelab-kubeconfig"
        # civitai (CLIENT) prod cluster kubeconfig — the civitai alerts source
        # port-forwards through THIS, never the homelab KUBECONFIG above.
        "CIVITAI_KUBECONFIG=%h/workspace/civit/datapacket-talos/prod-kubeconfig"
        "DEVRC_DIR=%h/workspace/devrc"
        "HOMELAB_DIR=%h/workspace/homelab-talos"
        # Rising-edge toast thresholds — SAME source as the pills' --red-above
        # (alertsRedAbove/civitaiRedAbove) so pill colour + toast fire on one line.
        # The poller's _env_int(..., 30/340) defaults are now pure fallback.
        "ALERTS_TOAST_ABOVE=${toString alertsRedAbove}"
        "CIVITAI_TOAST_ABOVE=${toString civitaiRedAbove}"
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
