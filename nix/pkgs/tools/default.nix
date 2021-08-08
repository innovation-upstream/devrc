{ pkgs ? import <nixpkgs> {} }:

with pkgs;
  (import ./bazel.nix {pkgs=pkgs;})
  ++
  (import ./docker.nix {pkgs=pkgs;})
  ++
  (import ./k8s.nix {pkgs=pkgs;})
  ++
  (import ./linkerd.nix {pkgs=pkgs;})

