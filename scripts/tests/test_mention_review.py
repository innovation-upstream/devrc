"""Guards for `mention-review` — the Go PR-review TUI — and for the THIRD GATE
TIER it brought with it.

⚠ IT CAN WRITE NOW. Phase 2 added comment / approve / request changes / submit
review / merge. The behavioural guards for those live in the Go suite, where
`Step` is pure and a test can assert "this keypress produced zero intents". What
is here is the same thing that was always here: the SEAMS between files the Go
compiler never reads together.

🔴 WHY ANY OF THIS IS IN PYTHON AT ALL. The Go suite tests the program. These
test the SEAMS the Go suite structurally cannot see: whether the tier is wired
into the gate, whether the packaging can state truthfully what it built, and
whether the click path was left alone. Each of those lives in a file the Go
compiler never reads.

🔴 AND THE SEAMS ARE THE DEFECT CLASS. A Go package and a Nix derivation can
each be hermetically correct and broken TOGETHER — a tier that exists and is run
by nobody, a version pattern that matches nothing, a `doCheck` that turns a red
test into a skipped host. Every guard below pins a RELATIONSHIP between two
files, not a property of one.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "nix" / "pkgs" / "tools" / "mention-review"
PKG_NIX = PKG / "default.nix"
VERSION_GO = PKG / "src" / "cmd" / "mention-review" / "version.go"
GO_RUNNER = ROOT / "scripts" / "run-go-tests.sh"
GATE = ROOT / "scripts" / "gate.sh"
FLAKE = ROOT / "flake.nix"
MENTION_OPEN = ROOT / "scripts" / "mention-open.py"


# --------------------------------------------------------------------------- #
# The version contract (§9.1)
# --------------------------------------------------------------------------- #

def test_the_nix_version_pattern_matches_exactly_one_line_of_the_go_source():
    """🔴 THE PATTERN AND THE SOURCE ARE TWO FILES THAT MUST AGREE, AND NOTHING
    ELSE MAKES THEM.

    `default.nix` reads the version out of `version.go` with a regex. Zero
    matches or two matches both yield `null`, which switches the package OFF —
    deliberately, because a derivation that cannot state truthfully what it is
    building must not be installed. That is the right runtime behaviour and a
    terrible thing to discover on a host: the failure is `mention-review:
    command not found`, hours later, on the machine that took the switch.

    This turns it into a test failure instead. It reads the pattern FROM the Nix
    file rather than restating it — a second copy of a regex is how a regex and
    its subject drift apart.
    """
    nix_src = PKG_NIX.read_text(encoding="utf-8")
    m = re.search(r'versionPattern\s*=\s*"(.*?)";', nix_src)
    assert m, "default.nix no longer declares a versionPattern"

    # Nix string escaping: `\"` in the Nix literal is a plain quote in the regex.
    pattern = m.group(1).replace('\\"', '"')
    # `builtins.match` is ANCHORED at both ends; Python's `match` anchors only
    # the start, so the trailing `.*` in the pattern plus `fullmatch` reproduces
    # Nix's semantics rather than approximating them.
    rx = re.compile(pattern)

    lines = VERSION_GO.read_text(encoding="utf-8").split("\n")
    hits = [ln for ln in lines if rx.fullmatch(ln)]
    assert len(hits) == 1, (
        f"the Nix version pattern matches {len(hits)} line(s) of version.go, "
        f"and it must match EXACTLY ONE. Zero or two both make `available = "
        f"false`, so the package is silently not installed.\n"
        f"pattern: {pattern!r}\nmatches: {hits}"
    )
    captured = rx.fullmatch(hits[0]).group(1)
    assert captured, "the pattern matched but captured an empty version"
    assert re.fullmatch(r"\d+\.\d+\.\d+", captured), (
        f"version {captured!r} is not x.y.z — the store path label and the "
        f"`--version` output both carry this string"
    )


def test_the_version_is_not_spelled_in_the_nix_file():
    """🔴 THE FAILURE THIS WHOLE MECHANISM EXISTS FOR IS A LITERAL.

    `clawgatectl.nix` is in its current shape because a hand-maintained
    `version = "x.y.z"` stamped 0.7.95 onto a binary built from 0.7.87 source,
    producing a CLI that printed help and exited 0 for a subcommand it did not
    have. A literal creeping back into this file recreates that exactly, and it
    would look perfectly ordinary in review.
    """
    nix_src = PKG_NIX.read_text(encoding="utf-8")
    body = re.sub(r"#[^\n]*", "", nix_src)  # comments discuss literals on purpose
    bad = re.findall(r'version\s*=\s*"\d', body)
    assert not bad, (
        f"default.nix spells a version LITERAL ({bad}); it must be derived from "
        f"the Go source by `parsedVersion`"
    )
    assert "parsedVersion" in body, "the derived version binding is gone"


def test_the_deploy_derivation_disables_doCheck():
    """🔴 `doCheck = false` IS A SAFETY PROPERTY, NOT A SPEED ONE.

    Verified in the pinned nixpkgs rather than assumed:
    `pkgs/build-support/go/module.nix` defaults `doCheck` to TRUE and its check
    phase runs `buildGoDir test` over every test directory. On a package in
    `home.packages`, a red Go test therefore FAILS a `home-manager switch` —
    which `ship.sh` reports as a SKIPPED host, the failure mode this repo's
    CLAUDE.md documents as silently stopping all future delivery to that
    machine. A failing test must cost a red gate leg, never a dead host.
    """
    body = re.sub(r"#[^\n]*", "", PKG_NIX.read_text(encoding="utf-8"))
    assert re.search(r"doCheck\s*=\s*false\s*;", body), (
        "the deploy derivation does not set `doCheck = false` — a red Go test "
        "can now fail a home-manager switch"
    )


# --------------------------------------------------------------------------- #
# The third gate tier
# --------------------------------------------------------------------------- #

def test_the_go_tier_is_wired_into_every_place_that_enumerates_tiers():
    """🔴 A TIER THAT EXISTS AND IS RUN BY NOBODY IS WORSE THAN NO TIER — it
    reads as coverage while providing none.

    Go was a THIRD tier added to a gate built for two, and that touches several
    files that each independently enumerate the legs. This pins the set, so a
    future tier cannot be half-added the way this one could have been.
    """
    assert GO_RUNNER.exists(), "scripts/run-go-tests.sh is missing"

    gate = GATE.read_text(encoding="utf-8")
    assert "GO_RUNNER" in gate, "gate.sh does not know about a go runner"
    assert re.search(r'"\$TIER"\s*=\s*"go"', gate), (
        "gate.sh has no `--tier go` branch"
    )
    assert "pytest|node|go|all" in gate, (
        "gate.sh's --tier validation does not accept `go`"
    )

    flake = FLAKE.read_text(encoding="utf-8")
    assert re.search(r"^\s*gotests\s*=", flake, re.M), (
        "flake.nix has no `checks.gotests` output — CI enumerates legs by "
        "building `.#checks.x86_64-linux.${LEG}`, so a missing output is a "
        "tier CI can never run"
    )
    assert "run-go-tests.sh" in flake, (
        "checks.gotests does not invoke the runner"
    )


def test_the_go_runner_env_overrides_are_refused_by_the_gate():
    """🔴 THE ASYMMETRIC-REFUSAL DEFECT, NOT REPEATED ONE TIER OVER.

    `gate.sh` refuses to run when a variable that changes WHAT RUNS is set in
    its environment, because `GATE: RESULT=PASS` would still read as a full
    verdict. That list was once `DEVRC_TARGETS` alone while three other
    variables had the same power — "an over-broad claim beside an asymmetric
    refusal, which reads as coverage and provides none", in gate.sh's own words.

    The go tier adds three more levers. Each must be in the list.
    """
    gate = GATE.read_text(encoding="utf-8")
    block = re.search(r"_gate_ambient=\(\)(.*?)done", gate, re.S)
    assert block, "gate.sh's ambient-variable refusal loop has moved"
    listed = block.group(1)
    for var in ("DEVRC_GATE_GO_RUNNER", "MIN_GO_TESTS", "MAX_GO_SKIPS"):
        assert var in listed, (
            f"{var} changes what the go tier runs or accepts, and gate.sh does "
            f"not refuse an ambient value for it"
        )


def test_the_runner_pins_its_packages_two_way():
    """The runner's own GUARD 2, driven as a subprocess.

    🔴 IT IS RUN, NOT READ. A test that grepped for the word `PACKAGES` would
    pass over a loop that had stopped comparing anything. `--check-packages`
    exists precisely so this can be exercised cheaply: no `go`, no tests.
    """
    proc = subprocess.run(
        ["bash", str(GO_RUNNER), "--check-packages", str(ROOT)],
        capture_output=True, text=True, timeout=120,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"the package pin is broken:\n{out}"

    m = re.search(r"discovered=(\d+) pinned=(\d+)", out)
    assert m, f"the runner no longer reports its counts:\n{out}"
    discovered, pinned = int(m.group(1)), int(m.group(2))
    # 🔴 POSITIVE CONTROL. `discovered == pinned` is satisfied by 0 == 0, which
    # is exactly what a broken glob produces — and it would read as a clean
    # two-way pin.
    assert discovered > 0, "discovery found ZERO packages — a broken glob, not an empty suite"
    assert discovered == pinned, f"discovered={discovered} pinned={pinned}"

    # 🔴 AND IT REPORTS `SCOPE: NONE`, NEVER FULL. A zero-test invocation that
    # printed `SCOPE: FULL` + `RESULT: PASS` is the full-gate-shaped pair off a
    # run that tested nothing — the shape `gate.sh` exits 91 for.
    assert "SCOPE: NONE" in out, (
        f"--check-packages ran zero tests and did not say SCOPE: NONE:\n{out}"
    )


def test_a_pinned_package_that_vanishes_is_caught(tmp_path):
    """🔴 MUTATION CONTROL ON GUARD 2, run rather than asserted.

    The two-way pin is only worth having if it can go RED. This copies the
    runner, adds a package nobody has, and watches it fail with THIS guard's own
    message — not with some other arm's.
    """
    mutated = tmp_path / "run-go-tests.sh"
    src = GO_RUNNER.read_text(encoding="utf-8")
    assert '"internal/udiff|11"' in src, "the PACKAGES table has been reshaped"
    mutated.write_text(
        src.replace('"internal/udiff|11"',
                    '"internal/udiff|11"\n  "internal/ghost|1"'),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(mutated), "--check-packages", str(ROOT)],
        capture_output=True, text=True, timeout=120,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, f"a pinned package with no tests passed:\n{out}"
    assert "pinned package 'internal/ghost' has NO test files" in out, (
        f"the failure did not come from THIS guard — a kill by a different "
        f"arm is green for the wrong reason and stays green with this arm "
        f"deleted.\n{out}"
    )


def test_an_undiscovered_package_with_tests_is_caught(tmp_path):
    """The OTHER direction of the same pin: a package with tests that nobody
    gave a floor. Without this arm a new package is swept in under the global
    total and its own collapse becomes invisible.
    """
    mutated = tmp_path / "run-go-tests.sh"
    src = GO_RUNNER.read_text(encoding="utf-8")
    assert '  "internal/udiff|11"\n' in src, "the PACKAGES table has been reshaped"
    mutated.write_text(src.replace('  "internal/udiff|11"\n', ""), encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(mutated), "--check-packages", str(ROOT)],
        capture_output=True, text=True, timeout=120,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, f"an unpinned package with tests passed:\n{out}"
    assert "has tests but is NOT in PACKAGES" in out, (
        f"the failure did not come from THIS guard:\n{out}"
    )


def test_the_runner_refuses_rather_than_passing_when_go_is_missing(tmp_path):
    """🔴 A MISSING TOOLCHAIN IS NEVER A PASS.

    `go test` absent means this tier measured nothing. Reporting that as success
    is how a gate goes green over an untested language — the exact shape
    `run-tests.sh`'s REQUIRED_TOOLS precondition exists for, and the reason it
    exits 3 rather than skipping.
    """
    # A PATH carrying everything the runner needs EXCEPT `go`.
    #
    # ⚠ THERE IS NO `/bin/bash` ON THIS HOST — it is NixOS, and the only
    # guaranteed absolute path is `/bin/sh`. An earlier version of this test
    # passed `executable="/bin/bash"` and died `FileNotFoundError: /bin/bash`,
    # which reads like a broken runner rather than a broken test.
    stub = tmp_path / "bin"
    stub.mkdir()
    tools = ("bash", "git", "grep", "mktemp", "dirname", "cat", "sed", "head",
             "rm", "env", "uname", "tr", "sort", "wc")
    resolved = 0
    for tool in tools:
        which = subprocess.run(["bash", "-c", f"command -v {tool}"],
                               capture_output=True, text=True)
        target = which.stdout.strip()
        if which.returncode == 0 and target:
            (stub / tool).symlink_to(target)
            resolved += 1
    # 🔴 POSITIVE CONTROL ON THE STUB ITSELF, BOTH HALVES. `go` must be absent
    # (or this proves nothing about a missing toolchain) AND the stub must
    # actually carry tools (or the runner dies for want of `bash` and the exit
    # code says nothing about `go`).
    assert not (stub / "go").exists()
    assert resolved >= 5, f"the stub PATH resolved only {resolved} tools"

    proc = subprocess.run(
        ["bash", str(GO_RUNNER), str(ROOT)],
        capture_output=True, text=True, timeout=180,
        env={"PATH": str(stub), "HOME": str(tmp_path)},
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 3, (
        f"a missing `go` produced exit {proc.returncode}, want 3:\n{out}"
    )
    assert "`go` is not on PATH" in out, out
    # And it must NOT claim it ran everything.
    assert "SCOPE: FULL" not in out, (
        f"a run that never started reported FULL scope:\n{out}"
    )
    assert "RESULT: FAIL" in out, out


# --------------------------------------------------------------------------- #
# Phase 2 can approve and merge — so NO TEST MAY REACH THE LIVE API
# --------------------------------------------------------------------------- #

def _go_packages() -> dict[str, dict]:
    """Every Go package dir under src/, with its imports split test/non-test.

    🔴 DERIVED FROM THE TREE, NOT LISTED. A hardcoded package list is the shape
    that rots: a new package doing HTTP would simply not be in it, and the guard
    below would pass without ever having looked at it.
    """
    out: dict[str, dict] = {}
    src = PKG / "src"
    for path in sorted(src.rglob("*.go")):
        rel = path.parent.relative_to(src).as_posix()
        entry = out.setdefault(rel, {"prod": set(), "tests": [], "dir": path.parent})
        text = path.read_text(encoding="utf-8")
        imports = set(re.findall(r'"([^"]+)"', text))
        if path.name.endswith("_test.go"):
            entry["tests"].append(path)
        else:
            entry["prod"] |= imports
    return out


def test_every_go_package_that_can_reach_github_carries_the_loopback_guard():
    """🔴 THE WRITE PHASE'S MOST IMPORTANT SEAM: a test that reaches the real
    API would approve or merge a real pull request with the operator's token.

    "Every test uses a fake" is a claim about the tests that exist today. The
    mechanism is a `TestMain` that replaces `http.DefaultTransport` with one
    refusing any non-loopback host — and this pins WHICH packages must carry it,
    derived from what they import rather than from a list somebody maintains.

    ⚠ `cmd/*` IS EXEMPT, WITH A REASON AND A SUBSTITUTE. Its tests run the
    BUILT BINARY as a subprocess, so an in-process transport cannot cover them;
    asserting the guard there would be a guard claiming coverage it does not
    have. The compensating property is asserted separately below: those tests
    run the binary with an empty credential environment, so there is no token
    for it to authenticate with even if a case did reach the network.
    """
    packages = _go_packages()
    assert packages, "no Go packages were discovered — a broken glob, not an empty tree"

    required, exempt = [], []
    for name, entry in sorted(packages.items()):
        reaches = (
            "net/http" in entry["prod"]
            or any(i.endswith("/internal/ghapi") for i in entry["prod"])
        )
        if not reaches:
            continue
        (exempt if name.startswith("cmd/") else required).append(name)

    # 🔴 POSITIVE CONTROL: the derivation must have SELECTED something. An
    # import scan that matched nothing would report no violations and read as a
    # clean bill of health — the silent zero this repo keeps getting bitten by.
    assert len(required) >= 2, (
        f"the scan selected {required} as network-reaching; the ui and ghapi "
        f"packages must both be in it, or the import matching is broken"
    )

    for name in required:
        entry = packages[name]
        bodies = "\n".join(p.read_text(encoding="utf-8") for p in entry["tests"])
        assert "http.DefaultTransport = " in bodies, (
            f"package {name} can reach GitHub and no test file installs the "
            f"loopback-only transport. This phase can approve and merge pull "
            f"requests; add a TestMain like internal/ghapi/nonet_test.go's."
        )
        assert "BLOCKED" in bodies, (
            f"package {name} installs a transport but nothing refuses a host — "
            f"a transport that blocks nothing reads as a guarantee and is none"
        )
        # 🔴 AND THE GUARD ITSELF MUST BE CONTROLLED. A blocking transport with
        # no test proving it blocks api.github.com is the instrument nobody
        # validated.
        assert "api.github.com" in bodies, (
            f"package {name}'s loopback guard has no NEGATIVE CONTROL — nothing "
            f"feeds it a real GitHub URL and watches it refuse"
        )

    # The exempt half is asserted too, so the exemption cannot silently widen.
    for name in exempt:
        entry = packages[name]
        bodies = "\n".join(p.read_text(encoding="utf-8") for p in entry["tests"])
        assert "GH_CONFIG_DIR=/nonexistent" in bodies, (
            f"package {name} is exempt from the in-process loopback guard "
            f"because it runs the binary as a subprocess — and it no longer "
            f"clears the credential environment, which was the substitute"
        )
    assert exempt, "nothing is exempt; the cmd package should be"


# --------------------------------------------------------------------------- #
# The click path IS flipped — and the two halves must agree
# --------------------------------------------------------------------------- #
#
# ⚠ TWO PHASE GUARDS WERE DELETED HERE, NOT EDITED, and their own docstrings
# asked for exactly that: `test_phase_1_does_NOT_flip_the_click_path_to_the_new_
# tui` and `test_the_alacritty_wrapper_does_not_yet_pin_the_new_tui`. They
# pinned "the retirement has not happened". It has now happened for the CLICK
# PATH, so a guard named for Phase 1 asserting Phase 4's state would be a test
# whose name lies. Editing them to accept `mention-review` is the move they
# explicitly warned against.
#
# 🔴 WHAT REPLACES THEM IS NOT NOTHING. The seam is still covered, and by a
# STRICTLY STRONGER guard that already existed:
# `test_mention_open.py::test_the_alacritty_wrapper_PATH_covers_every_executable
# _the_handler_spawns` reads `REVIEW_EXE` out of the SYNTAX TREE and pins it
# against the wrapper's `makeBinPath` TWO-WAY — a spawn with no package is
# "inert in production with a green suite", a package with no spawn is "dead
# weight in the closure". That test does not care WHICH binary the constant
# names, so it keeps working across this flip and across any future one, which
# is precisely why the phase-pinned pair was redundant once the phase ended.


def test_the_click_path_and_the_wrapper_name_the_SAME_tui():
    """🔴 A RELATIONSHIP, NOT A VALUE — it must not re-pin a literal.

    The pair this replaces asserted `nvim-octo` by name and would have to be
    rewritten on every flip; this asserts only that the two files AGREE, so it
    survives a future retirement or a rollback without edit.

    ⚠ IT IS DELIBERATELY NOT A SECOND COPY OF THE TWO-WAY LEDGER in
    `test_mention_open.py` — that one resolves the constant from the AST and is
    the authority. This is the cheap cross-file read, and it exists so a flip
    that updates ONE of the two files fails HERE with a message naming both,
    rather than only inside a ledger whose failure text is about closures.
    """
    src = MENTION_OPEN.read_text(encoding="utf-8")
    m = re.search(r'REVIEW_EXE\s*=\s*"([^"]+)"', src)
    assert m, "mention-open.py no longer declares REVIEW_EXE"
    exe = m.group(1)

    nix_src = (ROOT / "nix" / "programs" / "alacritty" / "default.nix").read_text(encoding="utf-8")
    m2 = re.search(r"makeBinPath\s*\[(.*?)\]", nix_src, re.S)
    assert m2, "the mentionOpen wrapper no longer calls lib.makeBinPath"
    # Comments in that block are PROSE ABOUT the packages — this file's own
    # history has a `pkgs.gh` named in one for months after it was removed.
    body = re.sub(r"#[^\n]*", "", m2.group(1))
    listed = set(re.findall(r"pkgs\.([A-Za-z0-9_-]+)", body))
    assert listed, "positive control: the wrapper DOES pin a PATH"

    assert exe in listed, (
        f"REVIEW_EXE spawns {exe!r} but the Alacritty wrapper's PATH pins "
        f"{sorted(listed)}. The click opens a terminal that flashes and "
        f"vanishes — alacritty exits 0 whether its `-e` command exits 0 or 127."
    )


# --------------------------------------------------------------------------- #
# The repo is PUBLIC
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("path", sorted(
    p for p in (PKG / "src").rglob("*.go")
))
def test_no_go_source_file_carries_a_real_repository_or_host(path: Path):
    """🔴 THIS REPO IS PUBLIC, AND FIXTURES ARE NOT EXEMPT.

    Captured text — anyone's diffs, filenames, message bodies — must not land
    here in any form. The Go fixtures use invented owners and repositories
    (`gardenersguild/trowelcast`, `rivalorg/spadeworks`); this checks the one
    real slug that legitimately appears in prose has not leaked into a FIXTURE,
    and that no private-looking host has.

    ⚠ NARROW BY CONSTRUCTION. It cannot see a repository name it has never
    heard of, so it is a tripwire rather than a proof — the content gates in
    `test_no_captured_text.py` and `test_no_client_hostnames.py` own the real
    ledgers.
    """
    text = path.read_text(encoding="utf-8")
    code = "\n".join(
        ln for ln in text.split("\n")
        if not ln.lstrip().startswith("//")
    )
    # api.github.com is the one host this program legitimately talks to.
    hosts = re.findall(r"https?://([A-Za-z0-9.-]+)", code)
    allowed = {"api.github.com", "github.com"}
    bad = sorted(set(hosts) - allowed)
    assert not bad, f"{path.name} carries non-GitHub host(s): {bad}"
