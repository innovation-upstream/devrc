"""Unit tests for scripts/i3status-load — the 1-minute load average pill.

The block reads /proc/loadavg directly (no cache, no poller), so `render` is
pure and testable without touching the filesystem. The subprocess tests drive
the real script so the env-var plumbing is exercised end to end — that is the
only layer where the `LOAD_WARNING` regression below is visible.
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

ICON = "utilities-system-monitor"
EMPTY = {"text": "", "state": "Idle"}


def _load(relpath, modname):
    loader = importlib.machinery.SourceFileLoader(
        modname, os.path.join(_SCRIPTS, relpath))
    spec = importlib.util.spec_from_loader(modname, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


blk = _load("i3status-load", "i3status_load")


def _run(env_extra=None):
    """Run the block as a subprocess, returning its parsed JSON."""
    env = dict(os.environ)
    env.pop("LOAD_WARNING", None)
    env.pop("LOAD_CRITICAL", None)
    env.update(env_extra or {})
    r = subprocess.run([sys.executable, _BLOCK],
                       capture_output=True, text=True, timeout=10, env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


class TestRender:
    """Pure render() tests — no filesystem, no /proc."""

    def test_below_warning_is_invisible(self):
        assert blk.render(2.0, warning=8, critical=16) == EMPTY

    def test_exact_warning_is_warning(self):
        out = blk.render(8.0, warning=8, critical=16)
        assert out["state"] == "Warning"
        assert out["text"] == "8.0"
        assert out["icon"] == ICON

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
        assert out["icon"] == ICON

    def test_fractional_load_renders_one_decimal(self):
        assert blk.render(3.7, warning=8, critical=16) == EMPTY
        out = blk.render(9.34, warning=8, critical=16)
        assert out["text"] == "9.3"
        assert out["state"] == "Warning"

    def test_short_text_always_matches_text(self):
        """i3status-rust falls back to short_text when the bar is crowded; a
        block whose short form disagreed would flicker between two values."""
        for load in (9.0, 20.0):
            out = blk.render(load, warning=8, critical=16)
            assert out["short_text"] == out["text"]

    @pytest.mark.parametrize("load", [0.0, 7.99])
    def test_invisible_block_carries_no_icon(self, load):
        """A hidden block must be byte-identical to the house _EMPTY shape —
        an `icon` key on an empty-text block still reserves bar width."""
        assert blk.render(load, warning=8, critical=16) == EMPTY


class TestWarningIsTheSoleVisibilityThreshold:
    """🔴 REGRESSION. The first draft gated visibility on the CORE COUNT before
    consulting `warning`, so lowering LOAD_WARNING below the core count did
    nothing at all — the knob was inert in the only direction anyone turns it.

    These run the real script, because the bug lived in the seam between the
    env-var plumbing and render(), not inside either one.
    """

    # 🔴 INTEGER thresholds on purpose. A fractional one ("0.01") ALSO fails
    # against the pre-fix script — but for the WRONG reason: that draft parsed
    # with int(), so it raised and rendered the `?` pill instead of the empty
    # block the precedence bug actually produces. An integer isolates the
    # mutation to the one behaviour under test. Fractional parsing has its own
    # test below.
    def test_a_low_warning_makes_the_block_visible_at_ordinary_load(self):
        """The regression: with a warning threshold far below the core count,
        the block MUST show the value. Pre-fix this returned the EMPTY block on
        any machine whose load was under its core count — verified red against
        that script at load 10.45 on a 24-core host."""
        out = _run({"LOAD_WARNING": "1", "LOAD_CRITICAL": "999999"})
        assert out["state"] == "Warning", out
        assert out["text"] not in ("", "?"), out
        float(out["text"])  # a rendered number, not a placeholder

    def test_a_low_critical_reaches_the_critical_state(self):
        out = _run({"LOAD_WARNING": "1", "LOAD_CRITICAL": "2"})
        assert out["state"] == "Critical", out

    def test_a_high_warning_hides_the_block(self):
        """Invariant guard, NOT regression coverage — the pre-fix script passed
        this too (its core-count gate hid the block for the wrong reason)."""
        out = _run({"LOAD_WARNING": "999999", "LOAD_CRITICAL": "999999"})
        assert out == EMPTY, out

    def test_a_fractional_threshold_is_accepted(self):
        """Thresholds parse as float. The pre-fix script used int() and blew up
        on any fractional value — load averages are fractional, so a threshold
        that cannot be is a needless edge."""
        out = _run({"LOAD_WARNING": "0.5", "LOAD_CRITICAL": "999999"})
        assert out["text"] != "?", out
        assert out["state"] == "Warning", out


class TestSubprocess:
    """Integration: the script as the bar actually invokes it."""

    def test_renders_valid_json_with_default_thresholds(self):
        out = _run()
        assert "state" in out and "text" in out
        assert out["state"] in ("Idle", "Warning", "Critical")

    def test_a_malformed_threshold_surfaces_a_visible_question_mark(self):
        """A typo'd env var must not silently revert to the default and render
        as if it were configured — it goes to the loud `?` pill."""
        out = _run({"LOAD_WARNING": "not-a-number"})
        assert out["text"] == "?", out
        assert out["state"] == "Warning", out
