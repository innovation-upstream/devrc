#!/usr/bin/env sh

# Init and configure fzf if we are in zsh
[ -f ~/.fzf.zsh ] && [ "$SHELL" = "/usr/bin/zsh" ] && source ~/.fzf.zsh

export FZF_DEFAULT_COMMAND='fdfind --type file --follow --hidden --exclude .git --exclude node_modules --exclude www --exclude public'
export FZF_ALT_C_COMMAND='fdfind --type d . --color=never'
export FZF_CTRL_T_COMMAND="$FZF_DEFAULT_COMMAND"
export PATH=$PATH:/usr/local/go/bin:$HOME/go/bin:$HOME/.linkerd2/bin

# Init nvm
export NVM_DIR="$([ -z "${XDG_CONFIG_HOME-}" ] && printf %s "${HOME}/.nvm" || printf %s "${XDG_CONFIG_HOME}/nvm")"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

# The next line updates PATH for the Google Cloud SDK.
if [ -f '/home/developer/google-cloud-sdk/path.zsh.inc' ]; then . '/home/developer/google-cloud-sdk/path.zsh.inc'; fi

# The next line enables shell command completion for gcloud.
if [ -f '/home/developer/google-cloud-sdk/completion.zsh.inc' ]; then . '/home/developer/google-cloud-sdk/completion.zsh.inc'; fi
