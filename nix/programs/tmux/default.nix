{ pkgs, ... }:
{
  enable = true;
  prefix = "C-a";
  keyMode = "vi";
  baseIndex = 1;
  extraConfig = builtins.readFile ../../../.tmux.conf;
  plugins = with pkgs.tmuxPlugins; [
    resurrect
    {
      plugin = continuum;
      extraConfig = ''
        set -g @continuum-restore 'on'
        set -g @continuum-save-interval '5'
      '';
    }
    {
      plugin = tmux-fzf;
      extraConfig = ''
        # Rebind from F (default) to f
        unbind-key F
        bind-key f run-shell -b "${tmux-fzf}/share/tmux-plugins/tmux-fzf/main.sh"
      '';
    }
  ];
}
