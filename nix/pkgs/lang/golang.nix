{ pkgs ? import <nixpkgs> {} }:

with pkgs;
[
  go
  gopls
  goimports
  mockgen
]

