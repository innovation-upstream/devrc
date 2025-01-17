{ pkgs, config, ... }:
let
  neovim = import ./neovim {pkgs=pkgs;};
  zsh = import ./zsh {config=config;};
  fzf = import ./fzf {};
  bash = import ./bash {config=config;};
  tmux = import ./tmux {pkgs=pkgs;};
  git = import ./git {};
  direnv = import ./direnv {};
  alacritty = import ./alacritty {};
in
{
  # Let Home Manager install and manage itself.
  home-manager.enable = true;
  neovim = neovim;
  zsh = zsh;
  fzf = fzf;
  bash = bash;
  tmux = tmux;
  git = git;
  direnv = direnv;
  alacritty = alacritty;
}
