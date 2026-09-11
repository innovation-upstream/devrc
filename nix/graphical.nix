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
# `i3 -c /etc/i3.conf` — run `sudo bash nix/system/apply-i3-to-hm.sh` to cutover.
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

  # Load-pill thresholds. 🔴 `loadWarnAbove` MUST equal `CPU_MON_THRESHOLD` in
  # nix/home.nix — the pill and cpu-monitor's toast are two renderings of ONE
  # decision, and the same drift already bit the alerts pill (34 vs 30, see
  # above). Pinned two-way by
  # `test_bar_status.py::test_the_load_pill_threshold_MATCHES_cpu_monitors`.
  #
  # NOT the core count. cpu-monitor was deliberately raised to a flat 48 on
  # 2026-08-05 to cut toast volume (measured: 123-267/day -> 11-32/day — see the
  # `dunst` RECALL BUFFER note in nix/home.nix, the paragraph reading "raising
  # CPU_MON_THRESHOLD/RUNAWAY_PCT on 08-05 cut the workbench from 123-267/day to
  # 11-32/day", ~:539). Keying the pill off `nproc` instead would warn at 24 on
  # the workbench — which idles in the twenties — re-creating exactly the noise
  # that change removed.
  loadWarnAbove = 48;
  loadCritAbove = 72;   # 1.5x the alert threshold: worse than "we already told you"

  # 🔴 SINGLE SOURCE for the cooling fan mapping — passed VERBATIM to BOTH the
  # `fansBlock` pill and its `fans-detail` click. They are two renderings of ONE
  # configuration, and they must never disagree about which header is the pump
  # or what floor alarms.
  #
  # This exists because an audit measured the drift: `fans-detail` used to carry
  # its own `KNOWN_FANS = [(1, "AIO pump", 500), (3, "Case fan", None)]` while
  # nix passed `--fan pump=1:500 --fan case=3` to the pill alone. Moving the pump
  # to another header and updating ONLY this line left **84 of 84 tests green**
  # and produced a pill reading `2448·1650 Idle` beside a view reading
  # `AIO pump ? unreadable`. The pill's own seam guard could not catch it: it
  # deliberately asserts STATE not spelling, which is right for the pill.
  #
  # The labels are display names because `fans-detail` renders them and the pill
  # renders none — so a friendly label costs the pill nothing and removes the
  # second copy. Pinned by
  # `test_fans_detail.py::test_the_pill_and_the_VIEW_get_the_SAME_fan_mapping`.
  fanArgs = "--fan 'AIO pump=1:500' --fan 'Case fan=3'";

  # Floating btop for the vitals-block left-clicks (memory/cpu/temperature/gpu).
  # `float,float` matches the existing i3 float rule so it opens as a float.
  # Explicit dimensions are REQUIRED — btop refuses to render ("terminal size too
  # small") in the default float size. (This 160x45 shape was the house idiom for
  # every float popup, including the retired agent-ops one.)
  btopCmd = "alacritty --class float,float -o window.dimensions.columns=160 -o window.dimensions.lines=45 -e btop";

  # The runaways pill's clicks — `runawaysBlock` binds it to BOTH buttons, and
  # is its only consumer. There is no Python counterpart any more:
  # `_syshealth_action` and the toast it served were deleted when cpu-monitor was
  # made the sole runaway announcer (see the `runaways` block in `_toast_specs`),
  # so this string is now the ONE spelling of the rule rather than one of two
  # that had to be kept in sync.
  # 🔴 The `read -n 1` hold is load-bearing: syshealth prints and exits in
  # ~0.16 s, and `alacritty -e CMD` closes when CMD does, so without it the
  # window flashes and vanishes — indistinguishable from the click doing
  # nothing, which is a defect this pill shipped once already.
  # 🔴 TWO tests, because they pin two DIFFERENT things and one alone is
  # walkable. `test_the_runaways_CLICK_still_holds_its_terminal_open` pins this
  # STRING's shape (terminal + syshealth + the hold) and never opens
  # `runawaysBlock`; `test_the_runaways_pill_CLICKS_are_wired_to_syshealthCmd`
  # pins that `runawaysBlock` actually commands this binding. MEASURED: with
  # only the first, re-pointing the left-click at `btopCmd` left 568 passed,
  # MUTANT SURVIVED — a perfect string nothing calls.
  # `${home}` rather than a literal `~`: every other working-tree reference in
  # this file interpolates it, and `~` survives only if the click is spawned
  # through a shell.
  syshealthCmd = "alacritty --class float,float -o window.dimensions.columns=120 -o window.dimensions.lines=40 -e ${pkgs.bash}/bin/bash -c '${home}/workspace/devrc/scripts/syshealth; echo; read -n 1 -r -s -p \"[any key to close]\"'";

  # The scratchpad legend pill's left-click: the same fzf picker tmux binds to
  # Alt+Shift+T (`~/.config/tmux/scratch-picker.sh`, deployed by nix/home.nix).
  # 🔴 THE FLOAT TERMINAL IS NOT OPTIONAL, for exactly the reason spelled out on
  # `runawaysBlock` below: i3status-rust runs a click through `sh -c` with NO
  # CONTROLLING TERMINAL, and scratch-picker.sh is an fzf TUI — a bare invocation
  # here exits `inappropriate ioctl for device` and the click is a SILENT NO-OP.
  # So it is modelled on `syshealthCmd` above.
  #
  # NO `read -n 1` HOLD IN THIS STRING, and that is a difference from
  # syshealthCmd rather than an omission: the picker carries its OWN hold, on
  # the only path that needs one. syshealth prints and exits in ~0.16 s, so
  # every one of its runs would flash; the picker's normal exits are either
  # long-lived (`tmux attach-session` / `tmux new-session` own the terminal
  # until the operator detaches) or a DELIBERATE instant exit (the operator
  # dismissed fzf). Its one accidental instant exit — attach failed AND create
  # failed — holds, inside the script, where it can print why.
  #
  # 🔴 AN EARLIER VERSION OF THIS COMMENT CLAIMED EVERY INSTANT EXIT WAS
  # DELIBERATE, AND THAT WAS FALSE. The picker opened with
  # `tmux display-message -p '#{session_name}'`, which — run OUTSIDE any tmux
  # client, which is exactly what this click does — answers with the server's
  # MOST-RECENTLY-USED session rather than "none". REPRODUCED on a private
  # socket 2026-09-11: with `$TMUX` unset, one client attached to the
  # NON-scratch session `work` and `scratch2` most recently used, the picker
  # took its `scratch*` detach branch and `tmux detach-client` — which outside a
  # client targets the server's best client, not "this" one — threw the `work`
  # client off the server. Exit 0, no output: the click read as "did nothing"
  # while damaging an unrelated session. FIXED IN THE PICKER, not here:
  # scripts/tmux-scratch-picker.sh now requires `[ -n "$TMUX" ]` for that
  # branch, and the outside-a-client path was then exercised end to end on a
  # real pty (fzf renders, selecting a slot attaches a client to it).
  scratchPickerCmd = "alacritty --class float,float -o window.dimensions.columns=120 -o window.dimensions.lines=40 -e ${home}/.config/tmux/scratch-picker.sh";

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
      { button = "left"; cmd = "alacritty --class float,float -o window.dimensions.columns=100 -o window.dimensions.lines=25 -e ${scriptsDir}/memory-detail"; }
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
  # load: 1-minute load average, read straight from /proc/loadavg (no cache, no
  # poller, no `bar_freshness` sibling — nothing here can go stale). Invisible
  # below `loadWarnAbove`; Warning at it (the SAME number cpu-monitor toasts
  # on); Critical at `loadCritAbove`. Both come from the single source at the
  # top of this file — see the 🔴 there for why it is not the core count.
  # Left-click → btop.
  #
  # 🔴 UNCONDITIONAL on purpose, in BOTH places — this entry and the `home.file`
  # below. /proc/loadavg exists on every host, so there is no reason to gate it;
  # gating only ONE of the two is what ships a block pointing at a script that
  # was never deployed. `test_bar_status.py` now enforces that pairing.
  loadBlock = {
    block = "custom";
    command = "${scriptsDir}/i3status-load --warning ${toString loadWarnAbove} --critical ${toString loadCritAbove}";
    json = true;
    # 15s, not 5s. This spawns a python process every tick forever on every
    # host; at 5s that is ~17k spawns/day for a value that is a ONE-MINUTE
    # average and cannot move meaningfully faster. The sibling custom blocks
    # are 15-30s.
    interval = 15;
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
  # fans: workbench only. AIO pump + case fan RPM, read straight from the
  # Nuvoton NCT6687D Super I/O via /sys/class/hwmon (no cache, no poller, no
  # `bar_freshness` sibling — nothing here can go stale).
  #
  #   `2660·1736`  Idle      both turning, both above their floors
  #   `!0·1736`    Critical  the PUMP has stopped
  #   `2660·?`     Warning   one tacho unreadable
  #   `?`          Warning   the chip is absent — i.e. `nct6683` is not loaded
  #
  # 🔴 NOT hide-at-zero, unlike every count pill. A count's quiet state means
  # "nothing to do"; a pump RPM is the number itself, and a cooler pill that is
  # invisible while healthy is invisible in exactly the state it certifies. The
  # colour rules still hold — neutral until something actually stalls.
  #
  # 🔴 The FLOOR IS PER-FAN AND OPTIONAL, and only the pump gets one. fan2 and
  # fan4-10 have nothing plugged in and read a permanent 0, and a case fan on a
  # zero-RPM PWM curve may legitimately stop when idle — a blanket floor would
  # make the pill cry wolf about a fan doing its job. The pump must never stop,
  # so it alarms below 500 RPM (it runs ~2660-2823, driven at pwm1=114%, which
  # is normal: MSI overdrives pump headers by design).
  #
  # 🔴 Workbench only, and gated in BOTH places — this entry via the `blocks`
  # list below and the `home.file` further down. The chip is this board's Super
  # I/O (MSI X670E GAMING PLUS WIFI); the laptop has no such device, and a block
  # whose script is deployed under a narrower gate than the block itself renders
  # a command that does not exist.
  #
  # 🔴 The driver is `nct6683` (NOT nct6775, which reports "no such device" for
  # this chip) and NOTHING loads it automatically. `nix/system/apply-nct6683-module.sh`
  # persists it via boot.kernelModules; until that has been run under sudo this
  # pill renders `?` after every reboot — deliberately visible, so the missing
  # driver announces itself rather than showing a blank block.
  #
  # 30s: a pump does not change speed meaningfully faster, and this spawns a
  # python process every tick forever.
  # 🔴 LEFT-CLICK OPENS THE COOLING VIEW, NOT btop. It used to be btop, which is
  # a CPU/memory view: when this pill goes red the questions are "is the pump
  # dead" and "how hot is the thing it was cooling", and btop answers neither.
  # `fans-detail` is the cooling equivalent of the memory/disk/media detail
  # floats — pump + case fan with PWM duty, then CPU/GPU/VRM/NVMe temperatures.
  fansBlock = {
    block = "custom";
    command = "${scriptsDir}/i3status-fans ${fanArgs}";
    json = true;
    interval = 30;
    click = [
      { button = "left"; cmd = "alacritty --class float,float -o window.dimensions.columns=84 -o window.dimensions.lines=22 -e ${scriptsDir}/fans-detail ${fanArgs}"; }
    ];
  };
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
  # Click opens the client Grafana, whose hostname is host-local: it lives in
  # ~/.config/bar/urls.env as `civitai_grafana` and is resolved by `bar-url` at
  # click time, NOT baked in here (this repo is PUBLIC — see scripts/bar-url).
  civitaiBlock = {
    block = "custom";
    # --red-above: neutral at/below the standing civitai-prod backlog (~312), red
    # only above it. Big client cluster, so the baseline is high; tune as it drifts.
    command = "${scriptsDir}/i3status-civitai --red-above ${toString civitaiRedAbove}";
    json = true;
    interval = 30;
    signal = 14;
    click = [
      { button = "left"; cmd = "${scriptsDir}/bar-url --open civitai_grafana"; }
    ];
  };
  # Telemetry deadman — which activity-telemetry (host, source) pairs have
  # STOPPED emitting. Covers BOTH hosts from this one workbench poller, because
  # the check reads the shared ClickHouse table rather than local state.
  # `tlm N` (Critical) = N dead sources; `tlm ?` (Warning) = the check has been
  # unable to evaluate for >30 min and CANNOT VOUCH for the pipeline — that
  # second state exists because this is the one block whose reassuring answer is
  # a zero, and a zero from a broken query must not read as "all healthy".
  # No --red-above: there is no standing backlog here, any count is real.
  telemetryBlock = {
    block = "custom";
    command = "${scriptsDir}/i3status-telemetry";
    json = true;
    interval = 30;
    signal = 17;
    click = [
      # Float the full per-host/per-source table (measured budget vs measured
      # silence, per pair). `read` holds the window open — deadman.py prints and
      # exits, and i3status-rust runs a click cmd through `sh -c`.
      { button = "left"; cmd = "alacritty --class float,float -o window.dimensions.columns=110 -o window.dimensions.lines=30 -e ${pkgs.bash}/bin/bash -c '${pollPyEnv}/bin/python3 ${home}/workspace/devrc/scripts/collector/deadman.py; echo; read -n 1 -r -s -p \"[any key to close]\"'"; }
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
  # stale. Left-click opens the media-menu rofi action launcher (open the service
  # UIs / pause-resume / force-start / VPN reconnect / search all missing / float
  # the live `media-detail --watch` TUI); right-click opens the qBit WebUI directly. The menu
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
  # rigcontrol: workbench only. Instant toggle on left-click (sleep ↔ wake).
  # The click handler runs via setsid -f so it doesn't block the bar during
  # the ~30s fade-in. i3status-rust does NOT pass $BLOCK_BUTTON to custom
  # block commands, so the toggle lives in a [[block.click]] handler, not
  # inside the render script.
  rigcontrolBlock = {
    block = "custom";
    command = "${scriptsDir}/i3blocks-rigcontrol";
    interval = "once";
    click = [
      { button = "left"; cmd = "setsid -f ${home}/workspace/devrc/scripts/rig-control-toggle"; }
    ];
  };
  # claude-runs: workbench only. LIVE count of Claude-Code-in-tmux runs — renders
  # `󰕮 N` (N>0) / bare `󰕮` (N==0) / `󰕮 ?` (could not measure), always neutral
  # (running agents are steady state, not "blocked on you"). json render,
  # recounts every 15s via a local tmux+/proc scan — NO poller/cache/signal
  # needed since it's local + cheap, so `bar_freshness` does not apply here.
  #
  # 🔴 IT HAS NO CLICK ANY MORE, deliberately. This block used to double as the
  # launcher for the `agent-ops` mission-control TUI, which is RETIRED (its
  # panels all had homes elsewhere — session-manager, /initiative-scan
  # and the bar's own pills — and its one irreplaceable part, the /proc-walking
  # Claude detector, was extracted to scripts/lib/claude_sessions.py). A click
  # exec'ing a path home-manager no longer deploys fails silently, so the click
  # goes with the TUI rather than pointing at a successor that is not a TUI.
  claudeRunsBlock = {
    block = "custom";
    command = "${scriptsDir}/i3status-claude-runs";
    json = true;
    interval = 15;
  };
  # scratchpads: the scratchpad COLOUR LEGEND — all 20 slots from the canonical
  # table (scripts/tmux-scratch-slots.sh) as `<hotkey><window count>` in the
  # slot's own colour when its tmux session is live, or the bare hotkey dimmed
  # to #504945 when it is not. The colours are the popup border colours set in
  # .tmux.conf, so the pill answers "which Alt-key gets me back to the orange
  # popup". `scratch ?` when it could not be measured — the same discriminant
  # grammar as claudeRunsBlock, and the reason the script is not a one-liner:
  # "no scratchpad sessions" is a REAL reading here (every slot dim) and must
  # not be confusable with a broken slot table or a missing tmux.
  #
  # MIGRATED OFF THE TMUX STATUS LINE. This was `#(scratch-status.sh)` in
  # `status-left`, where `status-left-length 90` minus the `#S` segment left
  # room for 14 of the 20 slots (its renderer hardcoded a `for i = 7` start to
  # drop the first six). The bar fits all 20 and is on screen from every
  # workspace, not only inside a tmux client.
  #
  # STATE IS ALWAYS Idle: a legend never demands attention. The bar is CALM —
  # colour means "look at me" — and the per-slot colour here rides inside pango
  # markup rather than in the block's state, so it colours letters without ever
  # claiming the whole pill is an alert.
  #
  # NO poller / cache / signal, exactly like loadBlock and claudeRunsBlock: one
  # local `tmux list-sessions` per tick is instant and never touches the
  # network, so there is nothing to go stale and `bar_freshness` (the
  # cache-staleness gate the count pills share) does not apply. 30s because a
  # scratchpad's window count is not a fast-moving number.
  #
  # 🔴 UNCONDITIONAL on both hosts, on purpose, in BOTH places — this entry and
  # the two `home.file`s below. Unlike the poller-backed count pills (workbench
  # only, they need credentials and a timer) the scratchpads are plain tmux
  # sessions and the Alt-key bindings are generated on every host, so the laptop
  # wants this legend just as much. Gating only ONE of the two is what ships a
  # block pointing at a script that was never deployed.
  scratchpadsBlock = {
    block = "custom";
    command = "${scriptsDir}/i3status-scratchpads";
    json = true;
    interval = 30;
    click = [
      { button = "left"; cmd = scratchPickerCmd; }
    ];
  };
  # gamemode (BOTH hosts): the toggle for i3's empty `mode "game"` binding mode.
  #   in game mode -> ` 󰊗 GAME `  Critical
  #   otherwise    -> ` 󰊗 `       Idle, ALWAYS VISIBLE
  #
  # 🔴 WHY THIS PILL EXISTS AT ALL. `$mod` is Mod1 — ALT — so i3 holds a global
  # X11 grab on ~60 Alt combos and a fullscreen game never receives any of them.
  # An EMPTY binding mode is the only native way to make i3 release those grabs
  # (a guard on the `exec` does not: i3 has already eaten the key), and i3 has no
  # per-window `bindsym` criteria, so the mode has to be switched deliberately.
  # The way IN is this pill; the way OUT is `Pause`/`Scroll_Lock` (bound INSIDE
  # the mode) or this pill again. There is deliberately no default-mode keybind
  # to enter — that would be one more Alt grab for no gain.
  #
  # NOT hide-at-zero, unlike every count pill: this block is the only entrance,
  # so an invisible idle state would be a toggle with no off-state affordance —
  # the same reason `notifsBlock` renders a bare bell at zero.
  #
  # signal 18: the next free real-time signal. 10-14, 16 and 17 belong to
  # `SIGNALS` in bar-status-poll (airvpn/clawgate/mail/alerts/civitai/media/
  # telemetry) and 15 to notifs, so 18 is the first unused. It is repainted by
  # the script's own `pkill -RTMIN+18` on both paths — the click below, and the
  # `mode "game"` escape bindings in nix/i3/config.nix. `interval` is a BACKSTOP
  # only, for a mode change made by some other route (`i3-msg mode game` by hand,
  # the SSH rescue path); the signal is what makes it feel instant.
  #
  # i3status-rust does NOT pass `$BLOCK_BUTTON` to custom block commands (see
  # rigcontrolBlock above), so the toggle is a `[[block.click]]` handler. It
  # re-invokes the SAME script with `--toggle` rather than open-coding the
  # i3-msg dance in nix: one deployed file, and the flip logic lands somewhere a
  # unit test can reach it.
  #
  # BOTH hosts — purely local (one i3-msg to the running WM), no poller, no
  # cache, no network. The laptop's `$mod` is the same Alt.
  gamemodeBlock = {
    block = "custom";
    command = "${scriptsDir}/i3status-gamemode";
    json = true;
    interval = 30;
    signal = 18;
    click = [
      { button = "left"; cmd = "${scriptsDir}/i3status-gamemode --toggle"; }
    ];
  };
  # runaways: workbench only. Count of runaway processes (sustained high CPU),
  # as decided by `scripts/syshealth` — the poller renders that verdict and owns
  # no predicate of its own. Hide-at-zero; red when >0.
  # 🔴 BOTH buttons open syshealth in a FLOAT TERMINAL, and the terminal is not
  # optional. i3status-rust runs a click through `sh -c` with NO CONTROLLING
  # TERMINAL (the live i3status-rs has TTY `?`), so a bare TUI here exits
  # `inappropriate ioctl for device` and the click is a SILENT NO-OP. An earlier
  # revision pointed left-click at a bare fzf menu for exactly that reason.
  # The bare-command left-clicks elsewhere in this file need no tty either —
  # rofi menus, yad, and the two toggles are GUIs or fire-and-forget, none of
  # them a TUI. (An earlier revision of this comment said "all rofi", which is
  # true of the menu/detail handlers and false of yad/gamemode/rig-control.)
  # Signal 19, matching SIGNALS in bar-status-poll.
  runawaysBlock = {
    block = "custom";
    command = "${scriptsDir}/i3status-runaways";
    json = true;
    interval = 30;
    signal = 19;
    click = [
      { button = "left"; cmd = syshealthCmd; }
      { button = "right"; cmd = syshealthCmd; }
    ];
  };

  blocks =
    [ memoryBlock diskBlock scratchpadsBlock netBlock cpuBlock loadBlock temperatureBlock ]
    ++ lib.optional (!isLaptop) fansBlock
    ++ lib.optional (!isLaptop) gpuBlock
    ++ lib.optional isLaptop batteryBlock
    ++ [ soundBlock ]
    ++ lib.optionals (!isLaptop) [ telemetryBlock alertsBlock civitaiBlock mailBlock clawgateBlock mediaBlock airvpnBlock runawaysBlock ]
    ++ [ timeBlock ]
    ++ lib.optionals (!isLaptop) [ claudeRunsBlock rigcontrolBlock ]
    ++ [ gamemodeBlock notifsBlock ];
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
  #
  # deep-search (WORKBENCH ONLY): a tiny PATH wrapper so the media tool is runnable
  # as a bare `deep-search` from any terminal. It execs the live script symlinked
  # into scriptsDir below (same file HM manages), pinned to a python3 with the
  # stdlib it needs (urllib/json/argparse) — no secret enters the nix store.
  #
  # Ferdium: multi-messenger desktop client (WhatsApp, Telegram, Slack, Discord,
  # 400+ recipes). Its login prompt is bypassed by pointing it at a self-hosted
  # Ferdium Server — see claudedocs/handoff-ferdium-server.md.
  #
  # Fonts: WhatsApp Web renders poorly without noto-fonts + colour emoji, and
  # liberation_ttf supplies the Segoe UI metrics it expects. `noto-fonts-emoji`
  # was RENAMED to `noto-fonts-color-emoji` in current nixpkgs — the old name
  # fails the switch with an attribute error, not a warning.
  home.packages = with pkgs; [
    nerd-fonts.jetbrains-mono
    ferdium
    noto-fonts
    noto-fonts-color-emoji
    liberation_ttf
  ]
    ++ lib.optional (!isLaptop) (pkgs.writeShellScriptBin "deep-search" ''
      exec ${pkgs.python3}/bin/python3 ${scriptsDir}/deep-search "$@"
    '');
  fonts.fontconfig.enable = true;

  # i3 config — raw string. INERT until the system cutover (apply-i3-to-hm.sh).
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
  # memory-detail: the memory block's left-click — top RAM consumers float.
  home.file.".config/i3status-rust/scripts/memory-detail" = {
    source = ../scripts/memory-detail;
    executable = true;
  };
  home.file.".config/i3status-rust/scripts/i3blocks-rigcontrol" = {
    source = ../scripts/i3blocks-rigcontrol;
    executable = true;
  };
  home.file.".config/i3status-rust/scripts/i3status-claude-runs" = {
    source = ../scripts/i3status-claude-runs;
    executable = true;
  };
  # gamemode: see `gamemodeBlock` above. UNCONDITIONAL, matching the block's
  # presence in the unconditional half of `blocks`. It is ALSO the block's own
  # left-click target (`… --toggle`), so a narrower gate here would ship a pill
  # that renders on a host where clicking it does nothing — and unlike a broken
  # `command`, a broken click is invisible until someone tries it mid-game.
  home.file.".config/i3status-rust/scripts/i3status-gamemode" = {
    source = ../scripts/i3status-gamemode;
    executable = true;
  };
  # load: see `loadBlock` above. UNCONDITIONAL, matching the block's presence in
  # the unconditional half of `blocks` — a narrower gate here than there means a
  # host renders a `custom` block whose command does not exist.
  home.file.".config/i3status-rust/scripts/i3status-load" = {
    source = ../scripts/i3status-load;
    executable = true;
  };
  # fans: see `fansBlock` above. `mkIf (!isLaptop)` MATCHES the block's gate in
  # the `blocks` list — the NCT6687D is the workbench board's Super I/O, and the
  # laptop has no such chip. Gating one of the two and not the other is what
  # ships a block whose command does not exist.
  home.file.".config/i3status-rust/scripts/i3status-fans" = lib.mkIf (!isLaptop) {
    source = ../scripts/i3status-fans;
    executable = true;
  };
  # 🔴 fans-detail is the fans pill's left-click target, AND `i3status-fans`
  # above is its REQUIRED CO-LOCATED SIBLING — fans-detail loads it by path to
  # reuse the chip-location and tacho-reading predicate rather than open-coding
  # a second copy that would drift silently (only one of the two is on screen).
  # So the two MUST carry the SAME gate: a fans-detail deployed without
  # i3status-fans beside it renders a red "sibling did not load" banner instead
  # of the cooling view. Pinned by
  # `test_fans_detail.py::test_fans_detail_and_its_SIBLING_are_deployed_together`.
  home.file.".config/i3status-rust/scripts/fans-detail" = lib.mkIf (!isLaptop) {
    source = ../scripts/fans-detail;
    executable = true;
  };
  # 🔴 claude_sessions.py is a CO-LOCATED SIBLING MODULE, not a block — the same
  # shape as bar_freshness.py BELOW (~line 470), and for the same reason: the
  # block scripts are extensionless, so they cannot be a package and cannot
  # import each other. It holds the ONE Claude-in-tmux detector (a /proc tree
  # walk, strictly more accurate than session-manager's
  # `pane_current_command =~ /claude/`), which used to live inside the retired
  # `agent-ops` TUI and be loaded out of ~/.config/tmux/agent-ops.
  #
  # It MUST be symlinked beside its consumer: without this entry the pill cannot
  # load the detector. That case now renders `󰕮 ?` rather than a bare glyph —
  # before the extraction it was indistinguishable from "nothing is running",
  # which on a workbench with 35 live sessions is the quietest possible lie.
  #
  # Both this entry and the block script above are UNGATED, while claudeRunsBlock
  # itself is `(!isLaptop)` — so the deploy is WIDER than its consumer, never
  # narrower. ⚠ That direction matters and is one edit from inverting:
  # bar_freshness.py below IS `mkIf (!isLaptop)`, so the narrow shape is the
  # local idiom. `mkIf isLaptop` here would leave the workbench — the only host
  # carrying the block — without the module, and the pill would render `?` on
  # the one machine that has it. The file must also be `git add`ed or the flake
  # omits it with a perfectly green switch. All of that is asserted structurally
  # (not by spelling this path) in
  # `test_the_bar_block_and_EVERY_file_it_needs_deploy_on_the_SAME_hosts` +
  # `test_the_shared_module_is_DEPLOYED_beside_the_block_that_loads_it`.
  home.file.".config/i3status-rust/scripts/claude_sessions.py" = {
    source = ../scripts/lib/claude_sessions.py;
  };

  # scratchpads: see `scratchpadsBlock` above. UNCONDITIONAL, matching the
  # block's presence in the unconditional half of `blocks` — a narrower gate
  # here than there means a host renders a `custom` block whose command does not
  # exist. Scratchpads are plain tmux sessions and the Alt-key bindings are
  # generated on both hosts, so there is nothing host-specific to gate on.
  home.file.".config/i3status-rust/scripts/i3status-scratchpads" = {
    source = ../scripts/i3status-scratchpads;
    executable = true;
  };
  # 🔴 scratch-slots.sh is a CO-LOCATED SIBLING DATA FILE, not a block — the
  # same shape as claude_sessions.py above, and for the same reason: the block
  # script is deployed as a lone nix-store symlink into scriptsDir, so anything
  # it reads must be symlinked BESIDE it. This is leg 1 of `_SLOT_PATHS` in
  # scripts/i3status-scratchpads, and the only leg that is true on a live host.
  # Without this entry the pill cannot load the slot table and renders
  # `scratch ?` — correctly, but permanently.
  #
  # It is the SAME canonical file nix/home.nix already deploys to
  # ~/.config/tmux/scratch-slots.sh for the tmux consumers; this is a second
  # symlink to the one source of truth, NOT a copy. Deployed under its DEPLOYED
  # name (`scratch-slots.sh`, not `tmux-scratch-slots.sh`) to match that
  # convention and the block's leg-1 lookup.
  #
  # UNGATED, matching the block script above — the deploy must never be
  # NARROWER than its consumer. And like every managed path, a new file must be
  # `git add`ed or the flake omits it from the deploy with a perfectly green
  # switch.
  home.file.".config/i3status-rust/scripts/scratch-slots.sh" = {
    source = ../scripts/tmux-scratch-slots.sh;
  };

  # Decoupled status-count block scripts (workbench blocks reference these by
  # scriptsDir path). They only read ~/.cache/bar-status/*.json — instant, never
  # network. The poller itself (scripts/bar-status-poll) is NOT symlinked here: it
  # is run from the repo working tree by the systemd unit below so it can resolve
  # its sibling scripts/mail-actions/_db.py (cf. mail-actions/run-archive.sh).
  # The clawgate/mail/alerts block scripts + poller are workbench-only, so their
  # symlinks are !isLaptop-gated too (they'd be dead files on the laptop otherwise).
  #
  # 🔴 bar_freshness.py is a CO-LOCATED SIBLING MODULE, not a block. Every count/
  # state block below loads it by explicit path out of its OWN directory (the
  # scripts are extensionless, so they cannot be a package and cannot import each
  # other) to get the one definition of "this cache is too old to present as a
  # measurement". It MUST be symlinked beside them: a block that cannot load it
  # falls through to its `?` pill, so a missing entry here turns every count pill
  # on a perfectly healthy workbench into a question mark. Same !isLaptop gate as
  # its consumers, pinned two-way against this file by
  # `test_every_block_that_loads_the_sibling_is_DEPLOYED_beside_it`.
  #
  # ⚠ THAT FALLBACK IS ONLY TRUE BECAUSE THE LOAD IS DEFERRED, and it was FALSE
  # when this comment was first written: the load ran bare at module level, so a
  # missing sibling killed the block outright (exit 1, empty stdout) rather than
  # producing the `?` this comment promises. The blocks now keep `fresh = None`
  # on a failed load and fail at USE, inside `__main__`'s `except`. Measured by
  # `test_a_block_that_cannot_load_the_SIBLING_renders_the_VISIBLE_pill`, which
  # runs each block with no sibling present — so if that ever regresses, the
  # failure here is a dead pill, not a question mark.
  home.file.".config/i3status-rust/scripts/bar_freshness.py" = lib.mkIf (!isLaptop) {
    source = ../scripts/bar_freshness.py;
  };
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
  home.file.".config/i3status-rust/scripts/i3status-telemetry" = lib.mkIf (!isLaptop) {
    source = ../scripts/i3status-telemetry;
    executable = true;
  };
  home.file.".config/i3status-rust/scripts/i3status-civitai" = lib.mkIf (!isLaptop) {
    source = ../scripts/i3status-civitai;
    executable = true;
  };
  # bar-url: resolves a NAMED host-specific URL out of ~/.config/bar/urls.env at
  # CLICK time. This repo is public, so a real client dashboard hostname cannot be
  # a nix string literal — but placeholdering it would leave a dead button, so the
  # value moves out of tracked source and the button keeps working. Same shape as
  # ~/.config/bar/{media,airvpn}.env. Read by the civitai block's left-click below
  # and by bar-status-poll's matching toast, so ONE file answers both.
  home.file.".config/i3status-rust/scripts/bar-url" = lib.mkIf (!isLaptop) {
    source = ../scripts/bar-url;
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
  # deep-search: terminal tool to search ALL Prowlarr indexers for a release and grab
  # a chosen one straight into qBittorrent. Sibling of media-detail/media-menu; reads
  # the same media.env creds (PROWLARR_URL/PROWLARR_KEY) by explicit path.
  # Workbench-only. Invoked as a bare `deep-search` via the writeShellScriptBin PATH
  # wrapper in home.packages.
  home.file.".config/i3status-rust/scripts/deep-search" = lib.mkIf (!isLaptop) {
    source = ../scripts/deep-search;
    executable = true;
  };
  # runaways: the block script (reads ~/.cache/bar-status/runaways.json).
  # Workbench-only, matching runawaysBlock's gate. Both clicks run `syshealth`
  # from the working tree via syshealthCmd, so there is no click handler to
  # deploy here.
  home.file.".config/i3status-rust/scripts/i3status-runaways" = lib.mkIf (!isLaptop) {
    source = ../scripts/i3status-runaways;
    executable = true;
  };

  # bar-status poller — WORKBENCH ONLY (!isLaptop). Every ~45s it queries clawgate
  # (pending Tasks), the homelab Postgres (open mail_actions), and Alertmanager
  # (firing alerts, homelab required + production best-effort) and writes a small
  # JSON status file per source to ~/.cache/bar-status/, then signals i3status-rs
  # to refresh the matching block. Fully fail-safe: a down source writes a 'stale'
  # marker (the block renders empty) and never wedges. Laptop is excluded: it is
  # nebula-only with no direct LAN path to these homelab endpoints, exactly like
  # mail-actions-archive in home.nix.
  #
  # A user service runs with a minimal env, so PATH must be explicit: the pinned
  # python (psycopg2) + kubectl (the mail + Alertmanager port-forwards) + procps
  # (pkill signals the bar) + coreutils. It resolves the kubeconfig/clawgate.env/
  # repo paths itself (no .zshenv handles under systemd).
  systemd.user.services.bar-status-poll = lib.mkIf (!isLaptop) {
    Unit = {
      Description = "Poll clawgate/mail/alerts/civitai/media/airvpn/telemetry/runaways → ~/.cache/bar-status for the i3 bar";
      After = [ "network-online.target" ];
      Wants = [ "network-online.target" ];
      # Toast on failure (the notify-failure@ template lives in home.nix, installed
      # on every host). This is the workbench, so the toast actually fires here.
      OnFailure = [ "notify-failure@%n.service" ];
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
      # deadman.py and lib/clawgate_tasks.py are listed too: the poller loads
      # BOTH by explicit path out of the working tree, so without these entries a
      # change to the deadman logic — or to the shared clawgate "needs the
      # operator" predicate, which decides the pill's whole meaning — would leave
      # the unit definition identical and the timer would not re-arm.
      # syshealth joined that set when the runaways source stopped carrying its
      # own predicate and started rendering syshealth's verdict — the poller
      # execs `$DEVRC_DIR/scripts/syshealth`, an explicit working-tree path, so
      # it belongs here by the rule stated above. Effect is milder than the
      # others (the unit is a oneshot re-run every 45s, so a changed syshealth
      # applies on the next poll either way); it is listed because the ledger
      # claims to enumerate this class, and a ledger that silently omits a
      # member is worse than one that never claimed to be complete.
      X-Restart-Triggers = [
        "${../scripts/bar-status-poll}"
        "${../scripts/collector/deadman.py}"
        "${../scripts/lib/clawgate_tasks.py}"
        "${../scripts/syshealth}"
      ];
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

  # rig-control scheduled mode switches — workbench only.
  # 3am → sleep (RGB off + monitor blackout), 10:15am → wake (RGB on + restore).
  systemd.user.services.rig-control-sleep = lib.mkIf (!isLaptop) {
    Unit = {
      Description = "Auto sleep mode: chassis RGB off + monitor blackout";
      After = [ "graphical-session.target" ];
    };
    Service = {
      Type = "oneshot";
      ExecStart = "${home}/workspace/devrc/scripts/rig-control.sh sleep";
    };
  };
  systemd.user.timers.rig-control-sleep = lib.mkIf (!isLaptop) {
    Unit = {
      Description = "Daily 3am sleep mode";
    };
    Timer = {
      OnCalendar = "02:30:00";
      Persistent = true;
    };
    Install = {
      WantedBy = [ "timers.target" ];
    };
  };

  systemd.user.services.rig-control-wake = lib.mkIf (!isLaptop) {
    Unit = {
      Description = "Auto wake mode: chassis RGB on + monitor restore";
      After = [ "graphical-session.target" ];
    };
    Service = {
      Type = "oneshot";
      ExecStart = "${home}/workspace/devrc/scripts/rig-control.sh wake";
    };
  };
  systemd.user.timers.rig-control-wake = lib.mkIf (!isLaptop) {
    Unit = {
      Description = "Daily 10:15am wake mode";
    };
    Timer = {
      OnCalendar = "10:15:00";
      Persistent = true;
    };
    Install = {
      WantedBy = [ "timers.target" ];
    };
  };

  # rig-control color gradient — updates chassis RGB every 60s based on the
  # time-of-day color schedule in rig-control-colors.conf. Skips when sleeping.
  systemd.user.services.rig-control-fade = lib.mkIf (!isLaptop) {
    Unit = {
      Description = "Update chassis RGB to time-of-day gradient color";
      After = [ "graphical-session.target" ];
    };
    Service = {
      Type = "oneshot";
      ExecStart = "${home}/workspace/devrc/scripts/rig-control-fade";
    };
  };
  systemd.user.timers.rig-control-fade = lib.mkIf (!isLaptop) {
    Unit = {
      Description = "Per-minute chassis RGB gradient update";
    };
    Timer = {
      OnBootSec = "60s";
      OnUnitActiveSec = "60s";
    };
    Install = {
      WantedBy = [ "timers.target" ];
    };
  };
}
