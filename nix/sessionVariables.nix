let
  fzfDefaultCommand = "fd --type file --follow --hidden --exclude .git --exclude node_modules --exclude www --exclude public -E vendor";
in
{
  EDITOR = "nvim";
  FZF_DEFAULT_COMMAND = fzfDefaultCommand;
  FZF_ALT_C_COMMAND = "fdfind --type d . --color=never";
  FZF_CTRL_T_COMMAND = fzfDefaultCommand;
}
