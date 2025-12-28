{}:
{
  enable = true;

  settings = {
    bell = {
      command = {
        program = "paplay";
        args = ["/run/current-system/sw/share/sounds/freedesktop/stereo/message.oga"];
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
