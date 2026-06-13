{ config, ... }:
{
  enable = true;
  autocd = true;
  dotDir = config.home.homeDirectory + "/.config/zsh";
  enableCompletion = true;
  initContent = let
    zshRc = builtins.readFile ../../../.zshrc;
    hasDevRc = builtins.pathExists "${config.home.homeDirectory}/.devrc";
    content = if hasDevRc then zshRc + builtins.readFile "${config.home.homeDirectory}/.devrc" else zshRc;
  in
    ''
    ${content}

    # Pass unmatched globs through literally instead of aborting the command
    # (bash default behavior). Without this, zsh fails commands like
    # `grep --include=*.go` or `ls /tmp/foo* 2>/dev/null` with "no matches found".
    unsetopt nomatch

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
    nemo = "GTK_THEME=Adwaita-dark nemo";

    # Toggle headless vs graphical mode (see ~/.server-mode marker in home.nix).
    # graphical-mode re-enables dunst/espanso; headless-mode disables them.
    graphical-mode = "rm -f ~/.server-mode && home-manager switch --flake ~/workspace/devrc --impure";
    headless-mode = "touch ~/.server-mode && home-manager switch --flake ~/workspace/devrc --impure";
  };

  history = {
    append = true;
    save = 15000;
    saveNoDups = true;
  };

}
