{ pkgs }:

with pkgs; [
  # Go
  go_1_25
  gopls

  # Python
  python312
  pyright

  # Rust
  rust-analyzer

  # Node.js / TypeScript
  nodejs_20
  typescript
  vscode-langservers-extracted # HTML/CSS/JSON LSPs
  typescript-language-server

  # Lua (5.1 for Neovim's LuaJIT)
  lua5_1
  lua-language-server
  lua53Packages.lyaml

  # C#
  csharp-ls

  # Perl
  perl

  # Cue
  cue

  # Nix
  nixd

  # YAML
  yaml-language-server

  # Bash
  bash-language-server
]
