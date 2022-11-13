{ config, pkgs, ... }:

let
  userPackages = import ./pkgs {pkgs=pkgs;};
  isNixOS = builtins.pathExists /etc/NIXOS;
  overlays = import ./overlays.nix;
  sessionVariables = import ./sessionVariables.nix;
  programs = import ./programs {pkgs=pkgs; config=config;};
in
{
  programs = programs;

  nixpkgs.overlays = overlays;

  home.stateVersion = "21.11";

  home.packages = if isNixOS
  then
    userPackages ++ [pkgs.autorandr]
  else
    userPackages;

  home.sessionVariables = sessionVariables;
}
