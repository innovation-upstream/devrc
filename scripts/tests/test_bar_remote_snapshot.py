"""Tests for the remote-host bar relay: gather -> pull -> pill -> detail.

The feature lets the laptop show the WORKBENCH's bar state. Three pieces:

  scripts/bar-remote-snapshot   `--gather` on the observed host, `--pull` on the
                                observing one
  scripts/i3status-remote-host  the single `wb` pill
  scripts/remote-host-detail    the drill-down behind its click

🔴 WHAT THESE TESTS ARE FOR, stated so a reader does not have to infer it: the
relay must never invent an alarm the remote bar is not showing, and must never
hide one it IS showing. Those two are the whole contract. Everything below is
one of them.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]


def _load(name: str, alias: str):
    """Import one of the extensionless block scripts by explicit path."""
    sys.dont_write_bytecode = True
    path = SCRIPTS / name
    loader = importlib.machinery.SourceFileLoader(alias, str(path))
    spec = importlib.util.spec_from_loader(alias, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


snap = _load("bar-remote-snapshot", "_bar_remote_snapshot")


# ---------------------------------------------------------------------------
# parse_block_commands
# ---------------------------------------------------------------------------

_REAL_SHAPE = """
[[block]]
block = "custom"
command = "/home/z/.config/i3status-rust/scripts/i3status-clawgate --red-above 0"
json = true
interval = 30
signal = 11

[[block.click]]
button = "left"
cmd = "alacritty -e /home/z/scripts/clawgate-menu"

[[block]]
block = "custom"
command = "/home/z/.config/i3status-rust/scripts/i3blocks-rigcontrol"
interval = "once"

[[block.click]]
button = "left"
cmd = "setsid -f /home/z/workspace/devrc/scripts/rig-control-toggle"

[[block]]
block = "cpu"
format = " $icon $utilization "

[[block]]
block = "custom"
json = true
command = "/home/z/.config/i3status-rust/scripts/i3status-mail"
"""


def test_json_is_read_PER_BLOCK_and_never_assumed():
    """🔴 THE REGRESSION THIS FILE EXISTS FOR.

    i3status-rust's `custom` block defaults to `json = false` -- the command's
    stdout IS the pill text. MEASURED on the live workbench bar: 15 custom
    blocks, only 14 declare `json = true`. `i3blocks-rigcontrol` is an
    i3blocks-era script that prints a bare `☀`.

    The first version of the gather assumed JSON for every custom block and
    scored that perfectly healthy block as `unparseable` / Warning -- inventing
    an alarm on the observing host for a block the observed bar was rendering
    fine. That is the exact half of the contract this relay must not break.
    """
    specs = snap.parse_block_commands(_REAL_SHAPE)
    by_name = {snap.block_name(s["command"]): s for s in specs}
    assert set(by_name) == {"i3status-clawgate", "i3blocks-rigcontrol", "i3status-mail"}
    assert by_name["i3status-clawgate"]["json"] is True
    assert by_name["i3status-mail"]["json"] is True, \
        "`json = true` before `command` in the same stanza must still be seen"
    assert by_name["i3blocks-rigcontrol"]["json"] is False, \
        "a custom block with no `json =` key is PLAIN TEXT, not JSON"


def test_a_click_cmd_is_never_collected_as_a_block_command():
    """🔴 A click `cmd` opens menus, float terminals and toggles game mode.
    Collecting one would fire a side effect on the observed host every pull."""
    specs = snap.parse_block_commands(_REAL_SHAPE)
    cmds = [s["command"] for s in specs]
    assert not any("clawgate-menu" in c for c in cmds)
    assert not any("rig-control-toggle" in c for c in cmds)
    assert not any("setsid" in c for c in cmds)


def test_a_non_custom_block_is_not_collected():
    """Built-ins (cpu/net/disk/memory) have no command to run; they are
    gathered separately under `vitals`."""
    specs = snap.parse_block_commands(_REAL_SHAPE)
    assert not any("utilization" in s["command"] for s in specs)


def test_an_empty_config_yields_no_blocks_rather_than_raising():
    assert snap.parse_block_commands("") == []


# ---------------------------------------------------------------------------
# run_block
# ---------------------------------------------------------------------------

def _script(tmp_path: Path, body: str, name: str = "blk") -> str:
    p = tmp_path / name
    p.write_text(body)
    p.chmod(0o755)
    return str(p)


def test_run_block_relays_the_blocks_OWN_state_verbatim(tmp_path):
    """The verdict travels; the predicate does not. A relayed Critical must
    arrive as Critical without this code knowing what a threshold is."""
    cmd = _script(tmp_path, '#!/bin/sh\necho \'{"text":"234!11","state":"Critical"}\'\n')
    out = snap.run_block(cmd, dict(os.environ), is_json=True)
    assert out["text"] == "234!11"
    assert out["state"] == "Critical"
    assert not out.get("error")


def test_run_block_plain_text_takes_stdout_as_the_pill_and_stays_Idle(tmp_path):
    cmd = _script(tmp_path, "#!/bin/sh\necho '☀'\n")
    out = snap.run_block(cmd, dict(os.environ), is_json=False)
    assert out["text"] == "☀"
    assert out["state"] == "Idle"
    assert not out.get("error")


def test_run_block_plain_text_keeps_only_the_first_line(tmp_path):
    """i3blocks' 2nd/3rd lines are short-text and colour; i3status-rust ignores
    them, so relaying them would show text the remote bar does not."""
    cmd = _script(tmp_path, "#!/bin/sh\nprintf 'full\\nshort\\n#ff0000\\n'\n")
    out = snap.run_block(cmd, dict(os.environ), is_json=False)
    assert out["text"] == "full"


def test_an_EMPTY_pill_is_Idle_not_an_error(tmp_path):
    """Every count pill is hide-at-zero: measured-and-quiet prints nothing.
    Scoring that as a failure would make a healthy quiet bar look broken."""
    cmd = _script(tmp_path, "#!/bin/sh\nexit 0\n")
    out = snap.run_block(cmd, dict(os.environ), is_json=True)
    assert out["text"] == ""
    assert out["state"] == "Idle"
    assert not out.get("error")


def test_a_failing_block_is_RECORDED_not_dropped(tmp_path):
    """🔴 A dropped block is an alarm that silently stops being relayed."""
    cmd = _script(tmp_path, "#!/bin/sh\necho 'not json at all'\n")
    out = snap.run_block(cmd, dict(os.environ), is_json=True)
    assert out["error"] == "unparseable"
    assert out["state"] == "Warning"


def test_a_block_that_hangs_cannot_hang_the_gather(tmp_path, monkeypatch):
    monkeypatch.setattr(snap, "BLOCK_TIMEOUT_SECS", 1)
    cmd = _script(tmp_path, "#!/bin/sh\nsleep 30\n")
    started = time.time()
    out = snap.run_block(cmd, dict(os.environ), is_json=True)
    assert out["error"] == "timeout"
    assert out["state"] == "Warning"
    assert time.time() - started < 15


# ---------------------------------------------------------------------------
# label resolution
# ---------------------------------------------------------------------------

def test_the_label_is_DECLARED_and_never_silently_guessed(monkeypatch):
    """🔴 BOTH OF THIS OPERATOR'S HOSTS ARE NAMED `nixos`.

    Measured on each machine: `hostname` is `nixos` on the workbench AND the
    laptop. So a snapshot labelled from the hostname identifies neither, and two
    snapshots would collide on one name -- the same trap RULES records from the
    claim-lock's `uname -n`, where each host read the other's claims as its own.

    The label must therefore be declared, and when it is NOT, the snapshot must
    say so rather than present a coin-flip as a fact.
    """
    monkeypatch.delenv("BAR_HOST_LABEL", raising=False)
    assert snap.resolve_label("workbench") == ("workbench", "declared")

    monkeypatch.setenv("BAR_HOST_LABEL", "laptop")
    assert snap.resolve_label(None) == ("laptop", "env")
    assert snap.resolve_label("workbench") == ("workbench", "declared"), \
        "an explicit label must win over the environment"

    monkeypatch.delenv("BAR_HOST_LABEL", raising=False)
    label, source = snap.resolve_label(None)
    assert source == "hostname", \
        "a hostname-derived label MUST be marked as guessed, not passed off as declared"


def test_the_gather_records_label_source_so_a_reader_can_say_GUESSED(monkeypatch, tmp_path):
    monkeypatch.setattr(snap, "BAR_CONFIG", str(tmp_path / "nope.toml"))
    monkeypatch.delenv("BAR_HOST_LABEL", raising=False)
    assert snap.gather("workbench")["label_source"] == "declared"
    assert snap.gather(None)["label_source"] == "hostname"


# ---------------------------------------------------------------------------
# vitals
# ---------------------------------------------------------------------------

def test_vitals_ts_is_an_INT_because_bar_freshness_refuses_to_coerce(monkeypatch, tmp_path):
    """🔴 `bar_freshness.int_or_none` does not coerce. A float `time.time()`
    short-circuits every staleness case to `?` and looks exactly like a code
    bug -- the fixture rule the bar skill states explicitly."""
    monkeypatch.setattr(snap, "BAR_CONFIG", str(tmp_path / "nope.toml"))
    assert isinstance(snap.gather("workbench")["ts"], int)


def test_disk_is_deduped_by_DEVICE_not_by_the_rendered_numbers():
    """🔴 On this workbench `/` and `/home` are ONE filesystem (same st_dev), so
    a value-based dedup keeps both and the detail view prints the same free
    space twice as though they were two disks. Comparing the rendered dicts
    cannot catch it -- their `mount` keys differ, which is exactly the field
    that makes them look distinct."""
    disks = snap.gather_vitals().get("disk", [])
    devs = []
    for d in disks:
        devs.append(os.stat(d["mount"]).st_dev)
    assert len(devs) == len(set(devs)), \
        "two entries describe the same device: %r" % disks


def test_vitals_never_shells_out(monkeypatch):
    """A vitals gather reads /proc and statvfs only, so it cannot hang or depend
    on anything being installed. Pinned because reaching for `nvidia-smi` or
    `sensors` here is the obvious next edit."""
    def _boom(*a, **k):
        raise AssertionError("gather_vitals must not run a subprocess")
    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "check_output", _boom)
    snap.gather_vitals()


# ---------------------------------------------------------------------------
# pull / unreachable
# ---------------------------------------------------------------------------

def test_an_unreachable_peer_PRESERVES_the_last_good_snapshot(tmp_path, monkeypatch):
    """🔴 AN OUTAGE MAY MAKE A READING LESS TRUSTED; IT MAY NEVER MAKE A
    RECORDED ALARM QUIETER.

    Overwriting the cache with a bare error object would turn a workbench with
    two stuck dispatches into a silent bar on the observing host.
    """
    monkeypatch.setattr(snap, "CACHE_DIR", str(tmp_path))
    dest = tmp_path / "workbench.json"
    good = {"schema": 1, "host": "workbench", "ts": int(time.time()),
            "blocks": [{"name": "i3status-clawgate", "text": "234!11",
                        "state": "Critical"}], "vitals": {}}
    dest.write_text(json.dumps(good))

    rc = snap._write_unreachable(str(dest), "workbench", "ssh timeout")
    assert rc == 0, "an unreachable peer is an expected state, not a unit failure"

    after = json.loads(dest.read_text())
    assert after["state"] == "unreachable"
    assert after["detail"] == "ssh timeout"
    assert after["last_good"]["blocks"][0]["text"] == "234!11", \
        "the alarm the peer last reported must survive the outage"


def test_repeated_outages_do_not_nest_last_good_forever(tmp_path, monkeypatch):
    """A second failure must carry the ORIGINAL good reading, not wrap the
    previous error object -- otherwise the alarm sinks one level deeper per
    poll and the pill stops finding it."""
    monkeypatch.setattr(snap, "CACHE_DIR", str(tmp_path))
    dest = tmp_path / "workbench.json"
    good = {"schema": 1, "host": "workbench", "ts": int(time.time()),
            "blocks": [{"name": "x", "text": "ALARM", "state": "Critical"}]}
    dest.write_text(json.dumps(good))

    snap._write_unreachable(str(dest), "workbench", "first")
    snap._write_unreachable(str(dest), "workbench", "second")

    after = json.loads(dest.read_text())
    assert after["detail"] == "second"
    assert "last_good" not in after["last_good"], "last_good nested inside itself"
    assert after["last_good"]["blocks"][0]["text"] == "ALARM"


def test_an_unreachable_peer_SAYS_WHY_on_stderr(tmp_path, monkeypatch, capsys):
    """🔴 The exit code is deliberately 0, so this line is the ONLY thing that
    distinguishes a permanently-broken pull (wrong host, no key) from the
    transient one it is designed to absorb."""
    monkeypatch.setattr(snap, "CACHE_DIR", str(tmp_path))
    dest = tmp_path / "workbench.json"
    snap._write_unreachable(str(dest), "workbench", "ssh rc=255: no route")
    err = capsys.readouterr().err
    assert "UNREACHABLE" in err and "no route" in err


def test_the_cache_file_is_written_atomically_and_private(tmp_path, monkeypatch):
    monkeypatch.setattr(snap, "CACHE_DIR", str(tmp_path))
    dest = tmp_path / "workbench.json"
    snap._atomic_write(str(dest), {"ts": 1, "host": "workbench"})
    assert json.loads(dest.read_text())["host"] == "workbench"
    assert oct(dest.stat().st_mode)[-3:] == "600"
    assert not list(tmp_path.glob(".snap-*")), "temp file left behind"


def test_pull_requires_ssh_dest():
    with pytest.raises(SystemExit):
        snap.main(["--pull", "workbench"])


def test_no_verb_is_an_error_not_a_silent_success():
    with pytest.raises(SystemExit):
        snap.main([])


# ---------------------------------------------------------------------------
# the pill: scripts/i3status-remote-host
# ---------------------------------------------------------------------------
#
# Run as a SUBPROCESS against a temp scripts dir, because the two things most
# worth pinning are about its DEPLOYED directory: whether the `bar_freshness.py`
# sibling is beside it, and whether a failure there still renders a pill. An
# in-process import cannot see either.

PILL = "i3status-remote-host"


def _pill_dir(tmp_path: Path, with_sibling: bool = True) -> Path:
    d = tmp_path / "scripts"
    d.mkdir(exist_ok=True)
    for name in (PILL, "bar-remote-snapshot"):
        tgt = d / name
        tgt.write_bytes((SCRIPTS / name).read_bytes())
        tgt.chmod(0o755)
    if with_sibling:
        (d / "bar_freshness.py").write_bytes((SCRIPTS / "bar_freshness.py").read_bytes())
    return d


def _run_pill(tmp_path: Path, payload, *args, with_sibling: bool = True) -> dict:
    d = _pill_dir(tmp_path, with_sibling)
    cache = tmp_path / "cache" / "bar-remote"
    cache.mkdir(parents=True, exist_ok=True)
    if payload is not None:
        (cache / "workbench.json").write_text(json.dumps(payload))
    env = dict(os.environ, XDG_CACHE_HOME=str(tmp_path / "cache"),
               PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run([sys.executable, str(d / PILL), *args],
                          capture_output=True, text=True, env=env, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip(), "the pill printed NOTHING -- an invisible pill"
    return json.loads(proc.stdout)


def _live(blocks, ts=None):
    return {"schema": 1, "host": "workbench", "ts": ts or int(time.time()),
            "blocks": blocks, "vitals": {}}


def test_the_pill_says_ok_when_the_peer_is_CURRENT_and_QUIET(tmp_path):
    out = _run_pill(tmp_path, _live([{"name": "i3status-mail", "text": "", "state": "Idle"}]))
    assert "ok" in out["text"]
    assert out["state"] == "Idle"


def test_the_pill_is_NEVER_EMPTY_even_when_all_is_well(tmp_path):
    """🔴 The one pill on this bar that must not hide at zero. It describes a
    machine nobody is looking at, so 'quiet' and 'I have not heard from it in an
    hour' would both render as nothing -- and the second is when you need it."""
    out = _run_pill(tmp_path, _live([{"name": "x", "text": "", "state": "Idle"}]))
    assert out["text"].strip(), "an invisible pill cannot report an unreachable peer"


def test_the_pill_RELAYS_a_remote_alarm_without_recomputing_it(tmp_path):
    out = _run_pill(tmp_path, _live([
        {"name": "i3status-clawgate", "text": "234!11", "state": "Critical"},
        {"name": "i3status-mail", "text": "", "state": "Idle"}]))
    assert "234!11" in out["text"]
    assert out["state"] == "Critical", "a relayed Critical must arrive as Critical"


def test_the_pill_rolls_up_to_the_WORST_state(tmp_path):
    out = _run_pill(tmp_path, _live([
        {"name": "a", "text": "x", "state": "Warning"},
        {"name": "b", "text": "y", "state": "Critical"},
        {"name": "c", "text": "", "state": "Idle"}]))
    assert out["state"] == "Critical"


def test_a_STALE_snapshot_gets_the_trailing_question_mark(tmp_path):
    """The bar-wide grammar: a trailing `?` means 'not a current measurement'.
    It does NOT mean quiet."""
    old = int(time.time()) - 4000
    out = _run_pill(tmp_path, _live([{"name": "x", "text": "", "state": "Idle"}], ts=old))
    assert out["text"].rstrip().endswith("?")
    assert out["state"] == "Warning", \
        "a reading we cannot refresh is never better news than a quiet one we can"


def test_an_UNREACHABLE_peer_still_shows_the_alarms_it_last_reported(tmp_path):
    """🔴 THE CARRY. An outage may make a reading less trusted; it may never
    make a recorded alarm quieter."""
    good = _live([{"name": "i3status-clawgate", "text": "234!11", "state": "Critical"}])
    out = _run_pill(tmp_path, {"schema": 1, "host": "workbench",
                               "ts": int(time.time()), "state": "unreachable",
                               "detail": "ssh timeout", "last_good": good})
    assert "234!11" in out["text"], "the alarm went silent during an outage"
    assert out["text"].rstrip().endswith("?")
    assert out["state"] == "Critical"


def test_a_MISSING_snapshot_renders_a_visible_question_mark(tmp_path):
    out = _run_pill(tmp_path, None)
    assert "?" in out["text"]
    assert out["state"] == "Warning"


def test_ZERO_blocks_is_not_reported_as_ok(tmp_path):
    """🔴 THE SILENT ZERO. Current, reachable, and reporting no blocks at all
    means the gather asked nothing -- saying `ok` there is a confident all-clear
    from an instrument wired to nothing."""
    out = _run_pill(tmp_path, _live([]))
    assert "ok" not in out["text"]
    assert "?" in out["text"]
    assert out["state"] == "Warning"


def test_the_pill_renders_even_with_NO_bar_freshness_SIBLING(tmp_path):
    """🔴 The deployed-seam failure the other blocks pin too: a missing sibling
    symlink must produce a VISIBLE `?`, never a dead pill. The load is deferred
    (`fresh = None`) precisely so this path is reachable."""
    out = _run_pill(tmp_path, _live([{"name": "x", "text": "", "state": "Idle"}]),
                    with_sibling=False)
    assert out["text"].strip(), "no sibling => EMPTY pill (the dead-block defect)"
    assert "?" in out["text"]


def test_remote_PANGO_markup_cannot_leak_into_the_pill(tmp_path):
    """The scratchpad legend relays as a row of coloured spans. Passing it
    through would let a remote block's markup decide how this pill renders;
    printing it raw would show literal `<span ...>` on the bar."""
    out = _run_pill(tmp_path, _live([
        {"name": "i3status-scratchpads",
         "text": '<span foreground="#b8bb26">g1</span>', "state": "Warning"}]))
    assert "<span foreground=\"#b8bb26\">" not in out["text"], "relayed markup leaked"
    assert "g1" in out["text"]
    # our OWN host tag is the only markup, and it must be well formed
    assert out["text"].count("<span") == 1
    assert out["text"].count("</span>") == 1


def test_the_host_label_is_wb_not_a_two_character_slice(tmp_path):
    """`host[:2]` spells `workbench` as `wo` -- wrong, and unreadable next to a
    `wb` the operator already says out loud."""
    out = _run_pill(tmp_path, _live([{"name": "x", "text": "", "state": "Idle"}]))
    assert ">wb<" in out["text"], out["text"]


def test_an_errored_remote_block_is_surfaced_not_smoothed_away(tmp_path):
    out = _run_pill(tmp_path, _live([
        {"name": "i3status-media", "text": "", "state": "Idle", "error": "timeout"}]))
    assert out["state"] in ("Warning", "Critical")
    assert "ok" not in out["text"]


# ---------------------------------------------------------------------------
# the detail view: scripts/remote-host-detail
# ---------------------------------------------------------------------------

def test_the_detail_view_names_BOTH_hosts_and_holds_nothing_back(tmp_path):
    d = _pill_dir(tmp_path)
    tgt = d / "remote-host-detail"
    tgt.write_bytes((SCRIPTS / "remote-host-detail").read_bytes())
    tgt.chmod(0o755)
    cache = tmp_path / "cache" / "bar-remote"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "workbench.json").write_text(json.dumps(_live([
        {"name": "i3status-clawgate", "text": "234!11", "state": "Critical"},
        {"name": "i3status-mail", "text": "", "state": "Idle"}])))
    env = dict(os.environ, XDG_CACHE_HOME=str(tmp_path / "cache"),
               PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run(
        [sys.executable, str(tgt), "--host", "workbench",
         "--local-label", "laptop", "--no-hold"],
        capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    body = proc.stdout
    assert "WORKBENCH" in body and "LAPTOP" in body, \
        "both hosts must be NAMED -- colour alone is unreadable in this font"
    assert "234!11" in body
    assert "clawgate" in body


def test_the_detail_view_says_LAST_GOOD_rather_than_presenting_it_as_current(tmp_path):
    d = _pill_dir(tmp_path)
    tgt = d / "remote-host-detail"
    tgt.write_bytes((SCRIPTS / "remote-host-detail").read_bytes())
    tgt.chmod(0o755)
    cache = tmp_path / "cache" / "bar-remote"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "workbench.json").write_text(json.dumps(
        {"schema": 1, "host": "workbench", "ts": int(time.time()),
         "state": "unreachable", "detail": "ssh timeout",
         "last_good": _live([{"name": "x", "text": "ALARM", "state": "Critical"}])}))
    env = dict(os.environ, XDG_CACHE_HOME=str(tmp_path / "cache"),
               PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run(
        [sys.executable, str(tgt), "--host", "workbench", "--no-hold"],
        capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "UNREACHABLE" in proc.stdout
    assert "LAST GOOD" in proc.stdout, \
        "stale data presented without saying so is the whole defect class"
    assert "ALARM" in proc.stdout


def test_the_detail_view_does_not_invent_a_hostname_for_THIS_host(tmp_path):
    """🔴 Both machines are named `nixos`, so the hostname names neither. With
    nothing declared the view must say `this-host`, which claims nothing,
    rather than a confident name identical on either machine.

    ⚠ ASSERTED BEHAVIOURALLY, and the first version of this test was not. It
    grepped the source for `os.uname().nodename` -- and matched the COMMENT
    explaining why that call is not used, so it failed against correct code. A
    guard on a WORD is walkable by rewording in both directions; this one runs
    the thing and reads what it printed.
    """
    d = _pill_dir(tmp_path)
    tgt = d / "remote-host-detail"
    tgt.write_bytes((SCRIPTS / "remote-host-detail").read_bytes())
    tgt.chmod(0o755)
    cache = tmp_path / "cache" / "bar-remote"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "workbench.json").write_text(json.dumps(
        _live([{"name": "x", "text": "", "state": "Idle"}])))
    env = dict(os.environ, XDG_CACHE_HOME=str(tmp_path / "cache"),
               PYTHONDONTWRITEBYTECODE="1")
    env.pop("BAR_HOST_LABEL", None)
    env.pop("ACTIVITY_HOST", None)
    proc = subprocess.run(
        [sys.executable, str(tgt), "--host", "workbench", "--no-hold"],
        capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "THIS-HOST" in proc.stdout.upper(), \
        "with no declared label the local section must claim no identity"
    real_hostname = os.uname().nodename
    local_section = proc.stdout.split("this host")[0][-400:] \
        if "this host" in proc.stdout else proc.stdout
    assert real_hostname.upper() not in local_section.upper() or real_hostname == "this-host", \
        "the view printed the machine's hostname as an identity: %r" % real_hostname
