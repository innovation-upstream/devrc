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
# NB: the npm `playwright` version in a project MUST match this bundle's
# `playwright-driver` version or Chromium's build number won't align — see
# scripts/playwright-nixos (`--version` prints the version to pin to).
# (home.sessionVariables land in profile.d, sourced by INTERACTIVE shells, not
# the non-interactive `zsh -c` the Bash tool uses — so scripts/playwright-nixos
# stays the switch-free path for agent/CI use.)
# Guarded so a host/refactor that doesn't pass the path can't hard-break the switch.
// pkgs.lib.optionalAttrs (playwrightBrowsersPath != null) {
  PLAYWRIGHT_BROWSERS_PATH = "${playwrightBrowsersPath}";
  PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS = "true";
}
