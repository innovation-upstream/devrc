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
#       latent: no file under `scripts/` currently has a tab-indented line.
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
# So the rule is inverted: a payload is BENIGN only when this module can PROVE
# the whole emitted string is a CLOSED LITERAL — the quote closes on this line,
# the text inside carries no interpolation marker, and nothing after the closing
# quote can append to what gets printed. Everything else is DYNAMIC, which is
# pinnable but only by a human enumeration. Unknown spellings now fail SAFE.
_LITERAL_VERDICT = re.compile(r"""\s*\\?["']?\s*(PASS|FAIL)\b""")

# Anything that can splice a runtime value into a literal: shell/Make expansion
# and command substitution (`$`, `` ` ``), an f-string or `.format` hole (`{`),
# a printf/%-format placeholder (`%`).
_INTERPOLATION = re.compile(r"""[$`{%]""")

# What may follow the closing quote for the emission to still be a closed
# literal: nothing, a bracket/paren/terminator, or a trailing comment. A `+`, a
# `,` introducing another argument, or a `.join(`/`.format(` call all append to
# what is printed, so any of them means the payload is NOT decidable here.
_TERMINAL_AFTER_QUOTE = re.compile(r"""^[)\];\s]*(?:\#.*)?$""")

COLLISION = "collision"
DYNAMIC = "dynamic"
BENIGN = "benign"


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
                      verdict. The DEFAULT, so an unrecognised spelling fails
                      safe rather than being waved through.
    `BENIGN`        — PROVED a closed literal that is not `PASS`/`FAIL`.
    """
    if not line_emits_reserved_prefix(line):
        return None
    after = line.split(RESERVED_PREFIX, 1)[1]
    if _LITERAL_VERDICT.match(after):
        return COLLISION

    quoted = _QUOTED.search(line)
    if quoted:
        quote = quoted.group(1)
        close = after.find(quote)
        if close == -1:
            return DYNAMIC              # the quote never closes on this line
        inside, rest = after[:close], after[close + 1:]
        if _INTERPOLATION.search(inside):
            return DYNAMIC              # a runtime value is spliced in
        if not _TERMINAL_AFTER_QUOTE.match(rest):
            return DYNAMIC              # `+ v`, `, v`, `.join(…)` — appends
        return BENIGN

    # Unquoted `echo RESULT: …` or a heredoc body line: the rest of the line IS
    # the payload, so it is literal unless something interpolates into it.
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


def benign_line() -> str:
    """Emits the prefix with a LITERAL non-verdict payload. Provably harmless,
    and the only shape the near-miss ledger may pin."""
    return 'echo "' + RESERVED_PREFIX + ' 3 problems"'


def comment_line() -> str:
    """Prose documenting the hazard, not an emission. Must not be flagged."""
    return '# DO NOT print "' + RESERVED_PREFIX + ' PASS (exit=0)" here.'
