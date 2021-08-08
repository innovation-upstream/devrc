{ pkgs ? import <nixpkgs> {} }:

with pkgs;
  (import ./golang.nix {pkgs=pkgs;})
  ++
  (import ./python.nix {pkgs=pkgs;})
  ++
  (import ./nodejs.nix {pkgs=pkgs;})
