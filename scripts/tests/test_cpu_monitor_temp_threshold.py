"""cpu-monitor's temperature threshold must resolve to DIFFERENT values per host.

WHY THIS FILE EXISTS. `CPU_MON_TEMP_THRESHOLD` was a single shared `88` in
`nix/home.nix`, carrying the comment "This laptop runs hot at idle (cooling
needs attention); warn early" — host-specific prose bolted to a host-agnostic
line. The laptop consequently carried `88 -> 92` as an UNCOMMITTED LOCAL EDIT,
which blocked every `ship.sh` fast-forward to that host (rc 7): the documented
"a skipped host silently stops receiving every future change while still looking
healthy" failure. It was found only because an unrelated ship happened to run.

So the invariant under test is not "the value is 92". It is that the expression
in `nix/home.nix` RESOLVES DIFFERENTLY on the two hosts — because a value that
can only be spelled once is a value the operator will re-edit locally, and the
ship will block again. A conditional that silently yields one value on both
hosts is the exact failure mode here, and in a diff it looks identical to
success.

HOW THIS DRIVES REAL CODE: the deployed expression is read out of
`nix/home.nix` (never restated here) and evaluated by `nix-instantiate --eval`
under an injected `isLaptop = true` and `isLaptop = false`. Two controls keep
that harness honest:

  * `test_the_harness_reports_SAME_for_a_constant` — NEGATIVE control. A harness
    that fabricated a difference would make the real assertion pass for the
    wrong reason.
  * `test_the_harness_can_observe_a_difference` — POSITIVE control on the eval
    plumbing itself.

And `test_home_nix_still_binds_isLaptop_to_the_backlight_probe` is a SEAM guard
(an invariant guard, not regression coverage — it did not fail before this
change): the harness INJECTS `isLaptop`, so a rename in `home.nix` would leave
these tests green over an expression production can no longer evaluate.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOME_NIX = ROOT / "nix" / "home.nix"

# The value each host must end up with. Stated here as literals on purpose: a
# test that derived them from the implementation would assert nothing.
LAPTOP_TEMP_THRESHOLD = "92"
WORKBENCH_TEMP_THRESHOLD = "88"


def _deployed_expression():
    """The Nix expression for the CPU_MON_TEMP_THRESHOLD list element, verbatim.

    Taken as the whole (stripped) source line, so an antiquotation containing
    its own double quotes survives — a `"[^"]*"` regex would truncate it at the
    first inner quote and silently hand back half an expression.
    """
    lines = [ln.strip() for ln in HOME_NIX.read_text().splitlines()
             if "CPU_MON_TEMP_THRESHOLD=" in ln and not ln.strip().startswith("#")]
    assert len(lines) == 1, (
        "expected exactly one non-comment CPU_MON_TEMP_THRESHOLD line in "
        "nix/home.nix, found %d: %r — the seam moved" % (len(lines), lines))
    expr = lines[0].rstrip(",")
    assert expr.startswith('"') and expr.endswith('"'), (
        "the CPU_MON_TEMP_THRESHOLD line is not a self-contained Nix string "
        "literal (%r); this harness can no longer evaluate it in isolation"
        % expr)
    return expr


def _eval(expr, is_laptop):
    if not shutil.which("nix-instantiate"):
        pytest.fail(
            "nix-instantiate not on PATH. This test is the only thing proving "
            "the threshold is genuinely host-conditional rather than one shared "
            "value wearing a conditional's clothes; a skip here is exactly how "
            "it collapses back. Run under the flake gate, where nix-instantiate "
            "is a declared required tool."
        )
    wrapped = "let isLaptop = %s; in %s" % ("true" if is_laptop else "false", expr)
    p = subprocess.run(
        ["nix-instantiate", "--eval", "--strict", "--json", "-E", wrapped],
        capture_output=True, text=True, cwd=ROOT, timeout=180,
    )
    assert p.returncode == 0, (
        "nix eval failed for isLaptop=%s on %r:\n%s" % (is_laptop, expr, p.stderr))
    return json.loads(p.stdout)


def test_the_harness_can_observe_a_difference():
    """POSITIVE CONTROL on the eval plumbing: it must be capable of returning
    two different strings, or 'the values differ' below could never be trusted
    to have measured anything."""
    probe = '"X=${if isLaptop then "A" else "B"}"'
    assert _eval(probe, True) == "X=A"
    assert _eval(probe, False) == "X=B"


def test_the_harness_reports_SAME_for_a_constant():
    """NEGATIVE CONTROL. A harness that fabricated a difference — e.g. by
    leaking the injected boolean into its output — would make the real
    assertion pass while the deployed value was still shared."""
    probe = '"X=88"'
    assert _eval(probe, True) == _eval(probe, False) == "X=88"


def test_the_deployed_threshold_differs_between_the_two_hosts():
    """🔴 THE POINT OF THE FILE. One shared value is what stranded the laptop's
    local edit and blocked ship.sh; if these two ever collapse to one string
    again, that recurs."""
    expr = _deployed_expression()
    laptop = _eval(expr, True)
    workbench = _eval(expr, False)
    assert laptop != workbench, (
        "CPU_MON_TEMP_THRESHOLD resolves to %r on BOTH hosts. It is a single "
        "shared value again — the laptop will carry its own threshold as an "
        "uncommitted edit and every ship.sh fast-forward to it will be skipped "
        "(rc 7)." % laptop)


def test_each_host_gets_its_own_measured_threshold():
    expr = _deployed_expression()
    assert _eval(expr, True) == "CPU_MON_TEMP_THRESHOLD=" + LAPTOP_TEMP_THRESHOLD
    assert _eval(expr, False) == "CPU_MON_TEMP_THRESHOLD=" + WORKBENCH_TEMP_THRESHOLD


def test_the_workbench_keeps_the_EARLIER_warning():
    """Direction guard. `isLaptop` is a backlight probe and fails OPEN — an
    unrecognised host evaluates false. That is only acceptable while the false
    branch is the LOWER (earlier-warning) number; inverting the arms would give
    every unknown host a silently laxer threshold."""
    assert int(WORKBENCH_TEMP_THRESHOLD) < int(LAPTOP_TEMP_THRESHOLD)
    expr = _deployed_expression()
    assert int(_eval(expr, False).split("=")[1]) < int(_eval(expr, True).split("=")[1])


def test_home_nix_still_binds_isLaptop_to_the_backlight_probe():
    """INVARIANT GUARD (not regression coverage — it did not fail before this
    change). The tests above INJECT `isLaptop`; production gets it from the
    `let` block. If that binding is renamed or removed, `home.nix` breaks while
    every assertion here stays green over an expression nothing can evaluate."""
    src = HOME_NIX.read_text()
    assert re.search(
        r'^\s*isLaptop = builtins\.pathExists "/sys/class/backlight/', src, re.M), (
        "nix/home.nix no longer binds `isLaptop` to the backlight probe. The "
        "CPU_MON_TEMP_THRESHOLD expression references that name, and this "
        "file's harness injects it — so the seam is now unverified.")
    assert "isLaptop" in _deployed_expression(), (
        "the CPU_MON_TEMP_THRESHOLD expression no longer references isLaptop, "
        "so this file is pinning a discriminator the deployed value ignores.")
