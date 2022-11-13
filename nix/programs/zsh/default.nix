{ config, ... }:
{
  enable = true;
  autocd = true;
  dotDir = ".config/zsh";
  enableCompletion = true;
  initExtra = let
    zshRc = builtins.readFile ../../../.zshrc;
    hasDevRc = builtins.pathExists "${config.home.homeDirectory}/.devrc";
  in
    if hasDevRc then zshRc + builtins.readFile "${config.home.homeDirectory}/.devrc" else zshRc;
  oh-my-zsh = {
    enable = true;

    theme = "robbyrussell";
    plugins = [
      "git"
    ];
  };
  shellAliases = {
    k = "kubectl";
    g = "git";
    d = "docker";
    dc = "docker-compose";
    b = "bazel";
    ba = "cat /sys/class/power_supply/BAT1/capacity";
  };
}
