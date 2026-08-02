// env.js — opencode plugin that injects the repo/kubeconfig handles into every
// bash tool invocation.
//
// WHY A PLUGIN AT ALL: opencode has NO `env` config key (verified on v1.18.4 —
// setting one is silently ignored), and its bash tool does NOT source zsh
// startup files, so the handles devrc exports from `.zshenv` are invisible to
// it. Before this existed, `KUBECONFIG=` was hand-retyped 169 times across 3
// different spellings. The `shell.env` hook is the only supported seam.
//
// Verified with a negative control: with the plugin present `$KC_HOMELAB`
// resolves inside a bash tool call; with it absent the variable is empty.
//
// DEPLOYMENT CONSTRAINTS (measured on v1.18.4 — do not "tidy" these away):
//   * the plugin glob is `{plugin,plugins}/*.{ts,js}` — NON-RECURSIVE, and
//     `.ts`/`.js` ONLY. A `.mjs` file will NOT load. This file must therefore
//     land directly at `~/.config/opencode/plugin/env.js`, never in a subdir.
//   * the hook mutates `output.env`; it does not return a new object.
//
// Managed by home-manager (see nix/home.nix). Edit HERE, then switch — editing
// ~/.config/opencode/plugin/env.js does nothing (it is a read-only store symlink).

const HOMELAB = "/home/zach/workspace/homelab-talos";

export const EnvPlugin = async () => ({
  "shell.env": async (_input, output) => {
    output.env.HOMELAB = HOMELAB;
    output.env.KC_HOMELAB = `${HOMELAB}/homelab-kubeconfig`;
    output.env.KC_WORKBENCH = `${HOMELAB}/workbench-kubeconfig`;
    output.env.KC_PROD = `${HOMELAB}/production-kubeconfig`;
  },
});
