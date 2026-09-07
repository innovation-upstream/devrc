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

Three of these cases correspond to real audit findings on PR #1361:
  * `test_unrelated_443_elsewhere_is_ignored` — a whole-file `:443` grep made
    the script refuse on any config carrying an unrelated quoted `:443`
    (an nginx proxyPass is the obvious one), blaming a substitution that worked.
  * `test_already_removed_is_a_noop_even_with_unrelated_443` — the same bug made
    the documented "re-running is a no-op" false.
  * `test_unknown_argument_refuses` — `MODE="${1:-apply}"` compared only against
    `--check`, so `--dry-run` ran the DESTRUCTIVE path.
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


def test_scoped_443_search_is_not_whole_file():
    """The `:443` searches must be scoped to staticHostMap-shaped lines.

    Pins the absence of the whole-file grep that made the script refuse on any
    config carrying an unrelated quoted `:443`. Structural: the helper must
    exist and the bare whole-file forms must not.
    """
    text = SCRIPT.read_text()
    assert "hostmap_443()" in text, "the scoping helper is gone"
    assert 'grep -qE \':443"\' "$CFG"' not in text
    assert 'grep -qE \':443"\' "$TMP"' not in text


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
