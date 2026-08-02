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

# A multi-line DOUBLE-quoted string. The mention below sits at column 0, i.e. in
# what looks like command position -- only quote tracking rejects it.
usage_text="
cmd_op phantommultilinedq OP [EXTRA]
"

# A multi-line SINGLE-quoted string: the `python3 -c '...'` shape from #278.
python3 -c '
def explain(kind):
    """Docs for the machine-readable cause.

cmd_op phantomdocstring is only ever mentioned here, never dispatched.
    """
    return kind
'

# A heredoc body. Unquoted at the character level, and the mention starts the
# line, so ONLY heredoc tracking rejects it.
cat <<EOF
cmd_op phantomheredoc OP [FIELDS] -- wire format reference
EOF

# An indented/tab-stripped heredoc with a QUOTED delimiter.
cat <<-'HELP'
	cmd_op phantomquotedheredoc -- also only documentation
HELP

# A backtick-quoted mention inside prose (the exact #278 shape).
printf '%s\n' "see \`cmd_op phantombacktick\` above"

# --- cases that isolate ONE defence each -------------------------------------
# The two below exist because a mutation sweep found the corresponding defence
# was NOT load-bearing on the earlier rig: removing it left the suite green.

# UNQUOTED prose in live command context. Quote/comment/heredoc masking all pass
# it through untouched -- ONLY the command-position anchor rejects it. (Mutation
# P5, "drop command-position anchoring", survived until this line existed.)
echo usage: cmd_op phantombareword OP [EXTRA]

# A single-quoted `python3 -c` block containing NO double quotes, with the
# mention at column 0 -- i.e. in apparent command position. ONLY single-quote
# masking rejects it. The earlier docstring case was being caught by accident:
# the `"""` in it toggled double-quote state, so removing single-quote masking
# left the suite green for the wrong reason. (Mutation P6.)
python3 -c '
cmd_op phantomsqblock is mentioned in a single-quoted program body
'
