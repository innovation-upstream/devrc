"""Unit tests for `fans-detail` — the fans pill's left-click cooling view.

HERMETIC. Every test builds a FAKE hwmon tree and points `--hwmon-root` at it,
so no test reads the builder's real /sys/class/hwmon and no verdict depends on
what hardware the runner happens to have.

Two guards here are about the SEAM rather than the rendering, and they are the
ones worth keeping:
  - fans-detail and its `i3status-fans` sibling must be deployed TOGETHER
    (fans-detail loads it by path; deployed alone it renders a banner, not
    the view), and
  - the nix `command =` for the click must actually point at this script.

    run:  pytest scripts/tests/test_fans_detail.py
"""
import importlib.machinery
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
DETAIL = SCRIPTS / "fans-detail"
GRAPHICAL = SCRIPTS.parent / "nix" / "graphical.nix"


def _load(name, modname):
    loader = importlib.machinery.SourceFileLoader(modname, str(SCRIPTS / name))
    spec = importlib.util.spec_from_loader(modname, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


detail = _load("fans-detail", "fans_detail")
fans = _load("i3status-fans", "i3status_fans_for_detail")


@pytest.fixture
def hw(tmp_path):
    """Build a fake hwmon tree. Returns (root, add)."""
    root = tmp_path / "hwmon"
    root.mkdir()
    state = {"n": 0}

    def add(chip, fans_map=None, pwm=None, temps=None):
        d = root / ("hwmon%d" % state["n"])
        state["n"] += 1
        d.mkdir()
        (d / "name").write_text(chip + "\n")
        for i, rpm in (fans_map or {}).items():
            (d / ("fan%d_input" % i)).write_text("%s\n" % rpm)
        for i, raw in (pwm or {}).items():
            (d / ("pwm%d" % i)).write_text("%s\n" % raw)
        for i, (label, milli) in enumerate(temps or [], start=1):
            (d / ("temp%d_input" % i)).write_text("%d\n" % milli)
            if label:
                (d / ("temp%d_label" % i)).write_text(label + "\n")
        return d

    return root, add


@pytest.fixture
def workbench(hw):
    """The real shape of this machine, measured 2026-09-08."""
    root, add = hw
    add("nvme", temps=[("Composite", 27000)])
    add("amdgpu", temps=[("edge", 46000)])
    add("k10temp", temps=[("Tctl", 71000), ("Tccd1", 72000)])
    add("nct6687",
        fans_map={1: 2448, 2: 0, 3: 1650, 4: 0, 5: 0},
        pwm={1: 201, 3: 153},
        temps=[("AMD TSI Addr 98h", 65000), ("Diode 0", 36000),
               ("Thermistor 15", 42000)])
    return root


def _frame(root, color=False):
    return detail.render(detail.find_chips(root), fans, now="", color=color)


# --------------------------------------------------------------------------- #
# pwm scaling — the one unit conversion here, and it is easy to get wrong
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw,pct", [(0, 0), (255, 100), (128, 50), (201, 79), (153, 60)])
def test_pwm_is_scaled_from_RAW_255_to_a_percentage(hw, raw, pct):
    """🔴 sysfs `pwmN` is 0-255, NOT a percentage. Rendering it unscaled shows
    a pump at "201%" — and `sensors` has independently been seen printing >100%
    for pump headers, so a plausible-looking number is not a check."""
    root, add = hw
    d = add("nct6687", pwm={1: raw})
    assert round(detail.read_pwm(d, 1)) == pct


def test_pwm_is_None_when_absent(hw):
    root, add = hw
    d = add("nct6687", fans_map={1: 1000})
    assert detail.read_pwm(d, 1) is None


def test_a_None_pwm_renders_an_empty_gauge_not_a_blank(hw):
    """A missing duty must still occupy its column — a blank reads as a
    rendering bug, an empty gauge reads as 'no data'."""
    assert detail.bar(None) == "░" * 10
    assert detail.bar(0) == "░" * 10
    assert detail.bar(100) == "█" * 10
    assert len(detail.bar(37)) == 10


# --------------------------------------------------------------------------- #
# fan rows — the states that matter
# --------------------------------------------------------------------------- #
def test_the_pump_and_case_fan_are_read_and_labelled(workbench):
    out = _frame(workbench)
    assert "AIO pump" in out and "2448 rpm" in out
    assert "Case fan" in out and "1650 rpm" in out


def test_a_STALLED_pump_is_called_out(hw):
    root, add = hw
    add("nct6687", fans_map={1: 0, 3: 1650}, pwm={1: 201})
    out = _frame(root)
    assert "STALLED" in out and "floor 500" in out


def test_a_stopped_CASE_fan_is_NOT_called_a_stall(hw):
    """It has no floor — a zero-RPM PWM curve is legitimate. Same rule the
    pill follows; the two must not disagree about what is alarming."""
    root, add = hw
    add("nct6687", fans_map={1: 2448, 3: 0}, pwm={1: 201})
    out = _frame(root)
    assert "STALLED" not in out


def test_an_unreadable_tacho_reads_unreadable_not_zero(hw):
    root, add = hw
    add("nct6687", fans_map={3: 1650})          # no fan1 at all
    out = _frame(root)
    pump = [l for l in out.splitlines() if "AIO pump" in l]
    assert pump, out
    assert "unreadable" in pump[0], pump[0]
    # 🔴 Assert on the PUMP's OWN row, not on the whole frame. The first version
    # was `"0 rpm" not in out`, which is satisfied by nothing and broken by
    # everything: the case fan's legitimate "1650 rpm" CONTAINS "0 rpm". A
    # substring assertion over a whole render is a coin flip.
    assert "rpm" not in pump[0], pump[0]
    assert "1650 rpm" in out, "the readable fan should still render"


def test_unconnected_headers_are_summarised_not_listed_as_faults(workbench):
    out = _frame(workbench)
    assert "unconnected header" in out
    assert "fan2" in out and "fan4" in out
    assert "STALLED" not in out


def test_the_named_fans_are_never_counted_as_unconnected(hw):
    """fan3 at 0 is a stopped case fan, not an empty header — listing it as
    unconnected would tell the operator nothing is plugged in."""
    root, add = hw
    d = add("nct6687", fans_map={1: 2448, 3: 0, 4: 0})
    assert detail.unconnected(d, fans) == [4]


# --------------------------------------------------------------------------- #
# temperatures
# --------------------------------------------------------------------------- #
def test_temps_are_millidegrees_and_are_labelled(workbench):
    out = _frame(workbench)
    assert "Tctl" in out and "71.0 C" in out
    assert "edge" in out and "46.0 C" in out
    assert "71000" not in out, "millidegrees leaked into the render"


def test_thermistors_collapse_to_a_range(workbench):
    out = _frame(workbench)
    assert "VRM/chip" in out
    assert re.search(r"\d+-\d+ C", out), out


def test_summarise_ignores_None_and_handles_empty():
    assert detail.summarise([3, 1, None, 2]) == (1, 3)
    assert detail.summarise([]) is None
    assert detail.summarise([None]) is None


# --------------------------------------------------------------------------- #
# the failure modes a launcher must survive
# --------------------------------------------------------------------------- #
def test_a_MISSING_CHIP_says_the_driver_is_not_loaded(hw):
    """🔴 The same meaning the pill's `?` carries, and it must name the fix —
    this is the view someone opens BECAUSE the pill said `?`."""
    root, add = hw
    add("nvme", temps=[("Composite", 27000)])
    out = _frame(root)
    assert "NOT FOUND" in out
    assert "apply-nct6683-module.sh" in out


def test_an_ABSENT_hwmon_root_renders_a_frame_rather_than_raising(tmp_path):
    out = detail.render(detail.find_chips(tmp_path / "nope"), fans, color=False)
    assert out.strip(), "empty frame"
    assert "NOT FOUND" in out


def test_a_MISSING_SIBLING_is_announced_not_silently_empty(workbench):
    """fans-detail reuses i3status-fans' predicate. Deployed without it, the
    view must SAY so — a cooling screen with no fan rows and no explanation is
    indistinguishable from a machine with no fans."""
    out = detail.render(detail.find_chips(workbench), None, color=False)
    assert "sibling" in out.lower()
    assert out.strip()


def test_render_NEVER_returns_empty_for_any_shape(hw):
    root, add = hw
    for chips in ({}, detail.find_chips(root)):
        out = detail.render(chips, fans, color=False)
        assert out.strip(), chips


# --------------------------------------------------------------------------- #
# end-to-end
# --------------------------------------------------------------------------- #
def test_dump_prints_one_frame_and_EXITS(workbench):
    """`--dump` must not enter the watch loop — a test that hangs is worse
    than one that fails."""
    p = subprocess.run([sys.executable, str(DETAIL), "--dump",
                        "--hwmon-root", str(workbench)],
                       capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    assert "AIO pump" in p.stdout and "2448" in p.stdout


def test_bad_arguments_still_render_rather_than_exiting_2(workbench):
    """argparse exits 2 on an unknown flag; a launcher that vanishes is worse
    than one that ignores the flag."""
    p = subprocess.run([sys.executable, str(DETAIL), "--dump", "--nonsense",
                        "--hwmon-root", str(workbench)],
                       capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr


# --------------------------------------------------------------------------- #
# the SEAM guards
# --------------------------------------------------------------------------- #
def test_the_sibling_loads_when_the_script_is_a_SYMLINK_to_a_LONE_store_path(tmp_path):
    """🔴 THE PRODUCTION BUG, REPRODUCED. `home.file` deploys EACH file as its
    OWN /nix/store path, so the deployed script is a symlink to
    `/nix/store/<hash>-hm_fansdetail` — a FILE sitting directly in /nix/store,
    NOT a directory containing anything.

    The first version resolved `__file__` before taking its parent, which made
    the sibling's computed location `/nix/store/i3status-fans`. That does not
    exist, so a correctly-deployed pair rendered the "sibling did not load"
    banner on the real host. Every other test in this file passed, because they
    run from the repo where the two files ARE siblings on disk — the layout the
    bug depends on only exists after a nix switch.

    This fixture is that layout: two lone files in separate directories, and a
    symlink dir that is the only place they are siblings.
    """
    storeish = tmp_path / "store"
    storeish.mkdir()
    # each "store path" is a lone FILE directly in `storeish`, as nix does it
    (storeish / "hash1-hm_fansdetail").write_bytes(DETAIL.read_bytes())
    (storeish / "hash2-hm_i3statusfans").write_bytes((SCRIPTS / "i3status-fans").read_bytes())

    linkdir = tmp_path / "scripts"
    linkdir.mkdir()
    (linkdir / "fans-detail").symlink_to(storeish / "hash1-hm_fansdetail")
    (linkdir / "i3status-fans").symlink_to(storeish / "hash2-hm_i3statusfans")

    root = tmp_path / "hwmon"
    (root / "hwmon0").mkdir(parents=True)
    (root / "hwmon0" / "name").write_text("nct6687\n")
    (root / "hwmon0" / "fan1_input").write_text("2448\n")
    (root / "hwmon0" / "fan3_input").write_text("1650\n")

    p = subprocess.run(
        [sys.executable, str(linkdir / "fans-detail"), "--dump",
         "--hwmon-root", str(root)],
        capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    assert "sibling" not in p.stdout.lower(), (
        "the sibling did not load through the symlink — this is the nix layout, "
        "and it is what the operator actually runs:\n" + p.stdout)
    assert "2448" in p.stdout, p.stdout

    # Positive control: the SAME harness must be able to SEE a missing sibling,
    # or the assertion above passes for a fixture that could never fail.
    (linkdir / "i3status-fans").unlink()
    q = subprocess.run(
        [sys.executable, str(linkdir / "fans-detail"), "--dump",
         "--hwmon-root", str(root)],
        capture_output=True, text=True, timeout=20)
    assert "sibling" in q.stdout.lower(), (
        "removing the sibling changed nothing — the check above is vacuous:\n"
        + q.stdout)


def test_fans_detail_and_its_SIBLING_are_deployed_together():
    """🔴 fans-detail loads `i3status-fans` BY PATH from beside itself. Deploy
    one without the other and the click opens a red banner instead of the
    cooling view — on a host reporting a fully successful switch.

    Same shape as `bar_freshness.py` for the count pills, and the nix comment
    beside the entry claims this test exists, so it must actually check it.
    """
    nix = GRAPHICAL.read_text()
    gates = {}
    for name in ("fans-detail", "i3status-fans"):
        m = re.search(
            r'home\.file\."\.config/i3status-rust/scripts/%s"\s*=\s*(.*)'
            % re.escape(name), nix)
        assert m, "%s has no home.file entry — the click has no target" % name
        gates[name] = m.group(1).strip()
        assert (SCRIPTS / name).exists(), (
            "%s is deployed but ../scripts/%s does not exist (un-`git add`ed? "
            "a flake omits untracked files silently)" % (name, name))
    assert gates["fans-detail"] == gates["i3status-fans"], (
        "fans-detail and its REQUIRED sibling are deployed under DIFFERENT "
        "gates:\n  fans-detail   %s\n  i3status-fans %s"
        % (gates["fans-detail"], gates["i3status-fans"]))


def test_the_fans_pill_click_points_at_THIS_script():
    """🔴 The click used to open btop — a CPU/memory view that answers neither
    question this pill raises. Nothing fed that `command =` to anything, so
    re-pointing it (or a typo) would be invisible to every test."""
    nix = GRAPHICAL.read_text()
    m = re.search(r"fansBlock = \{(.*?)^  \};", nix, re.S | re.M)
    assert m, "fansBlock not found in nix/graphical.nix"
    body = m.group(1)
    click = re.search(r'button = "left"; cmd = "([^"]+)"', body)
    assert click, "fansBlock has no left-click"
    cmd = click.group(1)
    assert "fans-detail" in cmd, (
        "the fans pill's left-click does not open the cooling view: %r" % cmd)
    assert "btop" not in cmd, (
        "the click points at btop again — btop is a CPU/memory view and shows "
        "no fan or pump data at all: %r" % cmd)
