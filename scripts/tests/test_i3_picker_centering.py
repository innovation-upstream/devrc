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

AND THE REVIEW TUI — the other window `mention-open.py` launches — MUST FIT THE
SCREEN, which is guards 3 and 4. It opened at 200x50 CHARACTER CELLS and overflowed
the display, unusably: a cell's pixel size is a function of font size and DPI, so
one cell count cannot satisfy a 3440x1413 workspace at ~96 DPI and a 2256x1480 one
at ~201 DPI. i3 sizes it in `ppt` — percent of the workspace — which is correct on
both hosts by construction, and the cell counts survive only as a pre-map hint. The
hazards mirror the picker's: a resize expressed in PIXELS reads almost identically
in the config, a `resize set` on a still-TILED window is a silent no-op, and hanging
the resize off the shared `class="float"` rule would resize a dozen windows plus the
picker while passing every positive guard.

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


def _chain(command: str) -> list:
    """The individual, whitespace-collapsed actions of one i3 command CHAIN.

    `a, b` / `a; b` is one `for_window` command running two actions. ONE writer
    for this split: the guards below ask both "which actions reach this window"
    (across every matching rule) and "which actions share ONE rule" (the
    floating-enable pairing), and two copies of the splitting could disagree
    about where an action starts.
    """
    return [" ".join(a.split()) for a in re.split(r"[,;]", command)
            if " ".join(a.split())]


def _matching_rules(cfg: str, window: dict) -> list:
    """[(criteria, command)] for every rule that FIRES on this window, in file
    order. i3 criteria are PCRE, matched unanchored, and every criterion in a
    rule must match for the rule to fire."""
    return [(crits, command) for crits, command in _rules(cfg)
            if all(re.search(pat, window.get(key, ""))
                   for key, pat in crits.items())]


def _actions(cfg: str, window: dict) -> set:
    """The normalised set of i3 actions a window with these properties receives.

    A command is an i3 command CHAIN (`a, b` / `a; b`), so it is split into its
    individual actions and whitespace-collapsed. A set, not a list: the guards
    care about WHICH actions reach the window, never how many rules produced them.
    """
    got = set()
    for _crits, command in _matching_rules(cfg, window):
        got.update(_chain(command))
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


def _review_window() -> dict:
    """The review TUI's window identity, DERIVED from the handler.

    Same derivation as the picker's and for the same reason: the i3 rule that
    sizes this window names it by `instance=`, so renaming `REVIEW_CLASS` without
    touching the rule leaves a rule matching NOTHING — which reads identically to
    a correct one in the config text, and shows up only as a window that is back
    to overflowing the screen.
    """
    general, sep, instance = MO.REVIEW_CLASS.partition(",")
    assert sep and instance, (
        "scripts/mention-open.py's REVIEW_CLASS is %r — it carries no INSTANCE "
        "half, so the i3 rule that sizes the review window "
        "(`instance=\"…\"`) can no longer name it specifically. The only rule "
        "left matching it would be the shared `class=\"float\"` one, which sizes "
        "nothing, and the window would fall back to its CHARACTER-CELL hint — "
        "the defect." % (MO.REVIEW_CLASS,))
    return {"class": general, "instance": instance, "title": "Alacritty"}


#: An i3 `resize set`, with the per-dimension UNIT captured. i3's grammar is
#: `resize set <width> [px|ppt] <height> [px|ppt]` and the unit DEFAULTS TO px
#: when omitted — which is the whole hazard this parser exists to see: `resize
#: set 90 90` is a legal 90x90 PIXEL window, and it is what dropping `ppt`
#: silently produces.
_RESIZE_SET = re.compile(
    r'^resize set (\d+)(?:\s+(px|ppt))? (\d+)(?:\s+(px|ppt))?$')


def _percent_resizes(actions) -> list:
    """[(width_pct, height_pct)] for every resize sized in PERCENT-OF-WORKSPACE.

    A resize whose units are px — or omitted, which MEANS px — is deliberately
    NOT returned: it is the defect, not a weaker form of the fix.
    """
    out = []
    for action in actions:
        m = _RESIZE_SET.match(action)
        if m and m.group(2) == "ppt" and m.group(4) == "ppt":
            out.append((int(m.group(1)), int(m.group(3))))
    return out


def _all_resizes(actions) -> list:
    """Every `resize`-ish action, whatever its units or spelling.

    Substring-matched ON PURPOSE, and only ever used to say "this window must
    receive NONE of these": for that direction a loose pattern is the safe one,
    because an unanticipated spelling (`resize grow`, `resize set 50 ppt`) is
    caught rather than waved through.
    """
    return sorted(a for a in actions if "resize" in a)


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


#: The floats this repo deliberately MOVES or RESIZES, excluded from the control
#: set below because guard 2 asks "which windows are moved that should not be".
#: Both are read from the handler, never spelled. They are skipped by the scanner
#: today anyway — `mention-open.py` passes them as ALL-CAPS variables — but the
#: moment either literal is written into a nix file the scanner would pick it up
#: and guard 2 would report the repo's own intended geometry as an offence.
_DELIBERATELY_PLACED = (MO.PICKER_CLASS, MO.REVIEW_CLASS)


def _control_windows() -> dict:
    """{spec: window} for every launched float that is NOT deliberately placed."""
    out = {}
    for spec in sorted(_launched_class_specs() - set(_DELIBERATELY_PLACED)):
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
# GUARD 3 — the REVIEW TUI is sized in PERCENT OF THE WORKSPACE.
#
# THE DEFECT: the review window opened at `REVIEW_COLUMNS`x`REVIEW_LINES` =
# 200x50 CHARACTER CELLS and overflowed the screen, unusably. A cell is not a
# length — its pixel size is a function of the font size and the display's DPI —
# so no single cell count can fit two displays. MEASURED: the workbench's usable
# workspace is 3440x1413 at ~96 DPI; the laptop's is 2256x1480 on a 285mm eDP-1
# panel, ~201 DPI. Both hosts resolve the SAME alacritty.toml out of the nix store
# and it sets no `[font]` size, so the laptop's cell is roughly twice the
# workbench's in each axis and the same constant asks for roughly four times the
# area. `ppt` is a percentage of the workspace i3 is placing the window on, so it
# is right on both hosts by construction — and on any display added later.
#
# The guards below pin the MECHANISM, not the number: that the window receives a
# resize whose UNITS are percent-of-workspace, that the rule issuing it also
# floats the window (a `resize set` on a TILED window is a silent no-op — the same
# hazard guard 1's companion pins for the picker), and that it is centred. The
# percentage itself is a taste call and may be tuned without going red.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("is_laptop", [False, True], ids=HOSTS)
def test_the_review_window_is_sized_in_PERCENT_OF_WORKSPACE_on_both_hosts(
        is_laptop):
    """🔴 THE REGRESSION GUARD for the reported defect.

    Asserted against a window with the review TUI's ACTUAL properties, read out
    of `REVIEW_CLASS`, and against the UNITS rather than the word: a rule that
    said `resize set 90 90` would read almost identically in the config and mean
    90 PIXELS, which is the defect in the other direction.
    """
    host = HOSTS[1] if is_laptop else HOSTS[0]
    window = _review_window()
    got = _actions(render(is_laptop), window)
    pcts = _percent_resizes(got)
    assert pcts, (
        "on the %s render of nix/i3/config.nix, a window with the review TUI's "
        "own properties (%s — from REVIEW_CLASS=%r) receives %s. Nothing sizes "
        "it as a PERCENT OF THE WORKSPACE (`resize set <w> ppt <h> ppt`), so its "
        "size is whatever `REVIEW_COLUMNS`x`REVIEW_LINES` CHARACTER CELLS happen "
        "to come to on this display — the defect: a cell's pixel size is "
        "DPI-dependent, the workbench is 3440x1413 at ~96 DPI and the laptop "
        "2256x1480 at ~201 DPI, and one cell count cannot fit both."
        % (host, window, MO.REVIEW_CLASS,
           sorted(got) or "NO for_window rule at all"))
    bad = [p for p in pcts if not all(0 < v <= 100 for v in p)]
    assert not bad, (
        "on the %s render the review window's percent-of-workspace resize is "
        "%s — a `ppt` value outside 1..100 is not a percentage of anything i3 "
        "can place it on." % (host, bad))


@pytest.mark.parametrize("is_laptop", [False, True], ids=HOSTS)
def test_the_rule_that_RESIZES_the_review_window_also_FLOATS_it(is_laptop):
    """🔴 `resize set` ON A TILED WINDOW IS A SILENT NO-OP.

    So the guard above could pass in full while the review window came up tiled
    and unresized. The pairing is asserted WITHIN ONE RULE rather than across the
    window's whole action set: today the shared `for_window [class="float"]` rule
    floats it first, but that is an ORDERING fact about a rule this one does not
    own, and narrowing or reordering that rule would turn the resize into a no-op
    with every other assertion here still green. Repeating `floating enable` in
    the sizing rule is idempotent, and is what makes it self-contained — the
    picker's own rule does the same, for the same reason.
    """
    host = HOSTS[1] if is_laptop else HOSTS[0]
    cfg = render(is_laptop)
    window = _review_window()
    sizing = [(crits, cmd) for crits, cmd in _matching_rules(cfg, window)
              if _percent_resizes(_chain(cmd))]
    assert sizing, (
        "on the %s render no rule matching the review window (%s) carries a "
        "percent-of-workspace resize at all — see the sizing guard above."
        % (host, window))
    unfloated = [cmd for _crits, cmd in sizing
                 if "floating enable" not in _chain(cmd)]
    assert not unfloated, (
        "on the %s render the rule that resizes the review window does NOT also "
        "float it: %s. `resize set` on a tiled window is a silent no-op, so this "
        "rule depends on another rule having floated the window first — make it "
        "self-contained by repeating `floating enable` in this chain."
        % (host, unfloated))


@pytest.mark.parametrize("is_laptop", [False, True], ids=HOSTS)
def test_the_sizing_rule_names_the_review_instance_EXACTLY(is_laptop):
    """🔴 FOUND BY MUTATION TESTING, and it is the hole every other guard here
    has: i3 criteria are PCRE matched UNANCHORED, so `instance="mention-review"`
    goes on matching a window renamed to `mention-review-v2`. Renaming
    `REVIEW_CLASS` therefore does NOT redden the guards above — they derive the
    window from the constant, the rule still fires on it by prefix, and everything
    stays green over a config and a handler that no longer agree.

    Two things are wrong with that state even though the window is still sized.
    The rule now over-matches: any future sibling instance sharing the prefix
    (`mention-review-diff`) silently inherits a geometry nobody chose for it. And
    the next rename — to something that does NOT share the prefix — is the one
    that leaves the rule inert, which is the defect; a half-working derivation is
    what lets the first rename land unnoticed so the second looks unrelated.

    So: the criterion must BE the instance, not merely match it. A deliberate
    anchoring (`^…$`) is accepted, an alternation or a prefix is not — the
    conservative direction, and a rule wanting to cover two windows can say so by
    failing here first.
    """
    host = HOSTS[1] if is_laptop else HOSTS[0]
    cfg = render(is_laptop)
    window = _review_window()
    sizing = [crits for crits, cmd in _matching_rules(cfg, window)
              if _percent_resizes(_chain(cmd))]
    assert sizing, (
        "on the %s render no rule matching the review window (%s) carries a "
        "percent-of-workspace resize at all — see the sizing guard above."
        % (host, window))
    loose = [c for c in sizing
             if c.get("instance", "").lstrip("^").rstrip("$")
             != window["instance"]]
    assert not loose, (
        "on the %s render the rule sizing the review window keys on "
        "instance=%r, which is not the instance scripts/mention-open.py actually "
        "launches (%r, from REVIEW_CLASS=%r). i3 matches criteria as UNANCHORED "
        "PCRE, so a PREFIX of the real instance still fires — the rule works "
        "today, over-matches any future sibling instance, and hides the rename "
        "that will eventually make it match nothing."
        % (host, [c.get("instance") for c in loose], window["instance"],
           MO.REVIEW_CLASS))


@pytest.mark.parametrize("is_laptop", [False, True], ids=HOSTS)
def test_the_review_window_is_CENTRED_on_both_hosts(is_laptop):
    """A 90%-of-workspace window placed at i3's default float position hangs off
    the edge it is pushed against — so the resize is only half the fix. Same
    assertion shape as guard 1's, against the review window's own identity."""
    host = HOSTS[1] if is_laptop else HOSTS[0]
    got = _actions(render(is_laptop), _review_window())
    assert "move position center" in got, (
        "on the %s render nothing centres the review window — it receives %s. "
        "A window sized to most of the workspace still needs placing, or i3's "
        "default float position pushes it off one edge." % (host, sorted(got)))


# --------------------------------------------------------------------------- #
# GUARD 4 — the resize reaches NOBODY ELSE.
#
# 🔴 INVARIANT GUARDS, NOT REGRESSION COVERAGE — both pass on the pre-fix tree,
# where no `resize set` exists anywhere, and they are labelled as such. What they
# exist for is the NEXT change: the cheap way to "simplify" this feature is to
# hang the resize off the shared `for_window [class="float"]` rule, which
# satisfies every guard above and resizes a dozen bar-click detail windows, the
# media and AirVPN detail terminals, AND the fzf picker — whose geometry lives in
# `PICKER_COLUMNS`/`PICKER_LINES` and is deliberately NOT restated in i3.
#
# Guard 2 covers the launched specs; these two cover the picker (excluded from
# that control set because it IS deliberately placed) and a synthetic float whose
# instance is simply something else, which is the case a scanner cannot enumerate.
# --------------------------------------------------------------------------- #
def _a_float_that_is_not_the_review() -> dict:
    """A synthetic `class="float"` window whose instance is neither the review's
    nor the picker's. Synthetic ON PURPOSE: the scanner can only see launchers
    that exist today, and the rule must be narrow for ones added tomorrow."""
    review, picker = _review_window(), _picker_window()
    instance = "some-other-float"
    for name, other in (("REVIEW_CLASS", review), ("PICKER_CLASS", picker)):
        assert not re.search(other["instance"], instance), (
            "this control window's instance %r is matched by %s's instance "
            "pattern %r, so it is not a control at all — rename it."
            % (instance, name, other["instance"]))
    return {"class": review["class"], "instance": instance,
            "title": "Alacritty"}


@pytest.mark.parametrize("is_laptop", [False, True], ids=HOSTS)
def test_a_DIFFERENT_float_instance_is_NOT_resized(is_laptop):
    """INVARIANT GUARD (green before the fix as well as after)."""
    host = HOSTS[1] if is_laptop else HOSTS[0]
    window = _a_float_that_is_not_the_review()
    extra = _actions(render(is_laptop), window) - _ALLOWED_FOR_A_GENERIC_FLOAT
    assert not extra, (
        "on the %s render a window that is `class=\"float\"` with some OTHER "
        "instance (%s) receives %s beyond %s. The review window's sizing rule "
        "must key on its `instance=`; hung off the shared `class=\"float\"` rule "
        "it resizes every float terminal in the system."
        % (host, window, sorted(extra), sorted(_ALLOWED_FOR_A_GENERIC_FLOAT)))


@pytest.mark.parametrize("is_laptop", [False, True], ids=HOSTS)
def test_the_PICKER_is_still_not_resized_by_anything(is_laptop):
    """INVARIANT GUARD (green before the fix as well as after).

    The picker's geometry is `PICKER_COLUMNS`/`PICKER_LINES` and i3 only centres
    it — nix/i3/config.nix says so explicitly, and an i3 `resize set` would
    restate that geometry in a second file that cannot see those constants.
    """
    host = HOSTS[1] if is_laptop else HOSTS[0]
    resizes = _all_resizes(_actions(render(is_laptop), _picker_window()))
    assert not resizes, (
        "on the %s render the mention-open picker receives %s. Its size lives in "
        "PICKER_COLUMNS/PICKER_LINES in scripts/mention-open.py and i3 must only "
        "CENTRE it — an i3 resize would silently pin the old geometry the next "
        "time those constants change." % (host, resizes))


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


def test_the_review_identity_really_comes_from_the_handler():
    """Same pin for guard 3: a spelled instance would leave the i3 rule inert and
    every review guard green after a rename in `mention-open.py`."""
    window = _review_window()
    assert MO.REVIEW_CLASS == "%s,%s" % (window["class"], window["instance"])
    # The two windows must be distinguishable by instance, or the review's rule
    # cannot avoid the picker and guard 4's picker arm is unsatisfiable.
    assert window["instance"] != _picker_window()["instance"]


def test_the_resize_parser_TELLS_PERCENT_FROM_PIXELS():
    """🔴 THE CONTROL THAT MAKES GUARD 3 MEAN ANYTHING. It is an "is a ppt resize
    present" check, so a `_percent_resizes` that accepted everything would pass it
    over a pixel resize — the exact defect — and one that accepted nothing would
    make it fail loudly (the safe direction). So: positive control that it SEES a
    ppt resize, and negative controls that it rejects every px spelling, including
    the one where the unit is OMITTED and therefore MEANS px.
    """
    assert _percent_resizes(["resize set 90 ppt 90 ppt"]) == [(90, 90)]
    assert _percent_resizes(["resize set 80 ppt 65 ppt"]) == [(80, 65)]
    # px, explicit and implicit — i3 defaults the unit to px
    assert _percent_resizes(["resize set 90 px 90 px"]) == []
    assert _percent_resizes(["resize set 90 90"]) == []
    # half-converted: one axis in cells-worth-of-pixels is still the defect
    assert _percent_resizes(["resize set 90 ppt 90 px"]) == []
    assert _percent_resizes(["resize set 90 ppt 90"]) == []
    # not a resize at all
    assert _percent_resizes(["floating enable", "move position center"]) == []
    # …and the loose any-resize scan used only for the "must receive none"
    # direction really does see the spellings the strict parser rejects
    assert _all_resizes(["resize set 90 90", "floating enable"]) == [
        "resize set 90 90"]
    assert _all_resizes(["resize grow width 10 ppt"]) == [
        "resize grow width 10 ppt"]
    assert _all_resizes(["floating enable"]) == []


def test_the_chain_splitter_and_the_matching_rule_finder_agree_with_actions():
    """`_actions` is now built out of `_matching_rules` + `_chain`, and guard 3's
    floating-enable pairing reads those two DIRECTLY. A disagreement between the
    two paths would let the pairing pass over a rule `_actions` never saw."""
    cfg = render(False)
    window = _picker_window()
    from_parts = set()
    for _crits, command in _matching_rules(cfg, window):
        from_parts.update(_chain(command))
    assert from_parts == _actions(cfg, window), (from_parts,
                                                 _actions(cfg, window))
    # the splitter handles both i3 chain separators and collapses whitespace
    assert _chain("floating enable,  resize set 90 ppt 90 ppt; move position "
                  "center") == ["floating enable", "resize set 90 ppt 90 ppt",
                                "move position center"]
    assert _chain("  ") == []
