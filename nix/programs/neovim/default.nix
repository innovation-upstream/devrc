{ pkgs, ... }:

let
  plugins = (import ./plugins.nix {pkgs=pkgs;});
in
{
  enable = true;
  package = pkgs.neovim-nightly;
  vimAlias = true;
  extraConfig = builtins.readFile ../../../.config/nvim/init.vim;
  plugins = with pkgs.vimPlugins; with plugins; [
    packer-nvim
    vim-fugitive
    undotree
    vim-signify
    completion-nvim
    completion-buffers
    vim-obsession
    vim-cue
  ];
  extraLuaPackages = ps: [
    ps.lyaml
  ];
}
