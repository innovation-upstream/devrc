{ config, pkgs, ... }:

let
  userPackages = import ./pkgs {pkgs=pkgs;};
  isNixOS = builtins.pathExists /etc/NIXOS;
  # To enable nightly, also remove comment in neovim/default.nix
  #overlays = import ./overlays.nix;
  sessionVariables = import ./sessionVariables.nix {
    elixirLspPath = pkgs.vscode-extensions.elixir-lsp.vscode-elixir-ls;
    playwrightBrowsersPath = pkgs.playwright-driver.browsers;
  };
  programs = import ./programs {pkgs=pkgs; config=config;};
in
{
  programs = programs;

  #nixpkgs.overlays = overlays;

  home.stateVersion = "24.11";

  home.packages = if isNixOS
  then
    userPackages ++ [pkgs.autorandr]
  else
    userPackages;

  home.sessionVariables = sessionVariables;

  # Symlink tmux scripts
  home.file.".config/tmux/idle-color.sh" = {
    source = ../scripts/tmux-idle-color.sh;
    executable = true;
  };
}
