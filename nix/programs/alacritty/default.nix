{}:
{
  enable = true;

  settings = {
    # Bell sound handled by tmux hook (set-hook alert-bell) to avoid double notification
    bell = {
      duration = 0;
    };

    keyboard.bindings = [
      { key = "Back"; mods = "Control"; chars = "\\u0017"; }       # Delete word
      { key = "Back"; mods = "Control|Shift"; chars = "\\u0015"; } # Delete to line start
      { key = "Left"; mods = "Control"; chars = "\\u001bb"; }      # Word back
      { key = "Right"; mods = "Control"; chars = "\\u001bf"; }     # Word forward
    ];
  };
}
