{ pkgs ? import <nixpkgs> {} }:

with pkgs;
  (import ./bazel.nix {pkgs=pkgs;})
  ++
  (import ./docker.nix {pkgs=pkgs;})
  ++
  (import ./k8s {pkgs=pkgs;})
  ++
  (import ./linkerd.nix {pkgs=pkgs;})
  ++
  (import ./direnv {pkgs=pkgs;})

