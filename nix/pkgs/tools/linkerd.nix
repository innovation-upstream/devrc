{ pkgs ? import <nixpkgs> {} }:

with pkgs;
[
  step-ca
  step-cli
  linkerd_edge
]

