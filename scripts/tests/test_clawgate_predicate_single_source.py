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
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LIB_REL = "scripts/lib/clawgate_tasks.py"

#: The pending states, spelled here ONCE so this guard can look for them. This
#: file is its own allowlist entry below — a scanner has to name what it hunts.
TARGET = {"open", "ready_for_review"}

#: 🔴 Paths permitted to contain the literal, each with the reason.
#: Accounting is TWO-WAY (same discipline as test_runtime_shebangs' allowlist):
#: an unlisted hit fails, and a listed path that no longer hits also fails, so a
#: rubber stamp cannot outlive the thing it stamped.
ALLOWLIST = {
    # THE definition. Everything else imports it.
    LIB_REL,
    # This guard: it must name the set it hunts for.
    "scripts/tests/test_clawgate_predicate_single_source.py",
    # The unit suite asserts the definition's literal value — a contract test
    # for the constant is the one legitimate second spelling.
    "scripts/tests/test_clawgate_tasks.py",
    # 🔴 A DIFFERENT RULE that this scanner's superset test legitimately catches,
    # found by writing it. `initiatives/tasks.OPEN_STATUSES` is
    # ("open", "in_progress", "ready_for_review") — the initiatives viewer's
    # DUPLICATE-DISPATCH guard ("is there already live work for this
    # initiative?"), which deliberately INCLUDES `in_progress` and deliberately
    # fails open on an unknown status. It answers "is this task still live?",
    # not "does this need the operator?", so folding the two together would give
    # the bar an in_progress task it must not count and give the dispatch guard
    # a stuck-agent notion it has no use for. Kept separate ON PURPOSE.
    "scripts/initiatives/tasks.py",
    # The same rule re-spelled in that suite's `_tv` fixture (it builds the
    # `open` flag by hand rather than importing OPEN_STATUSES). Pre-existing and
    # out of scope here; noted so the next reader does not think it is this one.
    "scripts/initiatives/tests/test_viewer.py",
}

#: 🔴 The importer ledger. Exactly these files load the shared module.
EXPECTED_IMPORTERS = {
    "scripts/bar-status-poll",      # the 45s bar poller, writes the cache
    "scripts/agent-ops",            # the mission-control TUI, reads it live
    "scripts/session-manager",      # the cross-host JSON report
}

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".direnv", "result",
             ".claude"}

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
        return False
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
    """The string constants directly inside a set/list/tuple/frozenset node, or
    None if this node is not one of those containers."""
    elts = None
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        elts = node.elts
    elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
          and node.func.id in {"frozenset", "set", "list", "tuple"}
          and len(node.args) == 1
          and isinstance(node.args[0], (ast.Set, ast.List, ast.Tuple))):
        elts = node.args[0].elts
    if elts is None:
        return None
    return {e.value for e in elts
            if isinstance(e, ast.Constant) and isinstance(e.value, str)}


def find_state_set_literals(source: str):
    """Line numbers of every literal container whose strings ⊇ TARGET."""
    hits = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        members = _string_members(node)
        if members is not None and TARGET <= members:
            hits.append(getattr(node, "lineno", 0))
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
# THE GUARD
# =========================================================================== #
def test_the_pending_state_set_is_defined_exactly_once():
    offenders = {}
    for p in python_files(REPO):
        rel = p.relative_to(REPO).as_posix()
        try:
            hits = find_state_set_literals(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        if hits and rel not in ALLOWLIST:
            offenders[rel] = hits
    assert not offenders, (
        "a second copy of the clawgate operator-pending state set appeared:\n"
        + "\n".join("  %s: line(s) %s" % (k, v) for k, v in offenders.items())
        + "\n\nImport it instead: scripts/lib/clawgate_tasks.PENDING_TASK_STATES."
        " That module also owns AGENT_IDLE_THRESHOLD_SECS and the stuck"
        " predicate. If a second spelling is genuinely required, add it to"
        " ALLOWLIST in this file WITH the reason.")


def test_every_allowlist_entry_still_carries_the_literal():
    # Two-way accounting: a stamp that outlives what it stamped is a lie the
    # next reader will trust.
    stale = []
    for rel in sorted(ALLOWLIST):
        p = REPO / rel
        if not p.exists():
            stale.append("%s (file is gone)" % rel)
            continue
        if not find_state_set_literals(p.read_text(encoding="utf-8")):
            stale.append("%s (no longer contains the literal)" % rel)
    assert not stale, ("ALLOWLIST entries no longer needed — delete them:\n  "
                       + "\n  ".join(stale))


def test_the_threshold_constant_is_also_defined_exactly_once():
    # The same regrowth risk applies to the 15-minute idle threshold: a consumer
    # that hardcodes 900 diverges silently the day the constant is retuned.
    offenders = []
    for p in python_files(REPO):
        rel = p.relative_to(REPO).as_posix()
        if rel in (LIB_REL, "scripts/tests/test_clawgate_tasks.py",
                   "scripts/tests/test_clawgate_predicate_single_source.py"):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if "IDLE_THRESHOLD" in line and "=" in line and "CG." not in line \
                    and "clawgate_tasks" not in line:
                offenders.append("%s:%d %s" % (rel, i, line.strip()))
    assert not offenders, (
        "an idle-threshold constant was re-spelled outside the shared module:\n  "
        + "\n  ".join(offenders)
        + "\n\nUse clawgate_tasks.AGENT_IDLE_THRESHOLD_SECS.")


# =========================================================================== #
# THE IMPORTER LEDGER — the SEAM, asserted in both directions
# =========================================================================== #
def _loads_shared_module(path: Path) -> bool:
    text = path.read_text(encoding="utf-8", errors="replace")
    return "clawgate_tasks.py" in text and "_load_clawgate_tasks" in text


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
    # gotcha). Also: agent-ops is DEPLOYED as a lone nix-store copy, so it must
    # fall back to $DEVRC_DIR rather than assume a sibling lib/ exists.
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
        return
    out = subprocess.run(["git", "-C", str(REPO), "ls-files", "--error-unmatch",
                          LIB_REL], capture_output=True, text=True)
    assert out.returncode == 0, "%s is not git-tracked" % LIB_REL
