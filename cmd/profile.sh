#!/usr/bin/env sh

export DEV_CLUSTER=k3d-dev-cluster-server-0

alias kubectl="docker exec $DEV_CLUSTER kubectl $@"

# Init and configure fzf
[ -f ~/.fzf.zsh ] && source ~/.fzf.zsh

export FZF_DEFAULT_COMMAND='fd --type file --follow --hidden --exclude .git'
export FZF_ALT_C_COMMAND='fd --type d . --color=never'
export FZF_CTRL_T_COMMAND="$FZF_DEFAULT_COMMAND"

