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

  # The ruby/python3 REMOTE-PLUGIN hosts (`:h provider-ruby`, `:h
  # provider-python`) -- NOT the python LSP server, which is a separate process
  # and unaffected. home-manager 26.05 flips both defaults to false; until
  # `home.stateVersion` reaches 26.05 the legacy `true` is taken and each emits
  # an eval warning on every switch. Adopted early rather than pinned, because
  # nothing here uses them. Measured 2026-09-07 on the workbench:
  #
  #   - rplugin.vim is 71 bytes -- four empty section headers, zero registered
  #     remote plugins -- on BOTH the generation's manifest and
  #     ~/.local/share/nvim/rplugin.vim.
  #   - None of the 4 nix plugins below nor the 25 packer-managed plugins in
  #     .config/nvim/lua/plugins.lua ships an `rplugin/`, `pythonx/` or `ruby/`
  #     directory.
  #   - GNU grep (not the ugrep wrapper) over .config/nvim/ for
  #     python|ruby|pynvim|provider|py3eval|*_host_prog finds only comments.
  #   - Generation closure 8,708,381,320 -> 8,664,830,096 B, i.e. -43,551,224
  #     (~41.5 MiB), measured at base 18bc1500 with `nix path-info -S`. The
  #     saving is entirely ruby (ruby-3.4.9 + gems + the two provider envs);
  #     withPython3 costs the neovim WRAPPER nothing, because home-manager
  #     passes wrapRc = false and the host_prog line lands in this init.lua
  #     rather than in the wrapper.
  #
  # Setting either option to either value silences the warning -- it hangs off
  # the default thunk (lib/deprecations.nix, mkStateVersionOptionDefault), so
  # merely being explicit is enough. `false` is chosen for the closure.
  #
  # Cost if this is ever wrong: `:python3`/`:ruby` and any remote plugin raise
  # E319. Flip back to `true` and switch.
  withRuby = false;
  withPython3 = false;
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
