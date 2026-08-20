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


def _live_load():
    with open("/proc/loadavg") as f:
        return float(f.read().split()[0])


def _run(*args):
    """Run the block as the bar invokes it, returning its parsed JSON."""
    r = subprocess.run([sys.executable, _BLOCK, *map(str, args)],
                       capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, "exit=%s stderr=%s" % (r.returncode, r.stderr)
    return json.loads(r.stdout)


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

    # Thresholds are derived by SUBTRACTION, not division: at load 0.0 — a
    # genuinely idle builder — `load / 2` is 0.0, which lands exactly ON the
    # boundary and makes the fractional case below invisible. Subtraction stays
    # strictly under the live load at every load, including zero (a negative
    # threshold is meaningless but harmless, and still exercises the branch).
    def test_a_warning_below_the_live_load_makes_the_block_visible(self):
        load = _live_load()
        out = _run("--warning", load - 1, "--critical", load + 1000)
        assert out["state"] == "Warning", out
        assert out["text"] not in ("", "?"), out
        float(out["text"])  # a rendered number, not a placeholder

    def test_a_critical_below_the_live_load_reaches_the_critical_state(self):
        load = _live_load()
        out = _run("--warning", load - 2, "--critical", load - 1)
        assert out["state"] == "Critical", out

    def test_a_warning_above_the_live_load_hides_the_block(self):
        """Invariant guard, NOT regression coverage — the pre-fix script passed
        this too, hiding the block for its own (wrong) reason."""
        out = _run("--warning", _live_load() + 1000,
                   "--critical", _live_load() + 2000)
        assert out == EMPTY, out

    def test_a_fractional_threshold_is_accepted(self):
        """Load averages are fractional, so a threshold that cannot be is a
        needless edge. The pre-fix script parsed with int() and blew up."""
        load = _live_load()
        out = _run("--warning", load - 0.5, "--critical", load + 1000)
        assert out["text"] != "?", out
        assert out["state"] == "Warning", out


class TestThresholdsThatWouldSILENTLYDisableTheBlock:
    """🔴 The `?`-pill promise is that a bad threshold is LOUD. Two values slip
    through float() and then lose every comparison, hiding the pill forever
    with no error — the exact silent-blank state the block exists to avoid."""

    @pytest.mark.parametrize("bad", ["nan", "inf", "-inf"])
    def test_non_finite_thresholds_are_rejected_loudly(self, bad):
        out = _run("--warning", bad, "--critical", "999")
        assert out["text"] == "?", "%s silently hid the block: %s" % (bad, out)

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

    def test_defaults_fall_back_to_the_core_count(self):
        """No flags -> warning at cores, critical at 2x. Only a sane default;
        nix/graphical.nix always passes both explicitly."""
        cores = os.cpu_count() or 1
        load = _live_load()
        out = _run()
        expected = ("Critical" if load >= cores * 2
                    else "Warning" if load >= cores else "Idle")
        assert out["state"] == expected, (
            "load=%s cores=%s -> expected %s, got %s" % (load, cores, expected, out))
