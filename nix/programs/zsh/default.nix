{ config, ... }:
{
  enable = true;
  autocd = true;
  dotDir = ".config/zsh";
  enableCompletion = true;
  initExtra = let
    zshRc = builtins.readFile ../../../.zshrc;
    hasDevRc = builtins.pathExists "${config.home.homeDirectory}/.devrc";
    content = if hasDevRc then zshRc + builtins.readFile "${config.home.homeDirectory}/.devrc" else zshRc;
  in
    ''
    PROMPT="%(?:%{$fg_bold[green]%}➜ :%{$fg_bold[red]%}➜ )"
    PROMPT+=' %{$fg[cyan]%}%c%{$reset_color%} $(git_prompt_info)'

    ZSH_THEME_GIT_PROMPT_PREFIX="%{$fg[green]%}"
    ZSH_THEME_GIT_PROMPT_SUFFIX="%{$reset_color%} "
    ZSH_THEME_GIT_PROMPT_DIRTY="%{$fg[blue]%} %{$fg[yellow]%}✗"
    ZSH_THEME_GIT_PROMPT_CLEAN="%{$fg[blue]%}"
    '';
  oh-my-zsh = {
    enable = true;

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
    gc = "git checkout $1";
  };
}
