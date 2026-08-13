#!/usr/bin/env bash
# tmux-autoname-session.sh — give a session tmux named with a bare counter a
# name that says WHERE IT IS, once, at creation.
#
# Called from the `session-created` hook in .tmux.conf, APPENDED with `-ga` so
# it does not clobber the pipe-activity hook already bound there:
#
#   set-hook -ga session-created \
#     'run-shell -b "~/.config/tmux/autoname-session.sh #{q:hook_session_name}"'
#
# `#{q:...}` is tmux's sh(1)-escaping format modifier: the session name is
# interpolated into a /bin/sh command line, and a name containing a space or a
# `;` would otherwise be parsed as shell syntax.
#
# 🔴 WHAT THIS IS FOR. `session-manager` renders `codename: —` for every session
# outside the scratch slot table, because that table IS the codename vocabulary.
# Measured on the workbench 2026-08-13: two auto-numbered sessions (`0`, `8`)
# held 9 of ~30 windows, so a third of the operator's live work was invisible to
# every surface that keys on a codename. `workbench 8:1` says nothing; the cwd
# says plenty. This closes it going FORWARD; `session-manager`'s `label` field
# covers the sessions that already exist.
#
# 🔴 IT IS DELIBERATELY TIMID. Five things stop it, and the FIRST is the one
# that matters: it renames ONLY a name matching `^[0-9]+$`, which is exactly
# what tmux auto-assigns and nothing a human would type. Widening that pattern
# is how this eats `scratch7`'s name — and `scratch7` is a hotkey target, a
# colour and a codename, not just a string. Every other guard below is a
# fallback; this one is the guard.
#
# Exits 0 on EVERY path, prints nothing on success. It runs from a tmux hook: a
# non-zero exit or a stray line of output lands in the operator's terminal (or a
# view-mode popup) at the moment they open a new session.
set -uo pipefail

# The session name tmux is asking about. Absent -> nothing to do.
SESSION="${1:-}"
[ -n "$SESSION" ] || exit 0

# --------------------------------------------------------------------------- #
# GUARD 1 — the auto-number gate. THE load-bearing one; see the header.
# --------------------------------------------------------------------------- #
[[ "$SESSION" =~ ^[0-9]+$ ]] || exit 0

# --------------------------------------------------------------------------- #
# Where is it? `=` prefixes an EXACT session-name match, so a session called `1`
# cannot be resolved to `12` by tmux's prefix matching.
# --------------------------------------------------------------------------- #
cwd=$(tmux display-message -p -t "=${SESSION}:" '#{pane_current_path}' 2>/dev/null) || exit 0
[ -n "$cwd" ] || exit 0

# Prefer the git repo ROOT's basename over the raw directory: a session opened
# in `<repo>/nix/system` is about the repo, not about `system`. Falls through to
# the raw directory when the cwd is not in a work tree (or `git` is absent).
root=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null) || root=""
dir="${root:-$cwd}"
dir="${dir%/}"

# --------------------------------------------------------------------------- #
# GUARD 2 — a cwd that yields nothing useful leaves the session ALONE. Renaming
# `0` to `zach` (the basename of $HOME) is worse than leaving it as `0`: it
# looks like information and is not.
# --------------------------------------------------------------------------- #
[ -n "$dir" ] || exit 0
[ "$dir" != "${HOME:-}" ] || exit 0
base=$(basename -- "$dir" 2>/dev/null) || exit 0
[ -n "$base" ] && [ "$base" != "/" ] && [ "$base" != "." ] || exit 0

# --------------------------------------------------------------------------- #
# GUARD 3 — sanitise. tmux forbids `.` and `:` in a session name, and the name
# is interpolated back into shell command lines by other hooks, so reduce to a
# conservative alphabet rather than escaping per consumer.
# --------------------------------------------------------------------------- #
cand=$(printf '%s' "$base" | tr -c 'A-Za-z0-9_-' '-' | tr -s '-')
cand="${cand#-}"
cand="${cand%-}"
[ -n "$cand" ] || exit 0

# --------------------------------------------------------------------------- #
# GUARD 4 — never take a name the scratch slot table owns.
#
# Sourced from the canonical table (deployed copy first, then the in-repo one),
# the same resolution every other consumer uses — a private copy of these names
# would go stale the first time a slot is renamed. BOTH halves of each entry are
# reserved: the session name (`scratch7`) because renaming onto it would make
# `$mod+Shift+O` resolve to the wrong window, and the codename (`orange`)
# because that is the word the HUD, the ledger and the operator use for it.
# --------------------------------------------------------------------------- #
_d="$(dirname "$0")"
if   [ -f "$_d/scratch-slots.sh" ];      then . "$_d/scratch-slots.sh"
elif [ -f "$_d/tmux-scratch-slots.sh" ]; then . "$_d/tmux-scratch-slots.sh"; fi

taken=()
for _entry in "${SCRATCH_SLOTS[@]:-}"; do
    [ -n "$_entry" ] || continue
    taken+=("${_entry%%:*}")   # session name
    taken+=("${_entry##*:}")   # codename
done

# --------------------------------------------------------------------------- #
# GUARD 5 — and never take a name a LIVE session already holds. tmux would
# refuse the rename anyway ("duplicate session"), but refusing silently leaves
# the session as `0` forever; a suffix keeps the name useful.
# --------------------------------------------------------------------------- #
while IFS= read -r _live; do
    [ -n "$_live" ] && taken+=("$_live")
done < <(tmux list-sessions -F '#{session_name}' 2>/dev/null || true)

_is_taken() {
    local want="$1" t
    for t in "${taken[@]:-}"; do
        [ "$t" = "$want" ] && return 0
    done
    return 1
}

pick="$cand"
if _is_taken "$pick"; then
    pick=""
    for n in 2 3 4 5 6 7 8 9 10; do
        if ! _is_taken "${cand}-${n}"; then pick="${cand}-${n}"; break; fi
    done
fi
# Ten collisions on one name is not a case worth guessing at — leave it alone.
[ -n "$pick" ] || exit 0

# Nothing to do if it is somehow already there (belt-and-braces: GUARD 1 already
# means `pick` cannot equal a purely-numeric `$SESSION`).
[ "$pick" != "$SESSION" ] || exit 0

tmux rename-session -t "=${SESSION}" "$pick" >/dev/null 2>&1 || exit 0
exit 0
