# Source nix
. "$HOME/.nix-profile/etc/profile.d/hm-session-vars.sh"
. "$HOME/workspace/devrc/nix/bin/source-nix.sh"

export DEVRC_DIR=${DEVRC_DIR:-$HOME/workspace/devrc}

# Directory jumping via CDPATH
export CDPATH=".:$HOME/workspace:$HOME/workspace/civit"


# Set keyboard repeat rate (X11 only, skip inside tmux to avoid running per-pane)
[[ -z "$TMUX" && -n "$DISPLAY" ]] && xset r rate 180 30
