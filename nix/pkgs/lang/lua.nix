{ pkgs ? import <nixpkgs> {} }:

with pkgs;
[
  # Lua 5.1 because Neovim uses LuaJIT which is lua 5.1
  lua5_1
  sumneko-lua-language-server
  lua53Packages.lyaml
]

