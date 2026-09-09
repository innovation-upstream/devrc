"""Unit tests for the `i3status-fans` block (AIO pump + case fan RPM).

All OFFLINE and HERMETIC: every test that touches sysfs points the block at a
FAKE `--hwmon-root` built by a fixture. None of them reads the real
/sys/class/hwmon, so the verdict does not depend on what hardware the runner
happens to have — which is how a test ends up asserting whatever the workbench
reports that minute.

These are NEW-FEATURE coverage plus INVARIANT GUARDS, not regression tests: the
block did not exist before, so there is no pre-change tree on which they could
be shown to fail. Two of them pin decisions that a plausible rewrite would
silently reverse, and those are called out in their own docstrings:
  - the state ordering (an alarm outranks an unreadable sibling), and
  - always-visible (this pill has NO empty-text state, unlike every count pill).

The script is extensionless, so it is loaded via SourceFileLoader — same shape
as test_bar_status.py.

    run:  pytest scripts/tests/test_i3status_fans.py
"""
import importlib.machinery
import importlib.util
import json
import math
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
FANS_SCRIPT = SCRIPTS / "i3status-fans"


def _load(name, modname):
    loader = importlib.machinery.SourceFileLoader(modname, str(SCRIPTS / name))
    spec = importlib.util.spec_from_loader(modname, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


fans = _load("i3status-fans", "i3status_fans")


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
@pytest.fixture
def hwmon(tmp_path):
    """Build a fake /sys/class/hwmon and return (root, make) .

    `make(dirname, chipname, {index: rpm})` writes one hwmon device. An rpm of
    None writes NO fan file for that index (an absent tacho), and a string is
    written verbatim (a malformed one).
    """
    root = tmp_path / "hwmon-root"
    root.mkdir()

    def make(dirname, chipname, fans_map=None):
        d = root / dirname
        d.mkdir()
        (d / "name").write_text(chipname + "\n")
        for idx, rpm in (fans_map or {}).items():
            if rpm is None:
                continue
            (d / ("fan%d_input" % idx)).write_text("%s\n" % rpm)
        return d

    return root, make


@pytest.fixture
def workbench(hwmon):
    """The real shape of the workbench: the chip is NOT hwmon0.

    Ten unrelated devices probe first (nvme/spd5118/k10temp/amdgpu), and the
    NCT6687D landed on hwmon11. The decoys are the point — see
    `test_find_chip_locates_BY_NAME_not_by_number`.
    """
    root, make = hwmon
    make("hwmon0", "nvme")
    make("hwmon1", "nvme", {1: 4321})       # a decoy that HAS a fan1_input
    make("hwmon6", "k10temp")
    make("hwmon10", "amdgpu", {1: 999})     # another decoy with a fan1_input
    make("hwmon11", "nct6687", {1: 2660, 2: 0, 3: 1736})
    return root


# --------------------------------------------------------------------------
# parse_fan
# --------------------------------------------------------------------------
def test_parse_fan_with_a_floor():
    got = fans.parse_fan("pump=1:500")
    assert got == fans.FanSpec("pump", 1, 500.0)
    assert got.floor == 500.0


def test_parse_fan_WITHOUT_a_floor_is_display_only():
    """🔴 The absence of a floor is MEANINGFUL, not a default of zero.

    A fan with `floor is None` can never raise Critical. Reading the missing
    floor as 0 would look identical in every healthy state and differ only in
    the one that matters — a stopped display-only fan.
    """
    got = fans.parse_fan("case=3")
    assert got == fans.FanSpec("case", 3, None)
    assert got.floor is None


@pytest.mark.parametrize("spec,needle", [
    ("pump", "LABEL=INDEX"),          # no '='
    ("=1:500", "empty label"),
    ("  =1", "empty label"),
    ("pump=x", "not an integer"),
    ("pump=0", "1-based"),            # hwmon fan inputs start at 1
    ("pump=-2", "1-based"),
    ("pump=1:", "not a number"),      # truncated floor, NOT read as absent
    ("pump=1:abc", "not a number"),
    ("pump=1:nan", "finite"),
    ("pump=1:inf", "finite"),
    ("pump=1:-1", "can never be crossed"),
])
def test_parse_fan_rejects(spec, needle):
    """Each rejection carries its OWN message, so a mutant that deletes one
    guard cannot be killed by a different guard's error (RULES.md)."""
    with pytest.raises(ValueError) as e:
        fans.parse_fan(spec)
    assert needle in str(e.value), str(e.value)


def test_a_truncated_floor_is_not_silently_read_as_absent():
    """`pump=1:` is the shape a shell mangling produces, and reading it as "no
    floor" would DISARM the pump alarm while the argument still looks configured.
    Pinned separately from the parametrize above because it is the only
    rejection whose failure mode is silent rather than loud."""
    with pytest.raises(ValueError):
        fans.parse_fan("pump=1:")
    # control: the same spec WITHOUT the colon is the legitimate display-only
    # form and must still parse — otherwise this guard is just "reject more".
    assert fans.parse_fan("pump=1").floor is None


# --------------------------------------------------------------------------
# find_chip
# --------------------------------------------------------------------------
def test_find_chip_locates_BY_NAME_not_by_number(workbench):
    """🔴 hwmon numbering is not stable across boots, and the decoys make that
    testable: hwmon1 and hwmon10 both carry a `fan1_input`, so a lookup that
    took the first directory, or a hardcoded index, would return a number here
    and be wrong in a way no healthy-path assertion could see."""
    d = fans.find_chip("nct6687", workbench)
    assert d is not None
    assert (d / "name").read_text().strip() == "nct6687"
    assert fans.read_rpm(d, 1) == 2660          # the chip's fan, not a decoy's
    assert fans.read_rpm(d, 1) not in (4321, 999)


def test_find_chip_returns_None_when_the_driver_is_not_loaded(hwmon):
    """The reboot case: nct6683 was never persisted, so no hwmon device carries
    that name. Must be None (-> a visible `?`), never an exception."""
    root, make = hwmon
    make("hwmon0", "nvme")
    make("hwmon1", "k10temp")
    assert fans.find_chip("nct6687", root) is None


def test_find_chip_survives_a_device_with_no_readable_name(hwmon):
    root, make = hwmon
    (root / "hwmon0").mkdir()                   # no `name` file at all
    make("hwmon1", "nct6687", {1: 1200})
    assert fans.find_chip("nct6687", root) is not None


def test_find_chip_returns_None_for_a_missing_root(tmp_path):
    assert fans.find_chip("nct6687", tmp_path / "nope") is None


@pytest.mark.parametrize("chip", ["", "*", "../../etc", "nct6687/../x", None])
def test_find_chip_rejects_an_implausible_name(chip):
    """🔴 THIS IS DEFENCE-IN-DEPTH, AND THIS DOCSTRING USED TO OVERCLAIM IT.

    It said "the name is pasted into a glob; a wildcard would make the lookup
    match whatever happens to be there", which is FALSE: `find_chip` globs the
    literal `"hwmon*"` and compares the name with `==`, so `*` matches no `name`
    file and finds nothing. MEASURED with the guard removed — `*`, `../../etc`,
    `nct6687/../x`, `''` and `..` all render the identical `?` pill, against a
    positive control (`nct6687` -> `2660`) showing the harness could see a
    difference.

    What the guard is actually worth: a typo becomes a loud ValueError instead
    of a silent `?`, and a future refactor that DOES interpolate the name cannot
    quietly become a sink. That is worth keeping and worth pinning; it is not
    the injection fix the old wording advertised.
    """
    with pytest.raises(ValueError):
        fans.find_chip(chip, "/sys/class/hwmon")


# --------------------------------------------------------------------------
# read_rpm
# --------------------------------------------------------------------------
def test_read_rpm_reads_an_integer(workbench):
    d = fans.find_chip("nct6687", workbench)
    assert fans.read_rpm(d, 1) == 2660
    assert fans.read_rpm(d, 3) == 1736
    assert fans.read_rpm(d, 2) == 0             # connected-to-nothing, a real 0


def test_read_rpm_is_None_for_an_absent_tacho(workbench):
    d = fans.find_chip("nct6687", workbench)
    assert fans.read_rpm(d, 7) is None


def test_read_rpm_is_None_for_a_malformed_or_negative_value(hwmon):
    root, make = hwmon
    make("hwmon0", "nct6687", {1: "garbage", 2: "-5", 3: ""})
    d = fans.find_chip("nct6687", root)
    assert fans.read_rpm(d, 1) is None
    assert fans.read_rpm(d, 2) is None          # a tacho cannot be negative
    assert fans.read_rpm(d, 3) is None


def test_read_rpm_is_None_when_the_chip_is_absent():
    assert fans.read_rpm(None, 1) is None


# --------------------------------------------------------------------------
# render
# --------------------------------------------------------------------------
PUMP = fans.FanSpec("pump", 1, 500.0)
CASE = fans.FanSpec("case", 3, None)


def test_render_healthy_is_neutral_and_shows_both_numbers():
    out = fans.render([(PUMP, 2660), (CASE, 1736)])
    assert out == {"icon": "refresh", "text": "2660·1736",
                   "short_text": "2660", "state": "Idle"}


def test_render_marks_the_STALLED_fan_and_goes_Critical():
    out = fans.render([(PUMP, 0), (CASE, 1736)])
    assert out["state"] == "Critical"
    assert out["text"] == "!0·1736"


def test_render_alarms_below_the_floor_not_only_at_zero():
    """A dying pump slows before it stops; the floor is the point of the floor."""
    assert fans.render([(PUMP, 499), (CASE, 1736)])["state"] == "Critical"
    assert fans.render([(PUMP, 500), (CASE, 1736)])["state"] == "Idle"


def test_a_display_only_fan_at_zero_does_NOT_alarm():
    """🔴 The false-alarm guard. A case fan on a zero-RPM PWM curve legitimately
    stops when idle, and fan2/fan4-10 read a permanent 0 because nothing is
    plugged into them. A blanket floor would make this pill cry wolf, which is
    how a real alarm gets ignored."""
    out = fans.render([(PUMP, 2660), (CASE, 0)])
    assert out["state"] == "Idle"
    assert out["text"] == "2660·0"
    assert "!" not in out["text"]


def test_render_one_unreadable_fan_is_a_Warning_with_a_per_fan_question_mark():
    out = fans.render([(PUMP, 2660), (CASE, None)])
    assert out == {"icon": "refresh", "text": "2660·?",
                   "short_text": "2660", "state": "Warning"}


def test_render_an_ALARM_OUTRANKS_an_unreadable_sibling():
    """🔴 THE ORDERING GUARD, and the mutation-sensitive assertion in this file.

    Swapping the two branches in `render` — checking `unknown` before `alarm` —
    passes every other test here, and downgrades a STOPPED PUMP to a yellow
    Warning because some unrelated tacho went unreadable in the same tick. The
    bar's house rule is that an outage may make a reading less trusted but may
    never make a recorded alarm quieter.
    """
    out = fans.render([(PUMP, 0), (CASE, None)])
    assert out["state"] == "Critical", out
    assert out["text"] == "!0·?"


def test_render_all_unreadable_is_a_bare_question_mark():
    """The driver-not-loaded state. Not `?·?` — one pill, one unknown."""
    out = fans.render([(PUMP, None), (CASE, None)])
    assert out == {"icon": "refresh", "text": "?", "short_text": "?",
                   "state": "Warning"}


def test_render_with_no_fans_configured_says_it_cannot_tell():
    """A block rendering nothing forever is indistinguishable from a healthy
    one, so an empty config must be loud rather than blank."""
    out = fans.render([])
    assert out["text"] == "?" and out["state"] == "Warning"


@pytest.mark.parametrize("readings", [
    [(PUMP, 2660), (CASE, 1736)],       # healthy
    [(PUMP, 0), (CASE, 1736)],          # stalled
    [(PUMP, 2660), (CASE, None)],       # partially unreadable
    [(PUMP, None), (CASE, None)],       # chip absent
    [],                                 # misconfigured
])
def test_this_pill_is_ALWAYS_VISIBLE(readings):
    """🔴 THE DESIGN GUARD. Every count pill on this bar hides at zero; this one
    must not, in ANY state. A cooler pill that is invisible while healthy is
    invisible in exactly the state it exists to certify, and someone porting the
    hide-at-zero idiom across would reintroduce that silently — the block would
    look correct and the bar would look normal.

    Compare `i3status-load`, whose healthy render is `{"text": "", ...}` with no
    icon at all. Nothing here may ever produce that.
    """
    out = fans.render(readings)
    assert out.get("text"), out
    assert out.get("icon") == "refresh", out
    assert out.get("state") in ("Idle", "Warning", "Critical"), out


# --------------------------------------------------------------------------
# main() + the last-resort handler
# --------------------------------------------------------------------------
def test_main_emits_valid_block_json(workbench, capsys):
    rc = fans.main(["--chip", "nct6687", "--hwmon-root", str(workbench),
                    "--fan", "pump=1:500", "--fan", "case=3"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"icon": "refresh", "text": "2660·1736",
                   "short_text": "2660", "state": "Idle"}


def test_main_renders_the_unknown_pill_when_the_driver_is_absent(hwmon, capsys):
    root, make = hwmon
    make("hwmon0", "nvme")
    rc = fans.main(["--chip", "nct6687", "--hwmon-root", str(root),
                    "--fan", "pump=1:500"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["text"] == "?"


def test_main_converts_argparses_exit_into_a_ValueError():
    """argparse exits 2 on a bad value, which would skip the `?` pill entirely
    and leave the bar with NO block. It must become an ordinary exception so the
    `__main__` handler can render the pill."""
    with pytest.raises(ValueError):
        fans.main(["--fan"])            # missing its value


@pytest.mark.parametrize("argv", [
    ["--fan", "pump=1:nan"],
    ["--fan", "pump=0"],
    ["--chip", "*"],
    ["--fan"],
])
def test_the_script_ALWAYS_prints_a_visible_pill_and_exits_0(argv):
    """End-to-end through `__main__`: whatever the failure, i3status-rust gets
    one line of valid JSON showing a `?`, and a zero exit. A non-zero exit or an
    empty stdout is a block that vanishes from the bar."""
    p = subprocess.run([sys.executable, str(FANS_SCRIPT)] + argv,
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out == {"icon": "refresh", "text": "?", "short_text": "?",
                   "state": "Warning"}


def test_the_positive_control_for_the_test_above(workbench):
    """🔴 The guard above asserts a `?` on bad input. Run alone it cannot tell
    "the fallback works" from "this script prints `?` no matter what" — so drive
    the SAME entry point with GOOD input through a real subprocess and watch the
    output move off the constant."""
    p = subprocess.run(
        [sys.executable, str(FANS_SCRIPT),
         "--chip", "nct6687", "--hwmon-root", str(workbench),
         "--fan", "pump=1:500", "--fan", "case=3"],
        capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["text"] == "2660·1736"


def test_a_CRASH_prints_a_traceback_instead_of_impersonating_a_missing_driver(tmp_path):
    """🔴 This pill's `?` is DEFINED — here, in the module docstring and in
    nix/graphical.nix — as "the chip is absent, i.e. nct6683 is not loaded". A
    crash renders the SAME `?`, so without a traceback any future defect
    impersonates a missing driver: it reads as a known documented state, sends
    the operator to the sudo script, and leaves nothing to contradict it.

    The trigger is synthetic on purpose (real hwmon `name` files are kernel
    ASCII): a `name` that is not valid UTF-8 makes `read_text()` raise
    UnicodeDecodeError — a ValueError, NOT the OSError `find_chip` catches.
    What is not synthetic is the requirement: stdout stays the pill, stderr
    carries the diagnosis, exit stays 0.
    """
    root = tmp_path / "hwmon-root"
    (root / "hwmon0").mkdir(parents=True)
    (root / "hwmon0" / "name").write_bytes(b"\xff\xfe not utf8\n")
    p = subprocess.run(
        [sys.executable, str(FANS_SCRIPT), "--hwmon-root", str(root),
         "--fan", "pump=1:500"],
        capture_output=True, text=True, timeout=30)

    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout) == {"icon": "refresh", "text": "?",
                                    "short_text": "?", "state": "Warning"}
    assert "Traceback" in p.stderr, (
        "a crash rendered the driver-not-loaded pill with NO diagnosis: "
        "stderr was %r" % (p.stderr,))
    assert "UnicodeDecodeError" in p.stderr, p.stderr


def test_the_ORDINARY_unknown_pill_stays_SILENT_on_stderr(tmp_path):
    """🔴 The control for the test above, and what stops it degenerating into
    "always print something". A genuinely absent chip is a NORMAL state, not an
    error: it must render the same pill with an EMPTY stderr, so the traceback
    remains a real signal rather than noise every 30 seconds."""
    root = tmp_path / "hwmon-root"
    (root / "hwmon0").mkdir(parents=True)
    (root / "hwmon0" / "name").write_text("nvme\n")
    p = subprocess.run(
        [sys.executable, str(FANS_SCRIPT), "--hwmon-root", str(root),
         "--fan", "pump=1:500"],
        capture_output=True, text=True, timeout=30)

    assert p.returncode == 0
    assert json.loads(p.stdout)["text"] == "?"
    assert p.stderr == "", (
        "the ordinary chip-absent path wrote to stderr — the traceback stops "
        "being a signal if it fires every tick: %r" % (p.stderr,))


def test_the_icon_is_a_key_the_bar_test_allowlists():
    """Cross-file pin: `test_bar_status.py` owns the MEASURED allowlist of real
    material-nf keys. An icon that is not one renders the whole pill as a red
    `Failed to render full text`, so this block's constant must be on it."""
    src = (SCRIPTS / "tests" / "test_bar_status.py").read_text()
    assert '"%s"' % fans.ICON in src.split("_KNOWN_GOOD_ICONS")[1].split("}")[0]


def test_the_module_declares_no_infinite_or_nan_floor_path():
    """Belt-and-braces on the two values that silently disable a threshold."""
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError):
            fans.parse_fan("pump=1:%r" % bad)


# --------------------------------------------------------------------------
# the floor-of-zero hole, and the nix seam that would have hidden it
# --------------------------------------------------------------------------
@pytest.mark.parametrize("zero", ["0", "0.0", "-0", "0e0"])
def test_a_floor_of_ZERO_is_REFUSED_because_it_can_never_fire(zero):
    """🔴 `read_rpm` maps every negative reading to None, so `rpm < 0` is
    UNREACHABLE: a floor of exactly 0 parses cleanly, looks configured, and can
    never alarm — a stopped pump renders `0` in neutral Idle forever. The
    original guard was `floor < 0`, which left this open by one.
    """
    with pytest.raises(ValueError) as e:
        fans.parse_fan("pump=1:%s" % zero)
    assert "can never be crossed" in str(e.value)


def test_the_smallest_ACCEPTED_floor_really_does_alarm_at_zero_rpm():
    """🔴 The positive control for the refusal above, and the thing that makes
    it a boundary rather than "reject more". Without it, a guard rejecting every
    floor would pass the test above while disabling the feature."""
    spec = fans.parse_fan("pump=1:0.5")
    assert spec.floor == 0.5
    assert fans.render([(spec, 0)])["state"] == "Critical"
    assert fans.render([(spec, 1)])["state"] == "Idle"


def _nix_fan_args():
    """The `--fan …` arguments nix actually passes, read out of graphical.nix."""
    nix = (SCRIPTS.parent / "nix" / "graphical.nix").read_text()
    m = re.search(r'command = "\$\{scriptsDir\}/i3status-fans([^"]*)"', nix)
    assert m, "fansBlock's `command =` line not found in nix/graphical.nix"
    return m.group(1).split()


def test_the_NIX_COMMAND_LINE_parses_and_arms_the_pump_alarm():
    """🔴 THE SEAM GUARD. Nothing fed that `command =` literal to `parse_fan`,
    so a one-character typo in nix shipped a broken pill with a fully green
    suite. MEASURED on the merged tree, mutating ONLY that string:

      `--fan pump=1:`  -> 529/529 green, and the live pill is a permanent
                          `?`/Warning while the pump turns at 2660 RPM.
      `--fan pump=1:0` -> 529/529 green, and the alarm can never fire.

    The precedent is in the same nix file: `test_the_load_pill_threshold_
    MATCHES_cpu_monitors` pins `loadBlock`'s `command =` STRING for exactly this
    reason. This asserts the STATE the arguments produce, not their spelling, so
    re-ordering or renaming a fan is free and disarming one is not.
    """
    args = _nix_fan_args()
    specs = [fans.parse_fan(a) for i, a in enumerate(args)
             if i and args[i - 1] == "--fan"]
    assert specs, "nix passes no --fan arguments at all"
    # At least one fan must be able to raise Critical, or the pill is decorative.
    armed = [s for s in specs if s.floor is not None]
    assert armed, (
        "NO fan in nix/graphical.nix carries a floor — the pump alarm is "
        "disarmed and the pill can only ever render numbers: %r" % (args,))
    # And that armed fan must actually alarm on a stopped tacho.
    for s in armed:
        assert fans.render([(s, 0)])["state"] == "Critical", s
        assert s.floor > 0, s


def test_the_nix_command_line_names_the_pump_first():
    """`short_text` keeps the FIRST fan only, so the bar drops the pump when
    space is tight if the order is ever swapped."""
    args = _nix_fan_args()
    first = [a for i, a in enumerate(args) if i and args[i - 1] == "--fan"][0]
    assert fans.parse_fan(first).label == "pump", first
