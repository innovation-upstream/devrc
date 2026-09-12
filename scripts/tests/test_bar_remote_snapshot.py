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
import re
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


def _hermetic_env(tmp_path: Path) -> dict:
    """A subprocess env that cannot reach the OPERATOR'S OWN deployed bar.

    🔴 WITHOUT `XDG_CONFIG_HOME` THESE TESTS EXECUTE THE REAL BAR. The detail
    view calls `gather_local()`, which spawns `bar-remote-snapshot --gather`,
    which reads `$XDG_CONFIG_HOME|~/.config/i3status-rust/config-top.toml` and
    runs every RELAY_BLOCKS command it finds there through `sh -c`. Setting only
    `XDG_CACHE_HOME` left that half live: MEASURED, the suite shelled out to the
    developer's tmux server, `/sys/class/hwmon` and `/proc` on every run, via
    ABSOLUTE paths that `nolaunch`'s PATH stubbing cannot intercept.

    It also made the local-gather half assert nothing anywhere: on the dev host
    it ran unasserted (every assertion targets the remote fixture), and in the
    `nix build` sandbox there is no `~/.config` at all, so it short-circuited to
    an empty block list. Executed in one tier, absent in the other, asserted in
    neither.
    """
    cfg = tmp_path / "config"
    (cfg / "i3status-rust").mkdir(parents=True, exist_ok=True)
    (cfg / "i3status-rust" / "config-top.toml").write_text(
        '[[block]]\nblock = "custom"\njson = true\ncommand = "/bin/echo {}"\n')
    return dict(os.environ,
                XDG_CACHE_HOME=str(tmp_path / "cache"),
                XDG_CONFIG_HOME=str(cfg),
                PYTHONDONTWRITEBYTECODE="1")


def _run_pill(tmp_path: Path, payload, *args, with_sibling: bool = True) -> dict:
    d = _pill_dir(tmp_path, with_sibling)
    cache = tmp_path / "cache" / "bar-remote"
    cache.mkdir(parents=True, exist_ok=True)
    if payload is not None:
        (cache / "workbench.json").write_text(json.dumps(payload))
    env = _hermetic_env(tmp_path)
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


def test_the_deployed_pill_is_labelled_wb_BY_NIX(tmp_path):
    """The label is a deployment fact, not a script default.

    An earlier version of this test asserted the SCRIPT's default was `wb`,
    which pinned a `_HOST_LABELS` map that production never executed -- nix
    passes `--label` at every call site. Round 0 flagged the map as dead code
    defended in the PR body; it is gone, and what is pinned now is the thing
    that actually decides what the operator sees.
    """
    nix = (SCRIPTS.parent / "nix" / "graphical.nix").read_text()
    assert "i3status-remote-host --host workbench --label wb" in nix, \
        "the deployed pill command no longer passes an explicit --label"
    out = _run_pill(tmp_path, _live([{"name": "x", "text": "", "state": "Idle"}]),
                    "--label", "wb")
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
    env = _hermetic_env(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(tgt), "--host", "workbench",
         "--local-label", "laptop", "--no-hold"],
        capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    body = proc.stdout
    assert "WORKBENCH" in body and "LAPTOP" in body, \
        "both hosts must be NAMED -- colour alone is unreadable in this font"
    # 🔴 POSITIVE CONTROL FOR HERMETICITY. `_hermetic_env` points
    # XDG_CONFIG_HOME at a fixture whose only custom block is `/bin/echo {}`,
    # so the LOCAL half of this view must show that and nothing else. If the
    # real deployed bar leaked in, its block names appear here instead -- which
    # is exactly what happened before, unnoticed, because every assertion above
    # targets the REMOTE fixture and none looked at the local half at all.
    for leaked in ("fans", "scratchpads", "rigcontrol", "claude-runs"):
        assert leaked not in body.split("this host")[-1], (
            "the local gather reached the OPERATOR'S REAL BAR: found %r" % leaked)
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
    env = _hermetic_env(tmp_path)
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
    env = _hermetic_env(tmp_path)
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


# ---------------------------------------------------------------------------
# block classification — the pin that would have caught the category error
# ---------------------------------------------------------------------------

def _custom_block_scripts_in_nix() -> set:
    """Every custom block's script basename, read out of nix/graphical.nix."""
    nix = (SCRIPTS.parent / "nix" / "graphical.nix").read_text()
    found = set()
    for m in re.finditer(r'command = "\$\{scriptsDir\}/([A-Za-z0-9_.-]+)', nix):
        found.add(m.group(1))
    return found


def test_every_custom_block_is_classified_exactly_once():
    """🔴 THE GUARD THE FIRST VERSION OF THIS FEATURE NEEDED AND DID NOT HAVE.

    The relay originally carried ALL 15 custom blocks under a `wb` label. Six of
    them are GLOBAL-SERVICE facts -- the clawgate board, homelab and client-prod
    Alertmanager, shared ClickHouse, a homelab qBittorrent pod -- whose numbers
    are identical whichever host reads them. Relaying those as "the workbench's
    state" is a category error, and because those backlogs are large and
    standing it made the pill permanently Critical: MEASURED 2026-09-12, three
    standing Criticals of which NOT ONE was a workbench fact.

    A new block must not be able to default into the relay silently. So the
    three sets are pinned two-way against the nix that defines the blocks: a
    block in no set fails here, and a set naming a block that no longer exists
    fails here too.
    """
    classified = snap.RELAY_BLOCKS | snap.NATIVE_BLOCKS | snap.LOCAL_ONLY_BLOCKS
    in_nix = _custom_block_scripts_in_nix()
    assert in_nix, "read no custom block commands out of graphical.nix — vacuous"

    unclassified = in_nix - classified
    assert not unclassified, (
        "custom block(s) in nix/graphical.nix with no classification: %r\n"
        "Add each to RELAY_BLOCKS (this host's own fact), NATIVE_BLOCKS (a "
        "global service the observer should run itself) or LOCAL_ONLY_BLOCKS "
        "(the observer has its own). Defaulting into the relay is the category "
        "error this guard exists to prevent." % sorted(unclassified))

    phantom = classified - in_nix
    assert not phantom, (
        "classified block(s) that are not custom blocks in graphical.nix: %r"
        % sorted(phantom))


def test_the_three_classes_are_DISJOINT():
    """A block in two sets would be both relayed and expected native — the pill
    and the local bar would then show the same fact twice, under two labels."""
    assert not (snap.RELAY_BLOCKS & snap.NATIVE_BLOCKS)
    assert not (snap.RELAY_BLOCKS & snap.LOCAL_ONLY_BLOCKS)
    assert not (snap.NATIVE_BLOCKS & snap.LOCAL_ONLY_BLOCKS)


def test_no_GLOBAL_SERVICE_block_is_relayed_under_a_host_label():
    """The specific regression: these six must never travel in the snapshot."""
    for name in ("i3status-clawgate", "i3status-alerts", "i3status-civitai",
                 "i3status-mail", "i3status-telemetry", "i3status-media"):
        assert name in snap.NATIVE_BLOCKS, name
        assert name not in snap.RELAY_BLOCKS, \
            "%s is a global-service fact; relaying it labels it as one host's" % name


def test_the_unseen_notification_backlog_is_not_relayed():
    """🔴 D7. `i3status-notifs` is the observed host's UNSEEN dunst backlog —
    185 at measurement, Critical, growing while the operator is away, and
    unactionable from the other machine because the relayed pill has no `seen`
    path. It was the loudest single contributor to the permanently-red pill."""
    assert "i3status-notifs" in snap.LOCAL_ONLY_BLOCKS
    assert "i3status-notifs" not in snap.RELAY_BLOCKS


def test_the_gather_relays_ONLY_the_classified_relay_set(tmp_path, monkeypatch):
    toml = "\n".join(
        '[[block]]\nblock = "custom"\njson = true\ncommand = "/x/%s"\n' % n
        for n in ("i3status-clawgate", "i3status-load", "i3status-notifs"))
    cfg = tmp_path / "config-top.toml"
    cfg.write_text(toml)
    monkeypatch.setattr(snap, "BAR_CONFIG", str(cfg))
    monkeypatch.setattr(snap, "POLLER_CACHE_DIR", str(tmp_path / "nocache"))
    monkeypatch.setattr(snap, "run_block",
                        lambda cmd, env, is_json=True: {"name": snap.block_name(cmd),
                                                        "text": "", "state": "Idle"})
    out = snap.gather("workbench")
    assert [b["name"] for b in out["blocks"]] == ["i3status-load"]
    assert out["not_relayed"] == ["i3status-clawgate", "i3status-notifs"], \
        "what was withheld must be RECORDED — 'this host does not run it' and " \
        "'the relay declined it' look identical otherwise"


# ---------------------------------------------------------------------------
# the poller-cache relay — what makes the six native blocks work on the observer
# ---------------------------------------------------------------------------

def test_the_poller_cache_travels_VERBATIM(tmp_path, monkeypatch):
    """🔴 Nothing is interpreted. The observer runs the SAME block scripts
    against the SAME bytes, so it reaches the same verdict by the same code with
    the same thresholds. Re-deriving anything here would turn a shared fact into
    a second opinion — the failure that made relaying them wrong to begin with."""
    src = tmp_path / "bar-status"
    src.mkdir()
    body = '{"ts": 1789000000, "count": 233, "stuck_count": 10}'
    (src / "clawgate.json").write_text(body)
    (src / "clawgate.toast-state").write_text("fired")
    monkeypatch.setattr(snap, "POLLER_CACHE_DIR", str(src))
    cache = snap.gather_poller_cache()
    assert cache["clawgate.json"] == body, "payload was altered in transit"
    assert "clawgate.toast-state" not in cache, \
        "the rising-edge LATCH belongs to the host that fires toasts; copying " \
        "it lets the observer clear a live latch and cause a re-toast"


def test_installing_the_cache_lands_it_where_the_observers_blocks_read_it(tmp_path, monkeypatch):
    dest = tmp_path / "bar-status"
    monkeypatch.setattr(snap, "POLLER_CACHE_DIR", str(dest))
    monkeypatch.setattr(snap, "local_poller_is_running", lambda: False)
    snap.install_poller_cache({"mail.json": '{"ts": 1789000000, "count": 5}'})
    got = json.loads((dest / "mail.json").read_text())
    assert got["count"] == 5
    assert oct((dest / "mail.json").stat().st_mode)[-3:] == "600"


def test_the_install_REFUSES_when_a_LOCAL_poller_is_running(tmp_path, monkeypatch, capsys):
    """🔴 The one collision this feature can cause. Two writers on one cache
    dir make the pills flip between local and relayed readings with no way to
    tell which you are looking at."""
    dest = tmp_path / "bar-status"
    monkeypatch.setattr(snap, "POLLER_CACHE_DIR", str(dest))
    monkeypatch.setattr(snap, "local_poller_is_running", lambda: True)
    snap.install_poller_cache({"mail.json": '{"ts": 1, "count": 5}'})
    assert not dest.exists(), "overwrote a live local poller's cache"
    assert "NOT installing" in capsys.readouterr().err


def test_a_relayed_cache_NAME_cannot_escape_the_directory(tmp_path, monkeypatch):
    dest = tmp_path / "bar-status"
    monkeypatch.setattr(snap, "POLLER_CACHE_DIR", str(dest))
    monkeypatch.setattr(snap, "local_poller_is_running", lambda: False)
    snap.install_poller_cache({
        "../../evil.json": '{"ts": 1}',
        "/etc/evil.json": '{"ts": 1}',
        "notjson.txt": "x",
        "ok.json": '{"ts": 1}',
    })
    assert sorted(p.name for p in dest.iterdir()) == ["ok.json"]


def test_the_local_poller_probe_FAILS_SAFE(monkeypatch):
    """If systemd cannot be asked, assume no local poller and proceed —
    refusing on an unanswerable question would break the pull on any host
    without systemd at all."""
    def _boom(*a, **k):
        raise FileNotFoundError("no systemctl")
    monkeypatch.setattr(subprocess, "run", _boom)
    assert snap.local_poller_is_running() is False


# ---------------------------------------------------------------------------
# the nix wiring for the split
# ---------------------------------------------------------------------------

def _nix() -> str:
    return (SCRIPTS.parent / "nix" / "graphical.nix").read_text()


def test_the_global_service_blocks_render_on_BOTH_hosts():
    """They are not one host's facts, so both bars show them."""
    nix = _nix()
    assert "++ [ telemetryBlock alertsBlock civitaiBlock mailBlock clawgateBlock mediaBlock ]" in nix
    # Derived from the classification, not retyped: a hardcoded list lets a
    # seventh native block stay workbench-only with the suite green.
    for script in sorted(snap.NATIVE_BLOCKS):
        line = 'home.file.".config/i3status-rust/scripts/%s" = {' % script
        assert line in nix, "%s is not deployed on both hosts — dead pill" % script


def test_the_LAN_BOUND_clicks_are_withheld_from_the_laptop():
    """🔴 MEASURED: these clicks target `grafana.homelab.lan`,
    `qbittorrent.workbench.lan` and `http://192.168.50.250:30302` — a LAN
    hostname, a LAN hostname and a LAN IP, none of which resolve from a
    nebula-only laptop; civitai and media additionally need per-host 0600
    credential files that do not exist there. Shipping them would put several
    silently dead buttons on the laptop bar."""
    nix = _nix()
    for blk in ("telemetryBlock", "alertsBlock", "civitaiBlock", "mailBlock",
                "clawgateBlock", "mediaBlock"):
        m = re.search(r"^  %s = \{\n(.*?)^  \};$" % blk, nix, re.M | re.S)
        assert m, blk
        body = m.group(1)
        assert "click = lib.optionals (!isLaptop) [" in body, \
            "%s's click list is not withheld from the laptop" % blk


def test_the_pull_and_the_poller_can_NEVER_run_on_the_same_host():
    """The structural half of the collision guard: nix must gate the relayed-cache
    writer and the local poller mutually exclusively."""
    nix = _nix()
    assert "systemd.user.timers.bar-remote-pull = lib.mkIf isLaptop" in nix
    assert "systemd.user.timers.bar-status-poll = lib.mkIf (!isLaptop)" in nix


def test_the_relayed_cache_dir_MATCHES_what_the_block_scripts_actually_read():
    """🔴 A SHARED CONTRACT WITH CODE THIS FEATURE DOES NOT OWN.

    The global-service pills only work on the observer because the pull writes
    their cache where THEY look. Every `i3status-*` block derives that path as
    `expanduser("~") / ".cache" / "bar-status"` and does NOT consult
    `XDG_CACHE_HOME`.

    An earlier version of `POLLER_CACHE_DIR` honoured XDG — tidier, and wrong:
    MEASURED on a host with the variable set, the pull reported success, the
    files landed, and all four global pills sat on `?` because the blocks were
    reading a different directory. A sync that succeeds while the pills stay
    blank is the worst shape available.

    Asserted against the BLOCKS' own source, not against a literal, so a future
    change on either side has to move both.
    """
    ours = snap.POLLER_CACHE_DIR
    checked = 0
    for name in sorted(snap.NATIVE_BLOCKS):
        block = SCRIPTS / name
        if not block.exists():
            continue
        src = block.read_text()
        assert "XDG_CACHE_HOME" not in src, (
            "%s now reads XDG_CACHE_HOME; POLLER_CACHE_DIR must follow it or "
            "the relayed cache lands where the block does not look" % name)
        assert '".cache", "bar-status"' in src.replace("'", '"'), (
            "%s no longer derives the documented cache path — re-check the "
            "contract rather than assuming it still holds" % name)
        checked += 1
    assert checked, "no NATIVE block scripts found — this test measured nothing"
    assert ours == os.path.join(os.path.expanduser("~"), ".cache", "bar-status"), \
        "POLLER_CACHE_DIR (%r) is not the path the blocks read" % ours


def test_the_relayed_cache_set_is_DERIVED_from_NATIVE_BLOCKS():
    """🔴 The classification must govern the CACHE too, not only the blocks.

    `gather_poller_cache` filtered on `.json` alone and carried all eight poller
    sources — including `airvpn.json` and `runaways.json`, whose blocks this
    same module lists in RELAY_BLOCKS as "genuinely this host's own state". The
    block split was pinned; the cache one layer down was not, so the category
    error the whole feature exists to fix was still live there.

    Not merely untidy: `i3status-airvpn` is one `mkIf` from the laptop, and the
    moment it lands there the observer's airvpn pill would report the OBSERVED
    host's tunnel as its own — fresh `ts`, no `wb` label, no `?`.
    """
    wanted = snap.native_cache_filenames()
    assert wanted == {n.split("-", 1)[1] + ".json" for n in snap.NATIVE_BLOCKS}
    for relayed in snap.RELAY_BLOCKS:
        if "-" not in relayed:
            continue
        assert relayed.split("-", 1)[1] + ".json" not in wanted, \
            "%s is a HOST-LOCAL block; its poller cache must not be installed " \
            "on the observer, where its pill would read as the observer's own" \
            % relayed


def test_a_HOST_LOCAL_source_is_not_carried(tmp_path, monkeypatch):
    src = tmp_path / "bar-status"
    src.mkdir()
    for f in ("clawgate.json", "airvpn.json", "runaways.json", "mail.json"):
        (src / f).write_text('{"ts": 1789000000}')
    monkeypatch.setattr(snap, "POLLER_CACHE_DIR", str(src))
    got = set(snap.gather_poller_cache())
    assert "clawgate.json" in got and "mail.json" in got
    assert "airvpn.json" not in got, "a RELAY_BLOCKS source leaked into the cache relay"
    assert "runaways.json" not in got


def test_the_global_service_block_deploy_list_is_DERIVED_not_hardcoded():
    """A seventh NATIVE block must not be able to stay workbench-only silently:
    it would be neither deployed on the observer nor relayed, so it would
    vanish from that bar entirely — no pill, no `?`, nothing."""
    nix = _nix()
    for name in sorted(snap.NATIVE_BLOCKS):
        line = 'home.file.".config/i3status-rust/scripts/%s" = {' % name
        assert line in nix, (
            "%s is a NATIVE block but is not deployed on both hosts — on the "
            "observer it would be absent rather than showing a `?`" % name)
