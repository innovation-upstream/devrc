"""STRUCTURAL guard: no `run-tests.sh` registry entry may forge a verdict line.

WHY
---
`run-tests.sh` inlines the stdout of its `HOOK_TESTS` and `SHELL_TESTS`
registries straight into its own stream — no capture, no prefixing. (The pytest
targets are different: pytest captures their stdout and replays it only on
failure.) So a registry entry printing `RESULT: PASS (exit=0)` at column 0 puts
a line into the gate's truth-telling channel that is byte-indistinguishable from
the runner's own verdict, and always *precedes* it.

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

COLLISIONS vs NEAR-MISSES
-------------------------
A COLLISION emits the reserved grammar (`RESULT: PASS` / `RESULT: FAIL`) and is
a hard failure. A NEAR-MISS emits the reserved PREFIX with some other payload.

🔴 There is a live near-miss today and it is pinned below, not hypothetical:
`scripts/claude-hooks/tests/test_bash_guard.py` ends with
`print("\\nRESULT:", "all good" if not fail else …)`, which really does put
`RESULT: all good` at column 0 of the runner's stream (verified by running it).
It is harmless only because `all good` is not `PASS`/`FAIL` — gate.sh's own
comment cites this exact line as the reason its grep is anchored and narrow. The
pin is two-way, so the obvious refactor to
`print("RESULT:", "PASS" if not fail else "FAIL")` fails this file twice over:
the near-miss pin goes stale AND the collision scan fires.

HOW TO SATISFY IT
-----------------
Print anything else. `PASS`/`FAIL` counts are fine indented or with any other
leading text (`  RESULT: 3 failure(s)`, `PASS 12  FAIL 0`). Do not add an
allowlist entry to get green: NEAR_MISSES is for lines that emit the prefix and
provably cannot collide, and every entry must say why.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from testlib import result_grammar as G  # noqa: E402

RUNNER = REPO / "scripts" / "run-tests.sh"
RUNNER_SRC = RUNNER.read_text(encoding="utf-8")
GATE_SRC = (REPO / "scripts" / "gate.sh").read_text(encoding="utf-8")

REGISTRIES = ("HOOK_TESTS", "SHELL_TESTS")

# --- THE PINNED NEAR-MISS LEDGER ----------------------------------------------
# (relative path, substring the line must contain, why it cannot collide).
# Accounted BOTH ways, the same discipline as test_runtime_shebangs.py's
# ALLOWLIST: an unpinned near-miss fails, and a pin matching nothing fails.
NEAR_MISSES = [
    ("scripts/claude-hooks/tests/test_bash_guard.py", '"all good"',
     "emits `RESULT: all good` at column 0 (verified by running it). The payload "
     "is a free-text summary, never PASS/FAIL, so it cannot match the reserved "
     "grammar `^RESULT: (PASS|FAIL)` that gate.sh greps for — gate.sh's own "
     "comment names this line as why that grep is anchored and narrow"),
]


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
    assert len(_registry_entries()) >= 8, (
        f"only {len(_registry_entries())} registry entries parsed; the runner "
        "declares ten. The parser is reading the wrong thing."
    )


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


def test_every_prefix_emission_is_either_pinned_or_absent():
    """The near-miss population, accounted. A line that emits the prefix
    without colliding is one refactor from colliding, so it is pinned with a
    reason rather than left to be rediscovered."""
    unpinned = [h for h in _hits()
                if not G.line_is_collision(h[2])
                and not any(_matches(e, h) for e in NEAR_MISSES)]
    assert not unpinned, (
        "a registry entry emits `RESULT:` at column 0 with an unpinned payload. "
        "It does not collide today; pin it with the reason it cannot, or print "
        "something else:\n  "
        + "\n  ".join(f"{p}:{n}: {l.strip()}" for p, n, l in unpinned))


def test_every_near_miss_pin_still_matches_something():
    """Stale-pin accounting. A pin that stops matching means the line moved or
    changed — left in place it pre-approves whatever appears there next."""
    hits = _hits()
    stale = [f"{rel} ~ {needle!r}" for rel, needle, why in NEAR_MISSES
             if not any(_matches((rel, needle, why), h) for h in hits)]
    assert not stale, (
        "NEAR_MISSES entries match nothing — the site changed. Re-verify how "
        "that file prints its summary, then delete the pin:\n  "
        + "\n  ".join(stale))


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


def test_positive_control_a_near_miss_is_seen_but_not_called_a_collision():
    """The two populations must be DISTINGUISHED, not merged. A scan that
    called every prefix emission a collision would fail the live tree and get
    itself deleted; one that called none would miss the hazard."""
    near = G.near_miss_line()
    assert G.line_emits_reserved_prefix(near), "near-miss not seen at all"
    assert not G.line_is_collision(near), "near-miss misreported as a collision"


def test_positive_control_an_unpinned_collision_is_reported():
    """POSITIVE CONTROL for the LEDGER, not the scanner. A pin that is too
    broad would swallow a real offender silently."""
    fabricated = ("scripts/tests/test_brand_new.sh", 7, G.offending_shell_line())
    assert not any(_matches(e, fabricated) for e in NEAR_MISSES), (
        "NEAR_MISSES matched a file that is not in it — an entry is too broad")


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
