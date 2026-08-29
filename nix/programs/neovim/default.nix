{ pkgs, config, ... }:

let
  plugins = (import ./plugins.nix {pkgs=pkgs;});

  # 🔴 `$DEVRC_DIR` IS NOT A SESSION VARIABLE. It is set in exactly one place --
  # a systemd user service's `Environment=` block in nix/graphical.nix -- so it
  # exists only inside a graphical session. Unlike `$DEVRC` and `$HOMELAB`, it
  # is NOT in .zshenv.
  #
  # init.vim sources every other config file through it, so with the variable
  # unset the first line expands to `source /.config/nvim/config/native.vim`,
  # raises E484, and ABORTS THE WHOLE CONFIG -- no options, no leader mappings,
  # no plugin config, no lua half. Measured 2026-08-29 over real ssh to the
  # laptop:
  #
  #     Error in /home/zach/.config/nvim/init.lua:
  #     E484: Can't open file /.config/nvim/config/native.vim
  #     clipboard: No provider. Try ":checkhealth" or ":h clipboard".
  #
  # So neovim has been running with NO configuration at all in every non-
  # graphical context: ssh, a bare TTY, a systemd unit, cron. It looked healthy
  # because the only place anyone reads a config error is the terminal they are
  # sitting in front of, which is the one place the variable IS set.
  #
  # Substituted at BUILD time instead of resolved at RUNTIME: there is no
  # environment left to get wrong. This still points at the WORKING TREE, so
  # editing the sourced files applies with no switch, exactly as before.
  devrcDir = "${config.home.homeDirectory}/workspace/devrc";
  initVim = builtins.replaceStrings [ "$DEVRC_DIR" ] [ devrcDir ]
    (builtins.readFile ../../../.config/nvim/init.vim);
in
{
  enable = true;
  defaultEditor = true;
  #package = pkgs.neovim;
  extraConfig = initVim;
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
