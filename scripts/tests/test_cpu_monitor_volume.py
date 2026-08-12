"""Tests for cpu-monitor's desktop-volume controls: the journal-only recovery
notices and the per-trigger daily cap.

CONTEXT, because the size of this change is deliberately small. PR #409 put
cpu-monitor at ~90/day and second on the noise ranking. Re-measured 2026-08-12
that is wrong: it is ~23/day on the workbench and ~13/day on the laptop. #409's
mean straddled a regime break — raising CPU_MON_THRESHOLD/RUNAWAY_PCT on
2026-08-05 took the workbench from 123-267/day to 11-32/day — and two
independent instruments agree after the break (dunst's per-notification icon
warning, calibrated at exactly 1 warning per notification by a 4-probe positive
control; and dunst's own history). So the changes here are a trim and a bound,
not surgery:

  * the "✓ back to normal" clears stop toasting (measured 6/12 of laptop and
    3/15 of workbench cpu-monitor toasts, and the ONE path in the script with no
    cooldown and no state at all);
  * load and runaway get a daily cap, which at 23/day will essentially never
    bind — its job is to make the old 267/day regime structurally unreachable,
    since the per-episode cooldown does not bound the aggregate (a NEW episode
    alerts immediately, so a flapping signal is unbounded).

HOW THESE DRIVE REAL CODE: the script ends in an infinite sampling loop, so the
tests source the PRELUDE (everything above `while :;`) into a shell and call the
real `alert_capped` / `clear_alert` / `roll_alert_day`. `test_prelude_extraction_
actually_found_the_functions` is the positive control on that extraction — if it
silently captured nothing, every "no toast was fired" assertion below would pass
for the wrong reason.
"""
import os
import re
import sys
import subprocess
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/
from testlib import mockbin  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPT = os.path.join(_HERE, "..", "cpu-monitor.sh")


def _prelude():
    """Everything above the main sampling loop: constants + functions."""
    src = open(_SCRIPT).read()
    idx = src.find("\nwhile :;")
    assert idx > 0, "cpu-monitor.sh no longer has a `while :;` main loop"
    return src[:idx]


def _run(body, env=None, tmp_path=None):
    """Source the real prelude, then run `body`. Returns CompletedProcess."""
    stub_log = tmp_path / "toasts.log"
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    # mockbin owns the shebang — a hand-written one ENOENTs in the nix sandbox.
    mockbin.write_exec(
        bindir / "notify-send",
        'line="notify-send"\nfor a in "$@"; do line="$line [$a]"; done\n'
        'printf \'%%s\\n\' "$line" | tr \'\\n\' \' \' >> "%s"\n'
        'printf \'\\n\' >> "%s"\nexit 0\n' % (stub_log, stub_log))

    script = tmp_path / "harness.sh"
    script.write_text(_prelude() + "\n" + body + "\n")

    e = dict(os.environ)
    e["PATH"] = str(bindir) + os.pathsep + e["PATH"]
    e["DISPLAY"] = ":99"
    e["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/dev/null"
    for k in list(e):
        if k.startswith("CPU_MON_"):
            e.pop(k)
    if env:
        e.update(env)
    p = subprocess.run(["bash", str(script)], env=e, capture_output=True,
                       text=True, timeout=60)
    p.toasts = [l for l in (stub_log.read_text().splitlines()
                            if stub_log.exists() else []) if l.strip()]
    return p


def test_prelude_extraction_actually_found_the_functions():
    """POSITIVE CONTROL on the harness. If the extraction captured nothing, every
    'no toast fired' assertion in this file would be vacuously true."""
    src = _prelude()
    for fn in ("alert()", "alert_capped()", "clear_alert()", "journal_only()",
               "roll_alert_day()"):
        assert fn in src, "prelude extraction missed %s" % fn


def test_harness_can_observe_a_toast(tmp_path):
    """POSITIVE CONTROL on the stub: the harness must be able to see a real
    desktop alert, or its zeros mean nothing."""
    p = _run('alert critical "probe" "body"', tmp_path=tmp_path)
    assert len(p.toasts) == 1, "harness cannot observe a toast: %r / %s" % (p.toasts, p.stderr)


# --------------------------------------------------------------------------
# RECOVERY NOTICES
# --------------------------------------------------------------------------

def test_clear_notices_do_not_toast_by_default(tmp_path):
    p = _run('clear_alert "✓ CPU load back to normal" "1-min load: 2.0"',
             tmp_path=tmp_path)
    assert p.toasts == [], "recovery notices must not reach the desktop by default"


def test_clear_notices_still_reach_the_journal(tmp_path):
    """Demoted, not deleted — the distinction the whole change rests on."""
    p = _run('clear_alert "✓ CPU load back to normal" "1-min load: 2.0"',
             tmp_path=tmp_path)
    assert "back to normal" in p.stderr
    assert "cpu-monitor" in p.stderr


def test_clear_notices_are_restorable_by_env(tmp_path):
    """Every reduction must be reversible without a code edit."""
    p = _run('clear_alert "✓ CPU load back to normal" "1-min load: 2.0"',
             env={"CPU_MON_CLEAR_TOASTS": "1"}, tmp_path=tmp_path)
    assert len(p.toasts) == 1, "CPU_MON_CLEAR_TOASTS=1 must restore the toast"


# --------------------------------------------------------------------------
# THE DAILY CAP
# --------------------------------------------------------------------------

def test_alerts_up_to_the_cap_all_toast(tmp_path):
    p = _run('for i in $(seq 1 8); do alert_capped load_alerts_today critical '
             '"⚠ High CPU load" "body $i"; done',
             env={"CPU_MON_MAX_ALERTS_PER_DAY": "8"}, tmp_path=tmp_path)
    assert len(p.toasts) == 8, "the first 8 must toast, got %d" % len(p.toasts)


def test_the_alert_past_the_cap_does_not_toast(tmp_path):
    """Off-by-one matters: the cap is a count of toasts ALLOWED, so #9 is the
    first one suppressed when the cap is 8."""
    p = _run('for i in $(seq 1 9); do alert_capped load_alerts_today critical '
             '"⚠ High CPU load" "body $i"; done',
             env={"CPU_MON_MAX_ALERTS_PER_DAY": "8"}, tmp_path=tmp_path)
    assert len(p.toasts) == 8, "the 9th must be capped, got %d toasts" % len(p.toasts)
    assert "body 9" not in "\n".join(p.toasts)


def test_a_capped_alert_says_so_and_names_the_knob(tmp_path):
    """A suppressed alert must never be indistinguishable from an absent one."""
    p = _run('for i in $(seq 1 9); do alert_capped load_alerts_today critical '
             '"⚠ High CPU load" "body $i"; done',
             env={"CPU_MON_MAX_ALERTS_PER_DAY": "8"}, tmp_path=tmp_path)
    assert "capped" in p.stderr
    assert "CPU_MON_MAX_ALERTS_PER_DAY" in p.stderr, \
        "the journal line must name the knob that undoes the suppression"
    assert "body 9" in p.stderr, "the capped alert's content must still be recorded"


def test_cap_zero_means_unlimited(tmp_path):
    """The revert path for the cap."""
    p = _run('for i in $(seq 1 20); do alert_capped load_alerts_today critical '
             '"⚠ High CPU load" "body $i"; done',
             env={"CPU_MON_MAX_ALERTS_PER_DAY": "0"}, tmp_path=tmp_path)
    assert len(p.toasts) == 20


def test_load_and_runaway_have_independent_budgets(tmp_path):
    """One noisy trigger must not spend the other's budget — a single shared
    counter would pass every other cap test in this file."""
    p = _run('for i in $(seq 1 3); do alert_capped load_alerts_today critical "L" "l$i"; done\n'
             'for i in $(seq 1 3); do alert_capped runaway_alerts_today critical "R" "r$i"; done',
             env={"CPU_MON_MAX_ALERTS_PER_DAY": "3"}, tmp_path=tmp_path)
    assert len(p.toasts) == 6, \
        "3 load + 3 runaway with cap 3 each must all toast, got %d" % len(p.toasts)


def test_counters_reset_when_the_day_rolls(tmp_path):
    """The cap is per DAY. A counter that never resets would silence the host
    permanently after one busy day — a far worse failure than the noise."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    # A `date` whose +%F answer changes once a marker file exists. Anything other
    # than `+%F` is an error rather than a pass-through: `roll_alert_day` is the
    # only caller reached from here, so a fall-through would mean the harness had
    # drifted from the code and should say so loudly. (A pass-through would also
    # need an absolute path — `/usr/bin/env` does not exist in the nix sandbox.)
    mockbin.write_exec(
        bindir / "date",
        'if [ "$1" = "+%%F" ]; then\n'
        '  if [ -f "%s/day2" ]; then echo 2026-01-02; else echo 2026-01-01; fi\n'
        '  exit 0\n'
        'fi\n'
        'echo "date stub: unexpected args: $*" >&2\n'
        'exit 64\n' % tmp_path)
    p = _run('for i in $(seq 1 4); do alert_capped load_alerts_today critical "L" "d1-$i"; done\n'
             'touch "%s/day2"\n'
             'for i in $(seq 1 4); do alert_capped load_alerts_today critical "L" "d2-$i"; done'
             % tmp_path,
             env={"CPU_MON_MAX_ALERTS_PER_DAY": "2"}, tmp_path=tmp_path)
    joined = "\n".join(p.toasts)
    assert "d1-1" in joined and "d1-2" in joined and "d1-3" not in joined, \
        "day 1 must cap at 2: %r" % p.toasts
    assert "d2-1" in joined and "d2-2" in joined and "d2-3" not in joined, \
        "day 2 must get a FRESH budget of 2: %r" % p.toasts


# --------------------------------------------------------------------------
# THE EXEMPTION (structural — see the honesty note in the docstring)
# --------------------------------------------------------------------------

def test_temperature_alert_is_not_capped():
    """The one trigger whose absence would be noticed stays uncapped.

    This is a STRUCTURAL assert on the call site rather than a behavioural one:
    the temperature branch only runs inside the sampling loop, which this file
    deliberately does not execute. It asserts the call site's function NAME, not
    the presence of a word elsewhere, so an unrelated occurrence cannot satisfy
    it — but it would not catch a cap imposed inside `alert` itself, which is
    why `test_harness_can_observe_a_toast` drives `alert` directly.
    """
    src = open(_SCRIPT).read()
    m = re.search(r'^\s*(alert\w*)\s+critical\s+"🌡 High CPU temperature"',
                  src, re.MULTILINE)
    assert m, "could not find the temperature alert call site"
    assert m.group(1) == "alert", (
        "the temperature alert must call `alert` (uncapped), not %r. Thermal "
        "events are exactly when the 9th alert of the day matters." % m.group(1))


@pytest.mark.parametrize("summary,counter", [
    ("⚠ High CPU load", "load_alerts_today"),
    ("⚠ Runaway process: ${rcomm}", "runaway_alerts_today"),
])
def test_load_and_runaway_call_sites_are_capped(summary, counter):
    """The mirror of the exemption: these two MUST go through the cap, EACH WITH
    ITS OWN COUNTER.

    The counter name is asserted, not just the function name, because a mutation
    run found that pointing the runaway call site at `load_alerts_today` SURVIVED
    the whole suite: `test_load_and_runaway_have_independent_budgets` calls
    `alert_capped` directly with explicit counter names and never exercises the
    call sites, so it proved the mechanism can keep two budgets while the
    shipped code kept one.
    """
    src = open(_SCRIPT).read()
    m = re.search(r'^\s*(alert\w*)\s+(\S+)\s+critical\s+"' + re.escape(summary),
                  src, re.MULTILINE)
    assert m, "could not find the call site for %r" % summary
    assert m.group(1) == "alert_capped", (
        "%r must go through the daily cap, got %r" % (summary, m.group(1)))
    assert m.group(2) == counter, (
        "%r must spend its OWN budget %r, not %r — a shared counter lets one "
        "flapping trigger silence the other" % (summary, counter, m.group(2)))


def test_every_clear_notice_goes_through_clear_alert():
    """A LEDGER, not a spot check: the set of recovery call sites is asserted
    whole, so adding a fourth "✓ ..." that calls `alert low` directly goes red
    instead of quietly reintroducing uncapped recovery chatter."""
    src = open(_SCRIPT).read()
    body = src[src.find("\nwhile :;"):]
    sites = re.findall(r'(\w+)\s+(?:low\s+)?"(✓[^"]*)"', body)
    assert len(sites) == 3, \
        "expected exactly 3 recovery notices (load, runaway, temperature); got %r" % (sites,)
    for fn, summary in sites:
        assert fn == "clear_alert", \
            "recovery notice %r must go through clear_alert, not %r" % (summary, fn)
