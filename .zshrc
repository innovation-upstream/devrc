# Source nix
. "$HOME/.nix-profile/etc/profile.d/hm-session-vars.sh"
. "$HOME/workspace/devrc/nix/bin/source-nix.sh"

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

export DEVRC_DIR=${DEVRC_DIR:-$HOME/workspace/devrc}

_direnv_hook() {
  trap -- '' SIGINT;
  eval "$("direnv" export zsh)";
  trap - SIGINT;
}
typeset -ag precmd_functions;
if [[ -z ${precmd_functions[(r)_direnv_hook]} ]]; then
  precmd_functions=( _direnv_hook ${precmd_functions[@]} )
fi
typeset -ag chpwd_functions;
if [[ -z ${chpwd_functions[(r)_direnv_hook]} ]]; then
  chpwd_functions=( _direnv_hook ${chpwd_functions[@]} )
fi

export PATH=$PATH:$HOME/go/bin:$HOME/.npm-packages/bin
export NODE_PATH=$HOME/.npm-packages/lib/node_modules

xset r rate 180 30
