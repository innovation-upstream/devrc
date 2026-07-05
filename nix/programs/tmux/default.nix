{ pkgs, ... }:
let
  inherit (pkgs) lib;
  # Generate the 12 scratchpad popup toggles from the canonical slot table
  # (scripts/tmux-scratch-slots.sh) instead of hardcoding them — with their
  # per-slot color + codename — in .tmux.conf. One source of truth: the same file
  # the tmux HUDs source and initiative-scan.py parses. Add/rename a scratchpad by
  # editing the slot table only.
  slotsText = builtins.readFile ../../../scripts/tmux-scratch-slots.sh;
  # Slot entries look like:  "scratch4:V:#83a598:Vapor"  (session:key:color:name).
  slotRe = "[[:space:]]*\"([^\":]+):([^\":]+):(#[0-9a-fA-F]+):([^\"]+)\"[[:space:]]*";
  slotLines = builtins.filter (l: builtins.match slotRe l != null)
    (lib.splitString "\n" slotsText);
  parse = l: let m = builtins.match slotRe l; in {
    sess = builtins.elemAt m 0;
    key = builtins.elemAt m 1;
    color = builtins.elemAt m 2;
    name = builtins.elemAt m 3;
  };
  # Byte-identical (per tmux's normalization) to the former hand-written bindings —
  # verified via a `tmux list-keys` diff before the cutover.
  mkBind = s: "bind -n M-${s.key} if-shell -F '#{==:#{session_name},${s.sess}}'"
    + " { detach-client }"
    + " { display-popup -d \"#{pane_current_path}\" -xC -yC -w 80% -h 80%"
    + " -S 'fg=${s.color}' -T ' ${s.name} '"
    + " -E 'tmux attach-session -t ${s.sess} || tmux new-session -s ${s.sess}' }";
  scratchBindings = builtins.concatStringsSep "\n" (map (s: mkBind (parse s)) slotLines);
in
{
  enable = true;
  prefix = "C-a";
  keyMode = "vi";
  baseIndex = 1;
  extraConfig = builtins.readFile ../../../.tmux.conf
    + "\n# --- generated scratchpad popup toggles (see nix/programs/tmux/default.nix) ---\n"
    + scratchBindings + "\n";
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
