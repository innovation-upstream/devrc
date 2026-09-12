"""Every site that bounds a nested runner spawn with its OWN number, enumerated.

WHAT IS BEING PROTECTED
-----------------------
`scripts/testlib/scoped_harness.py` defines `RUNNER_TIMEOUT_S` and its comment
used to say it was *"THE one place this bound is written."* 🔴 **That was false
when it was written**, and this file is the mechanical reason it cannot be false
again silently: measured 2026-09-11 on `e5f4e2f0`, **eight** further sites across
four files spawn a runner script carrying a bound of their own, at **three
distinct values** (30, 120, 300).

🔴 **THIS IS A LEDGER, NOT A SCANNER, AND THE DIFFERENCE IS THE WHOLE DESIGN.**
The obvious fix for "a guard that only sees one file" is a repo-wide heuristic
that finds every runner spawn and asserts its bound. That was built first and
**measured wrong in BOTH directions on a population of nine**:

  * FALSE POSITIVE — `test_run_tests_floors.py` sources ONE function out of
    `run-tests.sh` (`sed -n "/^_suggested_floor()/,/^}}/p"`) and calls it. Its
    argv names the runner, so a syntactic scan flags it; it never runs a suite
    and wants no hang bound at all.
  * FALSE NEGATIVE — `test_run_tests_preconditions.py`'s `_run` helper takes
    `timeout: int = 300` and spawns `["bash", *args]`. The runner arrives as an
    ARGUMENT, so no scan of that call's argv can see which script it runs.

Two errors in nine is ~22%. `claude/RULES.md`: *a permanently-red gate is worse
than no gate* — it trains everyone to click through. So the discovery pass below
is deliberately **advisory input to a hand-maintained ledger**, and the assertion
is on the LEDGER, which is exact. A new site fails this test; so does a removed
one. That is the `asserted ledger of every writer/caller, failing when the set
GROWS *or* SHRINKS` shape RULES.md prescribes for a seam nobody owns.

🔴 **THIS FILE CHANGES NO BOUND AND IS NOT A CLAIM THAT ANY OF THEM IS WRONG.**
Each entry below carries the reason its site has its own number. Several are
plainly legitimate — a test that asserts the runner ABORTS in seconds wants a
short bound, not the 600 s that governs full nested suites. What was wrong was
the *sentence* claiming they did not exist.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from testlib.scoped_harness import RUNNER_TIMEOUT_S  # noqa: E402

#: Callables on `subprocess` that start a process.
_SPAWNING_CALLABLES = {"run", "check_output", "check_call", "call", "Popen"}

#: A spawn whose argv names one of these is *candidate* runner traffic. Candidate
#: only — see the FALSE POSITIVE in the module docstring.
_RUNNER_TOKENS = ("run-tests.sh", "gate.sh", "scoped-tests.sh", "run-node-tests.sh")

#: 🔴 THE LEDGER. Key: (repo-relative path, how the bound is spelled). Value:
#: (how many such calls, why this site writes its own number).
#:
#: Spelling is `LITERAL <n>` for a numeric literal, `name <id>` for a parameter
#: or constant. Keying on the SPELLING and a COUNT rather than on line numbers is
#: deliberate: line numbers churn on every edit above them, and a ledger that
#: goes red for an unrelated insertion is one nobody keeps.
_OWN_BOUND_LEDGER: dict[tuple[str, str], tuple[int, str]] = {
    ("scripts/tests/test_devshell_satisfies_required_tools.py", "LITERAL 120"): (
        2,
        "asserts run-tests.sh ABORTS on a stubbed PATH with required tools "
        "missing. The run under test is expected to die in seconds; 600 would "
        "turn a wedged case into a 10-minute stall for no added coverage.",
    ),
    ("scripts/tests/test_gate_exit_truthfulness.py", "LITERAL 120"): (
        1,
        "drives gate.sh to a known non-zero exit; the run is short by "
        "construction.",
    ),
    ("scripts/tests/test_gate_exit_truthfulness.py", "name timeout"): (
        1,
        "a local helper parameterises its own bound; callers pass a value per "
        "case rather than sharing one.",
    ),
    ("scripts/tests/test_gate_reexec.py", "LITERAL 120"): (
        2,
        "exercises gate.sh's nix-develop re-exec; bounded short because the "
        "cases under test fail fast or not at all.",
    ),
    ("scripts/tests/test_gate_reexec.py", "LITERAL 30"): (
        1,
        "the DEVRC_GATE_NO_REEXEC opt-out path, which does no work and must "
        "return immediately — a long bound here would hide a regression.",
    ),
    ("scripts/tests/test_gate_reexec.py", "name timeout"): (
        1,
        "a local helper parameterises its own bound, as above.",
    ),
    ("scripts/tests/test_run_tests_floors.py", "ABSENT"): (
        1,
        "🔴 NOT RUNNER TRAFFIC — the documented FALSE POSITIVE. It `sed`s "
        "`_suggested_floor()` out of run-tests.sh and sources it to compare the "
        "shell rule against the Python one. No suite runs, so no hang bound is "
        "wanted. Listed so the discovery pass stays honest rather than "
        "allowlisted away invisibly.",
    ),
}

#: 🔴 SITES THE DISCOVERY PASS STRUCTURALLY CANNOT FIND — asserted separately,
#: by reading the file, because no scan of a call's argv can resolve a runner
#: path that arrives as a parameter. Key: path -> (the exact source line, why).
_INVISIBLE_TO_DISCOVERY: dict[str, tuple[str, str]] = {
    "scripts/tests/test_run_tests_preconditions.py": (
        "def _run(args: list[str], env: dict | None = None, timeout: int = 300):",
        "spawns `['bash', *args]`, so the runner path is in the CALLER's args "
        "and this call's argv names no script. The 300 is a fourth distinct "
        "value for the same predicate and is exactly what the 'one place' "
        "sentence denied existed.",
    ),
}

#: Positive control for the discovery pass. Below this, it has stopped finding
#: the population it is supposed to describe and every comparison against the
#: ledger would be passing over near-nothing.
_MIN_DISCOVERED_CALLS = 7


def _subprocess_aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Names that really resolve to `subprocess`, never a same-named helper.

    `scoped_harness.run()` is imported bare as `run` in several suites and is
    the CORRECT shared path; conflating it with `subprocess.run` would report
    ~22 compliant calls as offenders.
    """
    module_aliases: set[str] = set()
    direct_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "subprocess":
                    module_aliases.add(a.asname or a.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for a in node.names:
                if a.name in _SPAWNING_CALLABLES:
                    direct_aliases.add(a.asname or a.name)
    return module_aliases, direct_aliases


def _runner_bearing_names(tree: ast.AST) -> set[str]:
    """Assignment targets whose value mentions a runner script."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            src = ast.dump(node.value)
            if any(tok in src for tok in _RUNNER_TOKENS):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        out.add(target.id)
    return out


def _spell_bound(node: ast.Call) -> str:
    kw = {k.arg: k.value for k in node.keywords if k.arg}
    bound = kw.get("timeout")
    if bound is None:
        return "ABSENT"
    if isinstance(bound, ast.Constant):
        return f"LITERAL {bound.value}"
    if isinstance(bound, ast.Name):
        return f"name {bound.id}"
    return type(bound).__name__


def _discover() -> dict[tuple[str, str], int]:
    """Every `subprocess` spawn under scripts/ whose argv names a runner."""
    found: dict[tuple[str, str], int] = {}
    for path in sorted(REPO_ROOT.glob("scripts/**/*.py")):
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (OSError, SyntaxError):
            continue

        module_aliases, direct_aliases = _subprocess_aliases(tree)
        runner_names = _runner_bearing_names(tree)
        if not runner_names and not any(tok in text for tok in _RUNNER_TOKENS):
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            is_subprocess = (
                isinstance(fn, ast.Attribute)
                and fn.attr in _SPAWNING_CALLABLES
                and isinstance(fn.value, ast.Name)
                and fn.value.id in module_aliases
            ) or (isinstance(fn, ast.Name) and fn.id in direct_aliases)
            if not is_subprocess:
                continue

            argv = ast.dump(ast.Tuple(elts=list(node.args), ctx=ast.Load()))
            if not (
                any(tok in argv for tok in _RUNNER_TOKENS)
                or any(name in argv for name in runner_names)
            ):
                continue

            key = (str(path.relative_to(REPO_ROOT)), _spell_bound(node))
            found[key] = found.get(key, 0) + 1
    return found


def test_the_discovery_pass_still_finds_the_population_it_describes():
    """POSITIVE CONTROL — without it every comparison below can pass on nothing.

    A refactor that moves these spawns behind a helper, or a rename of the
    runner scripts, empties the walk. Both assertions in the ledger test are
    accumulated over what the walk FOUND, so an empty walk satisfies them by
    finding nothing — the failure mode that 'passes hardest on the worst
    outcome'.
    """
    total = sum(_discover().values())
    assert total >= _MIN_DISCOVERED_CALLS, (
        f"the discovery pass found {total} runner spawn(s), below the floor of "
        f"{_MIN_DISCOVERED_CALLS}. Either these calls moved behind a shared "
        "helper — in which case DELETE their ledger rows and lower this floor "
        "in the same commit — or the scan broke and the ledger test below is "
        "now vacuous. Both need a human, not a green."
    )


def test_every_site_writing_its_OWN_runner_bound_is_in_the_ledger():
    """🔴 TWO-WAY. Fails when the set GROWS *and* when it SHRINKS.

    Growing means a new site started writing its own bound and nobody recorded
    why. Shrinking means a recorded site was consolidated or deleted and the
    ledger now describes a repo that no longer exists — which is how the
    `scoped_harness` sentence rotted in the first place.
    """
    discovered = _discover()
    expected = {k: v[0] for k, v in _OWN_BOUND_LEDGER.items()}

    new = {k: n for k, n in discovered.items() if k not in expected}
    gone = {k: n for k, n in expected.items() if k not in discovered}
    moved = {
        k: (expected[k], discovered[k])
        for k in expected.keys() & discovered.keys()
        if expected[k] != discovered[k]
    }

    assert not new, (
        "these spawn a runner with a bound of their own and are NOT in "
        "`_OWN_BOUND_LEDGER`:\n  "
        + "\n  ".join(f"{f}  [{b}] x{n}" for (f, b), n in sorted(new.items()))
        + "\n\nAdd a row WITH ITS REASON, or route the call through "
        "`testlib.scoped_harness.run()` so it takes `RUNNER_TIMEOUT_S`. Do not "
        "add a row without a reason — an unexplained row is how the next reader "
        "concludes the duplication is deliberate."
    )
    assert not gone, (
        "the ledger records sites that no longer exist:\n  "
        + "\n  ".join(f"{f}  [{b}] x{n}" for (f, b), n in sorted(gone.items()))
        + "\n\nIf they were consolidated onto `RUNNER_TIMEOUT_S`, delete the "
        "row — and check whether `scoped_harness.py`'s comment about how many "
        "places write this bound is now stale in the other direction."
    )
    assert not moved, (
        "the ledger's COUNT disagrees with the repo:\n  "
        + "\n  ".join(
            f"{f}  [{b}]: ledger says {exp}, found {act}"
            for (f, b), (exp, act) in sorted(moved.items())
        )
        + "\n\nA count that drifts up is a new duplicate of the same predicate."
    )


@pytest.mark.parametrize("path", sorted(_INVISIBLE_TO_DISCOVERY))
def test_the_sites_no_scan_can_find_are_still_spelled_as_recorded(path):
    """The false-negative half, pinned by reading the source.

    🔴 These cannot be found by inspecting a call's argv, so the ONLY thing
    keeping them honest is this literal pin. If it fails, the line was edited:
    re-read it, and update the ledger row rather than deleting the pin.
    """
    line, _reason = _INVISIBLE_TO_DISCOVERY[path]
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    assert line in text, (
        f"{path} no longer contains the recorded line:\n  {line!r}\n\n"
        "This site bounds a runner spawn where the runner path arrives as a "
        "PARAMETER, so no discovery pass can see it. If it was consolidated "
        "onto `RUNNER_TIMEOUT_S`, delete its `_INVISIBLE_TO_DISCOVERY` row; if "
        "it merely moved or was reformatted, update the recorded line."
    )


def test_every_ledger_row_carries_a_REASON():
    """A row with no reason is an allowlist entry, and allowlists rot silently.

    The point of this file is that a duplicated bound is *explained*, not that
    it is *permitted*.
    """
    unexplained = [
        f"{f} [{b}]"
        for (f, b), (_n, reason) in _OWN_BOUND_LEDGER.items()
        if len(reason.strip()) < 40
    ]
    assert not unexplained, (
        "these ledger rows have no usable reason:\n  " + "\n  ".join(unexplained)
    )
    for path, (_line, reason) in _INVISIBLE_TO_DISCOVERY.items():
        assert len(reason.strip()) >= 40, f"{path}'s blind-spot row has no reason"


def test_the_shared_bound_is_still_the_one_the_ledger_is_measured_against():
    """INVARIANT GUARD, labelled as one: no bug ever violated this.

    It pins that `RUNNER_TIMEOUT_S` exists and is a plain int, because every
    message above tells a reader to route calls through it. A rename would make
    that advice unfollowable while this file stayed green.
    """
    assert isinstance(RUNNER_TIMEOUT_S, int) and RUNNER_TIMEOUT_S > 0, (
        f"RUNNER_TIMEOUT_S is {RUNNER_TIMEOUT_S!r}; the ledger's remediation "
        "advice names it as the shared bound to route calls through."
    )
