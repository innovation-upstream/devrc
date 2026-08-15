"""Behavioural tests for `standup.sh local` — the host-health section.

Everything runs against a STUB `systemctl` on PATH. Nothing here touches the
real user manager, ~/workspace, GitHub or any cluster.

WHY THIS SUITE EXISTS
---------------------
This section is a PORT. It was `render_local_health` in `scripts/agent-ops`, the
mission-control TUI, and it was the one panel of that dashboard with no other
owner when the TUI was retired — every other panel already lived in
`session-manager`, `standup` itself, `/initiative-scan` or a bar pill. A port is
where behaviour quietly goes missing, so the cases below pin the distinctions
the original drew rather than merely asserting the section prints something:

  * absent (not installed on this host) is NOT unhealthy — it is the normal
    state of the workbench-only units on the laptop, and collapsing it into
    "failed" would make the laptop permanently red, which trains you to ignore
    the section. This is the distinction that costs the most if it is lost.
  * never-run is NOT "0s ago". A unit whose ExecMain timestamp is 0 has never
    reached that point; printing an age there is the same class of lie as the
    clamp `window_activity_age` refuses to make.
  * a non-success `Result` is unhealthy even while `ActiveState` reads inactive
    — which is exactly what a oneshot timer job looks like after it fails.

🔴 THE CONTROL PAIR IS THE LOAD-BEARING TEST.
`test_control_pair_the_unhealthy_count_MOVES` runs the SAME code over a healthy
fixture and a broken one and asserts the number moves 0 -> 2. A section that
reports "0 failed" is indistinguishable from a section wired to nothing — the
stub could be answering nothing at all, the awk could match nothing, the scope
could never run — and every one of those reads as a clean bill of health. A
standalone "it printed 0" proves none of it.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from testlib.mockbin import write_exec  # noqa: E402

STANDUP = Path(os.environ.get(
    "STANDUP_SH", REPO_ROOT / "claude" / "skills" / "standup" / "standup.sh"))

pytestmark = pytest.mark.skipif(
    not shutil.which("bash"), reason="needs bash on PATH")

# The stub `systemctl`. It answers exactly the two invocations the section makes
# and reads its answers from files, so the script's OWN awk/parse code is what
# runs. A stub that pre-digested the output would be testing the stub.
#   --failed …            -> $SC_FIX/failed.txt   (absent => none failed)
#   show <unit> -p …      -> $SC_FIX/<unit>.txt   (absent => empty, i.e. absent)
SYSTEMCTL_STUB = r"""
set -u
fix="$SC_FIX"
for a in "$@"; do
  case "$a" in
    --failed) [ -f "$fix/failed.txt" ] && cat "$fix/failed.txt"; exit 0;;
  esac
done
# `systemctl --user show <unit> -p ...` — the unit is the arg after `show`
unit=""; prev=""
for a in "$@"; do
  [ "$prev" = show ] && { unit="$a"; break; }
  prev="$a"
done
[ -n "$unit" ] && [ -f "$fix/$unit.txt" ] && cat "$fix/$unit.txt"
exit 0
"""

# A healthy oneshot that completed 1h ago (uptime is read from the real
# /proc/uptime, so the monotonic stamps are computed per-run in `unit()`).
UNITS = "svc-a.service:svc-a;svc-b.service:svc-b"


class Harness:
    def __init__(self, tmp_path):
        self.root = tmp_path
        self.bin = tmp_path / "bin"
        self.fix = tmp_path / "fix"
        self.bin.mkdir(parents=True, exist_ok=True)
        self.fix.mkdir(parents=True, exist_ok=True)
        write_exec(self.bin / "systemctl", SYSTEMCTL_STUB)
        with open("/proc/uptime") as fh:
            self.uptime = int(float(fh.read().split()[0]))

    def failed(self, *units):
        (self.fix / "failed.txt").write_text(
            "".join("%s loaded failed failed Some Unit\n" % u for u in units),
            encoding="utf-8")
        return self

    def unit(self, name, *, load="loaded", active="inactive", sub="dead",
             result="success", exit_ago=3600, start_ago=None):
        """Write a `systemctl show` block. `*_ago` is seconds before now; None
        means the timestamp is 0 (never reached that point)."""
        def mono(ago):
            if ago is None:
                return 0
            return max(0, (self.uptime - ago)) * 1000000
        (self.fix / ("%s.txt" % name)).write_text(
            "LoadState=%s\nActiveState=%s\nSubState=%s\nResult=%s\n"
            "ExecMainExitTimestampMonotonic=%d\n"
            "ExecMainStartTimestampMonotonic=%d\n"
            % (load, active, sub, result, mono(exit_ago), mono(start_ago)),
            encoding="utf-8")
        return self

    #: 🔴 The ONLY binaries reachable when PATH is replaced below. Enumerated,
    #: not "whatever coreutils ships": `test_no_real_launchers` pins every site
    #: that REPLACES PATH rather than prepending, because replacing drops its
    #: stub directory and a real directory on the other side would put a real
    #: launcher back within reach. Nothing here can launch anything — no
    #: systemctl (that absence IS the test), no kubectl/gh/ssh/home-manager/
    #: pkill — and `_assert_restricted_bin_holds_only_these` proves the
    #: directory's contents equal this set rather than merely starting from it.
    RESTRICTED_BIN = ("bash", "awk", "grep", "cat", "tr", "date", "sed",
                      "basename", "printf")

    def _restricted_bin(self):
        d = self.root / "restricted-bin"
        d.mkdir(exist_ok=True)
        for tool in self.RESTRICTED_BIN:
            p = shutil.which(tool)
            if p and not (d / tool).exists():
                os.symlink(p, d / tool)
        # the pin's justification, asserted rather than asserted-in-prose
        assert set(os.listdir(d)) <= set(self.RESTRICTED_BIN), sorted(
            os.listdir(d))
        assert "systemctl" not in os.listdir(d)
        return d

    def run(self, units=UNITS, *, stub_systemctl=True):
        env = dict(os.environ)
        if stub_systemctl:
            env["PATH"] = "%s:%s" % (self.bin, env["PATH"])
        else:
            # exercises the graceful-skip leg: systemctl must be UNFINDABLE,
            # which no amount of prepending can achieve.
            env["PATH"] = str(self._restricted_bin())
        env["SC_FIX"] = str(self.fix)
        env["STANDUP_LOCAL_UNITS"] = units
        r = subprocess.run(["bash", str(STANDUP), "local"], env=env,
                           capture_output=True, text=True, timeout=120)
        assert "STATUS:" in r.stdout, "no STATUS line:\n%s\n%s" % (
            r.stdout, r.stderr)
        return r.stdout

    @staticmethod
    def status(out):
        return next(ln for ln in out.splitlines() if ln.startswith("STATUS:"))

    @staticmethod
    def local_counts(out):
        """(failed, unhealthy) as the STATUS line reports them."""
        seg = [s for s in Harness.status(out).split("·") if "Local" in s][0]
        nums = seg.replace("Local", "").replace("failed/", " ").replace(
            "unhealthy", "").split()
        return int(nums[0]), int(nums[1])


@pytest.fixture()
def h(tmp_path):
    return Harness(tmp_path)


def test_all_healthy_reads_clean(h):
    h.unit("svc-a.service").unit("svc-b.service")
    out = h.run()
    assert "✓ all user units healthy" in out
    assert h.local_counts(out) == (0, 0)
    assert "ACTIONS" not in out
    # the ages are rendered, not swallowed
    assert "1h ago" in out


def test_control_pair_the_unhealthy_count_MOVES(h):
    """🔴 POSITIVE CONTROL. The same code over a clean fixture and a broken one.
    A zero that cannot rise is indistinguishable from a section wired to
    nothing, so report the PAIR: 0 on the clean fixture, 2 on the broken one."""
    h.unit("svc-a.service").unit("svc-b.service")
    clean = h.local_counts(h.run())

    h.failed("borked.timer", "wedged.service")
    h.unit("svc-a.service", active="failed", result="exit-code")
    broken = h.local_counts(h.run())

    assert clean == (0, 0)
    assert broken == (2, 1), broken
    assert clean != broken


def test_a_failed_unit_is_named_and_becomes_an_ACTION(h):
    h.failed("borked.timer")
    h.unit("svc-a.service").unit("svc-b.service")
    out = h.run()
    assert "✗ 1 failed: borked.timer" in out
    assert "ACTIONS" in out
    assert "borked.timer" in out
    assert "journalctl --user" in out
    assert "All clear" not in out


def test_a_legend_or_header_line_is_never_read_as_a_failed_unit(h):
    """Only tokens ending in a real unit suffix count. `--no-legend` is passed,
    but a systemd that ignores it must not manufacture a failure."""
    (h.fix / "failed.txt").write_text(
        "UNIT LOAD ACTIVE SUB DESCRIPTION\n"
        "borked.timer loaded failed failed Thing\n"
        "\n1 loaded units listed.\n", encoding="utf-8")
    h.unit("svc-a.service").unit("svc-b.service")
    out = h.run()
    assert h.local_counts(out)[0] == 1, out
    assert "UNIT" not in Harness.status(out)


def test_absent_is_not_unhealthy(h):
    """🔴 The distinction that costs the most if the port lost it: the
    workbench-only units simply are not installed on the laptop."""
    h.unit("svc-a.service", load="not-found", result="")
    h.unit("svc-b.service")
    out = h.run()
    assert "svc-a" in out and "— absent" in out
    assert h.local_counts(out) == (0, 0)
    assert "ACTIONS" not in out


def test_a_unit_the_stub_knows_nothing_about_is_absent_not_broken(h):
    h.unit("svc-b.service")            # svc-a has no fixture at all
    out = h.run()
    assert "— absent" in out
    assert h.local_counts(out) == (0, 0)


def test_never_run_is_not_zero_seconds_ago(h):
    """A zero ExecMain timestamp means it never reached that point. Rendering an
    age there would be the strongest "just ran" the column can print, from a
    unit that has never run at all."""
    h.unit("svc-a.service", result="", exit_ago=None)
    h.unit("svc-b.service")
    out = h.run()
    line = next(ln for ln in out.splitlines() if "svc-a" in ln)
    assert "never run" in line, line
    assert "0s ago" not in line


def test_a_running_daemon_shows_its_START_age(h):
    h.unit("svc-a.service", active="active", sub="running", result="",
           exit_ago=None, start_ago=90000)
    h.unit("svc-b.service")
    out = h.run()
    line = next(ln for ln in out.splitlines() if "svc-a" in ln)
    assert "running" in line and "up 1d" in line, line
    assert h.local_counts(out) == (0, 0)


def test_a_nonsuccess_result_is_unhealthy_even_when_inactive(h):
    """What a failed oneshot timer job looks like after it exits: ActiveState is
    back to inactive and only `Result` still carries the failure."""
    h.unit("svc-a.service", active="inactive", sub="dead", result="exit-code")
    h.unit("svc-b.service")
    out = h.run()
    assert h.local_counts(out) == (0, 1)
    assert "exit-code" in out
    assert "svc-a.service" in out and "ACTIONS" in out


def test_an_unknown_state_is_not_reported_as_ok(h):
    """Tri-state: neither success nor failure is UNKNOWN, and must not be
    laundered into 'ok'."""
    h.unit("svc-a.service", active="activating", sub="start", result="")
    h.unit("svc-b.service")
    out = h.run()
    line = next(ln for ln in out.splitlines() if "svc-a" in ln)
    assert "activating" in line, line
    assert " ok " not in line


def test_missing_systemctl_skips_gracefully(h):
    h.unit("svc-a.service")
    out = h.run(stub_systemctl=False)
    assert "systemctl unavailable" in out
    assert "STATUS:" in out          # the run still completes


def test_the_local_scope_is_reachable_and_scoped(h):
    """The section must be its own scope AND part of `all`; and a scope that did
    not run must not contribute a reassuring 0 to STATUS."""
    src = STANDUP.read_text(encoding="utf-8")
    assert '[ "$SCOPE" = local ]' in src
    assert "scan_local" in src
    h.unit("svc-a.service").unit("svc-b.service")
    env = dict(os.environ)
    env["PATH"] = "%s:%s" % (h.bin, env["PATH"])
    env["SC_FIX"] = str(h.fix)
    r = subprocess.run(["bash", str(STANDUP), "alerts"], env=env,
                       capture_output=True, text=True, timeout=120)
    status = next(ln for ln in r.stdout.splitlines() if ln.startswith("STATUS:"))
    assert "Local" not in status, status
