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
the laptop's display, unusably. A cell's pixel size is a function of font size and
DPI, so one cell count cannot satisfy both hosts; i3 sizes it in `ppt` and
`mention-open.py` now passes NO `window.dimensions` at all, so i3 is the sole
authority and there is no second number to disagree with it.

🔴 `ppt` IS A PERCENTAGE OF THE **OUTPUT** RECT, NOT OF THE WORKSPACE. This file
said "workspace" everywhere, and every height DERIVED FROM A `ppt` PERCENTAGE was
short by the status bar. Measured at `451ead89`, this PR's previous head: `90 ppt`
read as 1332 px on the laptop (0.90 x 1480) and 1272 px on the workbench
(0.90 x 1413), and the 200x50 window's fit read as "64% x 78%" (1100 / 1413).

⚠ NOT "every height" — an earlier wording of this paragraph said that and it
OVER-states the defect. The transcribed rects (1413, 1480), the cell heights
(22.0, 37.0), the largest observed row counts (63, 39) and the 1100 px of a
50-row grid were all correct then and are correct now. Read the narrow version:
converting a FIT basis into the output rect is the SAME error in the other
direction, and this file forbids it explicitly elsewhere ("Do not carry one rect
over to the other").

Established from i3 4.25.1 source (the version on both hosts), two independent
artifacts: `src/commands.c`'s `cmd_resize_set()` multiplies by
`con_get_output(floating_con)->rect`, and `testcases/t/252-floating-size.t` checks
`resize set 33 ppt 20 ppt` against `int(0.33*1333)` x `int(0.2*999)` of a
`fake-outputs 1333x999`. i3's test outputs carry no bar, so workspace == output
there — which is why this cannot be caught by reading i3's suite alone. The
WORKSPACE rect is still the right basis for a FIT question, and it is what `move
position center` centres against (`con_get_workspace(…)->rect`), so one chain here
sizes against one rect and places against another.

🔴 AND `ppt` IS THE RIGHT UNIT, NOT A HOST-INDEPENDENT SIZE, WHICH IS WHY THE
PERCENTAGES ARE PER HOST. One shared `90 ppt 90 ppt` fits the laptop and makes the
workbench's window 41% WIDER than the size the operator called unusable — on the
host that never had the defect. So guard 3 pins the percentages PER HOST, and a
separate guard pins that the two hosts DISAGREE: collapsing them back to one number
is the regression this whole arrangement exists to prevent, and it would otherwise
pass every mechanism check here.

THE HAZARDS MIRROR THE PICKER'S: a resize expressed in PIXELS reads almost
identically in the config (i3 defaults the unit to px when omitted), a `resize set`
on a still-TILED window is a silent no-op, and hanging the resize off the shared
`class="float"` rule would resize a dozen windows plus the picker while passing
every positive guard.

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
    """[(width_pct, height_pct)] for every resize sized in PERCENT-OF-OUTPUT.

    `ppt` is a percentage of the OUTPUT rect, not the workspace's — i3 4.25.1
    `src/commands.c`, `cmd_resize_set()`. A resize whose units are px — or
    omitted, which MEANS px — is deliberately NOT returned: it is the defect, not
    a weaker form of the fix.
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
# GUARD 3 — the REVIEW TUI is sized in PERCENT OF THE OUTPUT RECT, PER HOST.
#
# THE DEFECT: the review window opened at 200x50 CHARACTER CELLS and overflowed
# the LAPTOP's screen, unusably. A cell is not a length — its pixel size is a
# function of the font size and the display's DPI — so no single cell count can
# fit two displays, and `mention-open.py` now passes no cell count at all.
#
# 🔴 `ppt` RESOLVES AGAINST THE **OUTPUT** RECT, NOT THE WORKSPACE'S. i3 4.25.1,
# `src/commands.c`, `cmd_resize_set()`:
#
#     const Con *output = con_get_output(floating_con);
#     … cwidth  = output->rect.width  * ((double)cwidth  / 100.0);
#     … cheight = output->rect.height * ((double)cheight / 100.0);
#
# corroborated by i3's own `testcases/t/252-floating-size.t`, which sets
# `fake-outputs 1333x999` and asserts `resize set 33 ppt 20 ppt` lands on
# `int(0.33*1333)` x `int(0.2*999)` — the OUTPUT. i3's test outputs carry no bar,
# so workspace == output there, which is why this cannot surface in i3's suite.
# Every height in this block that was DERIVED FROM A `ppt` PERCENTAGE was
# previously computed against the workspace and was short by the bar — not every
# height in it (the transcribed rects and cell sizes were fine). `78 ppt` read as
# sub-cell on the workbench when it is a whole ROW larger in the BARE RECT.
# ⚠ It is NOT a row larger once decoration is counted; see the decoration
# paragraph below and `test_the_WORKBENCH_keeps_rendering_…`'s docstring.
#
# ⚠ `move position center` USES THE **WORKSPACE** RECT (`cmd_move_window_to_center`
# -> `floating_center(…, con_get_workspace(…)->rect)`; `move absolute position
# center` uses the root rect). One chain, two rects — so the sizing basis and the
# placement basis are genuinely different, and the FIT question is still about the
# workspace.
#
# MEASURED 2026-09-12, read-only, i3 4.25.1 on both hosts. Rects from
# `i3-msg -t get_outputs` and `-t get_workspaces`; cell size from `TIOCGWINSZ` on
# each host's own running alacritty pty, which reports the grid in rows/cols AND
# its size in pixels — so the cell size is a DIVISION, not a DPI estimate. (The pty
# has to be a DIRECT child of alacritty: a tmux pane's winsize is synthesized by
# tmux and says nothing about a font.)
#
#   host       OUTPUT rect  workspace rect  bar   cell (px)     largest grid seen
#   workbench  3440x1440    3440x1413       27px  11.0 x 22.0   312 x 63
#   laptop     2256x1504    2256x1480       24px  19.0 x 37.0   118 x 39
#
# Width is identical in both columns — the bar is a full-width strip — so only the
# HEIGHT was ever wrong. The laptop's cell is 1.73x wider and 1.68x taller than the
# workbench's, 2.90x the AREA. An earlier revision of this file said "roughly twice
# in each axis … roughly four times the area"; that was unmeasured and it
# OVER-stated the gap, which is exactly how one percentage came to look like it
# could serve both hosts.
#
# 🔴 SO `ppt` IS THE RIGHT UNIT AND NOT A HOST-INDEPENDENT SIZE. `resize set 90 ppt
# 90 ppt` is ~2030x1354 px on the laptop (inside its 2256x1480 workspace) and
# 3096x1296 on the workbench — +41% on the width of the 2200x1100 window the
# operator called unusable, on the host where the review TUI had never even opened
# (its `picks.jsonl` does not exist; the laptop's does). Hence `_REVIEW_PPT` below,
# and hence the guard that the two hosts DISAGREE.
#
# ⚠ THE CELL ARITHMETIC BELOW MODELS THE i3 **RECT** ONLY, SO EVERY GRID IT
# PRODUCES IS A BARE-RECT GRID, NOT A RENDERED ONE — and `_DEPLOYED_REVIEW_CELLS`
# is a CLIENT-area cell count, so the two are not the same unit. Do not restate any
# of these pixel figures to a tenth.
#
# (a) DECORATION — QUANTIFIED, AND THE CLAIM THAT USED TO BE HERE WAS WRONG. This
# block said `nix/i3/config.nix` "sets `default_border pixel 2` but never
# `default_floating_border`, so floats take i3's default `normal 2` … a titlebar
# plus borders". THAT DOES NOT HOLD FOR THIS WINDOW. `config.default_floating_border`
# is applied ONLY inside `floating_enable()` under `if (automatic)` (i3 4.25.1
# `src/floating.c:353-354`). `for_window … floating enable` is a COMMAND, not the
# automatic path: `cmd_floating()` calls `floating_enable(con, false)`
# (`src/commands.c:1157`), and `run_assignments()` runs at `src/manage.c:588`
# (map time; `:746` on the remanage path) — AFTER the `want_floating` decision at
# `src/manage.c:462-546`. `want_floating` is set only by window-type atoms /
# `_NET_WM_STATE_MODAL` / sticky / transient-for / a fixed min==max size hint, and
# an alacritty toplevel matches none of them. So the container keeps what
# `con_new_skeleton()` gave it (`src/con.c:44`, `config.default_border`), which
# `default_border pixel 2` sets to **BS_PIXEL with `logical_px(2)`**, and
# `current_border_width` comes from the non-floating arm of `src/manage.c:546`.
# `con_border_style_rect_without_title()`'s non-BS_NORMAL branch is
# `{bw, bw, -2bw, -2bw}` (`src/con.c:1846-1849`): 2 px on all four sides, so the
# CLIENT area is the rect MINUS 4 px in BOTH axes. There is no titlebar.
#
# LIVE, read-only (`i3-msg -t get_tree` / `-t get_config`, i3 4.25.1): the running
# alacritty windows report `border=pixel, current_border_width=2`, and the config
# carries no `default_floating_border`, no `new_float` and no `for_window … border`.
# ⚠ Those live windows sit in a TABBED parent, and `con_border_style()`
# (`src/con.c:1942`) overrides a non-BS_NORMAL style to BS_NORMAL for a >1-child
# tabbed container — which is why their client HEIGHT is rect-2, not rect-4. A
# float's parent is a CT_FLOATING_CON with `layout = L_SPLITH`
# (`src/floating.c:291`), so no override applies and -4/-4 is the float's case.
# The live read is evidence for the BORDER STYLE and the CELL SIZE; the float's own
# inset comes off the C.
#
# (b) SIZE-INCREMENT SNAPPING — STILL NOT MODELLED. `floating_resize`
# (`src/floating.c`) upscales the decorated rect to a multiple of the window's
# width/height increments. Measuring it needs a window opened or resized on the
# operator's live desk.
#
# 🔴 AND THE RENDERED GRID HAS NEVER BEEN OBSERVED. The workbench's
# `~/.config/mention-open/picks.jsonl` does not exist (measured 2026-09-12), so the
# review window has never opened on that host; and no floating container existed in
# the live tree in any round of this PR's audit, so no real floating `deco_rect` was
# ever read. Everything about the float's inset is derived from i3's C.
#
# The guards below pin these things — COUNT THE LIST, and do not put a total beside
# it (a total kept next to its own list drifts: this block said "FIVE" while the
# block held nine test functions, and round 1 added two while incrementing the
# total by one):
#
#   1. the window receives a resize whose UNITS are percent-of-output (i3 defaults
#      the unit to px, so `resize set 90 90` is a legal 90-PIXEL window);
#   2. the percentages are THIS HOST'S;
#   3. the two hosts' percentages DIFFER;
#   4. the ledger of measured rects is internally coherent (the check that would
#      have caught the workspace-for-output substitution);
#   5. the workbench's pair still maps to `_DEPLOYED_REVIEW_CELLS` in the BARE
#      RECT (read its docstring for what that does and does not claim);
#   6. the size still FITS the workspace it is centred in, on both hosts;
#   7. the rule issuing the resize also FLOATS the window (a `resize set` on a
#      TILED window is a silent no-op — the same hazard guard 1's companion pins
#      for the picker);
#   8. the rule names the review instance EXACTLY (i3's PCRE is unanchored);
#   9. the window is CENTRED on both hosts.
# --------------------------------------------------------------------------- #
#: The `resize set <w> ppt <h> ppt` operands each host must receive, and the
#: MEASURED facts they were derived from. Literal expectations on purpose — a
#: value recomputed from the config would be the implementation restating itself.
#:
#: 🔴 TUNING EITHER PAIR MEANS RE-DERIVING IT FROM A FRESH MEASUREMENT, not
#: nudging the number. The laptop's 90x90 is the largest round pair whose
#: OUTPUT-relative size still fits inside the laptop's workspace rect.
#:
#: ⚠ THE WORKBENCH'S 77 HAS NO BETTER JUSTIFICATION THAN "IT IS WHAT IS ALREADY
#: COMMITTED", and saying so is the honest version. An earlier revision of this
#: docstring called 64x77 "the pair that reproduces the size that host ALREADY
#: renders"; it does not. `64 ppt 77 ppt` is a 2201x1108 BARE RECT, which is
#: 200x50 cells — but the CLIENT area is 4 px smaller in each axis (BS_PIXEL bw 2;
#: see the decoration paragraph above), i.e. 2197x1104 = **199x50** cells, one
#: column short of the 200x50 the deployed cell constants produced. `78 ppt` gives
#: the SAME 199x50, so the two are indistinguishable on screen and the "78 is one
#: row more" rationale was void. No purpose for 77 over 78 was found; that absence
#: is the finding, and a purpose invented to fill it would be a hypothesis.
_REVIEW_PPT = {"workbench": (64, 77), "laptop": (90, 90)}

#: The measured rects and cell size per host — see the MEASURED table above.
#:
#: 🔴 THIS LEDGER IS AN INPUT, NOT A DERIVED FACT. Nothing in this file can check
#: it against the machines; it is a transcription of a read-only measurement, and
#: the guards that read it are only as right as it is. Round 1 of this PR's audit
#: found it holding the WORKSPACE height in what the arithmetic used as the OUTPUT
#: height — which is why `output` and `workspace` are now separate keys and why
#: `test_the_MEASURED_ledger_is_INTERNALLY_COHERENT` exists. Re-measure
#: (`i3-msg -t get_outputs`, `-t get_workspaces`, `TIOCGWINSZ` on a direct
#: alacritty child pty) rather than adjusting a number to make a guard pass.
#:
#: `output` is what `resize set … ppt` multiplies; `workspace` is what `move
#: position center` centres against and what a FIT question is asked of.
_MEASURED = {
    "workbench": {"output": (3440, 1440), "workspace": (3440, 1413),
                  "cell": (11.0, 22.0)},
    "laptop": {"output": (2256, 1504), "workspace": (2256, 1480),
               "cell": (19.0, 37.0)},
}

#: What `REVIEW_COLUMNS`x`REVIEW_LINES` held on `main`. Those were passed to
#: alacritty as `window.dimensions`, so this is a CLIENT-AREA cell count.
#:
#: ⚠ IT IS NOT "THE SIZE THE WORKBENCH MUST KEEP RENDERING" — an earlier revision
#: of this docstring said that and the change does not keep it. The guard below
#: compares this against a BARE-RECT grid, which is one column wider than the
#: client grid the new rule produces (199x50). The two numbers are in different
#: units and they do not agree; see `_REVIEW_PPT`.
_DEPLOYED_REVIEW_CELLS = (200, 50)


def _review_ppt(is_laptop):
    """The (w, h) percent pairs the review window receives on this host."""
    return _percent_resizes(_actions(render(is_laptop), _review_window()))

@pytest.mark.parametrize("is_laptop", [False, True], ids=HOSTS)
def test_the_review_window_is_sized_in_PERCENT_OF_OUTPUT_on_both_hosts(
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
        "it as a PERCENT OF THE OUTPUT RECT (`resize set <w> ppt <h> ppt`). i3 is "
        "the ONLY thing that sizes this window — scripts/mention-open.py passes "
        "no `window.dimensions` for it — so with this rule gone the window is "
        "whatever alacritty defaults to, and nothing fits it to the display."
        % (host, window, MO.REVIEW_CLASS,
           sorted(got) or "NO for_window rule at all"))
    bad = [p for p in pcts if not all(0 < v <= 100 for v in p)]
    assert not bad, (
        "on the %s render the review window's percent-of-output resize is "
        "%s — a `ppt` value outside 1..100 is not a percentage of anything i3 "
        "can place it on." % (host, bad))


@pytest.mark.parametrize("is_laptop", [False, True], ids=HOSTS)
def test_the_review_windows_percentages_are_THIS_HOSTS(is_laptop):
    """🔴 THE PER-HOST PIN. The guard above accepts ANY percentage in 1..100, so
    it passed in full over a single shared `90 ppt 90 ppt` — which fits the laptop
    and is +41% on the width of the window the operator reported as unusable, on
    the workbench, where the review TUI had never opened at all.

    A percentage of an output is a different SIZE on every output. `ppt` buys the
    right unit; it does not buy one number serving two displays.
    """
    host = HOSTS[1] if is_laptop else HOSTS[0]
    want = _REVIEW_PPT[host]
    got = _review_ppt(is_laptop)
    m = _MEASURED[host]
    assert got == [want], (
        "on the %s render the review window receives percent-of-output "
        "resize(s) %s; this host must get exactly %s. OUTPUT %dx%d (what `ppt` "
        "multiplies), workspace %dx%d, cell %.1fx%.1f px (measured). If you are "
        "retuning this deliberately, re-derive the pair from a fresh measurement "
        "and update `_REVIEW_PPT` — and check the OTHER host separately, because "
        "the two are not interchangeable."
        % (host, got or "NONE", [want], m["output"][0], m["output"][1],
           m["workspace"][0], m["workspace"][1], m["cell"][0], m["cell"][1]))


def test_the_two_HOSTS_review_percentages_DIFFER():
    """🔴 THE GUARD ON THE DECISION ITSELF, and the one no per-host pin can give
    you. `_REVIEW_PPT` is a ledger in this file: a future change that collapses
    the two hosts back to one `resize set` AND edits the ledger to match passes
    every other guard here, green, having reintroduced the exact defect.

    So this reads the two RENDERS and asserts they disagree, without consulting
    the ledger at all. The percentages may both be retuned; they may not become
    one number. A display that genuinely made the same pair correct on both hosts
    would be a reason to delete this test deliberately, with the measurement that
    shows it — not a reason for it to quietly pass.
    """
    wb, lt = _review_ppt(False), _review_ppt(True)
    assert wb and lt, (
        "one of the renders carries no percent-of-output resize for the review "
        "window at all (workbench=%s, laptop=%s) — see the sizing guard above; "
        "this guard would otherwise pass vacuously on two empty lists being "
        "unequal to each other." % (wb or "NONE", lt or "NONE"))
    assert wb != lt, (
        "both hosts size the review window at %s. The percentages are PER HOST on "
        "purpose: `ppt` is a percentage of the OUTPUT rect, and the two outputs "
        "are %dx%d (workbench) and %dx%d (laptop), so one pair cannot express one "
        "intent on both. A shared 90x90 renders 3096x1296 on the workbench — 41%% "
        "wider than the 2200x1100 window the operator called unusable, on the host "
        "that never had the defect. Put the per-host fragment back in "
        "nix/i3/config.nix (`reviewSizePpt`)."
        % (wb, _MEASURED["workbench"]["output"][0],
           _MEASURED["workbench"]["output"][1],
           _MEASURED["laptop"]["output"][0], _MEASURED["laptop"]["output"][1]))


def test_the_MEASURED_ledger_is_INTERNALLY_COHERENT():
    """🔴 THE CHECK THAT WOULD HAVE CAUGHT ROUND 1'S DEFECT, and the reason
    `_MEASURED` carries two rects instead of one.

    Every other guard here reads `_MEASURED` as ground truth, so a wrong ledger is
    invisible to all of them: this PR shipped with the WORKSPACE height sitting in
    the slot the arithmetic used as the OUTPUT height, and the suite was green.
    Nothing in a unit test can measure a display — but it CAN assert that the two
    rects stand in the relationship a bar-on-an-output must produce, and that is
    precisely the relationship the substitution destroys.

    WIDTH must be EQUAL: an i3 bar is a full-width strip, so an output and its
    workspace differ in height only. HEIGHT must differ by a plausible bar, and
    the three failing shapes are DIFFERENT faults, so the message names each
    separately rather than attributing all of them to one cause:

      * gap EXACTLY ZERO  — the same rect was written into both slots (the bug
        round 1 shipped);
      * gap NEGATIVE      — the two rects are SWAPPED, or one of them is not this
        output's at all. NOT the identical-rect case;
      * gap LARGE relative to the screen — one of the two is not this output's
        rect.

    ⚠ The zero and negative branches used to share one sentence reading "a
    negative value means the same rect has been written into both slots", which
    is false of the negative case.

    ⚠ INVARIANT GUARD, NOT REGRESSION COVERAGE. The pre-fix tree had no `output`
    key for this to be red against; what it pins is that the ledger cannot be
    edited back into the shape the audit found. Its killing power is demonstrated
    by mutation (substitute the workspace height for the output height, per host)
    rather than by a red on an earlier commit.
    """
    for host, m in sorted(_MEASURED.items()):
        out_w, out_h = m["output"]
        ws_w, ws_h = m["workspace"]
        assert out_w == ws_w, (
            "%s: the ledger's OUTPUT width (%d) and workspace width (%d) differ. "
            "An i3 bar is a full-width strip, so these two rects can only differ "
            "in HEIGHT — one of them is not this output's rect. RE-MEASURE with "
            "`i3-msg -t get_outputs` and `-t get_workspaces`."
            % (host, out_w, ws_w))
        bar = out_h - ws_h
        assert bar > 0, (
            "%s: the ledger says the OUTPUT is %d px tall and the workspace %d px "
            "— a bar of %d px. The output rect is strictly taller than the "
            "workspace it hosts a bar on. %s That matters because `resize set … "
            "ppt` multiplies the OUTPUT rect (i3 4.25.1 `cmd_resize_set`), so a "
            "workspace height in the output slot silently understates every `ppt` "
            "height by the bar — round 1's defect. RE-MEASURE both rects; do not "
            "reconcile them by editing one."
            % (host, out_h, ws_h, bar,
               ("A gap of EXACTLY ZERO means the same rect has been written into "
                "both slots." if bar == 0 else
                "A NEGATIVE gap means the two rects are SWAPPED, or one of them "
                "is not this output's rect at all — it does NOT mean they are "
                "identical; that is the zero case.")))
        assert bar < out_h // 8, (
            "%s: the ledger implies a %d px status bar on a %d px output, which is "
            "not a status bar. One of `output`/`workspace` is not this output's "
            "rect — RE-MEASURE." % (host, bar, out_h))


def test_the_WORKBENCH_keeps_rendering_the_size_it_ALREADY_renders():
    """🔴 WHAT THIS PINS IS THE BARE-RECT DERIVATION OF `64 ppt 77 ppt`. READ THE
    NAME NARROWLY — it OVER-states what is established, and the following is the
    correction rather than a defence of it.

    WHAT IT ASSERTS: that `64 ppt 77 ppt` of the 3440x1440 OUTPUT (2201x1108 px)
    quantises to `_DEPLOYED_REVIEW_CELLS` — 200x50 — at the measured 11.0x22.0 px
    cell. That keeps the pair from being "nudged": a retune that is not re-derived
    from a fresh measurement fails here.

    🔴 WHAT IT DOES **NOT** ESTABLISH — three things, all of them claims an earlier
    revision of this docstring made:

    1. **NOT that the workbench renders what it renders today.** The 2201x1108 px
       above is the BARE i3 RECT. The decoration is `pixel 2`, so the CLIENT area
       is 4 px smaller in EACH axis (2197x1104) = **199x50** cells, while
       `_DEPLOYED_REVIEW_CELLS` is a CLIENT-area count. The width axis is one
       column short. The promise "the workbench must come out of this change
       rendering what it renders now" IS NOT MET, for `77` or `78`, and it is
       RETIRED here rather than re-justified. (Why that is survivable: the
       workbench's `~/.config/mention-open/picks.jsonl` does not exist, so this
       window has never opened on that host; the laptop — the actual defect — is
       fixed comfortably either way.)

    2. **NOT that `78 ppt` would have been a visible change.** The previous
       wording said `78 ppt` is "~1123 px = 51 rows — one row MORE than today".
       51 is a row count of the BARE RECT (1123 // 22); the client height is 1119,
       and 1119 // 22 is 50. So BOTH pairs render 199x50 and are indistinguishable
       on screen. `77` is retained because it is the value already committed and
       deployed in this branch — there is no better justification, and not finding
       one is the finding.

    3. **NOT a rendered grid at all.** The rendered grid has NEVER been observed:
       the window has never opened on the workbench, and no floating container
       existed in the live i3 tree in any round of this PR's audit, so no real
       floating `deco_rect` was ever read. The -4/-4 inset is derived from i3
       4.25.1's C (see the decoration paragraph above), not measured.

    ⚠ The "both read as the same grid" sentence this docstring used to carry had no
    antecedent and was false on its nearest reading: against the workspace's 1413,
    `77 ppt` is 1088 px = 49 rows and `78 ppt` is 1102 px = 50 — not the same. The
    comparison that IS true is the one `nix/i3/config.nix` states: `78 ppt` of the
    workspace and today's 200x50 read as the same grid.

    `_MEASURED` is a transcribed measurement, not a derived fact;
    `test_the_MEASURED_ledger_is_INTERNALLY_COHERENT` is what keeps it honest about
    which rect is which. Size-increment snapping remains unmodelled.
    """
    m = _MEASURED["workbench"]
    out_w, out_h = m["output"]
    cell_w, cell_h = m["cell"]
    want_cols, want_lines = _DEPLOYED_REVIEW_CELLS
    got = _review_ppt(False)
    assert len(got) == 1, (
        "the workbench render carries %s percent-of-output resizes for the "
        "review window, expected exactly one: %s" % (len(got), got or "NONE"))
    w_pct, h_pct = got[0]
    cols = int(out_w * w_pct / 100.0 // cell_w)
    lines = int(out_h * h_pct / 100.0 // cell_h)
    assert (cols, lines) == (want_cols, want_lines), (
        "the workbench's `resize set %d ppt %d ppt` maps to %dx%d CHARACTER CELLS "
        "OF THE BARE RECT (%.0fx%.0f px of a %dx%d OUTPUT at a measured %.1fx%.1f "
        "px cell — `ppt` multiplies the OUTPUT rect, not the %dx%d workspace). It "
        "has to come to %dx%d, which is what REVIEW_COLUMNS/REVIEW_LINES held on "
        "main. ⚠ THAT IS A BARE-RECT COMPARISON AGAINST A CLIENT-AREA COUNT, not a "
        "claim that the two render alike: the rendered grid is one column narrower "
        "(199x50) because a floating con's rect includes a 2 px border on all four "
        "sides. See this test's docstring before reading a green here as 'the "
        "workbench is unchanged'. If the workbench's font or display really has "
        "changed, RE-MEASURE (`TIOCGWINSZ` on a direct alacritty child pty, "
        "`i3-msg -t get_outputs` for the rect), update `_MEASURED`, and re-derive "
        "the percentages."
        % (w_pct, h_pct, cols, lines, out_w * w_pct / 100.0,
           out_h * h_pct / 100.0, out_w, out_h, cell_w, cell_h,
           m["workspace"][0], m["workspace"][1], want_cols, want_lines))


@pytest.mark.parametrize("is_laptop", [False, True], ids=HOSTS)
def test_the_review_window_FITS_the_workspace_it_is_CENTRED_in(is_laptop):
    """The reported defect, expressed in the two rects that actually decide it.

    `resize set … ppt` multiplies the OUTPUT rect; `move position center` centres
    against the WORKSPACE rect. So the size is a percentage of one rect and has to
    fit inside a SMALLER one, and nothing about `ppt` guarantees that: `100 ppt`
    of the height is taller than any workspace carrying a bar.

    ⚠ INVARIANT GUARD on today's percentages (both hosts already fit), labelled as
    such — the original defect was a CELL count, which this file cannot express.
    What it buys is that a future retune cannot reintroduce an overflowing window
    by arithmetic nobody re-did, and it is the only assertion here that checks the
    laptop's 90x90 against a MEASURED RECT.

    ⚠ That is narrower than it used to read. An earlier wording said "the only
    assertion here that checks the laptop's 90x90 against something other than the
    `_REVIEW_PPT` ledger", and that is overstated:
    `test_the_two_HOSTS_review_percentages_DIFFER` reads the two renders and never
    consults the ledger, and the sizing guard's `0 < v <= 100` bound does not read
    it either. Neither compares against a rect, which is the distinction that makes
    this guard the only one of its kind.
    """
    host = HOSTS[1] if is_laptop else HOSTS[0]
    m = _MEASURED[host]
    out_w, out_h = m["output"]
    ws_w, ws_h = m["workspace"]
    got = _review_ppt(is_laptop)
    assert len(got) == 1, (
        "the %s render carries %s percent-of-output resizes for the review "
        "window, expected exactly one: %s" % (host, len(got), got or "NONE"))
    w_pct, h_pct = got[0]
    px_w, px_h = out_w * w_pct / 100.0, out_h * h_pct / 100.0
    assert px_w <= ws_w and px_h <= ws_h, (
        "on the %s render the review window's `resize set %d ppt %d ppt` is "
        "%.0fx%.0f px (%d%% x %d%% of the %dx%d OUTPUT), which does not fit the "
        "%dx%d WORKSPACE it is centred in — %.0f%% x %.0f%% of it. That is the "
        "reported defect: a window hanging off the edge of the screen. Note the "
        "two rects are different on purpose (`cmd_resize_set` uses the output, "
        "`cmd_move_window_to_center` the workspace), so a height percentage above "
        "~%d ppt cannot fit here however legal it looks."
        % (host, w_pct, h_pct, px_w, px_h, w_pct, h_pct, out_w, out_h,
           ws_w, ws_h, 100.0 * px_w / ws_w, 100.0 * px_h / ws_h,
           int(100 * ws_h / out_h)))


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
        "percent-of-output resize at all — see the sizing guard above."
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

    ⚠ WHAT THAT COSTS, stated rather than left for the next person to discover:
    this guard FORBIDS the legitimate spelling
    `instance="mention-(open|review)"`. That alternation is a correct, arguably
    tidier way to write one rule covering both of this handler's windows, and it
    reddens here. The refusal is deliberate — an alternation is also how a rule
    silently grows to cover a window nobody sized for, and this file cannot tell
    the two apart — but it is a real constraint, not an oversight. A change that
    genuinely wants one rule for both windows has to widen this guard explicitly
    (match the criterion against the SET of instances it is allowed to name),
    which is the conversation worth having rather than skipping.
    """
    host = HOSTS[1] if is_laptop else HOSTS[0]
    cfg = render(is_laptop)
    window = _review_window()
    sizing = [crits for crits, cmd in _matching_rules(cfg, window)
              if _percent_resizes(_chain(cmd))]
    assert sizing, (
        "on the %s render no rule matching the review window (%s) carries a "
        "percent-of-output resize at all — see the sizing guard above."
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
    """A window sized to most of the workspace and placed at i3's default float
    position hangs off the edge it is pushed against — so the resize is only half
    the fix. Same assertion shape as guard 1's, against the review window's own
    identity."""
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
def test_the_PICKERS_CELL_COUNT_FITS_both_hosts_screens(is_laptop):
    """REGRESSION COVERAGE — red at 120 columns, green at 110.

    🔴 THIS IS THE GUARD THE REVIEW WINDOW'S DEFECT NEVER HAD, and its absence is
    why the picker shipped too wide for a year. `PICKER_COLUMNS` is a CELL count
    and a cell's pixel size is per-host, so one constant has to fit BOTH displays
    — the identical defect class the review window's `ppt` rule exists to end,
    one window over. Nothing checked it, so nothing objected.

    🔴 MEASURED LIVE, not computed: at 120 columns the laptop's picker came up as
    `rect=2284x818 at +-14+355` (client 2280x814) on a 2256 px screen. i3 centres
    it, so the 28 px of overflow was split across BOTH edges — x was NEGATIVE, and
    ~14 px was clipped left and right. An earlier comment in `mention-open.py` put
    this at "about 24 px" by measuring the CLIENT against the screen and omitting
    the 2 px border per side; the RECT is what has to fit.

    ⚠ THE BORDER TERM IS DERIVED, NOT MEASURED — see the decoration paragraph
    above. `default_border pixel 2` with no `default_floating_border` means
    BS_PIXEL/2 px for a `for_window … floating enable` container, so the rect is
    the client plus 2 px per side. That is read off i3's source; a floating
    `deco_rect` was never obtainable (no floating containers in any live tree).
    If it is ever wrong, it is wrong by a few px in the SAFE direction here.
    """
    host = HOSTS[1] if is_laptop else HOSTS[0]
    cell_w, cell_h = _MEASURED[host]["cell"]
    ws_w, ws_h = _MEASURED[host]["workspace"]
    border = 2 * 2  # BS_PIXEL, logical_px(2), both sides
    rect_w = MO.PICKER_COLUMNS * cell_w + border
    rect_h = MO.PICKER_LINES * cell_h + border
    assert rect_w <= ws_w and rect_h <= ws_h, (
        "on %s the picker's %dx%d CELLS are %.0fx%.0f px of container rect "
        "(cell %.1fx%.1f + %d px border), which does not fit the %dx%d WORKSPACE "
        "— %.1f%% x %.1f%% of it. i3 CENTRES this window, so an overflowing width "
        "hangs off BOTH edges (measured at 120 columns: x=-14, ~14 px clipped each "
        "side). Lower PICKER_COLUMNS/PICKER_LINES in scripts/mention-open.py — do "
        "NOT add an i3 `resize set`, which would restate this geometry in a file "
        "that cannot see these constants while `picker_header` kept wrapping to the "
        "OLD width. The binding constraint is the host with the LARGER cell."
        % (host, MO.PICKER_COLUMNS, MO.PICKER_LINES, rect_w, rect_h,
           cell_w, cell_h, border, ws_w, ws_h,
           100.0 * rect_w / ws_w, 100.0 * rect_h / ws_h))


@pytest.mark.parametrize("is_laptop", [False, True], ids=HOSTS)
def test_the_PICKERS_WRAP_WIDTH_tracks_its_COLUMN_COUNT(is_laptop):
    """REGRESSION COVERAGE for the seam the fit guard alone cannot see.

    🔴 A WIDTH THAT FITS THE SCREEN IS NOT A WIDTH THE TEXT WAS WRAPPED FOR.
    `picker_header` wraps to `PICKER_COLUMNS - 2`, so the wrap width and the
    window width are two consumers of ONE constant — and the tempting fix for an
    overflowing picker (size it in i3 instead) changes the window while leaving
    the text wrapped for the old width, which no screen-fit assertion can detect.
    This pins the RELATIONSHIP, not either side.

    ⚠ THE FUNCTION IS `picker_header`, NOT `format_row`. `mention-open.py` named
    `format_row` as the wrapper and **no such function has ever existed in that
    file** — a cross-reference nothing could check. (Other modules do have a real
    `format_row`, which is exactly why a grep for it looks reassuring.) It is why
    the first draft of this guard reached for `hasattr` and would have SKIPPED
    itself silently. No `hasattr`, no skip: a wrong name now fails loudly.
    """
    del is_laptop  # host-independent: this is a seam between two code paths
    wrapped = MO.picker_header("x" * (MO.PICKER_COLUMNS * 3))
    widest = max(len(line) for line in wrapped)
    assert widest <= MO.PICKER_COLUMNS - 2, (
        "picker_header wrapped a long message to %d columns while PICKER_COLUMNS "
        "is %d. The wrap width must stay derived from the column count, or the "
        "picker renders text wider than the window it is drawn in."
        % (widest, MO.PICKER_COLUMNS))


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
