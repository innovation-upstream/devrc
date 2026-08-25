"""Branch-liveness analysis for guards: which branch bodies never run.

WHAT THIS ANSWERS
-----------------
A guard whose branches handle spellings nobody writes reads as safety while
providing none. `claude/RULES.md` -> "A guard's DESCRIPTION claims COVERAGE".
The remedy that worked (browser-bridge `_cli_timeout_violations`, PR #820) was
NOT hardening: delete the unexercised branches and state the limit. This module
is the measurement that finds them, so the next one is found by a command
instead of by four audit rounds.

HOW "ZERO CORPUS INSTANCES" IS OPERATIONALISED
----------------------------------------------
A branch body has zero corpus instances when it is never EXECUTED while the
guard runs against the real corpus AND against the guard's own test battery.
That is exact for a corpus-scanning guard and requires no inference about what
case a branch handles -- the alternative (count occurrences of the spelling the
branch matches) needs a heuristic reading of the branch's intent, which would
itself be an instance of the defect this module exists to find.

🔴 THE BATTERY HALF IS LOAD-BEARING, NOT A REFINEMENT. A guard's
VIOLATION-REPORTING branch has zero corpus instances precisely when the repo is
CLEAN -- that is the branch doing its job. Tracing the corpus alone would flag
it and recommend deleting the guard's firing path, which is worse than the
defect. Because the trace covers the whole test run, a reporting branch with a
planted positive control is exercised BY that control and does not flag. So a
flag means one of exactly two things, and both are the defect:
  (a) dead recognition code -- no corpus instance and no test -> delete it;
  (b) a reporting branch with NO positive control -> the battery does not
      cover what its comment says it covers.
Which of the two it is, is the one call a human makes. It is not guessed here.

🔴 STATED LIMITS, because a limit a reader can act on beats a branch that is
dead, unexercised and wrong:
  - LINE granularity. A sub-line branch -- `a and b`, a ternary, a
    comprehension `if` -- cannot be discriminated by line coverage and is NOT
    enumerated. It is not reported as clean; it is not reported at all.
  - Python only. A guard written in bash, TypeScript or Go is out of
    instrument. The registry records those explicitly rather than letting a
    silent absence read as coverage.
  - A guard must be INVOCABLE. One that cannot be run is reported UNDECIDABLE,
    never silently passed.
  - This measures the run you gave it. A branch reachable only under a
    different environment reads as dead here; that is why the resolution is
    "delete or justify at the site", under a human's eye, not an auto-fix.
"""

import ast
import io
import re
import tokenize
from dataclasses import dataclass

# A one-line justification at the site. `# pragma: no cover` is the spelling
# already in this repo (`scripts/tests/test_no_public_ips.py`), so it is reused
# rather than a second marker being minted for the same job. A REASON is
# required: a bare marker asserts nothing, and the whole class being fixed here
# is claims with nothing behind them.
_JUSTIFY_RE = re.compile(
    r"#\s*(?:pragma:\s*no\s*cover|dead-guard-ok)\b[:\s-]*(?P<reason>.*)$")

# `if __name__ == "__main__":` never runs under a test runner. Excluded
# mechanically -- an exact AST shape match, not a name heuristic -- because it
# is a module entry point, not a guard branch.
_MAIN_GUARD = "__name__"


@dataclass(frozen=True)
class Branch:
    kind: str          # if-body | else-body | except | match-case | loop-else
    cond_line: int     # the line carrying the condition (where a justification may sit)
    first_line: int    # first line of the body
    last_line: int     # last line of the body
    snippet: str       # the body's first line, stripped, for the census

    def lines(self):
        return range(self.first_line, self.last_line + 1)


@dataclass(frozen=True)
class Flag:
    path: str
    branch: Branch
    justified_reason: str | None


def _is_main_guard(node):
    """`if __name__ == '__main__':` -- exact shape, not a text match."""
    t = node.test
    return (isinstance(t, ast.Compare)
            and isinstance(t.left, ast.Name) and t.left.id == _MAIN_GUARD
            and len(t.ops) == 1 and isinstance(t.ops[0], ast.Eq))


def _span(body):
    first = body[0].lineno
    last = max((getattr(s, "end_lineno", None) or s.lineno) for s in body)
    return first, last


def branch_bodies(src, filename="<src>"):
    """Every LINE-DISCRIMINABLE conditional branch body in `src`.

    Sub-line branches are deliberately absent -- see the module docstring's
    stated limits. `elif` needs no special case: ast nests it as an `If` inside
    `orelse`, so it is enumerated on its own walk step.
    """
    tree = ast.parse(src, filename=filename)
    lines = src.splitlines()

    def mk(kind, cond_line, body):
        first, last = _span(body)
        snip = lines[first - 1].strip() if 0 < first <= len(lines) else ""
        return Branch(kind, cond_line, first, last, snip[:100])

    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            if _is_main_guard(node):
                continue
            out.append(mk("if-body", node.test.lineno, node.body))
            # An `else` whose sole statement is an `If` IS an elif -- skip it
            # here; the walk reaches that If on its own and reports it as
            # `if-body` with its own condition line.
            if node.orelse and not (len(node.orelse) == 1
                                    and isinstance(node.orelse[0], ast.If)):
                out.append(mk("else-body", node.test.lineno, node.orelse))
        elif isinstance(node, ast.ExceptHandler):
            out.append(mk("except", node.lineno, node.body))
        elif isinstance(node, ast.match_case):
            out.append(mk("match-case", node.body[0].lineno, node.body))
        elif isinstance(node, (ast.For, ast.While)) and node.orelse:
            out.append(mk("loop-else", node.lineno, node.orelse))
    return sorted(out, key=lambda b: (b.first_line, b.kind))


def justifications(src):
    """{lineno: reason} for every one-line justification comment.

    Uses `tokenize`, not a regex over raw lines, so a `#` inside a string
    literal is not mistaken for a comment. That matters here: these guards are
    full of string literals containing the very patterns they scan for.
    """
    out = {}
    try:
        toks = tokenize.generate_tokens(io.StringIO(src).readline)
        for tok in toks:
            if tok.type != tokenize.COMMENT:
                continue
            m = _JUSTIFY_RE.search(tok.string)
            if m:
                out[tok.start[0]] = m.group("reason").strip()
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # A file that will not tokenize cannot be judged. The caller reports it
        # as undecidable; returning {} here would silently mean "unjustified".
        raise
    return out


def evaluate(path, src, executed):
    """Flags for one file: branch bodies with no executed line.

    `executed` is the set of line numbers reached during the run. A branch
    counts as TAKEN if ANY line in its body span ran -- not merely its first
    line -- because CPython's line attribution for a multi-line statement is
    not guaranteed to be the statement's first line.
    """
    just = justifications(src)
    flags = []
    for b in branch_bodies(src, path):
        if any(ln in executed for ln in b.lines()):
            continue
        reason = just.get(b.cond_line) or just.get(b.first_line)
        flags.append(Flag(path, b, reason or None))
    return flags


def unresolved(flags):
    """Flags with no justification -- the ones that fail the run."""
    return [f for f in flags if not f.justified_reason]
