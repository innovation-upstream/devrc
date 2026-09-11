#!/usr/bin/env bash
# Canonical scratchpad slot table — THE single source of truth for the
# session <-> hotkey <-> color <-> codename mapping. The tmux `bind -n M-<key>`
# popup toggles are GENERATED from this table at home-manager build time
# (nix/programs/tmux/default.nix), so this file is the source, not a mirror.
# Example: `Alt+Shift+V` -> tmux session `scratch4` -> codename `Vapor`.
#
# 🔴 The binding is tmux's ROOT key table, NOT i3. There is no i3 binding for any
# slot — measured: `grep -rE 'mod\+Shift\+(V|G|P)' nix/i3/` -> 0, against 79 live
# `bindsym` lines in nix/i3/config.nix, and 22 `M-<key>` entries in
# `tmux list-keys -T root`. A previous version of this comment spelled the hotkey
# `$mod+Shift+V` (i3 syntax); a reader following it presses a chord that is bound
# to nothing.
#
# 🔴 CASE IS SIGNIFICANT and is not a shift-modifier convention: `M-v` -> scratch3
# (`violet`) and `M-V` -> scratch4 (`Vapor`) are DIFFERENT sessions. Never
# lowercase a key when quoting it.
#
# Each binding is a TOGGLE: if the client is already in that session it runs
# `detach-client` (dismissing the popup); otherwise it opens `display-popup`.
#
# This file is SOURCED (not executed). Consumers — keep in sync by reading THIS,
# never a private copy:
#   - tmux-scratch-monitor.sh   (Alt+m HUD)
#   - scripts/i3status-scratchpads  (the LIVE colour legend — an i3status-rust
#                                    bar block; parses this file with a regex,
#                                    it does not source it)
#   - tmux-scratch-status.sh    (the legend's retired tmux status-left renderer,
#                                kept only for debugging — nothing calls it)
#   - scripts/session-analysis/initiative-scan.py  (parses this file's entries)
#
# Field order per entry:  session:key:color:name
#   session — tmux session name (also the emit key)
#   key     — hotkey letter; the binding is tmux `bind -n M-<key>` (Alt+<key>),
#             case-sensitive. NOT an i3 chord.
#   color   — hex, matches the popup border color in .tmux.conf
#   name    — human codename shown in the HUD / ledger
# Consumers source this by looking in their own dir for the deployed name first,
# then the repo name (they run from ~/.config/tmux/ deployed, or scripts/ in-repo):
#   _d="$(dirname "$0")"
#   if   [ -f "$_d/scratch-slots.sh" ];      then . "$_d/scratch-slots.sh"
#   elif [ -f "$_d/tmux-scratch-slots.sh" ]; then . "$_d/tmux-scratch-slots.sh"; fi
#
# 🔴 THE ENTRY GRAMMAR LIVES ON THE MARKER LINE BELOW, AND IT IS THE ONLY COPY.
# Two consumers do NOT source this file — they read it with a regex:
#   * nix/programs/tmux/default.nix  (generates the `bind -n M-<key>` popups)
#   * scripts/i3status-scratchpads   (the bar's colour legend)
# They used to carry a regex EACH, and the two disagreed in both directions:
# a 9- or 12-digit hex colour (both valid pango) got a tmux binding but was
# dropped from the legend, and a COMMENTED-OUT slot line was ignored by nix but
# matched by the legend, which then advertised a hotkey bound to nothing. So the
# pattern is written here ONCE and both read it from this file — the same bytes
# they already open for the table itself, so it needs no extra deploy.
#
# Written in the intersection of POSIX ERE (nix `builtins.match`) and Python
# `re`. 🔴 THE WHITESPACE BRACKETS HOLD A LITERAL SPACE AND A LITERAL TAB BYTE
# — `[ <TAB>]*`, invisible in most editors. That is the ONE spelling both
# engines accept:
#   * `[ \t]` written with a BACKSLASH and a `t` does NOT work in nix — a
#     backslash inside a POSIX bracket expression is literal, so it means
#     "space, backslash or t". MEASURED 2026-09-11:
#     `builtins.match "[ \\t]*x" "<TAB>x"` -> null.
#   * `[[:blank:]]` works in nix (MEASURED: it matches a tab) but Python's `re`
#     has no POSIX classes.
#   * a raw tab BYTE inside the bracket works in both (MEASURED on the same
#     day: nix `builtins.match "[ <TAB>]*x[ <TAB>]*" "<TAB>x<TAB>"` -> `[]`,
#     i.e. a match).
# An earlier version of this file used `[ ]*` — space only — and cited the two
# failures above as if they exhausted the options. They do not, and the
# omission was a live narrowing: a TAB-INDENTED entry then matched NOBODY's
# grammar while bash still put it in `SCRATCH_SLOTS`. MEASURED before the fix,
# one entry re-indented with a tab: bash 20 entries, the legend 19 slots with
# NO `?`, nix 19 bindings with NO throw — because the CANDIDATE patterns
# (the shortfall floor's yardstick) were spelled `[ ]*` too and narrowed in
# lockstep with the grammar, so the floor could not fire. The candidate
# patterns are now deliberately WIDER than this grammar on BOTH sides; see
# `_CANDIDATE_RE` in scripts/i3status-scratchpads and `candidateLines` in
# nix/programs/tmux/slot-table.nix.
#
# Applied WHOLE-LINE by both: nix matches it against each split line, and the
# Python side wraps it in `^…$` under `re.MULTILINE`. That anchoring is what
# makes a commented-out entry a non-entry for everyone.
#
# ⚠ WHAT IS GIVEN UP, deliberately: an entry with a TRAILING COMMENT
# (`"a:b:#c:d" # note`) is NOT a slot to this grammar, while bash DOES put it
# in the array — MEASURED, a 3-entry fixture whose middle line carried a
# trailing comment yielded `${#SCRATCH_SLOTS[@]}` = 3. That shape used to be
# silently dropped by two of the three readers; it is now LOUD instead,
# because the wider candidate patterns count it and the shortfall floor then
# refuses the whole table (nix throws, the legend renders `?`). Write the
# comment on its own line above the entry.
#
# The colour lengths are the ones pango accepts — #RGB, #RRGGBB, #RRRGGGBBB,
# #RRRRGGGGBBBB — so 5- and 7-digit hex, which no pango parser accepts, is not
# a slot to anybody. Capture groups, in order: session, key, colour (with `#`),
# the colour's hex digits, name.
# SLOT_ENTRY_RE: [ 	]*"([^":]+):([^":]+):(#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{9}|[0-9a-fA-F]{12})):([^"]+)"[ 	]*
SCRATCH_SLOTS=(
    "scratch:g:#b8bb26:grove"
    "scratch2:G:#d79921:Gold"
    "scratch3:v:#b16286:violet"
    "scratch4:V:#83a598:Vapor"
    "scratch5:p:#cc241d:poppy"
    "scratch6:P:#689d6a:Pool"
    "scratch7:o:#fe8019:orange"
    "scratch8:O:#d3869b:Orchid"
    "scratch9:n:#458588:navy"
    "scratch10:N:#928374:Nickel"
    "scratch11:w:#ebdbb2:wheat"
    "scratch12:W:#af3a03:Walnut"
    "scratch13:m:#fabd2f:mango"
    "scratch14:M:#d65d0e:Mars"
    "scratch15:i:#8f3f71:iris"
    "scratch16:I:#ebdbb2:Ivory"
    "scratch17:u:#b16286:ube"
    "scratch18:U:#928374:Umber"
    "scratch19:y:#689d6a:yew"
    "scratch20:Y:#79740e:Yarrow"
)
