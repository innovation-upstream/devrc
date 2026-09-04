"""Tests for `scripts/syshealth`.

TWO TIERS, on purpose. The rules live in pure functions over a list of `Proc`
records, so most of this file calls them directly on fixtures — no subprocess,
no PATH stubs, no dependence on what this host happens to be running. The
end-to-end tier below then drives the real CLI with a stubbed `ps` and a fake
`/proc` root, because a pure-function suite is structurally blind to argument
parsing, the exit-code path and the renderer.

🔴 FIXTURE VALUES ARE PAIRWISE DISTINCT AND DISTINCT FROM THE CONSTANTS THEY
TEST. A fixture whose %CPU happens to equal the default threshold cannot see a
mutant that hardcodes that default, so it would survive a fully green suite.
Where a test pins a boundary it also pins a value on the other side of it.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/
from testlib import mockbin  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / "syshealth"


def _load():
    """Import `scripts/syshealth` — it has no .py suffix, so by spec + location.

    🔴 The module must be in `sys.modules` BEFORE exec_module: `@dataclass`
    resolves annotations through `sys.modules[cls.__module__].__dict__`, and
    without the registration that lookup returns None and the import dies with
    a bare AttributeError pointing at dataclasses.py.
    """
    loader = importlib.machinery.SourceFileLoader("syshealth", str(SCRIPT))
    spec = importlib.util.spec_from_loader("syshealth", loader)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["syshealth"] = mod
    spec.loader.exec_module(mod)
    return mod


sh = _load()


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------
def P(pid, ppid=1, user="zach", pcpu=0.3, pmem=0.1, rss_kb=4096,
      etimes=3607, stat="S", args="/usr/bin/thing --flag"):
    """A Proc with deliberately unround defaults, so a mutant that substitutes a
    constant cannot coincidentally match one."""
    return sh.Proc(pid=pid, ppid=ppid, user=user, pcpu=pcpu, pmem=pmem,
                   rss_kb=rss_kb, etimes=etimes, stat=stat, args=args)


def zombie(pid, ppid, comm="find", etimes=90061):
    return P(pid=pid, ppid=ppid, pcpu=0.0, rss_kb=0, etimes=etimes,
             stat="Z", args=f"[{comm}] <defunct>")


class Args:
    """Stand-in for the argparse namespace, with the script's real defaults."""

    def __init__(self, **kw):
        self.cpu_threshold = sh.DEF_CPU_THRESHOLD
        self.mem_threshold = sh.DEF_MEM_THRESHOLD
        self.load_threshold = None
        self.runaway_pct = sh.DEF_RUNAWAY_PCT
        self.runaway_age = sh.DEF_RUNAWAY_AGE
        self.min_age = sh.DEF_MIN_AGE
        self.swap_warn_pct = sh.DEF_SWAP_WARN_PCT
        self.avail_crit_pct = sh.DEF_AVAIL_CRIT_PCT
        self.ignore = sh.DEF_IGNORE
        self.systemd = False
        self.fds = False
        self.json = False
        self.proc_root = "/proc"
        self.__dict__.update(kw)


# ---------------------------------------------------------------------------
# parse_ps
# ---------------------------------------------------------------------------
def test_parse_ps_reads_a_normal_row():
    procs = sh.parse_ps("  1234  1200 zach  12.5  3.7  918273  4821 Sl+ /bin/thing -a -b\n")
    assert len(procs) == 1
    p = procs[0]
    assert (p.pid, p.ppid, p.user) == (1234, 1200, "zach")
    assert (p.pcpu, p.pmem, p.rss_kb, p.etimes) == (12.5, 3.7, 918273, 4821)
    assert p.stat == "Sl+"
    assert p.args == "/bin/thing -a -b"


def test_parse_ps_keeps_spaces_in_args():
    """`args` is last and takes the whole remainder — the reason `comm=` is not
    requested from ps at all. A maxsplit off by one truncates the command."""
    procs = sh.parse_ps("7 1 root 0.0 0.0 100 5 S python3 -u -c import sys; go()\n")
    assert procs[0].args == "python3 -u -c import sys; go()"


def test_parse_ps_handles_a_defunct_row():
    procs = sh.parse_ps(" 316293 2273825 root  0.1  0.0     0  516852 Z    [find] <defunct>\n")
    assert procs[0].is_zombie
    assert procs[0].comm == "find"


def test_parse_ps_skips_unparseable_and_short_lines():
    text = ("PID PPID USER %CPU %MEM RSS ELAPSED STAT COMMAND\n"   # a header
            "  1 0 root 0.0 0.0 12 99 Ss /sbin/init\n"
            "garbage\n"
            "\n")
    procs = sh.parse_ps(text)
    assert [p.pid for p in procs] == [1]


def test_parse_ps_tolerates_an_empty_args_column():
    """Some ps builds print nothing at all for a defunct process."""
    procs = sh.parse_ps(" 42 1 root 0.0 0.0 0 77 Z \n")
    assert len(procs) == 1 and procs[0].args == ""


# ---------------------------------------------------------------------------
# Proc properties
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("stat,expected", [
    ("Z", True), ("Zs", True), ("Zl+", True),
    ("S", False), ("Ss", False), ("R", False), ("D", False), ("Sl+", False),
    # 🔴 Not a state ps emits today — Z never appears as a trailing flag. It is
    # here to pin the PREFIX semantics as a deliberate contract: without it,
    # rewriting the guard as `"Z" in self.stat` passes the whole suite (measured
    # — that mutant SURVIVED until this row was added). The parametrize claimed
    # to cover both failure directions while only covering one.
    ("SZ", False),
])
def test_is_zombie_reads_only_the_leading_state_letter(stat, expected):
    """`Zs` IS a real zombie state on this box (measured: pid 988429), so an
    equality test against "Z" misses it. The prefix read is pinned in both
    directions — see the SZ row for why the second direction is synthetic."""
    assert P(1, stat=stat).is_zombie is expected


def test_comm_is_basenamed_and_debracketed():
    assert P(1, args="/nix/store/abc-k3s/bin/k3s server").comm == "k3s"
    assert P(1, args="[run_batch.sh] <defunct>").comm == "run_batch.sh"
    assert P(1, args="").comm == ""


def test_rss_gib_converts_from_kib():
    assert P(1, rss_kb=2 * 1024 * 1024).rss_gib == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# 🔴 The min-age guard — the reason this script exists rather than four ps calls
# ---------------------------------------------------------------------------
def test_a_young_process_at_impossible_cpu_is_not_flagged():
    """THE regression test. Reproduces the exact rows the manual sweep of
    2026-09-03 misread: `ps` itself at 1100% and a `pgrep` at 240%, both aged
    0s, both reported as runaways. ps's %CPU is cpu-time/elapsed over the
    process's whole life, so at ~0 elapsed it is division noise."""
    young = P(3733669, pcpu=240.0, etimes=0, args="pgrep -o -f find /nix -xdev")
    elig = sh.eligible([young], ignore=[], min_age=sh.DEF_MIN_AGE, mypid=999999)
    assert elig == []
    assert sh.find_cpu_hogs(elig, sh.DEF_CPU_THRESHOLD) == []
    assert sh.find_runaways(elig, sh.DEF_RUNAWAY_PCT, sh.DEF_RUNAWAY_AGE) == []


def test_the_same_process_IS_flagged_once_it_is_old_enough():
    """The other half of the control pair. Without this, a mutant that drops
    every process (`return []`) would pass the test above."""
    old = P(3733669, pcpu=240.0, etimes=4001, args="pgrep -o -f find /nix -xdev")
    elig = sh.eligible([old], ignore=[], min_age=sh.DEF_MIN_AGE, mypid=999999)
    assert [p.pid for p in elig] == [3733669]
    assert [p.pid for p in sh.find_cpu_hogs(elig, sh.DEF_CPU_THRESHOLD)] == [3733669]


@pytest.mark.parametrize("etimes,eligible_now", [
    (sh.DEF_MIN_AGE - 1, False),   # just under
    (sh.DEF_MIN_AGE, True),        # exactly at the boundary — `>=`, not `>`
    (sh.DEF_MIN_AGE + 1, True),
])
def test_min_age_boundary_is_inclusive(etimes, eligible_now):
    procs = [P(555, pcpu=61.5, etimes=etimes)]
    got = sh.eligible(procs, ignore=[], min_age=sh.DEF_MIN_AGE, mypid=999999)
    assert bool(got) is eligible_now


def test_min_age_is_configurable_and_actually_consulted():
    """Feeds a value the default CANNOT equal, so a mutant hardcoding
    DEF_MIN_AGE is visible."""
    procs = [P(556, pcpu=61.5, etimes=sh.DEF_MIN_AGE + 3)]
    assert sh.eligible(procs, [], min_age=sh.DEF_MIN_AGE, mypid=999999)
    assert sh.eligible(procs, [], min_age=sh.DEF_MIN_AGE + 900, mypid=999999) == []


def test_zombies_are_never_age_gated():
    """A fresh zombie is still a zombie — the min-age rule is about %CPU being
    meaningless, which does not apply to a state letter."""
    z = zombie(77, ppid=42, etimes=1)
    assert sh.find_zombies([z, P(42, args="/bin/parent")], mypid=999999)


# ---------------------------------------------------------------------------
# Self-exclusion
# ---------------------------------------------------------------------------
def test_self_pids_covers_self_children_and_ancestors():
    procs = [P(100, ppid=1, args="/bin/agent"),      # grandparent
             P(200, ppid=100, args="/bin/zsh"),      # parent
             P(300, ppid=200, args="python syshealth"),  # us
             P(400, ppid=300, args="ps -eo ..."),    # our ps child
             P(500, ppid=1, args="/bin/unrelated")]
    assert sh.self_pids(procs, mypid=300) == {100, 200, 300, 400}


def test_our_own_ps_child_is_never_reported_as_a_hog():
    """The 1100% row in the 2026-09-03 sweep WAS the ps that produced it."""
    procs = [P(300, ppid=1, args="python syshealth", etimes=99),
             P(400, ppid=300, pcpu=1100.0, etimes=99, args="ps -eo pid=,ppid=")]
    elig = sh.eligible(procs, [], min_age=sh.DEF_MIN_AGE, mypid=300)
    assert sh.find_cpu_hogs(elig, sh.DEF_CPU_THRESHOLD) == []


def test_self_pids_terminates_on_a_parent_cycle():
    """A torn `ps` read can produce a cycle; the walk must not hang."""
    procs = [P(10, ppid=11), P(11, ppid=10)]
    assert sh.self_pids(procs, mypid=10) == {10, 11}


# ---------------------------------------------------------------------------
# Ignore list
# ---------------------------------------------------------------------------
def test_parse_ignore_accepts_commas_or_spaces_and_lowercases():
    assert sh.parse_ignore("Anno, logd  Foo,") == ["anno", "logd", "foo"]
    assert sh.parse_ignore("") == []


def test_ignore_matches_command_case_insensitively():
    p = P(1, args="Z:\\steam\\Anno1800.exe -windowed", pcpu=203.0, etimes=2263)
    assert sh.is_ignored(p, ["anno"])
    assert not sh.is_ignored(p, ["logd"])


def test_an_ignored_process_is_dropped_from_every_cpu_rule():
    procs = [P(1, args="/games/Anno1800.exe", pcpu=203.0, etimes=2263),
             P(2, args="/bin/real-hog", pcpu=97.5, etimes=2263)]
    elig = sh.eligible(procs, ["anno"], min_age=sh.DEF_MIN_AGE, mypid=999999)
    assert [p.pid for p in elig] == [2]


def test_an_empty_ignore_list_drops_nothing():
    """Positive control for the filter: with no tokens it must not silently
    match everything (a `not any([])`-shaped mutant would)."""
    procs = [P(1, args="/games/Anno1800.exe", pcpu=203.0, etimes=2263)]
    assert len(sh.eligible(procs, [], min_age=sh.DEF_MIN_AGE, mypid=999999)) == 1


# ---------------------------------------------------------------------------
# Zombies + parent tracking
# ---------------------------------------------------------------------------
def test_zombie_parent_is_resolved_to_a_live_command():
    procs = [zombie(316293, ppid=2273825, comm="find"),
             P(2273825, ppid=2273620, args="sleep infinity", stat="Ss")]
    z = sh.find_zombies(procs, mypid=999999)[0]
    assert z["ppid"] == 2273825
    assert z["parent_comm"] == "sleep"
    assert z["parent_alive"] is True
    assert z["reparented_to_init"] is False


def test_zombie_with_a_missing_parent_is_reported_as_gone():
    z = sh.find_zombies([zombie(316293, ppid=999001)], mypid=999999)[0]
    assert z["parent_alive"] is False and z["parent_comm"] is None


def test_zombie_reparented_to_init_is_labelled():
    procs = [zombie(316293, ppid=1), P(1, ppid=0, args="/sbin/init")]
    z = sh.find_zombies(procs, mypid=999999)[0]
    assert z["reparented_to_init"] is True and z["parent_alive"] is True


def test_a_zombie_whose_parent_is_also_a_zombie_is_flagged():
    procs = [zombie(2, ppid=3), zombie(3, ppid=4), P(4, args="/bin/top")]
    got = {z["pid"]: z for z in sh.find_zombies(procs, mypid=999999)}
    assert got[2]["parent_is_zombie"] is True
    assert got[3]["parent_is_zombie"] is False


def test_zombies_are_sorted_oldest_first():
    procs = [zombie(1, ppid=9, etimes=100), zombie(2, ppid=9, etimes=90061),
             zombie(3, ppid=9, etimes=5000), P(9, args="/bin/p")]
    assert [z["pid"] for z in sh.find_zombies(procs, mypid=999999)] == [2, 3, 1]


def test_group_zombies_collapses_a_shared_un_reaping_parent():
    """Reproduces the measured shape: 18 under one parent, 3 under another."""
    procs = [P(2273825, args="sleep infinity"), P(3716907, args="/bin/stash")]
    procs += [zombie(1000 + i, ppid=2273825, comm="find") for i in range(18)]
    procs += [zombie(2000 + i, ppid=3716907, comm="python3") for i in range(3)]
    groups, single = sh.group_zombies(sh.find_zombies(procs, mypid=999999))
    assert single == []
    assert [(g["ppid"], g["count"]) for g in groups] == [(2273825, 18), (3716907, 3)]
    assert groups[0]["parent_comm"] == "sleep"


@pytest.mark.parametrize("n,grouped", [
    (sh.GROUP_MIN_CHILDREN - 1, False),
    (sh.GROUP_MIN_CHILDREN, True),
])
def test_group_zombies_boundary(n, grouped):
    procs = [P(700, args="/bin/parent")]
    procs += [zombie(800 + i, ppid=700) for i in range(n)]
    groups, single = sh.group_zombies(sh.find_zombies(procs, mypid=999999))
    assert bool(groups) is grouped
    assert (len(single) == 0) is grouped


def test_zombie_group_reports_the_oldest_age_not_the_first():
    """🔴 Grouped on a list whose FIRST element is not the oldest, on purpose.

    `find_zombies` already returns oldest-first, so feeding its output straight
    in makes `kids[0]["age_sec"]` and `max(...)` the same value — the mutant is
    then equivalent and SURVIVES (measured). Reversing the list is the control
    that makes the two expressions disagree.
    """
    procs = [P(700, args="/bin/parent")]
    procs += [zombie(801, ppid=700, etimes=5), zombie(802, ppid=700, etimes=90061),
              zombie(803, ppid=700, etimes=77)]
    zs = list(reversed(sh.find_zombies(procs, mypid=999999)))
    assert zs[0]["age_sec"] != 90061, "fixture no longer exercises the ordering"
    groups, _ = sh.group_zombies(zs)
    assert groups[0]["oldest_age_sec"] == 90061


# ---------------------------------------------------------------------------
# Hogs, runaways, grouping
# ---------------------------------------------------------------------------
def test_cpu_hogs_respect_the_threshold_on_both_sides():
    procs = [P(1, pcpu=61.5, etimes=2263), P(2, pcpu=13.2, etimes=2263)]
    elig = sh.eligible(procs, [], sh.DEF_MIN_AGE, mypid=999999)
    assert [p.pid for p in sh.find_cpu_hogs(elig, 50.0)] == [1]
    assert [p.pid for p in sh.find_cpu_hogs(elig, 70.0)] == []
    assert [p.pid for p in sh.find_cpu_hogs(elig, 10.0)] == [1, 2]


def test_cpu_hogs_are_sorted_hottest_first():
    procs = [P(1, pcpu=61.5, etimes=2263), P(2, pcpu=97.5, etimes=2263),
             P(3, pcpu=73.1, etimes=2263)]
    elig = sh.eligible(procs, [], sh.DEF_MIN_AGE, mypid=999999)
    assert [p.pid for p in sh.find_cpu_hogs(elig, 50.0)] == [2, 3, 1]


def test_mem_hogs_use_gib_not_kib():
    """1_500_000 KiB is ~1.43 GiB — over a 1.0 threshold, under a 2.0 one.
    A mutant comparing raw KiB against the GiB threshold flags both."""
    procs = [P(1, rss_kb=1_500_000, etimes=2263), P(2, rss_kb=700_000, etimes=2263)]
    elig = sh.eligible(procs, [], sh.DEF_MIN_AGE, mypid=999999)
    assert [p.pid for p in sh.find_mem_hogs(elig, 1.0)] == [1]
    assert [p.pid for p in sh.find_mem_hogs(elig, 2.0)] == []


def test_runaway_needs_BOTH_the_percentage_and_the_age():
    hot_young = P(1, pcpu=97.5, etimes=61)        # hot, too new
    warm_old = P(2, pcpu=13.2, etimes=90061)      # old, not hot
    hot_old = P(3, pcpu=97.5, etimes=90061)       # both
    elig = sh.eligible([hot_young, warm_old, hot_old], [], sh.DEF_MIN_AGE,
                       mypid=999999)
    got = sh.find_runaways(elig, sh.DEF_RUNAWAY_PCT, sh.DEF_RUNAWAY_AGE)
    assert [p.pid for p in got] == [3]


@pytest.mark.parametrize("pcpu,etimes,crit", [
    (sh.CRIT_RUNAWAY_PCT + 13, sh.CRIT_RUNAWAY_AGE + 401, True),
    (sh.CRIT_RUNAWAY_PCT - 13, sh.CRIT_RUNAWAY_AGE + 401, False),
    (sh.CRIT_RUNAWAY_PCT + 13, sh.CRIT_RUNAWAY_AGE - 401, False),
    (sh.CRIT_RUNAWAY_PCT, sh.CRIT_RUNAWAY_AGE, True),  # boundary is inclusive
])
def test_critical_runaway_needs_both_arms(pcpu, etimes, crit):
    assert sh.is_critical_runaway(P(1, pcpu=pcpu, etimes=etimes)) is crit


def test_group_by_parent_collapses_workers_and_sums_them():
    parent = P(2992278, args="python -m pytest scripts/tests -n 4")
    kids = [P(2993230 + i, ppid=2992278, pcpu=25.8, rss_kb=292336, etimes=286)
            for i in range(4)]
    elig = sh.eligible([parent] + kids, [], sh.DEF_MIN_AGE, mypid=999999)
    hogs = sh.find_cpu_hogs(elig, 20.0)
    groups, single = sh.group_by_parent(hogs, [parent] + kids)
    assert len(groups) == 1
    g = groups[0]
    assert g["ppid"] == 2992278 and g["count"] == 4
    assert g["parent_comm"] == "python"
    assert g["total_pcpu"] == pytest.approx(103.2)
    assert single == []


@pytest.mark.parametrize("n,grouped", [
    (sh.GROUP_MIN_CHILDREN - 1, False),
    (sh.GROUP_MIN_CHILDREN, True),      # exactly at the bar — `>=`, not `>`
    (sh.GROUP_MIN_CHILDREN + 1, True),
])
def test_group_by_parent_boundary_is_inclusive(n, grouped):
    """🔴 The `== GROUP_MIN_CHILDREN` row is load-bearing. The sum test below
    uses four workers, which groups under both `>= 3` and `> 3`, so without a
    row landing exactly ON the bar a `>` mutant SURVIVES the whole suite
    (measured)."""
    kids = [P(10 + i, ppid=9, pcpu=61.5, etimes=2263) for i in range(n)]
    groups, single = sh.group_by_parent(kids, kids)
    assert bool(groups) is grouped
    assert (single == []) is grouped
    assert len(single) == (0 if grouped else n)


def test_group_by_parent_handles_a_dead_parent():
    kids = [P(10 + i, ppid=999001, pcpu=61.5, etimes=2263) for i in range(4)]
    groups, _ = sh.group_by_parent(kids, kids)
    assert groups[0]["parent_alive"] is False
    assert groups[0]["parent_comm"] is None


# ---------------------------------------------------------------------------
# Overview + verdict
# ---------------------------------------------------------------------------
def _meminfo(total=129_000_000, avail=64_000_000, swap_total=90_600_000,
             swap_free=39_000_000):
    return {"MemTotal": total, "MemAvailable": avail,
            "SwapTotal": swap_total, "SwapFree": swap_free}


def _report(procs, args=None, load=(9.1, 9.18, 9.28), meminfo=None, **kw):
    return sh.build_report(args or Args(**kw), procs, load,
                           meminfo or _meminfo(), 2_680_000,
                           proc_root="/nonexistent-proc", mypid=999999)


def test_a_clean_system_exits_0():
    rep = _report([P(1, ppid=0, args="/sbin/init"), P(500, args="/bin/idle")],
                  meminfo=_meminfo(swap_total=0, swap_free=0),
                  load_threshold=24.0)
    assert rep["verdict"] == {"exit_code": 0, "critical": [], "warnings": []}
    assert rep["exit_code"] == 0


def test_zombies_alone_produce_exit_1():
    procs = [P(700, args="/bin/parent"), zombie(801, ppid=700)]
    rep = _report(procs, meminfo=_meminfo(swap_total=0, swap_free=0),
                  load_threshold=24.0)
    assert rep["exit_code"] == 1
    assert any("zombie" in w for w in rep["verdict"]["warnings"])


def test_high_load_produces_exit_1_and_names_the_number():
    rep = _report([P(1, args="/sbin/init")], load=(55.18, 39.53, 32.08),
                  meminfo=_meminfo(swap_total=0, swap_free=0), load_threshold=24.0)
    assert rep["exit_code"] == 1
    assert rep["overview"]["load_high"] is True
    assert any("55.18" in w for w in rep["verdict"]["warnings"])


def test_load_below_threshold_is_not_a_warning():
    rep = _report([P(1, args="/sbin/init")], load=(9.1, 9.2, 9.3),
                  meminfo=_meminfo(swap_total=0, swap_free=0), load_threshold=24.0)
    assert rep["overview"]["load_high"] is False and rep["exit_code"] == 0


def test_load_threshold_defaults_to_the_core_count():
    rep = _report([P(1, args="/sbin/init")], load=(9.1, 9.2, 9.3),
                  meminfo=_meminfo(swap_total=0, swap_free=0))
    assert rep["overview"]["load_threshold"] == float(os.cpu_count() or 1)


def test_swap_pressure_warns_and_reports_the_percentage():
    rep = _report([P(1, args="/sbin/init")], load_threshold=24.0,
                  meminfo=_meminfo(swap_total=90_600_000, swap_free=39_000_000))
    assert rep["overview"]["swap_used_pct"] == pytest.approx(56.9, abs=0.2)
    assert rep["overview"]["swap_high"] is True
    assert rep["exit_code"] == 1


def test_low_swap_use_does_not_warn():
    rep = _report([P(1, args="/sbin/init")], load_threshold=24.0,
                  meminfo=_meminfo(swap_total=90_600_000, swap_free=85_000_000))
    assert rep["overview"]["swap_high"] is False and rep["exit_code"] == 0


def test_no_swap_configured_is_not_pressure():
    """Division-by-zero guard: a box with swap off must read 0%, not crash and
    not warn."""
    rep = _report([P(1, args="/sbin/init")], load_threshold=24.0,
                  meminfo=_meminfo(swap_total=0, swap_free=0))
    assert rep["overview"]["swap_used_pct"] == 0.0
    assert rep["overview"]["swap_high"] is False


def test_memory_near_exhaustion_is_CRITICAL_exit_2():
    rep = _report([P(1, args="/sbin/init")], load_threshold=24.0,
                  meminfo=_meminfo(total=129_000_000, avail=2_400_000,
                                   swap_total=0, swap_free=0))
    assert rep["exit_code"] == 2
    assert rep["verdict"]["critical"]


def test_a_sustained_runaway_is_CRITICAL_exit_2():
    procs = [P(479313, pcpu=213.0, etimes=90061, args="/games/Darktide.exe")]
    rep = _report(procs, load_threshold=24.0, ignore="",
                  meminfo=_meminfo(swap_total=0, swap_free=0))
    assert rep["exit_code"] == 2
    assert rep["runaways"][0]["critical"] is True
    assert any("479313" in c for c in rep["verdict"]["critical"])


def test_the_same_runaway_is_only_a_warning_when_short_lived():
    """Control pair for the test above: drops ONLY the age below the critical
    bar, so a mutant ignoring the age arm is visible."""
    procs = [P(479313, pcpu=213.0, etimes=sh.CRIT_RUNAWAY_AGE - 401,
               args="/games/Darktide.exe")]
    rep = _report(procs, load_threshold=24.0, ignore="",
                  meminfo=_meminfo(swap_total=0, swap_free=0))
    assert rep["exit_code"] == 1
    assert rep["runaways"][0]["critical"] is False


def test_critical_outranks_warning_in_the_exit_code():
    procs = [P(700, args="/bin/parent"), zombie(801, ppid=700),
             P(479313, pcpu=213.0, etimes=90061, args="/bin/hot")]
    rep = _report(procs, load=(55.18, 39.5, 32.0), load_threshold=24.0, ignore="",
                  meminfo=_meminfo(swap_total=0, swap_free=0))
    assert rep["exit_code"] == 2
    assert rep["verdict"]["warnings"] and rep["verdict"]["critical"]


def test_the_verdict_names_every_reason_not_just_the_code():
    """A bare 1 tells the reader nothing about which of six rules fired."""
    procs = [P(700, args="/bin/parent"), zombie(801, ppid=700),
             P(3, pcpu=97.5, etimes=2263, args="/bin/hog")]
    rep = _report(procs, load=(55.18, 39.5, 32.0), load_threshold=24.0, ignore="",
                  meminfo=_meminfo(swap_total=90_600_000, swap_free=39_000_000))
    joined = " | ".join(rep["verdict"]["warnings"])
    for expected in ("load1", "swap", "zombie", "cpu hogs"):
        assert expected in joined, f"{expected!r} missing from {joined!r}"


def test_grouped_hogs_are_counted_by_member_not_by_group():
    """A count of GROUPS would report 1 where 4 processes are hogging."""
    parent = P(2992278, args="python -m pytest -n 4")
    kids = [P(2993230 + i, ppid=2992278, pcpu=97.5, etimes=286) for i in range(4)]
    rep = _report([parent] + kids, load_threshold=24.0, ignore="",
                  meminfo=_meminfo(swap_total=0, swap_free=0))
    assert any("4 cpu hogs" in w for w in rep["verdict"]["warnings"])


def test_report_counts_processes_and_eligibles_separately():
    procs = [P(1, args="/sbin/init"), P(2, etimes=0, args="/bin/newborn")]
    rep = _report(procs, load_threshold=24.0,
                  meminfo=_meminfo(swap_total=0, swap_free=0))
    assert rep["counts"]["processes"] == 2
    assert rep["counts"]["eligible"] == 1


def test_thresholds_are_echoed_into_the_report():
    """The report must carry the scope it was measured at."""
    rep = _report([P(1, args="/sbin/init")], cpu_threshold=71.0,
                  mem_threshold=3.5, min_age=45, load_threshold=24.0,
                  meminfo=_meminfo(swap_total=0, swap_free=0))
    th = rep["thresholds"]
    assert (th["cpu"], th["mem_gib"], th["min_age_sec"]) == (71.0, 3.5, 45)


# ---------------------------------------------------------------------------
# Optional sections
# ---------------------------------------------------------------------------
def test_failed_units_parses_unit_names():
    got = sh.failed_units(lambda cmd: (0, "dl-router.service loaded failed failed X\n"
                                          "mail.service loaded failed failed Y\n", ""))
    assert got == {"measured": True, "reason": None,
                   "units": ["dl-router.service", "mail.service"]}


def test_failed_units_asks_systemctl_for_a_read_verb():
    """🔴 The systemctl call site must stay a READ, pinned against the repo's own
    verb list rather than a literal copied into this file.

    `test_no_real_launchers.py` acknowledges `syshealth` as a systemctl reacher on
    exactly this ground — it is the only acknowledged file that really invokes the
    binary — so if the argv ever grows a mutating verb, that justification becomes
    false. This is the test named there; it fails the moment that happens.

    Note `--failed` is deliberately NOT used: it is a FLAG, and nolaunch's
    safe-flag list is only --version/--help/-h, so the short spelling would fail
    closed in the suite while reading identically on a real host.
    """
    from testlib import nolaunch  # noqa: PLC0415 — keep module import cheap

    seen = {}

    def runner(cmd):
        seen["cmd"] = cmd
        return 0, "", ""

    sh.failed_units(runner)
    argv = seen["cmd"]
    assert argv[0] == "systemctl"
    # The first non-flag token after the binary is the positional verb.
    verb = next(a for a in argv[1:] if not a.startswith("-"))
    assert verb in nolaunch.SYSTEMCTL_READ_VERBS, (
        f"{verb!r} is not a read verb; the nolaunch stub blocks it and the "
        "acknowledgement in test_no_real_launchers.py is no longer true")
    assert "--failed" not in argv, "the flag spelling fails closed in the suite"


def test_failed_units_reports_COULD_NOT_MEASURE_rather_than_zero():
    """🔴 A systemctl that could not run must not read as 'no failed units'."""
    got = sh.failed_units(lambda cmd: (127, "", "systemctl: not found"))
    assert got["measured"] is False
    assert got["units"] == []
    assert "not found" in got["reason"]


def test_a_could_not_measure_systemd_section_sets_no_warning():
    rep = _report([P(1, args="/sbin/init")], systemd=True, load_threshold=24.0,
                  meminfo=_meminfo(swap_total=0, swap_free=0))
    rep["systemd"] = {"measured": False, "reason": "boom", "units": []}
    assert sh.verdict(rep)["exit_code"] == 0


def test_failed_units_do_warn_when_measured():
    rep = _report([P(1, args="/sbin/init")], systemd=True, load_threshold=24.0,
                  meminfo=_meminfo(swap_total=0, swap_free=0))
    rep["systemd"] = {"measured": True, "reason": None, "units": ["a.service"]}
    v = sh.verdict(rep)
    assert v["exit_code"] == 1 and any("failed user unit" in w for w in v["warnings"])


def test_optional_sections_are_absent_unless_asked_for():
    rep = _report([P(1, args="/sbin/init")], load_threshold=24.0,
                  meminfo=_meminfo(swap_total=0, swap_free=0))
    assert "systemd" not in rep and "fds" not in rep


def test_fd_counts_reports_unreadable_separately_from_zero(tmp_path):
    """🔴 The positive control for the FD scan. Most processes are not ours, so
    /proc/<pid>/fd is EACCES; folding those into the result would print a short
    tidy list that reads as 'nothing has many FDs' while never having looked."""
    proc = tmp_path / "proc"
    (proc / "10" / "fd").mkdir(parents=True)
    for i in range(7):
        (proc / "10" / "fd" / str(i)).write_text("")
    unreadable = proc / "11" / "fd"
    unreadable.mkdir(parents=True)
    os.chmod(unreadable, 0o000)
    try:
        got = sh.fd_counts([P(10), P(11), P(12)], proc_root=str(proc), mypid=999999)
    finally:
        os.chmod(unreadable, 0o755)
    assert got["top"][0] == {"pid": 10, "comm": "thing", "user": "zach", "fds": 7}
    assert got["examined"] == 1
    assert got["vanished"] == 1          # pid 12 has no directory at all
    if os.geteuid() != 0:                # root can read a 0000 dir
        assert got["unreadable"] == 1


def test_fd_counts_skips_zombies_and_self(tmp_path):
    proc = tmp_path / "proc"
    for pid in (10, 20):
        (proc / str(pid) / "fd").mkdir(parents=True)
        (proc / str(pid) / "fd" / "0").write_text("")
    got = sh.fd_counts([P(10), zombie(20, ppid=1)], proc_root=str(proc), mypid=10)
    assert got["top"] == [] and got["examined"] == 0


# ---------------------------------------------------------------------------
# /proc readers
# ---------------------------------------------------------------------------
def test_read_loadavg_and_meminfo_and_uptime(tmp_path):
    (tmp_path / "loadavg").write_text("55.18 39.53 32.08 12/3456 789\n")
    (tmp_path / "meminfo").write_text(
        "MemTotal:       129000000 kB\nMemAvailable:    64000000 kB\n"
        "SwapTotal:       90600000 kB\nSwapFree:        39000000 kB\n"
        "HugePages_Total:        0\n")
    (tmp_path / "uptime").write_text("2680000.41 61000000.00\n")
    assert sh.read_loadavg(str(tmp_path)) == (55.18, 39.53, 32.08)
    mi = sh.read_meminfo(str(tmp_path))
    assert mi["MemTotal"] == 129000000 and mi["SwapFree"] == 39000000
    assert mi["HugePages_Total"] == 0
    assert sh.read_uptime(str(tmp_path)) == pytest.approx(2680000.41)


def test_collect_ps_refuses_an_empty_result():
    """🔴 A zero here is never a clean bill of health — this host has processes,
    so 'ps parsed nothing' is a broken instrument, not a healthy machine."""
    with pytest.raises(RuntimeError, match="cannot measure"):
        sh.collect_ps(lambda cmd: (0, "\n", ""))


def test_collect_ps_surfaces_a_failing_ps():
    with pytest.raises(RuntimeError, match="exit 127"):
        sh.collect_ps(lambda cmd: (127, "", "ps: not found"))


def test_collect_ps_asks_for_the_documented_format():
    seen = {}

    def runner(cmd):
        seen["cmd"] = cmd
        return 0, " 1 0 root 0.0 0.0 12 99 Ss /sbin/init\n", ""

    sh.collect_ps(runner)
    assert seen["cmd"] == ["ps", "-eo", sh.PS_FORMAT]
    assert sh.PS_FORMAT.count("=") == sh.PS_FIELDS
    assert sh.PS_FORMAT.split(",")[-1] == "args="   # args MUST stay last


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sec,text", [
    (7, "7s"), (61, "1m01s"), (3599, "59m59s"),
    (3600, "1h00m"), (90061, "1d01h"), (2_680_000, "31d00h"),
])
def test_fmt_age(sec, text):
    assert sh.fmt_age(sec) == text


def test_render_emits_every_section_and_the_verdict():
    import io
    procs = [P(700, args="/bin/parent"), zombie(801, ppid=700),
             P(3, pcpu=97.5, etimes=90061, args="/bin/hog --loop")]
    rep = _report(procs, load_threshold=24.0, ignore="",
                  meminfo=_meminfo(swap_total=0, swap_free=0))
    buf = io.StringIO()
    sh.render(rep, out=buf)
    text = buf.getvalue()
    for heading in ("OVERVIEW", "ZOMBIES", "CPU HOGS", "MEM HOGS",
                    "RUNAWAYS", "VERDICT"):
        assert heading in text
    assert "exit 1" in text


def test_render_prints_a_kill_hint_with_the_identity_needed_to_check_it():
    """Report-only by design: the hint must carry cwd and ppid, because this box
    runs parallel agents whose workers look exactly like a runaway."""
    import io
    rep = _report([P(479313, pcpu=97.5, etimes=90061, args="/bin/hot")],
                  load_threshold=24.0, ignore="",
                  meminfo=_meminfo(swap_total=0, swap_free=0))
    buf = io.StringIO()
    sh.render(rep, out=buf)
    text = buf.getvalue()
    assert "to kill:  kill 479313" in text
    assert "cwd:" in text and "ppid:" in text
    assert "sibling agent" in text


def test_the_script_has_no_kill_flag():
    """Pins the v1 decision STRUCTURALLY, not in prose: syshealth never signals
    a process. A future --kill must come with its own guard coverage, and this
    test failing is the prompt to write it."""
    parser = sh.build_parser()
    flags = {a for action in parser._actions for a in action.option_strings}
    assert "--kill" not in flags
    src = SCRIPT.read_text()
    for forbidden in ("os.kill", "SIGTERM", "SIGKILL", "pkill", "killpg"):
        assert forbidden not in src, f"{forbidden} appeared in a report-only tool"


# ---------------------------------------------------------------------------
# End-to-end: the real CLI, stubbed ps, fake /proc
# ---------------------------------------------------------------------------
PS_ROWS = "\n".join([
    "      1       0 root  0.0  0.0   12000 2680000 Ss  /sbin/init",
    " 700       1 root  0.4  0.1   40000  700000 Ss  /bin/parent --serve",
    " 801     700 root  0.0  0.0       0  400000 Z   [find] <defunct>",
    " 802     700 root  0.0  0.0       0  400000 Z   [grep] <defunct>",
    " 803     700 root  0.0  0.0       0  400000 Z   [head] <defunct>",
    " 900       1 zach 97.5  0.9 1500000   90061 Rl  /bin/hog --loop",
    " 901       1 zach  1.2  0.0    9000       3 R   ps -eo pid=,ppid=",
])


@pytest.fixture()
def env(tmp_path):
    """A stub `ps` on PATH plus a fake /proc; returns a runner."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    mockbin.write_exec(bindir / "ps", f"cat <<'ROWS'\n{PS_ROWS}\nROWS\n")
    mockbin.write_exec(bindir / "systemctl", "echo 'bad.service x failed failed Z'\n")
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "loadavg").write_text("55.18 39.53 32.08 12/3456 789\n")
    (proc / "meminfo").write_text(
        "MemTotal:       129000000 kB\nMemAvailable:    64000000 kB\n"
        "SwapTotal:              0 kB\nSwapFree:               0 kB\n")
    (proc / "uptime").write_text("2680000.41 61000000.00\n")

    def run(*flags):
        e = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}")
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--proc-root", str(proc),
             "--load-threshold", "24", *flags],
            capture_output=True, text=True, env=e, timeout=60)

    return run


def test_cli_end_to_end_exits_1_and_finds_the_planted_problems(env):
    r = env()
    assert r.returncode == 1, r.stderr
    assert "3x zombie under pid 700" in r.stdout
    assert "900" in r.stdout and "hog" in r.stdout
    assert "55.18" in r.stdout


def test_cli_never_reports_its_own_ps(env):
    """pid 901 is the stub `ps` itself, aged 3s. It must appear in neither the
    hog list nor the runaway list."""
    r = env()
    assert "901" not in r.stdout


def test_cli_json_is_valid_and_its_exit_code_matches_the_process(env):
    r = env("--json")
    doc = json.loads(r.stdout)
    assert doc["exit_code"] == r.returncode == 1
    assert doc["verdict"]["exit_code"] == r.returncode
    assert doc["zombies"]["count"] == 3
    assert doc["overview"]["load_high"] is True
    assert {"overview", "zombies", "cpu_hogs", "mem_hogs", "runaways",
            "thresholds", "counts", "verdict", "exit_code"} <= set(doc)


def test_cli_json_and_human_agree_on_the_verdict(env):
    """Two renderers, one verdict — a divergence here means one of them is
    computing its own answer."""
    assert env("--json").returncode == env().returncode


def test_cli_thresholds_change_the_outcome(env):
    """Raising the bar above the planted hog must clear the CPU section — the
    positive/negative control pair for flag plumbing."""
    hot = env("--cpu-threshold", "60", "--json")
    cool = env("--cpu-threshold", "99", "--json")
    assert json.loads(hot.stdout)["cpu_hogs"]["single"]
    assert not json.loads(cool.stdout)["cpu_hogs"]["single"]


def test_cli_min_age_flag_reaches_the_rule(env):
    """Raising min-age past the hog's 90061s age drops it from every CPU rule.

    Asserted as a control PAIR against the default run rather than against an
    absolute count: `/sbin/init` in the fixture is 2,680,000s old and survives
    any plausible min-age, so `eligible == 0` would be pinning the fixture's
    uptime, not the flag.
    """
    before = json.loads(env("--json").stdout)
    after = json.loads(env("--min-age", "999999", "--json").stdout)
    assert before["cpu_hogs"]["single"] and before["runaways"]
    assert after["cpu_hogs"]["single"] == [] and after["runaways"] == []
    assert after["counts"]["eligible"] < before["counts"]["eligible"]


def test_cli_ignore_flag_suppresses_a_match(env):
    doc = json.loads(env("--ignore", "hog", "--json").stdout)
    assert doc["cpu_hogs"]["single"] == []
    assert doc["zombies"]["count"] == 3  # zombies are not ignore-filtered


def test_cli_opt_in_sections_are_off_by_default(env):
    doc = json.loads(env("--json").stdout)
    assert "systemd" not in doc and "fds" not in doc
    assert "SYSTEMD" not in env().stdout


def test_cli_systemd_section_appears_on_request(env):
    doc = json.loads(env("--systemd", "--json").stdout)
    assert doc["systemd"] == {"measured": True, "reason": None,
                              "units": ["bad.service"]}
    assert any("failed user unit" in w for w in doc["verdict"]["warnings"])


def test_cli_fds_section_appears_on_request(env):
    doc = json.loads(env("--fds", "--json").stdout)
    assert set(doc["fds"]) == {"top", "examined", "unreadable", "vanished"}


def test_cli_could_not_measure_exits_3_not_0(tmp_path):
    """🔴 An unreadable /proc must not read as a clean system."""
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--proc-root", str(tmp_path / "nope")],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 3
    assert "COULD NOT MEASURE" in r.stderr


def test_cli_exits_0_on_a_genuinely_clean_stub(tmp_path):
    """The negative control for the whole pipeline: same code path, nothing
    planted, must be silent and green. Without this, a script that always
    returned 1 would pass every test above."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    rows = ("      1       0 root 0.0 0.0 12000 2680000 Ss /sbin/init\n"
            " 500       1 zach 0.4 0.1 40000  700000 Sl /bin/idle --wait\n")
    mockbin.write_exec(bindir / "ps", f"cat <<'ROWS'\n{rows}ROWS\n")
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "loadavg").write_text("1.10 1.20 1.30 1/100 2\n")
    (proc / "meminfo").write_text(
        "MemTotal:       129000000 kB\nMemAvailable:   100000000 kB\n"
        "SwapTotal:              0 kB\nSwapFree:               0 kB\n")
    (proc / "uptime").write_text("2680000.41 61000000.00\n")
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--proc-root", str(proc),
         "--load-threshold", "24"],
        capture_output=True, text=True,
        env=dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}"), timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "exit 0" in r.stdout and "clean" in r.stdout


def test_the_clean_and_dirty_runs_actually_differ(env):
    """The control pair, asserted as a pair: if these two ever produce the same
    output the fixtures have stopped exercising anything."""
    dirty = env().stdout
    assert "ZOMBIES (3" in dirty
    assert "ZOMBIES (0" not in dirty
