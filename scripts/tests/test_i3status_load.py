"""Unit tests for scripts/i3status-load — the 1-minute load average pill.

`render` is pure and tested directly. The subprocess tests drive the real
script, because two of the bugs this file guards lived in the seam between
argument parsing and render(), not inside either one.

🔴 The subprocess tests derive their thresholds from the host's OWN live load
average rather than hardcoding numbers. An earlier draft used fixed thresholds
(warning=1, critical=2), which quietly required the host to be busy: on an idle
machine at load 0.3 all three went red with no defect present. A test whose
verdict depends on how busy the builder is will eventually lie in both
directions, so the thresholds are computed relative to whatever the load is.
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.join(_HERE, "..")
_BLOCK = os.path.join(_SCRIPTS, "i3status-load")

ICON = "cogs"
EMPTY = {"text": "", "state": "Idle"}


def _load(relpath, modname):
    loader = importlib.machinery.SourceFileLoader(
        modname, os.path.join(_SCRIPTS, relpath))
    spec = importlib.util.spec_from_loader(modname, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


blk = _load("i3status-load", "i3status_load")


def _run(*args):
    """Run the block as the bar invokes it, returning its parsed JSON."""
    r = subprocess.run([sys.executable, _BLOCK, *map(str, args)],
                       capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, "exit=%s stderr=%s" % (r.returncode, r.stderr)
    return json.loads(r.stdout)


@pytest.fixture
def at_load(tmp_path):
    """Run the block against a FIXED load average.

    🔴 The deterministic seam. Every end-to-end assertion here used to compare
    the host's live load against a threshold, which quietly made the verdict
    depend on how busy the builder was: one test compared ambient load to the
    core count with no margin at all, on a box whose documented idle sits
    exactly at that boundary. The 1-minute average also moves mid-test — a
    0.70 jump inside the read window was measured — so even a margin is a bet.
    """
    def run(load, *args):
        p = tmp_path / "loadavg"
        p.write_text("%s 0.00 0.00 1/1 1\n" % load)
        return _run(*args, "--loadavg-path", str(p))
    return run


class TestRender:
    """Pure render() tests — no filesystem, no /proc."""

    def test_below_warning_is_invisible(self):
        assert blk.render(2.0, warning=8, critical=16) == EMPTY

    def test_exact_warning_is_warning(self):
        out = blk.render(8.0, warning=8, critical=16)
        assert out["state"] == "Warning"
        assert out["text"] == "8.0"

    def test_between_thresholds_is_warning(self):
        out = blk.render(12.0, warning=8, critical=16)
        assert out["state"] == "Warning"
        assert out["text"] == "12.0"

    def test_exact_critical_is_critical(self):
        out = blk.render(16.0, warning=8, critical=16)
        assert out["state"] == "Critical"
        assert out["text"] == "16.0"

    def test_far_above_critical_is_critical(self):
        out = blk.render(50.0, warning=8, critical=16)
        assert out["state"] == "Critical"
        assert out["text"] == "50.0"

    def test_unreadable_load_is_a_visible_question_mark(self):
        out = blk.render(None, warning=8, critical=16)
        assert out["state"] == "Warning"
        assert out["text"] == "?"

    def test_fractional_load_renders_one_decimal(self):
        assert blk.render(3.7, warning=8, critical=16) == EMPTY
        out = blk.render(9.34, warning=8, critical=16)
        assert out["text"] == "9.3"

    def test_short_text_always_matches_text(self):
        """i3status-rust falls back to short_text when the bar is crowded; a
        block whose short form disagreed would flicker between two values."""
        for load in (9.0, 20.0):
            out = blk.render(load, warning=8, critical=16)
            assert out["short_text"] == out["text"]

    @pytest.mark.parametrize("load", [0.0, 7.99])
    def test_invisible_block_carries_no_icon(self, load):
        """A hidden block must be byte-identical to the house _EMPTY shape — an
        `icon` key on an empty-text block still reserves bar width."""
        assert blk.render(load, warning=8, critical=16) == EMPTY

    def test_the_icon_is_the_upstream_load_icon(self):
        """🔴 Pinned to a LITERAL, not to `blk.ICON`. Asserting a constant
        against itself is what let `utilities-system-monitor` — which is not an
        i3status-rust icon key at all, and renders as a red `Failed to render
        full text` — pass a green suite. `cogs` is what upstream's own `load`
        block uses; `test_bar_status.py` checks it against the real icon set."""
        assert blk.ICON == "cogs"
        assert blk.render(9.0, warning=8, critical=16)["icon"] == "cogs"


class TestWarningIsTheSoleVisibilityThreshold:
    """🔴 REGRESSION. The first draft gated visibility on the CORE COUNT before
    consulting `warning`, so lowering the threshold below the core count did
    nothing — the knob was inert in the only direction anyone turns it.

    Verified red against that script with `{'text': '', 'state': 'Idle'}` at
    load 10.45 on a 24-core host. Integer thresholds were used for that check
    deliberately: a fractional one also failed pre-change, but for the WRONG
    reason (that draft parsed with int(), so it raised and rendered the `?`
    pill rather than the empty block the precedence bug actually produces).
    """

    # Loads are FIXED via the fixture, so these say the same thing on an idle
    # laptop and a saturated builder. 4.0 is below every host's core count,
    # which is the whole point: pre-fix, a warning of 1.0 at load 4.0 rendered
    # the empty block because 4.0 < cores won first.
    def test_a_warning_below_the_load_makes_the_block_visible(self, at_load):
        out = at_load(4.0, "--warning", 1.0, "--critical", 1000)
        assert out["state"] == "Warning", out
        assert out["text"] == "4.0", out

    def test_a_critical_below_the_load_reaches_the_critical_state(self, at_load):
        out = at_load(4.0, "--warning", 1.0, "--critical", 2.0)
        assert out["state"] == "Critical", out

    def test_a_warning_above_the_load_hides_the_block(self, at_load):
        """Invariant guard, NOT regression coverage — the pre-fix script passed
        this too, hiding the block for its own (wrong) reason."""
        assert at_load(4.0, "--warning", 1000, "--critical", 2000) == EMPTY

    def test_a_fractional_threshold_is_accepted(self, at_load):
        """Load averages are fractional, so a threshold that cannot be is a
        needless edge. The pre-fix script parsed with int() and blew up."""
        out = at_load(4.0, "--warning", 0.5, "--critical", 1000)
        assert out["text"] == "4.0", out
        assert out["state"] == "Warning", out

    def test_the_boundary_is_inclusive_at_both_thresholds(self, at_load):
        assert at_load(8.0, "--warning", 8, "--critical", 99)["state"] == "Warning"
        assert at_load(8.0, "--warning", 4, "--critical", 8)["state"] == "Critical"
        assert at_load(7.99, "--warning", 8, "--critical", 99) == EMPTY

    def test_critical_equal_to_warning_is_allowed_and_skips_the_warning_band(
            self, at_load):
        """`==` is accepted (only `<` is incoherent), and the consequence is
        worth pinning: the block then has no Warning band at all."""
        assert at_load(8.0, "--warning", 8, "--critical", 8)["state"] == "Critical"
        assert at_load(7.9, "--warning", 8, "--critical", 8) == EMPTY


class TestThresholdsThatWouldSILENTLYDisableTheBlock:
    """🔴 The `?`-pill promise is that a bad threshold is LOUD. Two values slip
    through float() and then lose every comparison, hiding the pill forever
    with no error — the exact silent-blank state the block exists to avoid."""

    # `-inf` is NOT in this list: argparse rejects it before check_thresholds
    # ever sees it (a leading `-` that is not a number reads as a flag), so it
    # would pass this test without exercising the isfinite branch at all. It is
    # covered below as an argparse-rejection case instead — the same `?` pill,
    # a different mechanism, and naming the mechanism is the point.
    @pytest.mark.parametrize("bad", ["nan", "inf"])
    def test_non_finite_thresholds_are_rejected_loudly(self, bad):
        out = _run("--warning", bad, "--critical", "999")
        assert out["text"] == "?", "%s silently hid the block: %s" % (bad, out)

    @pytest.mark.parametrize("bad", ["-inf", "--bogus", ""])
    def test_argparse_rejections_also_reach_the_question_mark(self, bad):
        out = _run("--warning", bad, "--critical", "999")
        assert out["text"] == "?", "%r left the bar without a block: %s" % (bad, out)

    @pytest.mark.parametrize("bad", ["nan", "inf"])
    def test_non_finite_critical_is_rejected_loudly(self, bad):
        out = _run("--warning", "1", "--critical", bad)
        assert out["text"] == "?", "%s silently hid the block: %s" % (bad, out)

    def test_critical_below_warning_is_rejected(self):
        """The critical branch is evaluated first, so an inverted pair paints a
        load the docstring calls hidden bright red."""
        out = _run("--warning", "10", "--critical", "2")
        assert out["text"] == "?", out

    def test_a_malformed_threshold_surfaces_the_question_mark(self):
        """argparse exits 2 on a bad value; unconverted, that leaves the bar
        with no block at all rather than a visible error."""
        out = _run("--warning", "not-a-number")
        assert out["text"] == "?", out
        assert out["state"] == "Warning", out


class TestSubprocess:
    """Integration: the script as the bar actually invokes it."""

    def test_renders_valid_json_with_default_thresholds(self):
        out = _run()
        assert out["state"] in ("Idle", "Warning", "Critical")
        assert "text" in out

    def test_defaults_fall_back_to_the_core_count(self, at_load):
        """No flags -> warning at cores, critical at 2x. Only a sane default;
        nix/graphical.nix always passes both explicitly.

        Probed at fixed loads either side of the core count rather than against
        ambient load: comparing the host's live load to its own core count is a
        zero-margin test on exactly the value the workbench idles at, and the
        1-minute average moves between the test's read and the script's.
        """
        cores = float(os.cpu_count() or 1)
        assert at_load(cores - 0.1) == EMPTY
        assert at_load(cores)["state"] == "Warning"
        assert at_load(cores * 2)["state"] == "Critical"

    def test_one_flag_alone_leaves_the_other_at_its_default(self, at_load):
        """Documented consequence: the fallbacks are INDEPENDENT, so passing a
        --warning above the default --critical is an inverted pair and lands on
        the `?` pill. nix always passes both; this pins why it must."""
        cores = float(os.cpu_count() or 1)
        assert at_load(4.0, "--warning", cores * 2 + 1)["text"] == "?"
