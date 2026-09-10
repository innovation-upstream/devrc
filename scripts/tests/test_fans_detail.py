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
import os
import importlib.machinery
import importlib.util
import re
import subprocess
import sys
import time
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


#: The mapping nix passes. Built through the SIBLING's parser, so these tests
#: exercise the same path production does rather than a hand-built table.
SPECS = [fans.parse_fan(x) for x in detail.DEFAULT_FANS]


def _frame(root, color=False, specs=None):
    return detail.render(detail.find_chips(root), fans,
                         specs=SPECS if specs is None else specs,
                         now="", color=color)


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
    assert detail.unconnected(d, fans, SPECS) == [4]


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


def test_a_temperature_is_NOT_gauged_as_a_percentage():
    """🔴 `bar()` takes a PERCENTAGE and the temps were passing DEGREES into it.
    It happened to look plausible (80 C -> 8/10) while meaning nothing, and it
    silently breaks the moment the scale or width changes. `temp_bar` scales
    30-100 C — the range an operator cares about — so the two disagree."""
    assert detail.temp_bar(30.0) == "░" * 10          # floor: empty
    assert detail.temp_bar(100.0) == "█" * 10         # ceiling: full
    assert detail.temp_bar(65.0) == detail.bar(50.0)  # midpoint of 30-100
    # and it is genuinely different from treating C as %
    assert detail.temp_bar(40.0) != detail.bar(40.0)


def test_temperature_colour_crosses_at_the_thresholds():
    """The view exists to answer "how hot is it" — a number with no colour makes
    the operator do the comparison."""
    assert detail.temp_color(70.0) == detail.CYAN
    assert detail.temp_color(detail.TEMP_WARN) == detail.YELLOW
    assert detail.temp_color(detail.TEMP_CRIT) == detail.RED
    assert detail.temp_color(None) == detail.CYAN
    assert detail.TEMP_WARN < detail.TEMP_CRIT


def test_two_board_sensors_with_the_same_prefix_render_DISTINCTLY(hw):
    """`tlabel[:8]` rendered `AMD TSI Addr 98h` and `…Addr 9Ah` identically, so
    two different sensors read as one."""
    root, add = hw
    add("nct6687", fans_map={1: 2448},
        temps=[("AMD TSI Addr 98h", 82000), ("AMD TSI Addr 9Ah", 61000)])
    out = _frame(root)
    board = [l for l in out.splitlines() if "AMD TSI" in l]
    assert len(board) == 2, out
    # Compare the LABEL region — everything before the reading. Slicing fixed
    # token positions is what made the first version of this assertion compare
    # ['AMD','TSI'] against itself and fail on correct output.
    labels = [l.split(" C ")[0].rsplit(" ", 1)[0].strip() for l in board]
    assert labels[0] != labels[1], (labels, board)
    assert "98" in labels[0] and "9A" in labels[1], labels


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
    out = detail.render(detail.find_chips(tmp_path / "nope"), fans, specs=SPECS, color=False)
    assert out.strip(), "empty frame"
    assert "NOT FOUND" in out


def test_a_MISSING_SIBLING_is_announced_not_silently_empty(workbench):
    """fans-detail reuses i3status-fans' predicate. Deployed without it, the
    view must SAY so — a cooling screen with no fan rows and no explanation is
    indistinguishable from a machine with no fans."""
    out = detail.render(detail.find_chips(workbench), None, specs=SPECS, color=False)
    assert "sibling" in out.lower()
    assert out.strip()


def test_render_NEVER_returns_empty_for_any_shape(hw):
    root, add = hw
    for chips in ({}, detail.find_chips(root)):
        out = detail.render(chips, fans, specs=SPECS, color=False)
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
def test_an_UNDECODABLE_hwmon_name_still_prints_a_frame(tmp_path):
    """🔴 `read_str` caught only OSError while `read_int` caught ValueError too.
    A hwmon `name` that is not valid UTF-8 raises UnicodeDecodeError — a
    ValueError — which escaped `find_chips`, past `render`, into the __main__
    failsafe: **stdout 0 bytes, exit 0**. That breaks `--dump`'s "one frame"
    contract silently, and a caller doing `out=$(fans-detail --dump)` gets an
    empty string with a success status.

    The fixture is the one the sibling's own suite already uses for the pill.
    """
    root = tmp_path / "hwmon"
    (root / "hwmon0").mkdir(parents=True)
    (root / "hwmon0" / "name").write_bytes(b"\xff\xfe bad\n")
    (root / "hwmon1").mkdir()
    (root / "hwmon1" / "name").write_text("nct6687\n")
    (root / "hwmon1" / "fan1_input").write_text("2448\n")

    p = subprocess.run(
        [sys.executable, str(DETAIL), "--dump", "--hwmon-root", str(root),
         "--fan", "AIO pump=1:500"],
        capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip(), "stdout was EMPTY — the frame contract broke"
    assert "2448" in p.stdout, p.stdout
    assert "Traceback" not in p.stderr, p.stderr


def test_render_does_not_raise_on_an_undecodable_name(tmp_path):
    """The pure half of the same defect: `render`'s docstring says it never
    raises, so the undecodable device must be skipped, not propagated."""
    root = tmp_path / "hwmon"
    (root / "hwmon0").mkdir(parents=True)
    (root / "hwmon0" / "name").write_bytes(b"\xff\xfe\n")
    out = detail.render(detail.find_chips(root), fans, specs=[], color=False)
    assert out.strip()


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


def _nix_shaped(tmp_path, detail_bytes=None):
    """The DEPLOYED layout: two lone `store` FILES plus a symlink dir that is
    the only place they are siblings. Returns (linkdir, hwmon_root).

    Same fixture as the symlink test above, factored out so the bytecode guard
    and its positive control run through an identical harness.
    """
    storeish = tmp_path / "store"
    storeish.mkdir(parents=True)
    (storeish / "hash1-hm_fansdetail").write_bytes(
        DETAIL.read_bytes() if detail_bytes is None else detail_bytes)
    (storeish / "hash2-hm_i3statusfans").write_bytes(
        (SCRIPTS / "i3status-fans").read_bytes())

    linkdir = tmp_path / "scripts"          # ~/.config/i3status-rust/scripts
    linkdir.mkdir(parents=True)
    (linkdir / "fans-detail").symlink_to(storeish / "hash1-hm_fansdetail")
    (linkdir / "i3status-fans").symlink_to(storeish / "hash2-hm_i3statusfans")

    root = tmp_path / "hwmon"
    (root / "hwmon0").mkdir(parents=True)
    (root / "hwmon0" / "name").write_text("nct6687\n")
    (root / "hwmon0" / "fan1_input").write_text("2448\n")
    return linkdir, root


def _run_deployed(linkdir, root):
    env = dict(os.environ)
    # 🔴 The harness must NOT silently supply the very suppression under test:
    # inheriting PYTHONDONTWRITEBYTECODE would make the positive control below
    # produce no .pyc either, and the guard would pass with the flag deleted.
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    return subprocess.run(
        [sys.executable, str(linkdir / "fans-detail"), "--dump",
         "--hwmon-root", str(root)],
        capture_output=True, text=True, timeout=20, env=env)


def _pycs(linkdir):
    return sorted(p.name for p in linkdir.glob("__pycache__/*.pyc"))


def test_loading_the_sibling_writes_NO_pyc_into_the_operators_config_dir(tmp_path):
    """🔴 A `.pyc` here would pin OLD code FOREVER, not merely litter.

    `_load_sibling` loads through the SYMLINK in
    ~/.config/i3status-rust/scripts, so the bytecode cache is written into that
    real, writable config dir (/nix/store itself is read-only). CPython
    validates a cached `.pyc` on the source's **mtime-in-whole-seconds + size**
    — and every /nix/store path has **mtime=1** (measured 2026-09-09:
    `/nix/store/4lmg…-hm_i3statusfans mtime=1 size=11916`). The mtime half can
    therefore never change, so any later redeploy landing on the same byte size
    is invisible to the check and the stale cache is served instead.

    Measured on the live host, not argued: that config dir already holds three
    July `.pyc` files, two of which STILL validate against today's deployed
    source — headers `src_mtime=1`, sizes 6571 / 6930, equal to the
    `disk-detail` / `i3status-notifs` store paths deployed now. The mechanism
    was also reproduced directly on 3.12.14: edit a module's contents, keep the
    size, reset mtime to 1, re-import — the OLD value comes back. Those three
    predate this script; the point of the guard is that fans-detail never joins
    them.
    """
    linkdir, root = _nix_shaped(tmp_path)
    p = _run_deployed(linkdir, root)
    assert p.returncode == 0, p.stderr
    assert "2448" in p.stdout, p.stdout            # the sibling really loaded
    assert _pycs(linkdir) == [], (
        "fans-detail wrote a bytecode cache into the deployed scripts dir: %s\n"
        "Every /nix/store source has mtime=1, and CPython validates a .pyc on "
        "mtime-seconds + size, so this file outlives every same-size redeploy "
        "and the operator silently keeps running the OLD sibling. "
        "`sys.dont_write_bytecode` in _load_sibling is what prevents it."
        % _pycs(linkdir))

    # 🔴 POSITIVE CONTROL — the assertion above is vacuous unless this harness
    # can actually SEE a .pyc being written. Strip the suppression from a copy
    # of the script and the same run must produce one.
    assign = re.compile(rb"(?m)^([ \t]*)sys\.dont_write_bytecode[ \t]*=.*\n")
    original = DETAIL.read_bytes()
    assert assign.search(original), (
        "no `sys.dont_write_bytecode = …` line to strip — this control is "
        "mutating something that is no longer there")
    # `pass`, not deletion: the restore lives in a `finally:` whose body would
    # otherwise become empty and the copy would die of SyntaxError, which a
    # careless reader could mistake for "no .pyc, guard works".
    unguarded = assign.sub(lambda m: m.group(1) + b"pass\n", original)
    assert not assign.search(unguarded), "the strip missed a line"
    ctl_dir, ctl_root = _nix_shaped(tmp_path / "ctl", detail_bytes=unguarded)
    q = _run_deployed(ctl_dir, ctl_root)
    assert q.returncode == 0, q.stderr
    assert "2448" in q.stdout, q.stdout
    assert _pycs(ctl_dir), (
        "POSITIVE CONTROL FAILED: even without `sys.dont_write_bytecode` no "
        ".pyc appeared, so the guard above proves nothing. Check that the "
        "child is not inheriting -B / PYTHONDONTWRITEBYTECODE.\n" + q.stdout)


def test_load_sibling_holds_the_flag_ACROSS_the_import_then_RESTORES_it(monkeypatch):
    """Two claims about a PROCESS-GLOBAL, and the ORDER between them.

    `sys.dont_write_bytecode` is interpreter-wide. Setting it and walking away
    is inert today (nothing calls `main()` in-process) but leaves a trap for the
    first in-process caller: every later import in that interpreter, the test
    runner's own included, silently stops caching. So it must be restored.

    And the restore must land strictly AFTER `exec_module` — restoring one line
    too early hands the write straight back to the import the flag exists to
    cover, which is why this spies on the flag AT `exec_module` rather than
    merely reading it at the end. Checking for a `.pyc` beside the repo copy
    could NOT tell you this: the module-level `_load(...)` at the top of this
    file already wrote `scripts/__pycache__/i3status-fans…pyc` during
    collection, so an "is a new file there" assertion is answered before this
    test starts. The nix-shaped subprocess test above is what covers the file.
    """
    seen = []
    real_exec = importlib.machinery.SourceFileLoader.exec_module

    def spy(self, module):
        seen.append(bool(sys.dont_write_bytecode))
        return real_exec(self, module)

    monkeypatch.setattr(importlib.machinery.SourceFileLoader,
                        "exec_module", spy)

    prev = sys.dont_write_bytecode
    try:
        for start in (False, True):
            seen.clear()
            sys.dont_write_bytecode = start
            mod = detail._load_sibling()
            assert mod is not None, "the sibling did not load from the repo tree"
            assert seen == [True], (
                "entered with dont_write_bytecode=%r and the sibling's "
                "exec_module ran with %r — the flag must be TRUE for the "
                "import itself, or the .pyc is written anyway" % (start, seen))
            assert bool(sys.dont_write_bytecode) == start, (
                "_load_sibling leaked dont_write_bytecode process-globally: "
                "entered %r, left %r" % (start, sys.dont_write_bytecode))
    finally:
        sys.dont_write_bytecode = prev


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


def _pty_trial(key, hwmon_root, seconds=6.0):
    """Drive the live watch loop under a real pty. Returns True if it exited."""
    import pty as _pty
    import signal as _sig
    m, s = _pty.openpty()
    p = subprocess.Popen(
        [sys.executable, str(DETAIL), "--hwmon-root", str(hwmon_root),
         "--interval", "1", "--fan", "AIO pump=1:500"],
        stdin=s, stdout=s, stderr=s, close_fds=True)
    os.close(s)
    time.sleep(1.5)                       # let it draw at least one frame
    if key:
        os.write(m, key)
    t0, exited = time.time(), False
    while time.time() - t0 < seconds:
        if p.poll() is not None:
            exited = True
            break
        time.sleep(0.1)
    if not exited:
        p.send_signal(_sig.SIGKILL)
    p.wait(timeout=10)
    os.close(m)
    return exited


@pytest.mark.parametrize("key,label,should_exit", [
    (b"q", "q", True),
    (b"Q", "Q", True),
    (b"\x1b", "esc", True),
    (b"\x03", "ctrl-c", True),
    (b"x", "an unrelated key", False),     # negative control
    (None, "no key at all", False),        # negative control
])
def test_the_FOOTER_KEYS_actually_close_the_view(key, label, should_exit, hw):
    """🔴 THE FOOTER IS A CLAIM. It advertised `q / Ctrl-C to close` while the
    watch loop read stdin NOWHERE — measured under a pty, `q` left it running
    past 8 s, so the keypress echoed, got wiped by the next 2 s redraw, and the
    view read as HUNG.

    Ctrl-C needed its own fix: cbreak leaves ISIG on, so the tty driver turns
    `\\x03` into a SIGINT that only arrives if this process is in the tty's
    FOREGROUND PROCESS GROUP — true under alacritty, not true under a bare pty,
    where the byte was swallowed and the promise was false. ISIG is now cleared
    so the byte is delivered and handled identically everywhere.

    The two negative controls are what stop this becoming "any key quits",
    which would make the assertions above pass while the view was unusable.
    """
    root, add = hw
    add("nct6687", fans_map={1: 2448}, pwm={1: 201})
    assert _pty_trial(key, root) is should_exit, label


def test_the_pill_and_the_VIEW_get_the_SAME_fan_mapping():
    """🔴 THE DRIFT GUARD. An audit measured this exact hole: `fans-detail`
    carried its own `KNOWN_FANS = [(1, "AIO pump", 500), (3, "Case fan", None)]`
    while nix passed `--fan pump=1:500 --fan case=3` to the PILL alone.

    Moving the pump to another header and updating only the nix line left
    **84 of 84 tests green**, and produced:

        PILL : {"text": "2448·1650", "state": "Idle"}
        VIEW :   AIO pump   ?  ░░░░░░░░░░  ?%  unreadable

    The pill's own seam guard cannot catch it — it deliberately asserts STATE
    not spelling ("re-ordering or renaming a fan is free"), which is correct for
    the pill. So the mapping is now ONE nix binding passed verbatim to both, and
    this asserts they are literally the same string.
    """
    nix = GRAPHICAL.read_text()
    m = re.search(r"fansBlock = \{(.*?)^  \};", nix, re.S | re.M)
    assert m, "fansBlock not found"
    body = m.group(1)
    cmd = re.search(r'command = "([^"]+)"', body)
    click = re.search(r'button = "left"; cmd = "([^"]+)"', body)
    assert cmd and click, body

    binding = re.search(r'fanArgs = "([^"]+)"', nix)
    assert binding, "no single-source `fanArgs` binding in nix/graphical.nix"

    def fan_args(s):
        # Resolve the binding first: the whole point of the fix is that neither
        # command spells the mapping itself, so a raw scan finds nothing.
        return re.findall(r"--fan\s+('[^']*'|\S+)",
                          s.replace("${fanArgs}", binding.group(1)))

    pill, view = fan_args(cmd.group(1)), fan_args(click.group(1))
    assert pill, "the pill resolves to no --fan arguments"
    assert pill == view, (
        "the pill and the cooling view disagree about the fan mapping — one of "
        "them will render a fan the other cannot see:\n  pill %r\n  view %r"
        % (pill, view))

    # 🔴 And they must come from the SAME binding, not two equal literals that
    # drift on the next edit. Both commands must interpolate it.
    assert "${fanArgs}" in cmd.group(1), (
        "the pill spells its own fan mapping instead of using `fanArgs`: %r"
        % cmd.group(1))
    assert "${fanArgs}" in click.group(1), (
        "the cooling view spells its own fan mapping instead of using "
        "`fanArgs`: %r" % click.group(1))


def test_the_view_parses_the_fan_args_nix_actually_passes():
    """The strings agreeing is not enough: they must also be VALID. A spec the
    sibling rejects (floor 0, truncated `:`) would silently drop that fan's row
    from the view while the pill refuses to start at all."""
    nix = GRAPHICAL.read_text()
    m = re.search(r'fanArgs = "([^"]+)"', nix)
    assert m, "no single-source `fanArgs` binding in nix/graphical.nix"
    specs = re.findall(r"--fan\s+'([^']*)'|--fan\s+(\S+)", m.group(1))
    flat = [a or b for a, b in specs]
    assert flat, m.group(1)
    parsed = [fans.parse_fan(s) for s in flat]        # raises on a bad spec
    armed = [p for p in parsed if p.floor is not None]
    assert armed, "no fan in the shared mapping carries a floor — nothing alarms"
    assert parsed[0].index == 1, "the pump is expected on fan1 first"


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
