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
planted positive control is exercised BY that control and does not flag.

🔴 A FLAG IS EVIDENCE, NOT A VERDICT, AND IT HAS THREE READINGS -- ONLY TWO OF
WHICH ARE DEFECTS:
  (a) dead recognition code -- no corpus instance and no test -> delete it;
  (b) a reporting branch with NO positive control -> the battery does not
      cover what its comment says it covers;
  (c) NOT A DEFECT: the branch runs somewhere this tracer cannot see -- most
      often because its test drives the guard through a SUBPROCESS.
Which one it is, is the call a human makes. It is not guessed here.
An earlier revision claimed a flag meant "exactly two things, and both are the
defect". That was itself the over-claiming-guard defect this module exists to
find: (c) is common -- 8 of this module's own tests drive their subject through
`subprocess.run` -- and a reviewer told there were only two readings would
mis-adjudicate every one of them.

🔴 STATED LIMITS, because a limit a reader can act on beats a branch that is
dead, unexercised and wrong:
  - NOT EVERY BRANCH IS ENUMERATED. `try/.../else:` and `async for/.../else:`
    bodies are absent -- only `ExceptHandler` and `For`/`While` orelse are
    walked. They are not reported clean; they are not reported at all.
  - LINE granularity. A sub-line branch -- `a and b`, a ternary, a
    comprehension `if` -- cannot be discriminated by line coverage and is NOT
    enumerated. It is not reported as clean; it is not reported at all.
  - Python only. A guard written in bash, TypeScript or Go is out of
    instrument. The registry records those explicitly rather than letting a
    silent absence read as coverage.
  - SUBPROCESSES ARE INVISIBLE. `sys.settrace` is per-interpreter, so a guard
    exercised only by spawning a child python reads as 100% dead. The caller
    refuses to publish a file whose executed-line count is ZERO for exactly
    this reason -- but a guard driven PARTLY in-process and partly by
    subprocess still under-reports, and nothing detects that.
  - THE TRACER CAN BE DISARMED BY THE CODE UNDER TEST. `sys.settrace` is one
    global slot; any test that clears it costs this instrument every line
    after that point. The plugin re-arms per test AND reports `clobbered`, and
    the caller refuses to publish when it is set.
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
    toks = tokenize.generate_tokens(io.StringIO(src).readline)
    for tok in toks:
        if tok.type != tokenize.COMMENT:
            continue
        m = _JUSTIFY_RE.search(tok.string)
        if m:
            out[tok.start[0]] = m.group("reason").strip()
    # A file that will not tokenize cannot be judged -- the exception is left to
    # propagate and the caller reports it as undecidable. There is deliberately
    # no `except: raise` here: it was a no-op that existed only to hold this
    # comment, which is a branch that reads as handling and handles nothing.
    return out


def evaluate(path, src, executed):
    """Flags for one file: branch bodies with no executed line.

    `executed` is the set of line numbers reached during the run.

    🔴 A BRANCH IS TAKEN IF **ANY** LINE IN ITS BODY SPAN RAN, NOT MERELY ITS
    FIRST. This is pinned behaviour, not defensive width: a statement that
    emits NO BYTECODE -- `global x`, `nonlocal x` -- never produces a line
    event, so a body whose first statement is one of those has an untraceable
    first line while the rest of it plainly runs. Narrowing the span to the
    first line reports such a branch DEAD while it is executing.
    (An earlier revision claimed no reachable input distinguished the two and
    recorded the mutant as an expected survivor. That claim was wrong, and a
    `global` declaration is the counterexample; it is now a fixture.)

    🔴 WHERE A JUSTIFICATION MAY SIT. Exactly two places: the branch's own
    HEADER line, and the FIRST line of its body. Both narrower rules that were
    tried are wrong in a way that costs a reader something:

      * reading `cond_line` for EVERY kind silenced an `else` with a comment
        written about its `if` -- they share a condition line, so one comment
        resolved two branches. A silent false negative with no workaround.
      * restricting `cond_line` to `if-body` over-corrected and broke
        `except <E>:  # pragma: no cover - reason`, the idiomatic and
        coverage.py-compatible placement. It silently invalidated four
        justifications that already existed in the scanned repos' source and
        flipped them to unresolved flags in the committed census.
      * reading ANY line of the body span let a NESTED branch's comment resolve
        its PARENT: when a parent is dead its children are too, so there was no
        placement that justified the inner without silencing the outer --
        strictly worse than the defect being fixed.

    So: `cond_line` is read for every kind EXCEPT `else-body`, because
    `else-body` is the only kind whose header line introduces a SIBLING branch
    as well as its own.

    🔴 WHICH LINE IS `cond_line` DIFFERS BY KIND, and an earlier version of
    this docstring got two of them wrong. Measured:
      * `if-body`   -> the `if`/`elif` test line. Read.
      * `except`    -> the `except <E>:` line. Read -- the idiomatic placement.
      * `else-body` -> the `if` test line (shared with its sibling). NOT read.
      * `match-case`-> the first line of the case BODY, not the `case X:` line.
        So a comment on `case X:` resolves NOTHING.
      * `loop-else` -> the `for`/`while` line, not the `else:` line. So a
        comment on the loop's `else:` resolves NOTHING -- and one on the `for`
        line resolves the loop-else, which is a collision of the same family as
        the if/else one, in the other direction.
    For those two kinds, put the comment on the FIRST LINE OF THE BODY. That
    placement always works, for every kind.

    🔴 AND "the body's first line is owned by no nested branch" IS NOT QUITE
    TRUE: a nested loop's header IS its parent's body-first line, so
    `if a:` / `for x in xs:  # pragma` / `else:` resolves the outer `if-body`
    and the inner `loop-else` from one comment. Stated rather than fixed --
    every candidate fix so far has traded one collision for another, and this
    one fails CLOSED in the direction that matters least (it over-resolves a
    branch whose parent is already justified).
    """
    just = justifications(src)
    flags = []
    for b in branch_bodies(src, path):
        if any(ln in executed for ln in b.lines()):
            continue
        # 🔴 CARRY THE REASON FAITHFULLY, INCLUDING AN EMPTY ONE. Whether a
        # bare marker counts is decided in ONE place -- `unresolved` -- and
        # nowhere else. Three coercions used to enforce it independently
        # (`if not reason`, `reason or None`, and `unresolved`), so no single
        # mutation could flip the rule and the battery could not test it: the
        # duplicated-predicate shape this repo's rules call out, in the guard
        # written to find duplicated predicates.
        reason = just.get(b.cond_line) if b.kind != "else-body" else None
        if reason is None:
            reason = just.get(b.first_line)
        flags.append(Flag(path, b, reason))
    return flags


def unresolved(flags):
    r"""Flags with no justification -- the ones that fail the run.

    🔴 THE SOLE ARBITER of whether a marker counts, and it requires a WORD
    CHARACTER, not merely a non-empty string. `[:\s-]*` in the pattern strips
    the ASCII hyphen only, so `# pragma: no cover \u2014` (an EM DASH -- which is
    what every real justification site in this repo uses) yielded the reason
    "\u2014" and resolved the flag. A bare marker with a punctuation mark after it
    is still a bare marker.
    """
    return [f for f in flags
            if not (f.justified_reason and re.search(r"\w", f.justified_reason))]
