{ pkgs ? import <nixpkgs> {} }:

with pkgs;
[
  #nodePackages.firebase-tools
  nodePackages.typescript
  #nodePackages.typescript-language-server
  nodePackages.vscode-json-languageserver
  nodePackages.vscode-langservers-extracted
  nodejs_20
  typescript-language-server
]

