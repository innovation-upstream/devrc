{ pkgs, config, ... }:
let
  # `config` for home.homeDirectory: neovim's extraConfig substitutes the repo
  # path into init.vim at BUILD time rather than leaving `$DEVRC_DIR` to be
  # resolved at runtime. See nix/programs/neovim/default.nix for why.
  neovim = import ./neovim {pkgs=pkgs; config=config;};
  zsh = import ./zsh {config=config;};
  fzf = import ./fzf {};
  bash = import ./bash {config=config;};
  tmux = import ./tmux {pkgs=pkgs;};
  git = import ./git {};
  direnv = import ./direnv {};
  alacritty = import ./alacritty {};
  k9s = import ./k9s {};
  ranger = import ./ranger {};
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
  k9s = k9s;
  ranger = ranger;
}
