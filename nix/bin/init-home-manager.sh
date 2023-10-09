#!/usr/bin/env bash

DEVRC_DIR=${DEVRC_DIR:-$PWD}
nix_home_path="${DEVRC_DIR}/nix/home.nix"

NIX_PATH=$HOME/.nix-defexpr/channels${NIX_PATH:+:}$NIX_PATH TMPDIR=/tmp nix-shell --show-trace '<home-manager>' -A install

cat << EOF > ~/.config/home-manager/home.nix
let
  home-nix-path = /. + builtins.toPath "$(echo "${nix_home_path}")";
  imports = [ home-nix-path ];
in
{
  inherit imports;
  home.username = "$(whoami)";
  home.homeDirectory = "$(echo "${HOME}")";
}
EOF
