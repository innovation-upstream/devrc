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

# Directory jumping via CDPATH
export CDPATH=".:$HOME/workspace:$HOME/workspace/civit"

# Claude task launcher — names tmux window and writes initial task state
# Usage: ct "fix ingress TLS"  or  ct (launches claude without naming)
ct() {
    if [[ -n "$1" ]]; then
        local task="$*"
        tmux rename-window "$task"
        local win_id=$(tmux display-message -p '#{window_id}')
        local task_dir="${HOME}/.tmux/tasks"
        mkdir -p "$task_dir"
        jq -n \
            --arg task "$task" \
            --arg win_id "$win_id" \
            --arg started "$(date -Iseconds)" \
            --arg session "$(tmux display-message -p '#{session_name}')" \
            --arg win_idx "$(tmux display-message -p '#{window_index}')" \
            --arg cwd "$PWD" \
            '{task: $task, window_id: $win_id, tmux_session: $session,
              window_index: ($win_idx | tonumber), status: "started",
              cwd: $cwd, started: $started, last_activity: $started, summary: ""}' \
            > "${task_dir}/${win_id//[@%]/}.json"
    fi
    claude
}

# Mark current Claude task as completed (updates window prefix to ✅)
ctdone() {
    local win_id=$(tmux display-message -p '#{window_id}')
    local win_name=$(tmux display-message -p '#{window_name}')
    local task_name=$(echo "$win_name" | sed 's/^[^ ]* //')
    [[ -z "$task_name" ]] && task_name="$win_name"
    tmux rename-window "✅ ${task_name}"
    local task_file="${HOME}/.tmux/tasks/${win_id//[@%]/}.json"
    if [[ -f "$task_file" ]]; then
        local tmp=$(mktemp)
        jq '.status = "completed"' "$task_file" > "$tmp" && mv "$tmp" "$task_file"
    fi
}

# Set keyboard repeat rate (X11 only, skip inside tmux to avoid running per-pane)
[[ -z "$TMUX" && -n "$DISPLAY" ]] && xset r rate 180 30
