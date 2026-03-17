{ pkgs ? import <nixpkgs> {} }:

with pkgs;
  (import ./docker.nix {pkgs=pkgs;})
  ++
  (import ./lazygit.nix {pkgs=pkgs;})
  ++
  (import ./k9s.nix {pkgs=pkgs;})
  ++
  (import ./nemo.nix {pkgs=pkgs;})

