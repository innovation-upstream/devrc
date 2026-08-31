"""STRUCTURAL guard: no `run-tests.sh` registry entry may forge a verdict line.

WHY
---
`run-tests.sh` inlines the stdout of its `HOOK_TESTS` and `SHELL_TESTS`
registries straight into its own stream — no capture, no prefixing
(`run-tests.sh` runs them as bare `python "$HOOK_TEST"` / `bash "$SHELL_TEST"`).
So a registry entry printing `RESULT: PASS (exit=0)` at column 0 puts a line
into the gate's truth-telling channel that is byte-indistinguishable from the
runner's own verdict, and always *precedes* it.

⚠ **The pytest targets are excluded because their TEST bodies are captured, NOT
because nothing in that tier reaches column 0.** An earlier revision said pytest
"captures their stdout and replays it only on failure" full stop, and that is
false for PLUGINS: the runner does `python -m pytest … >"$log" 2>&1; cat "$log"`,
and a plugin writing at import/session scope goes straight through — measured,
`scripts/testlib/gitenv_plugin.py:243` prints `gitenv(session) …` at column 0 in
every gate run. No plugin emits the reserved grammar today, so this is an
adjacent population that is UNSCANNED, not one that is safe by construction. The
node tier is genuinely safe: node's TAP reporter prefixes test stdout with `# `,
so a `.test.mjs` cannot reach column 0 (measured, top-level and in-test).

`scripts/tests/test_cleanup_disk_gate.sh` did exactly that until round 4 of
#1057's audit ladder, and a red run reported green. The fix there was a comment
saying not to do it — which binds that one file and nothing else. This guard is
the structural version: the population is read from the runner's OWN registry
arrays, so an entry added tomorrow is covered without anyone remembering.

WHAT THE HAZARD ACTUALLY IS, MEASURED
-------------------------------------
Two readers select the verdict out of the stream, and until this commit they
disagreed:

  * `gate.sh` takes `grep -aE '^RESULT: (PASS|FAIL)' | tail -1` — the LAST
    match, which is the runner's EXIT-trap line. Correct, and it carries a
    comment explaining the column-0 anchor.
  * `test_gate_exit_truthfulness.py` used `re.search(..., re.M)` — the FIRST
    match, and it required the exit-carrying form.

The first-match reader is the one that could go vacuously GREEN. Its regression
claim is "the runner emits a verdict carrying its own exit code". Had the EXIT
trap regressed to the bare `RESULT: FAIL` it emitted at origin/main, that regex
would have skipped the real line and matched a forged exit-carrying one from a
registry entry instead — certifying the deliverable against a line the runner
never wrote. Both readers now share `testlib.result_grammar.select_verdict`,
and `test_the_shell_reader_still_agrees_with_the_python_one` pins gate.sh to it.

THREE PAYLOAD CLASSES, NOT TWO
------------------------------
An earlier revision of this guard had two, and that was a real defect: it
classified any payload that was not a literal `PASS`/`FAIL` as a near-miss, and
told the operator in its own failure message that such a line "does not collide
today". For a payload the scanner cannot read, that sentence is false —
`printf "RESULT: %s (exit=%d)" "$verdict" "$rc"` emits a genuine forged verdict
whenever `$verdict` is `PASS`, and an operator obeying the message would have
pinned a live forgery and gone green. So:

  * **COLLISION** — a literal `PASS`/`FAIL`. Hard failure, not pinnable.
  * **DYNAMIC** — the payload is computed at runtime (format placeholder,
    f-string hole, variable, or a separate argument). Whether it collides
    **cannot be decided by reading the source**. Pinnable only in
    `DYNAMIC_PAYLOADS`, where the pin must ENUMERATE the possible values and say
    why none can be `PASS`/`FAIL`. The scanner is not asserting that — a human is.
  * **BENIGN** — a literal payload that is not `PASS`/`FAIL`. Provable by the
    scanner, and the only class `NEAR_MISSES` may pin.

🔴 **DYNAMIC exists because the forgeries and the one live benign line are the
SAME SHAPE, and no predicate can separate them by source text.** Measured:
`printf "RESULT: %s (exit=%d)" …`, `print(f"RESULT: {v}")`,
`print("RESULT:", "PASS" if bad else "FAIL")` and the live
`print("\\nRESULT:", "all good" if not fail else …)` are all "prefix + a payload
the scan cannot resolve". So the tempting fix — call every non-literal payload a
collision — makes this guard RED ON `main` with no remedy but editing
`test_bash_guard.py`, i.e. a permanently-red gate. That is why the third class is
pinnable and why its pin is a HUMAN enumeration rather than a scanner proof.
This is the guard's real boundary, stated rather than left as an implicit fit to
one file.

🔴 There is a live DYNAMIC emission today, pinned below, not hypothetical:
`scripts/claude-hooks/tests/test_bash_guard.py` ends with
`print("\\nRESULT:", "all good" if not fail else f"{fail} failure(s)")`, which
really does put `RESULT: all good` at column 0 of the runner's stream — verified
by running the file, and visible in the sandbox gate log two lines above the
runner's own verdict. gate.sh's comment cites this exact line as why its grep is
anchored and narrow.

HOW TO SATISFY IT
-----------------
Print anything else. A `PASS`/`FAIL` count is fine indented or behind any other
leading text (`  RESULT: 3 failure(s)`, `PASS 12  FAIL 0`). Do not add a ledger
entry to get green: `NEAR_MISSES` is for payloads the scanner PROVED benign, and
`DYNAMIC_PAYLOADS` is for payloads a human enumerated. Every entry must say why.

🔴 WHAT A GREEN RUN HERE CANNOT SEE
-----------------------------------
This is a SOURCE scan, so it sees a literal in the file it scans and nothing
else.

🔴 **THIS LIST IS NOT EXHAUSTIVE, AND READING IT AS EXHAUSTIVE HAS ALREADY COST
FIVE FAIL-OPENS.** Every one of them was a way of putting the prefix at column 0
that no bullet here named — an escape after the prefix, an escape before it, a
non-`\n` escape spelling, a pipe stage rewriting the stream, an interpolation
hole before the prefix. Each was found by EXECUTING a candidate and reading the
bytes back through gate.sh's own reader, never by reading this list. So: when
you need to know whether a shape is covered, run it and look — do not consult
these bullets and conclude. What is written here is what is KNOWN to be missing,
not the boundary of what is missing.

Known blind spots:

  * an emission whose PREFIX is built up across lines — `v="RESULT:"` on one
    line and `echo "$v PASS"` on another. Measured: `echo "$v PASS"` alone is
    not seen at all (no `RESULT:` literal, so no arm fires), and the assignment
    `v="RESULT:"` is seen but classified BENIGN. 🔴 An earlier revision of this
    bullet claimed the single-line form was "caught, as DYNAMIC" — false, and
    false in the UNSAFE direction, which is precisely the defect this file
    exists to catch. Stated correctly here after being measured;
  * runtime DEDENTING — `textwrap.dedent` strips common leading SPACES, so an
    indented literal inside a triple-quoted block can still reach column 0. The
    `<<-` tab case IS covered (see `_HEREDOC`); the space-stripping one is not,
    because deciding it needs the whole string, not the line;
  * a payload the scan can see but not read — reported as DYNAMIC and pinnable
    only by a human enumeration, which is an assertion, not a proof;
  * PROSE inside a triple-quoted docstring. Whole-line `#` comments are exempted;
    a docstring quoting `"RESULT: PASS"` with double quotes still reads as an
    emission and would have to be reworded or moved behind backticks;
  * a registry entry that SOURCES another file, whose emissions are that file's
    lines, not the entry's;
  * every population outside the two registries — including pytest PLUGINS,
    which reach column 0 today (see the header). Unscanned, not proven safe.

🔴 TWO MUTANTS ARE KNOWN TO SURVIVE THIS FILE, AND NEITHER IS CLOSED
--------------------------------------------------------------------
Recorded because a mutation sweep that reports only its kills is a claim about
the mutants someone imagined:

  * deleting the `NEAR_MISSES` filter clause from
    `test_every_benign_prefix_emission_is_pinned` survives. There are ZERO live
    BENIGN hits, so the clause iterates over nothing — an empty population
    cannot exercise a filter, and no assertion over the live tree can fix that.
    What IS closed is the predicate itself:
    `test_the_ledger_FILTER_works_regardless_of_what_the_live_tree_holds`
    exercises `_matches` on synthetic ledgers, so the filter is proven to work
    the moment a BENIGN hit appears. The clause's REACHABILITY is not proven,
    and saying otherwise would be the vacuous-guard shape this file is about.
  * rewriting the registry cross-check so both sides are the same computation
    survives — a comparison cannot detect being edited into a tautology. The
    check is nonetheless LIVE, which is the part that matters and was measured:
    breaking the regex to `\\.py"` makes the two extractions disagree (9 vs 5)
    and fails with its own message.

The scan is aimed at the shape that actually recurred: a copy-pasted literal.
`test_cleanup_disk_gate.sh` acquired it by copy-paste, and the fix that was left
behind was a comment. If an indirect emission ever appears, the honest response
is a BEHAVIOURAL check — run each entry and read its stdout — not a wider regex.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from testlib import result_grammar as G  # noqa: E402

RUNNER = REPO / "scripts" / "run-tests.sh"
RUNNER_SRC = RUNNER.read_text(encoding="utf-8")
GATE_SRC = (REPO / "scripts" / "gate.sh").read_text(encoding="utf-8")

REGISTRIES = ("HOOK_TESTS", "SHELL_TESTS")

# --- THE TWO PINNED LEDGERS ---------------------------------------------------
# (relative path, substring the line must contain, why it cannot collide).
# Both accounted BOTH ways, the same discipline as test_runtime_shebangs.py's
# ALLOWLIST: an unpinned hit fails, and a pin matching nothing fails.
#
# NEAR_MISSES is for BENIGN payloads — a literal the scanner itself proved is not
# PASS/FAIL. Nothing qualifies today; the list is live and deliberately empty,
# which the input guard below distinguishes from "the ledger stopped being read".
NEAR_MISSES: list[tuple[str, str, str]] = []

# 🔴 DYNAMIC_PAYLOADS is the weaker ledger and says so. The payload is computed
# at runtime, so the SCANNER proves nothing here — each entry is a HUMAN
# enumerating the reachable values. Pin one only after reading the code that
# produces them.
DYNAMIC_PAYLOADS = [
    ("scripts/claude-hooks/tests/test_bash_guard.py", '"all good"',
     "`print(\"\\nRESULT:\", \"all good\" if not fail else f\"{fail} failure(s)\")` "
     "— emits `RESULT: all good` at column 0, verified by running the file AND "
     "observed in a real sandbox gate log two lines above the runner's own "
     "verdict. The payload is a separate argument, so the scanner cannot read it; "
     "ENUMERATED by hand, it is exactly two values — the literal `all good`, and "
     "`f\"{fail} failure(s)\"` where `fail` is an int counter. Neither can be "
     "`PASS` or `FAIL`, so neither matches `^RESULT: (PASS|FAIL)`. gate.sh's own "
     "comment names this line as why that grep is anchored and narrow"),
]


def _array_body(name: str) -> str:
    """The text between `NAME=(` and its closing `)` in run-tests.sh, with
    comment lines removed.

    🔴 ROUND-3 FINDING NEW-3: the comment strip is load-bearing, not tidiness.
    `_bash_array` skips `#` lines and the cross-check's regex did not, so a
    comment merely MENTIONING a quoted script path made the two extractions
    disagree — firing "One of them is reading the wrong thing" and sending a
    debugger after a parser bug that does not exist. That is the exact hazard
    the assertion it guards was written to replace. Today's `SHELL_TESTS`
    comment escapes only because it happens to use backticks.
    """
    m = re.search(rf"^{name}=\((.*?)^\)", RUNNER_SRC, re.S | re.M)
    assert m, f"{name} not found in run-tests.sh — the scan is reading nothing"
    return "\n".join(ln for ln in m.group(1).splitlines()
                     if not ln.strip().startswith("#"))


def _bash_array(name: str) -> list[str]:
    """The entries of a `NAME=( … )` literal in run-tests.sh, unquoted.

    Same parser shape as test_no_real_launchers_all_targets.py — the registries
    are the population, so reading them from the runner is what makes this guard
    cover a future entry nobody remembers to add here.
    """
    m = re.search(rf"^{name}=\((.*?)^\)", RUNNER_SRC, re.S | re.M)
    assert m, f"{name} not found in run-tests.sh — the scan is reading nothing"
    out = []
    for raw in m.group(1).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line.strip('"'))
    return out


def _registry_entries() -> list[str]:
    return [e for name in REGISTRIES for e in _bash_array(name)]


def _hits() -> list[tuple[str, int, str]]:
    out: list[tuple[str, int, str]] = []
    for rel in _registry_entries():
        for lineno, line in G.scan_source(REPO / rel):
            out.append((rel, lineno, line))
    return sorted(out)


def _matches(entry, hit) -> bool:
    rel, needle, _why = entry
    return hit[0] == rel and needle in hit[2]


# --------------------------------------------------------------------------- #
# GUARD THE INPUT FIRST. Every assertion below is an empty-list assertion, and
# an empty list is what a scan wired to nothing also produces.
# --------------------------------------------------------------------------- #

def test_the_registries_are_non_empty_and_every_entry_exists():
    """A typo'd array name, or a renamed registry, silently empties the
    population and makes the whole file pass vacuously."""
    for name in REGISTRIES:
        entries = _bash_array(name)
        assert entries, f"{name} parsed as EMPTY — the scan covers nothing"
    missing = [e for e in _registry_entries() if not (REPO / e).is_file()]
    assert not missing, f"registry names a file that does not exist: {missing}"
    # 🔴 ROUND-2 FINDING N4. The first attempt compared `_registry_entries()`
    # against `sum(len(_bash_array(n)) …)` — the SAME computation, so the `==`
    # could never fail and the message claimed a parser check it did not do.
    # This cross-checks against a genuinely DIFFERENT extraction: count quoted
    # script paths inside each array body by regex, rather than by splitting
    # lines. It is a second reading, not an independent oracle — it would still
    # miss a hazard both share — but it does catch the line-splitting bugs the
    # message names.
    parsed = len(_registry_entries())
    by_regex = sum(
        len(re.findall(r'"[^"\n]+\.(?:py|sh)"', _array_body(n)))
        for n in REGISTRIES)
    assert parsed == by_regex, (
        f"two different extractions of the registries disagree: line-splitting "
        f"saw {parsed}, a quoted-path regex saw {by_regex}. One of them is "
        "reading the wrong thing.")
    assert parsed >= 8, f"only {parsed} registry entries — the scan covers ~nothing"


def test_the_scan_reads_real_bytes_from_those_files():
    """The files are found AND non-trivial. `scan_source` swallows OSError and
    returns [], so an unreadable path is indistinguishable from a clean one."""
    for rel in _registry_entries():
        assert (REPO / rel).stat().st_size > 200, f"{rel} is suspiciously small"


# --------------------------------------------------------------------------- #
# THE SCAN
# --------------------------------------------------------------------------- #

def test_no_registry_entry_forges_a_verdict_line():
    """🔴 THE GUARD. A collision is read as the runner's own verdict."""
    collisions = [h for h in _hits() if G.line_is_collision(h[2])]
    assert not collisions, (
        "a run-tests.sh registry entry emits the RESERVED verdict grammar at "
        "column 0 — it lands in the runner's stream and is indistinguishable "
        "from the runner's own verdict:\n  "
        + "\n  ".join(f"{p}:{n}: {l.strip()}" for p, n, l in collisions))


def test_no_registry_entry_emits_an_UNREADABLE_payload_unpinned():
    """🔴 THE FINDING FROM ROUND 1, as a test.

    A DYNAMIC payload is one the scanner CANNOT read — and the earlier revision
    reported exactly this population with the words "it does not collide today",
    which is false: `printf "RESULT: %s (exit=%d)" "$verdict" "$rc"` forges a
    verdict whenever the variable holds PASS. The message must say the payload
    is unknown, and the pin must come from a human enumeration.
    """
    unpinned = [h for h in _hits()
                if G.classify_payload(h[2]) == G.DYNAMIC
                and not any(_matches(e, h) for e in DYNAMIC_PAYLOADS)]
    assert not unpinned, (
        "a registry entry emits `RESULT:` at column 0 with a payload this scan "
        "CANNOT READ (a format placeholder, an f-string hole, a variable, or a "
        "separate argument). Whether it forges a verdict at runtime is NOT "
        "decidable here — do not assume it is benign. Either print a literal "
        "payload the scanner can check, or add a DYNAMIC_PAYLOADS pin that "
        "ENUMERATES the reachable values and says why none can be PASS/FAIL:\n  "
        + "\n  ".join(f"{p}:{n}: {l.strip()}" for p, n, l in unpinned))


def test_every_benign_prefix_emission_is_pinned():
    """The provably-harmless population. A literal non-verdict payload is still
    one refactor from a collision, so it is pinned with a reason rather than
    left to be rediscovered."""
    unpinned = [h for h in _hits()
                if G.classify_payload(h[2]) == G.BENIGN
                and not any(_matches(e, h) for e in NEAR_MISSES)]
    assert not unpinned, (
        "a registry entry emits `RESULT:` at column 0 with an unpinned literal "
        "payload. The scanner read it and it is not PASS/FAIL, so it cannot "
        "collide today — pin it with that reason, or print something else:\n  "
        + "\n  ".join(f"{p}:{n}: {l.strip()}" for p, n, l in unpinned))


def test_every_pin_in_both_ledgers_still_matches_something():
    """Stale-pin accounting, both ledgers. A pin that stops matching means the
    line moved or changed — left in place it pre-approves whatever appears
    there next."""
    hits = _hits()
    stale = [f"{name}: {rel} ~ {needle!r}"
             for name, ledger in (("NEAR_MISSES", NEAR_MISSES),
                                  ("DYNAMIC_PAYLOADS", DYNAMIC_PAYLOADS))
             for rel, needle, why in ledger
             if not any(_matches((rel, needle, why), h) for h in hits)]
    assert not stale, (
        "ledger entries match nothing — the site changed. Re-verify how that "
        "file prints its summary, then delete the pin:\n  " + "\n  ".join(stale))


# --------------------------------------------------------------------------- #
# POSITIVE CONTROLS. The three assertions above are ZEROS; a zero is
# indistinguishable from a scan wired to nothing (claude/RULES.md).
# --------------------------------------------------------------------------- #

def test_positive_control_the_scan_finds_a_quoted_shell_collision(tmp_path):
    p = tmp_path / "t.sh"
    p.write_text("set -e\n" + G.offending_shell_line() + "\n", encoding="utf-8")
    hits = G.scan_source(p)
    assert len(hits) == 1, f"scan is wired to nothing: {hits}"
    assert hits[0][0] == 2
    assert G.line_is_collision(hits[0][1])


def test_positive_control_the_scan_finds_an_escaped_newline_collision(tmp_path):
    """Shape (a) behind `\\n` — the REAL shape in the tree. A naive `"RESULT:`
    needle cannot see it and reports a clean zero for a file that emits the
    prefix at column 0."""
    p = tmp_path / "t.py"
    p.write_text("x = 1\n" + G.offending_python_escape_line() + "\n", encoding="utf-8")
    hits = G.scan_source(p)
    assert len(hits) == 1, f"scan cannot see an escaped-newline emission: {hits}"
    assert G.line_is_collision(hits[0][1])


def test_positive_control_the_scan_finds_an_unquoted_collision(tmp_path):
    p = tmp_path / "t.sh"
    p.write_text(G.offending_unquoted_line() + "\n", encoding="utf-8")
    hits = G.scan_source(p)
    assert len(hits) == 1, f"scan cannot see an unquoted emission: {hits}"
    assert G.line_is_collision(hits[0][1])


def test_positive_control_the_scan_finds_a_bare_heredoc_line(tmp_path):
    """🔴 Shape (c). It shipped one round with NO control while the module
    docstring already claimed every shape had one — and a mutant neutering the
    arm (`False and _HEREDOC.match(line)`) survived a fully green 15/15 suite."""
    p = tmp_path / "t.sh"
    p.write_text("cat <<EOF\n" + G.offending_heredoc_line() + "\nEOF\n",
                 encoding="utf-8")
    hits = G.scan_source(p)
    assert len(hits) == 1, f"scan cannot see a bare heredoc body line: {hits}"
    assert hits[0][0] == 2
    assert G.line_is_collision(hits[0][1])


def test_positive_control_the_scan_finds_a_TAB_indented_heredoc_line(tmp_path):
    """`<<-` strips leading TABS at runtime, so a tab-indented body line still
    lands at column 0 — invisible to a plain `^RESULT:` anchor."""
    p = tmp_path / "t.sh"
    p.write_text("cat <<-EOF\n" + G.offending_tab_heredoc_line() + "\nEOF\n",
                 encoding="utf-8")
    hits = G.scan_source(p)
    assert len(hits) == 1, f"scan cannot see a tab-stripped heredoc line: {hits}"
    assert G.line_is_collision(hits[0][1])


def test_positive_control_a_BENIGN_literal_is_seen_but_not_called_a_collision():
    """The classes must be DISTINGUISHED, not merged. A scan calling every
    prefix emission a collision would fail the live tree and get itself deleted;
    one calling none would miss the hazard."""
    benign = G.benign_line()
    assert G.line_emits_reserved_prefix(benign), "benign emission not seen at all"
    assert G.classify_payload(benign) == G.BENIGN, G.classify_payload(benign)


def test_positive_control_an_UNREADABLE_payload_is_never_classed_benign():
    """🔴 ROUND-1 AND ROUND-2 FINDINGS, pinned together.

    The first three were the round-1 defect. The last four are the round-2 one:
    the fix enumerated DANGEROUS shapes, and these carry no marker from that
    list, so all four were reported to the operator as provably harmless while
    emitting a real forged verdict. They are why BENIGN is now a whitelist.
    """
    for line in (G.dynamic_payload_printf_line(),
                 G.dynamic_payload_separate_arg_line(),
                 G.dynamic_payload_concat_line(),
                 G.dynamic_payload_join_line(),
                 G.dynamic_payload_backtick_line(),
                 G.dynamic_payload_unclosed_quote_line()):
        assert G.line_emits_reserved_prefix(line), f"not seen at all: {line}"
        assert G.classify_payload(line) == G.DYNAMIC, (
            f"a payload the scanner cannot read was classed "
            f"{G.classify_payload(line)!r}, not DYNAMIC: {line}")
        assert not G.line_is_collision(line), (
            "line_is_collision must stay LITERAL-only; DYNAMIC is carried by "
            "classify_payload so the two ledgers stay distinguishable")


@pytest.mark.parametrize("marker", sorted(G.INTERPOLATION_FIXTURES))
def test_each_interpolation_marker_has_its_OWN_control(marker):
    """🔴 ROUND-2 FINDING N2. Three mutants each deleting ONE alternative of the
    old dynamic regex all SURVIVED a fully green suite — every fixture carried
    two markers, so a different arm always killed it and the deleted one was
    never the reason. One fixture per marker, carrying that marker alone."""
    line = G.INTERPOLATION_FIXTURES[marker]
    others = [m for m in G.INTERPOLATION_FIXTURES if m != marker]
    payload = line.split(G.RESERVED_PREFIX, 1)[1]
    assert not any(o in payload for o in others), (
        f"the {marker!r} fixture also carries {[o for o in others if o in payload]} "
        "— a mutant deleting this alternative would die for the wrong reason")
    assert G.classify_payload(line) == G.DYNAMIC, (
        f"{marker!r} no longer forces DYNAMIC: {line}")


@pytest.mark.parametrize("shape", sorted(G.FALLBACK_FIXTURES))
def test_the_unquoted_and_heredoc_arms_are_REACHED_and_correct(shape):
    """🔴 ROUND-3 FINDING NEW-1. The fallback arm returns BENIGN by default, and
    for a whole round NO fixture reached it — so mutating it to `return BENIGN`
    AND to `return DYNAMIC` both survived a fully green suite. A branch nothing
    executes cannot be verified by anything.

    `echo RESULT: ok && echo RESULT: PASS` is the reachable consequence: bash
    really emits `RESULT: PASS` at column 0, and it was reported to the operator
    as provably harmless.
    """
    line, expected = G.FALLBACK_FIXTURES[shape]
    assert G.line_emits_reserved_prefix(line), f"not seen at all: {line}"
    assert G.classify_payload(line) == expected, (
        f"{shape!r} classified {G.classify_payload(line)!r}, expected "
        f"{expected!r}: {line}")


@pytest.mark.parametrize("op", sorted(G.SEPARATOR_FIXTURES))
def test_each_COMMAND_SEPARATOR_has_its_OWN_control(op):
    """🔴 ROUND-4 FINDING NEW-4. The first separator class had a killer for `|`
    ALONE — dropping `;`, `&`, `<` or `>` each survived a green suite. That is
    the same "one mutant per alternative" gap already closed for the
    interpolation class in round 2, reintroduced in the fix for round 3.

    Drop that character and the line stops splitting, becomes one command whose
    payload starts ` ok …`, and is reported BENIGN.
    """
    line = G.SEPARATOR_FIXTURES[op]
    assert G.classify_payload(line) == G.COLLISION, (
        f"{op!r} no longer splits the line, so the second command's literal "
        f"verdict is invisible: {line}")


@pytest.mark.parametrize("op", sorted(G.CHAIN_FIXTURES))
def test_a_chain_stage_spelling_a_verdict_is_a_collision(op):
    """`|`, `>(…)` and `<(…)` are not independent commands, but a stage that
    spells a literal verdict still forges one."""
    line = G.CHAIN_FIXTURES[op]
    assert G.classify_payload(line) == G.COLLISION, (
        f"{op!r} no longer exposes the chain stage's literal verdict: {line}")


@pytest.mark.parametrize("shape", sorted(G.SEVERITY_RUNG_FIXTURES))
def test_the_BENIGN_below_DYNAMIC_rung_is_pinned(shape):
    """🔴 ROUND-6 FINDING NEW-12, and the surviving mutant was fail-OPEN.

    Every separator fixture is benign-then-COLLISION, so `_SEVERITY`'s top rung
    was pinned and the lower one was not: flattening it to
    `{BENIGN: 1, DYNAMIC: 1, COLLISION: 3}` survived a fully green suite. With
    `v=PASS` the line below really writes `RESULT: PASS` at column 0, and the
    mutant reports it BENIGN.
    """
    line, expected = G.SEVERITY_RUNG_FIXTURES[shape]
    assert G.classify_payload(line) == expected, (
        f"a line mixing a BENIGN command with a {expected} one was reported "
        f"{G.classify_payload(line)!r} — the severity rung is not ordered: {line}")


def test_a_chain_stage_MENTIONING_a_verdict_is_not_a_collision():
    """🔴 ROUND-6 FINDING NEW-13. The chain path carried its OWN copy of the
    literal-verdict predicate and no fixture covered it: mutating that copy's
    `.match` to `.search` survived while the identical mutation in the other
    copy was killed. Direction is fail-safe, but COLLISION has no ledger, so the
    consequence is an unpinnable false positive whose only remedy is editing
    prose — round 4's NEW-5 hazard, on a path with no second-mention fixture.

    Both copies are now `_spells_a_verdict`; this covers the chain caller.
    """
    line = 'echo "' + G.RESERVED_PREFIX + ' see the PASS docs" | cat'
    assert G.classify_payload(line) == G.DYNAMIC, (
        "a chain whose payload merely MENTIONS a verdict word was reported "
        f"{G.classify_payload(line)!r}: {line}")


@pytest.mark.parametrize("shape", sorted(G.CHAIN_TRANSFORM_FIXTURES))
def test_a_transforming_chain_is_NEVER_reported_benign(shape):
    """🔴 ROUND-5 FINDING NEW-7 — the one fail-OPEN defect in this ladder.

    No stage here spells a verdict, yet for most of them bash really writes
    `RESULT: PASS` at column 0: the downstream stage REWRITES the upstream text.
    A revision that reasoned "a pipe is a separator, not a hazard: this emits
    `RESULT: ok` and nothing else" reported all of these as provably harmless —
    true of `tee`, false of `sed`/`awk`/`tr` — and the suite PINNED one as
    correct.

    Every other accepted imprecision in this module errs toward DYNAMIC. This
    was the only one that erred toward BENIGN, which is why the chain path can
    return COLLISION or DYNAMIC and nothing else.

    🔴 The shared property is asserted; the DIFFERING one is data, not prose —
    `tee` is in this list because a pipe is unprovable, NOT because it forges.
    A list-wide sentence claiming otherwise was false for exactly that member,
    the third time in this ladder a whole-list claim broke on one entry.
    """
    line, expected, really_forges = G.CHAIN_TRANSFORM_FIXTURES[shape]
    verdict = G.classify_payload(line)
    assert verdict != G.BENIGN, (
        f"a chain was reported PROVABLY HARMLESS; a downstream stage can rewrite "
        f"the stream, and for this one bash really forges={really_forges}: {line}")
    assert verdict == expected, (
        f"expected {expected!r} (not provable) for {shape!r}, got {verdict!r}: {line}")
    assert isinstance(really_forges, bool), (
        f"{shape!r} carries no ground-truth flag — the whole point of the third "
        "element is that this claim is data a test checks, not prose")


@pytest.mark.parametrize("shape", sorted(G.UNRESOLVED_ESCAPE_FIXTURES))
def test_an_unresolved_escape_payload_is_never_reported_benign(shape):
    """🔴 ROUND-8 FINDING NEW-15 — the SECOND fail-open in this module.

    The closed-literal proof is PER LINE. An escaped newline inside the payload
    starts another line at column 0 that the proof never looked at, so
    `printf "RESULT: ok\\nRESULT: FAIL\\n"` was reported BENIGN while bash really
    wrote `RESULT: FAIL` there. The failure message would then have told the
    operator it "cannot collide today — pin it with that reason", putting a real
    forgery into the ledger reserved for provably-harmless payloads.

    DYNAMIC rather than COLLISION because whether the escape is INTERPRETED
    depends on the emitter: `printf`/`echo -e` expand it, a bare `echo` does not
    — and the `bare-echo-literal` fixture is the one that forges nothing, which
    is why guessing COLLISION would be an unpinnable false positive.
    """
    line, expected, _really_forges = G.UNRESOLVED_ESCAPE_FIXTURES[shape]
    verdict = G.classify_payload(line)
    assert verdict != G.BENIGN, (
        f"an embedded-newline payload was reported PROVABLY HARMLESS; the proof "
        f"is per line and only the first line was proved: {line}")
    assert verdict == expected, (
        f"expected {expected!r} for {shape!r}, got {verdict!r}: {line}")


def test_the_unresolved_escape_ledger_covers_BOTH_answers():
    """The class exists because the module CANNOT tell an expanding emitter from
    a literal one. A ledger where every entry forges would not show that."""
    flags = {f for _line, _v, f in G.UNRESOLVED_ESCAPE_FIXTURES.values()}
    assert flags == {True, False}, (
        f"really_forges takes only {flags}; the `bare-echo` case is the whole "
        "reason this class is DYNAMIC rather than COLLISION")


@pytest.mark.parametrize("shape", sorted(G.BEFORE_PREFIX_ESCAPE_FIXTURES))
def test_a_BEFORE_prefix_escape_is_seen_and_never_reported_benign(shape):
    """🔴 ROUND-10 FINDING NEW-19 — the FOURTH fail-open, and the worst of them.

    `_ESC` named `\\n`/`\\r` AND required them to be quote-adjacent, so
    `print("checks done\\nRESULT: PASS (exit=0)")` reported that the line emits
    NOTHING at column 0 — `classify_payload` returned None, so the line never
    entered the population and was invisible to all four guard assertions at
    once. That is strictly worse than a wrong class: a BENIGN verdict would at
    least have demanded a pin.

    🔴 This test exists because adding the fixtures was NOT enough: with the
    ledger present but nothing asserting the VERDICT, removing the detection AND
    removing the classification both survived a green suite. Fixtures nothing
    consumes are the same defect as a branch nothing reaches.
    """
    line, expected, _forges = G.BEFORE_PREFIX_ESCAPE_FIXTURES[shape][:3]
    assert G.line_emits_reserved_prefix(line), (
        f"the scan does not SEE {shape!r} at all, so it is invisible to every "
        f"assertion in this file: {line}")
    verdict = G.classify_payload(line)
    assert verdict is not None, f"{shape!r} classified as emitting nothing: {line}"
    assert verdict != G.BENIGN, (
        f"{shape!r} was reported PROVABLY HARMLESS while the escape can put the "
        f"prefix at column 0: {line}")
    assert verdict == expected, (
        f"expected {expected!r} for {shape!r}, got {verdict!r}: {line}")


def test_the_before_prefix_fixtures_really_place_their_escape_BEFORE_the_prefix():
    """Guard the guard: if a fixture put its escape after the prefix it would be
    covered by UNRESOLVED_ESCAPE_FIXTURES instead, and this ledger would prove
    nothing about the half it was written for."""
    for shape, entry in G.BEFORE_PREFIX_ESCAPE_FIXTURES.items():
        line = entry[0]
        head = line.split(G.RESERVED_PREFIX, 1)[0]
        # An ESCAPE or an INTERPOLATION HOLE — both can put a line break before
        # the prefix, and the arm matches either. Checking only for a backslash
        # would exclude the interpolation fixtures from their own guard.
        assert any(c in head for c in (G.ESC, "$", "`", "{", "%")), (
            f"{shape!r} has no unresolved marker before the prefix, so it does "
            f"not exercise the before-prefix arm: {line}")


def _ground_truth_ledgers() -> dict:
    """Every ground-truth ledger in the module, found by SHAPE.

    🔴 ROUND-10 FINDING NEW-20. The first version keyed on a `_FIXTURES` name
    suffix AND on tuples of length exactly 3, while its docstring claimed to
    find "every dict carrying a 3-tuple ground-truth ledger". Measured, a ledger
    named `..._LEDGER`, and one carrying a fourth element documenting WHY the
    flag holds, both SURVIVED — each with an unmeasured `really_forges` flag,
    which is NEW-14 again. Both are plausible next edits.

    Keyed on the only thing that actually matters: a dict of tuples whose third
    element is a bool. That is the ground-truth flag, wherever it lives and
    whatever the dict is called.
    """
    return {name: obj for name, obj in vars(G).items()
            if isinstance(obj, dict) and obj
            and all(isinstance(v, tuple) and len(v) >= 3
                    and isinstance(v[2], bool) for v in obj.values())}


FORGE_TOOLS = ("bash", "python3", "sed", "tr", "awk", "tee")


def test_the_really_forges_flags_are_MEASURED_against_real_bash(tmp_path):
    """🔴 ROUND-7 FINDING NEW-14 — the defect reproduced inside its own fix.

    Round 7 moved the ground truth out of prose and into a `really_forges`
    field, and the commit message claimed "Corrupting a flag fails the suite; a
    wrong sentence never could". That was FALSE: the only assertion on the field
    was `isinstance(..., bool)`, a TYPE check. Measured by the audit, BOTH
    corruption directions survived a fully green suite — `tee` flipped to True
    (the exact false claim the field was introduced to kill) and `sed` flipped
    to False. An assertion that READS as verification while providing none is
    worse than none, because it stops anyone looking.

    So the flag is now what it always claimed to be: a MEASUREMENT. Each fixture
    is executed under real bash and its stdout matched against the module's own
    reserved grammar.

    🔴 A missing tool FAILS rather than skips. If `awk` is absent the flags are
    unverifiable, and a green run that silently checked nothing is the exact
    shape this whole file exists to prevent.
    """
    missing = [t for t in FORGE_TOOLS if shutil.which(t) is None]
    assert not missing, (
        f"cannot verify the ground-truth flags — missing {missing}. This is a "
        "FAILURE, not a skip: an unverifiable ledger read as verified is what "
        "NEW-14 was.")

    bash = shutil.which("bash")
    # 🔴 BOTH ground-truth ledgers. A second ledger carrying unmeasured flags is
    # NEW-14 again — the whole finding was a field that READ as verified.
    # 🔴 DISCOVERED, not listed — see the pin below. Listing them is what let a
    # ledger go unmeasured twice.
    ledger = {f"{name}/{k}": v
              for name, d in sorted(_ground_truth_ledgers().items())
              for k, v in d.items()}
    # 🔴 ROUND-9 FINDING NEW-18. Deleting a ledger from this merge SURVIVED a
    # green suite — the comment saying "a second ledger with unmeasured flags is
    # NEW-14 again" was prose, not a check, which is NEW-14's own shape one
    # level up. Pinned: every dict in the module carrying a 3-tuple ground-truth
    # ledger must be measured here, discovered rather than listed, so a THIRD
    # ledger is covered without anyone remembering.
    assert len(ledger) == sum(len(d) for d in _ground_truth_ledgers().values())
    assert len(_ground_truth_ledgers()) >= 3, (
        f"only {len(_ground_truth_ledgers())} ground-truth ledgers discovered; "
        "the module declares at least three. The discovery predicate is too "
        "narrow and a ledger is going unmeasured.")
    for shape, entry in sorted(ledger.items()):
        line, _verdict, really_forges = entry[0], entry[1], entry[2]
        # 🔴 A fixture declares its INTERPRETER when it is not a shell line.
        # The first version ran everything under bash, so `print("…")` produced
        # an EMPTY stdout and read as "does not forge" — a measurement that
        # silently measured nothing, which is the shape this test exists to
        # kill. Caught by this very assertion on a Python fixture.
        interp = entry[3] if len(entry) > 3 else "bash"
        # `wait` so a process substitution's output is flushed before bash exits;
        # cwd=tmp_path so `tee f` writes into the sandbox, not the repo.
        argv = ([shutil.which("python3"), "-c", line] if interp == "python"
                else [bash, "-c", line + "\nwait\n"])
        proc = subprocess.run(argv, cwd=str(tmp_path),
                              capture_output=True, text=True, timeout=60)
        observed = bool(G.RESERVED_RE.search(proc.stdout))
        assert observed == really_forges, (
            f"{shape!r} claims really_forges={really_forges} but bash actually "
            f"{'DID' if observed else 'did NOT'} write a reserved verdict at "
            f"column 0.\n  line: {line}\n  stdout: {proc.stdout!r}")

    # Positive + negative control on the DETECTOR itself: a zero from a matcher
    # wired to nothing is indistinguishable from a fixture that does not forge.
    forging = subprocess.run([bash, "-c", 'echo "RESULT: PASS (exit=0)"'],
                             cwd=str(tmp_path), capture_output=True, text=True,
                             timeout=60)
    assert G.RESERVED_RE.search(forging.stdout), (
        "the detector cannot see a verdict bash definitely emitted — every "
        "really_forges=False above would be a false negative")
    quiet = subprocess.run([bash, "-c", 'echo "RESULT: ok"'], cwd=str(tmp_path),
                           capture_output=True, text=True, timeout=60)
    assert not G.RESERVED_RE.search(quiet.stdout), (
        "the detector fires on a non-verdict, so every really_forges=True is "
        "unearned")


def test_the_forge_ledger_covers_BOTH_answers():
    """A ledger where every entry shares the same ground truth proves nothing
    about the field — the `tee` case is the whole reason it exists."""
    flags = {f for _line, _v, f in G.CHAIN_TRANSFORM_FIXTURES.values()}
    assert flags == {True, False}, (
        f"really_forges takes only {flags} across the chain fixtures; the field "
        "distinguishes nothing unless both answers are represented")


@pytest.mark.parametrize("shape", sorted(G.SECOND_MENTION_BENIGN))
def test_a_second_MENTION_without_a_second_command_is_not_a_collision(shape):
    """🔴 ROUND-4 FINDING NEW-5. Judging by "is there another RESULT: anywhere
    on the line" made a trailing comment, and a prefix quoted INSIDE the
    payload, into COLLISIONs — which have no ledger, so the only remedy was
    editing someone's prose. That is the very hazard the whole-line comment
    exemption exists to prevent, and the historical remedy for this entire bug
    class was "add a comment saying not to do it".

    Splitting into COMMANDS rather than scanning the whole line is what makes
    these correct: neither runs a second command.
    """
    line = G.SECOND_MENTION_BENIGN[shape]
    assert G.classify_payload(line) != G.COLLISION, (
        f"a benign second mention was reported as an unpinnable COLLISION: {line}")


def test_array_body_strips_comments_so_the_cross_check_cannot_false_fire():
    """🔴 ROUND-4 FINDING NEW-6. The NEW-3 fix shipped with nothing that would
    go red if it regressed — reverting the comment strip left the suite green,
    and it only went red when an auditor planted the comment by hand.

    A comment merely MENTIONING a quoted script path makes the two extractions
    disagree and fires "One of them is reading the wrong thing", sending a
    debugger after a parser bug that does not exist.
    """
    body = _array_body("SHELL_TESTS")
    assert not any(ln.strip().startswith("#") for ln in body.splitlines()), (
        "_array_body returned comment lines, so the quoted-path cross-check "
        "counts paths that `_bash_array` skips")
    # The live SHELL_TESTS comment block must actually contain a comment, or
    # this test passes vacuously against a body that never had one.
    raw = re.search(r"^SHELL_TESTS=\((.*?)^\)", RUNNER_SRC, re.S | re.M)
    assert raw and any(ln.strip().startswith("#")
                       for ln in raw.group(1).splitlines()), (
        "SHELL_TESTS has no comment lines any more, so this test no longer "
        "exercises the strip — re-point it at a registry that does")


def test_the_fallback_fixtures_actually_reach_the_fallback_arm():
    """Guard the guard above: if every fixture were quoted, the parametrized
    test would pass while still never executing the arm it names."""
    for shape, (line, _expected) in G.FALLBACK_FIXTURES.items():
        assert not G._QUOTED.search(line), (
            f"{shape!r} opens the prefix with a QUOTE, so it takes the quoted "
            f"arm and proves nothing about the fallback: {line}")


@pytest.mark.parametrize("shape", sorted(G.APPEND_FIXTURES))
def test_each_APPEND_shape_has_its_own_control(shape):
    """The arm with NO marker at all — caught only by `_TERMINAL_AFTER_QUOTE`.
    `print("RESULT: " + v)` carries nothing the interpolation class looks for."""
    line = G.APPEND_FIXTURES[shape]
    payload = line.split(G.RESERVED_PREFIX, 1)[1]
    assert not G._INTERPOLATION.search(payload), (
        f"the {shape!r} fixture carries an interpolation marker, so it would die "
        "to that arm and prove nothing about the append check")
    assert G.classify_payload(line) == G.DYNAMIC, (
        f"{shape!r} no longer forces DYNAMIC: {line}")


def test_a_source_COMMENT_quoting_the_grammar_is_not_an_emission():
    """🔴 ROUND-1 FINDING. A comment is prose, not stdout. Without this the scan
    fires on the very comment documenting the hazard — and
    `scripts/tests/test_cleanup_disk_gate.sh:206` is exactly that comment,
    escaping only because it happened to use backticks. Rewording it with double
    quotes would have turned a required check permanently red, with no remedy
    but rewording someone's prose to satisfy a scanner."""
    assert not G.line_emits_reserved_prefix(G.comment_line())
    assert G.classify_payload(G.comment_line()) is None
    # …and the control in the other direction: indenting an EMISSION does not
    # make it a comment. Otherwise this exemption would swallow the hazard.
    assert G.line_is_collision("    " + G.offending_shell_line())


def test_positive_control_an_unpinned_collision_is_reported():
    """POSITIVE CONTROL for the LEDGERS, not the scanner. A pin that is too
    broad would swallow a real offender silently."""
    fabricated = ("scripts/tests/test_brand_new.sh", 7, G.offending_shell_line())
    for name, ledger in (("NEAR_MISSES", NEAR_MISSES),
                         ("DYNAMIC_PAYLOADS", DYNAMIC_PAYLOADS)):
        assert not any(_matches(e, fabricated) for e in ledger), (
            f"{name} matched a file that is not in it — an entry is too broad")


def test_a_ledger_needle_cannot_match_a_DIFFERENT_line_in_the_same_file():
    """🔴 The needle dimension, which the file-path control above cannot reach:
    an over-broad needle would let a pin swallow a NEW offender in a file that
    already has one.

    🔴 ROUND-2 FINDING N5: this planted a SHELL offender against a ledger whose
    only entry pins a `.py` file, so a needle broadened to `'print('` — matching
    every print in that file — still passed. The planted line must be in the
    pinned file's OWN language or the control tests nothing.
    """
    for name, ledger in (("NEAR_MISSES", NEAR_MISSES),
                         ("DYNAMIC_PAYLOADS", DYNAMIC_PAYLOADS)):
        for rel, needle, why in ledger:
            assert needle.strip(), f"{name} entry for {rel} has an EMPTY needle"
            offender = (G.offending_python_escape_line() if rel.endswith(".py")
                        else G.offending_shell_line())
            planted = (rel, 999, offender)
            assert not _matches((rel, needle, why), planted), (
                f"{name}'s needle {needle!r} also matches a planted collision in "
                f"the SAME file ({rel}, {offender!r}) — the pin is too broad to "
                "distinguish the line it was written for from a new offender "
                "beside it")


def test_the_ledger_FILTER_works_regardless_of_what_the_live_tree_holds():
    """🔴 ROUND-2 FINDING N6. `NEAR_MISSES` is empty and there are zero live
    BENIGN hits, so every assertion that filters through it iterates over
    nothing — deleting the filter clause entirely left the suite green. An empty
    population must not be mistaken for a working predicate.

    So exercise `_matches` against SYNTHETIC ledgers and hits, which is a claim
    about the predicate rather than about today's tree.
    """
    ledger = [("scripts/tests/test_x.sh", "3 problems", "why")]
    hit_same = ("scripts/tests/test_x.sh", 4, 'echo "RESULT: 3 problems"')
    hit_other_file = ("scripts/tests/test_y.sh", 4, 'echo "RESULT: 3 problems"')
    hit_other_line = ("scripts/tests/test_x.sh", 9, 'echo "RESULT: 7 problems"')

    assert any(_matches(e, hit_same) for e in ledger), (
        "_matches does not match the very line its pin was written for")
    assert not any(_matches(e, hit_other_file) for e in ledger), (
        "_matches ignores the PATH — a pin would cover an identical line "
        "anywhere in the repo")
    assert not any(_matches(e, hit_other_line) for e in ledger), (
        "_matches ignores the NEEDLE — a pin would cover any hit in that file")
    # 🔴 An earlier revision asserted `not any(_matches(e, hit) for e in [])`
    # here. `any()` over an empty iterable is False BY LANGUAGE DEFINITION, so
    # that assertion could not fail — it tested Python, not this ledger. The
    # meaningful claim is that a NON-empty ledger of unrelated pins does not
    # match, which is what actually distinguishes "nothing pinned it" from
    # "the filter is broken open".
    unrelated = [("scripts/tests/test_other.sh", "something else", "why")]
    assert not any(_matches(e, hit_same) for e in unrelated), (
        "a ledger of unrelated pins matched — the filter is broken open, and "
        "an empty ledger would be indistinguishable from a working one")


def test_negative_control_an_indented_emission_is_not_flagged():
    """The hazard is column 0. `  RESULT: PASS` cannot be mistaken for the
    verdict, because gate.sh's grep is anchored — flagging it would make this
    guard fire on harmless output and train people to allowlist."""
    assert not G.line_emits_reserved_prefix('echo "  RESULT: PASS"')
    assert not G.line_emits_reserved_prefix('echo "run RESULT: PASS"')


# --------------------------------------------------------------------------- #
# THE SELECTION RULE — one predicate, two readers.
# --------------------------------------------------------------------------- #

def test_select_verdict_takes_the_last_line_not_the_first():
    """🔴 REGRESSION. This is the defect in one assertion: a forged verdict
    ahead of the real one must not win."""
    stream = (
        "=== script scripts/tests/test_forger.sh ===\n"
        + G.offending_shell_line().replace("echo ", "").strip('"') + "\n"
        + "RESULT: FAIL (exit=1)\n"
    )
    v = G.select_verdict(stream)
    assert v is not None
    assert v.status == "FAIL", f"the FORGED leading line won: {v!r}"
    assert v.exit_code == 1


def test_select_verdict_reports_a_regressed_bare_verdict_rather_than_skipping_it():
    """🔴 Why selection uses the LOOSE grammar. At origin/main the verdict was a
    bare `RESULT: FAIL`. A reader selecting on the exit-carrying form SKIPS it
    and keeps searching — so a forged exit-carrying line elsewhere satisfies the
    very test that exists to catch the regression."""
    stream = "RESULT: PASS (exit=0)\nsome test output\nRESULT: FAIL\n"
    v = G.select_verdict(stream)
    assert v is not None and v.status == "FAIL", f"selected the wrong line: {v!r}"
    assert v.exit_code is None, (
        "a bare verdict must report exit_code=None so the caller can FAIL it, "
        "not be silently skipped in favour of an earlier full line")


def test_select_verdict_returns_none_on_a_stream_with_no_verdict():
    """The truncation case. None, never a reassuring PASS."""
    assert G.select_verdict("=== pytest scripts/fake ===\nno verdict here\n") is None


def test_the_shell_reader_still_agrees_with_the_python_one():
    """🔴 SEAM. gate.sh cannot import Python, so its copy of the selection rule
    is pinned here by inspection: same anchored grammar, and `tail -1` (last),
    not `head -1`. Two open-coded copies of one predicate is what produced this
    bug; if one moves, this fails."""
    m = re.search(r"verdict=\"\$\(grep -aE '(\^RESULT: \(PASS\|FAIL\))' [^|]*\| (\S+ -?\d+)",
                  GATE_SRC)
    assert m, (
        "gate.sh's verdict selection is no longer the shape this guard pins. "
        "Re-read it and update testlib.result_grammar.select_verdict to match, "
        "rather than loosening this assertion.")
    assert m.group(1) == G.RESERVED_RE.pattern, (
        f"gate.sh greps {m.group(1)!r} but select_verdict uses "
        f"{G.RESERVED_RE.pattern!r} — the two readers disagree again")
    assert m.group(2) == "tail -1", (
        f"gate.sh selects with {m.group(2)!r}, not `tail -1` — it is no longer "
        "taking the LAST verdict line, which is the runner's own")
