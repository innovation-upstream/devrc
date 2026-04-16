{}:
{
  enable = true;

  settings = {
    # Bell sound handled by tmux hook (set-hook alert-bell) to avoid double notification
    bell = {
      duration = 0;
    };

    # Gruvbox Dark theme
    colors = {
      primary = {
        background = "#282828";
        foreground = "#ebdbb2";
      };
      normal = {
        black = "#282828";
        red = "#cc241d";
        green = "#98971a";
        yellow = "#d79921";
        blue = "#458588";
        magenta = "#b16286";
        cyan = "#689d6a";
        white = "#a89984";
      };
      bright = {
        black = "#928374";
        red = "#fb4934";
        green = "#b8bb26";
        yellow = "#fabd2f";
        blue = "#83a598";
        magenta = "#d3869b";
        cyan = "#8ec07c";
        white = "#ebdbb2";
      };
    };

    keyboard.bindings = [
      { key = "Back"; mods = "Control"; chars = "\\u0017"; }       # Delete word
      { key = "Back"; mods = "Control|Shift"; chars = "\\u0015"; } # Delete to line start
      { key = "Left"; mods = "Control"; chars = "\\u001bb"; }      # Word back
      { key = "Right"; mods = "Control"; chars = "\\u001bf"; }     # Word forward
    ];
  };
}
