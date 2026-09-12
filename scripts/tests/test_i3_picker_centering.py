"""🔴 THE mention-open PICKER MUST OPEN CENTRED — AND NOTHING ELSE MAY MOVE.

The picker spawns as `alacritty --class float,mention-open`, so i3 sees a window
whose `class` is `float` and whose `instance` is `mention-open`. `nix/i3/config.nix`
carries `for_window [class="float"] floating enable`, which floats it and sets no
position — and i3's default placement for a new float pins it to the LEFT edge of
the screen. That is the defect: a modal the operator has just summoned appears in
the corner instead of under their eyes.

THE FIX HAS TWO HALVES AND BOTH ARE HAZARDS, which is why there are two guards:

1. THE PICKER IS CENTRED. A rule matching this window must issue `move position
   center`. Silent when absent — the picker still opens, still works, just in the
   wrong place — so nothing but a test notices a regression.

2. NO OTHER FLOAT IS. `class="float"` is shared by every float terminal this repo
   launches (a dozen bar-click detail windows in `nix/graphical.nix`, plus
   `media-menu` and `airvpn-menu`). Giving THAT rule a position would satisfy
   guard 1 perfectly while relocating a dozen windows the operator never asked to
   move. So guard 2 pins the whole normalised action set a generic float receives
   — an ALLOWLIST, not a blocklist of geometry verbs, because a blocklist is
   walkable by any spelling it failed to imagine (`move absolute position`,
   `resize set`, `move window to position`, a future one).

WHY NEITHER GUARD IS SPELLED. The picker's identity is DERIVED by importing
`scripts/mention-open.py` and splitting `PICKER_CLASS`, so renaming the instance
there without updating the i3 rule goes red rather than passing over a rule that
now matches nothing. The control windows are DERIVED too — scanned out of the
`--class` arguments this repo actually launches — so a new float launcher is
covered the day it is added, not the day somebody remembers this file.

AND BOTH RUN PER HOST. `nix/i3/config.nix` is a function of `{ isLaptop }`; a rule
parked inside a host-conditional fragment would be silently absent on the other
machine. Rendering is `testlib.i3_render`, positive-controlled in
`test_i3_render_seam.py`.

NOT TESTED HERE, deliberately: that the window lands centred on a live screen.
That is a property of i3 4.24 executing a directive it parsed, it needs a
graphical session, and observing it would mean raising a window on the operator's
desk. The directive's presence is what this repo owns; i3's honouring of it is not.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
from testlib.i3_render import HOSTS, render  # noqa: E402

_HANDLER = REPO / "scripts" / "mention-open.py"
_spec = importlib.util.spec_from_file_location("mention_open_for_centering", _HANDLER)
MO = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(MO)


# --------------------------------------------------------------------------- #
# A tiny i3 `for_window` engine.
#
# i3 criteria are PCRE matched UNANCHORED against the window's properties, and a
# `for_window` line fires when EVERY criterion matches. Several rules can fire on
# one window; i3 runs them in file order. Modelling that — rather than grepping
# for a substring — is what lets the guards below ask "what would happen to THIS
# window", which is the actual relationship, instead of "does this word appear".
# --------------------------------------------------------------------------- #
_FOR_WINDOW = re.compile(r'^\s*for_window\s+\[(.*?)\]\s*(.+?)\s*$')
_CRITERION = re.compile(r'(\w+)\s*=\s*"([^"]*)"')

#: Window properties this engine models. A criterion naming anything else is
#: REFUSED rather than ignored: silently dropping an unknown criterion would make
#: a rule look like it fires on windows it does not, and guard 2 would then report
#: a move nobody makes — or, worse, miss one.
_MODELLED = frozenset({"class", "instance", "title"})


def _rules(cfg: str):
    """[(criteria, command)] for every `for_window` in the RENDERED config."""
    out = []
    for raw in cfg.splitlines():
        if raw.strip().startswith("#"):
            continue
        m = _FOR_WINDOW.match(raw)
        if not m:
            continue
        crit_text, command = m.group(1), m.group(2)
        crits = dict(_CRITERION.findall(crit_text))
        leftover = _CRITERION.sub("", crit_text).strip()
        assert not leftover, (
            "a `for_window` criterion in nix/i3/config.nix is not the "
            "`key=\"value\"` shape this engine parses (%r left over from %r) — "
            "extend the parser rather than letting the rule read as matching "
            "nothing" % (leftover, raw.strip()))
        unknown = sorted(set(crits) - _MODELLED)
        assert not unknown, (
            "`for_window` rule %r uses criteria this engine does not model: %s. "
            "Add them to `_MODELLED` and give the synthetic windows below that "
            "property — an unmodelled criterion silently changes which rules "
            "fire, in BOTH directions." % (raw.strip(), unknown))
        out.append((crits, command))
    return out


def _actions(cfg: str, window: dict) -> set:
    """The normalised set of i3 actions a window with these properties receives.

    A command is an i3 command CHAIN (`a, b` / `a; b`), so it is split into its
    individual actions and whitespace-collapsed. A set, not a list: the guards
    care about WHICH actions reach the window, never how many rules produced them.
    """
    got = set()
    for crits, command in _rules(cfg):
        if all(re.search(pat, window.get(key, "")) for key, pat in crits.items()):
            for action in re.split(r"[,;]", command):
                action = " ".join(action.split())
                if action:
                    got.add(action)
    return got


def _picker_window() -> dict:
    """The mention-open picker's window identity, DERIVED from the handler."""
    general, sep, instance = MO.PICKER_CLASS.partition(",")
    assert sep and instance, (
        "scripts/mention-open.py's PICKER_CLASS is %r — it carries no INSTANCE "
        "half, so the i3 rule that centres the picker (`instance=\"…\"`) can no "
        "longer name this window specifically, and the only rule left matching "
        "it is the shared `class=\"float\"` one that centres nothing."
        % (MO.PICKER_CLASS,))
    # `title` is whatever alacritty happens to set; nothing may key on it, and a
    # rule that did would go red here — the conservative direction.
    return {"class": general, "instance": instance, "title": "Alacritty"}


# --------------------------------------------------------------------------- #
# The OTHER floats — derived, never spelled.
# --------------------------------------------------------------------------- #
#: `--class <spec>` as written on an alacritty command line, in nix or in python
#: (`"--class", "float,float"`). A spec that is a bare ALL-CAPS identifier is a
#: variable reference, not a literal — `mention-open.py` passes `PICKER_CLASS`
#: that way — and is skipped, because its value is read by import instead.
_CLASS_ARG = re.compile(r'--class["\s,]+([A-Za-z0-9_.-]+(?:,[A-Za-z0-9_.-]+)?)')


def _launched_class_specs() -> set:
    """Every `--class` spec this repo launches an alacritty float with."""
    sources = list((REPO / "nix").rglob("*.nix"))
    sources += [p for p in (REPO / "scripts").glob("*") if p.is_file()]
    found = set()
    for path in sources:
        try:
            text = path.read_text(errors="replace")
        except OSError:                                   # pragma: no cover
            continue
        for spec in _CLASS_ARG.findall(text):
            if re.fullmatch(r"[A-Z_]+", spec):            # a variable, not a value
                continue
            found.add(spec)
    return found


def _control_windows() -> dict:
    """{spec: window} for every launched float that is NOT the picker."""
    out = {}
    for spec in sorted(_launched_class_specs() - {MO.PICKER_CLASS}):
        general, _, instance = spec.partition(",")
        out[spec] = {"class": general, "instance": instance or general,
                     "title": "Alacritty"}
    return out


# --------------------------------------------------------------------------- #
# GUARD 1 — the picker is centred, on both hosts.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("is_laptop", [False, True], ids=HOSTS)
def test_the_picker_is_CENTRED_on_both_hosts(is_laptop):
    """The defect this exists for: the picker opened pinned to the LEFT edge.

    Asserted as "what happens to a window with the picker's ACTUAL properties",
    with those properties read out of `PICKER_CLASS`. A rule whose `instance=`
    no longer matches what the handler launches is exactly as broken as a deleted
    rule, and reads identically in the source text.
    """
    window = _picker_window()
    got = _actions(render(is_laptop), window)
    assert "move position center" in got, (
        "on the %s render of nix/i3/config.nix, a window with the picker's own "
        "properties (%s — from PICKER_CLASS=%r) receives %s. Nothing centres it, "
        "so the picker opens wherever i3 places a new float: pinned to the LEFT "
        "edge of the screen."
        % (HOSTS[1] if is_laptop else HOSTS[0], window, MO.PICKER_CLASS,
           sorted(got) or "NO for_window rule at all"))


@pytest.mark.parametrize("is_laptop", [False, True], ids=HOSTS)
def test_the_picker_is_FLOATED_by_a_rule_that_still_matches_it(is_laptop):
    """`move position center` on a TILED window is a silent no-op.

    Centring is only meaningful once the window is floating, and the two facts
    live in different rules today — so pin the pair rather than assuming the
    shared rule will always be there, and always be reached first.
    """
    got = _actions(render(is_laptop), _picker_window())
    assert "floating enable" in got, (
        "nothing floats the picker on the %s render — `move position center` is "
        "a no-op on a tiled window, so the centring above would pass while the "
        "picker tiled. Actions received: %s"
        % (HOSTS[1] if is_laptop else HOSTS[0], sorted(got)))


# --------------------------------------------------------------------------- #
# GUARD 2 — no OTHER float is moved.
# --------------------------------------------------------------------------- #
#: The complete set of i3 actions a generic `class="float"` terminal may receive.
#: An ALLOWLIST: any addition, of any spelling, goes red here and must be a
#: deliberate decision about a dozen windows rather than a side effect of fixing
#: one. Widening it means the operator has asked for every float to change.
_ALLOWED_FOR_A_GENERIC_FLOAT = {"floating enable"}


@pytest.mark.parametrize("is_laptop", [False, True], ids=HOSTS)
def test_no_OTHER_float_this_repo_launches_is_moved_or_resized(is_laptop):
    """🔴 THE ONE THAT MATTERS. The cheap way to centre the picker is to hang
    `move position center` off the shared `for_window [class="float"]` rule —
    which passes guard 1 and relocates every bar-click detail window, the media
    detail terminal, the AirVPN detail terminal and the tmux scratch picker.

    The control windows are SCANNED out of the repo's own `--class` arguments, so
    this covers float launchers added after this file was written.
    """
    cfg = render(is_laptop)
    controls = _control_windows()
    assert controls, (
        "no non-picker `--class` spec was found in nix/**.nix or scripts/* — "
        "the scanner is wired to nothing and this guard is vacuous")
    offenders = {}
    for spec, window in controls.items():
        extra = _actions(cfg, window) - _ALLOWED_FOR_A_GENERIC_FLOAT
        if extra:
            offenders[spec] = sorted(extra)
    assert not offenders, (
        "on the %s render, windows that are NOT the mention-open picker receive "
        "actions beyond %s:\n%s\nThese are shared float terminals (bar-click "
        "detail windows, media-menu, airvpn-menu). If the picker was centred by "
        "widening the shared `class=\"float\"` rule, narrow it to the picker's "
        "`instance=` instead. If every float really is meant to move, say so by "
        "widening `_ALLOWED_FOR_A_GENERIC_FLOAT` in this file."
        % (HOSTS[1] if is_laptop else HOSTS[0],
           sorted(_ALLOWED_FOR_A_GENERIC_FLOAT),
           "\n".join("  %s -> %s" % (s, a) for s, a in sorted(offenders.items()))))


# --------------------------------------------------------------------------- #
# CONTROLS for the engine both guards read through.
# --------------------------------------------------------------------------- #
def test_the_rule_engine_can_SEE_a_centering_rule_and_a_title_criterion():
    """🔴 POSITIVE CONTROL. Guard 1 is an "is this action present" check and
    guard 2 an "is this action absent" one — a `_actions` that returned the empty
    set would make guard 2 pass forever and guard 1 fail loudly, so only guard 2's
    direction is at risk, and it is the one that matters.

    The subject is deliberately NOT the picker: the rig-control popup has carried
    `floating enable, move position center` since long before this feature, and
    it is matched on `title=`, so finding it proves the engine resolves chained
    commands AND multi-criterion rules.
    """
    yad = {"class": "Yad", "instance": "yad", "title": "Rig Controls"}
    got = _actions(render(False), yad)
    assert "move position center" in got, got
    assert "floating enable" in got, got
    # …and the title criterion is really being honoured, not skipped
    other_title = dict(yad, title="Something Else")
    assert "move position center" not in _actions(render(False), other_title)


def test_the_rule_engine_finds_the_rules_that_are_actually_there():
    """A `_rules` returning nothing would make guard 2 vacuous. Pin that it finds
    a plausible number, and that at least one is multi-criterion."""
    rules = _rules(render(False))
    assert len(rules) >= 3, rules
    assert any(len(crits) >= 2 for crits, _ in rules), rules
    assert all(cmd.strip() for _, cmd in rules), rules


def test_an_unmodelled_criterion_is_REFUSED():
    """🔴 NEGATIVE CONTROL for the refusal above — it must actually be able to
    fire, or an unknown criterion would be silently dropped and both guards would
    be measuring a rule set no i3 has."""
    with pytest.raises(AssertionError, match="does not model"):
        _rules('for_window [window_role="pop-up"] floating enable')
    with pytest.raises(AssertionError, match="shape this engine parses"):
        _rules('for_window [class="float" con_mark] floating enable')


def test_the_class_scanner_finds_the_repos_real_float_launchers():
    """POSITIVE CONTROL for the control-window scanner: it must find the picker's
    own spec (proving it reads the shapes both nix and python use) and at least
    one distinct other."""
    specs = _launched_class_specs()
    assert MO.PICKER_CLASS in specs, sorted(specs)
    assert len(specs - {MO.PICKER_CLASS}) >= 1, sorted(specs)
    assert not any(re.fullmatch(r"[A-Z_]+", s) for s in specs), sorted(specs)


def test_the_picker_identity_really_comes_from_the_handler():
    """Guard 1 is only structural if the instance it looks for is the one the
    handler launches. Pin the derivation, not the value."""
    window = _picker_window()
    assert MO.PICKER_CLASS == "%s,%s" % (window["class"], window["instance"])
