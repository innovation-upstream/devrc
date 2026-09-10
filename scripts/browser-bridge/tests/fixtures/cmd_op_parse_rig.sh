#!/usr/bin/env bash
# Hermetic rig for the `cmd_op <op>` dispatch parser in test_surface_parity.py.
# NOT a runnable script -- it is parsed, never executed.
#
# The contract, asserted by test_the_dispatch_parser_ignores_mentions_and_keeps_calls:
#   * every op named `real*` MUST be harvested (a genuine dispatch)
#   * every op named `phantom*` MUST NOT be (a MENTION of cmd_op, not a call)
#
# WHY THIS EXISTS. The first version of the parser matched `\bcmd_op\s+(\w+)` on
# any line whose first non-space character was not `#`. MEASURED on the merged
# tree of PR #277 + PR #278 (tip a0a0021): #278 added a Python docstring inside a
# `python3 -c` block reading "The machine-readable cause, from `cmd_op stderr` it
# already emits" -- and the parser harvested a phantom wire op named `stderr`,
# turning the gate red with a diagnostic that sent the reader hunting for an op
# that does not exist. Invisible on either branch alone. The class of bug, not the
# word `stderr`, is what the cases below pin.
#
# 🔴 WHAT THIS RIG DELIBERATELY NO LONGER CONTAINS, and why. Five cases used to
# live here -- phantommultilinedq (a multi-line double-quoted string),
# phantomdocstring and phantomsqblock (mentions at column 0 inside a
# single-quoted `python3 -c` program body), phantomheredoc and
# phantomquotedheredoc (heredoc bodies, plain and `<<-`-with-quoted-delimiter).
# Each was rejected ONLY by `mask_shell_noncode`, a 129-line hand-rolled shell
# lexer that test_surface_parity.py used to carry. That lexer was DELETED after a
# 2x2 measurement over the real `browser` CLI showed it rejected nothing the
# command-position anchor did not already reject (19 ops parsed with it, 19
# without, identical names). Keeping those cases would pin behaviour the parser
# no longer has. The shapes are named here so the blind spot is on the record;
# reintroducing them means reintroducing a lexer, so re-run
# `claudedocs/browser-bridge-shell-masker-measurement.py` first.

# --- MUST be harvested: genuine dispatches, in real command position ---------

cmd_op realplain | pretty

  cmd_op realindented "\"url\":${url}" | pretty

resp="$(cmd_op realsubshell "$full")" || exit 1

if true; then cmd_op realafterthen; fi

check_token && cmd_op realafterand

false || cmd_op realafteror

( cmd_op realsubgroup )

foo; cmd_op realaftersemi

# --- MUST NOT be harvested: mentions -----------------------------------------

# cmd_op phantomcomment OP [EXTRA_JSON_FIELDS] -- the doc block of the function
#   cmd_op phantomindentedcomment -- an indented continuation of a comment

die "no answer from the extension -- retry with cmd_op phantomdq in the message"

echo 'inline note: cmd_op phantomsq is a mention, not a call'

# A backtick-quoted mention inside prose (the exact #278 shape).
printf '%s\n' "see \`cmd_op phantombacktick\` above"

# --- cases that isolate ONE defence each -------------------------------------
# Each of the two below exists because it is the ONLY case in this rig that the
# named defence rejects: delete that defence and this line, and only this line,
# leaks.

# UNQUOTED prose in live command context. A whole-line comment filter passes it
# through untouched -- ONLY the command-position anchor rejects it. (Mutation
# P5, "drop command-position anchoring", survived until this line existed.)
echo usage: cmd_op phantombareword OP [EXTRA]

# A WHOLE-LINE comment that quotes a dispatch verbatim, so the `$(` inside it
# puts the mention in apparent command position and the anchor ACCEPTS it --
# ONLY the whole-line comment filter rejects it. This is not hypothetical: it is
# the shape of `browser`'s own comment
#   # substitution (`resp="$(cmd_op nav ...)"`), and a subshell cannot write the
# which was the single mention the deleted shell lexer really was catching on
# the live CLI. It is harmless there only because `nav` happens to be a real op.
# substitution (`resp="$(cmd_op phantomcommentsubst ...)"`), harvested by anchor
