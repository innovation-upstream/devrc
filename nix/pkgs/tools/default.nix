{ pkgs ? import <nixpkgs> {} }:

with pkgs;
  (import ./bazel.nix {pkgs=pkgs;})
  ++
  (import ./docker.nix {pkgs=pkgs;})

