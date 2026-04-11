{ pkgs ? import <nixpkgs> {} }:

with pkgs;
[
  vscode-extensions.elixir-lsp.vscode-elixir-ls
]

