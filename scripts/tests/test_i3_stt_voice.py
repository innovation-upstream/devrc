"""🔴 GUARDS for hold-to-talk voice input (`stt-voice`) — i3, bar, packaging.

The feature spans four layers that must agree or it ships dead in one of four
ways, each of which is SILENT (no switch failure, no error anywhere):

  1. THE HOTKEY. `$mod+equal` press = start, `--release` = stop. A missing or
     typo'd half means hold-to-talk records forever, or never starts, and i3
     says nothing. Also: the key must collide with nothing (i3 takes the LAST
     binding for a keyspec — a later duplicate would silently replace one of
     the two stt halves), and the game-mode escape keys (Pause / Scroll_Lock,
     bound only INSIDE `mode "game"`) must not be claimed here.

  2. THE WINDOW RULE. The transcript TUI is a Go/bubbletea program spawned in
     an alacritty with `--class stt-voice,stt-voice`; the class in
     nix/i3/config.nix's `for_window` and the class constant in the Go source
     are ONE value spelled in TWO files — a mismatch floats nothing.

  3. THE BAR PILL. `sttBlock` in nix/graphical.nix and its `home.file` for
     scripts/i3status-stt must be gated the SAME way in BOTH places — the two
     known ways a block ships dead (a block commanding a script home-manager
     never deployed; a script deployed for a block that is not in the list).

  4. THE SIGNAL + THE PACKAGE. The repainting RTMIN offset is spelled in the
     nix block AND in the Go source; the binary lands on PATH only through the
     overlay + tools/default.nix + home.packages chain, and a version literal
     in the derivation is forbidden (mention-review's mechanism is reused).

Like test_i3_game_mode.py, the i3 assertions run against the RENDERED config
for BOTH isLaptop values (testlib.i3_render), not against the source text — a
bind parked inside a host-conditional fragment silently misses one host.

Every guard here was shown RED before the code it guards existed, and each
mutation was re-shown RED before being restored — see the run notes in the
commit message.

    run:  pytest scripts/tests/test_i3_stt_voice.py
"""
from __future__ import annotations

import pathlib
import re
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]

import sys  # noqa: E402
sys.path.insert(0, str(REPO / "scripts"))
from testlib.i3_render import HOSTS as _HOSTS, render as _render  # noqa: E402

I3_CONFIG = REPO / "nix" / "i3" / "config.nix"
GRAPHICAL_NIX = REPO / "nix" / "graphical.nix"
FLAKE_NIX = REPO / "flake.nix"
TOOLS_DEFAULT = REPO / "nix" / "pkgs" / "tools" / "default.nix"
PKG_DIR = REPO / "nix" / "pkgs" / "tools" / "stt-voice"
MAIN_GO = PKG_DIR / "src" / "cmd" / "stt-voice" / "main.go"
BLOCK_SCRIPT_REL = "scripts/i3status-stt"
BLOCK_SCRIPT = REPO / BLOCK_SCRIPT_REL

STT_KEY = "$mod+equal"
STT_START = "exec --no-startup-id stt-voice start"
STT_STOP = "exec --no-startup-id stt-voice stop"
GAME_MODE = "game"          # test_i3_game_mode.py owns the mode itself


# --------------------------------------------------------------------------- #
# Parsing the RENDERED i3 config: bindings (WITH press/release flags), modes,
# for_window rules.
# --------------------------------------------------------------------------- #
_BINDSYM = re.compile(r"^\s*bindsym\s+((?:--\S+\s+)*)(\S+)\s+(.*)$")
_MODE_OPEN = re.compile(r'^\s*mode\s+"([^"]+)"\s*\{')
_FOR_WINDOW = re.compile(r'^\s*for_window\s+\[class="([^"]+)"\]\s+(.*)$')


def _bindings_by_mode(cfg: str) -> dict:
    """{mode_name: [(flags, keyspec, command)]}; the default mode is keyed `""`.

    Unlike test_i3_game_mode.py's table, the flags are KEPT: press and release
    bindings for the same key are two entries, and the hold-to-talk pair is
    only hold-to-talk when exactly one of each exists.
    """
    out = {"": []}
    depth = 0
    mode = ""
    for raw in cfg.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        opened = _MODE_OPEN.match(raw)
        if opened and depth == 0:
            mode = opened.group(1)
            out.setdefault(mode, [])
        m = _BINDSYM.match(raw)
        if m:
            out.setdefault(mode, []).append(
                (m.group(1).strip(), m.group(2), m.group(3).strip()))
        depth += line.count("{") - line.count("}")
        if depth <= 0:
            depth = 0
            mode = ""
    return out


def test_the_binding_parser_sees_the_modes_that_are_actually_there():
    """🔴 POSITIVE CONTROL for `_bindings_by_mode`. Every guard below says
    "the stt pair is the ONLY thing on $mod+equal" and "the game-mode keys are not
    claimed" — a parser that returned an empty default mode, or folded every
    binding into the default mode, would satisfy all of them silently."""
    modes = _bindings_by_mode(_render(False))
    assert "resize" in modes, sorted(modes)
    assert "bar" not in modes and "colors" not in modes, sorted(modes)
    assert len(modes[""]) > 40, len(modes[""])
    assert 0 < len(modes[GAME_MODE]) < len(modes[""])
    game_keys = {k for _, k, _ in modes[GAME_MODE]}
    assert game_keys, game_keys


def _stt_binds(is_laptop):
    """The (flags, command) entries bound to the stt key in the default mode."""
    binds = _bindings_by_mode(_render(is_laptop))[""]
    return [(f, c) for f, k, c in binds if k == STT_KEY]


@pytest.mark.parametrize("is_laptop", [False, True], ids=_HOSTS)
def test_the_press_and_release_bindsym_pairs_exist_on_BOTH_hosts(is_laptop):
    """🔴 One press + one `--release` on the same key, with the exact commands.
    i3 config errors are silent: a typo'd half means the recording never stops
    (a wav file grows until the disk objects) or never starts, and nothing
    anywhere says so."""
    stt = _stt_binds(is_laptop)
    press = [(f, c) for f, c in stt if "--release" not in f]
    release = [(f, c) for f, c in stt if "--release" in f]
    assert press == [("", STT_START)], (
        "no (or a duplicate/typo'd) PRESS binding for %s — hold-to-talk never "
        "starts. Found for %s: %s" % (STT_KEY, STT_KEY, stt))
    assert release == [("--release", STT_STOP)], (
        "no (or a duplicate/typo'd) --release binding for %s — the recording "
        "never stops: a wav grows until the disk objects. Found: %s"
        % (STT_KEY, stt))


@pytest.mark.parametrize("is_laptop", [False, True], ids=_HOSTS)
def test_the_stt_key_is_claimed_by_nothing_else_and_claims_nothing(is_laptop):
    """🔴 Two directions, both derived:

    - the stt pair is the ONLY thing bound to `$mod+equal` in the default mode
      (i3 takes the LAST binding for a keyspec, so a later duplicate would
      silently kill one half of hold-to-talk);
    - the game-mode escape keys (Pause / Scroll_Lock, which live ONLY inside
      `mode "game"`) are not claimed here, and the stt key is not bound inside
      the game mode either (the mode exists to release grabs — nothing in it
      may be a key a game needs)."""
    modes = _bindings_by_mode(_render(is_laptop))
    game_keys = {k for _, k, _ in modes.get(GAME_MODE, [])}
    assert STT_KEY not in game_keys, (
        "%s is bound inside `mode %r` — that mode exists to RELEASE grabs; a "
        "key a game needs must not be there" % (STT_KEY, GAME_MODE))
    assert not ({"Pause", "Scroll_Lock"} & {k for _, k, _ in modes[""]}), (
        "Pause/Scroll_Lock are the game-mode escapes and must stay UNBOUND in "
        "the default mode (test_i3_game_mode.py's own guard)")
    for f, k, c in modes[""]:
        if _is_stt_cmd(c):
            assert k == STT_KEY, (
        "an stt-voice command is bound to a key other than %s" % STT_KEY)
    for keyspec in (STT_KEY, "Pause", "Scroll_Lock"):
        claims = [(f, c) for f, k, c in modes[""] if k == keyspec]
        if keyspec == STT_KEY:
            assert len(claims) == 2, (
                "%s is bound %d times in the default mode (%s) — i3 takes the "
                "LAST one, so one half of hold-to-talk is silently dead"
                % (STT_KEY, len(claims), claims))
        else:
            assert not claims, (
                "%s is claimed in the default mode: %s — it is a game-mode "
                "escape and must stay free" % (keyspec, claims))


def _is_stt_cmd(cmd: str) -> bool:
    return "stt-voice" in cmd


@pytest.mark.parametrize("is_laptop", [False, True], ids=_HOSTS)
def test_the_TUI_window_rule_exists_on_BOTH_hosts(is_laptop):
    """The TUI is `alacritty --class stt-voice,stt-voice -e stt-voice tui …`;
    without the for_window rule it tiles (usable but wrong) — with the rule the
    transcript opens as a centered float. Pinned on the RENDERED config."""
    rules = []
    for raw in _render(is_laptop).splitlines():
        m = _FOR_WINDOW.match(raw)
        if m:
            rules.append(m)
    mine = [r for r in rules if r.group(1) == stt_class()]
    assert len(mine) == 1, (
        "nix/i3/config.nix has no (or more than one) for_window rule for "
        'class="%s": %s' % (stt_class(), [r.group(0) for r in rules]))
    body = mine[0].group(2)
    assert "floating enable" in body, body
    assert "move position center" in body, body


# --------------------------------------------------------------------------- #
# The class and the signal: one value, spelled in TWO files each.
# --------------------------------------------------------------------------- #
def _go_const(name: str, rx: str):
    src = MAIN_GO.read_text()
    matches = re.findall(rx, src)
    assert len(matches) == 1, (
        "expected exactly one %r declaration in %s, found %s — the guard "
        "derives this value from the Go source, so an ambiguous or moved "
        "constant is a refusal, not a guess" % (name, MAIN_GO, matches))
    return matches[0]


def stt_class() -> str:
    return _go_const("tuiClass", r'tuiClass\s*=\s*"([^"]+)"')


def stt_signal() -> str:
    return _go_const("pkillSignal", r"pkillSignal\s*=\s*(\d+)")


def test_the_TUI_class_agrees_with_the_i3_rule():
    """The rule matches on WM_CLASS; the Go tool sets it. Rename one and not
    the other and the window tiles — or, with a narrower rewrite, does not
    appear where expected. 🔴 SELF-CONTAINED, and MEASURED as such: the first
    version derived the class from the Go source and asserted only the
    construction — a renamed constant survived it (the rename made BOTH sides
    agree on the WRONG string). The i3 rule is parsed HERE and compared to
    the Go constant here."""
    cls = stt_class()
    assert cls, "tuiClass in %s is empty" % MAIN_GO
    # the spawn must actually carry --class <cls>,<cls> built from the
    # constant (gofmt's spacing is not a contract, so the check is a regex)
    src = MAIN_GO.read_text()
    assert '"--class"' in src and re.search(
        r"tuiClass\s*\+\s*\",\"\s*\+\s*tuiClass", src), (
        "the Go tool no longer spawns the TUI with --class %s,%s" % (cls, cls))
    # and the i3 rule must name THAT class
    rule_classes = []
    for raw in _render(False).splitlines():
        m = _FOR_WINDOW.match(raw)
        if m:
            rule_classes.append(m.group(1))
    assert cls in rule_classes, (
        'the Go TUI class %r does not appear in any nix/i3/config.nix '
        "for_window rule (%s) — the transcript window would tile"
        % (cls, rule_classes))


# --------------------------------------------------------------------------- #
# The bar pill: block list, deploy gates, click target, sources.
# Parsers read ASSIGNMENTS, not vocabulary — modelled on test_i3_game_mode.py,
# where three real breakages survived a spelled check.
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
    return set(re.findall(r'\$\{scriptsDir\}/([\w.-]+)', body))


def test_the_stt_pill_and_EVERY_file_it_needs_deploy_on_the_SAME_hosts():
    """🔴 BOTH known ways a block ships dead, one guard:

    (a) `sttBlock` missing from the `blocks` list — the script deploys, the
        pill is gone, and the toggle has no on-screen affordance;
    (b) the block present but its `home.file` missing or NARROWER — the pill
        renders with a command that does not exist on that host.

    Voice input is the feature that makes the pill exist at all: unlike every
    count pill it has no alternate way in (the hotkey is the OTHER trigger, so
    a dead pill is survivable — but a dead CLICK is still invisible until the
    operator tries it, so the click target is covered too)."""
    text = GRAPHICAL_NIX.read_text()
    blocks = _parse_block_list(text)
    files = _parse_home_file_entries(text)

    assert "sttBlock" in blocks, (
        "sttBlock is not in the bar's block list — the state pill is gone "
        "and recording has no visible affordance besides holding the key "
        "blind. Found: %s" % sorted(blocks))
    block_hosts = _nix_guard_hosts(blocks["sttBlock"])
    assert block_hosts == set(_HOSTS), (
        "sttBlock is gated %r — the task wires it for BOTH hosts, "
        "not isLaptop-gated" % blocks["sttBlock"])

    body = _block_body(text, "sttBlock")
    targets = _scripts_dir_targets(body)
    assert targets, "sttBlock references no ${scriptsDir} script at all"
    assert any("--toggle" in ln for ln in body.splitlines()), (
        "sttBlock has no `--toggle` click handler — the pill renders the "
        "recording state but cannot stop it by click")

    attr = ".config/i3status-rust/scripts/i3status-stt"
    assert attr in files, (
        "%s has no home.file entry — the block would exec a path "
        "home-manager does not deploy" % attr)
    hosts = _nix_guard_hosts(files[attr]["guard"])
    assert hosts == set(_HOSTS), (
        "%s is gated %r — narrower than the block (%s): one host renders a "
        "block whose command does not exist"
        % (attr, files[attr]["guard"], sorted(block_hosts)))
    assert files[attr]["source"] == "../scripts/i3status-stt", (
        "%s deploys source=%r, expected ../scripts/i3status-stt"
        % (attr, files[attr]["source"]))
    assert BLOCK_SCRIPT.is_file(), BLOCK_SCRIPT_REL


def test_the_guard_evaluator_and_parsers_are_not_wired_to_nothing():
    """🔴 POSITIVE CONTROL for the machinery above, mirroring
    test_i3_game_mode.py: the parsers must DISAGREE across entries that
    genuinely differ, or every "X covers Y" assertion is vacuous. Every
    subject named here is deliberately NOT the block under test."""
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
    assert _nix_guard_hosts(blocks["gamemodeBlock"]) == {"workbench", "laptop"}
    assert _nix_guard_hosts(blocks["batteryBlock"]) == {"laptop"}

    # `_scripts_dir_targets` must find MORE than one target on a block that
    # has more than one — otherwise the click-coverage claim is a coincidence.
    assert len(_scripts_dir_targets(_block_body(text, "notifsBlock"))) >= 2

    # the stt pill must NOT match some other block's body (an over-broad
    # `_block_body` regex would silently pin gamemodeBlock instead)
    stt_body = _block_body(text, "sttBlock")
    assert "i3status-stt" in stt_body
    assert "i3status-gamemode" not in stt_body


def test_the_signal_number_agrees_in_both_places_and_collides_with_none():
    """🔴 A signal mismatch is COMPLETELY SILENT — the tool flips state, the
    bar keeps the old pill until its backstop interval, which reads as "the
    toggle is flaky", never as "two files disagree about a number". And a
    signal that COLLIDES repaints somebody else's pill, which is worse."""
    nix = GRAPHICAL_NIX.read_text()
    body = _block_body(nix, "sttBlock")
    m = re.search(r"signal\s*=\s*(\d+)\s*;", body)
    assert m, "sttBlock declares no `signal` — transitions only repaint at " \
              "the backstop interval and elapsed never ticks"
    nix_signal = int(m.group(1))
    go_signal = int(stt_signal())

    assert go_signal == nix_signal, (
        "pkillSignal=%d in %s but sttBlock signal=%d — the tool signals a "
        "block that is not listening, and the pill lags every transition"
        % (go_signal, MAIN_GO, nix_signal))

    others = [int(n) for n in re.findall(r"signal\s*=\s*(\d+)\s*;", nix)]
    assert others.count(nix_signal) == 1, (
        "signal %d is declared by more than one block in nix/graphical.nix"
        % nix_signal)
    assert nix_signal not in _poller_signals(), (
        "signal %d is already used by a bar-status-poll source — the poller "
        "would repaint the stt pill and vice versa" % nix_signal)
    # POSITIVE CONTROL: the uniqueness assertions above are meaningless if the
    # parsers found nothing to collide with.
    assert len(others) >= 6, others
    assert len(_poller_signals()) >= 5, _poller_signals()


def _poller_signals():
    m = re.search(r"^SIGNALS = \{(.*?)\}",
                  (REPO / "scripts" / "bar-status-poll").read_text(),
                  re.S | re.M)
    assert m, "could not find SIGNALS in scripts/bar-status-poll"
    return {int(n) for n in re.findall(r":\s*(\d+)", m.group(1))}


# --------------------------------------------------------------------------- #
# The binary: overlay → tools/default.nix → home.packages.
# --------------------------------------------------------------------------- #
def test_the_binary_is_wired_onto_the_PATH():
    """🔴 The whole hotkey chain execs `stt-voice` BY BARE NAME. A package
    that is built but never wired into home.packages is a hotkey that does
    nothing at all — `sh: stt-voice: command not found` in i3's stderr, and
    nothing on screen. Three links, each pinned:

      1. flake.nix overlay maps `pkgs.stt-voice` to the derivation;
      2. nix/pkgs/tools/default.nix lists it behind the SAME null filter the
         other local Go tools use (a derivation that cannot state its version
         must vanish, not fail the switch);
      3. the module exists on disk (go.mod + main + version)."""
    tools = TOOLS_DEFAULT.read_text()
    assert re.search(r"pkgs\.stt-voice\s*!=\s*null", tools), (
        "nix/pkgs/tools/default.nix lists stt-voice without the null filter "
        "— a null derivation would land in home.packages and FAIL the switch")
    assert "pkgs.stt-voice" in tools

    flake = FLAKE_NIX.read_text()
    assert re.search(
        r'stt-voice\s*=\s*import\s+\./nix/pkgs/tools/stt-voice\s*\{\s*pkgs\s*=\s*final;\s*\};',
        flake), (
        "flake.nix has no stt-voice overlay entry — pkgs.stt-voice is an "
        "attribute error and the tools list cannot add it")

    for rel in ("default.nix", "src/go.mod", "src/cmd/stt-voice/main.go",
                "src/cmd/stt-voice/version.go"):
        assert (PKG_DIR / rel).is_file(), rel

    # The version is READ OUT OF THE SOURCE, never a literal in the derivation
    # (clawgatectl's 0.7.95 lesson — see mention-review/default.nix's header).
    dflt = (PKG_DIR / "default.nix").read_text()
    assert re.search(r'versionFile\s*=\s*\./src/cmd/stt-voice/version\.go', dflt), (
        "stt-voice/default.nix no longer reads the version out of version.go "
        "— a hand-maintained literal there is the bug clawgatectl.nix exists "
        "because of")
    assert 'var buildVersion = "' in (
        PKG_DIR / "src/cmd/stt-voice/version.go").read_text()


def test_the_feature_files_are_VISIBLE_TO_THE_FLAKE():
    """🔴 A flake copies only TRACKED files: an un-`git add`ed script, Go
    source or test produces a perfectly successful switch — with the pill
    absent (script), the binary unbuildable (version.go), or the go-test tier
    silently floor-short (a _test.go file). The nix sandbox has no `.git`, so
    the direct check there is existence, exactly as
    test_i3_game_mode.py does it."""
    critical = [
        BLOCK_SCRIPT_REL,
        "nix/pkgs/tools/stt-voice/default.nix",
        "nix/pkgs/tools/stt-voice/src/go.mod",
        "nix/pkgs/tools/stt-voice/src/cmd/stt-voice/main.go",
        "nix/pkgs/tools/stt-voice/src/cmd/stt-voice/version.go",
    ]
    for rel in critical:
        p = REPO / rel
        assert p.is_file(), "%s does not exist at all" % rel
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", BLOCK_SCRIPT_REL,
         "nix/pkgs/tools/stt-voice"],
        capture_output=True, text=True, timeout=30)
    if out.returncode == 0:           # dev host; the sandbox has no .git
        tracked = set(out.stdout.split())
        missing = [rel for rel in critical if rel not in tracked]
        # and every Go test file must be tracked, or the gotests tier in CI
        # runs a package with silently fewer tests than its floor
        for p in sorted(PKG_DIR.glob("src/**/*_test.go")):
            rel = str(p.relative_to(REPO))
            if rel not in tracked:
                missing.append(rel)
        assert not missing, (
            "UNTRACKED (a flake omits untracked files silently — `git add` "
            "them): %s" % missing)
