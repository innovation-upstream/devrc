#!/usr/bin/env bash
# Scratch picker — list existing scratch-* sessions, toggle or create new ones.
#
# TWO CALLERS, and they run it in DIFFERENT environments:
#   * tmux Alt+Shift+T  -> `display-popup -E ~/.config/tmux/scratch-picker.sh`.
#     MEASURED on a throwaway server 2026-09-11: a `display-popup -E` child DOES
#     get `$TMUX` (`TMUX=<socket>,<pid>,<sid>`; `$TMUX_PANE` is NOT set, which is
#     why nothing here reads it), and `display-message` there answers the
#     client's own session.
#   * the i3status-rust scratchpad legend pill's left-click ->
#     `scratchPickerCmd` in nix/graphical.nix, a float alacritty. NO `$TMUX`.
#
# 🔴 `$TMUX` IS THE ONLY EVIDENCE THAT WE ARE INSIDE A CLIENT, AND THE DETACH
# BRANCH MUST NOT RUN WITHOUT IT. `tmux display-message -p '#{session_name}'`
# does not answer "none" outside a client — it answers the server's
# MOST-RECENTLY-USED session. MEASURED (private `TMUX_TMPDIR` server, `$TMUX`
# unset, one client attached to the NON-scratch session `work`, `scratch2`
# created last so it was MRU): `display-message` answered `scratch2`, the
# `scratch*` test fired, and `tmux detach-client` — which outside a client
# targets the server's best client, not "this" one — detached the `work`
# client. client-count 1 -> 0, this script exited 0 with nothing on stdout, so
# from the bar the click read as "did nothing" while it threw an unrelated
# session's client off the server. The `[ -n "$TMUX" ]` conjunct below is what
# makes the branch mean what its comment says; without it the guard is a
# question about the SERVER, not about us.

# If we're inside a scratch session, just detach (the toggle half of M-T).
if [ -n "${TMUX:-}" ]; then
    current_session=$(tmux display-message -p '#{session_name}' 2>/dev/null)
    if [[ "$current_session" == scratch* ]]; then
        tmux detach-client
        exit 0
    fi
fi

# List scratch sessions + option to create new
selected=$(
    {
        tmux list-sessions -F '#{session_name}' 2>/dev/null | grep '^scratch' | sort
        echo "[+ new scratchpad]"
    } | fzf --prompt="scratch> " \
            --header="enter: toggle | type to filter/create" \
            --reverse \
            --height=100%
)

[ -z "$selected" ] && exit 0

if [ "$selected" = "[+ new scratchpad]" ]; then
    selected="scratch-$(date +%s)"
fi

# Attach, else create. MEASURED with a real pty on a throwaway server
# (2026-09-11), both callers:
#   * NO $TMUX (the bar click): `attach-session` attaches THIS terminal to the
#     chosen session — client=<our pty> session=scratch5.
#   * $TMUX set (the display-popup child): `attach-session` ALSO succeeds — tmux
#     refuses nesting only when the target contains the calling pane, and a
#     popup is its own pty. No "sessions should be nested with care".
#
# 🔴 NO `exec`, and the hold below is the reason. If BOTH commands fail there is
# nothing long-lived left to hold the float terminal open, so it would vanish
# instantly — indistinguishable from the click doing nothing, which is the
# defect class `syshealthCmd` in nix/graphical.nix carries its own `read -n 1`
# for. `exec` would replace this shell and take that branch away.
if tmux attach-session -t "$selected" 2>/dev/null; then
    exit 0
fi
if tmux new-session -s "$selected"; then
    exit 0
fi
printf '\nscratch-picker: could not attach to or create session %s\n' "$selected" >&2
read -n 1 -r -s -p "[any key to close]"
exit 1
