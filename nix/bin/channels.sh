#!/usr/bin/env bash

set -x

# main channel - nixpkgs
nix-channel --add https://nixos.org/channels/nixpkgs-unstable

# channel for home-manager
nix-channel --add https://github.com/nix-community/home-manager/archive/master.tar.gz home-manager

# update all the channels
nix-channel --update
