{ elixirLspPath, ... }:

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

  ELIXIR_LSP_PATH = "${elixirLspPath}/share/vscode/extensions/JakeBecker.elixir-ls/elixir-ls-release/language_server.sh";
}
