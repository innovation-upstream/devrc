#!/usr/bin/env bash

nix_home_path="${DEVRC_DIR}/nix/home.nix"

nix-shell --show-trace '<home-manager>' -A install

cat << EOF > ~/.config/nixpkgs/home.nix
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
