function git_prompt_info() {
  local ref
  if [[ "$(command git config --get oh-my-zsh.hide-dirty)" != "1" ]]; then
    if [[ "$(__git_prompt_git config --get oh-my-zsh.hide-status 2>/dev/null)" != "1" ]]; then
      ref=$(__git_prompt_git symbolic-ref HEAD 2> /dev/null) || \
      ref=$(__git_prompt_git rev-parse --short HEAD 2> /dev/null) || return 0
      echo "$ZSH_THEME_GIT_PROMPT_PREFIX${ref#refs/heads/}$(parse_git_dirty)$ZSH_THEME_GIT_PROMPT_SUFFIX"
    fi
  fi
}

# Reasonable zsh defaults
export ZSH="/home/$USER/.oh-my-zsh"
ZSH_THEME="robbyrussell"
plugins=(git golang gcloud)
source $ZSH/oh-my-zsh.sh

# Custom Dev Env Init
if test -f $HOME/.devrc; then
  source $HOME/.devrc
else
  BLUE='\033[1;34m'
  NC='\033[0m' # No Color
  printf "${BLUE}No \$HOME/.devrc file found! ${NC}Copy ~/.devrc.default if you would like \
to configure your shell.\n"
  source $HOME/.devrc.default
fi

