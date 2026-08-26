"""REPO-WIDE structural guard: the clawgate operator-pending predicate has ONE
definition, and the three surfaces that answer it all import that one.

WHY
---
Before scripts/lib/clawgate_tasks.py the set `{"open", "ready_for_review"}` was
open-coded at two sites — `scripts/bar-status-poll` (`PENDING_TASK_STATES`) and
`scripts/agent-ops` (`_PENDING_STATES`) — and both were wrong in the SAME
direction, excluding the `in_progress` state a dead dispatch is stranded in.
`scripts/session-manager` read the poller's cache rather than the API, so it
inherited the blindness without holding a copy at all: a wedged agent was
invisible on EVERY surface simultaneously.

Consolidating without pinning it just resets the clock. `claude/RULES.md` → "One
rule, one place": a predicate open-coded at N sites is typically wrong at N-1 of
them, and the only thing that keeps N at 1 is a test that fails when it becomes
2.

WHAT IS CHECKED, AND WHY STRUCTURALLY
-------------------------------------
🔴 A SPELLED guard would pass while the hazard exists in a different shape.
`grep 'frozenset({"open", "ready_for_review"})'` misses a list, a tuple, a set
literal, single quotes, reversed order, or an extra member. So this walks the
AST of every Python file in the repo (extensionless scripts included — that is
what all three consumers are) and flags any set/list/tuple/frozenset-call whose
string constants are a SUPERSET of {"open", "ready_for_review"}. Order, quoting,
container type and extra members cannot evade it.

Second half: an IMPORTER LEDGER, asserted in both directions. It fails when the
set of files loading the shared module GROWS (a fourth surface appeared and its
rendering was never reviewed) and when it SHRINKS (a consumer quietly went back
to its own copy) — the seam, not the components. `claude/RULES.md` → "Verified
in isolation is the new vacuous green".
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from testlib import skip_dirs  # noqa: E402

LIB_REL = "scripts/lib/clawgate_tasks.py"

#: The pending states, spelled here ONCE so this guard can look for them. This
#: file is its own allowlist entry below — a scanner has to name what it hunts.
TARGET = {"open", "ready_for_review"}

#: 🔴 Paths permitted to contain the literal, MAPPED TO HOW MANY TIMES, each
#: with the reason. Accounting is TWO-WAY (same discipline as
#: test_runtime_shebangs' allowlist): an unlisted hit fails, and a listed path
#: that no longer hits also fails, so a rubber stamp cannot outlive the thing it
#: stamped.
#:
#: 🔴 THE COUNT IS THE POINT. A path-level allowlist forgives a path, not an
#: occurrence: appending a genuine second `frozenset({"open",
#: "ready_for_review"})` to any file already on this list kept the guard green,
#: which is exactly the regrowth it exists to catch — the predicate coming back
#: as copy N+1 inside a file that was pardoned for copy 1. Pinning the number
#: makes every NEW occurrence fail even in a forgiven file. Update a number here
#: only together with the reason it moved.
ALLOWLIST = {
    # THE definition. Everything else imports it: PENDING_TASK_STATES itself.
    LIB_REL: 1,
    # This guard: it must name the set it hunts for (TARGET, above).
    "scripts/tests/test_clawgate_predicate_single_source.py": 1,
    # The unit suite asserts the definition's literal value — a contract test
    # for the constant is the one legitimate second spelling. Two: the contract
    # assertion, and one fixture that builds the same pair by hand.
    "scripts/tests/test_clawgate_tasks.py": 2,
    # 🔴 A DIFFERENT RULE that this scanner's superset test legitimately catches,
    # found by writing it. `initiatives/tasks.OPEN_STATUSES` is
    # ("open", "in_progress", "ready_for_review") — the initiatives viewer's
    # DUPLICATE-DISPATCH guard ("is there already live work for this
    # initiative?"), which deliberately INCLUDES `in_progress` and deliberately
    # fails open on an unknown status. It answers "is this task still live?",
    # not "does this need the operator?", so folding the two together would give
    # the bar an in_progress task it must not count and give the dispatch guard
    # a stuck-agent notion it has no use for. Kept separate ON PURPOSE.
    "scripts/initiatives/tasks.py": 1,
    # The same rule re-spelled in that suite's `_tv` fixture (it builds the
    # `open` flag by hand rather than importing OPEN_STATUSES). Pre-existing and
    # out of scope here; noted so the next reader does not think it is this one.
    "scripts/initiatives/tests/test_viewer.py": 1,
}

#: 🔴 The importer ledger. Exactly these files load the shared module.
EXPECTED_IMPORTERS = {
    "scripts/bar-status-poll",      # the 45s bar poller, writes the cache
    "scripts/session-manager",      # the cross-host JSON report
}
# `scripts/agent-ops` — the mission-control TUI — was the third importer until it
# was RETIRED. It is not "one fewer surface to keep in sync": it read this
# predicate live, and dropping it here is the SHRINK case this ledger is built
# to make audible, recorded rather than silently absorbed.

#: 🔴 SHARED BASE + THIS SITE'S OWN ADDITION, spelled here so the effective set
#: is readable where it is used. The base is `testlib/skip_dirs.GENERATED`; this
#: set was one of the two that had NOT learned about `.pytest_cache` (see that
#: module's header for the measurement and for the red it caused in the sibling
#: shared-detector ledger — not named here, because that ledger hunts for its
#: own trigger token repo-wide and naming it makes this file a finding).
#:
#: `.claude` is added HERE and deliberately not in the base: per-host Claude Code
#: state, gitignored, and the parent of the agent worktrees. It must NOT reach
#: `public_ip_scan` / `client_host_scan`, which are security gates on a PUBLIC
#: repo. `VIRTUALENVS` is granted because this walker has no `git ls-files`
#: tier — it AST-parses whatever `.py` files are on disk, which in the
#: operator's checkout is 395 files of vendored pip source under `.venv/`.
SKIP_DIRS = set(skip_dirs.GENERATED | skip_dirs.VIRTUALENVS) | {".claude"}

#: `#!`, assembled from character codes rather than written as a quoted literal.
#: Same reason as `scripts/testlib/shebang_scan.py`'s needles: the repo-wide
#: runtime-shebang guard looks for a shebang sitting directly behind a quote, so
#: spelling it here makes this file its own offender (it did, in the sandbox
#: tier only — the dev-host run was green).
_SHEBANG = chr(35) + chr(33)


def _looks_like_python(path: Path) -> bool:
    if path.suffix == ".py":
        return True
    if path.suffix or not path.is_file():
        return False
    try:
        first = path.open("rb").readline(200).decode("utf-8", "replace")
    except OSError:
        return False  # pragma: no cover - not raised over the tracked corpus
    return first.startswith(_SHEBANG) and "python" in first


def python_files(root: Path):
    """Every python file under `root`, extensionless scripts included.

    🔴 The skip list is applied to the path RELATIVE to `root`, never to the
    absolute one. This checkout can itself live under a skipped directory name
    (worktrees are created inside `.claude/worktrees/`), and matching on the
    absolute parts made the scan walk exactly ZERO files while reporting a
    perfect green — the failure mode the positive control below exists to catch,
    caught by it.
    """
    for p in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        if p.is_file() and _looks_like_python(p):
            yield p


def _string_members(node):
    """`(members, inner)` for a set/list/tuple/frozenset node, else `(None, None)`.

    `members` is the set of string constants directly inside it. `inner` is the
    wrapped container node when this is a `frozenset({...})`-style call, so the
    caller can avoid counting one literal twice — the ALLOWLIST pins exact
    counts now, and `frozenset({...})` matching as both the Call and its inner
    Set would make every pinned number an artefact of the spelling used.
    """
    elts = inner = None
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        elts = node.elts
    elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
          and node.func.id in {"frozenset", "set", "list", "tuple"}
          and len(node.args) == 1
          and isinstance(node.args[0], (ast.Set, ast.List, ast.Tuple))):
        inner = node.args[0]
        elts = inner.elts
    if elts is None:
        return None, None
    return {e.value for e in elts
            if isinstance(e, ast.Constant) and isinstance(e.value, str)}, inner


def find_state_set_literals(source: str):
    """Line numbers of every literal container whose strings ⊇ TARGET.

    ONE entry per literal: `ast.walk` is breadth-first, so a wrapping
    `frozenset(...)` call is always seen before the set it wraps and can retire
    it. Without that, `frozenset({"open", "ready_for_review"})` counts as two.
    """
    hits, consumed = [], set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if id(node) in consumed:
            continue
        members, inner = _string_members(node)
        if members is not None and TARGET <= members:
            hits.append(getattr(node, "lineno", 0))
            if inner is not None:
                consumed.add(id(inner))
    return hits


# =========================================================================== #
# 🔴 POSITIVE CONTROL. A scanner that reports zero is indistinguishable from a
# scanner wired to nothing until it has been watched to produce a non-zero
# count. These run FIRST and cover every shape the real one must not miss.
# =========================================================================== #
@pytest.mark.parametrize("snippet", [
    'X = frozenset({"open", "ready_for_review"})',        # the original spelling
    "X = frozenset({'ready_for_review', 'open'})",        # reversed + single quotes
    'X = {"open", "ready_for_review"}',                    # bare set literal
    'X = ["open", "ready_for_review"]',                    # list
    'X = ("open", "ready_for_review")',                    # tuple
    'X = set(["ready_for_review", "open"])',               # set() over a list
    'X = frozenset({"open", "ready_for_review", "blocked"})',   # extra member
    'def f(states={"open", "ready_for_review"}): pass',    # a default argument
    'if s in ("open", "ready_for_review"): pass',          # an inline membership test
])
def test_positive_control_the_scanner_sees_every_shape(snippet):
    assert find_state_set_literals(snippet), snippet


@pytest.mark.parametrize("snippet", [
    'X = {"open"}',                                   # one half only
    'X = {"ready_for_review"}',                       # the other half only
    'X = {"open": 1, "ready_for_review": 2}',         # a dict, not a state set
    'X = "open ready_for_review"',                    # one string, not a set
    'X = [OPEN, READY]',                              # names, not literals
])
def test_negative_control_the_scanner_does_not_fire_on_near_misses(snippet):
    assert find_state_set_literals(snippet) == []


def test_the_scanner_actually_walks_the_repo():
    # 🔴 The count that matters is INSTANCES, not declarations: a scanner
    # pointed at an empty glob reports a perfect zero. Assert it examined a
    # realistic number of files and found the one known-good definition.
    files = list(python_files(REPO / "scripts"))
    assert len(files) > 100, "the scan walked almost nothing: %d files" % len(files)
    lib = REPO / LIB_REL
    assert lib in files
    assert find_state_set_literals(lib.read_text()), \
        "the definition itself no longer matches — the scanner is looking for " \
        "a set this module no longer spells"


# =========================================================================== #
# THE CHECKS, as pure functions over a root
# =========================================================================== #
# 🔴 Each of the three loops below used to live inline inside its test, walking
# the LIVE repo. That made every one of them structurally undriveable: they fire
# only on a dirty corpus, and the corpus is clean, so the FIRING half of each
# guard had never been executed by any test. Extracted verbatim (same
# comparisons, same message strings) so a synthetic tree can drive them — the
# live tests below call these with REPO and assert exactly what they did before.
def scan_for_unallowlisted_literals(root: Path, allowlist=ALLOWLIST) -> dict:
    """`{relpath: [line, ...]}` for every file holding TARGET outside `allowlist`."""
    offenders = {}
    for p in python_files(root):
        rel = p.relative_to(root).as_posix()
        try:
            hits = find_state_set_literals(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue  # pragma: no cover - not raised over the tracked corpus
        if hits and rel not in allowlist:
            offenders[rel] = hits
    return offenders


def check_allowlist_counts(root: Path, allowlist=ALLOWLIST) -> list:
    """Messages for every allowlist entry whose file is gone or miscounted."""
    wrong = []
    for rel, expected in sorted(allowlist.items()):
        p = root / rel
        if not p.exists():
            wrong.append("%s: file is gone (expected %d)" % (rel, expected))
            continue
        hits = find_state_set_literals(p.read_text(encoding="utf-8"))
        if len(hits) != expected:
            wrong.append("%s: expected %d, found %d at line(s) %s"
                         % (rel, expected, len(hits), hits))
    return wrong


def scan_for_threshold_respellings(root: Path, skip=()) -> list:
    """`["rel:lineno text", ...]` for every re-spelling of the shared constant."""
    offenders = []
    for p in python_files(root):
        rel = p.relative_to(root).as_posix()
        if rel in skip:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if "THRESHOLD_SECS" in line and "=" in line and "CG." not in line \
                    and "clawgate_tasks" not in line:
                offenders.append("%s:%d %s" % (rel, i, line.strip()))
    return offenders


# =========================================================================== #
# 🔴 POSITIVE CONTROLS on the three FIRING paths above. Each feeds a synthetic
# tree that MUST make one guard fire, and asserts THAT guard's own message —
# never merely "the list is non-empty". Counts are pairwise distinct so the two
# arms of the allowlist comparison cannot be confused for each other.
# =========================================================================== #
def test_positive_control_an_unallowlisted_copy_IS_reported(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "rogue.py").write_text(
        "# a fourth surface re-spelling the predicate\n"
        'STATES = frozenset({"ready_for_review", "open"})\n')
    # …and a file that IS pardoned must stay out of the report.
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "lib").mkdir()
    (tmp_path / "scripts" / "lib" / "clawgate_tasks.py").write_text(
        'PENDING_TASK_STATES = frozenset({"open", "ready_for_review"})\n')

    offenders = scan_for_unallowlisted_literals(tmp_path)
    assert offenders == {"pkg/rogue.py": [2]}, offenders


def test_positive_control_a_pardoned_file_that_VANISHED_is_reported(tmp_path):
    # Only the "file is gone" arm: nothing on disk at all. 7 is unique to this
    # test, so a mutant that leaks into the other arm's message is visible.
    wrong = check_allowlist_counts(tmp_path, {"scripts/vanished.py": 7})
    assert wrong == ["scripts/vanished.py: file is gone (expected 7)"], wrong


def test_positive_control_a_pardoned_file_with_the_WRONG_COUNT_is_reported(tmp_path):
    # Only the count arm: the file exists and holds exactly 2 literals while the
    # pardon was issued for 3. Both numbers differ from each other and from the
    # 7 above, so this cannot pass by coincidence with the other arm.
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "pardoned.py").write_text(
        'A = frozenset({"open", "ready_for_review"})\n'
        'B = ["ready_for_review", "open", "blocked"]\n')
    wrong = check_allowlist_counts(tmp_path, {"scripts/pardoned.py": 3})
    assert wrong == [
        "scripts/pardoned.py: expected 3, found 2 at line(s) [1, 2]"], wrong
    # The same file pinned at its true count is silent — the guard is not simply
    # rejecting everything.
    assert check_allowlist_counts(tmp_path, {"scripts/pardoned.py": 2}) == []


def test_positive_control_a_respelled_threshold_constant_is_reported(tmp_path):
    (tmp_path / "consumer.py").write_text(
        "import os\n"
        "STUCK_THRESHOLD_SECS = 900\n"
        "threshold = CG.STUCK_THRESHOLD_SECS\n")
    (tmp_path / "skipped.py").write_text("OTHER_THRESHOLD_SECS = 42\n")

    offenders = scan_for_threshold_respellings(tmp_path, skip=("skipped.py",))
    assert offenders == ["consumer.py:2 STUCK_THRESHOLD_SECS = 900"], offenders


# =========================================================================== #
# THE GUARD
# =========================================================================== #
def test_the_pending_state_set_is_defined_exactly_once():
    offenders = scan_for_unallowlisted_literals(REPO)
    assert not offenders, (
        "a second copy of the clawgate operator-pending state set appeared:\n"
        + "\n".join("  %s: line(s) %s" % (k, v) for k, v in offenders.items())
        + "\n\nImport it instead: scripts/lib/clawgate_tasks.PENDING_TASK_STATES."
        " That module also owns STUCK_THRESHOLD_SECS and the stuck"
        " predicate. If a second spelling is genuinely required, add it to"
        " ALLOWLIST in this file WITH the reason.")


def test_every_allowlist_entry_carries_EXACTLY_the_pinned_number_of_literals():
    """🔴 Two-way accounting, at OCCURRENCE granularity rather than path.

    A stamp that outlives what it stamped is a lie the next reader will trust —
    and so is a stamp that silently covers more than it was issued for. The
    path-level version of this check passed when a second literal was appended
    to an already-listed file, which is the regrowth the whole guard exists to
    catch, wearing a pardon it was never granted.
    """
    wrong = check_allowlist_counts(REPO)
    assert not wrong, (
        "the ALLOWLIST no longer accounts for the literals in these files:\n  "
        + "\n  ".join(wrong)
        + "\n\nFOUND MORE than pinned: a new copy of the predicate appeared in a"
        " file that was allowlisted for a DIFFERENT one — import"
        " clawgate_tasks.PENDING_TASK_STATES instead. FOUND FEWER (or zero): the"
        " entry is stale; drop the count or the entry. Never just re-pin the"
        " number to whatever was measured — say in the comment why it moved.")


def test_the_threshold_constant_is_also_defined_exactly_once():
    # The same regrowth risk applies to the 15-minute grace/idle window: a
    # consumer that hardcodes 900 diverges silently the day it is retuned.
    #
    # The needle is `THRESHOLD_SECS`, not the old `IDLE_THRESHOLD`: the constant
    # is now `STUCK_THRESHOLD_SECS` (it gates every disjunct, not just the idle
    # clock), and a needle pinned to the previous NAME would have gone quietly
    # vacuous the moment it was renamed — a guard that stops matching is
    # indistinguishable from a guard that finds nothing. `_SECS` is what keeps it
    # off unrelated thresholds like session-manager's DEFAULT_STALE_THRESHOLD.
    offenders = scan_for_threshold_respellings(
        REPO, skip=(LIB_REL, "scripts/tests/test_clawgate_tasks.py",
                    "scripts/tests/test_clawgate_predicate_single_source.py"))
    assert not offenders, (
        "a stuck-threshold constant was re-spelled outside the shared module:\n  "
        + "\n  ".join(offenders)
        + "\n\nUse clawgate_tasks.STUCK_THRESHOLD_SECS.")


def test_the_threshold_needle_can_actually_fire():
    # 🔴 POSITIVE CONTROL for the scan above. It reports a count of ZERO on a
    # healthy tree, which is indistinguishable from a needle that matches
    # nothing — precisely how renaming the constant would have retired the old
    # `IDLE_THRESHOLD` needle in silence. Both the current name and a plausible
    # re-spelling must be caught by the same predicate the loop uses.
    def caught(line):
        return ("THRESHOLD_SECS" in line and "=" in line
                and "CG." not in line and "clawgate_tasks" not in line)

    assert caught("STUCK_THRESHOLD_SECS = 900")
    assert caught("AGENT_IDLE_THRESHOLD_SECS = 900")     # the previous name
    assert caught("MY_OWN_THRESHOLD_SECS=900")
    # …and does not fire on a legitimate use of the shared constant, nor on the
    # unrelated staleness threshold that lives in session-manager.
    assert not caught("threshold = CG.STUCK_THRESHOLD_SECS")
    assert not caught("DEFAULT_STALE_THRESHOLD = 3600")


# =========================================================================== #
# THE IMPORTER LEDGER — the SEAM, asserted in both directions
# =========================================================================== #
def _docstring_node_ids(tree) -> set:
    """Ids of every docstring Constant, so PROSE mentioning the module does not
    read as loading it."""
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None) or []
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            out.add(id(body[0].value))
    return out


def _loads_shared_module(path: Path) -> bool:
    """Does this file load `clawgate_tasks`, by ANY mechanism or helper name?

    🔴 SPELLED vs STRUCTURAL. This used to require the literal helper name
    `_load_clawgate_tasks`, so a fourth consumer that loaded the module under any
    other name was invisible to the ledger and the "GROWS" half — the half that
    catches a new surface rendering this queue without review — was defeated by a
    rename. Nothing about the hazard depends on what the helper is called.

    What a loader CANNOT avoid is naming the module: either as a path string
    handed to an explicit loader, or as an import name. Both are matched, so
    switching mechanism does not evade it either. Docstrings are excluded, so a
    file that merely writes ABOUT the module is not counted as a consumer.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return False  # pragma: no cover - not raised over the tracked corpus
    skip = _docstring_node_ids(tree)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and "clawgate_tasks" in node.value and id(node) not in skip):
            return True
        if isinstance(node, ast.Import):
            if any("clawgate_tasks" in a.name for a in node.names):
                return True
        if isinstance(node, ast.ImportFrom):
            if node.module and "clawgate_tasks" in node.module:
                return True
    return False


@pytest.mark.parametrize("snippet", [
    # the real shape, under the helper name the ledger used to require…
    'def _load_clawgate_tasks():\n    p = "scripts/lib/clawgate_tasks.py"\n',
    # …and under three names it never heard of. THIS is the hole: a rename made
    # a genuine fourth consumer invisible and left the ledger green.
    'def _boot():\n    p = "scripts/lib/clawgate_tasks.py"\n',
    'CG = _import_by_path("lib/clawgate_tasks.py")',
    'x = os.path.join(D, "lib", "clawgate_tasks.py")',
    # a different MECHANISM entirely — sys.path plus a plain import
    'import clawgate_tasks',
    'from clawgate_tasks import PENDING_TASK_STATES',
    'from scripts.lib.clawgate_tasks import attention',
])
def test_positive_control_the_ledger_sees_a_loader_under_ANY_name(snippet,
                                                                  tmp_path):
    p = tmp_path / "consumer.py"
    p.write_text(snippet)
    assert _loads_shared_module(p), snippet


@pytest.mark.parametrize("snippet", [
    '"""This module is unrelated to clawgate_tasks.py."""\n',   # module docstring
    'def f():\n    """See clawgate_tasks.py for the predicate."""\n',
    'x = 1   # clawgate_tasks.py has the real one\n',           # a comment
    'import json',
])
def test_negative_control_prose_about_the_module_is_not_a_consumer(snippet,
                                                                   tmp_path):
    # A file that merely mentions the module must not join the ledger, or every
    # doc edit becomes a ledger failure and the ledger stops being read.
    p = tmp_path / "prose.py"
    p.write_text(snippet)
    assert not _loads_shared_module(p), snippet


def test_exactly_the_expected_surfaces_import_the_shared_module():
    found = set()
    for p in python_files(REPO / "scripts"):
        rel = p.relative_to(REPO).as_posix()
        if rel.startswith("scripts/tests/") or rel == LIB_REL:
            continue
        if _loads_shared_module(p):
            found.add(rel)
    assert found == EXPECTED_IMPORTERS, (
        "the set of surfaces sharing the clawgate predicate changed.\n"
        "  expected: %s\n  found:    %s\n"
        "GROWING means a new surface renders this queue and its output was "
        "never reviewed here; SHRINKING means a consumer went back to its own "
        "copy, which is the bug this module exists to prevent."
        % (sorted(EXPECTED_IMPORTERS), sorted(found)))


def test_each_importer_resolves_the_module_by_explicit_path_not_sys_path():
    # scripts/lib/ holds unrelated modules; a sys.path insert would let any of
    # them shadow a name these consumers rely on (the CLAUDE.md _db.py/llm.py
    # gotcha). Also: a consumer may be DEPLOYED as a lone nix-store copy, so it
    # must fall back to $DEVRC_DIR rather than assume a sibling lib/ exists.
    for rel in sorted(EXPECTED_IMPORTERS):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "SourceFileLoader" in text, rel
        assert "DEVRC_DIR" in text or "devrc" in text, rel


def test_no_importer_keeps_a_hardcoded_fallback_predicate():
    # 🔴 A fallback copy is how this bug survived: two copies, both stale. If
    # the shared module cannot be loaded the consumer must FAIL, not degrade to
    # a set it spelled itself. (The state-set scan above enforces the literal;
    # this pins the intent in the ImportError.)
    for rel in sorted(EXPECTED_IMPORTERS):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "raise ImportError" in text, \
            "%s must raise rather than fall back to its own predicate" % rel


def test_the_nix_unit_re_arms_when_the_shared_predicate_changes():
    # The poller loads the module by explicit path out of the WORKING TREE, so
    # without this trigger a change to the predicate leaves the unit definition
    # byte-identical and the timer never re-arms — the pill would keep the old
    # meaning across a switch that reported success.
    nix = (REPO / "nix" / "graphical.nix").read_text(encoding="utf-8")
    assert "scripts/lib/clawgate_tasks.py" in nix, \
        "add ${../scripts/lib/clawgate_tasks.py} to bar-status-poll's " \
        "X-Restart-Triggers"


def test_the_shared_module_is_present_and_tracked():
    # 🔴 An untracked file is silently absent from the flake source, so the
    # deploy succeeds and the module simply is not there (CLAUDE.md).
    #
    # Two checks, because neither alone covers both tiers: EXISTENCE is the one
    # that means something inside the nix sandbox (the store copy is built from
    # tracked files only, so an untracked module would simply not be here, and
    # every importer would fail); TRACKEDNESS is the one that means something on
    # a dev host, where the file exists whether or not git knows about it. The
    # git half is conditional on a git dir being present rather than skipped
    # outright, so it cannot go quietly vacuous on the tier that has one.
    import subprocess
    assert (REPO / LIB_REL).exists(), "%s is missing from this tree" % LIB_REL
    if not (REPO / ".git").exists():
        return  # pragma: no cover - REPO/.git always exists in a checkout
    out = subprocess.run(["git", "-C", str(REPO), "ls-files", "--error-unmatch",
                          LIB_REL], capture_output=True, text=True)
    assert out.returncode == 0, "%s is not git-tracked" % LIB_REL
