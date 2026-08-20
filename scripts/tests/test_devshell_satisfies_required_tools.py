"""The gate's tool precondition must be DISCOVERABLE, and stay satisfiable.

THE DEFECT (measured on origin/main, a29b97b):

    $ nix-shell -p 'python312.withPackages(...)' \
        --run "bash scripts/run-tests.sh ." 2>&1 | tail -5
    run-tests: FATAL — required tool(s) missing from PATH: logrotate
      ...  Add them to the caller's inputs (flake.nix checks.pytests
      nativeBuildInputs / the pre-push nix-shell) — do NOT drop them from
      REQUIRED_TOOLS to make this pass.
    RESULT: FAIL (exit=2)

The precondition itself is CORRECT and is not what changed: the suites `skipif`
on these binaries, so a missing one takes the run green while testing less.
What was wrong was discoverability. The remedy it named — `flake.nix
checks.pytests nativeBuildInputs` — is a derivation you cannot stand inside, and
the repo had NO devShell, so a contributor's only route was to reverse-engineer
the list into an ad-hoc `nix-shell -p ...`. Get it wrong and you get a red gate
that reads like a code failure.

The fix is a `devShells.default` built from the SAME `gateTools` list as
`checks.pytests`, and a FATAL that names the exact invocation.

WHAT EACH TEST HERE IS:

  * test_the_missing_tool_fatal_names_a_runnable_invocation
        REGRESSION. Red at a29b97b (no `nix develop` anywhere in the message),
        green at HEAD.
  * test_a_default_devshell_exists  /  test_the_devshell_and_the_gate_share_one
    tool_list  /  test_gate_tools_is_defined_exactly_once
        SEAM guard, NOT regression coverage. They pin the RELATIONSHIP that
        makes the fix durable: one list, two consumers.

🔴 SCOPE OF THE CLAIM. These are source-level checks; they do NOT build the
shell. That `gateTools` actually satisfies REQUIRED_TOOLS is proven elsewhere
and better — `checks.pytests` runs this very runner with
`nativeBuildInputs = gateTools` in the nix sandbox, so if the list were
insufficient the gate would exit 2 on the precondition. The only way that proof
stops transferring to the devShell is the two consumers drifting apart, which is
exactly what the seam tests below forbid. Deliberately not re-verified by a nix
build here: nested nix builds are not available in the hermetic sandbox tier,
so such a test would be a skip pretending to be coverage.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_TESTS = REPO_ROOT / "scripts" / "run-tests.sh"
FLAKE = REPO_ROOT / "flake.nix"


def _fatal_block() -> str:
    """The `required tool(s) missing` FATAL block, from its echo to the exit."""
    src = RUN_TESTS.read_text()
    start = src.find("required tool(s) missing from PATH")
    assert start != -1, (
        "could not find the 'required tool(s) missing from PATH' FATAL in "
        "run-tests.sh. If it was reworded, update this parser -- do NOT delete "
        "the test, or the message goes back to being unguarded."
    )
    end = src.find("exit 2", start)
    assert end != -1, "found the FATAL but not its `exit 2`; the parser is broken"
    return src[start:end]


# ---------------------------------------------------------------------------
# REGRESSION
# ---------------------------------------------------------------------------

def test_the_missing_tool_fatal_names_a_runnable_invocation():
    """RED at a29b97b, GREEN at HEAD.

    At a29b97b the message named only `flake.nix checks.pytests
    nativeBuildInputs` and `the pre-push nix-shell` -- neither of which is
    something a reader can run.
    """
    block = _fatal_block()

    # 🔴 Asserted on a SINGLE LINE carrying BOTH halves, not on the two strings
    # appearing anywhere in the block. The first version of this guard checked
    # `"nix develop" in block` and `"run-tests.sh" in block` separately, and a
    # mutation SURVIVED it: deleting the copy-pasteable invocation line entirely
    # still left the prose sentence "(or `nix develop` once, then `bash
    # scripts/run-tests.sh .` ...)" spelling both words, so the guard passed
    # while the actionable remedy was gone. A word another sentence can spell is
    # not a guard; what makes the message useful is one runnable line.
    invocations = [
        ln for ln in block.splitlines()
        if "nix develop" in ln and "run-tests.sh" in ln
    ]
    assert invocations, (
        "the missing-tool FATAL has no single line that both enters the dev "
        "shell AND runs the gate. A contributor hitting this gets a red gate "
        "and no copy-pasteable remedy, which reads as 'the gate is broken'.\n\n"
        f"FATAL block was:\n{block}"
    )
    # The line must be parameterised by the resolved repo root, not a literal
    # relative path that only works from one cwd.
    assert any("$ROOT" in ln for ln in invocations), (
        "the invocation line hardcodes a path instead of using the runner's "
        f"own resolved $ROOT:\n{invocations}"
    )
    # And it must say this is an environment problem, not a code failure --
    # that misreading is the whole defect.
    assert "not a code failure" in block, (
        "the FATAL does not tell the reader the repo is fine; without that, a "
        "red gate on a missing binary still reads as a broken build"
    )


def test_the_fatal_still_forbids_deleting_the_precondition():
    """INVARIANT guard — the precondition must not be softened into advice.

    Making the message friendlier is exactly the edit that would also make
    "just drop logrotate from REQUIRED_TOOLS" sound reasonable. It must not.
    """
    block = _fatal_block()
    assert "Do NOT drop entries from REQUIRED_TOOLS" in block or \
           "do NOT drop them from" in block, block
    assert "go green while" in block, (
        "the message no longer explains WHY a missing tool is fatal (the run "
        "would go green while testing less)"
    )


# ---------------------------------------------------------------------------
# SEAM — one list, two consumers
# ---------------------------------------------------------------------------

def test_a_default_devshell_exists():
    """The thing the FATAL tells you to enter must actually be defined."""
    src = FLAKE.read_text()
    assert re.search(r"devShells\.\$\{system\}\.default\s*=", src), (
        "flake.nix defines no devShells.<system>.default, but run-tests.sh's "
        "FATAL tells the reader to run `nix develop`. The remedy would 404."
    )


def test_the_devshell_and_the_gate_share_one_tool_list():
    """SEAM guard: both consumers must reference `gateTools`, not a copy.

    Fails if either side inlines its own list again -- the drift that would
    silently make `nix develop` stop satisfying the precondition it is
    advertised as satisfying.
    """
    src = FLAKE.read_text()

    assert re.search(r"nativeBuildInputs\s*=\s*gateTools\s*;", src), (
        "checks.pytests no longer uses `gateTools` for nativeBuildInputs. If "
        "the gate gets its own inlined list again, the devShell can drift out "
        "of satisfying REQUIRED_TOOLS with nothing to notice."
    )
    assert re.search(r"packages\s*=\s*gateTools\s*;", src), (
        "devShells.default no longer uses `gateTools`. Same drift, other side."
    )


def test_gate_tools_is_defined_exactly_once():
    """SEAM guard: a SECOND definition of gateTools would defeat the sharing."""
    src = FLAKE.read_text()
    definitions = re.findall(r"^\s*gateTools\s*=", src, re.M)
    assert len(definitions) == 1, (
        f"expected exactly one `gateTools =` binding, found {len(definitions)}. "
        "Two bindings means the gate and the shell can be given different lists "
        "while every other test here still passes."
    )


def test_every_required_tool_is_justified_in_the_comment_block():
    """INVARIANT guard, and a positive control on the REQUIRED_TOOLS parser.

    The FATAL now claims 'each one is justified in the comment block directly
    above it'. That is a claim about the file, so it is checked.
    """
    src = RUN_TESTS.read_text()
    m = re.search(r"^REQUIRED_TOOLS=\((.*?)\)\s*$", src, re.M)
    assert m, "could not parse REQUIRED_TOOLS from run-tests.sh"
    tools = m.group(1).split()
    assert len(tools) >= 10, f"parsed only {tools} -- the parser is broken, not the list"

    # The justification block is the comment run immediately preceding it.
    block = src[:m.start()]
    block = block[block.rfind("\n\n"):]
    unjustified = [t for t in tools if t not in block]
    assert not unjustified, (
        f"REQUIRED_TOOLS entries with no justification in the comment block "
        f"above them: {unjustified}. The FATAL promises the reader one."
    )
