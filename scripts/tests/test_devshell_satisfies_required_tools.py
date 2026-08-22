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

🔴 SCOPE OF THE CLAIM — two tiers, and they are NOT the same strength.

  * The FATAL guard is BEHAVIOURAL. It runs the real runner with a PATH holding
    only `bash` and reads what it prints. Two text-matching versions of it were
    walked by auditors before it got here; see the fixture.

  * The flake guards are SOURCE-level, comment-stripped. They can see that
    `devShells.default` is written and live; they CANNOT see that it evaluates.
    The behavioural check would be `nix eval ...#devShells.x86_64-linux.default`,
    which needs the flake's inputs — unavailable in the hermetic tier, which
    evaluates a `cp -r` of the tree with no inputs and no network. The section
    header restates this where the guards are.

That `gateTools` actually satisfies REQUIRED_TOOLS is proven elsewhere and
better: `checks.pytests` runs this very runner with
`nativeBuildInputs = gateTools` in the nix sandbox, so an insufficient list
exits 2 on the precondition. The only way that proof stops transferring to the
devShell is the two consumers drifting apart — which is what the seam guards
forbid.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_TESTS = REPO_ROOT / "scripts" / "run-tests.sh"
FLAKE = REPO_ROOT / "flake.nix"


@pytest.fixture(scope="module")
def emitted_fatal(tmp_path_factory):
    """RUN the runner with an empty PATH and return what it actually printed.

    🔴 This fixture is the whole point of this file's second review round. The
    guard here used to READ run-tests.sh as text and match strings in it. That
    is walkable by a single `#`: an auditor commented out the one copy-pasteable
    remedy line and the guard reported 6 passed, while the FATAL a human sees
    had a blank gap where the remedy used to be. Source text is not behaviour.

    Driving the real path is cheap and fully deterministic: the tool
    precondition is GUARD 1, before any test runs, so with a PATH containing
    only `bash` the runner exits 2 in milliseconds. `env -i`-style isolation
    (an explicit env dict) is what makes it independent of the ambient PATH --
    inside the nix sandbox every REQUIRED_TOOLS binary IS present, so a test
    that relied on one being absent would measure the environment, not the code.
    """
    bash = shutil.which("bash")
    assert bash, "bash is not on PATH; this suite cannot drive the runner"

    stub = tmp_path_factory.mktemp("only-bash")
    (stub / "bash").symlink_to(bash)
    home = tmp_path_factory.mktemp("home")

    # 🔴 This REPLACES PATH rather than prepending, so it is a registered entry
    # in test_no_real_launchers.py's PINNED_PATH_CLOBBERS ledger -- the guard
    # that stops a new clobber from silently dropping the launcher-stub dir.
    # Replacing is correct here (a precondition about ABSENT binaries cannot be
    # tested by prepending), and that ledger entry justifies it by ENUMERATING
    # this directory's contents. The assertion below is what makes the
    # enumeration a live invariant instead of a claim in a comment: if anything
    # ever lands in this dir besides the bash symlink, this fails here rather
    # than quietly widening what the subprocess can reach.
    assert sorted(p.name for p in stub.iterdir()) == ["bash"], (
        "the stub PATH dir must hold exactly one entry (a bash symlink); it "
        f"holds {sorted(p.name for p in stub.iterdir())}. PINNED_PATH_CLOBBERS "
        "justifies this clobber by enumerating those contents."
    )

    proc = subprocess.run(
        [bash, str(RUN_TESTS), str(REPO_ROOT)],
        env={"PATH": str(stub), "HOME": str(home)},
        capture_output=True,
        text=True,
        timeout=120,
    )
    combined = proc.stdout + proc.stderr

    # POSITIVE CONTROL. Without this, every assertion below could be matching
    # against output from some OTHER failure -- or against nothing at all -- and
    # a reassuring pass would mean only "the string I looked for was absent".
    assert "required tool(s) missing from PATH" in combined, (
        "driving run-tests.sh with an empty PATH did NOT produce the tool "
        f"precondition FATAL, so this fixture is measuring the wrong thing.\n"
        f"exit={proc.returncode}\n{combined[-3000:]}"
    )
    # 🔴 3, not 2, since 2026-08-22. GUARD 1 is an ENVIRONMENT precondition, and
    # run-tests.sh now distinguishes those (exit 3) from REPO-CONTENT guards
    # (exit 2) so `githooks/tests-on-push.sh` can degrade on the former without
    # degrading away the latter -- the target list, floor table, launcher stubs
    # and spool wiring, whose own messages warn that silencing them is "how a
    # suite stops running while the gate goes green".
    assert proc.returncode == 3, (
        f"expected the ENVIRONMENT precondition to exit 3, got {proc.returncode}\n"
        f"{combined[-3000:]}"
    )
    return combined


def _fatal_block() -> str:
    """The `required tool(s) missing` FATAL block AS SOURCE TEXT.

    ⚠ Retained only for the assertions that are genuinely about the SOURCE (that
    the precondition is still spelled as a hard failure). Anything about what
    the operator SEES must use the `emitted_fatal` fixture instead -- this
    function cannot tell a live line from a commented-out one.
    """
    src = RUN_TESTS.read_text()
    start = src.find("required tool(s) missing from PATH")
    assert start != -1, (
        "could not find the 'required tool(s) missing from PATH' FATAL in "
        "run-tests.sh. If it was reworded, update this parser -- do NOT delete "
        "the test, or the message goes back to being unguarded."
    )
    # 🔴 TERMINATE ON ANY `exit <n>`, not a hardcoded code. GUARD 1 exited 2 until
    # 2026-08-22, when environment preconditions moved to 3 so the pre-push hook
    # could degrade on them. This parser still looked for `exit 2`, so it walked
    # PAST GUARD 1 and stopped at GUARD 5's — widening the window from 17 lines to
    # 1235. The `assert end != -1` tripwire below could not fire, because it DID
    # find an `exit 2`, just the wrong one. Measured: with the window widened, both
    # sentences this test exists to protect could be deleted from the real FATAL
    # and re-planted as dead prose 20 lines further down, and the test PASSED.
    #
    # A guard keyed on a value that another file is free to change is a guard with
    # a remote off-switch. Match the SHAPE instead.
    m = re.search(r"^\s*exit \d+\s*$", src[start:], re.M)
    assert m, "found the FATAL but not its `exit <n>`; the parser is broken"
    end = start + m.start()
    block = src[start:end]
    # A window that spans more than this guard is the failure above, returning.
    assert block.count("\n") < 60, (
        f"the FATAL window is {block.count(chr(10))} lines — it has run past its "
        f"own `exit` into a later guard, so every assertion on it is meaningless."
    )
    return block


# ---------------------------------------------------------------------------
# REGRESSION
# ---------------------------------------------------------------------------

def test_the_missing_tool_fatal_names_a_runnable_invocation(emitted_fatal):
    """RED at a29b97b, GREEN at HEAD.

    At a29b97b the message named only `flake.nix checks.pytests
    nativeBuildInputs` and `the pre-push nix-shell` -- neither of which is
    something a reader can run.

    🔴 Asserted against what the runner PRINTS, not against its source. Two
    walks have been measured against earlier versions of this guard, and only
    the second one is closed by wording:
      round 1 -- checked `"nix develop" in src` and `"run-tests.sh" in src`
                 separately. SURVIVED deleting the whole invocation line,
                 because the block's own prose sentence spells both words.
      round 2 -- required one LINE carrying both. SURVIVED commenting that line
                 out with a single `#`, because the text was still in the file.
    Reading stderr closes both: a commented-out echo prints nothing.
    """
    # The remedy must survive as ONE line the reader can copy -- not two words
    # scattered across the prose, which is what round 1 walked.
    invocations = [
        ln for ln in emitted_fatal.splitlines()
        if "nix develop" in ln and "run-tests.sh" in ln
    ]
    assert invocations, (
        "the missing-tool FATAL printed no single line that both enters the "
        "dev shell AND runs the gate. A contributor hitting this gets a red "
        "gate and no copy-pasteable remedy, which reads as 'the gate is "
        f"broken'.\n\nWhat it actually printed:\n{emitted_fatal}"
    )
    # $ROOT must have been EXPANDED, not printed literally: the line is only
    # copy-pasteable if it names a real path. This also pins that the remedy is
    # parameterised by the runner's own resolved root rather than a fixed
    # relative path that works from exactly one cwd.
    assert any(str(REPO_ROOT) in ln for ln in invocations), (
        "the printed invocation does not contain the resolved repo root, so it "
        f"is not copy-pasteable:\n{invocations}"
    )
    assert not any("$ROOT" in ln for ln in invocations), (
        f"`$ROOT` was printed literally instead of expanded:\n{invocations}"
    )
    # And it must say this is an environment problem, not a code failure --
    # that misreading is the whole defect.
    assert "not a code failure" in emitted_fatal, (
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
#
# 🔴 SHAPE THIS SECTION CANNOT CATCH, stated rather than left to be discovered.
# These read flake.nix as SOURCE, because the behavioural check (`nix eval
# ...#devShells.x86_64-linux.default`) needs the flake's INPUTS, and the
# hermetic tier evaluates a `cp -r` of the tree with no inputs and no network.
# So they can see that the attribute is WRITTEN and not commented out; they
# cannot see that it EVALUATES. A change that leaves the text intact but breaks
# evaluation (a typo in `gateTools`, a bad `pkgs.` attr, a shadowed binding) is
# invisible here and is caught only by `nix flake check` / a real `nix develop`.
#
# What they DO now catch, and did not before: commenting the block out. An
# auditor wrapped the whole devShells block in `/* ... */`, leaving flake.nix
# valid, and every test in this file passed while `nix develop` answered
#     error: flake '...' does not provide attribute 'devShells.x86_64-linux.default'
# `_uncommented_flake()` strips nix comments before matching, so that mutant now
# dies. Comment-stripping is a TEXT defence against a TEXT walk -- it is not a
# substitute for evaluation, which is why the gap above is spelled out.
# ---------------------------------------------------------------------------

def _uncommented_flake() -> str:
    """flake.nix with nix comments removed, so a commented-out block cannot match.

    Handles both nix comment forms: `/* ... */` blocks and `#` line comments.
    Deliberately simple rather than a real nix lexer -- a `#` inside a string
    literal (a URL fragment, say) truncates that line, which is harmless for the
    structural anchors matched here and is the conservative direction: it can
    only ever HIDE text from a match, never invent it. The positive control
    below proves it does not hide the anchors we depend on.
    """
    src = FLAKE.read_text()
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"#[^\n]*", "", src)
    return src


def test_the_comment_stripper_keeps_live_code_positive_control():
    """A stripper that ate everything would make every test below vacuous.

    Pins both directions: live anchors SURVIVE stripping, and text that is only
    present inside a comment DOES NOT.
    """
    stripped = _uncommented_flake()
    assert "devShells" in stripped, "the stripper removed live code"
    assert "gateTools" in stripped, "the stripper removed live code"
    # A sentence that exists only inside a comment in flake.nix must be gone.
    raw = FLAKE.read_text()
    assert "THE GATE'S TOOLCHAIN" in raw, (
        "the comment this control keys on was reworded; pick another "
        "comment-only string rather than deleting the control"
    )
    assert "THE GATE'S TOOLCHAIN" not in stripped, (
        "the stripper did NOT remove comment text, so every guard below is "
        "still walkable by commenting the code out"
    )


def test_a_default_devshell_exists():
    """The thing the FATAL tells you to enter must actually be defined.

    Matched against comment-stripped source: a `/* ... */` around this block
    used to pass here while `nix develop` 404'd.
    """
    assert re.search(r"devShells\.\$\{system\}\.default\s*=", _uncommented_flake()), (
        "flake.nix defines no devShells.<system>.default, but run-tests.sh's "
        "FATAL tells the reader to run `nix develop`. The remedy would 404."
    )


def test_the_devshell_and_the_gate_share_one_tool_list():
    """SEAM guard: both consumers must reference `gateTools`, not a copy.

    Fails if either side inlines its own list again -- the drift that would
    silently make `nix develop` stop satisfying the precondition it is
    advertised as satisfying. Comment-stripped for the same reason as above:
    `packages = gateTools;` stayed spelled inside the auditor's block comment.
    """
    src = _uncommented_flake()

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
    definitions = re.findall(r"^\s*gateTools\s*=", _uncommented_flake(), re.M)
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

    # 🔴 The justification block is the CONTIGUOUS RUN OF `#` LINES immediately
    # above the array -- walked backwards line by line, not `rfind("\n\n")`.
    # Measured: `rfind` reached 51 lines back, swallowing the `unset CDPATH`
    # prose, so unrelated English was being accepted as justification.
    block_lines = []
    for line in reversed(src[:m.start()].splitlines()):
        if line.lstrip().startswith("#"):
            block_lines.append(line)
        elif line.strip() == "":
            continue  # blank lines inside the comment run are fine
        else:
            break  # first line of real code ends the block
    block = "\n".join(block_lines)

    # 🔴 WORD matching, not substring containment. `t not in block` reported
    # "justified" for any tool whose name is a substring of unrelated prose --
    # `ed`, `tr`, `cat`, `id` and `who` all passed with zero justification, and
    # `at` passed on the English word "at" even under word boundaries, which is
    # why the block above had to be narrowed as well. Both fixes are load-
    # bearing; neither alone closes it.
    def justified(tool):
        return re.search(rf"(?<![\w-]){re.escape(tool)}(?![\w-])", block) is not None

    unjustified = [t for t in tools if not justified(t)]
    assert not unjustified, (
        f"REQUIRED_TOOLS entries with no justification in the comment block "
        f"above them: {unjustified}. The FATAL promises the reader one."
    )
