"""REPO-WIDE seam guard: the clawgate TASK base URL is resolved ONE way.

WHY
---
The task/board service was extracted out of the permission router, so the two
now run on two base URLs, and the precedence that picks between them
(`CLAWGATE_TASK_API_URL` -> `CLAWGATE_API_URL` -> a shared default) is a
predicate like any other. `claude/RULES.md` -> "One rule, one place": open-coded
at N sites it is typically wrong at N-1 of them, in the same direction.

Two of the three consumers import the shared definition outright (the board
poller, and `scripts/mail-actions/clawgate.py`). The third,
`scripts/signal/clawgate.py`, CANNOT:

  * it runs from a container image whose Dockerfile COPYs the modules of its own
    directory BY NAME — `COPY . .` is refused on purpose, devrc is public — and
    whose dockerignore denies `**` and re-admits only those same names;
  * two build-time controls assert the image's file set EXACTLY, in both
    directions, and a gate test pins the COPY list against that directory's
    contents;

so an import would mean widening a deliberately narrow, security-motivated
allowlist to carry a six-hundred-line module for three lines of it. The copy is
kept instead — and pinned HERE, mechanically, rather than by good intentions.

WHAT IS CHECKED, AND WHY BEHAVIOURALLY
--------------------------------------
🔴 A guard on the LEDGER alone is a guard on a SPELLING: the two modules could
name the same two variables in the same order and still disagree about which one
wins, about an empty value, or about a trailing slash. So the ledger and the
default are compared AND the two resolvers are driven over the same table of
environments and required to return the same string for every row — including
the rows where a wrong precedence is the only thing that separates them.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LIB_REL = "scripts/lib/clawgate_tasks.py"
SIGNAL_REL = "scripts/signal/clawgate.py"
MAIL_REL = "scripts/mail-actions/clawgate.py"

#: Fixture bases. Pairwise distinct AND distinct from the shared default, so a
#: mutant that collapses one resolver onto the other key — or onto the default —
#: cannot survive by returning a value that happens to match.
TASK_BASE = "http://task-api.example:18081"
ROUTER_BASE = "http://router-api.example:27443"

#: Every environment the two resolvers must agree on. The first three rows are
#: the ones a wrong precedence gets wrong; the rest are the edges.
ENV_TABLE = [
    {"CLAWGATE_TASK_API_URL": TASK_BASE, "CLAWGATE_API_URL": ROUTER_BASE},
    {"CLAWGATE_TASK_API_URL": TASK_BASE},
    {"CLAWGATE_API_URL": ROUTER_BASE},
    {},
    {"CLAWGATE_TASK_API_URL": "", "CLAWGATE_API_URL": ROUTER_BASE},
    {"CLAWGATE_TASK_API_URL": TASK_BASE + "/", "CLAWGATE_API_URL": ROUTER_BASE},
    {"CLAWGATE_TASK_API_URL": "", "CLAWGATE_API_URL": ""},
    {"CLAWGATE_API_URL": ROUTER_BASE + "///"},
]


def _load(rel: str, name: str, extra_path: str | None = None):
    """Load a module by EXPLICIT PATH, optionally with a directory on sys.path.

    `scripts/signal/clawgate.py` imports a sibling by bare name, so its own
    directory has to be importable for the load to succeed — the same way the
    image arranges it.
    """
    path = REPO / rel
    added = False
    if extra_path and extra_path not in sys.path:
        sys.path.insert(0, extra_path)
        added = True
    try:
        loader = importlib.machinery.SourceFileLoader(name, str(path))
        spec = importlib.util.spec_from_file_location(name, str(path),
                                                      loader=loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        return mod
    finally:
        if added:
            sys.path.remove(extra_path)


@pytest.fixture(scope="module")
def shared():
    return _load(LIB_REL, "_guard_clawgate_tasks")


@pytest.fixture(scope="module")
def signal_mod():
    return _load(SIGNAL_REL, "_guard_signal_clawgate",
                 extra_path=str(REPO / "scripts" / "signal"))


# =========================================================================== #
# 🔴 HARNESS CONTROLS. Every assertion below is `derived == derived`, and two
# wrong-but-equal values compare equal just as happily as two right ones. So
# first: prove the table can DISCRIMINATE — that a resolver with the precedence
# reversed disagrees with the shared one on it. A table that cannot separate
# those two would pass with either module broken.
# =========================================================================== #
def _reversed_precedence(env, names, default):
    for name in reversed(names):
        value = env.get(name)
        if value:
            return value.rstrip("/")
    return default.rstrip("/")


def test_the_table_separates_the_two_possible_precedences(shared):
    disagreements = [
        env for env in ENV_TABLE
        if shared.task_base_url(env) != _reversed_precedence(
            env, shared.TASK_API_URL_VARS, shared.DEFAULT_API_URL)]
    assert disagreements, (
        "HARNESS BROKEN: no row in ENV_TABLE distinguishes the correct "
        "precedence from the reversed one, so every parity assertion below "
        "would pass with either module's ledger swapped")


def test_the_modules_under_test_actually_loaded(shared, signal_mod):
    # A fixture that silently produced an empty module would make every parity
    # check vacuous. Name the attributes the checks rely on.
    for mod, attr in ((shared, "task_base_url"), (signal_mod, "task_base_url"),
                      (signal_mod, "task_endpoint")):
        assert callable(getattr(mod, attr, None)), (mod, attr)
    assert len(ENV_TABLE) >= 6


# =========================================================================== #
# THE GUARD
# =========================================================================== #
def test_the_signal_copy_names_the_same_ledger_in_the_same_order(shared,
                                                                 signal_mod):
    assert signal_mod.TASK_API_URL_VARS == shared.TASK_API_URL_VARS, (
        "scripts/signal/clawgate.py carries a DELIBERATE copy of the task-URL "
        "ledger (it cannot import the shared module — see its docstring). The "
        "copy has drifted: %r vs the shared %r. ORDER is the contract, not "
        "decoration: the specific key must win and the general one must be the "
        "fallback." % (signal_mod.TASK_API_URL_VARS, shared.TASK_API_URL_VARS))


def test_the_signal_copy_carries_the_same_default(shared, signal_mod):
    assert signal_mod.DEFAULT_API_URL == shared.DEFAULT_API_URL, (
        "the copied default drifted: %r vs the shared %r. A host told nothing "
        "about the split must resolve identically on both surfaces."
        % (signal_mod.DEFAULT_API_URL, shared.DEFAULT_API_URL))


@pytest.mark.parametrize("env", ENV_TABLE, ids=range(len(ENV_TABLE)))
def test_the_two_resolvers_agree_on_every_environment(shared, signal_mod, env):
    """🔴 BEHAVIOUR, not spelling. Equal ledgers and equal defaults still leave
    room to disagree about which key wins, about an empty value, and about a
    trailing slash — all three are rows in the table."""
    assert signal_mod.task_base_url(env) == shared.task_base_url(env), (
        "the Signal copy and the shared definition resolve %r differently: "
        "%r vs %r" % (env, signal_mod.task_base_url(env),
                      shared.task_base_url(env)))


def test_the_mail_actions_producer_resolves_THROUGH_the_shared_module():
    """The other half of the seam, asserted in the opposite direction: the
    on-host producer must not have grown a copy of its own. It is loaded here by
    source text rather than imported, because what is being pinned is that the
    shared module is what it reaches for."""
    text = (REPO / MAIL_REL).read_text(encoding="utf-8")
    assert "clawgate_tasks.py" in text, (
        "%s no longer loads the shared module by explicit path — if it grew a "
        "local copy of the precedence, delete it and import instead." % MAIL_REL)
    assert "raise ImportError" in text, (
        "%s must FAIL when the shared module cannot be loaded rather than fall "
        "back to a copy of the rule — a silent fallback is how two copies get "
        "out of step in the first place." % MAIL_REL)


def test_neither_producer_still_hardcodes_a_task_endpoint():
    """🔴 THE ORIGINAL DEFECT, pinned so it cannot come back in either file.

    Both modules held `ENDPOINT = "http://<host>:<port>/api/tasks"`. The shape
    that must never return is a base URL and the tasks path welded together in
    one literal; the bare default constant is fine and is checked above.
    """
    for rel in (MAIL_REL, SIGNAL_REL):
        text = (REPO / rel).read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("*"):
                continue          # a comment ABOUT the old literal is fine
            assert not (stripped.startswith("ENDPOINT") and "=" in stripped), (
                "%s:%d re-introduced a hardcoded endpoint constant: %s\n"
                "Resolve the base from configuration instead (task_endpoint)."
                % (rel, i, stripped))


def test_the_hardcoded_endpoint_needle_can_actually_fire():
    """POSITIVE CONTROL for the scan above: a guard reporting zero on a clean
    tree is indistinguishable from one wired to nothing."""
    def caught(line):
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("*"):
            return False
        return stripped.startswith("ENDPOINT") and "=" in stripped

    assert caught('ENDPOINT = "http://host:1/api/tasks"')
    assert caught("    ENDPOINT='http://host:1/api/tasks'")
    assert not caught('# ENDPOINT = "http://host:1/api/tasks" (removed)')
    assert not caught('TASKS_PATH = "/api/tasks"')
