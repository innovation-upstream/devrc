{ pkgs ? import <nixpkgs> {} }:

with pkgs;
[
  vscode-extensions.graphql.vscode-graphql
]

