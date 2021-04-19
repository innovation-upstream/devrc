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

BLUE='\033[1;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color
BOLDNC="${NC}\033[1m"

# Custom Dev Env Init
if test -f $HOME/.devrc; then
  source $HOME/.devrc
  # Try to catch if the .devrc is misconfigured
  if ! command -v bazel &> /dev/null; then
    printf "${BLUE}bazel ${RED}command was not found after loading ${BOLDNC}\$HOME/.devrc! \
${RED}Please ensure you are sourcing ${BOLDNC}\$HOME/cmd/profile.sh ${RED}or initializing nvm in \
${BOLDNC}\$HOME/.devrc${NC}. \n(See \$HOME/.devrc.default for a working example)\n"
  fi
else
  printf "Using ${BLUE}\$HOME/.devrc${BOLDNC}. Copy ~/.devrc.default if you would like \
to further configure your shell.\n"
  source $HOME/.devrc.default
fi

