{ pkgs ? import <nixpkgs> {} }:

with pkgs;
[
  nodePackages.firebase-tools
  nodePackages.typescript
  nodePackages.typescript-language-server
  nodePackages.vscode-json-languageserver
  nodePackages.vscode-css-languageserver-bin
  nodePackages.vscode-langservers-extracted
  nodejs-18_x
]

