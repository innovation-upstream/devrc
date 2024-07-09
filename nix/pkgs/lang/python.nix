{ pkgs ? import <nixpkgs> {} }:

with pkgs;
[
  python312Full
  python312Packages.jedi-language-server
]

