{ pkgs ? import <nixpkgs> {} }:

with pkgs;
[
  nodePackages.vscode-langservers-extracted
]

