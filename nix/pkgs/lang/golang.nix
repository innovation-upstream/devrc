{ pkgs ? import <nixpkgs> {} }:

with pkgs;
[
  go_1_22
  gopls
]

