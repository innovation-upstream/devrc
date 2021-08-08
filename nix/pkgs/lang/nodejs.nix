{ pkgs ? import <nixpkgs> {} }:

with pkgs;
[
  nodePackages.firebase-tools
  nodePackages.typescript
  nodePackages.typescript-language-server
  nodePackages.vscode-json-languageserver
  nodePackages.neovim
  nodePackages.eslint_d
  nodePackages.vscode-css-languageserver-bin
]

