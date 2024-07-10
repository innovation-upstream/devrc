{ pkgs ? import <nixpkgs> {} }:

with pkgs;
[
  python312Full
  pyright
]

