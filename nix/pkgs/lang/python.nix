{ pkgs ? import <nixpkgs> {} }:

with pkgs;
[
  python312
  pyright
]

