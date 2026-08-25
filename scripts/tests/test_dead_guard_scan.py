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

    sys.settrace(tr)
    try:
        ns["scan"](corpus)
    finally:
        sys.settrace(None)
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

    sys.settrace(tr)
    try:
        ns["scan"](["clean line"])        # the corpus: clean
        ns["scan"](["a VIOLATION here"])  # the battery's positive control
    finally:
        sys.settrace(None)
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
