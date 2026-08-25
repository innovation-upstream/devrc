#!/usr/bin/env python3
"""The detector's own battery -- committed, not run once in a shell.

🔴 THIS FILE EXISTS BECAUSE OF THE DEFECT IT TESTS FOR. The browser-bridge
guard this tool generalises carried a comment claiming each spelling "has a
planted positive control in the battery"; there was no such test, the battery
was an in-session shell loop, and six of seven mutants survived green. A tool
that finds unexercised branches must not ship with unexercised branches.

Every case here drives the real analysis. None asserts an expectation derived
from the implementation: the verdicts are literal.
"""

import pathlib
import subprocess
import sys
import tokenize

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
SCAN = REPO / "scripts" / "dead-guard-scan.py"
REGISTRY = REPO / "scripts" / "data" / "dead-guard-registry.tsv"

sys.path.insert(0, str(REPO / "scripts" / "lib"))
import dead_guard as dg  # noqa: E402


# --------------------------------------------------------------------------
# the analysis itself

def _run(body, corpus):
    """Execute `body`'s scan() over `corpus` under the tracer, return flags."""
    ns = {}
    exec(compile(body, "<t>", "exec"), ns)
    seen = set()

    def tr(frame, event, arg):
        if frame.f_code.co_filename == "<t>":
            # pragma: no cover - a tracer is not traced BY ITSELF: CPython
            # suppresses tracing inside the trace function to avoid infinite
            # recursion, so these lines run but never appear in the trace.
            # A genuine blind spot of this instrument, not dead code.
            if event == "line":  # pragma: no cover - a tracer is not traced by itself
                seen.add(frame.f_lineno)  # pragma: no cover - a tracer is not traced by itself
            return tr
        return None

    # 🔴 SAVE AND RESTORE, NEVER `settrace(None)`. `sys.settrace` is a single
    # global slot: clearing it disarms whatever tracer was already installed --
    # including dead_guard_plugin's, when this very suite is being scanned. That
    # made the tool refuse to publish devrc's census (correctly: the trace would
    # have been a lower bound, i.e. false positives against live code). Handing
    # the previous tracer back is what a well-behaved test owes its harness.
    prev = sys.gettrace()
    sys.settrace(tr)
    try:
        ns["scan"](corpus)
    finally:
        sys.settrace(prev)
    return dg.evaluate("<t>", body, seen)


LIVE = 'def scan(ls):\n    for l in ls:\n        if "A" in l:\n            return 1\n    return 0\n'
DEAD = ('def scan(ls):\n    for l in ls:\n        if "A" in l:\n            return 1\n'
        '        if "ZZZ" in l:\n            return 2\n    return 0\n')


def test_a_branch_with_a_real_corpus_instance_is_not_flagged():
    """NEGATIVE control. Without this, flagging everything would 'pass'."""
    assert _run(LIVE, ["has A"]) == []


def test_a_branch_with_zero_corpus_instances_is_flagged_at_its_own_line():
    """POSITIVE control -- and it pins the LINE, so a mutant that flags the
    wrong branch dies here rather than counting as a catch."""
    flags = _run(DEAD, ["has A"])
    assert len(flags) == 1, flags
    assert flags[0].branch.first_line == 6, flags[0].branch
    assert flags[0].branch.snippet == "return 2"


def test_the_same_guard_flags_when_the_corpus_stops_containing_the_case():
    """The verdict must track EXECUTION, not the source text -- otherwise the
    positive control above could be a property of how the branch is spelled."""
    assert _run(LIVE, ["has A"]) == []
    flags = _run(LIVE, ["nothing"])
    assert len(flags) == 1 and flags[0].branch.snippet == "return 1"


def test_a_reporting_branch_covered_only_by_its_battery_is_not_flagged():
    """🔴 THE FALSE POSITIVE THAT WOULD MAKE THIS TOOL HARMFUL.

    A guard's violation-reporting branch has zero CORPUS instances exactly when
    the repo is clean. Flagging it would recommend deleting the guard's firing
    path. Because the trace spans the whole run, a planted positive control
    exercises it and it does not flag. Driven here with both drives, in one
    trace, the way a real run sees them.
    """
    src = ('def scan(ls):\n    bad = []\n    for l in ls:\n        if "VIOLATION" in l:\n'
           '            bad.append(l)\n    return bad\n')
    ns = {}
    exec(compile(src, "<t>", "exec"), ns)
    seen = set()

    def tr(frame, event, arg):
        if frame.f_code.co_filename == "<t>":
            # pragma: no cover - a tracer is not traced BY ITSELF: CPython
            # suppresses tracing inside the trace function to avoid infinite
            # recursion, so these lines run but never appear in the trace.
            # A genuine blind spot of this instrument, not dead code.
            if event == "line":  # pragma: no cover - a tracer is not traced by itself
                seen.add(frame.f_lineno)  # pragma: no cover - a tracer is not traced by itself
            return tr
        return None

    prev = sys.gettrace()                 # restore, never clear -- see _run()
    sys.settrace(tr)
    try:
        ns["scan"](["clean line"])        # the corpus: clean
        ns["scan"](["a VIOLATION here"])  # the battery's positive control
    finally:
        sys.settrace(prev)
    assert dg.evaluate("<t>", src, seen) == []


def test_elif_is_reported_once_against_its_own_condition_line():
    """An `elif` is an `If` nested in `orelse`. Reporting it as the outer
    `else` would name the wrong line to the reader fixing it."""
    src = "def f(x):\n    if x == 1:\n        a = 1\n    elif x == 2:\n        b = 2\n    else:\n        c = 3\n"
    kinds = [(b.kind, b.first_line, b.cond_line) for b in dg.branch_bodies(src)]
    assert ("if-body", 3, 2) in kinds
    assert ("if-body", 5, 4) in kinds, "the elif must carry ITS OWN condition line"
    assert ("else-body", 7, 4) in kinds
    assert len(kinds) == 3, kinds


def test_except_and_match_and_loop_else_are_enumerated():
    src = ("def f(x):\n"
           "    try:\n        a = 1\n    except ValueError:\n        b = 2\n"
           "    for i in x:\n        pass\n    else:\n        c = 3\n"
           "    match x:\n        case 1:\n            d = 4\n")
    kinds = {b.kind for b in dg.branch_bodies(src)}
    assert {"except", "loop-else", "match-case"} <= kinds, kinds


def test_main_guard_is_not_a_guard_branch():
    assert dg.branch_bodies('if __name__ == "__main__":\n    pass\n') == []
    # ...but a DIFFERENT dunder comparison still is, so the exclusion is the
    # exact shape and not "any `if` mentioning a dunder".
    assert len(dg.branch_bodies('if __file__ == "x":\n    pass\n')) == 1


def test_sub_line_branches_are_absent_rather_than_reported_clean():
    """A stated limit, asserted. A ternary/`and`/comprehension-`if` cannot be
    discriminated at line granularity, so it is never enumerated -- and
    therefore can never be silently certified."""
    assert dg.branch_bodies("x = 1 if a else 2\n") == []
    assert dg.branch_bodies("y = a and b or c\n") == []
    assert dg.branch_bodies("z = [i for i in q if i]\n") == []


# --------------------------------------------------------------------------
# the justification hatch

def test_a_justification_needs_a_reason():
    src = DEAD.replace('if "ZZZ" in l:', 'if "ZZZ" in l:  # pragma: no cover')
    flags = _run(src, ["has A"])
    assert len(flags) == 1
    assert dg.unresolved(flags) == flags, "a bare marker asserts nothing"


def test_a_justification_with_a_reason_resolves_the_flag():
    src = DEAD.replace('if "ZZZ" in l:',
                       'if "ZZZ" in l:  # pragma: no cover - kept for the 2.x wire format')
    flags = _run(src, ["has A"])
    assert len(flags) == 1
    assert flags[0].justified_reason == "kept for the 2.x wire format"
    assert dg.unresolved(flags) == []


def test_dead_guard_ok_is_accepted_as_the_same_hatch():
    src = DEAD.replace('if "ZZZ" in l:', 'if "ZZZ" in l:  # dead-guard-ok: vendor quirk')
    assert dg.unresolved(_run(src, ["has A"])) == []


def test_a_pragma_inside_a_STRING_is_not_a_justification():
    """🔴 These guards are full of string literals containing the patterns they
    scan for. A regex over raw lines would read this as a hatch and silence a
    real flag; `tokenize` does not."""
    src = ('def scan(ls):\n    for l in ls:\n        if "A" in l:\n            return 1\n'
           '        if "ZZZ" in l:\n            return "# pragma: no cover - not a comment"\n'
           '    return 0\n')
    flags = _run(src, ["has A"])
    assert len(flags) == 1
    assert dg.unresolved(flags) == flags


def test_justification_is_read_from_the_condition_line_or_the_body_line():
    for placement in (
        'if "ZZZ" in l:  # pragma: no cover - on the condition\n            return 2',
        'if "ZZZ" in l:\n            return 2  # pragma: no cover - on the body',
    ):
        src = DEAD.replace('if "ZZZ" in l:\n            return 2', placement)
        assert dg.unresolved(_run(src, ["has A"])) == [], placement


# --------------------------------------------------------------------------
# the CLI contract the task's Verifier block names

def test_self_test_exits_zero_and_shows_BOTH_directions():
    p = subprocess.run([sys.executable, str(SCAN), "--self-test"],
                       capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stdout + p.stderr
    out = p.stdout
    # Not `grep -q`: count the controls, so a run that silently stopped early
    # cannot read as a pass.
    assert "positive control" in out and "negative control" in out, out
    assert "SELF-TEST: PASS" in out, out
    assert out.count("control") >= 5, out


def test_scan_of_an_unregistered_repo_is_undecidable_not_clean(tmp_path):
    """🔴 An unknown repo must NOT exit 0. A silent zero is indistinguishable
    from a clean result, and this whole class is comforting zeros."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=60)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin",
                    "git@github.com:nobody/not-registered.git"], check=True, timeout=60)
    p = subprocess.run([sys.executable, str(SCAN), "--repo", str(tmp_path)],
                       capture_output=True, text=True, timeout=300)
    assert p.returncode == 2, (p.returncode, p.stdout, p.stderr)
    assert "not-registered" in p.stderr


def test_a_missing_trace_file_is_undecidable_not_a_clean_run(monkeypatch, tmp_path):
    """🔴 THE ZERO THAT MATTERS. If the traced run never wrote its output, an
    empty `executed` set makes EVERY branch look dead -- or, handled the other
    way, makes the run look clean. It must be neither."""
    sys.path.insert(0, str(REPO / "scripts"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("dgs", SCAN)
    dgs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dgs)
    monkeypatch.setattr(dgs, "run_traced", lambda *a, **k: (None, 4, "boom"))
    monkeypatch.setattr(dgs, "repo_slug", lambda r: "innovation-upstream/devrc")
    monkeypatch.setattr(dgs, "guard_files", lambda *a, **k: [pathlib.Path("x.py")])
    assert dgs.scan(REPO) == dgs.EXIT_UNDECIDABLE


# --------------------------------------------------------------------------
# the END-TO-END exit-code contract criterion 3 of clawgate task #358 names:
# "exiting non-zero when a branch has zero corpus instances and no inline
# justification ... exits 0 on a clean tree and non-zero on a planted dead
# branch". Driven through the real CLI against a real git repo -- the unit
# tests above cannot see a wiring break between analysis and exit status.

def _synthetic_repo(tmp_path, guard_src):
    (tmp_path / "scripts" / "tests").mkdir(parents=True)
    (tmp_path / "scripts" / "tests" / "test_guard.py").write_text(
        guard_src, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=60)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin",
                    "git@github.com:test/synthetic.git"], check=True, timeout=60)
    reg = tmp_path / "reg.tsv"
    reg.write_text(
        "test/synthetic\tpython\tinstrument\tscripts/tests/test_guard.py\n"
        "test/synthetic\tbash\tout-of-instrument\t"
        "a reason long enough to satisfy the registry's own contract that an "
        "out-of-instrument row must say WHY it is not measured\n",
        encoding="utf-8")
    return reg


# A guard with one live branch (the corpus hits it) and one dead branch.
_E2E_GUARD = '''
def scan(lines):
    hits = []
    for ln in lines:
        if "REAL" in ln:
            hits.append("real")
        if "NEVER_WRITTEN" in ln:
            hits.append("dead")
    return hits


def test_the_guard_runs_against_the_corpus():
    assert scan(["a REAL line"]) == ["real"]
'''


def _cli(repo, reg):
    p = subprocess.run([sys.executable, str(SCAN), "--repo", str(repo),
                        "--registry", str(reg)],
                       capture_output=True, text=True, timeout=900)
    return p.returncode, p.stdout + p.stderr


def test_e2e_a_planted_dead_branch_makes_the_command_exit_NONZERO(tmp_path):
    """POSITIVE control on the shipped CLI, not on an internal function."""
    reg = _synthetic_repo(tmp_path, _E2E_GUARD)
    rc, out = _cli(tmp_path, reg)
    assert rc == 1, (rc, out)
    assert "hits.append(\"dead\")" in out, out
    # ...and it must NOT condemn the live branch alongside it.
    assert "hits.append(\"real\")" not in out, out


def test_e2e_a_clean_tree_exits_ZERO(tmp_path):
    """NEGATIVE control: the same guard with the dead branch removed. Without
    this, 'exits 1' could just mean the tool always exits 1."""
    reg = _synthetic_repo(tmp_path, _E2E_GUARD.replace(
        '        if "NEVER_WRITTEN" in ln:\n            hits.append("dead")\n', ""))
    rc, out = _cli(tmp_path, reg)
    assert rc == 0, (rc, out)
    assert "FLAG" not in out, out


def test_e2e_a_justified_dead_branch_exits_ZERO(tmp_path):
    """The hatch closes the run, end to end -- criterion 2's resolution path."""
    reg = _synthetic_repo(tmp_path, _E2E_GUARD.replace(
        'if "NEVER_WRITTEN" in ln:',
        'if "NEVER_WRITTEN" in ln:  # pragma: no cover - kept for the v1 wire format'))
    rc, out = _cli(tmp_path, reg)
    assert rc == 0, (rc, out)


def test_e2e_the_census_names_the_file_LINE_and_the_case(tmp_path):
    """Criterion 1 wants file:line, the case handled, and the instance count."""
    reg = _synthetic_repo(tmp_path, _E2E_GUARD)
    census = tmp_path / "census.tsv"
    subprocess.run([sys.executable, str(SCAN), "--repo", str(tmp_path),
                    "--registry", str(reg), "--census", str(census)],
                   capture_output=True, text=True, timeout=900)
    text = census.read_text(encoding="utf-8")
    rows = [r for r in text.splitlines()
            if r.startswith("test/synthetic\tflagged")]
    assert len(rows) == 1, text
    cells = rows[0].split("\t")
    # Line 8 of _E2E_GUARD, counted off the FIXTURE (it opens with a newline):
    # 7 is `if "NEVER_WRITTEN" in ln:`, 8 is its body. The census points at the
    # BODY, which is the line you delete.
    assert cells[2] == "scripts/tests/test_guard.py:8", cells
    assert cells[5] == "0", "corpus_instances must be recorded as 0"
    # 🔴 NOT `"out-of-instrument" in text`: the census HEADER contains that
    # phrase, so the substring form passes with every out-of-instrument DATA
    # row deleted. Found by mutants-dead-guard.sh
    # (`census-drops-the-out-of-instrument-rows` survived) -- the same
    # over-claiming-guard defect this whole tool exists to find, in its own
    # test. Assert the ROW.
    oo_rows = [r for r in text.splitlines()
               if r.startswith("test/synthetic\tout-of-instrument\t")]
    assert len(oo_rows) == 1, \
        f"the census must carry what was NOT measured as rows, got: {oo_rows}"
    assert "measured under" in text, "the interpreter must be recorded"


def test_e2e_a_repo_with_NOTHING_instrumentable_still_lands_in_the_census(tmp_path):
    """🔴 The repo whose absence would most mislead. talos-infra and civitai
    have zero instrumentable guards and ~170 of the ~270 total between them; if
    a scan of them wrote nothing, the census would read as though they had been
    swept and found clean."""
    (tmp_path / "scripts").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=60)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin",
                    "git@github.com:test/nothing-here.git"], check=True, timeout=60)
    reg = tmp_path / "reg.tsv"
    reg.write_text("test/nothing-here\tbash\tout-of-instrument\t"
                   "every guard in this repo is bash, which this tool has no "
                   "coverage backend for, so none of them are measured here\n",
                   encoding="utf-8")
    census = tmp_path / "c.tsv"
    p = subprocess.run([sys.executable, str(SCAN), "--repo", str(tmp_path),
                        "--registry", str(reg), "--census", str(census)],
                       capture_output=True, text=True, timeout=900)
    assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
    assert census.exists(), "a repo with nothing instrumentable wrote NO census"
    rows = [r for r in census.read_text(encoding="utf-8").splitlines()
            if r.startswith("test/nothing-here\tout-of-instrument\t")]
    assert len(rows) == 1, census.read_text(encoding="utf-8")


def test_e2e_the_census_path_is_repo_relative_not_absolute(tmp_path):
    """A committed census read on another machine must not name this one."""
    reg = _synthetic_repo(tmp_path, _E2E_GUARD)
    census = tmp_path / "c.tsv"
    subprocess.run([sys.executable, str(SCAN), "--repo", str(tmp_path),
                    "--registry", str(reg), "--census", str(census)],
                   capture_output=True, text=True, timeout=900)
    body = [l for l in census.read_text(encoding="utf-8").splitlines()
            if l.startswith("test/synthetic\tflagged")]
    assert body and not body[0].split("\t")[2].startswith("/"), body


# --------------------------------------------------------------------------
# span semantics — a branch body is TAKEN if ANY of its lines ran
#
# 🔴 EVERY FIXTURE ABOVE HAS A SINGLE-LINE BRANCH BODY, WHICH MAKES `any` AND
# `all` INDISTINGUISHABLE. An audit mutated `any(...)` -> `all(...)` and
# `last = max(...)` -> `last = first` and BOTH survived the whole suite, so the
# span semantics `dead_guard.evaluate` calls load-bearing were entirely
# untested. That is the fixture-collapse trap: a fixture whose values cannot
# differ between two implementations cannot see the difference. These cases
# give the branch a MULTI-LINE body with a line that does NOT run.

_MULTILINE = (
    'def scan(ls):\n'                    # 1
    '    for l in ls:\n'                 # 2
    '        if "A" in l:\n'             # 3
    '            hit = 1\n'              # 4  <- runs
    '            if "B" in l:\n'         # 5  <- runs (condition false)
    '                return "b"\n'       # 6  <- does NOT run
    '            return "a"\n'           # 7  <- runs
    '    return None\n')                 # 8


def test_a_multiline_body_is_TAKEN_when_only_SOME_of_its_lines_ran():
    """`any`, not `all`. Under `all` the outer if-body would be condemned
    because line 6 never runs — a false positive on plainly live code."""
    flags = _run(_MULTILINE, ["A only"])
    lines = sorted(f.branch.first_line for f in flags)
    assert 4 not in lines, (
        "the outer if-body (line 4) RAN and must not be flagged; "
        f"got flags at {lines}")
    # The genuinely-unexecuted inner branch IS flagged, so this fixture is not
    # simply flagging nothing.
    assert lines == [6], lines


def test_the_body_span_reaches_its_LAST_line_not_just_its_first():
    """Kills `last = max(...)` -> `last = first`. Only the last line of the
    body runs, so a span truncated to the first line reports it dead."""
    src = ('def scan(ls):\n'
           '    for l in ls:\n'
           '        if "A" in l:\n'
           '            if "Z" in l:\n'
           '                pass\n'
           '            return "deep"\n'
           '    return None\n')
    flags = _run(src, ["A only"])
    lines = sorted(f.branch.first_line for f in flags)
    assert 4 not in lines, (
        f"the outer if-body spans lines 4-6 and line 6 ran; got {lines}")
    assert lines == [5], lines


# --------------------------------------------------------------------------
# the zeros that must be UNDECIDABLE rather than clean or dead

def test_a_LIBRARY_guard_driven_only_by_a_SUBPROCESS_is_undecidable(tmp_path):
    """🔴 The worst possible output: a complete, confident, entirely false
    census of working code. `sys.settrace` is per-interpreter, so a library
    guard exercised only by spawning a child python is invisible, and every one
    of its branches would be reported dead.

    ⚠️ The subject must be a LIBRARY module, not the test file. pytest imports
    a test file during collection, so its module-level lines always trace and
    this arm can never fire for one — a limit the CLI states at the site.
    """
    lib = tmp_path / "scripts" / "guardlib.py"
    lib.parent.mkdir(parents=True, exist_ok=True)
    lib.write_text('def scan(ls):\n'
                   '    hits = []\n'
                   '    for l in ls:\n'
                   '        if "REAL" in l:\n'
                   '            hits.append("real")\n'
                   '    return hits\n', encoding="utf-8")
    guard = ('import subprocess, sys, pathlib\n'
             '\n'
             'LIB = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "guardlib.py"\n'
             '\n'
             '\n'
             'def test_driven_only_through_a_subprocess():\n'
             '    code = "import importlib.util,sys;"\\\n'
             '           "s=importlib.util.spec_from_file_location(\'g\', sys.argv[1]);"\\\n'
             '           "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"\\\n'
             '           "print(m.scan([\'a REAL line\']))"\n'
             '    p = subprocess.run([sys.executable, "-c", code, str(LIB)],\n'
             '                       capture_output=True, text=True, timeout=60)\n'
             '    assert "real" in p.stdout, (p.stdout, p.stderr)\n')
    reg = _synthetic_repo(tmp_path, guard)
    reg.write_text(
        "test/synthetic\tpython\tinstrument\tscripts/tests/test_guard.py\n"
        "test/synthetic\tpython\tinstrument\tscripts/guardlib.py\n"
        "test/synthetic\tbash\tout-of-instrument\t"
        "a reason long enough to satisfy the registry's own contract that an "
        "out-of-instrument row must say WHY it is not measured\n",
        encoding="utf-8")
    rc, out = _cli(tmp_path, reg)
    assert rc == 2, (rc, out)
    assert "guardlib.py: NO line of this file was traced" in out, out
    assert 'hits.append("real")' not in out, \
        "a live branch was reported dead on an untraced library module"


def test_a_test_that_CLEARS_the_tracer_makes_the_run_undecidable(tmp_path):
    """🔴 `sys.settrace` is one global slot. A test that clears it disarms this
    instrument for every file collected afterwards, which then reports LIVE
    branches as dead — on a GREEN run, so the red-run banner never fires.
    Re-arming per test is not sufficient on its own: a tracer cleared partway
    through a test still loses that test's lines, so the run is reported
    UNDECIDABLE rather than published."""
    guard = ('import sys\n'
             '\n'
             'def scan(ls):\n'
             '    hits = []\n'
             '    for l in ls:\n'
             '        if "REAL" in l:\n'
             '            hits.append("real")\n'
             '    return hits\n'
             '\n'
             '\n'
             'def test_that_clears_the_global_tracer():\n'
             '    def tr(frame, event, arg):\n'
             '        return None\n'
             '    sys.settrace(tr)\n'
             '    try:\n'
             '        assert scan(["a REAL line"]) == ["real"]\n'
             '    finally:\n'
             '        sys.settrace(None)\n'
             '\n'
             '\n'
             'def test_zz_runs_after_the_clobber():\n'
             '    assert scan(["a REAL line"]) == ["real"]\n')
    reg = _synthetic_repo(tmp_path, guard)
    rc, out = _cli(tmp_path, reg)
    assert rc == 2, (rc, out)
    assert "sys.settrace" in out, out


def test_a_guard_that_cannot_be_DECODED_is_undecidable_not_a_findings_exit(tmp_path):
    """🔴 A file this tool cannot analyse must exit 2, not 1. Exit 1 means
    "found dead branches" and is indistinguishable from a real result — and the
    escaping exception wrote no census at all.

    A latin-1 `.py` is the REACHABLE case: `read_text(encoding="utf-8")` raises
    `UnicodeDecodeError` before anything is parsed.
    """
    reg = _synthetic_repo(tmp_path, _E2E_GUARD)
    bad = tmp_path / "scripts" / "tests" / "test_latin1.py"
    bad.write_bytes(b'# caf\xe9\ndef test_ok():\n    assert True\n')
    reg.write_text(reg.read_text(encoding="utf-8").replace(
        "scripts/tests/test_guard.py", "scripts/tests/test_*.py"), encoding="utf-8")
    rc, out = _cli(tmp_path, reg)
    assert rc == 2, (rc, out)
    assert "cannot be analysed (UnicodeDecodeError" in out, out


def test_tokenize_TokenError_is_deliberately_NOT_in_the_catch_list():
    """🔴 THE FIX FOR AN AUDIT FINDING WAS ITSELF A DEAD GUARD BRANCH.

    `tokenize.TokenError` is not a `SyntaxError` subclass, so adding it looked
    obviously right — and it is unreachable. `ast.parse` runs first and raises
    `SyntaxError` for every input that would raise `TokenError`. Measured here
    rather than asserted, so that if CPython ever changes this the catch can be
    restored WITH a real input behind it. Applying this repo's own rule to the
    fix round: delete the unexercised branch and state the limit.
    """
    assert not issubclass(tokenize.TokenError, SyntaxError)
    for src in ('x = "abc\n', 'x = """abc\n', 'x = (1,\n', 'x = 1 + \\\n'):
        with pytest.raises(SyntaxError):
            dg.branch_bodies(src)          # ast.parse gets there first
        with pytest.raises(tokenize.TokenError):
            dg.justifications(src)         # ...only tokenize would have raised
    assert "tokenize.TokenError" not in SCAN.read_text(encoding="utf-8").split(
        "🔴 `tokenize.TokenError` IS DELIBERATELY ABSENT")[1], \
        "the catch was re-added without a reachable input to justify it"


def test_an_unparseable_guard_is_undecidable_not_a_findings_exit(tmp_path):
    """The parse arm, driven: a syntactically broken guard exits 2."""
    reg = _synthetic_repo(tmp_path, _E2E_GUARD)
    # A second registered guard that cannot be tokenised.
    bad = tmp_path / "scripts" / "tests" / "test_broken.py"
    bad.write_text('def test_ok():\n    assert True\n\nx = "unterminated\n',
                   encoding="utf-8")
    reg.write_text(reg.read_text(encoding="utf-8").replace(
        "scripts/tests/test_guard.py", "scripts/tests/test_*.py"), encoding="utf-8")
    rc, out = _cli(tmp_path, reg)
    assert rc == 2, (rc, out)
    assert "cannot be analysed" in out, out


def test_an_instrument_selector_matching_NOTHING_is_undecidable(tmp_path):
    """🔴 A one-character typo in a selector otherwise reduces the whole report
    to nothing and exits 0 — shaped identically to the legitimate 'this repo
    has no python guards' case."""
    reg = _synthetic_repo(tmp_path, _E2E_GUARD)
    reg.write_text(reg.read_text(encoding="utf-8").replace(
        "scripts/tests/test_guard.py", "scripts/tests/test_guardd.py"),
        encoding="utf-8")
    rc, out = _cli(tmp_path, reg)
    assert rc == 2, (rc, out)
    assert "matched NO python file" in out, out


# --------------------------------------------------------------------------
# the census is an artifact people re-derive

def test_the_census_is_IDEMPOTENT(tmp_path):
    """🔴 It is append-only no longer. The regeneration command is printed in
    the census's own header, so following it used to DOUBLE every row — and a
    flag resolved in the source was never removed, so the artifact drifted
    upward from the thing it measures."""
    reg = _synthetic_repo(tmp_path, _E2E_GUARD)
    census = tmp_path / "c.tsv"
    for _ in range(3):
        subprocess.run([sys.executable, str(SCAN), "--repo", str(tmp_path),
                        "--registry", str(reg), "--census", str(census)],
                       capture_output=True, text=True, timeout=900)
    rows = [r for r in census.read_text(encoding="utf-8").splitlines()
            if r.startswith("test/synthetic\t")]
    assert len(rows) == 2, rows          # 1 flagged + 1 out-of-instrument
    assert len(set(rows)) == 2, rows


def test_the_census_never_carries_an_absolute_path(tmp_path):
    """🔴 The committed census recorded
    `/home/<user>/workspace/.../.venv/bin/python3` — the operator's home dir
    and an unrelated repo's venv, published into devrc and pinning the artifact
    to one machine. The interpreter VERSION is what changes which branches run;
    the path is a terminal diagnostic, not an artifact field."""
    reg = _synthetic_repo(tmp_path, _E2E_GUARD)
    census = tmp_path / "c.tsv"
    subprocess.run([sys.executable, str(SCAN), "--repo", str(tmp_path),
                    "--registry", str(reg), "--census", str(census)],
                   capture_output=True, text=True, timeout=900)
    text = census.read_text(encoding="utf-8")
    assert "measured under Python " in text, text
    for line in text.splitlines():
        assert "/home/" not in line, line
        assert str(tmp_path) not in line, line


def test_the_census_records_that_the_run_was_RED(tmp_path):
    """A reader six months out cannot otherwise tell that N flags came off a
    failing run. The stdout banner does not survive into the artifact."""
    guard = _E2E_GUARD + '\n\ndef test_deliberately_failing():\n    assert False\n'
    reg = _synthetic_repo(tmp_path, guard)
    census = tmp_path / "c.tsv"
    subprocess.run([sys.executable, str(SCAN), "--repo", str(tmp_path),
                    "--registry", str(reg), "--census", str(census)],
                   capture_output=True, text=True, timeout=900)
    text = census.read_text(encoding="utf-8")
    assert "RED RUN" in text and "1 test(s) FAILED" in text, text


def test_a_TAB_in_a_snippet_cannot_shift_the_census_columns():
    import importlib.util
    spec = importlib.util.spec_from_file_location("dgs_tsv", SCAN)
    dgs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dgs)
    assert "\t" not in dgs._tsv("a\tb\nc")


# --------------------------------------------------------------------------
# the registry is an artifact with a contract

def test_registry_parses_and_every_repo_declares_what_is_NOT_measured():
    """🔴 The out-of-instrument rows are the honesty of this tool. A repo with
    only `instrument` rows would report a clean exit while ~180 of ~270 guards
    went unlooked-at with nothing saying so."""
    sys.path.insert(0, str(REPO / "scripts"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("dgs2", SCAN)
    dgs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dgs)
    rows = dgs.load_registry(REGISTRY)
    assert rows, "registry is empty"
    slugs = {r["slug"] for r in rows}
    assert slugs == {"innovation-upstream/devrc", "civitai/talos-infra",
                     "civitai/civitai", "ZacxDev/homelab-infra"}, slugs
    for s in slugs:
        oo = [r for r in rows if r["slug"] == s and r["status"] == "out-of-instrument"]
        assert oo, f"{s} declares nothing out-of-instrument -- see the docstring"
        for r in oo:
            assert len(r["selector"]) > 40, \
                f"{s}: an out-of-instrument row must say WHY, not just that: {r}"
    for r in rows:
        assert r["status"] in ("instrument", "out-of-instrument"), r


def test_registry_names_every_test_directory_in_devrc():
    """🔴 THE GUARD THE PREVIOUS ONE ONLY CLAIMED TO BE.

    `test_registry_parses_and_every_repo_declares_what_is_NOT_measured` says in
    its docstring that it would catch "a repo with only `instrument` rows …
    guards unlooked-at". Its body asserts only that at least ONE
    out-of-instrument row exists, so it could not see a guard surface present
    in NEITHER status — and 12 python guard modules under
    `scripts/claude-hooks/`, including the PreToolUse deny-guards
    `guard_core.py` and `bash-guard.py`, were exactly that. The registry's own
    headline claim ("a guard absent from this file would read as measured and
    clean") was violated for devrc's largest python guard surface.

    Bidirectional, like homelab-infra's `ci-manifest.txt`: a new test directory
    with no row FAILS, so the set cannot silently grow. It does not try to
    decide what a "guard" is — it requires a DECISION to be on record.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("dgs_reg", SCAN)
    dgs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dgs)
    rows = dgs.load_registry(REGISTRY)

    dirs = dgs.test_dirs(REPO)
    assert len(dirs) >= 10, (
        f"only {len(dirs)} test dirs found in {REPO} — the enumeration is "
        f"broken, and an empty ledger would pass this test vacuously: {dirs}")

    missing = dgs.unregistered_test_dirs(REPO, rows, "innovation-upstream/devrc")
    assert missing == [], (
        "these directories hold test_*.py and appear in NO registry row, in "
        "either status — so this tool reports clean over them while never "
        "having looked:\n  " + "\n  ".join(missing))


def test_registry_is_keyed_on_the_remote_slug_not_the_clone_directory():
    """Two of the four clones are named nothing like their repo. A basename key
    would match the wrong profile, silently."""
    for local, slug in (("datapacket-talos", "civitai/talos-infra"),
                        ("homelab-talos", "ZacxDev/homelab-infra")):
        assert local not in REGISTRY.read_text(encoding="utf-8").split("\n\n")[-1], \
            f"{local} appears as a key; it is not the repo name for {slug}"


@pytest.mark.parametrize("url,want", [
    ("git@github.com:innovation-upstream/devrc.git", "innovation-upstream/devrc"),
    ("https://github.com/civitai/talos-infra.git", "civitai/talos-infra"),
    ("https://github.com/civitai/talos-infra", "civitai/talos-infra"),
])
def test_slug_parsing_handles_ssh_https_and_a_missing_dot_git(url, want, tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=60)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", url],
                   check=True, timeout=60)
    import importlib.util
    spec = importlib.util.spec_from_file_location("dgs3", SCAN)
    dgs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dgs)
    assert dgs.repo_slug(tmp_path) == want
