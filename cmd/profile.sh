#!/usr/bin/env sh

export DEV_CLUSTER_CONTAINER=k3d-dev-cluster-server-0

alias kubectl="docker exec $DEV_CLUSTER_CONTAINER kubectl $@"

# Init and configure fzf if we are in zsh
[ -f ~/.fzf.zsh ] && [ "$SHELL" = "/usr/bin/zsh" ] && source ~/.fzf.zsh

export FZF_DEFAULT_COMMAND='fdfind --type file --follow --hidden --exclude .git --exclude node_modules --exclude www --exclude public'
export FZF_ALT_C_COMMAND='fdfind --type d . --color=never'
export FZF_CTRL_T_COMMAND="$FZF_DEFAULT_COMMAND"
export PATH=$PATH:/usr/local/go/bin:$HOME/go/bin

# Init nvm
export NVM_DIR="$([ -z "${XDG_CONFIG_HOME-}" ] && printf %s "${HOME}/.nvm" || printf %s "${XDG_CONFIG_HOME}/nvm")"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
