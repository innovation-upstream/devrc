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
          { trigger = ":rau"; replace = "dispatch a subagent to audit the PR for risks, regressions, assumptions, gaps, edge cases, bugs, issues, behaviour changes, leaks, second-order consequences"; label = "PR audit checklist tail"; search_terms = ["review" "audit" "pr" "risks" "regressions" "subagent"]; }
          { trigger = ":rns"; replace = "recommend next steps"; label = "Recommend next steps"; search_terms = ["next" "recommend" "steps" "whats next"]; }
          { trigger = ":pst"; replace = "proceed, use subagent, ensure test coverage"; label = "Proceed with subagent + test coverage"; search_terms = ["proceed" "subagent" "test" "coverage" "dispatch" "yes"]; }
          { trigger = ":kickoff"; replace = "give me the kickoff message to copy paste to next session"; label = "Kickoff message for next session"; search_terms = ["kickoff" "kick off" "next session" "copy paste" "handoff" "message"]; }
          { trigger = ":nday"; replace = "it's the next day, check"; label = "Next-day check-in"; search_terms = ["next day" "check" "days" "resume" "morning"]; }
          { trigger = ":fhrs"; replace = "it's been a few hours, check"; label = "Few-hours check-in"; search_terms = ["hours" "check" "elapsed" "resume"]; }
          { trigger = ":fdays"; replace = "it's been a few days, check"; label = "Few-days check-in"; search_terms = ["days" "check" "elapsed" "resume"]; }
          { trigger = ":mdc"; replace = "merged and deployed, check"; label = "Merged and deployed, check"; search_terms = ["merged" "deployed" "check" "verify"]; }
          { trigger = ":wn"; replace = "what's next"; label = "What's next"; search_terms = ["next" "whats next" "what next"]; }
          { trigger = ":cont"; replace = "continue from where you left off."; label = "Continue from where you left off"; search_terms = ["continue" "resume" "left off"]; }
          { trigger = ":pec"; replace = "push an empty commit"; label = "Push an empty commit"; search_terms = ["push" "empty" "commit" "trigger" "ci"]; }
          { trigger = ":aep"; replace = "dispatch subagents to audit each PR for risks, regressions, assumptions, gaps, edge cases, bugs, issues, behaviour changes, leaks, second-order consequences"; label = "Audit each PR (one subagent per PR)"; search_terms = ["audit" "each" "prs" "subagents" "risks" "regressions" "review"]; }

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

  # CPU load monitor: desktop alert on sustained high load
  home.file.".config/cpu-monitor/cpu-monitor.sh" = {
    source = ../scripts/cpu-monitor.sh;
    executable = true;
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
}
