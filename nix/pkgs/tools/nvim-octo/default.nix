# nvim-octo — neovim + octo.nvim, wrapped as a single-purpose review TUI.
#
# WHAT IT IS FOR: a clicked GitHub `repo#N` mention in the terminal opens a full
# review-and-merge surface instead of a browser tab. `scripts/mention-open.py`
# spawns `alacritty … -e nvim-octo <owner/repo> <number>`; this package is that
# `nvim-octo`.
#
# 🔴 A DEDICATED WRAPPER, NOT octo ADDED TO THE OPERATOR'S DAILY NEOVIM. Two
# reasons, and the second is the load-bearing one:
#   * the review surface stays hermetic — its plugin set, its colours and its
#     keymaps cannot be changed by an edit to an editor config, and it cannot
#     be broken by one either;
#   * the merge-safety config in ./octo-init.lua strips upstream's four merge
#     KEYMAPS and its `<CR>` merge MENU. Doing that inside the daily editor
#     would impose this repo's risk posture on every other use of neovim, which
#     is not what was asked for and is not reversible per-buffer.
#
# 🔴 vimPlugins.octo-nvim DECLARES NO RUNTIME DEPENDENCIES. It is a bare
# `buildVimPlugin`, so nothing pulls in the modules its Lua actually
# `require`s. Every entry in `start` below is therefore mandatory rather than
# decorative:
#   plenary-nvim       — octo's async/job primitives; `require("plenary.job")`.
#   fzf-lua            — the picker `octo-init.lua` selects. NOT a preference:
#                        the telescope provider is the only one that wires
#                        `picker_config.mappings.merge_pr` to a merge action,
#                        and that table survives `mappings_disable_default`.
#                        See the picker note in octo-init.lua.
#   nvim-web-devicons  — fzf-lua and octo's file panel both resolve icons
#                        through it; without it the panel renders unlabelled.
#   gruvbox-nvim       — the colorscheme `octo-init.lua` sets, matching the
#                        alacritty palette in nix/programs/alacritty.
#
# 🔴 `gh` IS A HARD REQUIREMENT, NOT A NICETY. octo's `setup()` checks
# `vim.fn.executable(config.values.gh_cmd)` and REFUSES to initialise without
# it — the `Octo` user command is created by `commands.setup()`, which runs
# after that check, so a missing `gh` yields an editor where the ex-command in
# the wrapper below does not exist. It is pinned by store path through
# `runtimeInputs` rather than inherited, because the whole call chain starts in
# an alacritty hint spawned with the DISPLAY MANAGER's environment.
{ pkgs, lib ? pkgs.lib }:

let
  octoInit = ./octo-init.lua;

  # 🔴 THE INIT IS REFERENCED BY STORE PATH, WHICH IS WHAT MAKES THE CONFIG AND
  # THE BINARY ONE ARTEFACT. An edit to octo-init.lua changes this derivation's
  # inputs, so a host running an old wrapper is running the old config too —
  # there is no state where the keymaps on screen disagree with the file in the
  # repo except "you have not switched yet", which is the honest one.
  nvimWithOcto = pkgs.neovim.override {
    configure = {
      customRC = ''
        luafile ${octoInit}
      '';
      packages.octo = {
        start = with pkgs.vimPlugins; [
          octo-nvim
          plenary-nvim
          fzf-lua
          nvim-web-devicons
          gruvbox-nvim
        ];
        opt = [ ];
      };
    };
  };
in
pkgs.writeShellApplication {
  name = "nvim-octo";

  # `git` is here because octo reads the remote host/name for buffers opened
  # WITHOUT an explicit repo. `mention-open.py` always passes one, so this is
  # belt-and-braces for a hand-typed invocation rather than a path the click
  # depends on — stated plainly rather than justified as essential.
  runtimeInputs = [ nvimWithOcto pkgs.gh pkgs.git ];

  text = builtins.readFile ./nvim-octo.sh;

  meta = with lib; {
    description = "neovim + octo.nvim as a single-purpose GitHub review TUI";
    mainProgram = "nvim-octo";
    platforms = platforms.linux;
  };
}
