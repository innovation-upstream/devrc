"""IGNORE-list tests for cpu-monitor — specifically the comm-truncation trap.

WHY THIS FILE EXISTS. Linux truncates a process comm to 15 chars, so
"Farthest Frontier" reaches cpu-monitor as "Farthest Fronti". An entry keyed on
the GAME'S NAME rather than on the comm — "frontier", the intuitive choice —
matches nothing, looks like it worked, and the alerts simply keep arriving while
the reader blames the threshold. `is_ignored` also splits on spaces as well as
commas, so a two-word entry silently becomes two independent substrings, one of
which is the dead one.

Measured 2026-08-12: 12 real alerts on the workbench, every one reading
"⚠ Runaway process: Farthest Fronti".

HOW THESE DRIVE REAL CODE: same prelude-sourcing harness as
test_cpu_monitor_volume.py — everything above the script's `while :;` loop is
sourced into a shell and the REAL `is_ignored` is called.
`test_the_prelude_really_contains_is_ignored` is the positive control on that
extraction; without it a broken extraction would make every NOMATCH assertion
below pass for the wrong reason.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/

_HERE = os.path.dirname(os.path.abspath(__file__))
# _HERE is scripts/tests, so parents[0] is scripts/ and parents[1] is the repo root.
_SCRIPTS = Path(_HERE).resolve().parents[0]
_ROOT = Path(_HERE).resolve().parents[1]
_SCRIPT = _SCRIPTS / "cpu-monitor.sh"

# Verbatim from dunst history on the workbench — ALREADY truncated by the kernel.
_REAL_COMM = "Farthest Fronti"


def _prelude():
    """Everything above the infinite sampling loop, so the real functions can be
    sourced without the script running forever."""
    src = _SCRIPT.read_text()
    cut = src.index("while :;")
    return src[:cut]


def _ignored(comm, ignore, tmp_path):
    script = tmp_path / "harness.sh"
    script.write_text(
        _prelude()
        + '\nif is_ignored "%s"; then echo MATCH; else echo NOMATCH; fi\n' % comm)
    e = dict(os.environ)
    for k in list(e):
        if k.startswith("CPU_MON_"):
            e.pop(k)
    e["CPU_MON_IGNORE"] = ignore
    p = subprocess.run(["bash", str(script)], env=e, capture_output=True,
                       text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    assert "MATCH" in p.stdout or "NOMATCH" in p.stdout, \
        "harness produced no verdict: %r / %s" % (p.stdout, p.stderr)
    return "MATCH" in p.stdout and "NOMATCH" not in p.stdout


def test_the_prelude_really_contains_is_ignored():
    """POSITIVE CONTROL on the extraction. If this captured nothing, every
    NOMATCH assertion below would be vacuously true."""
    assert "is_ignored()" in _prelude()


def test_is_ignored_can_say_NO(tmp_path):
    """POSITIVE CONTROL on the matcher: it must be capable of NOT matching, or
    'the game is ignored' proves nothing."""
    assert not _ignored(_REAL_COMM, "anno,logd", tmp_path)


def test_is_ignored_can_say_YES(tmp_path):
    """...and capable of matching, against an entry that is not under test."""
    assert _ignored("Anno1800.exe", "anno,logd", tmp_path)


def test_farthest_matches_the_TRUNCATED_comm(tmp_path):
    assert _ignored(_REAL_COMM, "anno,logd,farthest", tmp_path)


def test_frontier_does_NOT_match_the_truncated_comm(tmp_path):
    """The entire reason the entry is 'farthest'. 'frontier' is the intuitive
    choice and it is silently dead — pinned so nobody 'tidies' the list into
    uselessness."""
    assert not _ignored(_REAL_COMM, "anno,logd,frontier", tmp_path)


def test_a_two_word_entry_is_split_and_cannot_be_relied_on(tmp_path):
    """Documents the OTHER half of the trap: is_ignored splits on spaces, so
    'farthest frontier' is two substrings, and it only appears to work because
    the first one happens to match on its own."""
    assert _ignored(_REAL_COMM, "farthest frontier", tmp_path)
    assert not _ignored(_REAL_COMM, "frontier fronti_no_match", tmp_path)


def test_the_DEPLOYED_ignore_list_actually_covers_the_game(tmp_path):
    """SEAM: nix/home.nix owns the deployed value, cpu-monitor.sh owns the
    matcher. Each is fine in isolation; the PAIR is what has to hold. Read the
    real value out of home.nix rather than restating it here, so editing one
    side without the other goes red."""
    home_nix = _ROOT / "nix" / "home.nix"
    src = home_nix.read_text()
    m = re.search(r'"CPU_MON_IGNORE=([^"]*)"', src)
    assert m, "CPU_MON_IGNORE not found in nix/home.nix — the seam moved"
    deployed = m.group(1)
    assert _ignored(_REAL_COMM, deployed, tmp_path), (
        "the DEPLOYED CPU_MON_IGNORE=%r does not match %r" % (deployed, _REAL_COMM))
