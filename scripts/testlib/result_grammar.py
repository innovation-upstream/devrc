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
# not "the source says echo". Each has its own positive control in the guard.
#
#   (a) a quoted literal opening with the prefix, optionally behind escaped
#       newlines — `echo "RESULT: …"`, `print("\nRESULT:", …)`. The `\n` case is
#       NOT hypothetical: scripts/claude-hooks/tests/test_bash_guard.py emits
#       `RESULT: all good` exactly that way, and a scan blind to it reports a
#       clean zero for a file that really does write the prefix at column 0.
#   (b) an UNQUOTED echo/printf argument — `echo RESULT: PASS`.
#   (c) a bare line already at column 0 inside a heredoc.
#
_ESC = r"(?:\\[nr])*"
_QUOTED = re.compile(rf"""(['"]){_ESC}{re.escape(RESERVED_PREFIX)}""")
_UNQUOTED = re.compile(
    rf"""\b(?:echo|printf|print)\b\s+(?:-\S+\s+)*{re.escape(RESERVED_PREFIX)}"""
)
_HEREDOC = re.compile(rf"""^{re.escape(RESERVED_PREFIX)}""")


def line_emits_reserved_prefix(line: str) -> bool:
    """True when this source line would put `RESULT:` at column 0 of stdout."""
    return bool(_QUOTED.search(line) or _UNQUOTED.search(line)
                or _HEREDOC.match(line))


def line_is_collision(line: str) -> bool:
    """True when the emitted payload is the RESERVED grammar itself.

    A collision is indistinguishable from a runner verdict. A line that emits
    the prefix with some other payload (`RESULT: all good`) is a near-miss:
    harmless to today's readers, one refactor away from a collision.
    """
    if not line_emits_reserved_prefix(line):
        return False
    after = line.split(RESERVED_PREFIX, 1)[1]
    return bool(re.match(r"""\s*\\?["']?\s*(PASS|FAIL)\b""", after))


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


def near_miss_line() -> str:
    """Emits the prefix, but not the reserved grammar. Not a collision."""
    return 'print("\\n' + RESERVED_PREFIX + '", "all good")'
