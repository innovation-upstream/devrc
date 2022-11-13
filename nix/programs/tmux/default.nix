{ pkgs, ... }:
{
  enable = true;
  prefix = "C-a";
  keyMode = "vi";
  baseIndex = 1;
  extraConfig = builtins.readFile ../../../.tmux.conf;
  plugins = with pkgs.tmuxPlugins; [
    {
      plugin = dracula;
      extraConfig = ''
        set -g @dracula-plugins "cpu-usage ram-usage"
      '';
    }
    {
      plugin = resurrect;
      extraConfig = "set -g @resurrect-strategy-nvim 'session'";
    }
    {
      plugin = continuum;
      extraConfig = ''
        set -g @continuum-restore 'on'
        set -g @continuum-save-interval '5'
      '';
    }
  ];
}
