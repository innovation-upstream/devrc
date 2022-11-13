{ config, ... }:
{
  enable = true;
  initExtra = let 
    cmd = ''
    . "$HOME/workspace/devrc/nix/bin/source-nix.sh"
    [ "$(command -v zsh)" ] && zsh
  '';
    hasDevBashRc = builtins.pathExists "${config.home.homeDirectory}/bashrc.devrc";
  in
    if hasDevBashRc then cmd + builtins.readFile "${config.home.homeDirectory}/bashrc.devrc" else cmd;
}
