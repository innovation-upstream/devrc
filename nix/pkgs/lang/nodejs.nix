{ pkgs ? import <nixpkgs> {} }:

with pkgs;
[
  typescript
  #nodePackages.vscode-json-languageserver
  vscode-langservers-extracted
  nodejs_20
  typescript-language-server
]

