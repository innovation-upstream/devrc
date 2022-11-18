{ pkgs ? import <nixpkgs> {} }:

with pkgs;
[
  go_1_19
  gopls
  gotools
  mockgen
  gotests
]

