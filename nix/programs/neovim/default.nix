{ pkgs, ... }:

let
  plugins = (import ./plugins.nix {pkgs=pkgs;});
in
{
  enable = true;
  defaultEditor = true;
  #package = pkgs.neovim;
  extraConfig = builtins.readFile ../../../.config/nvim/init.vim;
  plugins = with pkgs.vimPlugins; with plugins; [
    undotree
    vim-signify
    vim-obsession
    vim-cue
  ];
  extraLuaPackages = ps: [
    ps.lyaml
  ];
}
