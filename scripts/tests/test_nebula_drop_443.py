"""Coverage for nix/system/apply-nebula-drop-443.sh.

The script edits a root-owned /etc/nixos/configuration.nix and then rebuilds the
OS, so the value of a test here is entirely in the parts that can be exercised
without root: the SUBSTITUTION, and the REFUSALS. Does it remove exactly the
`"<ip>:443"` token beside a `"<ip>:4242"` for the SAME host, leave every other
shape byte-identical, and refuse rather than guess when the anchor is not what
it assumed?

🔴 The negative controls are the point, not the happy path. A looser pattern
eats a `"a:4242" "b:4242"` row (two addresses, neither of them :443) or matches
`:4433`/`:14443`, and both failures are silent — the file still parses as Nix
and the operator sees a successful run. Every such row below is asserted
BYTE-IDENTICAL, not merely "still present".

Cases corresponding to real audit findings on PR #1361, named exactly:
  * `test_scoped_helper_ignores_an_unrelated_443` (round 1, #1) — a whole-file
    `:443` grep made the script refuse on any config carrying an unrelated
    quoted `:443` (an nginx proxyPass is the obvious one), blaming a
    substitution that had worked.
  * `test_the_guess_gate_is_gone` (rounds 2-4) — the "is a :443 present in some
    OTHER shape?" detector was rewritten three times and broke a different way
    each round (miss a wrapped list; fire on a WireGuard endpoint AND miss
    DNS/IPv6; print block-relative line numbers as file lines; be defeated by a
    comment). It was DELETED, not fixed a fourth time: `--check` reads the
    running process and answers the same question without guessing at Nix.
  * `test_unknown_argument_refuses` (round 1, #3) — `MODE="${1:-apply}"` was
    compared only against `--check`, so `--dry-run` ran the DESTRUCTIVE path.
  * `test_verify_is_errexit_safe` (round 2, F1) — `check_one; check_rc=$?` is
    dead code under `set -e`: the bare call exits the shell before the
    assignment, so the whole three-outcome verification was unreachable.

🔴 An earlier version of this docstring named two tests that did not exist. A
docstring is a claim like any other; these names are asserted by
`test_docstring_names_only_real_tests` below.
"""

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "nix" / "system" / "apply-nebula-drop-443.sh"

# The substitution, lifted from the script so the test exercises the shipped
# expression rather than a copy that can drift out of step with it.
_SED_RE = re.compile(r"^sed -E '(?P<expr>s/.*/)' \"\$CFG\"", re.M)


def _sed_expr() -> str:
    m = _SED_RE.search(SCRIPT.read_text())
    assert m, "could not find the sed substitution in the script — did it move?"
    return m.group("expr")


def _apply(text: str) -> str:
    """Run the script's OWN sed expression over `text`."""
    out = subprocess.run(
        ["sed", "-E", _sed_expr()],
        input=text, capture_output=True, text=True, check=True,
    )
    return out.stdout


PAIR = '      "10.42.0.2" = [ "203.0.113.9:4242" "203.0.113.9:443" ];\n'
GONE = '      "10.42.0.2" = [ "203.0.113.9:4242" ];\n'


def test_removes_the_dead_address():
    assert _apply(PAIR) == GONE


def test_is_idempotent():
    assert _apply(GONE) == GONE


@pytest.mark.parametrize(
    "line",
    [
        # 🔴 THE row a looser pattern eats: two addresses, neither of them :443.
        '      "10.42.0.1" = [ "192.168.50.94:4242" "198.51.100.7:4242" ];\n',
        # Ports that merely CONTAIN 443.
        '      "10.42.0.3" = [ "203.0.113.9:4242" "203.0.113.9:4433" ];\n',
        '      "10.42.0.4" = [ "203.0.113.9:4242" "203.0.113.9:14443" ];\n',
        # 🔴 A :443 belonging to a DIFFERENT host. Writing this case is what
        # found that the substitution matched regardless of IP — only the shell
        # guard's ORDERING kept that safe. The expression now carries a `\2`
        # backreference, so it discriminates on its own and this row survives
        # byte-identical even if the substitution is ever reached out of order.
        '      "10.42.0.5" = [ "203.0.113.9:4242" "198.51.100.7:443" ];\n',
        # Not a staticHostMap entry at all.
        '  services.nginx.virtualHosts."a".locations."/".proxyPass = "https://up:443";\n',
        # Single address, nothing to do.
        '      "10.42.0.6" = [ "203.0.113.9:4242" ];\n',
    ],
)
def test_leaves_other_shapes_byte_identical(line):
    assert _apply(line) == line


def test_preserves_a_trailing_comment():
    src = '      "10.42.0.2" = [ "203.0.113.9:4242" "203.0.113.9:443" ];  # keep me\n'
    assert _apply(src) == '      "10.42.0.2" = [ "203.0.113.9:4242" ];  # keep me\n'


def test_changes_exactly_one_line_in_a_realistic_file():
    src = (
        "{ config, pkgs, ... }:\n{\n"
        '  services.nginx.virtualHosts."a".locations."/".proxyPass = "https://up:443";\n'
        "  services.nebula.networks.mesh = {\n    staticHostMap = {\n"
        '      "10.42.0.1" = [ "192.168.50.94:4242" "198.51.100.7:4242" ];\n'
        + PAIR +
        "    };\n  };\n}\n"
    )
    out = _apply(src)
    changed = [
        (a, b) for a, b in zip(src.splitlines(), out.splitlines()) if a != b
    ]
    assert len(changed) == 1, changed
    assert out.count("\n") == src.count("\n")
    # The unrelated nginx :443 survives — the bug that made the script unusable.
    assert 'proxyPass = "https://up:443"' in out


def test_unknown_argument_refuses():
    """An unrecognised argument must NOT fall through to the destructive path.

    Asserted on the rc and the message; the script exits 64 before any
    root check, so this needs neither root nor a config file.
    """
    r = subprocess.run(
        ["bash", str(SCRIPT), "--dry-run"], capture_output=True, text=True
    )
    assert r.returncode == 64, (r.returncode, r.stdout, r.stderr)
    assert "unknown argument" in r.stderr


def _hostmap_443_re() -> str:
    """Lift `hostmap_443`'s real pattern out of the script.

    Only ONE helper survives: the `any_443_addr` / `hostmap_block` pair that four
    audit rounds kept re-breaking was DELETED rather than fixed a fifth time —
    `--check`, which reads the running process, answers that question and cannot
    be fooled by Nix layout. `hostmap_443` answers the narrow question "did a
    :443 survive MY one-line substitution?" against a line this script just
    wrote, and is unaffected by any of those findings.
    """
    m = re.search(r"^hostmap_443\(\)\s*\{ grep -nE '(?P<re>[^']+)'", SCRIPT.read_text(), re.M)
    assert m, "could not find hostmap_443() — did it move or get renamed?"
    return m.group("re")


def _matches(pattern: str, line: str) -> bool:
    return subprocess.run(
        ["grep", "-qE", pattern], input=line, text=True
    ).returncode == 0


def _check_443_re() -> str:
    """The `--check` :443 pattern, lifted from the script rather than retyped."""
    m = re.search(r"grep -qE '(?P<re>:443[^']*)'", SCRIPT.read_text())
    assert m, "could not find the --check :443 pattern — did it move?"
    return m.group("re")


NGINX_443 = '  services.nginx.virtualHosts."a".locations."/".proxyPass = "https://up:443";\n'


def test_scoped_helper_ignores_an_unrelated_443():
    """`hostmap_443` answers 'did a :443 survive MY patch?' — it must be scoped.

    Round-1 finding #1: a whole-file grep here aborted the run on any config
    containing an unrelated quoted `:443`, so the script could never succeed.
    """
    pat = _hostmap_443_re()
    assert not _matches(pat, NGINX_443), "scoped helper matched an nginx proxyPass"
    assert _matches(pat, PAIR), "scoped helper missed a real one-line entry"


def test_the_guess_gate_is_gone():
    """The deleted detector must not come back without a fresh decision.

    Four rounds of findings lived in `any_443_addr`/`hostmap_block`, each fix
    moving the failure rather than removing it. Re-adding either is a choice
    that should be made deliberately, not by copying an old draft back in.
    """
    code = _code_lines()
    assert "any_443_addr" not in code
    assert "hostmap_block" not in code


def test_verify_is_errexit_safe():
    """`check_one` must never be called bare — round-2 finding F1.

    Under `set -e` a bare `check_one; check_rc=$?` exits the shell on any
    non-zero return, so the three-outcome block was dead code for rc 1 and rc 2
    and a transient read failure silently rolled back a good change.
    """
    code = _code_lines()
    assert "check_one; check_rc=$?" not in code, "bare call is errexit-unsafe"
    assert code.count("check_one || check_rc=$?") >= 2, "both call sites must be guarded"
    # 🔴 THE `rc=0` PRE-INIT IS LOAD-BEARING AND WAS UNGUARDED. Without it, under
    # `set -u`, `check_one || check_rc=$?` is an unbound-variable crash on the
    # SUCCESS path — so after a verification that PASSED, `OK` is still 0 and the
    # EXIT trap rolls back a correctly-applied change, re-adding the dead entry.
    # That is the exact disaster F1 was about, reached from the other side.
    assert code.count("check_rc=0; check_one || check_rc=$?") >= 2, (
        "the `check_rc=0;` pre-init is missing — set -u crashes on the SUCCESS path"
    )


def test_docstring_names_only_real_tests():
    """Every `test_*` named in the module docstring must actually exist."""
    doc = __doc__ or ""
    named = set(re.findall(r"`(test_\w+)`", doc))
    defined = set(re.findall(r"^def (test_\w+)", Path(__file__).read_text(), re.M))
    missing = named - defined
    assert not missing, f"docstring names tests that do not exist: {sorted(missing)}"


def test_check_pattern_does_not_match_a_longer_port():
    """`--check`'s :443 pattern must not fire on :4433 / :14443.

    Round-2 mutation sweep found a loosened `':443$'` surviving. This pins the
    boundary behaviourally: a bare `:443$` would miss a trailing-comment line,
    and a bare `:443` would match `:4433`.
    """
    pat = _check_443_re()
    assert _matches(pat, "  - 203.0.113.9:443\n")
    # 🔴 THE DISCRIMINATING CASE. Round 3 replaced a shape assertion with three
    # behavioural ones and lost the ability to see a `:443$` regression — all
    # three behave identically under it. A trailing comment is what separates
    # them, and the round-2 sweep had named this mutant without exercising it.
    assert _matches(pat, "  - 203.0.113.9:443   # dead\n"), "a ':443$' regression would pass"
    assert not _matches(pat, "  - 203.0.113.9:4433\n")
    assert not _matches(pat, "  - 203.0.113.9:14443\n")


def _code_lines() -> str:
    """The script with comment-only lines stripped.

    🔴 Asserting on the raw text is a SPELLED guard, not a structural one: the
    first draft of the test below failed because the word `systemctl cat`
    appears in a COMMENT explaining why it is not used. A guard that a comment
    can trip is a guard that says nothing about the code.
    """
    return "\n".join(
        ln for ln in SCRIPT.read_text().splitlines()
        if not ln.lstrip().startswith("#")
    )


def test_check_mode_reads_the_running_process():
    """`--check` must read /proc/<pid>/cmdline, not `systemctl cat`.

    The unit file reflects post-switch on-disk state; the process reflects what
    it was actually started with. Reading the former reports PASS while a
    deferred restart leaves the dead address live.
    """
    code = _code_lines()
    assert "/proc/$pid/cmdline" in code
    assert "systemctl cat" not in code, "still reading the unit file, not the process"


def test_check_mode_has_a_positive_control():
    """`--check` must refuse to pass a block it could not actually read."""
    assert "grep -qE ':4242'" in SCRIPT.read_text()
