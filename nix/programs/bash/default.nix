{ config, ... }:
{
  enable = true;
  # Trampoline to zsh — consider setting zsh as login shell via
  # users.users.<name>.shell = pkgs.zsh in NixOS configuration instead
  initExtra = let
    cmd = ''
    . "$HOME/workspace/devrc/nix/bin/source-nix.sh"
    [ "$(command -v zsh)" ] && exec zsh
  '';
    hasDevBashRc = builtins.pathExists "${config.home.homeDirectory}/bashrc.devrc";
  in
    if hasDevBashRc then cmd + builtins.readFile "${config.home.homeDirectory}/bashrc.devrc" else cmd;
}
