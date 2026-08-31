"""Unit tests for `scripts/testlib/nix_units.py` — the shared nix-source reader.

WHY THIS FILE EXISTS
--------------------
🔴 IT DID NOT, AND THAT IS THE FINDING. `nix_units` grew three times across one
PR's audit ladder — comment stripping, then `directive()`, then a continuation-
line joiner and a balance heuristic — and each round its only exercise was the
two production call sites in `test_index_store_backup_claim.py`, both of which
read single-line directives from a `nix/home.nix` that has never contained a
multi-line one. An anchor-asserted mutation sweep measured the cost: reverting
the joiner, deleting the balance check, joining with `""` instead of `" "`, and
returning None instead of raising on an unterminated declaration ALL SURVIVED a
fully green 812-test run. Every branch written to fix a real defect could have
been reverted the next day with nothing going red.

🔴 THE MODULE IS READ-ONLY AND SO IS THIS FILE. `nix_units` opens nothing and
runs no command; every fixture below is a synthetic string written here. It
reads `nix/home.nix` only through the caller, never itself.

WHAT EACH GROUP PINS, and the measured defect behind it:
  * `strip_nix_comments` — a TRAILING `# retired: <attr> = {` once read as a
    live declaration, while the docstring promised comment-blindness.
  * `directive` name matching — `ExecStart` must not match `ExecStartPre`.
  * `directive` vs substring — deleting an ExecStart line left the guard green
    because `X-Restart-Triggers` on the next line carried the same path.
  * `directive` joining — a WRAPPED declaration is not a multi-line value;
    refusing it turned a benign reformat into a red required check.
  * `directive` refusing — a genuinely ambiguous multi-line value must RAISE,
    never return a plausible truncation a caller would act on.
  * `is_conditional` — a `lib.mkIf` on the line AFTER the `=` was invisible,
    which is the shape the backup SERVICE actually uses.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from testlib.nix_units import (  # noqa: E402
    declares,
    directive,
    is_conditional,
    strip_nix_comments,
    unit_source,
)


# --- strip_nix_comments --------------------------------------------------------

class TestStripNixComments:
    def test_whole_line_comments_go(self):
        """Dropped entirely, not blanked — the line does not survive as empty."""
        assert strip_nix_comments("# gone\nkept = 1;\n") == "kept = 1;"

    def test_trailing_comments_go(self):
        """The measured walk: a trailing comment naming a retired attr path."""
        src = 'other = 1;  # retired: systemd.user.timers.x = {\n'
        assert "systemd.user.timers.x" not in strip_nix_comments(src)

    def test_a_hash_inside_a_string_is_not_a_comment(self):
        """A colour literal or URL fragment must survive intact."""
        src = 'frame_color = "#83a598";  # gruvbox blue accent\n'
        out = strip_nix_comments(src)
        assert '"#83a598"' in out
        assert "gruvbox" not in out

    def test_an_escaped_quote_does_not_flip_string_state(self):
        """🔴 ODD number of escaped quotes, deliberately.

        The first version of this fixture had TWO, so a mutant that stopped
        honouring `\\` produced byte-identical output — the parity error
        cancelled and the branch survived a green suite. One escape does not
        cancel: with the escape ignored, the string is read as closing at
        `\\"`, the trailing `#` lands outside a string, and the comment is cut.
        """
        src = 'a = "3\\" wide";  # gone\n'
        assert strip_nix_comments(src) == 'a = "3\\" wide";'


# --- declares ------------------------------------------------------------------

class TestDeclares:
    SRC = "  systemd.user.services.real = {\n  };\n  # systemd.user.services.ghost = {\n"

    def test_a_live_declaration_reads_true(self):
        assert declares("systemd.user.services.real", self.SRC)

    def test_a_commented_declaration_reads_false(self):
        assert not declares("systemd.user.services.ghost", self.SRC)

    def test_an_absent_one_reads_false(self):
        assert not declares("systemd.user.services.nope", self.SRC)


# --- directive: name matching --------------------------------------------------

class TestDirectiveNameMatching:
    def test_a_prefix_collision_does_not_match(self):
        """`ExecStartPre` must not answer a request for `ExecStart`."""
        blk = 'ExecStartPre = "/a";\nExecStart = "/b";\n'
        assert directive("ExecStart", blk) == '"/b"'
        assert directive("ExecStartPre", blk) == '"/a"'

    def test_only_a_prefix_collision_present_returns_none(self):
        assert directive("ExecStart", 'ExecStartPre = "/a";\n') is None

    def test_absent_returns_none(self):
        assert directive("WantedBy", "Type = 1;\n") is None

    def test_leading_whitespace_and_tight_spacing(self):
        assert directive("Type", "\t  Type='oneshot';\n") == "'oneshot'"


class TestDirectiveIsNotASubstringSearch:
    """🔴 The measured hole: the path appears TWICE, on consecutive lines."""

    BLK = (
        '        ExecStart = "${pyEnv}/bin/python3 %h/scripts/asi/backup.py";\n'
        '        X-Restart-Triggers = [ "${../scripts/asi/backup.py}" ];\n'
    )

    def test_reads_the_directive_not_the_neighbour(self):
        assert directive("ExecStart", self.BLK).endswith('backup.py"')

    def test_deleting_the_directive_is_visible_though_the_path_remains(self):
        without = "\n".join(
            ln for ln in self.BLK.splitlines() if "ExecStart" not in ln)
        assert "backup.py" in without, "control: the path must still be present"
        assert directive("ExecStart", without) is None


# --- directive: joining vs refusing --------------------------------------------

class TestDirectiveJoinsWrappedDeclarations:
    """A wrapped DECLARATION is not a multi-line VALUE."""

    def test_a_value_on_the_next_line_is_joined(self):
        blk = 'ExecStart =\n  "/nix/store/aaa/bin/python /long/path/backup.py";\n'
        assert directive("ExecStart", blk) == '"/nix/store/aaa/bin/python /long/path/backup.py"'

    def test_joining_uses_a_single_space(self):
        assert directive("A", "A =\n  1\n  2;\n") == "1 2"

    def test_a_balanced_list_across_lines_is_joined(self):
        assert directive("WantedBy", 'WantedBy = [\n  "timers.target"\n];\n') == (
            '[ "timers.target" ]')


class TestDirectiveRefusesRatherThanTruncating:
    """🔴 A truncation is worse than a refusal here.

    A plausible-but-wrong value makes `_nix_wires_a_backup` answer False on a
    LIVE unit, and the guard then instructs a maintainer to publish "the unit
    has been RETIRED" into a shipped skill reference.
    """

    def test_an_indented_string_body_raises(self):
        blk = "ExecStart = pkgs.writeShellScript \"x\" ''\n  echo hi;\n'';\n"
        with pytest.raises(NotImplementedError):
            directive("ExecStart", blk)

    def test_an_indented_string_using_a_NIX_ESCAPE_still_raises(self):
        """🔴 `''${` is an ESCAPE, not a second delimiter.

        Counting `''` naively reads this as balanced and hands back the
        truncation — measured, with the real consequence above.
        """
        blk = (
            "ExecStart = pkgs.writeShellScript \"asib\" ''\n"
            "  export PATH=''${PATH}:/usr/bin;\n"
            "  exec python3 /scripts/asi/backup.py\n"
            "'';\n"
        )
        with pytest.raises(NotImplementedError):
            directive("ExecStart", blk)

    def test_a_dollar_escape_also_still_raises(self):
        blk = "ExecStart = pkgs.writeShellScript \"x\" ''\n  echo ''$HOME;\n'';\n"
        with pytest.raises(NotImplementedError):
            directive("ExecStart", blk)

    def test_an_attrset_value_raises(self):
        with pytest.raises(NotImplementedError):
            directive("Environment", "Environment = {\n  A = 1;\n  B = 2;\n};\n")

    def test_a_LIST_value_raises(self):
        """🔴 The `[`/`]` clause, which nothing exercised.

        Without it this returns the truncation `'[ "A=1"'` — a plausible WRONG
        value, which is the outcome `directive()`'s docstring calls the worst of
        the three. The attrset case above cannot reach this branch: it is caught
        by the `{`/`}` clause first.
        """
        with pytest.raises(NotImplementedError):
            directive("Environment", 'Environment = [\n  "A=1";\n  "B=2"\n];\n')

    def test_an_unterminated_declaration_raises(self):
        with pytest.raises(NotImplementedError):
            directive("ExecStart", 'ExecStart = "/a"\n')


class TestDirectiveDoesNotOverRefuseSingleLineValues:
    """🔴 The regression the balance check caused before it was scoped.

    An unbalanced bracket INSIDE a single-line string is not a truncation — the
    `;` that ended the line is the one that ends the declaration.
    """

    @pytest.mark.parametrize("value", [
        '"${pkgs.grep}/bin/grep \'^[\' f"',
        '"/bin/sh -c \'printf %s }\'"',
        '"/bin/sh -c \'a; b\'"',
    ])
    def test_single_line_values_are_returned_not_refused(self, value):
        assert directive("ExecStart", f"ExecStart = {value};\n") == value


# --- is_conditional ------------------------------------------------------------

class TestIsConditional:
    ONE_LINE = "  systemd.user.timers.t = {\n    x = 1;\n  };\n"
    LET_IN = (
        "  systemd.user.services.s =\n"
        "    let\n      pyEnv = 1;\n    in\n    {\n      x = 1;\n    };\n"
    )

    def test_an_unconditional_one_line_declaration(self):
        assert not is_conditional("systemd.user.timers.t", self.ONE_LINE)

    def test_a_gate_on_the_same_line(self):
        gated = self.ONE_LINE.replace("t = {", "t = lib.mkIf serverMode {")
        assert is_conditional("systemd.user.timers.t", gated)

    def test_an_unconditional_let_in_declaration(self):
        assert not is_conditional("systemd.user.services.s", self.LET_IN)

    def test_a_gate_on_the_line_AFTER_the_equals(self):
        """🔴 The shape the backup SERVICE uses, and the one that was invisible."""
        gated = self.LET_IN.replace(
            "s =\n", "s =\n    lib.mkIf serverMode (\n", 1)
        assert is_conditional("systemd.user.services.s", gated)

    def test_a_mkIf_INSIDE_the_body_is_not_a_gate_on_the_unit(self):
        """The span stops at the body's opening brace, so this must read False."""
        inner = self.ONE_LINE.replace("x = 1;", "x = lib.mkIf cond 1;")
        assert not is_conditional("systemd.user.timers.t", inner)


# --- unit_source ---------------------------------------------------------------

class TestUnitSource:
    SRC = (
        "  systemd.user.services.a = {\n    Aa = 1;\n  };\n"
        "  systemd.user.timers.b = {\n    Bb = 2;\n  };\n"
    )

    def test_bounded_at_the_next_top_level_unit(self):
        blk = unit_source("systemd.user.services.a", self.SRC)
        assert "Aa = 1;" in blk and "Bb = 2;" not in blk

    def test_the_last_unit_runs_to_the_end(self):
        assert "Bb = 2;" in unit_source("systemd.user.timers.b", self.SRC)

    def test_a_commented_only_declaration_raises(self):
        with pytest.raises(AssertionError):
            unit_source("systemd.user.services.ghost",
                        "  # systemd.user.services.ghost = {\n")
