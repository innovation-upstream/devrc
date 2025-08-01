{ pkgs ? import <nixpkgs> {} }:

with pkgs;
[
  go_1_25
  gopls
]

