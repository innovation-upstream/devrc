"""The clawgatectl derivation must not be able to LABEL a binary with a version
its source does not carry.

WHAT THIS IS FOR
----------------
`nix/pkgs/tools/clawgatectl.nix` builds a Go CLI from a LOCAL working tree of a
DIFFERENT repo (`~/workspace/homelab-talos/containers/clawgate`). Two repos, two
delivery mechanisms, and only one of them is automatic:

  * the CODE is read live off disk — nothing in devrc converges that repo
    (`scripts/ship.sh` is scoped to `$HOME/workspace/devrc`);
  * the VERSION used to be a hand-maintained literal in the devrc file, stamped
    into the binary with `-X main.buildVersion=`.

Measured 2026-08-14: homelab-infra #323 added `task status`/`task comment` and
set the Go default to "0.7.95"; four minutes later devrc #483 bumped the nix
literal 0.7.87 -> 0.7.95 and shipped to both hosts. The laptop's homelab-talos
was 24 commits behind — its client.go correctly said "0.7.87" — and the ldflag
OVERWROTE that truth. The laptop then carried a binary with neither subcommand,
labelled 0.7.95; `clawgatectl task status <id> in_progress` printed help and
exited 0. A silent no-op wearing a correct-looking version string.

So the invariant asserted here is: the version in the store path and the version
compiled into the binary both come from the file being compiled, and there is no
literal anywhere that can disagree with it.

TWO LAYERS, ON PURPOSE
----------------------
1. STRUCTURAL (always runs): no hardcoded version literal survives in the nix
   file, the extraction reads the Go source, and the ldflag is fed the SAME
   derived value rather than a second literal.
2. BEHAVIOURAL (needs `nix-instantiate`): the file is actually EVALUATED against
   throwaway fixture source trees, with `pkgs` stubbed down to the four
   attributes it touches. That is the layer that can see a regex which parses
   but extracts the wrong thing — a structural check type-checks past that.

The behavioural layer FAILS rather than skips when `nix-instantiate` is missing
— the policy `test_opencode_config.py::nix_eval` already sets in this repo, and
`nix-instantiate` is both a run-tests.sh REQUIRED_TOOL and on the flake gate's
sandbox PATH. A skip there would be a green that measured nothing.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
NIX_FILE = REPO_ROOT / "nix" / "pkgs" / "tools" / "clawgatectl.nix"
FUZZYCLAW_NIX = REPO_ROOT / "nix" / "pkgs" / "tools" / "tmux-fuzzyclaw.nix"

SRC_SUBDIR = "homelab-talos/containers/clawgate/cmd/clawgatectl"

# A `pkgs` cut down to exactly what clawgatectl.nix touches. `buildGoModule` is
# the identity function, so evaluating the file produces the ARGUMENT ATTRSET
# rather than a derivation — no store write, no build, no network, and every
# field this test cares about is readable straight off it.
STUB_NIX = """{ file, workspace }:
let
  lib = {
    cleanSource = x: x;
    optionals = c: l: if c then l else [ ];
    platforms = { linux = [ "x86_64-linux" ]; };
  };
  pkgs = { inherit lib; buildGoModule = a: a; };
  out = import file { inherit pkgs workspace; };
in
if out == [ ] then "UNAVAILABLE"
else {
  version = (builtins.head out).version;
  ldflags = (builtins.head out).ldflags;
  pname = (builtins.head out).pname;
}
"""

def _tree(tmp_path, *, client_go=None, main_go='package main\n\nfunc main() {}\n'):
    """Build a throwaway `workspace` containing the clawgatectl source files."""
    ws = tmp_path / "ws"
    d = ws / SRC_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    if main_go is not None:
        (d / "main.go").write_text(main_go)
    if client_go is not None:
        (d / "client.go").write_text(client_go)
    return ws


def _eval(tmp_path, ws):
    # 🔴 FAIL, NOT SKIP — the same policy scripts/tests/test_opencode_config.py's
    # `nix_eval()` already sets, and for the same reason: this is the only layer
    # that can see a regex which parses but extracts the wrong thing, so a skip
    # here is a green that measured nothing. `nix-instantiate` is a REQUIRED_TOOL
    # in run-tests.sh and is on the flake gate's sandbox PATH, so an absence is a
    # broken environment, not an expected condition.
    if shutil.which("nix-instantiate") is None:
        pytest.fail(
            "nix-instantiate not on PATH. These tests EVALUATE clawgatectl.nix "
            "against fixture source trees and must not be skipped — a skip is "
            "how a binary mislabelled with a hardcoded version ships. Run under "
            "`nix develop` / scripts/gate.sh."
        )
    stub = tmp_path / "stub.nix"
    stub.write_text(STUB_NIX)
    proc = subprocess.run(
        ["nix-instantiate", "--eval", "--strict", "--json", str(stub),
         "--argstr", "file", str(NIX_FILE),
         "--argstr", "workspace", str(ws)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, (
        "evaluating clawgatectl.nix failed:\n" + proc.stdout + proc.stderr
    )
    return json.loads(proc.stdout)


CLIENT_GO = '''package main

import "fmt"

// buildVersion is the clawgate server version this CLI was built against. It is
// overridable at link time (-ldflags "-X main.buildVersion=0.8.0").
var buildVersion = "%s"

const emptyPathParamMsg = "refusing to build a request path"

func show() { fmt.Println(buildVersion) }
'''


# --------------------------------------------------------------------------- #
# 1. BEHAVIOURAL — the derivation reports what the SOURCE says, and only that
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("declared", ["0.7.87", "9.9.9", "1.2.3-rc4"])
def test_the_version_comes_from_the_go_source(tmp_path, declared):
    """🔴 THE REGRESSION. Three DISTINCT values, none of them the version the
    file used to hardcode, so an assertion cannot pass by reading a literal that
    happens to agree — the mutation that reintroduces `version = "0.7.95"` has
    no fixture it can satisfy.
    """
    ws = _tree(tmp_path, client_go=CLIENT_GO % declared)
    got = _eval(tmp_path, ws)
    assert got != "UNAVAILABLE", got
    assert got["version"] == declared, got
    assert "-X main.buildVersion=%s" % declared in got["ldflags"], got



def test_the_stamped_ldflag_and_the_store_version_are_the_same_string(tmp_path):
    """The two halves of the 08-14 failure are one value now.

    `version` decides the store path; the ldflag decides what the binary says
    about itself. They disagreed for ~19 hours on the laptop and nothing could
    see it. Asserted as an EQUALITY between the two evaluated fields, not as the
    presence of a substring in the nix file.
    """
    ws = _tree(tmp_path, client_go=CLIENT_GO % "4.5.6")
    got = _eval(tmp_path, ws)
    stamps = [f for f in got["ldflags"] if f.startswith("-X main.buildVersion=")]
    assert stamps == ["-X main.buildVersion=" + got["version"]], got



@pytest.mark.parametrize("client_go,why", [
    ('package main\n\nvar buildVersionX = "9.9.9"\n', "declaration renamed"),
    ('package main\n\nvar buildVersion string\n', "no literal to read"),
    ("package main\n\nconst buildVersion = \"9.9.9\"\n", "const, not var"),
    ('var buildVersion = "1.1.1"\nvar buildVersion = "2.2.2"\n', "ambiguous: two matches"),
    (None, "client.go absent entirely"),
])
def test_an_unparseable_source_yields_NO_PACKAGE_not_a_fallback(tmp_path, client_go, why):
    """🔴 A fallback literal would recreate the exact lie this change removes.

    The chosen failure mode is `available = false` — no binary, so `clawgatectl:
    command not found` at use time. Loud, and specifically NOT a failed
    home-manager switch: ship.sh reports a failed switch as a SKIPPED host, the
    documented failure mode that silently stops all future delivery.

    The two-match case is here because "extract the first match" is the obvious
    implementation and it is a guess presented as a fact.
    """
    ws = _tree(tmp_path, client_go=client_go)
    assert _eval(tmp_path, ws) == "UNAVAILABLE", why



def test_a_checkout_too_old_to_have_main_go_still_yields_no_package(tmp_path):
    """The pre-existing guard must survive the new one — a checkout predating
    cmd/clawgatectl would otherwise fail deep in the Go build and take the whole
    switch down with it (measured on the laptop 2026-08-13, c417af30)."""
    ws = _tree(tmp_path, client_go=CLIENT_GO % "9.9.9", main_go=None)
    assert _eval(tmp_path, ws) == "UNAVAILABLE"



def test_the_evaluator_can_tell_available_from_unavailable(tmp_path):
    """🔴 POSITIVE CONTROL for the harness itself. Every UNAVAILABLE assertion
    above is worthless if the stub returns "UNAVAILABLE" for everything — a
    scanner wired to nothing. So: one tree that MUST evaluate to a package, and
    one that must not, through the identical code path.
    """
    good = _eval(tmp_path / "a", _tree(tmp_path / "a", client_go=CLIENT_GO % "7.7.7"))
    bad = _eval(tmp_path / "b", _tree(tmp_path / "b", client_go=None))
    assert good != "UNAVAILABLE" and good["pname"] == "clawgatectl", good
    assert bad == "UNAVAILABLE", bad


# --------------------------------------------------------------------------- #
# 2. STRUCTURAL — always runs, including where nix does not exist
# --------------------------------------------------------------------------- #
def _code_lines():
    """Non-comment lines of clawgatectl.nix. The header DOCUMENTS the old
    literal (`0.7.87 -> 0.7.95`) as the incident record, so a whole-file grep
    would flag the file's own post-mortem."""
    return [ln for ln in NIX_FILE.read_text().splitlines()
            if not ln.strip().startswith("#")]


def test_no_hardcoded_version_literal_survives_in_the_nix_file():
    """The literal IS the bug. Not "is out of date" — a version written in devrc
    is a claim about code in homelab-talos, and nothing holds the two together.
    """
    import re
    offenders = [ln for ln in _code_lines()
                 if re.search(r'version\s*=\s*"[0-9]', ln, re.I)]
    assert offenders == [], (
        "clawgatectl.nix hardcodes a version — it must be READ from "
        "cmd/clawgatectl/client.go instead: %r" % offenders
    )


def test_the_version_is_read_from_the_compiled_source_file():
    code = "\n".join(_code_lines())
    assert "builtins.readFile" in code
    assert "cmd/clawgatectl/client.go" in code, (
        "the version must be derived from the file that is actually compiled"
    )
    assert "builtins.match" in code


def test_the_ldflag_is_fed_the_derived_value_and_not_a_second_literal():
    code = "\n".join(_code_lines())
    stamps = [ln for ln in _code_lines() if "main.buildVersion=" in ln]
    assert len(stamps) == 1, stamps
    assert "-X main.buildVersion=${parsedVersion}" in stamps[0], stamps
    assert "parsedVersion" in code


def test_no_fingerprint_or_suffix_is_appended_to_the_stamped_version():
    """🔴 client.go compares `h.Version == buildVersion` by EXACT equality, so
    any suffix ("-dirty", a tree hash) fires the skew note on every command on
    every host forever. A permanently-noisy warning is worse than none.

    Asserted on the STAMP LINE's shape: the interpolation must be the whole tail
    of the flag, with nothing concatenated after it.
    """
    stamps = [ln.strip() for ln in _code_lines() if "main.buildVersion=" in ln]
    assert len(stamps) == 1, stamps
    assert stamps[0] == '''ldflags = [ "-s" "-w" "-X main.buildVersion=${parsedVersion}" ];''', (
        "the stamped version is not exactly the derived value: %r" % stamps
    )


def test_an_unparseable_source_switches_the_package_OFF_rather_than_failing():
    """Structural counterpart to the behavioural test above, so the claim is
    still made where nix is unavailable: availability is a conjunction of the
    path guard and a successful parse, and the whole package rides on it."""
    code = "\n".join(_code_lines())
    assert "available = builtins.pathExists mainFile && parsedVersion != null;" in code
    assert "pkgs.lib.optionals available" in code
    assert "throw" not in code and "assert " not in code, (
        "an unparseable source must not fail the switch — ship.sh reports that "
        "as a SKIPPED host, which stops all future delivery to it"
    )


def test_the_header_no_longer_describes_a_hand_maintained_version():
    """A comment is a claim too. The old header explained the version/source
    relationship in terms this change makes false."""
    header = NIX_FILE.read_text().split("{ pkgs, workspace }:")[0]
    assert "Stamping" not in header
    assert "READ OUT OF THE SOURCE" in header, (
        "the header must state where the version now comes from"
    )
    assert "buildVersion" in header


# --------------------------------------------------------------------------- #
# 3. THE SIBLING — tmux-fuzzyclaw has the same SHAPE and could not take the fix
# --------------------------------------------------------------------------- #
def test_tmux_fuzzyclaw_is_documented_as_having_no_version_source_of_truth():
    """🔴 An ASSERTED FINDING, not an omission.

    tmux-fuzzyclaw.nix has the identical pattern — a hardcoded `version` plus a
    matching hardcoded ldflag, built from `${workspace}/tmux-fuzzyclaw` — so the
    obvious question is why it did not get the same treatment. Measured
    2026-08-18: its `cmd/version.go` says `var Version = "dev"`, the string
    "2.0.0" appears NOWHERE in that repo outside the devrc nix file, and the
    repo carries no git tags. There is no source of truth to read; inventing one
    would be the same lie in a new place.

    This test fails if that ever stops being true, which is the trigger to apply
    Change A there too.
    """
    src = FUZZYCLAW_NIX.read_text()
    assert "workspace}/tmux-fuzzyclaw" in src, "the sibling no longer builds from a local tree"
    note = [ln for ln in src.splitlines() if "cmd/version.go" in ln]
    assert note, (
        "tmux-fuzzyclaw.nix keeps a hand-maintained version literal and must SAY "
        "why it cannot be derived (its cmd/version.go declares Version = \"dev\")"
    )
