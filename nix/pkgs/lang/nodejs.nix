{ pkgs ? import <nixpkgs> {} }:

with pkgs;
[
  nodePackages.typescript
  #nodePackages.vscode-json-languageserver
  nodePackages.vscode-langservers-extracted
  nodejs_20
  typescript-language-server
]

