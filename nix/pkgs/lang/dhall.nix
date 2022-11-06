{ pkgs ? import <nixpkgs> {} }:

with pkgs;
[
  dhall
  dhall-lsp-server
]

