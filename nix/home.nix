{ config, pkgs, lib, ... }:

let
  userPackages = import ./pkgs {pkgs=pkgs;};
  isNixOS = builtins.pathExists /etc/NIXOS;
  home = config.home.homeDirectory;
  workspace = "${home}/workspace";
  # To enable nightly, also remove comment in neovim/default.nix
  #overlays = import ./overlays.nix;
  sessionVariables = import ./sessionVariables.nix {
    elixirLspPath = pkgs.vscode-extensions.elixir-lsp.vscode-elixir-ls;
    playwrightBrowsersPath = pkgs.playwright-driver.browsers;
  };
  programs = import ./programs {pkgs=pkgs; config=config;};
in
{
  programs = programs;

  # Espanso text expander service (X11/i3)
  services.espanso = {
    enable = true;
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
          { trigger = ":pv"; replace = "${workspace}/promptver "; label = "promptver path"; }
          { trigger = ":hlt"; replace = "${workspace}/homelab-talos "; label = "homelab-talos path"; search_terms = ["infra"]; }
          { trigger = ":gss"; replace = "${workspace}/go-static-site "; label = "go static site"; search_terms = ["repo"]; }
          { trigger = ":nixos"; replace = "/etc/nixos/configuration.nix"; label = "nixos config"; search_terms = ["nixos" "system"]; }

          # hot phrases
          { trigger = ":psg"; replace = "prometheus stack grafana "; label = "Prometheus stack grafana"; search_terms = ["monitoring" "metrics"]; }
          { trigger = ":gal"; replace = "grafana alloy "; label = "alloy"; search_terms = ["monitoring" "metrics"]; }
          { trigger = ":prov"; replace = "provisioned dashboards list "; label = "provisioned dashboards"; search_terms = ["monitoring" "metrics"]; }
          { trigger = ":mfc"; replace = "make the following changes:\n"; label = "make changes"; search_terms = ["workflow"]; }
          { trigger = ":itc"; replace = "implement the following changes:\n"; label = "implement changes"; search_terms = ["workflow"]; }

          # hot singles
          { trigger = "dashbaord"; replace = "dashboard"; }
          { trigger = ":su"; replace = "set"; label = "setup"; search_terms = ["setup"]; }

          # Workflows
          { trigger = ":cpr"; replace = "commit push reconcile flux verify"; label = "Commit push reconcile verify"; search_terms = ["flux" "deploy"]; }
          { trigger = ":tmt:"; replace = "use task-master MCP to create tasks, then implement and validate them"; label = "Task-master workflow"; search_terms = ["taskmaster" "workflow"]; }

          { trigger = ":cdp"; replace = "${workspace}/civit/datapacket-talos "; label = "civitai datapacket-talos path"; search_terms = ["civitai"]; }
          { trigger = ":cgf"; replace = "${workspace}/civit/civitai-gpu-fleet "; label = "civitai gpu-fleet path"; search_terms = ["civitai"]; }
          { trigger = ":cdo"; replace = "${workspace}/civit/civitai-deployment "; label = "civitai do deployment path"; search_terms = ["civitai"]; }
          { trigger = ":cpk"; replace = "${workspace}/civit/datapacket-talos/prod-kubeconfig "; label = "civitai dp prod kubeconfig path"; search_terms = ["civitai"]; }
          { trigger = ":cdk"; replace = "${home}/Downloads/civitai-kubeconfig.yaml "; label = "civitai do kubeconfig path"; search_terms = ["civitai"]; }

          # Word expansions
          { trigger = ":anal"; replace = "analyze "; }
          { trigger = ":analc"; replace = "analyze the client "; search_terms = ["client"]; }
          { trigger = ":gene"; replace = "generator "; search_terms = ["client"]; }
          { trigger = ":prop"; replace = "propose"; }
          { trigger = ":det"; replace = "determine "; }
          { trigger = ":gra"; replace = "grafana "; }

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
    enable = true;
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

  #nixpkgs.overlays = overlays;

  home.stateVersion = "24.11";

  home.packages = if isNixOS
  then
    userPackages ++ [pkgs.autorandr]
  else
    userPackages;

  home.sessionVariables = sessionVariables // {
    NODE_PATH = "${home}/.npm-packages/lib/node_modules";
  };

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
  home.file.".config/tmux/task-dashboard.sh" = {
    source = ../scripts/tmux-task-dashboard.sh;
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
}
