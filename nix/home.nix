{ config, pkgs, lib, ... }:

let
  userPackages = import ./pkgs {pkgs=pkgs;};
  isNixOS = builtins.pathExists /etc/NIXOS;
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
          { trigger = ":pv"; replace = "/home/zach/workspace/promptver "; label = "promptver path"; }
          { trigger = ":hlt"; replace = "/home/zach/workspace/homelab-talos "; label = "homelab-talos path"; search_terms = ["infra"]; }
          { trigger = ":gss"; replace = "/home/zach/workspace/go-static-site "; label = "go static site"; search_terms = ["repo" ]; }
          { trigger = ":nixos"; replace = "/etc/nixos/configuration.nix"; label = "nixos config"; search_terms = ["nixos" "system"]; }

          # hot phrases
          { trigger = ":psg"; replace = "prometheus stack grafana "; label = "Prometheus stack grafana"; search_terms = ["monitoring" "metrics"]; }
          { trigger = ":gal"; replace = "grafana alloy "; label = "alloy"; search_terms = ["monitoring" "metrics"]; }
          { trigger = ":prov"; replace = "provisioned dashboards list "; label = "provisioned dashboards "; search_terms = ["monitoring" "metrics"]; }
          { trigger = ":mfc"; replace = "make the following changes:\n"; label = "make changes"; search_terms = ["workflow" ]; }
          { trigger = ":mtfc"; replace = "make the following changes:\n"; label = "make changes"; search_terms = ["workflow" ]; }
          { trigger = ":itfc"; replace = "implement the following changes:\n"; label = "implement changes"; search_terms = ["workflow" ]; }
          { trigger = ":itc"; replace = "implement the following changes:\n"; label = "implement changes"; search_terms = ["workflow" ]; }

          # hot singles
          { trigger = "dashbaord"; replace = "dashboard"; }
          { trigger = ":su"; replace = "set"; label = "setup"; search_terms = ["setup"]; }

          # Workflows
          { trigger = ":cpr"; replace = "commit push reconcile flux verify"; label = "Comit push reconcile verify"; search_terms = ["flux" "deploy"]; }
          { trigger = ":tmt:"; replace = "use task-master MCP to create tasks, then implement and validate them"; label = "Comit push reconcile verify"; search_terms = ["flux" "deploy"]; }

          { trigger = ":cdp"; replace = "/home/zach/workspace/civit/datapacket-talos "; label = "civitai datapacket-talos path"; search_terms = ["civitai"]; }
          { trigger = ":cgf"; replace = "/home/zach/workspace/civit/civitai-gpu-fleet "; label = "civitai gpu-fleet path"; search_terms = ["civitai"]; }
          { trigger = ":cdo"; replace = "/home/zach/workspace/civit/civitai-deployment "; label = "civitai do deployment path"; search_terms = ["civitai"]; }
          { trigger = ":cpk"; replace = "/home/zach/workspace/civit/datapacket-talos/prod-kubeconfig "; label = "civitai dp prod kubeconfig path"; search_terms = ["civitai"]; }
          { trigger = ":cdk"; replace = "/home/zach/Downloads/civitai-kubeconfig.yaml "; label = "civitai do kubeconfig path"; search_terms = ["civitai"]; }

          # Word expansions
          { trigger = ":anal"; replace = "analyze "; }
          { trigger = ":analc"; replace = "analyze the client "; search_terms = ["client" ]; }
          { trigger = ":gene"; replace = "generator "; search_terms = ["client" ]; }
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

  home.sessionVariables = sessionVariables;

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
}
