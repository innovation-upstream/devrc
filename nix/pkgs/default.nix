{ pkgs ? import <nixpkgs> {} }:

with pkgs;
[
  # basics
  coreutils
  gnused # sed
  zsh
  oh-my-zsh
  bash # use latest bash
  tmux
  vim
  fzf # fuzzy finder
  gotop # terminal based graphical activity monitor
  wget
  gcc
  bat

  # search
  gnugrep
  ripgrep
  fd

  # git
  git # to replace possible old git comes with OS
  lefthook

  google-cloud-sdk
  nix-direnv
]
++
(import ./lang {pkgs=pkgs;})
++
(import ./tools {pkgs=pkgs;})
