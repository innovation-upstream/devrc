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
  ripgrep
  fd

  # git
  git # to replace possible old git comes with OS
  lefthook

  google-cloud-sdk
]
++
(import ./lang {pkgs=pkgs;})
++
(import ./tools {pkgs=pkgs;})
