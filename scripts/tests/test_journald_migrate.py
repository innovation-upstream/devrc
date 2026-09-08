"""Tests for scripts/lib/journald_migrate.py.

Every case here is either a shape measured on this fleet or a defect an adversarial
audit of PR #1412 found in the first implementation. The audit-found ones are marked
`# audit R1 #N` so a later reader can tell a regression guard from a shape test.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "lib"))

import journald_migrate as jm  # noqa: E402

BLOCK = """\
{
  services.openssh.enable = true;

  services.journald.extraConfig = ''
    SystemMaxUse=2G
  '';

  fonts.packages = [ ];
}
"""

INLINE = """\
{
  services.journald.extraConfig = "SyncIntervalSec=30s";
}
"""


def _settings_body(text: str) -> str:
    """The lines between `settings.Journal = {` and its closing brace."""
    out, inside = [], False
    for line in text.splitlines():
        if "services.journald.settings.Journal" in line:
            inside = True
            continue
        if inside:
            if line.strip() == "};":
                break
            out.append(line)
    return "\n".join(out)


# --------------------------------------------------------------------------- shapes


def test_block_form_is_migrated_and_the_rest_of_the_file_is_untouched():
    new, pairs = jm.rewrite(BLOCK)
    assert pairs == [("SystemMaxUse", "2G")]
    assert "extraConfig" not in new
    assert '    SystemMaxUse = "2G";' in new
    # everything either side survives verbatim
    assert "services.openssh.enable = true;" in new
    assert "fonts.packages = [ ];" in new


def test_the_inline_double_quoted_form_is_migrated():  # audit R1 #6
    """The laptop carries `= "SyncIntervalSec=30s";`, not a ''-block.

    The first implementation matched only the block form, so it refused on the only
    other host in the fleet while the doc claimed it was host-agnostic.
    """
    new, pairs = jm.rewrite(INLINE)
    assert pairs == [("SyncIntervalSec", "30s")]
    assert 'SyncIntervalSec = "30s";' in new
    assert "extraConfig" not in new


def test_indentation_of_the_original_assignment_is_preserved():
    src = "{\n      services.journald.extraConfig = ''\n        A=1\n      '';\n}\n"
    new, _ = jm.rewrite(src)
    assert "      services.journald.settings.Journal = {\n" in new
    assert '        A = "1";\n' in new


def test_several_settings_keep_their_order():
    src = BLOCK.replace("SystemMaxUse=2G", "SystemMaxUse=2G\n    MaxRetentionSec=1month")
    _, pairs = jm.rewrite(src)
    assert pairs == [("SystemMaxUse", "2G"), ("MaxRetentionSec", "1month")]


def test_comments_and_blank_lines_are_skipped():
    src = BLOCK.replace(
        "SystemMaxUse=2G", "# a hash comment\n\n    ; a semicolon comment\n    SystemMaxUse=2G"
    )
    _, pairs = jm.rewrite(src)
    assert pairs == [("SystemMaxUse", "2G")]


def test_crlf_line_endings_survive_the_rewrite():  # audit R1 #9
    """A universal-newlines read + "\\n" write rewrites every line ending in the file,
    which degrades the operator's `diff -u` review surface to "everything changed"."""
    src = BLOCK.replace("\n", "\r\n")
    new, pairs = jm.rewrite(src)
    assert pairs == [("SystemMaxUse", "2G")]
    assert 'SystemMaxUse = "2G";' in new
    assert "\r\n" in new
    # the whole point: no line ending anywhere in the file changed flavour
    assert new.count("\n") == new.count("\r\n"), "a bare LF was introduced"


# ------------------------------------------------------------------ value escaping


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2G", '"2G"'),
        ("1month", '"1month"'),
        (r"5m\t", r'"5m\\t"'),  # audit R1 #10 — a backslash, else a literal TAB
        ('say "hi"', r'"say \"hi\""'),
        ("${cap}", r'"\${cap}"'),
        (r"back\\slash", r'"back\\\\slash"'),
    ],
)
def test_values_are_escaped_for_a_nix_double_quoted_string(raw, expected):
    assert jm.to_nix_string(raw) == expected


def test_a_backslash_value_does_not_become_a_tab():  # audit R1 #10
    r"""`SyncIntervalSec=5m\t` is 4 characters in a ''-string and a TAB in a "-string.

    The first implementation interpolated straight into `"{v}"`, so this parsed, and
    evaluated, to the wrong value — the one silently-wrong case in that rewrite.
    """
    src = BLOCK.replace("SystemMaxUse=2G", r"SyncIntervalSec=5m\t")
    new, _ = jm.rewrite(src)
    body = _settings_body(new)
    assert body.strip(), "the helper found no settings body — the assertions below would be vacuous"
    assert r'"5m\\t"' in new, "the backslash was not doubled for the \"-string"
    # The negative half, and it CAN fail: an unescaped implementation emits exactly this.
    assert r'"5m\t"' not in new, "emitted an unescaped backslash — Nix reads it as a TAB"


def test_a_backslash_in_a_value_is_not_eaten_as_a_regex_group_reference():
    r"""re.sub() interprets \1 and \g<n> in the REPLACEMENT, which would undo the
    escaping above. The rewrite passes a function to sub() for exactly this reason."""
    src = BLOCK.replace("SystemMaxUse=2G", r"SystemMaxUse=\1\g<0>")
    new, _ = jm.rewrite(src)
    assert r"\\1\\g<0>" in new


# ------------------------------------------------------------------------ refusals


@pytest.mark.parametrize(
    ("src", "because"),
    [
        (BLOCK.replace("services.openssh.enable = true;", BLOCK.split("\n", 1)[1]), "two blocks"),
        ("{\n  services.openssh.enable = true;\n}\n", "no assignment at all"),
        (BLOCK.replace("SystemMaxUse=2G", "[Journal]\n    SystemMaxUse=2G"), "section header"),
        (BLOCK.replace("SystemMaxUse=2G", "# only a comment"), "no settings"),
        (BLOCK.replace("SystemMaxUse=2G", "Max_Use=2G"), "underscore in key"),
        (BLOCK.replace("SystemMaxUse=2G", "9Lives=2G"), "key starting with a digit"),
        (BLOCK.replace("SystemMaxUse=2G", "no equals sign here"), "no `=`"),
    ],
)
def test_refuses_rather_than_guessing(src, because):
    with pytest.raises(jm.Refused):
        jm.rewrite(src)


@pytest.mark.parametrize(
    ("value", "what"),
    [
        ("${cap}", "a bare antiquotation"),
        ("2G${suffix}", "an antiquotation with a literal prefix"),
        ("''${literal}", "a ''-escaped antiquotation"),
    ],
)
def test_a_value_that_depends_on_EVALUATION_is_refused(value, what):  # audit R2 NEW-5
    """`${x}` in the source is evaluated by Nix; escaping it freezes the literal text.

    Nothing downstream catches that substitution: `nix-instantiate --parse` accepts both
    spellings, and the apply script's post-switch check compares against the same
    un-evaluated text it printed, so it MATCHES and reports success. Measured with
    `nix-instantiate --eval`: source `''SystemMaxUse=${cap}''` with cap="9G" evaluates to
    `SystemMaxUse=9G`, while the escaped rewrite evaluates to the literal `${cap}`.
    """
    src = BLOCK.replace("SystemMaxUse=2G", f"SystemMaxUse={value}")
    with pytest.raises(jm.Refused, match="antiquotation"):
        jm.rewrite(src)


def test_parse_settings_REQUIRES_the_form_argument():  # audit R4 #3
    """Pins the parameter as required, which a default silently un-pins.

    It defaulted to "block" — the permissive side — for one round, and audit round 4
    measured that FLIPPING that default survived the entire suite, because the only
    caller passes it explicitly. Nothing would have caught a future caller omitting it
    on inline input, which is precisely the silently-wrong value the guard exists to
    stop. This test fails if a default is reintroduced.
    """
    with pytest.raises(TypeError):
        jm.parse_settings("SystemMaxUse=2G")  # type: ignore[call-arg]


def test_parse_settings_refuses_an_unknown_form():
    with pytest.raises(jm.Refused, match="unknown source form"):
        jm.parse_settings("SystemMaxUse=2G", "sideways")


def test_a_bare_double_apostrophe_escape_is_refused():  # audit R3 NEW-C
    """Every earlier param of the test above also contained `${`, so all three died on
    THAT half of the guard. Audit round 3 mutation-tested it: deleting `or "''" in value`
    SURVIVED a green 30-test run. This case has no `${` and kills that mutant."""
    src = BLOCK.replace("SystemMaxUse=2G", r"SystemMaxUse=2G''\\")
    with pytest.raises(jm.Refused, match="antiquotation"):
        jm.rewrite(src)


@pytest.mark.parametrize("value", [r"5m\t", r"C:\\path", r"a\nb"])
def test_the_INLINE_form_refuses_a_backslash(value):  # audit R3, mirror of R1 #10
    """The mirror hazard. In a `''`-string a backslash is literal, so re-escaping it is
    right. In a `"`-string Nix has ALREADY interpreted it — `"5m\t"` IS `5m<TAB>` — so
    escaping the raw text would emit a literal backslash-t instead, wrong in the opposite
    direction. We only see un-evaluated text, so refuse."""
    src = INLINE.replace("SyncIntervalSec=30s", f"SyncIntervalSec={value}")
    with pytest.raises(jm.Refused, match="backslash"):
        jm.rewrite(src)


def test_the_BLOCK_form_still_ACCEPTS_a_backslash():
    """The other side of that boundary — refusing both forms would be over-wide, and
    this is the case R1 #10's escaping exists to serve."""
    src = BLOCK.replace("SystemMaxUse=2G", r"SyncIntervalSec=5m\t")
    _, pairs = jm.rewrite(src)
    assert pairs == [("SyncIntervalSec", r"5m\t")]


def test_the_inline_form_refuses_an_antiquotation_too():  # audit R2 NEW-5
    """Same hazard, other spelling: `${` antiquotes inside a "-string as well."""
    src = INLINE.replace("SyncIntervalSec=30s", "SyncIntervalSec=${interval}")
    with pytest.raises(jm.Refused, match="antiquotation"):
        jm.rewrite(src)


def test_a_plain_dollar_sign_is_still_allowed():
    """Only `${` antiquotes — a lone `$` is an ordinary character and must not refuse."""
    src = BLOCK.replace("SystemMaxUse=2G", "SystemMaxUse=2G$")
    _, pairs = jm.rewrite(src)
    assert pairs == [("SystemMaxUse", "2G$")]


def test_two_assignments_in_DIFFERENT_forms_are_also_refused():
    """One block + one inline is still ambiguous — the count is across both spellings."""
    src = BLOCK.replace(
        "  fonts.packages = [ ];",
        '  services.journald.extraConfig = "SyncIntervalSec=30s";',
    )
    with pytest.raises(jm.Refused, match="found 2"):
        jm.rewrite(src)


def test_an_already_migrated_file_is_refused_not_double_migrated():
    new, _ = jm.rewrite(BLOCK)
    with pytest.raises(jm.Refused, match="found 0"):
        jm.rewrite(new)


# ------------------------------------------------------------------------- the CLI


def test_the_cli_writes_the_rewritten_file_and_reports_the_keys(tmp_path):
    src = tmp_path / "configuration.nix"
    src.write_text(BLOCK, encoding="utf-8")
    out = tmp_path / "out.nix"
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "lib" / "journald_migrate.py"),
         str(src), "--out", str(out), "--print-keys"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "SystemMaxUse=2G" in proc.stderr
    assert 'SystemMaxUse = "2G";' in out.read_text(encoding="utf-8")
    assert src.read_text(encoding="utf-8") == BLOCK, "the source must not be modified"


def test_the_cli_exits_2_and_writes_nothing_when_it_refuses(tmp_path):
    src = tmp_path / "configuration.nix"
    src.write_text("{ }\n", encoding="utf-8")
    out = tmp_path / "out.nix"
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "lib" / "journald_migrate.py"),
         str(src), "--out", str(out)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 2
    assert "REFUSED" in proc.stderr
    assert not out.exists(), "a refusal must not leave a half-written output file"
