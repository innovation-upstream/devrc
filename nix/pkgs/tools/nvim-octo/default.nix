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
# ⚠ IT IS NOT IN `home.packages`, AND THAT IS DELIBERATE. `nix/pkgs/tools/
# default.nix` carried an entry for a while; it bought only a hand-typed
# invocation nobody asked for, while adding a `home.packages` entry (a known
# collision surface with an imperative `nix profile install` on this host) and a
# second wrapped-neovim closure on the operator's PATH. The CLICK does not use
# it: the hint handler resolves `nvim-octo` through the wrapper's pinned
# `lib.makeBinPath` in `nix/programs/alacritty/default.nix`, which is the entry
# the AST ledger pins and the one that survives the ~1s window where a
# `home-manager switch` blanks `~/.nix-profile`. The overlay in `flake.nix`
# already forces this derivation to build, so nothing is un-gated by its
# absence.
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

  # ⚠ `pkgs.git` IS DELIBERATELY ABSENT, AND IT WAS HERE. octo reads a remote's
  # host/name only for a buffer opened WITHOUT an explicit repo — and this
  # wrapper REFUSES such an invocation: `nvim-octo.sh` requires both arguments
  # and exits 64 without them. So git could only ever have served a call shape
  # that cannot happen, which is dead weight in the closure plus a comment
  # justifying a code path that does not exist. If a future `nvim-octo` grows a
  # one-argument or zero-argument form, add it back WITH that form.
  runtimeInputs = [ nvimWithOcto pkgs.gh ];

  text = builtins.readFile ./nvim-octo.sh;

  meta = with lib; {
    description = "neovim + octo.nvim as a single-purpose GitHub review TUI";
    mainProgram = "nvim-octo";
    platforms = platforms.linux;
  };
}
