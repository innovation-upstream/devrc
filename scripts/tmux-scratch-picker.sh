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

# 🔴 WHICH SERVER WE TALK TO — the same question scripts/i3status-scratchpads
# answers in `_socket_roots`/`find_socket`, and the two MUST agree: the legend
# pill and the picker behind its click are one feature, and a picker listing a
# different server's sessions than the legend renders is a click that does not
# do what the thing it was clicked on says.
#
# tmux's compiled-in socket dir is `/tmp`; this host's real socket is under
# `$TMUX_TMPDIR=/run/user/1000`, a value with no established source (see the
# long note in scripts/i3status-scratchpads). Inside a tmux client `$TMUX`
# already names the socket, so this only runs for the bar-click caller.
#
# ⚠ DUPLICATED IN TWO LANGUAGES, deliberately: the legend is Python and this is
# bash, and the alternative — shelling out to the block script — would make a
# click depend on a bar internal. Both lists are pinned against each other by
# scripts/tests/test_scratchpads_block.py, which runs THIS script with a fake
# tmux and checks the root it picks.
if [ -z "${TMUX:-}" ]; then
    _uid=$(id -u)
    if [ -n "${TMUX_TMPDIR:-}" ]; then
        : # an explicit declaration is authoritative — do not search past it
    else
        for _root in "${XDG_RUNTIME_DIR:-}" "/run/user/$_uid" /tmp; do
            [ -n "$_root" ] || continue
            if [ -e "$_root/tmux-$_uid/default" ]; then
                export TMUX_TMPDIR="$_root"
                break
            fi
        done
    fi
    unset _uid _root
fi

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
fzf_rc=$?

# 🔴 AN EMPTY `$selected` HAS TWO CAUSES AND ONLY ONE OF THEM IS DELIBERATE.
# fzf's documented codes: 0 = a selection, 1 = no match, 130 = interrupted (ESC
# or ^C — the operator dismissed it). ANYTHING ELSE means fzf did not run: 127
# when the binary is not on PATH, 2 on an fzf error. Those produce exactly the
# same empty `$selected`, and exiting 0 on them makes the float terminal vanish
# instantly — the indistinguishable-from-nothing failure this script's hold at
# the bottom exists for.
#
# Non-hypothetical: a `home-manager switch` blanks `~/.nix-profile` for ~1s
# while it writes the intermediate generation, so anything invoked by BARE
# COMMAND NAME during a switch dies "command not found" (recorded in the
# project's own memory). `fzf` here is a bare name.
case "$fzf_rc" in
    0|1|130) ;;
    *)
        printf '\nscratch-picker: fzf exited %s — the picker could not run.\n' \
            "$fzf_rc" >&2
        read -n 1 -r -s -p "[any key to close]"
        exit 1
        ;;
esac

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
