{ config, ... }:
{
  enable = true;
  autocd = true;
  dotDir = ".config/zsh";
  enableCompletion = true;
  initContent = let
    zshRc = builtins.readFile ../../../.zshrc;
    hasDevRc = builtins.pathExists "${config.home.homeDirectory}/.devrc";
    content = if hasDevRc then zshRc + builtins.readFile "${config.home.homeDirectory}/.devrc" else zshRc;
  in
    ''
    ${content}
    PROMPT='%{$fg_bold[white]%}%c%{$reset_color%} $(git_prompt_info)'

    ZSH_THEME_GIT_PROMPT_PREFIX="%{$fg[green]%}"
    ZSH_THEME_GIT_PROMPT_SUFFIX="%{$reset_color%} "
    ZSH_THEME_GIT_PROMPT_DIRTY="%{$fg[blue]%} %{$fg_bold[yellow]%}!"
    ZSH_THEME_GIT_PROMPT_CLEAN="%{$fg[blue]%}"
    '';

  oh-my-zsh = {
    enable = true;

    plugins = [
      "git"
    ];
  };

  shellAliases = {
    g = "git";
    d = "docker";
    n = "nvim";
    k = "kubectl";
  };

  history = {
    append = true;
    save = 15000;
    saveNoDups = true;
  };

}
