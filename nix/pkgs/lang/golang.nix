{ pkgs ? import <nixpkgs> {} }:

with pkgs;
[
  go_1_17
  gopls
  gotools
  mockgen
  gotests
]

