{ pkgs ? import <nixpkgs> {} }:

with pkgs;
[
  #rustup
  rust-analyzer
]

