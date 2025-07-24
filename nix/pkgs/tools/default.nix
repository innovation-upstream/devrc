{ pkgs ? import <nixpkgs> {} }:

with pkgs;
  (import ./docker.nix {pkgs=pkgs;})
  ++
  (import ./lazygit.nix {pkgs=pkgs;})

