# File manager backend for Chrome/Brave "Show in folder" + xdg-open under i3.
#
# Brave/Chrome classify a directory path with gio/mimetype before launching a
# file manager; under bare i3 these helpers and their daemons aren't present,
# so "Show in folder" silently no-ops even when inode/directory is correctly
# pinned to thunar.desktop (done at the home-manager layer in nix/home.nix).
#
# Imported from /etc/nixos/configuration.nix. services.tumbler is intentionally
# NOT set here — it's already enabled in configuration.nix.
{ pkgs, ... }:
{
  services.gvfs.enable = true;    # gio + gvfsd + volume monitor
  programs.dconf.enable = true;   # ca.desrt.dconf: stops thunar/nemo dconf-commit errors

  environment.systemPackages = with pkgs; [
    shared-mime-info     # mimetype + update-mime-database
    file                 # libmagic type-detection fallback
    desktop-file-utils   # update-desktop-database
  ];
}
