{ pkgs ? import <nixpkgs> {} }:

with pkgs;
[
  go_1_24
  gopls
]

