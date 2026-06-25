{ config, pkgs, lib, isNixOS ? false, ... }:

let
  home = config.home.homeDirectory;
  workspace = "${home}/workspace";
  # Headless/server mode: `touch ~/.server-mode` to disable graphical-session
  # services (dunst, espanso) that can't start without X/i3 and otherwise make
  # every `home-manager switch` report "degraded / Failed services".
  serverMode = builtins.pathExists "${home}/.server-mode";
  userPackages = import ./pkgs { inherit pkgs workspace; };
  sessionVariables = import ./sessionVariables.nix {
    inherit pkgs;
    elixirLspPath = pkgs.vscode-extensions.elixir-lsp.vscode-elixir-ls;
    playwrightBrowsersPath = pkgs.playwright-driver.browsers;
    homePath = home;
  };
  programs = import ./programs { inherit pkgs config; };
in
{
  programs = programs;

  # Espanso text expander service (X11/i3)
  services.espanso = {
    enable = !serverMode;
    package = pkgs.espanso;
    x11Support = true;
    waylandSupport = false;

    configs = {
      default = {
        # ALT+SPACE conflicts with i3 focus mode_toggle, use CTRL instead
        search_shortcut = "CTRL+SPACE";
        backend = "Clipboard";
      };
    };

    matches = {
      base = {
        matches = [
          # Date/time with labels for search bar (ALT+SPACE)
          { trigger = ":date"; replace = "{{mydate}}"; label = "Today's date"; search_terms = ["today" "calendar"]; vars = [{ name = "mydate"; type = "date"; params = { format = "%Y-%m-%d"; }; }]; }
          { trigger = ":time"; replace = "{{mytime}}"; label = "Current time"; search_terms = ["now" "clock"]; vars = [{ name = "mytime"; type = "date"; params = { format = "%H:%M"; }; }]; }
          { trigger = ":datetime"; replace = "{{mydt}}"; label = "Date and time"; search_terms = ["timestamp"]; vars = [{ name = "mydt"; type = "date"; params = { format = "%Y-%m-%d %H:%M"; }; }]; }
          { trigger = ":iso"; replace = "{{myiso}}"; label = "ISO 8601 timestamp"; search_terms = ["utc" "rfc"]; vars = [{ name = "myiso"; type = "date"; params = { format = "%Y-%m-%dT%H:%M:%S%z"; }; }]; }

          # Paths - labeled for autocomplete
          { trigger = ":hlt"; replace = "${workspace}/homelab-talos "; label = "homelab-talos path"; search_terms = ["infra"]; }
          { trigger = ":kuc"; replace = "${workspace}/kubeclaw "; label = "kubeclaw path"; search_terms = ["kubeclaw"]; }
          { trigger = ":nixos"; replace = "/etc/nixos/configuration.nix"; label = "nixos config"; search_terms = ["nixos" "configuration"]; }

          # hot singles
          { trigger = "dashbaord"; replace = "dashboard"; }
          { trigger = "reocmmend"; replace = "recommend"; }

          # Workflows
          { trigger = ":whn"; replace = "write the handoff to continue in next session"; label = "Write handoff for next session"; search_terms = ["handoff" "session"]; }
          { trigger = ":rau"; replace = "dispatch a subagent to adversarially audit the PR for bugs, regressions, edge cases, race conditions, security holes, error/failure handling, backward-compat and data-integrity risks, leaked secrets, hidden assumptions, and second-order consequences — cite evidence from the diff, rank findings by severity, and propose a fix for each"; label = "PR audit checklist tail"; search_terms = ["review" "audit" "pr" "risks" "regressions" "subagent"]; }
          { trigger = ":rns"; replace = "recommend next steps"; label = "Recommend next steps"; search_terms = ["next" "recommend" "steps" "whats next"]; }
          { trigger = ":rnx"; replace = "recommend next steps ranked by leverage (impact vs effort); call out the single highest-value move and any quick wins"; label = "Recommend next steps, ranked by leverage"; search_terms = ["next" "recommend" "steps" "ranked" "leverage" "impact" "deep"]; }
          { trigger = ":pst"; replace = "proceed, use a subagent, ensure test coverage, and verify it actually works"; label = "Proceed with subagent + test coverage + verify"; search_terms = ["proceed" "subagent" "test" "coverage" "verify" "dispatch" "yes"]; }
          { trigger = ":kickoff"; replace = "give me the kickoff message to copy paste to next session"; label = "Kickoff message for next session"; search_terms = ["kickoff" "kick off" "next session" "copy paste" "handoff" "message"]; }
          { trigger = ":nday"; replace = "it's the next day — re-check live state before acting: CI/pipelines, rollouts, new PR comments, anything that moved; don't trust stale assumptions"; label = "Next-day re-check"; search_terms = ["next day" "check" "days" "resume" "morning"]; }
          { trigger = ":fhrs"; replace = "it's been a few hours — re-check live state before acting: CI/pipelines, rollouts, new PR comments, anything that moved; don't trust stale assumptions"; label = "Few-hours re-check"; search_terms = ["hours" "check" "elapsed" "resume"]; }
          { trigger = ":fdays"; replace = "it's been a few days — re-check live state before acting: CI/pipelines, rollouts, new PR comments, anything that moved; don't trust stale assumptions"; label = "Few-days re-check"; search_terms = ["days" "check" "elapsed" "resume"]; }
          { trigger = ":mdc"; replace = "merged and deployed — now verify it actually works: check live state, reproduce the original behaviour end-to-end, confirm the symptom is gone, report honestly (deployed ≠ verified)"; label = "Merged & deployed — verify"; search_terms = ["merged" "deployed" "check" "verify"]; }
          { trigger = ":wn"; replace = "what's next"; label = "What's next"; search_terms = ["next" "whats next" "what next"]; }
          { trigger = ":cont"; replace = "continue from where you left off — re-verify current state first, it may have changed"; label = "Continue from where you left off"; search_terms = ["continue" "resume" "left off"]; }
          { trigger = ":pec"; replace = "push an empty commit"; label = "Push an empty commit"; search_terms = ["push" "empty" "commit" "trigger" "ci"]; }
          { trigger = ":aep"; replace = "dispatch subagents to adversarially audit each PR (one per PR) for bugs, regressions, edge cases, race conditions, security holes, error/failure handling, backward-compat and data-integrity risks, leaked secrets, hidden assumptions, and second-order consequences — cite evidence from the diff, rank findings by severity, and propose a fix for each"; label = "Audit each PR (one subagent per PR)"; search_terms = ["audit" "each" "prs" "subagents" "risks" "regressions" "review"]; }

          { trigger = ":cc"; replace = "${workspace}/civit/civitai "; label = "civitai main repo path"; search_terms = ["civitai" "repo" "web"]; }
          { trigger = ":cdp"; replace = "${workspace}/civit/datapacket-talos "; label = "civitai datapacket-talos path"; search_terms = ["civitai"]; }
          { trigger = ":cgf"; replace = "${workspace}/civit/civitai-gpu-fleet "; label = "civitai gpu-fleet path"; search_terms = ["civitai"]; }
          { trigger = ":cmo"; replace = "${workspace}/civit/civitai-orchestration "; label = "civitai-orchestration path"; search_terms = ["civitai" "orchestration"]; }
          { trigger = ":csc"; replace = "${workspace}/civit/civitai-spine-controller "; label = "civitai-spine-controller path"; search_terms = ["civitai" "spine controller" "spine"]; }
          { trigger = ":cpk"; replace = "${workspace}/civit/datapacket-talos/prod-kubeconfig "; label = "civitai dp prod kubeconfig path"; search_terms = ["civitai"]; }
          { trigger = ":subk"; replace = "${workspace}/civit/civitai-gpu-fleet/submodel-dc-03-a-kubeconfig "; label = "civitai submodel dc 03 kubeconfig path"; search_terms = ["civitai" "gpu" "submodel" "dc 03"]; }

          # Utilities
          { trigger = ":uuid"; replace = "{{uuid}}"; label = "Generate UUID"; search_terms = ["guid" "random"]; vars = [{ name = "uuid"; type = "shell"; params = { cmd = "uuidgen"; }; }]; }
          { trigger = ":clip"; replace = "{{clip}}"; label = "Paste from clipboard"; vars = [{ name = "clip"; type = "clipboard"; }]; }
        ];
      };
    };
  };

  # Compositor disabled — NVIDIA forceFullCompositionPipeline handles vsync/tearing
  # picom conflicts with NVIDIA's composition pipeline causing workspace switch flicker
  # services.picom = {
  #   enable = true;
  #   backend = "glx";
  #   vSync = true;
  # };

  # Notification daemon (Gruvbox-themed)
  services.dunst = {
    enable = !serverMode;
    settings = {
      global = {
        font = "Monospace 10";
        frame_color = "#504945";
        separator_color = "frame";
        corner_radius = 4;
      };
      urgency_low = {
        background = "#282828";
        foreground = "#ebdbb2";
        timeout = 5;
      };
      urgency_normal = {
        background = "#282828";
        foreground = "#ebdbb2";
        frame_color = "#83a598";
        timeout = 10;
      };
      urgency_critical = {
        background = "#cc241d";
        foreground = "#ebdbb2";
        timeout = 0;
      };
    };
  };

  # Workaround: ensure espanso config directory exists
  home.activation.espansoConfigDir = lib.hm.dag.entryAfter ["writeBoundary"] ''
    mkdir -p ~/.config/espanso/config
  '';

  # Seed the activity-collector EnvironmentFile with safe defaults if it does not
  # exist yet. The real file holds the (future) ClickHouse credentials, so it is
  # NEVER in the nix store and NEVER committed — created here once, chmod 600,
  # then edited in place. We copy the in-repo .env.example as the template.
  home.activation.activityCollectorEnv = lib.hm.dag.entryAfter ["writeBoundary"] ''
    envFile="$HOME/.config/activity-collector/env"
    if [ ! -e "$envFile" ]; then
      mkdir -p "$HOME/.config/activity-collector"
      cp ${../scripts/collector/.env.example} "$envFile"
      chmod 600 "$envFile"
      echo "activity-collector: seeded $envFile from .env.example (edit to add CLICKHOUSE_PASSWORD)"
    fi
  '';

  home.stateVersion = "24.11";

  home.packages = if isNixOS
  then
    userPackages ++ [pkgs.autorandr]
  else
    userPackages;

  home.sessionVariables = sessionVariables;

  home.sessionPath = [
    "${home}/go/bin"
    "${home}/.npm-packages/bin"
  ];

  # Symlink tmux scripts
  home.file.".config/tmux/idle-update.sh" = {
    source = ../scripts/tmux-idle-update.sh;
    executable = true;
  };
  home.file.".config/tmux/pipe-activity.sh" = {
    source = ../scripts/tmux-pipe-activity.sh;
    executable = true;
  };
  home.file.".config/tmux/activity-receiver.sh" = {
    source = ../scripts/tmux-activity-receiver.sh;
    executable = true;
  };
  home.file.".config/tmux/task-hook.sh" = {
    source = ../scripts/tmux-task-hook.sh;
    executable = true;
  };
  home.file.".config/tmux/task-resume.sh" = {
    source = ../scripts/tmux-task-resume.sh;
    executable = true;
  };
  home.file.".config/tmux/scratch-picker.sh" = {
    source = ../scripts/tmux-scratch-picker.sh;
    executable = true;
  };
  home.file.".config/tmux/scratch-status.sh" = {
    source = ../scripts/tmux-scratch-status.sh;
    executable = true;
  };
  home.file.".config/tmux/scratch-monitor.sh" = {
    source = ../scripts/tmux-scratch-monitor.sh;
    executable = true;
  };
  home.file.".config/tmux/claude-counters.sh" = {
    source = ../scripts/tmux-claude-counters.sh;
    executable = true;
  };
  home.file.".config/tmux/initiatives.sh" = {
    source = ../scripts/tmux-initiatives.sh;
    executable = true;
  };

  home.file.".config/tmux/activity-emit.sh" = {
    source = ../scripts/tmux-activity-emit.sh;
    executable = true;
  };

  # CPU load monitor: desktop alert on sustained high load
  home.file.".config/cpu-monitor/cpu-monitor.sh" = {
    source = ../scripts/cpu-monitor.sh;
    executable = true;
  };

  # Activity-telemetry collector: hot-path emit helper + daemon. Symlinked from
  # the repo so both hosts stay in sync. Config (CLICKHOUSE_URL/credentials) lives
  # in ~/.config/activity-collector/env — created below, NOT in the nix store.
  home.file.".config/activity-collector/emit" = {
    source = ../scripts/collector/emit;
    executable = true;
  };
  home.file.".config/activity-collector/collector.py" = {
    source = ../scripts/collector/collector.py;
    executable = true;
  };

  # Claude Code activity source (5th source): a periodic tailer that scans the
  # ~/.claude transcripts and emits NEW user-typed messages / slash-commands as
  # source=claude events via the shared emit helper. Symlinked recursively so the
  # tailer lands at ~/.config/activity-collector/claude/tailer.py and resolves its
  # sibling emit at ~/.config/activity-collector/emit (two dirs up). Driven by a
  # systemd user TIMER (below), not Restart=always — it is a periodic oneshot.
  home.file.".config/activity-collector/claude" = {
    source = ../scripts/collector/claude;
    recursive = true;
  };

  # GUI activity collectors (keylogger + browser receiver). The whole module
  # dir is symlinked recursively so the daemons can import their sibling modules
  # (keymap/chunker/winctx/spool_emit). The browser receiver reuses keylog's
  # spool_emit (single source of truth for the v1 line format), so keylog/ must
  # be present even on a browser-only host.
  home.file.".config/activity-collector/keylog" = {
    source = ../scripts/collector/keylog;
    recursive = true;
  };
  home.file.".config/activity-collector/browser-ext" = {
    source = ../scripts/collector/browser-ext;
    recursive = true;
  };
  # i3 focus collector. Reuses keylog's spool_emit (the v1 line format), so
  # keylog/ must be present alongside it (it always is — shipped above).
  home.file.".config/activity-collector/i3" = {
    source = ../scripts/collector/i3;
    recursive = true;
  };

  # Global Claude Code behavioural config — single source of truth for both
  # hosts (these were drifting when edited per-host). Synced via scripts/ship.sh.
  # NOTE: now read-only symlinks into the nix store → edit `devrc/claude/*.md`
  # then `home-manager switch` (or ship.sh), NOT `~/.claude/*.md` directly.
  # CLAUDE.md and skills/ stay per-host/mutable (host-specific + frequently edited).
  home.file.".claude/RULES.md" = {
    source = ../claude/RULES.md;
    force = true;  # overwrite the pre-existing unmanaged file on first switch
  };
  home.file.".claude/PRINCIPLES.md" = {
    source = ../claude/PRINCIPLES.md;
    force = true;
  };

  systemd.user.services.cpu-monitor = {
    Unit = {
      Description = "Desktop alert on sustained high CPU load";
      After = [ "graphical-session.target" ];
    };
    Service = {
      # PATH must be explicit: a user service does not inherit the login shell PATH.
      Environment = [
        "PATH=${lib.makeBinPath [ pkgs.coreutils pkgs.gawk pkgs.procps pkgs.gnugrep pkgs.libnotify ]}"
        # This laptop runs hot at idle (cooling needs attention); warn early.
        "CPU_MON_TEMP_THRESHOLD=88"
      ];
      ExecStart = "${pkgs.bash}/bin/bash %h/.config/cpu-monitor/cpu-monitor.sh";
      Restart = "always";
      RestartSec = 10;
    };
    Install = {
      # default.target = starts on login. i3 is not systemd-integrated, so the
      # script borrows DISPLAY/DBUS from i3's /proc environ to reach dunst.
      WantedBy = [ "default.target" ];
    };
  };

  # Activity-telemetry collector daemon. Batches spooled events and ships them to
  # ClickHouse. Mirrors the cpu-monitor user-service pattern (Restart=always,
  # explicit PATH via lib.makeBinPath). Config comes from the EnvironmentFile
  # (not the nix store); EnvironmentFile is optional so a missing file (e.g. mid
  # first switch, before activation seeds it) does not fail the unit.
  systemd.user.services.activity-collector = {
    Unit = {
      Description = "Personal activity-telemetry collector → ClickHouse";
      # No graphical-session dep: this must run in headless/server mode too.
      After = [ "network.target" ];
    };
    Service = {
      Type = "simple";
      # PATH must be explicit: a user service does not inherit the login PATH.
      # python3 (with stdlib only) + base64/coreutils for the helper path.
      Environment = [
        "PATH=${lib.makeBinPath [ pkgs.python312 pkgs.coreutils pkgs.bash ]}"
      ];
      EnvironmentFile = "-%h/.config/activity-collector/env";
      ExecStart = "${pkgs.python312}/bin/python3 %h/.config/activity-collector/collector.py";
      Restart = "always";
      RestartSec = 10;
      # Restart on a script-only change. sd-switch only restarts a unit when the
      # unit definition itself changes; the script is symlinked-by-path, so a
      # code edit alone leaves the daemon running STALE code until a manual
      # `systemctl --user restart`. Pinning the script's store path here makes the
      # unit definition change whenever the code changes → switch restarts it.
      X-Restart-Triggers = [ "${../scripts/collector/collector.py}" ];
    };
    Install = {
      WantedBy = [ "default.target" ];
    };
  };

  # Claude Code activity source — periodic oneshot tailer. Type=oneshot (runs the
  # scan once and exits) driven by the timer below, NOT Restart=always. Stdlib-only
  # python + the emit helper's bash/coreutils on PATH. No graphical/network dep —
  # it only reads local transcripts and appends to the local spool, so it runs in
  # headless/server mode too. Host is stamped by the collector daemon (ACTIVITY_HOST).
  systemd.user.services.claude-activity-source = {
    Unit = {
      Description = "Tail Claude Code transcripts → activity spool (source=claude)";
    };
    Service = {
      Type = "oneshot";
      Environment = [
        "PATH=${lib.makeBinPath [ pkgs.python312 pkgs.coreutils pkgs.bash ]}"
      ];
      ExecStart = "${pkgs.python312}/bin/python3 %h/.config/activity-collector/claude/tailer.py";
    };
  };

  # Timer: fire the tailer ~every 5 min. OnUnitActiveSec re-arms relative to the
  # last run, so a slow scan never overlaps itself. OnStartupSec gives one prompt
  # run shortly after login. Persistent catches up a single missed run after sleep.
  systemd.user.timers.claude-activity-source = {
    Unit = {
      Description = "Periodic timer for the Claude Code activity source";
    };
    Timer = {
      OnStartupSec = "1min";
      OnUnitActiveSec = "5min";
      Persistent = true;
    };
    Install = {
      WantedBy = [ "timers.target" ];
    };
  };

  # X11 full-content keystroke collector. Captures globally via the RECORD
  # extension (python-xlib) as the logged-in user — needs the X session, so it
  # is gated on graphical-session.target (NOT started in headless/server mode).
  # Writes typing units into the same spool the activity-collector ships.
  # NOTE: staged but NOT enabled here; enablement is a deliberate converge step.
  systemd.user.services.keylog = {
    Unit = {
      Description = "X11 full-content keystroke collector → activity spool";
      # Requires a live X session (RECORD + active-window context).
      After = [ "graphical-session.target" ];
      PartOf = [ "graphical-session.target" ];
    };
    Service = {
      Type = "simple";
      # python3 WITH python-xlib (the X RECORD plumbing). DISPLAY is borrowed
      # from the running session; i3 is not systemd-integrated, so import the
      # graphical env if available.
      Environment = [
        "PATH=${lib.makeBinPath [ (pkgs.python312.withPackages (ps: [ ps.xlib ])) pkgs.coreutils ]}"
      ];
      ExecStart = "${pkgs.python312.withPackages (ps: [ ps.xlib ])}/bin/python3 %h/.config/activity-collector/keylog/keylog.py";
      Restart = "always";
      RestartSec = 10;
      # Restart on a script-only change (see activity-collector for rationale).
      X-Restart-Triggers = [ "${../scripts/collector/keylog}" ];
    };
    Install = {
      WantedBy = [ "graphical-session.target" ];
    };
  };

  # i3 focus collector. Subscribes to i3's IPC event stream and emits a
  # source=i3 record on every window-focus / workspace-focus change — capturing
  # attention even when the user is only READING (the keylogger only records
  # focus context WHEN typing). Needs a live i3 IPC socket, so it is gated on
  # graphical-session.target (laptop-only, NOT started in headless/server mode),
  # exactly like keylog. Writes into the same spool the activity-collector ships,
  # reusing keylog's spool_emit (single source of truth for the v1 line format).
  systemd.user.services.i3-source = {
    Unit = {
      Description = "i3 window/workspace focus collector → activity spool";
      # Requires a live i3 (IPC socket); tracks the graphical session.
      After = [ "graphical-session.target" ];
      PartOf = [ "graphical-session.target" ];
    };
    Service = {
      Type = "simple";
      # python3 WITH i3ipc (the IPC client). WM_CLASS/title come straight from
      # the i3ipc container, so no Xlib is needed. I3SOCK is auto-discovered by
      # i3ipc from the running session.
      Environment = [
        "PATH=${lib.makeBinPath [ (pkgs.python312.withPackages (ps: [ ps.i3ipc ])) pkgs.coreutils ]}"
      ];
      ExecStart = "${pkgs.python312.withPackages (ps: [ ps.i3ipc ])}/bin/python3 %h/.config/activity-collector/i3/i3source.py";
      Restart = "always";
      RestartSec = 10;
      # Restart on a script-only change (see activity-collector for rationale).
      # Tracks i3/ AND keylog/, because i3source reuses keylog's spool_emit
      # (single source of truth for the v1 line format).
      X-Restart-Triggers = [ "${../scripts/collector/i3}" "${../scripts/collector/keylog}" ];
    };
    Install = {
      WantedBy = [ "graphical-session.target" ];
    };
  };

  # Browser-activity receiver: localhost HTTP bridge that the MV3 extension
  # POSTs to; writes browser nav/focus events into the activity spool. Loopback
  # only, stdlib-only python. No X dependency (the extension lives in the
  # browser), but it is only useful alongside a running browser, so it tracks
  # default.target like the collector. Staged but NOT enabled.
  systemd.user.services.browser-activity-receiver = {
    Unit = {
      Description = "Browser-activity receiver (localhost → activity spool)";
      After = [ "network.target" ];
    };
    Service = {
      Type = "simple";
      Environment = [
        "PATH=${lib.makeBinPath [ pkgs.python312 pkgs.coreutils ]}"
        # Bind loopback only; keep off any external interface.
        "BROWSER_RECEIVER_HOST=127.0.0.1"
        "BROWSER_RECEIVER_PORT=8787"
      ];
      ExecStart = "${pkgs.python312}/bin/python3 %h/.config/activity-collector/browser-ext/receiver.py";
      Restart = "always";
      RestartSec = 10;
      # Restart on a script-only change (see activity-collector for rationale).
      # Tracks browser-ext AND keylog, because the receiver reuses keylog's
      # spool_emit (single source of truth for the v1 line format).
      X-Restart-Triggers = [ "${../scripts/collector/browser-ext}" "${../scripts/collector/keylog}" ];
    };
    Install = {
      WantedBy = [ "default.target" ];
    };
  };

  # Laptop-only SOCKS5 tunnel to the homelab kube API via the workbench. The
  # homelab API server (192.168.50.94:6443) is LAN-only and the laptop is
  # nebula-only, so it cannot reach the API directly. This holds an `ssh -D`
  # SOCKS proxy on 127.0.0.1:1080 through the workbench (nebula 10.42.0.30, which
  # IS on the LAN). The kubeconfig ~/.kube/homelab-nebula.yaml points at the real
  # API with `proxy-url: socks5://127.0.0.1:1080` — server stays 192.168.50.94 so
  # its TLS cert still verifies. Gated on graphical-session.target → starts on the
  # laptop only (the headless workbench reaches the API directly and never starts
  # it, like keylog). The kubeconfig is placed out-of-band (chmod 600, holds admin
  # creds — deliberately NOT in the world-readable nix store).
  systemd.user.services.homelab-kube-tunnel = {
    Unit = {
      Description = "SOCKS5 tunnel to the homelab kube API via the workbench (nebula)";
      After = [ "graphical-session.target" "network-online.target" ];
      PartOf = [ "graphical-session.target" ];
    };
    Service = {
      Type = "simple";
      Environment = [ "PATH=${lib.makeBinPath [ pkgs.openssh ]}" ];
      # -N: no remote command; -D: dynamic SOCKS on loopback. Keepalives let
      # systemd notice a dead link and Restart it.
      ExecStart = "${pkgs.openssh}/bin/ssh -N -D 127.0.0.1:1080 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new -o BatchMode=yes zach@10.42.0.30";
      Restart = "always";
      RestartSec = 10;
    };
    Install = {
      WantedBy = [ "graphical-session.target" ];
    };
  };
}
