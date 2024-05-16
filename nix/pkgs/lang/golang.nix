{ pkgs ? import <nixpkgs> {} }:

with pkgs;
[
  go_1_21
  gopls
]

