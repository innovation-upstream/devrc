{ pkgs ? import <nixpkgs> {} }:

with pkgs;
[
  go_1_20
  gopls
  gotools
  mockgen
  gotests
]

