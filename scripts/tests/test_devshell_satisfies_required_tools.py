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
exits 3 on the precondition (an ENVIRONMENT abort; repo-content guards exit 2). The only way that proof stops transferring to the
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
    only `bash` the runner exits 3 in milliseconds. `env -i`-style isolation
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


def test_the_fatal_still_forbids_deleting_the_precondition(emitted_fatal):
    """INVARIANT guard — the precondition must not be softened into advice.

    Making the message friendlier is exactly the edit that would also make
    "just drop logrotate from REQUIRED_TOOLS" sound reasonable. It must not.

    🔴 READS THE EMITTED OUTPUT, NOT THE SOURCE. It used to parse a window out of
    run-tests.sh, and that window was terminated by a hardcoded `exit 2`. When
    GUARD 1 moved to `exit 3` the search walked past it into a later guard,
    widening the window from 17 lines to 1235 — its own `assert end != -1`
    tripwire could not fire, because it HAD found an `exit 2`, just the wrong
    one. Both protected sentences could then be deleted from the live echoes and
    re-planted as dead comment prose further down, and this test passed.

    A width cap was the first repair and it was one guard too loose: the
    cumulative windows are 17 → 50 → 108 → 126 → 1235, so a `< 60` bound still
    admitted a ONE-guard overshoot, which is all the walk needs. This file's own
    source-parser docstring already said the rule — "anything about what the
    operator SEES must use the `emitted_fatal` fixture, this function cannot tell
    a live line from a commented-out one". The right repair was to follow it.
    `emitted_fatal` is the real FATAL, printed by a real run: no window, no
    terminator, and a commented-out echo prints nothing.
    """
    # 🔴 SLICE TO THE FATAL. `emitted_fatal` is the whole run's output, which also
    # carries the always-printed `RESULT:` verdict line. Matching the protected
    # sentences against all of it is walkable: soften them in GUARD 1's echoes and
    # re-plant the exact strings in the verdict line, and this test goes green
    # while the operator's FATAL is softened. Measured. Narrower than the 1235-line
    # source window it replaced, but the same shape — so bound it to the block.
    _m = emitted_fatal.find("required tool(s) missing from PATH")
    assert _m != -1, f"no FATAL in the output at all:\n{emitted_fatal}"
    _end = emitted_fatal.find("RESULT:", _m)
    block = emitted_fatal[_m:_end if _end != -1 else None]
    assert block.count("\n") < 30, (
        f"the FATAL block is {block.count(chr(10))} lines — the RESULT: terminator "
        f"was not found where expected, so this assertion is unbounded again."
    )

    assert "Do NOT drop entries from REQUIRED_TOOLS" in block or \
           "do NOT drop them from" in block, block
    assert "go green while" in block, (
        "the message no longer explains WHY a missing tool is fatal (the run "
        f"would go green while testing less).\n\nWhat it printed:\n{block}"
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


# ---------------------------------------------------------------------------
# GUARD 1 CLASSIFIES BY CAUSE (devrc#705)
#
# GUARD 1 reads REQUIRED_TOOLS, which is REPO CONTENT, but exited 3 for every
# cause -- so `githooks/tests-on-push.sh` DEGRADED on a typo there and the push
# went through with zero tests run. Measured before the fix: `logrotatee` in
# REQUIRED_TOOLS -> rc 3 -> push allowed. The test that would have caught the
# typo never ran, because the runner aborts at GUARD 1 before pytest starts.
#
# The discriminator is DEVRC_GATE_ENV=1, exported by BOTH sanctioned gate
# environments. The two tests below pin the two halves, and BOTH are needed:
# the behavioural one proves the runner branches on the marker, and the seam one
# proves something still SETS it. Drop the export from flake.nix and the
# behavioural test still passes while the gate silently reverts to
# always-degrade -- the exact silent direction this issue was filed about.


@pytest.mark.parametrize(
    "marker,expect_rc,must_say,must_not_say",
    [
        # In a sanctioned gate env the toolchain supplies everything gateTools
        # declares, so a STILL-missing entry means the repo asked for something
        # nothing supplies. Repo defect -> 2 -> the hook BLOCKS.
        ("1", 2, "REPO defect", "not a code failure"),
        # Outside one, the caller simply is not in the gate env. Caller defect
        # -> 3 -> the hook degrades, and the FATAL says how to get in.
        (None, 3, "not a code failure", "REPO defect"),
    ],
    ids=["inside-gate-env-BLOCKS", "outside-gate-env-degrades"],
)
def test_guard1_classifies_by_cause_not_by_site(
    tmp_path_factory, marker, expect_rc, must_say, must_not_say
):
    """Same missing tool, two causes, two exit codes -- driven, not parsed."""
    bash = shutil.which("bash")
    assert bash, "bash is not on PATH; this suite cannot drive the runner"
    stub = tmp_path_factory.mktemp("only-bash-cause")
    (stub / "bash").symlink_to(bash)
    home = tmp_path_factory.mktemp("home-cause")

    # An explicit env dict, so the ambient DEVRC_GATE_ENV cannot leak in and
    # decide the arm for us. That matters: this suite normally runs INSIDE the
    # dev shell, where the marker IS set, so inheriting it would collapse both
    # parametrisations onto the same arm and the pair would agree vacuously.
    env = {"PATH": str(stub), "HOME": str(home)}
    if marker is not None:
        env["DEVRC_GATE_ENV"] = marker

    proc = subprocess.run(
        [bash, str(RUN_TESTS), str(REPO_ROOT)],
        env=env, capture_output=True, text=True, timeout=120,
    )
    out = proc.stdout + proc.stderr

    # POSITIVE CONTROL -- without it, an abort from some OTHER guard that
    # happened to share the expected code would read as a pass.
    assert "required tool(s) missing from PATH" in out, (
        "GUARD 1 is not what fired, so this measures the wrong guard.\n"
        f"exit={proc.returncode}\n{out[-3000:]}"
    )
    assert proc.returncode == expect_rc, (
        f"DEVRC_GATE_ENV={marker!r} should classify as exit {expect_rc}, got "
        f"{proc.returncode}. Exit 3 DEGRADES the pre-push hook; exit 2 blocks "
        f"it.\n{out[-3000:]}"
    )
    # The wording must match the classification. A correct code under the wrong
    # diagnosis is its own defect: the environment arm tells the reader "nothing
    # in the repo is broken" and to go enter the dev shell, which is actively
    # wrong advice for someone who is already inside one.
    assert must_say in out, f"expected {must_say!r} in the FATAL:\n{out[-3000:]}"
    assert must_not_say not in out, (
        f"the FATAL carries {must_not_say!r}, which belongs to the OTHER arm — "
        f"the diagnosis contradicts the exit code.\n{out[-3000:]}"
    )


def test_both_sanctioned_gate_envs_export_the_marker():
    """The two tiers that satisfy REQUIRED_TOOLS must both announce themselves.

    🔴 SILENT-FAILURE DIRECTION. If only the devShell exports it, `nix flake
    check` misclassifies its OWN repo defects as environment faults; if only
    checks.pytests does, the pre-push tier -- the only one that runs
    automatically here, since this repo has no CI and no branch protection --
    goes back to degrading on a typo. Neither shows up as a failure anywhere:
    the runner still exits, just with the code that lets the push through.

    Reads flake.nix as SOURCE, with the same limitation the seam tests above
    document -- it can see the export is WRITTEN and not commented out, not that
    nix evaluates it into the environment. `_uncommented_flake` is what makes
    the commented-out case fail rather than pass.
    """
    src = _uncommented_flake()
    hits = src.count("DEVRC_GATE_ENV=1")
    assert hits >= 2, (
        f"DEVRC_GATE_ENV=1 is exported {hits} time(s) in flake.nix; both the "
        "devShell shellHook AND checks.pytests must set it. GUARD 1 uses it to "
        "tell a REPO defect from a CALLER defect, so a tier that does not set "
        "it silently degrades instead of blocking (devrc#705)."
    )
    # ...and specifically in the devShell, not twice in the sandbox check.
    shell_at = src.find("shellHook")
    assert shell_at != -1, "no shellHook in flake.nix"
    tail = src[shell_at:shell_at + 800]
    assert "DEVRC_GATE_ENV=1" in tail, (
        "the devShell's shellHook does not export DEVRC_GATE_ENV=1, so a "
        "contributor running the gate from `nix develop` gets the "
        "ENVIRONMENT diagnosis for a REPO defect and the hook degrades."
    )
