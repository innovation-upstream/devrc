"""🔴 GAME MODE IS AN *EMPTY* i3 BINDING MODE — the emptiness IS the mechanism.

`set $mod Mod1` makes `$mod` ALT, so i3's default mode holds a global X11 grab
on ~60 Alt combos (Alt+Tab, Alt+1..0, Alt+Shift+1..0, Alt+Return, Alt+d/f/e/a/
b/n/r/h/j/k/l/space/grave/minus/equal). A grab means the keypress is delivered
to i3 and the focused window NEVER SEES IT — a game is not merely interrupted by
rofi, it is deaf to those keys. i3 offers no per-window `bindsym` criteria and no
conditional grab, so switching to a binding mode that binds (almost) nothing is
the only native way to make it let go.

WHAT THAT MAKES TESTABLE, and why each guard is shaped the way it is:

1. THE MODE MUST EXIST ON BOTH HOSTS. `nix/i3/config.nix` is a function of
   `{ isLaptop }`, and three of its fragments are host-conditional. A mode
   parked inside one of those would silently not exist on the other machine.
   So this file RENDERS the config for both values of `isLaptop` (a tiny nix
   string evaluator, positive-controlled below) and asserts against the render,
   not against the source text.

2. THE ESCAPE KEYS MUST STILL BE ESCAPES. Being stuck in game mode with no way
   out is the worst failure this feature has: every Alt binding is dead by
   design, so a keyboard with no working escape leaves `i3-msg` over SSH as the
   only exit. The hazard is NOT "somebody deleted the escape" — it is that some
   FUTURE default-mode binding quietly claims the same key, which is legal, is
   silent, and makes the escape unreachable the moment you need it. So the guard
   pins a RELATIONSHIP: **every key bound inside the mode must be bound nowhere
   in the default mode**, with both sets DERIVED by parsing. Spelling `Pause` and
   `Scroll_Lock` here would be a guard on words — a rename of the escape keys
   would walk straight past it while the collision it exists to catch persists.

3. NOTHING IN THE MODE MAY USE `$mod`. Anything bound there is a key the game
   goes back to not receiving, and `$mod` is the entire set this feature is
   about. A `$mod` escape would also be self-defeating: a mode whose purpose is
   to release Alt must not need Alt to leave.

4. THE PILL AND EVERY FILE IT NEEDS MUST DEPLOY ON THE SAME HOSTS, from the
   right source. Modelled on the ▦ claude-runs pill's guard
   (`test_the_bar_block_and_EVERY_file_it_needs_deploy_on_the_SAME_hosts`),
   which was written after three real breakages survived a SPELLED check — so
   this one reads assignments rather than vocabulary, and covers the block's
   CLICK target as well as its `command` (this pill is its own click handler,
   and a dead click is invisible until someone tries it mid-game).

5. ONE SIGNAL NUMBER, THREE PLACES. The i3 escape bindings, the nix block and
   the script each name the RTMIN offset that repaints the pill. A mismatch is
   completely silent: the mode still flips, the bar just keeps showing the old
   state until its backstop interval. Pinned as equality, plus uniqueness
   against every other block's signal and the poller's `SIGNALS`.

NOT TESTED HERE, deliberately: that i3 actually releases the grabs. That is a
property of i3 4.24, not of this repo, and no hermetic test can observe it — the
mechanism is documented in the config comment instead.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
I3_CONFIG = REPO / "nix" / "i3" / "config.nix"
GRAPHICAL_NIX = REPO / "nix" / "graphical.nix"
BLOCK_SCRIPT_REL = "scripts/i3status-gamemode"

_HOSTS = ("workbench", "laptop")


def _load(relpath, modname):
    loader = importlib.machinery.SourceFileLoader(
        modname, str(REPO / relpath))
    spec = importlib.util.spec_from_loader(modname, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


blk = _load(BLOCK_SCRIPT_REL, "i3status_gamemode")


# --------------------------------------------------------------------------- #
# A TINY NIX STRING EVALUATOR for nix/i3/config.nix.
#
# The file is `{ isLaptop ? false }: let <fragments> in ''<body>''`, where every
# fragment is `if isLaptop then <string> else <string>`. Rendering it properly
# would mean asking `nix`, which is ground truth and CANNOT RUN in the nix-build
# check tier that gates merges (no nested nix, and that is the authoritative
# tier). So: parse the three shapes this file actually uses, and REFUSE loudly
# on anything else — `_render` raises rather than returning a body with an
# unresolved `${…}` in it, because a silently unsubstituted interpolation would
# make every assertion below pass over text no host ever gets.
# --------------------------------------------------------------------------- #
_INTERP = re.compile(r"\$\{(\w+)\}")


def _nix_string_at(text, pos):
    """Parse the nix string literal starting at/after `pos`.

    Handles `''…''` and `"…"`. Returns (value, index just past the literal).
    """
    i = pos
    while i < len(text) and text[i] in " \t\n":
        i += 1
    if text.startswith("''", i):
        end = text.index("''", i + 2)
        return text[i + 2:end], end + 2
    if text[i] == '"':
        end = text.index('"', i + 1)
        return text[i + 1:end], end + 1
    raise AssertionError(
        "nix/i3/config.nix fragment at offset %d is not a string literal this "
        "evaluator understands (%r…) — extend it rather than letting the "
        "config render with an unresolved interpolation" % (i, text[i:i + 40]))


def _fragments(header):
    """{name: (laptop_value, workbench_value)} for each `if isLaptop` binding."""
    out = {}
    for m in re.finditer(r"^  (\w+) =", header, re.M):
        name = m.group(1)
        region_end = len(header)
        nxt = re.search(r"^  \w+ =", header[m.end():], re.M)
        if nxt:
            region_end = m.end() + nxt.start()
        region = header[m.start():region_end]
        cond = region.find("if isLaptop then")
        if cond < 0:
            continue                      # not host-conditional; nothing to do
        then_val, after = _nix_string_at(region, cond + len("if isLaptop then"))
        els = region.index("else", after)
        else_val, _ = _nix_string_at(region, els + len("else"))
        out[name] = (then_val, else_val)
    return out


def _render(is_laptop: bool) -> str:
    """The i3 config as it is written to ~/.config/i3/config on that host."""
    text = I3_CONFIG.read_text()
    marker = "\nin\n''\n"
    head, body = text.split(marker, 1)
    body = body[:body.rindex("''")]
    frags = _fragments(head)

    def sub(m):
        name = m.group(1)
        assert name in frags, (
            "`${%s}` in the i3 config body is not an `if isLaptop` fragment "
            "this evaluator can resolve — extend `_fragments`, or this test "
            "renders text no host ever receives" % name)
        return frags[name][0 if is_laptop else 1]

    rendered = _INTERP.sub(sub, body)
    assert "${" not in rendered, (
        "unresolved interpolation left in the rendered config: "
        + rendered[rendered.index("${"):rendered.index("${") + 60])
    return rendered


def test_the_renderer_really_renders_two_different_configs():
    """🔴 POSITIVE CONTROL for the evaluator every guard below reads through.

    A renderer that returned the raw body, or the same string twice, would make
    "present on both hosts" true by construction. So: the two renders must
    DIFFER, and each must carry the fragment that belongs only to it.
    """
    laptop, workbench = _render(True), _render(False)
    assert laptop != workbench
    assert "brightnessctl" in laptop and "brightnessctl" not in workbench
    assert "xrandr --output DP-0" in workbench
    assert "xrandr --output DP-0" not in laptop
    # …and the shared body is in both, so the split did not eat it.
    for cfg in (laptop, workbench):
        assert "set $mod Mod1" in cfg
        assert "default_border pixel 2" in cfg


# --------------------------------------------------------------------------- #
# Parsing the RENDERED i3 config: bindings, per binding mode.
# --------------------------------------------------------------------------- #
_BINDSYM = re.compile(r"^\s*bindsym\s+((?:--\S+\s+)*)(\S+)\s+(.*)$")
_MODE_OPEN = re.compile(r'^\s*mode\s+"([^"]+)"\s*\{')


def _bindings_by_mode(cfg: str) -> dict:
    """{mode_name: {keyspec: command}}; the default mode is keyed `""`.

    Brace-depth tracked so the `bar { … colors { … } }` stanza and any nested
    block cannot be mistaken for a binding mode. Comment lines are dropped; a
    TRAILING comment is harmless because only the keyspec is compared.
    """
    out = {"": {}}
    depth = 0
    mode = ""
    for raw in cfg.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        opened = _MODE_OPEN.match(raw)
        if opened and depth == 0:
            mode = opened.group(1)
            out.setdefault(mode, {})
        m = _BINDSYM.match(raw)
        if m:
            out.setdefault(mode, {})[m.group(2)] = m.group(3).strip()
        depth += line.count("{") - line.count("}")
        if depth <= 0:
            depth = 0
            mode = ""
    return out


def test_the_binding_parser_sees_the_modes_that_are_actually_there():
    """🔴 POSITIVE CONTROL for `_bindings_by_mode`.

    Every assertion below is of the form "the game mode's keys avoid X". A
    parser that returned an empty game mode, or folded every binding into the
    default mode, would satisfy all of them silently. So pin that it separates
    modes, finds a healthy number of default-mode bindings, and does NOT invent
    a mode out of the `bar { … }` stanza.
    """
    modes = _bindings_by_mode(_render(False))
    assert "resize" in modes, sorted(modes)
    assert "bar" not in modes and "colors" not in modes, sorted(modes)
    # the default mode really is the big one — ~60 Alt grabs live there
    assert len(modes[""]) > 40, len(modes[""])
    # resize is parsed as its own, much smaller set, and its keys are bare
    assert 0 < len(modes["resize"]) < len(modes[""])
    assert "h" in modes["resize"] and "h" not in modes[""]


def _game_mode(cfg: str) -> dict:
    modes = _bindings_by_mode(cfg)
    assert blk.GAME_MODE in modes, (
        "nix/i3/config.nix defines no `mode %r` — the pill in "
        "scripts/i3status-gamemode watches a binding mode nothing enters, and "
        "renders its neutral state forever. Modes found: %s"
        % (blk.GAME_MODE, sorted(k for k in modes if k)))
    return modes[blk.GAME_MODE]


@pytest.mark.parametrize("is_laptop", [False, True], ids=_HOSTS)
def test_the_game_mode_exists_on_BOTH_hosts(is_laptop):
    """Parking the mode inside a host-conditional fragment is silent: the other
    machine simply has no such mode, `i3-msg mode game` fails, and the pill sits
    neutral forever."""
    assert _game_mode(_render(is_laptop))


@pytest.mark.parametrize("is_laptop", [False, True], ids=_HOSTS)
def test_the_game_mode_has_at_least_one_escape_back_to_default(is_laptop):
    """Without one, entering game mode is a one-way trip for the keyboard."""
    game = _game_mode(_render(is_laptop))
    escapes = {k: v for k, v in game.items() if 'mode "default"' in v}
    assert escapes, (
        "no binding inside `mode %r` returns to `mode \"default\"` — every Alt "
        "binding is released by design, so with no escape the only way out is "
        "`DISPLAY=:0 i3-msg mode default` from another machine. Bindings "
        "found: %s" % (blk.GAME_MODE, game))


@pytest.mark.parametrize("is_laptop", [False, True], ids=_HOSTS)
def test_no_escape_key_is_ALSO_bound_in_the_default_mode(is_laptop):
    """🔴 THE ONE THAT MATTERS. A future default-mode binding claiming an escape
    key is legal i3, silent, and strands the operator exactly when they cannot
    reach the bar. Both sets are DERIVED, never spelled: renaming the escape
    keys keeps the guard pointed at whatever they were renamed to.
    """
    cfg = _render(is_laptop)
    modes = _bindings_by_mode(cfg)
    game = _game_mode(cfg)
    clash = {k: modes[""][k] for k in game if k in modes[""]}
    assert not clash, (
        "a key bound inside `mode %r` is ALSO bound in the DEFAULT mode:\n"
        % blk.GAME_MODE
        + "\n".join("  %s -> game: %r | default: %r"
                    % (k, game[k], cmd) for k, cmd in sorted(clash.items()))
        + "\nThat is legal i3 and produces no warning, but it means the key is "
          "grabbed for something else on the way in, and the escape becomes "
          "unreachable at the exact moment it is needed. Pick a free key, or "
          "free this one in the default mode."
    )


@pytest.mark.parametrize("is_laptop", [False, True], ids=_HOSTS)
def test_nothing_in_the_game_mode_uses_the_mod_key(is_laptop):
    """The mode exists to make i3 let go of ~60 ALT grabs. A `$mod` binding here
    re-grabs one of them — and an escape that needs Alt would be self-defeating
    in a mode whose whole purpose is that Alt is free.
    """
    game = _game_mode(_render(is_laptop))
    modded = {k: v for k, v in game.items()
              if "$mod" in k or "Mod1" in k or "Alt" in k}
    assert not modded, (
        "`mode %r` binds a $mod/Alt combo: %s. Every binding in this mode is a "
        "key the game goes back to NOT receiving, and $mod is the entire set "
        "this feature releases." % (blk.GAME_MODE, modded))


# --------------------------------------------------------------------------- #
# The pill: block list, deploy gates, sources, click target.
# Parsers deliberately read ASSIGNMENTS, not the file's vocabulary — the ▦
# pill's first guard was spelled on substrings and three real breakages
# survived it while every spelled string was still present.
# --------------------------------------------------------------------------- #
def _nix_guard_hosts(guard):
    g = (guard or "").strip()
    while g.startswith("(") and g.endswith(")"):
        g = g[1:-1].strip()
    if g == "":
        return set(_HOSTS)
    if g == "isLaptop":
        return {"laptop"}
    if g == "!isLaptop":
        return {"workbench"}
    raise AssertionError(
        "unrecognised nix guard %r — extend `_nix_guard_hosts` rather than "
        "letting an unknown gate read as 'enabled everywhere'" % guard)


def _parse_home_file_entries(text):
    out = {}
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r'\s*home\.file\."([^"]+)"\s*=\s*(.*)$', line)
        if not m:
            continue
        attr, rhs = m.group(1), m.group(2).strip()
        g = re.match(r'lib\.mkIf\s+(.+?)\s*\{$', rhs)
        guard = g.group(1) if g else ""
        src = None
        for j in range(i + 1, min(i + 12, len(lines))):
            if re.match(r'\s*\};\s*$', lines[j]):
                break
            # up to the SEMICOLON, so a trailing `#` comment cannot contribute
            sm = re.match(r'\s*source\s*=\s*([^;]+);', lines[j])
            if sm:
                src = sm.group(1).strip()
        out[attr] = {"guard": guard, "source": src}
    return out


def _parse_block_list(text):
    m = re.search(r'\n  blocks =\s*\n(.*?);\n', text, re.S)
    assert m, "could not find the `blocks =` assignment in nix/graphical.nix"
    body = "\n".join(ln for ln in m.group(1).splitlines()
                     if not ln.strip().startswith("#"))
    out = {}
    for seg in body.split("++"):
        seg = seg.strip()
        if not seg:
            continue
        om = re.match(r'lib\.optionals?\s+(\([^)]*\)|!?\w+)\s*(.*)$', seg, re.S)
        guard, rest = (om.group(1), om.group(2)) if om else ("", seg)
        for ident in re.findall(r'\b([a-z]\w*Block)\b', rest):
            out[ident] = guard
    assert out, "parsed no blocks out of:\n" + body
    return out


def _block_body(text, ident):
    m = re.search(r'\n  %s = \{(.*?)\n  \};' % re.escape(ident), text, re.S)
    assert m, "no block definition named " + ident
    return m.group(1)


def _scripts_dir_targets(body):
    """Every `${scriptsDir}/<name>` the block references — command AND clicks."""
    return set(re.findall(r'\$\{scriptsDir\}/([\w.-]+)', body))


def test_the_gamemode_pill_and_EVERY_file_it_needs_deploy_on_the_SAME_hosts():
    """🔴 The relationship, not the vocabulary.

    Covers the CLICK target as well as the `command`: this pill is its own
    toggle, and unlike a broken `command` (a visibly missing block) a broken
    click is invisible until the operator tries it — plausibly mid-game, with
    the bar behind a fullscreen window.
    """
    text = GRAPHICAL_NIX.read_text()
    blocks = _parse_block_list(text)
    files = _parse_home_file_entries(text)

    assert "gamemodeBlock" in blocks, (
        "gamemodeBlock is not in the bar's block list — the pill is gone from "
        "the bar and there is then NO way into game mode at all (there is "
        "deliberately no default-mode keybind). Found: %s" % sorted(blocks))
    block_hosts = _nix_guard_hosts(blocks["gamemodeBlock"])
    assert block_hosts, "the block is enabled on no host at all"

    body = _block_body(text, "gamemodeBlock")
    targets = _scripts_dir_targets(body)
    assert targets, "gamemodeBlock references no ${scriptsDir} script at all"
    # the click target must be resolved FROM the block, not restated here
    assert any("--toggle" in ln for ln in body.splitlines()), (
        "gamemodeBlock has no `--toggle` click handler — the pill renders the "
        "mode but cannot change it, and nothing else can enter game mode")

    for name in sorted(targets):
        attr = ".config/i3status-rust/scripts/" + name
        want_source = "../scripts/" + name
        assert attr in files, (
            "%s has no home.file entry — the block would exec a path "
            "home-manager does not deploy" % attr)
        hosts = _nix_guard_hosts(files[attr]["guard"])
        assert block_hosts <= hosts, (
            "%s deploys on %s but its block runs on %s: the gate is NARROWER "
            "than its consumer's" % (attr, sorted(hosts), sorted(block_hosts)))
        assert files[attr]["source"] == want_source, (
            "%s deploys source=%r, expected %r"
            % (attr, files[attr]["source"], want_source))
        assert (REPO / want_source[3:]).is_file(), want_source


def test_the_block_script_is_VISIBLE_TO_THE_FLAKE():
    """A flake copies only TRACKED files: an un-`git add`ed script produces a
    perfectly successful `home-manager switch` with the pill simply absent.

    Measured differently per tier on purpose. In the nix build sandbox the tree
    IS what the flake could see (no `.git`), so `is_file()` is the direct
    measurement and the only one available; on a dev host the tree also holds
    untracked files, so `git ls-files` is consulted there.
    """
    assert (REPO / BLOCK_SCRIPT_REL).is_file(), BLOCK_SCRIPT_REL
    out = subprocess.run(["git", "-C", str(REPO), "ls-files", BLOCK_SCRIPT_REL],
                         capture_output=True, text=True, timeout=30)
    if out.returncode == 0:           # dev host; the sandbox has no .git
        assert BLOCK_SCRIPT_REL in out.stdout.split(), (
            "%s is UNTRACKED — `git add` it or the flake ships a switch that "
            "silently lacks it" % BLOCK_SCRIPT_REL)


def test_the_guard_evaluator_and_parsers_are_not_wired_to_nothing():
    """🔴 POSITIVE CONTROL for the machinery above: the parsers must DISAGREE
    across entries that genuinely differ, or every "X covers Y" assertion is
    vacuous. Every subject named here is deliberately NOT the block under test.
    """
    text = GRAPHICAL_NIX.read_text()
    files = _parse_home_file_entries(text)
    blocks = _parse_block_list(text)

    assert _nix_guard_hosts("") == {"workbench", "laptop"}
    assert _nix_guard_hosts("(!isLaptop)") == {"workbench"}
    assert _nix_guard_hosts("isLaptop") == {"laptop"}
    with pytest.raises(AssertionError):
        _nix_guard_hosts("(config.something.else)")

    guards = {v["guard"] for v in files.values()}
    assert "" in guards and any("isLaptop" in g for g in guards), guards
    assert len({v["source"] for v in files.values() if v["source"]}) > 5
    assert _nix_guard_hosts(blocks["memoryBlock"]) == {"workbench", "laptop"}
    assert _nix_guard_hosts(blocks["rigcontrolBlock"]) == {"workbench"}
    assert _nix_guard_hosts(blocks["batteryBlock"]) == {"laptop"}

    # `_scripts_dir_targets` must find MORE than one target on a block that has
    # more than one — otherwise the click-coverage claim above is a coincidence.
    assert len(_scripts_dir_targets(_block_body(text, "notifsBlock"))) >= 2


# --------------------------------------------------------------------------- #
# ONE signal number, three places.
# --------------------------------------------------------------------------- #
def _poller_signals():
    m = re.search(r"^SIGNALS = \{(.*?)\}",
                  (REPO / "scripts" / "bar-status-poll").read_text(),
                  re.S | re.M)
    assert m, "could not find SIGNALS in scripts/bar-status-poll"
    return {int(n) for n in re.findall(r":\s*(\d+)", m.group(1))}


def test_the_signal_number_agrees_in_all_three_places_and_collides_with_none():
    """🔴 A signal mismatch is COMPLETELY SILENT. The mode still flips and the
    escape key still works; the bar just keeps painting the old state until its
    backstop interval — which reads as "the toggle is flaky", not as "two files
    disagree about a number". And a signal that COLLIDES repaints somebody
    else's pill, which is worse: the wrong block refreshes and this one does not.
    """
    nix = GRAPHICAL_NIX.read_text()
    body = _block_body(nix, "gamemodeBlock")
    m = re.search(r"signal\s*=\s*(\d+)\s*;", body)
    assert m, "gamemodeBlock declares no `signal` — the pill can only ever be " \
              "repainted by its backstop `interval`"
    nix_signal = int(m.group(1))

    assert blk.SIGNAL == nix_signal, (
        "scripts/i3status-gamemode SIGNAL=%d but gamemodeBlock signal=%d — the "
        "click handler would signal a block that is not listening"
        % (blk.SIGNAL, nix_signal))

    game = _game_mode(_render(False))
    in_mode = sorted({int(n) for cmd in game.values()
                      for n in re.findall(r"-RTMIN\+(\d+)", cmd)})
    assert in_mode, (
        "no `pkill -RTMIN+N i3status-rs` in the `mode %r` escape bindings — "
        "escaping by KEYBOARD would leave the pill showing GAME until its "
        "backstop interval, i.e. the bar lies about the mode it is in"
        % blk.GAME_MODE)
    assert in_mode == [nix_signal], (
        "the `mode %r` escape bindings signal %s but the block listens on %d"
        % (blk.GAME_MODE, in_mode, nix_signal))

    others = [int(n) for n in re.findall(r"signal\s*=\s*(\d+)\s*;", nix)]
    assert others.count(nix_signal) == 1, (
        "signal %d is declared by more than one block in nix/graphical.nix (%s)"
        % (nix_signal, sorted(others)))
    assert nix_signal not in _poller_signals(), (
        "signal %d is already used by a bar-status-poll source — the poller "
        "would repaint the game pill and vice versa" % nix_signal)
    # POSITIVE CONTROL: the two uniqueness assertions above are meaningless if
    # the parsers found nothing to collide with.
    assert len(others) >= 6, others
    assert len(_poller_signals()) >= 5, _poller_signals()


# --------------------------------------------------------------------------- #
# The render script's pure halves. No i3 anywhere in this section.
# --------------------------------------------------------------------------- #
def test_parse_binding_state_reads_i3s_answer():
    """The shape i3 4.24 actually returns, measured on this host."""
    assert blk.parse_binding_state('{"name":"default"}') == "default"
    assert blk.parse_binding_state('{"name":"game"}') == "game"
    assert blk.parse_binding_state('{"name": "resize"}\n') == "resize"


@pytest.mark.parametrize("raw", [
    "",                       # i3-msg produced nothing (timeout, killed)
    "   \n",
    "not json at all",
    "ERROR: no such reply type",
    "[]",                     # a list, not the object we expect
    "null",
    '{"nome":"game"}',        # right shape, wrong key
    '{"name":null}',
    '{"name":42}',
    '{"name":"   "}',         # blank is not a mode name
])
def test_parse_binding_state_is_UNMEASURED_not_a_guess(raw):
    """🔴 None, never a coerced string. Every one of these must be
    distinguishable from a real mode — `render` and `next_mode` both branch on
    None, and a coerced `""` would render the neutral pill (claiming "not in
    game mode") on a host that might be squarely in it."""
    assert blk.parse_binding_state(raw) is None


def test_render_is_LOUD_in_game_mode():
    out = blk.render("game")
    assert out["state"] == "Critical"
    assert "GAME" in out["text"]
    assert blk.GLYPH in out["text"] and blk.GLYPH in out["short_text"]


def test_render_is_NEUTRAL_but_VISIBLE_in_every_other_mode():
    """🔴 NOT hide-at-zero, deliberately — the pill is the only way IN, so an
    invisible idle state would be a toggle with no off-state affordance."""
    for mode in ("default", "resize", "some_future_mode"):
        out = blk.render(mode)
        assert out["state"] == "Idle", mode
        assert out["text"].strip() == blk.GLYPH, mode
        assert "GAME" not in out["text"], mode


def test_render_is_FAIL_SAFE_when_the_mode_could_not_be_read():
    """The documented contract, shared with i3status-notifs: an unreadable
    query renders an empty block rather than guessing a state."""
    assert blk.render(None) == {"text": "", "state": "Idle"}


def test_the_rendered_block_is_JSON_i3status_rust_can_parse():
    for mode in ("game", "default", None):
        assert json.loads(json.dumps(blk.render(mode)))["state"] in (
            "Idle", "Critical")


def test_next_mode_flips_both_ways():
    assert blk.next_mode("game") == "default"
    assert blk.next_mode("default") == "game"
    # any OTHER measured mode is still "not in game mode" -> a click enters it
    assert blk.next_mode("resize") == "game"


def test_next_mode_does_NOTHING_when_the_current_mode_is_unknown():
    """🔴 The read and the write go through the same i3-msg, so a failed read
    means the write had no chance either. Guessing would let one click LEAVE
    game mode on a host that never entered it — restoring ~60 Alt grabs
    mid-game, with the bar plausibly hidden behind a fullscreen window."""
    assert blk.next_mode(None) is None


#: PATH pointed at a directory that DOES NOT EXIST, so `i3-msg` and `pkill` are
#: both unresolvable. REPLACING is required to reach the case at all: i3-msg is
#: installed on the dev host AND resolvable in the check sandbox, so no amount
#: of PREPENDING can make it unfindable, and a prepending version would measure
#: the LIVE i3 on this workbench instead of the fail-safe branch. Nothing is
#: reachable through the replacement — it holds no binaries because it holds
#: nothing; both call sites go through this ONE helper so the guard in
#: test_no_real_launchers.py has a single site to pin. `sys.executable` is
#: absolute, so the interpreter still resolves.
def _env_with_no_i3():
    return dict(os.environ,
                PATH=str(REPO / "scripts" / "tests" / "no-such-bin-dir"))


def test_the_toggle_is_reachable_from_the_command_line():
    """The pill's click handler is `<script> --toggle`, so the script must
    accept that argument — and the fail-safe path must exit 0 and print NOTHING
    (a click handler has nowhere to show an error, and stdout on a click is
    stray output i3status-rust would try to parse).
    """
    out = subprocess.run(
        [sys.executable, str(REPO / BLOCK_SCRIPT_REL), "--toggle"],
        capture_output=True, text=True, timeout=30, env=_env_with_no_i3())
    assert out.returncode == 0, out
    assert out.stdout == "", out.stdout


def test_the_block_renders_an_EMPTY_pill_when_i3_msg_is_UNREACHABLE():
    """END-TO-END fail-safe, through `main()` — the contract that a broken query
    must never crash or wedge the bar. POSITIVE CONTROL for the `None` unit test
    above: it proves the empty pill is what a real unreachable i3 produces, not
    only what `render(None)` returns when hand-fed."""
    out = subprocess.run(
        [sys.executable, str(REPO / BLOCK_SCRIPT_REL)],
        capture_output=True, text=True, timeout=30, env=_env_with_no_i3())
    assert out.returncode == 0, out
    assert json.loads(out.stdout) == {"text": "", "state": "Idle"}, out.stdout
