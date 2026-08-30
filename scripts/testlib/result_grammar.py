"""The RESERVED `RESULT:` verdict grammar — one definition, one selection rule.

WHY THIS EXISTS
---------------
`run-tests.sh` and `run-node-tests.sh` end with a single verdict line, emitted
from one writer behind an EXIT trap::

    RESULT: FAIL (exit=1)

That line is the gate's truth-telling channel: it is what survives the `| tail`
every consumer writes, and `scripts/gate.sh` cross-checks it against the process
status, refusing to vouch (exit 90) when the two disagree. See
`scripts/tests/test_gate_exit_truthfulness.py` for the incident that produced it.

The channel has one structural weakness. `run-tests.sh` runs its `HOOK_TESTS`
and `SHELL_TESTS` registries by inlining their stdout **directly into its own
stream** — no capture, no prefixing (unlike the pytest targets, whose output
pytest captures and only replays on failure). So any registry entry that prints
a line beginning `RESULT: PASS` or `RESULT: FAIL` at column 0 injects a line
that is byte-indistinguishable from the runner's own verdict, and always
*precedes* it.

`scripts/tests/test_cleanup_disk_gate.sh` did exactly that until the fourth
round of #1057's audit ladder: it printed `RESULT: PASS (exit=0)` and a red run
was read as green. It was fixed by adding a comment saying not to — which stops
nobody's next copy-paste. Hence this module and
`scripts/tests/test_result_grammar_is_reserved.py`.

THE SELECTION RULE, AND WHY IT IS HERE RATHER THAN AT EACH READER
-----------------------------------------------------------------
The runner's verdict is the LAST reserved-grammar line at column 0, because the
EXIT trap emits it after everything else. `gate.sh` already had this right::

    grep -aE '^RESULT: (PASS|FAIL)' "$log" | tail -1

`test_gate_exit_truthfulness.py` did not: it used `re.search(..., re.M)`, which
takes the FIRST match. Two open-coded copies of one predicate, disagreeing —
`claude/RULES.md` → "One rule, one place". `select_verdict` below is the single
Python definition; the guard file pins that gate.sh's shell copy still agrees.

🔴 Selection matches the LOOSE grammar (`RESULT: PASS`/`RESULT: FAIL`), not the
exit-carrying form, and that is load-bearing. A reader that selects on
`\\(exit=\\d+\\)` SKIPS a regressed bare `RESULT: FAIL` and keeps searching —
so a forged exit-carrying line elsewhere in the stream satisfies it, and the
regression coverage for "the verdict carries its exit code" passes on a line the
runner never wrote. Select loosely, then assert the shape.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

# The reserved grammar: a verdict line, anchored at column 0. Deliberately the
# same expression gate.sh greps for.
RESERVED_RE = re.compile(r"^RESULT: (PASS|FAIL)", re.M)

# The full, exit-carrying shape the runners are required to emit.
EXIT_CARRYING_RE = re.compile(r"^RESULT: (PASS|FAIL) \(exit=(\d+)\)$", re.M)

# The reserved PREFIX. A line emitting this at column 0 with any other payload
# is a near-miss, not a collision — see the guard's NEAR_MISSES pin.
RESERVED_PREFIX = "RESULT:"


class Verdict(NamedTuple):
    status: str            # "PASS" | "FAIL"
    exit_code: int | None  # None when the line is the regressed bare form
    line: str


def select_verdict(text: str) -> Verdict | None:
    """The runner's own verdict: the LAST reserved-grammar line at column 0.

    Mirrors `gate.sh`'s `grep -aE '^RESULT: (PASS|FAIL)' | tail -1`. Returns
    None when the stream carries no verdict at all — the truncation case, which
    a consumer must not read as a pass.
    """
    hits = [(ln, m) for ln in text.splitlines()
            if (m := RESERVED_RE.match(ln)) is not None]
    if not hits:
        return None
    line, status_m = hits[-1]
    status = status_m.group(1)
    exit_m = EXIT_CARRYING_RE.match(line)
    return Verdict(
        status=status,
        exit_code=int(exit_m.group(2)) if exit_m else None,
        line=line,
    )


# --------------------------------------------------------------------------- #
# The SOURCE scan: which lines would emit the reserved prefix at column 0?
# --------------------------------------------------------------------------- #
#
# Three shapes, because the hazard is "the emitted text starts with RESULT:",
# not "the source says echo". Each has its own positive control in the guard —
# including shape (c), which went one round with NO control at all while this
# comment already claimed it had one.
#
#   (a) a quoted literal opening with the prefix, optionally behind escaped
#       newlines — `echo "RESULT: …"`, `print("\nRESULT:", …)`. The `\n` case is
#       NOT hypothetical: scripts/claude-hooks/tests/test_bash_guard.py emits
#       `RESULT: all good` exactly that way, and a scan blind to it reports a
#       clean zero for a file that really does write the prefix at column 0.
#   (b) an UNQUOTED echo/printf argument — `echo RESULT: PASS`.
#   (c) a bare line at column 0 inside a heredoc. `\t*` because `<<-` strips
#       leading TABS at runtime, so a tab-indented heredoc line still lands at
#       column 0; spaces are never stripped by `<<-`, so they stay safe.
#       ⚠ KNOWN AND ACCEPTED FALSE POSITIVE, in the opposite direction: one line
#       cannot tell you its heredoc operator, so a tab-indented body under a
#       PLAIN `<<EOF` (where tabs are NOT stripped, so it emits at column 1 and
#       is harmless) is still reported as a COLLISION — which has no ledger, so
#       the only remedy would be editing the file. Accepted because it is
#       latent: no tab-indented `RESULT:` line exists under `scripts/`.
#       (Tab-indented lines DO exist there — the operative claim is the
#       narrower one, and the wider phrasing was measurably false.)
#       Prefer spaces in heredoc bodies. If this ever fires for real, the fix is
#       to make the tab-indented case pinnable, not to drop the `\t*`.
#
_ESC = r"(?:\\[nr])*"
_QUOTED = re.compile(rf"""(['"]){_ESC}{re.escape(RESERVED_PREFIX)}""")
_UNQUOTED = re.compile(
    rf"""\b(?:echo|printf|print)\b\s+(?:-\S+\s+)*{re.escape(RESERVED_PREFIX)}"""
)
_HEREDOC = re.compile(rf"""^\t*{re.escape(RESERVED_PREFIX)}""")

# A whole-line source comment is PROSE, not an emission. Without this the scan
# fires on the very comment that documents the hazard — and there is one:
# scripts/tests/test_cleanup_disk_gate.sh:206 is the #1057 warning this guard
# exists to make structural, and it escaped only because it happened to use
# backticks. Rewording it with double quotes would have turned a required check
# permanently red with no remedy but rewording someone's prose.
_COMMENT = re.compile(r"^\s*#")

# --- PAYLOAD CLASSIFICATION ---------------------------------------------------
# Three outcomes, not two, and BENIGN is a WHITELIST.
#
# 🔴 The first version of this enumerated the DYNAMIC shapes (`%s`, `{`, `$`) and
# called everything else benign. That set is unbounded, and an audit found three
# ordinary spellings it missed — `print("RESULT: " + v)`, `print("RESULT: ".join(p))`
# and `` echo "RESULT: `cat v`" `` — each reported to the operator as "provably
# harmless" while emitting a real forged verdict. Enumerating what is DANGEROUS
# regenerates that bug on the next spelling nobody thought of.
#
# So for the QUOTED arm the rule is inverted: a payload is BENIGN only when this
# module can PROVE the whole emitted string is a CLOSED LITERAL — the quote
# closes on this line, the text inside carries no interpolation marker, and
# nothing after the closing quote can append to what gets printed. Everything
# else is DYNAMIC, pinnable only by a human enumeration.
#
# 🔴 THAT WHITELIST IS QUOTED-ONLY, AND SAYING OTHERWISE IS THE DEFECT THIS
# MODULE EXISTS TO CATCH. An earlier revision's docstring and commit message
# both claimed "unknown spellings now fail SAFE" without qualification; the
# unquoted-command and heredoc arms below were, and still are, a NAMED
# BLACKLIST — they return BENIGN by default and escape only on an enumerated
# set. That is a real width difference, it was found by mutating an arm that NO
# fixture reached (both `return BENIGN` and `return DYNAMIC` survived a green
# suite), and it is now stated here rather than implied away. The blacklist has
# since been widened to the measured hazard — shell control operators, which let
# `echo RESULT: ok && echo RESULT: PASS` emit a forged verdict — and both arms
# now have fixtures that actually reach them.
_LITERAL_VERDICT = re.compile(r"""\s*\\?["']?\s*(PASS|FAIL)\b""")

# Anything that can splice a runtime value into a literal: shell/Make expansion
# and command substitution (`$`, `` ` ``), an f-string or `.format` hole (`{`),
# a printf/%-format placeholder (`%`).
_INTERPOLATION = re.compile(r"""[$`{%]""")

# What may follow the closing quote for the emission to still be a closed
# literal: nothing, a bracket/paren/terminator, or a trailing comment. A `+`, a
# `,` introducing another argument, or a `.join(`/`.format(` call all append to
# what is printed, so any of them means the payload is NOT decidable here.
#
# ⚠ ACCEPTED OVER-CONSERVATISM: this also rejects closed literals with ordinary
# trailing content — `echo "RESULT: 3 problems" >&2`, `print("RESULT: done",
# file=sys.stderr)`, `print(…, flush=True)` — and `_INTERPOLATION` likewise
# rejects a literal `%` or `{` in the payload (`RESULT: 50% coverage`). Those
# are closed literals with nothing to enumerate, so a DYNAMIC_PAYLOADS pin would
# be vacuous and would dilute a ledger that is supposed to carry real human
# enumerations. Measured impact today: ZERO inside the two registries (11
# repo-wide, all in prose or assignments outside the scanned population). The
# error is in the FAIL-SAFE direction, which is why it is accepted rather than
# tuned — but if it starts firing on ordinary summary lines, widen this class
# rather than teaching people to pin reflexively.
#
# ⚠ SECOND ACCEPTED IMPRECISION, same direction: the separator split is
# QUOTE-BLIND, so a `;`, `|` or `&` INSIDE a quoted literal cuts the string and
# breaks the quote-closing proof, giving DYNAMIC. Measured examples —
# `echo "RESULT: a & b"`, `echo "RESULT: see http://x/?a=1&b=2"`,
# `echo "RESULT: 3 problems; and counting"`. All are closed literals reported as
# unprovable. Zero live impact in the scanned population, pinnable in
# DYNAMIC_PAYLOADS, and fail-SAFE — a revision of this comment claimed the split
# was "precise in both directions", which it is not.
_TERMINAL_AFTER_QUOTE = re.compile(r"""^[)\];\s]*(?:\#.*)?$""")

# 🔴 TWO DIFFERENT RELATIONSHIPS, and collapsing them into one "separator" list
# was a fail-OPEN bug.
#
# `;` and `&` start an INDEPENDENT command: each is judged on its own and the
# line takes the worst verdict.
_SEPARATOR = re.compile(r"""[;&]""")

# `|`, `>(…)` and `<(…)` are NOT independent. A pipe's downstream stage INHERITS
# stdout and REWRITES the upstream stream, and a process substitution runs a
# second emitter — neither needs the literal `RESULT:` token to put a verdict at
# column 0. Measured under bash, all of these really emit `RESULT: PASS`:
#
#     echo RESULT: ok | sed 's/ok/PASS/'
#     echo RESULT: PASX | tr X S
#     echo RESULT: ok | awk '{sub(/ok/,"PASS"); print}'
#     echo RESULT: ok > >(echo RESULT: PASS)
#     cat <(echo RESULT: ok) <(echo RESULT: PASS)
#
# An earlier revision called a pipe "a separator, not a hazard: this emits
# `RESULT: ok` and nothing else" — true of `tee`, FALSE of `sed`/`awk`/`tr`, and
# it reported every line above as provably harmless. Two of them had been
# COLLISION one revision earlier, so it was a strict regression. This is the one
# place in this module where being wrong is fail-OPEN, which is why the chain
# can only ever return COLLISION or DYNAMIC — never BENIGN.
_CHAIN = re.compile(r"""\||>\(|<\(""")

COLLISION = "collision"
DYNAMIC = "dynamic"
BENIGN = "benign"

# Ranked so a line holding several commands is judged by its worst one.
_SEVERITY = {BENIGN: 1, DYNAMIC: 2, COLLISION: 3}


def line_emits_reserved_prefix(line: str) -> bool:
    """True when this source line would put `RESULT:` at column 0 of stdout."""
    if _COMMENT.match(line):
        return False
    return bool(_QUOTED.search(line) or _UNQUOTED.search(line)
                or _HEREDOC.match(line))


def classify_payload(line: str) -> str | None:
    """What does this line put after the reserved prefix?

    `None`          — it emits nothing at column 0.
    `COLLISION`     — a literal `PASS`/`FAIL`. Indistinguishable from a verdict.
    `DYNAMIC`       — this module CANNOT PROVE what gets printed. 🔴 That is not
                      "probably fine": `printf "RESULT: %s" "$verdict"` and
                      `` echo "RESULT: `cat v`" `` both emit a real forged
                      verdict. The DEFAULT for the quoted arm, so an
                      unrecognised spelling fails safe.
    `BENIGN`        — PROVED a closed literal that is not `PASS`/`FAIL`.

    🔴 A line can run SEVERAL commands, and the forgery hides in the second:
    `echo RESULT: ok && echo RESULT: PASS` really puts `RESULT: PASS` at column
    0 (measured with `cat -A`). Each command is judged on its own and the line
    takes the WORST verdict.
    """
    if not line_emits_reserved_prefix(line):
        return None
    # `;`/`&` give INDEPENDENT commands. No fallback for "no separator" is
    # needed and none is written: `RESULT:` contains no separator character, so
    # `split` can never cut the token, and a separator-free line simply comes
    # back as a one-element list. An earlier revision carried an `if not
    # commands` branch here that NOTHING could reach — a poison mutant in it
    # survived a full green run.
    worst = None
    for command in _SEPARATOR.split(line):
        if RESERVED_PREFIX not in command:
            continue
        verdict = _classify_chain(command)
        if worst is None or _SEVERITY[verdict] > _SEVERITY[worst]:
            worst = verdict
    return worst


def _classify_chain(command: str) -> str:
    """One command, which may be a PIPELINE or carry a process substitution.

    🔴 Never returns BENIGN. A downstream pipe stage rewrites the stream and a
    process substitution runs a second emitter, so nothing about the emitted
    text is provable from the source — the most this can say is "a stage spells
    a literal verdict" (COLLISION) or "cannot prove" (DYNAMIC).
    """
    if not _CHAIN.search(command):
        return _classify_one_command(command)
    for fragment in _CHAIN.split(command):
        if RESERVED_PREFIX not in fragment:
            continue
        after = fragment.split(RESERVED_PREFIX, 1)[1]
        if _LITERAL_VERDICT.match(after):
            return COLLISION
    return DYNAMIC


def _classify_one_command(command: str) -> str:
    """Classify ONE command — no separators inside it by construction."""
    after = command.split(RESERVED_PREFIX, 1)[1]
    if _LITERAL_VERDICT.match(after):
        return COLLISION

    quoted = _QUOTED.search(command)
    if quoted:
        quote = quoted.group(1)
        close = after.find(quote)
        if close == -1:
            return DYNAMIC              # the quote never closes here
        inside, rest = after[:close], after[close + 1:]
        if _INTERPOLATION.search(inside):
            return DYNAMIC              # a runtime value is spliced in
        if not _TERMINAL_AFTER_QUOTE.match(rest):
            return DYNAMIC              # `+ v`, `, v`, `.join(…)` — appends
        return BENIGN

    # Unquoted command, or a heredoc body line: the rest IS the payload, so it
    # is literal text unless something interpolates into it. 🔴 A NAMED
    # BLACKLIST, not the whitelist above — see the module docstring.
    return DYNAMIC if _INTERPOLATION.search(after) else BENIGN


def line_is_collision(line: str) -> bool:
    """True only for a literal `PASS`/`FAIL` payload.

    🔴 A `False` here does NOT mean "safe" — see `classify_payload`. Callers
    that need "is this line proven harmless" must check for `BENIGN`, never
    `not line_is_collision(...)`.
    """
    return classify_payload(line) == COLLISION


def scan_source(path: Path) -> list[tuple[int, str]]:
    """Every line of `path` that would emit the reserved prefix at column 0."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [(n, ln.rstrip("\n"))
            for n, ln in enumerate(text.splitlines(), 1)
            if line_emits_reserved_prefix(ln)]


# --------------------------------------------------------------------------- #
# Offender factories for the guard's positive controls. They live HERE so the
# guard file never has to spell a literal its own scan would flag.
# --------------------------------------------------------------------------- #

def offending_shell_line() -> str:
    """A shell emission that COLLIDES with the reserved grammar."""
    return 'echo "' + RESERVED_PREFIX + ' PASS (exit=0)"'


def offending_python_escape_line() -> str:
    """Shape (a) behind an escaped newline — the real `test_bash_guard.py`
    shape, and the one a naive `"RESULT:` needle cannot see."""
    return 'print("\\n' + RESERVED_PREFIX + ' FAIL (exit=1)")'


def offending_unquoted_line() -> str:
    """Shape (b) — no quotes at all."""
    return "echo " + RESERVED_PREFIX + " PASS"


def offending_heredoc_line() -> str:
    """Shape (c) — a bare heredoc body line, already at column 0."""
    return RESERVED_PREFIX + " PASS (exit=0)"


def offending_tab_heredoc_line() -> str:
    """Shape (c) under `<<-`, which strips leading TABS at runtime — so this
    lands at column 0 despite being indented in the source."""
    return "\t" + RESERVED_PREFIX + " PASS (exit=0)"


def dynamic_payload_printf_line() -> str:
    """A REAL forgery the literal check cannot see: the payload is a format
    placeholder, so it emits `RESULT: PASS (exit=0)` whenever the variable
    holds `PASS`. Must classify as DYNAMIC, never BENIGN."""
    return 'printf "' + RESERVED_PREFIX + ' %s (exit=%d)\\n" "$verdict" "$rc"'


def dynamic_payload_separate_arg_line() -> str:
    """The payload is a separate argument, so the source cannot decide it —
    `print("RESULT:", "PASS" if bad else "FAIL")` is a forgery in both branches."""
    return 'print("' + RESERVED_PREFIX + '", "PASS" if bad else "FAIL")'


def dynamic_payload_concat_line() -> str:
    """🔴 Missed by the first (blacklist) classifier: `+` concatenation carries
    no interpolation marker at all, so it was reported as provably benign."""
    return 'print("' + RESERVED_PREFIX + ' " + verdict)'


def dynamic_payload_join_line() -> str:
    """🔴 Also missed: the literal is a JOINER, and the printed text is the
    argument list."""
    return 'print("' + RESERVED_PREFIX + ' ".join(parts))'


def dynamic_payload_backtick_line() -> str:
    """🔴 Also missed: shell command substitution. `cat verdict.txt` printing
    `PASS` makes this a live forgery."""
    return 'echo "' + RESERVED_PREFIX + ' `cat verdict.txt`"'


def dynamic_payload_unclosed_quote_line() -> str:
    """The quote does not close on this line, so nothing about the payload is
    decidable from it."""
    return 'echo "' + RESERVED_PREFIX + ' continued on the next line...'


# One fixture per INTERPOLATION marker, each carrying that marker and no other
# dynamic signal — so a mutant deleting one alternative cannot be killed by a
# different arm catching the same fixture. That is exactly how three such
# mutants survived a fully green suite one round ago.
INTERPOLATION_FIXTURES = {
    "$":  'echo "' + RESERVED_PREFIX + ' $verdict"',
    "`":  'echo "' + RESERVED_PREFIX + ' `cat v`"',
    "{":  'print(f"' + RESERVED_PREFIX + ' {verdict}")',
    "%":  'printf "' + RESERVED_PREFIX + ' %s"',
}

# One fixture per APPEND shape — the arm that has no marker at all and is caught
# only by `_TERMINAL_AFTER_QUOTE`.
APPEND_FIXTURES = {
    "concat":       'print("' + RESERVED_PREFIX + ' " + v)',
    "join":         'print("' + RESERVED_PREFIX + ' ".join(p))',
    "another-arg":  'print("' + RESERVED_PREFIX + '", v)',
}


# The UNQUOTED-command and HEREDOC arms. 🔴 No fixture reached them for a whole
# round, so both `return BENIGN` and `return DYNAMIC` mutants survived a fully
# green suite — a branch nothing executes cannot be verified by anything.
FALLBACK_FIXTURES = {
    # 🔴 Each isolates ONE clause. Fixtures carrying two signals let a mutant
    # die to the wrong clause — measured twice in this ladder.
    "unquoted-var":      ("echo " + RESERVED_PREFIX + " $v", DYNAMIC),
    "unquoted-literal":  ("echo " + RESERVED_PREFIX + " ok", BENIGN),
    # heredoc body: literal TEXT, so a separator there is printed, not run.
    "heredoc-literal":   (RESERVED_PREFIX + " 3 problems; and counting", BENIGN),
    "heredoc-var":       (RESERVED_PREFIX + " $verdict", DYNAMIC),
}

# `;` and `&` start an INDEPENDENT command. Each fixture is a two-command line
# whose SECOND command spells a literal verdict; drop that character from
# `_SEPARATOR` and the line stops splitting, becomes one command whose payload
# starts ` ok …`, and is reported BENIGN. So every character has its own killer.
#
# ⚠ `or-or` is deliberately NOT in this list. `echo X || echo Y` SHORT-CIRCUITS
# — the first command succeeds, so the second never runs and the line emits only
# `RESULT: ok`. An earlier revision asserted every fixture here was "a real
# two-command forgery: the second command emits a literal verdict at column 0",
# which was measurably false for that one. It lives in CHAIN_FIXTURES, where the
# claim is the weaker and true one: `|` makes the line unprovable.
SEPARATOR_FIXTURES = {
    "semicolon": "echo " + RESERVED_PREFIX + " ok; echo " + RESERVED_PREFIX + " PASS",
    "ampersand": "echo " + RESERVED_PREFIX + " ok & echo " + RESERVED_PREFIX + " PASS",
    "and-and":   "echo " + RESERVED_PREFIX + " ok && echo " + RESERVED_PREFIX + " PASS",
}

# `|`, `>(…)` and `<(…)` do NOT give independent commands — a downstream stage
# rewrites the stream, a process substitution runs a second emitter. A stage
# spelling a literal verdict makes the line a COLLISION.
CHAIN_FIXTURES = {
    "pipe":        "echo " + RESERVED_PREFIX + " ok | echo " + RESERVED_PREFIX + " PASS",
    "or-or":       "echo " + RESERVED_PREFIX + " ok || echo " + RESERVED_PREFIX + " FAIL",
    "procsub-out": "echo " + RESERVED_PREFIX + " ok > >(echo " + RESERVED_PREFIX + " PASS)",
    "procsub-in":  "cat <(echo " + RESERVED_PREFIX + " ok) <(echo "
                   + RESERVED_PREFIX + " PASS)",
}

# 🔴 THE FAIL-OPEN CASES. No stage spells a verdict, yet bash really writes
# `RESULT: PASS` at column 0 — the downstream stage REWRITES the upstream text.
# A revision that called a pipe "a separator, not a hazard" reported every one of
# these as provably harmless. They must be DYNAMIC: not provable, never BENIGN.
CHAIN_TRANSFORM_FIXTURES = {
    "sed":    "echo " + RESERVED_PREFIX + " ok | sed 's/ok/PASS/'",
    "tr":     "echo " + RESERVED_PREFIX + " PASX | tr X S",
    "awk":    "echo " + RESERVED_PREFIX + " ok | awk '{sub(/ok/,\"PASS\"); print}'",
    "tee":    "echo " + RESERVED_PREFIX + " ok | tee f",
    "procsub-transform": "echo " + RESERVED_PREFIX + " ok > >(sed 's/ok/PASS/')",
}

# Lines that MENTION the grammar a second time without running a second
# command. 🔴 These were COLLISIONs — which have no ledger — under a rule that
# asked "is there another RESULT: anywhere on the line". The remedy would have
# been editing someone's prose, the exact hazard the whole-line comment
# exemption exists to prevent.
SECOND_MENTION_BENIGN = {
    "trailing-comment": 'echo "' + RESERVED_PREFIX + ' 3 problems"  # never print '
                        + RESERVED_PREFIX + ' PASS here',
    "inside-the-string": 'echo "' + RESERVED_PREFIX + ' see the '
                         + RESERVED_PREFIX + ' PASS docs"',
}


def benign_line() -> str:
    """Emits the prefix with a LITERAL non-verdict payload. Provably harmless,
    and the only shape the near-miss ledger may pin."""
    return 'echo "' + RESERVED_PREFIX + ' 3 problems"'


def comment_line() -> str:
    """Prose documenting the hazard, not an emission. Must not be flagged."""
    return '# DO NOT print "' + RESERVED_PREFIX + ' PASS (exit=0)" here.'
