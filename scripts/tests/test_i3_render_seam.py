"""🔴 POSITIVE CONTROL for `testlib.i3_render` — the evaluator TWO guards read through.

`test_i3_game_mode.py` and `test_i3_picker_centering.py` both assert properties of
the i3 config *as rendered for a specific host*, because `nix/i3/config.nix` is a
function of `{ isLaptop }` and a rule parked inside a host-conditional fragment is
silently absent on the other machine.

That makes the renderer an INSTRUMENT, and an instrument wired to nothing reports
a reassuring result: a `render` that returned the raw body, or the same string for
both hosts, would make every "present on both hosts" assertion in both files true
by construction. So this file owns the control — the two renders must DIFFER, each
must carry the fragment that belongs only to it, and the shared body must survive
the split.

It lives in its own file rather than inside either consumer because the renderer
is now shared: a control parked in one consumer would disappear with it, leaving
the other reading through an unvouched instrument.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
from testlib.i3_render import HOSTS, fragments, render  # noqa: E402


def test_the_renderer_really_renders_two_different_configs():
    laptop, workbench = render(True), render(False)
    assert laptop != workbench
    assert "brightnessctl" in laptop and "brightnessctl" not in workbench
    assert "xrandr --output DP-0" in workbench
    assert "xrandr --output DP-0" not in laptop
    # …and the shared body is in both, so the split did not eat it.
    for cfg in (laptop, workbench):
        assert "set $mod Mod1" in cfg
        assert "default_border pixel 2" in cfg


def test_the_two_host_names_line_up_with_the_isLaptop_argument():
    """`HOSTS` is used as pytest `ids=` beside `[False, True]` in both consumers.

    If the order ever flipped, every parametrized failure would name the WRONG
    machine — the least useful kind of red. Pin it against a fragment whose host
    is unambiguous.
    """
    assert HOSTS == ("workbench", "laptop")
    by_host = {HOSTS[0]: render(False), HOSTS[1]: render(True)}
    assert "brightnessctl" in by_host["laptop"]
    assert "brightnessctl" not in by_host["workbench"]


def test_an_unresolvable_interpolation_is_REFUSED_not_silently_left_in():
    """🔴 NEGATIVE CONTROL — can the instrument go red at all?

    The whole value of `render` is that it never hands back text carrying an
    unsubstituted `${…}`; such text is not what any host receives, and every
    assertion over it would be measuring a fiction. Feed the parser a body that
    names a fragment it cannot resolve and watch it refuse.
    """
    with pytest.raises(AssertionError):
        fragments("  weird = if isLaptop then someIdentifier else '''';")


def test_the_fragment_parser_finds_the_host_conditional_fragments():
    """A `fragments` that returned `{}` would make `render` raise on every body —
    loud, but it would also make the negative control above pass for the wrong
    reason. So pin that it finds the real ones."""
    head = (REPO / "nix" / "i3" / "config.nix").read_text().split("\nin\n''\n")[0]
    found = fragments(head)
    assert len(found) >= 3, sorted(found)
    for name, (laptop_val, workbench_val) in found.items():
        assert laptop_val != workbench_val, name
