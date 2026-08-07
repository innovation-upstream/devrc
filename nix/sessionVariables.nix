{ pkgs, elixirLspPath, homePath, playwrightBrowsersPath ? null, ... }:

let
  fzfDefaultCommand = "fd --type file --follow --hidden --exclude .git --exclude node_modules --exclude www --exclude public -E vendor";
  fzfIncludeHiddenCommand = "fd --type file --follow --hidden -I -E .git -E node_modules -E www --exclude public -E vendor -E bazel-out -E bazel-bin -E bazel-peazy-dev -E bazel-cache -E bazel-testlogs -E .direnv -E logs -E .next";
in
{
  EDITOR = "nvim";
  FZF_DEFAULT_COMMAND = fzfDefaultCommand;
  FZF_INCLUDE_HIDDEN_COMMAND = fzfIncludeHiddenCommand;
  FZF_ALT_C_COMMAND = fzfIncludeHiddenCommand;
  FZF_CTRL_T_COMMAND = fzfDefaultCommand;

  NODE_PATH = "${homePath}/.npm-packages/lib/node_modules";

  ELIXIR_LSP_PATH = "${elixirLspPath}/share/vscode/extensions/JakeBecker.elixir-ls/elixir-ls-release/language_server.sh";
  K9S_FEATURE_GATE_NODE_SHELL = "true";
}
# Playwright on NixOS: point it at the nixpkgs-patched browser bundle instead of
# its own download (a generic-linux ELF that stub-ld refuses → exitCode=127 /
# "GLIBC_ABI_GNU2_TLS not found"), and skip the host-requirements probe. This
# makes interactive shells + the Playwright MCP launch Chromium natively.
# 🔴 This is a GLOBAL, SINGLE-VERSION default, and it is deliberately the flake's
# DEFAULT `playwright-driver` (1.61.1 today) — the version the Playwright MCP and
# every 1.61.x project want. It cannot serve a project pinned to another Playwright
# line: the version→Chromium-build mapping is 1:1, and a mismatch does not error
# usefully — the suite collects every file and executes NONE (measured 2026-08-06:
# civitai on 1.57.0 → `Test Files (130)` / `Tests no tests`, exit 1).
# Per-project bundles are therefore OPT-IN and live OUTSIDE this variable:
# flake.nix exposes `playwright-driver-<major>_<minor>` alongside the default, and
# scripts/playwright-nixos picks the one matching the project's own installed
# Playwright (and asserts the chromium revision actually matches). Nothing here
# changes for a 1.61.1 project; do NOT point this at a non-default bundle.
# (home.sessionVariables land in profile.d, sourced by INTERACTIVE shells, not
# the non-interactive `zsh -c` the Bash tool uses — though a long-lived agent
# process still INHERITS them from the interactive shell that launched it, so in
# practice scripts/playwright-nixos usually OVERRIDES this value rather than
# supplying a missing one. Either way it stays the switch-free path.)
# Guarded so a host/refactor that doesn't pass the path can't hard-break the switch.
// pkgs.lib.optionalAttrs (playwrightBrowsersPath != null) {
  PLAYWRIGHT_BROWSERS_PATH = "${playwrightBrowsersPath}";
  PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS = "true";
}
