"""Unit tests for the `i3status-stt` block (hold-to-talk voice input).

The Go tool `stt-voice` (nix/pkgs/tools/stt-voice) OWNS the state file
(`~/.cache/stt-voice/state.json`): $mod+m press starts recording, release stops
+ transcribes + opens the transcript TUI, and every state write is atomic. This
block is the READ half: one local file read per tick, no poller, no network,
and a left-click that re-invokes the tool's own `toggle`.

All HERMETIC: state files are fixtures under tmp_path, `--state` points the
script at them, and the toggle e2e runs with a PATH that holds nothing. None
of these tests needs a mic, an X11 server, or the ASR endpoint.

    run:  pytest scripts/tests/test_i3status_stt.py
"""
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
STT_SCRIPT = SCRIPTS / "i3status-stt"


def _load(name, modname):
    loader = importlib.machinery.SourceFileLoader(modname, str(SCRIPTS / name))
    spec = importlib.util.spec_from_loader(modname, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


stt = _load("i3status-stt", "i3status_stt")


# --------------------------------------------------------------------------- #
# parse_state: the state file is the tool's contract, and every malformed
# shape must be UNMEASURED (None), never coerced into a state.
# --------------------------------------------------------------------------- #
def test_parse_state_reads_the_tool_state_file():
    st = stt.parse_state(
        '{"state":"recording","pid":123,"started_at":1758400000.0,'
        '"last_error":""}')
    assert st == {"state": "recording", "pid": 123,
                  "started_at": 1758400000.0, "last_error": ""}


@pytest.mark.parametrize("raw", [
    "",                    # empty file / truncated write
    "   \n",
    "not json at all",
    "[]",                  # a list, not the object we expect
    "null",
    '{"nope":1}',          # right shape, wrong key
    '{"state":null}',
    '{"state":42}',
    '{"state":"   "}',     # blank is not a state name
    '{"state":"recording","pid":"x"}',          # pid must be an int
    '{"state":"recording","pid":null}',
    '{"state":"recording","started_at":"soon"}',  # started_at must be a number
])
def test_parse_state_is_UNMEASURED_not_a_guess(raw):
    """🔴 None, never a coerced dict. The render matrix branches on the state
    string, so a malformed file that parse-coerced into `idle` would HIDE a
    recording that is actually live (the opposite of the dead-pid guard's
    intent), and one that coerced into `recording` would show a phantom REC."""
    assert stt.parse_state(raw) is None


def test_parse_state_tolerates_a_partial_but_honest_file(tmp_path):
    """`pid`/`started_at`/`last_error` are optional per key — the writer
    updates `state` first on some paths (and history never depends on them)."""
    st = stt.parse_state('{"state":"idle"}')
    assert st == {"state": "idle"}


# --------------------------------------------------------------------------- #
# pid_alive: `kill -0` — a crashed recorder must NEVER leave a stale REC pill.
# --------------------------------------------------------------------------- #
def test_pid_alive_true_for_a_live_pid():
    p = subprocess.Popen(["true"])
    try:
        assert stt.pid_alive(p.pid) is True
    finally:
        p.wait()


def test_pid_alive_false_for_a_dead_pid():
    p = subprocess.Popen(["true"])
    p.wait()
    assert stt.pid_alive(p.pid) is False


def test_pid_alive_false_for_a_pid_that_cannot_exist():
    """pid 2**22 is beyond the usual pid_max; a ValueError/OSError must read as
    dead, not crash the block."""
    assert stt.pid_alive(2 ** 22) is False


# --------------------------------------------------------------------------- #
# render: the matrix from the block's docstring / nix/graphical.nix comment.
# --------------------------------------------------------------------------- #
NOW = 1758400042.0


def _rec(secs=42.0, pid=999999):
    return {"state": "recording", "pid": pid, "started_at": NOW - secs,
            "last_error": ""}


def test_render_hides_on_idle_and_on_unmeasurable():
    assert stt.render(None, NOW, None) == stt.EMPTY
    assert stt.render({"state": "idle"}, NOW, None) == stt.EMPTY
    assert stt.render({"state": "some-future-state"}, NOW, None) == stt.EMPTY


def test_render_recording_is_RED_with_elapsed_seconds():
    out = stt.render(_rec(42.0), NOW, True)
    assert out["state"] == "Critical"
    assert "REC" in out["text"]
    assert "42s" in out["text"]
    assert stt.GLYPH in out["text"]


def test_render_recording_without_started_at_is_RED_without_a_fake_count():
    """No timestamp -> no elapsed. A `0s` would be a lie the operator trusts."""
    out = stt.render({"state": "recording", "pid": 1}, NOW, True)
    assert out["state"] == "Critical"
    assert "REC" in out["text"]
    assert "s" not in out["text"].replace(stt.GLYPH, "").split("REC")[1]


def test_render_elapsed_never_going_negative(tmp_path):
    """A future started_at (clock step) clamps at 0 instead of showing -3s."""
    out = stt.render(_rec(-7.0), NOW, True)
    assert "0s" in out["text"]


def test_render_TRANScribing_is_YELLOW():
    out = stt.render({"state": "transcribing", "pid": 1}, NOW, True)
    assert out["state"] == "Warning"
    assert "stt …" in out["text"]


def test_render_ERROR_is_RED_and_survives_a_dead_pid():
    """The pid of an error row is the process that FAILED; it is always dead by
    the time anyone re-reads the file. The error is information, not a live
    process — it renders until the next start clears it."""
    out = stt.render({"state": "error", "pid": 2 ** 22,
                      "last_error": "401"}, NOW, False)
    assert out["state"] == "Critical"
    assert "stt!" in out["text"]


def test_render_maps_a_DEAD_recorder_pid_to_IDLE():
    """🔴 THE ONE THAT MATTERS: a crashed recorder must NEVER leave a stale
    red REC pill. `kill -0` failing maps recording -> idle, exactly as
    documented in nix/graphical.nix and the task for this feature."""
    out = stt.render(_rec(42.0), NOW, False)
    assert out == stt.EMPTY


def test_render_maps_a_DEAD_transcriber_pid_to_IDLE():
    out = stt.render({"state": "transcribing", "pid": 2 ** 22}, NOW, False)
    assert out == stt.EMPTY


def test_render_block_is_JSON_i3status_rust_can_parse():
    for st, alive in ((_rec(1.0), True), ({"state": "transcribing", "pid": 1}, True),
                      ({"state": "error", "pid": 1}, None), (None, None)):
        out = stt.render(st, NOW, alive)
        assert json.loads(json.dumps(out))["state"] in ("Idle", "Warning",
                                                        "Critical")
        assert "icon" not in out  # glyph lives in the text (gamemode precedent)


# --------------------------------------------------------------------------- #
# The state path honours XDG_CACHE_HOME, matching the Go tool's UserCacheDir.
# --------------------------------------------------------------------------- #
def test_state_path_follows_xdg_cache_home(monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", "/tmp/xdg-fixture")
    assert stt.state_path() == str(Path("/tmp/xdg-fixture/stt-voice/state.json"))
    monkeypatch.setenv("XDG_CACHE_HOME", "")
    monkeypatch.setenv("HOME", "/tmp/home-fixture")
    assert stt.state_path() == str(Path("/tmp/home-fixture/.cache/stt-voice/"
                                         "state.json"))


# --------------------------------------------------------------------------- #
# End-to-end through main(), the way i3status-rust actually runs the block.
# --------------------------------------------------------------------------- #
def _run(argv, env=None):
    return subprocess.run([sys.executable, str(STT_SCRIPT)] + argv,
                          capture_output=True, text=True, timeout=30,
                          env=env or dict(os.environ))


def _state_file(tmp_path, body):
    p = tmp_path / "state.json"
    p.write_text(body)
    return p


def test_e2e_render_of_a_live_recording(tmp_path):
    p = _state_file(tmp_path, json.dumps(_rec(9.0, pid=os.getpid())))
    r = _run(["--state", str(p)])
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["state"] == "Critical"
    assert "REC" in out["text"]


def test_e2e_corrupt_state_file_renders_IDLE(tmp_path):
    """🔴 POSITIVE HALF of the dead-RE guard at the file level: the block's
    contract says a broken state file hides the pill, never crashes the bar."""
    p = _state_file(tmp_path, "{ not json")
    r = _run(["--state", str(p)])
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"text": "", "state": "Idle"}


def test_e2e_missing_state_file_renders_IDLE(tmp_path):
    r = _run(["--state", str(tmp_path / "nope.json")])
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"text": "", "state": "Idle"}


def test_e2e_the_positive_control_the_render_moves(tmp_path):
    """The two tests above both assert emptiness; alone they cannot tell "the
    fallback works" from "this script prints empty no matter what"."""
    p = _state_file(tmp_path, json.dumps(_rec(9.0, pid=os.getpid())))
    r = _run(["--state", str(p)])
    assert json.loads(r.stdout)["text"] != ""


def test_e2e_toggle_with_no_stt_voice_on_PATH_is_a_silent_no_op(tmp_path):
    """A click handler has nowhere to show an error; a missing binary must be
    swallowed (the Go tool itself will toast when it DOES run). PATH is
    REPLACED with an empty dir, not prepended: `stt-voice` is installed on the
    dev host, so a prepended PATH would still resolve the live binary and
    measure nothing."""
    env = dict(os.environ)
    env["PATH"] = str(tmp_path / "does-not-exist")
    r = _run(["--toggle"], env=env)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "", r.stdout


def test_the_click_wiring_invokes_the_tool_toggle():
    """The pill's click must reach `stt-voice toggle` — the same toggle the
    task names for both trigger sources — via the script's own argv, in the
    gamemode `--toggle` shape the graphical.nix block wires."""
    src = STT_SCRIPT.read_text()
    assert '"stt-voice", "toggle"' in src, (
        "the click handler no longer invokes `stt-voice toggle` — the pill "
        "renders the tool's state but can no longer flip it")
    assert "--toggle" in src
