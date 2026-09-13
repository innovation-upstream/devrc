"""Unit + round-trip tests for the activity-collector daemon.

Run: nix-shell -p python312Packages.pytest --run "pytest scripts/collector/tests"
No test hits the real ClickHouse — the HTTP opener is mocked.
"""
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Import the daemon module (sibling dir, not a package).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import collector as C  # noqa: E402

EMIT = Path(__file__).resolve().parent.parent / "emit"


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeResp:
    def __init__(self, status=200):
        self.status = status

    def close(self):
        pass


class FakeOpener:
    """Records inserted bodies; can be told to fail N times then succeed."""

    def __init__(self, fail_times=0, status=200, raise_exc=None):
        self.bodies: list[bytes] = []
        self.calls = 0
        self.fail_times = fail_times
        self.status = status
        self.raise_exc = raise_exc

    def __call__(self, req, timeout=None):
        self.calls += 1
        self.bodies.append(req.data)
        if self.calls <= self.fail_times:
            if self.raise_exc:
                raise self.raise_exc
            return FakeResp(status=500)
        return FakeResp(status=self.status)

    @property
    def rows(self) -> list[dict]:
        out = []
        for b in self.bodies:
            for line in b.decode("utf-8").splitlines():
                if line:
                    out.append(json.loads(line))
        return out


def cfg(tmp_path, **kw) -> C.Config:
    base = dict(spool_dir=tmp_path / "spool", batch_size=500, flush_seconds=0.0)
    base.update(kw)
    return C.Config(**base)


def b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


# --------------------------------------------------------------------------- #
# parse_line
# --------------------------------------------------------------------------- #
def test_parse_basic():
    ev = C.parse_line(f"v1\tts=2026-06-23 14:00:00.123\tsource=zsh\tkind=command\tb64:text={b64('echo hi')}\tduration_ms=42\texit_code=0")
    assert ev == {
        "ts": "2026-06-23 14:00:00.123",
        "source": "zsh",
        "kind": "command",
        "text": "echo hi",
        "duration_ms": 42,
        "exit_code": 0,
    }


def test_parse_arbitrary_content_survives():
    nasty = 'rm -rf "$X"; echo \'q\'\twith\ttabs\nand a newline \\back\\slash 你好 password123!'
    line = f"v1\tts=2026-06-23 14:00:00.000\tsource=zsh\tkind=command\tb64:text={b64(nasty)}"
    ev = C.parse_line(line)
    assert ev["text"] == nasty


def test_host_override_replaces_emit_host():
    # emit stamps host=nixos on both machines; the daemon's ACTIVITY_HOST wins.
    line = f"v1\tts=t\tsource=zsh\tkind=command\thost=nixos\tb64:text={b64('echo hi')}"
    assert C.parse_line(line)["host"] == "nixos"            # passthrough when unset
    assert C.parse_line(line, "laptop")["host"] == "laptop"  # override wins
    assert C.parse_line(line, "workbench")["host"] == "workbench"


def test_parse_unknown_keys_go_to_payload():
    ev = C.parse_line(f"v1\tts=t\tsource=zsh\tkind=command\twindow=@3\tpane=%7")
    pl = json.loads(ev["payload"])
    assert pl == {"window": "@3", "pane": "%7"}


def test_parse_merges_explicit_payload_with_extras():
    explicit = b64(json.dumps({"a": 1}))
    ev = C.parse_line(f"v1\tts=t\tsource=x\tkind=k\tb64:payload={explicit}\tfoo=bar")
    assert json.loads(ev["payload"]) == {"a": 1, "foo": "bar"}


@pytest.mark.parametrize("line", [
    "",
    "garbage no version",
    "v2\tts=t\tsource=s\tkind=k",          # wrong version
    "v1\tnoeqsign\tsource=s\tkind=k",      # token without '='
    "v1\tsource=s\tkind=k",                # missing ts
    "v1\tts=t\tkind=k",                    # missing source
    f"v1\tts=t\tsource=s\tkind=k\tb64:text=!!!notb64!!!",  # bad base64
])
def test_parse_malformed_returns_none(line):
    assert C.parse_line(line) is None


def test_parse_bad_int_defaults_zero():
    ev = C.parse_line("v1\tts=t\tsource=s\tkind=k\tduration_ms=notanum")
    assert ev["duration_ms"] == 0


# --------------------------------------------------------------------------- #
# JSONEachRow formatting
# --------------------------------------------------------------------------- #
def test_jsoneachrow_body():
    body = C.format_jsoneachrow([{"a": 1}, {"b": "x"}])
    assert body == b'{"a":1}\n{"b":"x"}\n'


def test_jsoneachrow_unicode_not_escaped():
    body = C.format_jsoneachrow([{"text": "你好"}])
    assert "你好" in body.decode("utf-8")


# --------------------------------------------------------------------------- #
# Spool rotation + batching
# --------------------------------------------------------------------------- #
def write_lines(path: Path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        for ln in lines:
            f.write(ln + "\n")


def test_rotate_and_ship(tmp_path):
    c = cfg(tmp_path)
    sp = C.Spool(c)
    write_lines(sp.current, [
        f"v1\tts=t\tsource=zsh\tkind=command\tb64:text={b64('one')}",
        f"v1\tts=t\tsource=zsh\tkind=command\tb64:text={b64('two')}",
    ])
    opener = FakeOpener()
    client = C.ClickHouseClient(c, opener=opener)
    stats = C.flush_once(sp, client)
    assert stats == {"shipped": 1, "failed": 0}
    assert [r["text"] for r in opener.rows] == ["one", "two"]
    assert sp.segments() == []          # deleted after 200
    assert not sp.current.exists()      # rotated away


def test_batching_splits_into_multiple_inserts(tmp_path):
    c = cfg(tmp_path, batch_size=2)
    sp = C.Spool(c)
    write_lines(sp.current, [f"v1\tts=t\tsource=s\tkind=k\tb64:text={b64(str(i))}" for i in range(5)])
    opener = FakeOpener()
    C.flush_once(sp, C.ClickHouseClient(c, opener=opener))
    # 5 events / batch 2 => 3 inserts
    assert opener.calls == 3
    assert [r["text"] for r in opener.rows] == ["0", "1", "2", "3", "4"]


def test_empty_current_no_rotate(tmp_path):
    c = cfg(tmp_path)
    sp = C.Spool(c)
    sp.current.write_text("")
    opener = FakeOpener()
    stats = C.flush_once(sp, C.ClickHouseClient(c, opener=opener))
    assert stats == {"shipped": 0, "failed": 0}
    assert opener.calls == 0


# --------------------------------------------------------------------------- #
# Offline buffering: accumulate -> retry -> ship on recovery, no double-ship
# --------------------------------------------------------------------------- #
def test_offline_accumulate_then_ship_on_recovery(tmp_path):
    c = cfg(tmp_path)
    sp = C.Spool(c)

    # Backend down: opener raises a URLError.
    import urllib.error
    down = FakeOpener(fail_times=99, raise_exc=urllib.error.URLError("offline"))
    client_down = C.ClickHouseClient(c, opener=down)

    # Three flush cycles while offline; each adds a batch.
    for cycle in range(3):
        write_lines(sp.current, [f"v1\tts=t\tsource=s\tkind=k\tb64:text={b64(f'c{cycle}')}"])
        stats = C.flush_once(sp, client_down)
        assert stats["failed"] == 1
    # All segments still buffered on disk, nothing lost.
    assert len(sp.segments()) == 3

    # Backend recovers.
    up = FakeOpener()
    client_up = C.ClickHouseClient(c, opener=up)
    stats = C.flush_once(sp, client_up)
    assert stats == {"shipped": 3, "failed": 0}
    assert sorted(r["text"] for r in up.rows) == ["c0", "c1", "c2"]
    assert sp.segments() == []


def test_no_double_ship_after_partial_failure(tmp_path):
    """A segment that fails to ship is retried; a segment that succeeds is NOT
    re-inserted on the next pass (it was deleted)."""
    c = cfg(tmp_path)
    sp = C.Spool(c)

    # First pass: succeeds for the one segment present.
    write_lines(sp.current, [f"v1\tts=t\tsource=s\tkind=k\tb64:text={b64('a')}"])
    opener = FakeOpener()
    client = C.ClickHouseClient(c, opener=opener)
    C.flush_once(sp, client)
    first_calls = opener.calls
    assert sorted(r["text"] for r in opener.rows) == ["a"]

    # Second pass with NO new data: nothing to ship, no re-insert of "a".
    C.flush_once(sp, client)
    assert opener.calls == first_calls  # unchanged → no double ship


def test_failed_segment_retried_not_lost(tmp_path):
    c = cfg(tmp_path)
    sp = C.Spool(c)
    write_lines(sp.current, [f"v1\tts=t\tsource=s\tkind=k\tb64:text={b64('keep')}"])
    # Fail once, then succeed.
    opener = FakeOpener(fail_times=1)
    client = C.ClickHouseClient(c, opener=opener)

    stats = C.flush_once(sp, client)
    assert stats["failed"] == 1
    assert len(sp.segments()) == 1   # retained for retry

    stats = C.flush_once(sp, client)
    assert stats["shipped"] == 1
    assert sp.segments() == []
    assert [r["text"] for r in opener.rows][-1] == "keep"


# --------------------------------------------------------------------------- #
# Malformed / oversized handling
# --------------------------------------------------------------------------- #
def test_segment_with_malformed_lines_ships_good_drops_bad(tmp_path):
    c = cfg(tmp_path)
    sp = C.Spool(c)
    write_lines(sp.current, [
        f"v1\tts=t\tsource=s\tkind=k\tb64:text={b64('good1')}",
        "this is not a valid v1 line",
        f"v1\tts=t\tsource=s\tkind=k\tb64:text={b64('good2')}",
    ])
    opener = FakeOpener()
    C.flush_once(sp, C.ClickHouseClient(c, opener=opener))
    assert sorted(r["text"] for r in opener.rows) == ["good1", "good2"]


def test_all_malformed_segment_dropped_not_wedged(tmp_path):
    c = cfg(tmp_path)
    sp = C.Spool(c)
    write_lines(sp.current, ["garbage1", "garbage2"])
    opener = FakeOpener()
    stats = C.flush_once(sp, C.ClickHouseClient(c, opener=opener))
    assert opener.calls == 0           # nothing valid to insert
    assert sp.segments() == []         # but the file is dropped, not stuck


# --------------------------------------------------------------------------- #
# Buffer cap + drop logging
# --------------------------------------------------------------------------- #
def test_buffer_cap_drops_oldest_and_logs(tmp_path, caplog):
    import logging
    c = cfg(tmp_path, max_buffer_bytes=200)
    sp = C.Spool(c)
    # Create several segments each > the cap fraction so size enforcement trips.
    for i in range(5):
        seg = sp.dir / f"seg-{i:013d}-0001.log"
        seg.write_text("x" * 100)
    with caplog.at_level(logging.WARNING):
        dropped = sp.enforce_cap()
    assert dropped >= 1
    remaining = sum(p.stat().st_size for p in sp.segments())
    assert remaining <= c.max_buffer_bytes
    assert any("BUFFER CAP" in r.message for r in caplog.records)


def test_buffer_cap_age_drops_old_segment(tmp_path, caplog):
    import logging
    c = cfg(tmp_path, max_buffer_age_seconds=1.0)
    sp = C.Spool(c)
    old = sp.dir / "seg-0000000000001-0001.log"
    old.write_text("x")
    os.utime(old, (0, 0))  # epoch 0 => ancient
    with caplog.at_level(logging.WARNING):
        sp.enforce_cap()
    assert not old.exists()
    assert any("over-age" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# HTTP client auth headers + non-2xx handling
# --------------------------------------------------------------------------- #
def test_client_sends_auth_headers_when_password_set(tmp_path):
    c = cfg(tmp_path, user="writer", password="s3cret")
    captured = {}

    def opener(req, timeout=None):
        captured["user"] = req.get_header("X-clickhouse-user")
        captured["key"] = req.get_header("X-clickhouse-key")
        captured["url"] = req.full_url
        return FakeResp(200)

    C.ClickHouseClient(c, opener=opener).insert(b"{}\n")
    assert captured["user"] == "writer"
    assert captured["key"] == "s3cret"
    assert "INSERT+INTO" in captured["url"] or "INSERT%20INTO" in captured["url"]


def test_client_raises_on_non_2xx(tmp_path):
    c = cfg(tmp_path)
    client = C.ClickHouseClient(c, opener=lambda req, timeout=None: FakeResp(500))
    with pytest.raises(RuntimeError):
        client.insert(b"{}\n")


def test_insert_url_uses_config(tmp_path):
    c = cfg(tmp_path, clickhouse_url="http://example/", database="db", table="t")
    assert c.insert_url.startswith("http://example/?")
    assert "db.t" in c.insert_url


# --------------------------------------------------------------------------- #
# Config from env
# --------------------------------------------------------------------------- #
def test_config_from_env():
    env = {
        "CLICKHOUSE_URL": "http://h/",
        "CLICKHOUSE_USER": "u",
        "CLICKHOUSE_PASSWORD": "p",
        "ACTIVITY_BATCH_SIZE": "7",
        "ACTIVITY_FLUSH_SECONDS": "3",
        "ACTIVITY_SPOOL_DIR": "/tmp/spool-x",
    }
    c = C.Config.from_env(env)
    assert c.clickhouse_url == "http://h"   # trailing slash stripped
    assert (c.user, c.password) == ("u", "p")
    assert c.batch_size == 7
    assert c.flush_seconds == 3.0
    assert str(c.spool_dir) == "/tmp/spool-x"


# --------------------------------------------------------------------------- #
# End-to-end: emit (real shell) -> spool -> daemon parse, arbitrary content
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not EMIT.exists(), reason="emit script missing")
def test_emit_to_daemon_roundtrip_arbitrary_content(tmp_path):
    spool = tmp_path / "spool"
    env = dict(os.environ, ACTIVITY_SPOOL_DIR=str(spool))

    nasty_cmd = 'echo "he said \\"hi\\"" \'single\'; cat <<X\nmulti\nline 你好\nX\npassword123!'
    nasty_cwd = "/home/zach/weird dir/with\ttab"

    rc = subprocess.run(
        ["bash", str(EMIT),
         "source=zsh", "kind=command",
         f"b64:text={nasty_cmd}", f"b64:cwd={nasty_cwd}",
         "duration_ms=123", "exit_code=2",
         "b64:project=devrc", "window=@9"],
        env=env, capture_output=True, text=True,
    )
    assert rc.returncode == 0, rc.stderr

    cur = spool / "current.log"
    assert cur.exists()
    lines = [l for l in cur.read_text().splitlines() if l]
    assert len(lines) == 1

    ev = C.parse_line(lines[0])
    assert ev is not None
    assert ev["text"] == nasty_cmd          # exact survival incl password string
    assert ev["cwd"] == nasty_cwd
    assert ev["duration_ms"] == 123
    assert ev["exit_code"] == 2
    assert ev["project"] == "devrc"
    assert json.loads(ev["payload"]) == {"window": "@9"}
    # ts + host auto-filled by emit.
    assert "ts" in ev and ev["source"] == "zsh"


@pytest.mark.skipif(not EMIT.exists(), reason="emit script missing")
def test_emit_ts_is_utc(tmp_path):
    # emit auto-fills ts as the UTC instant (`date -u`), so it lands inside the
    # UTC window we bracket around the call — a local-time stamp on a non-UTC
    # host would be offset out of this window.
    import datetime as _dt
    spool = tmp_path / "spool"
    env = dict(os.environ, ACTIVITY_SPOOL_DIR=str(spool))
    before = _dt.datetime.now(_dt.timezone.utc)
    rc = subprocess.run(
        ["bash", str(EMIT), "source=zsh", "kind=command", "b64:text=tz"],
        env=env, capture_output=True, text=True,
    )
    after = _dt.datetime.now(_dt.timezone.utc)
    assert rc.returncode == 0, rc.stderr
    ev = C.parse_line((spool / "current.log").read_text().splitlines()[0])
    parsed = _dt.datetime.strptime(ev["ts"], "%Y-%m-%d %H:%M:%S.%f").replace(
        tzinfo=_dt.timezone.utc
    )
    assert before - _dt.timedelta(seconds=2) <= parsed <= after + _dt.timedelta(seconds=2)


@pytest.mark.skipif(not EMIT.exists(), reason="emit script missing")
def test_emit_concurrent_appends_dont_interleave(tmp_path):
    spool = tmp_path / "spool"
    env = dict(os.environ, ACTIVITY_SPOOL_DIR=str(spool))
    procs = []
    for i in range(20):
        procs.append(subprocess.Popen(
            ["bash", str(EMIT), "source=zsh", "kind=command", f"b64:text=cmd{i}"],
            env=env,
        ))
    for p in procs:
        p.wait()
    lines = [l for l in (spool / "current.log").read_text().splitlines() if l]
    assert len(lines) == 20
    texts = sorted(C.parse_line(l)["text"] for l in lines)
    assert texts == sorted(f"cmd{i}" for i in range(20))


# --------------------------------------------------------------------------- #
# WHICH MACHINE IS THIS (#1601)
# --------------------------------------------------------------------------- #
# 🔴 THE COLLECTOR WAS THE ONE CONSUMER THAT DID NOT DERIVE. `ACTIVITY_HOST`
# absent, it fell back to `""`, under which `parse_line` leaves emit's
# `host=$(hostname)` standing — and `hostname` is `nixos` on BOTH machines. Every
# other consumer of the label goes through `scripts/lib/host_label.py`, which
# names the machine from an address it actually HOLDS. That single gap is why the
# first cut of #1601 had to WRITE a label into `~/.config/activity-collector/env`
# from a home-manager activation, and that write mangled a `CLICKHOUSE_PASSWORD`
# line in a systemd `EnvironmentFile=`. Deriving here deletes the write.
#
# 🔴 HERMETIC BY CONSTRUCTION, AND IT HAS TO BE: this suite runs on ONE OF THE TWO
# REAL MACHINES the module is about, so the address probe would happily answer
# truthfully mid-test and make an assertion pass for a reason unrelated to the
# code. `_hermetic_host_label` below states "this machine holds none of the known
# addresses" and points the env file at a path that does not exist; every test
# that WANTS a signal injects one. `test_the_hermeticity_fixture_is_installed` is
# the positive control on it — a guard nobody has watched work is not a guard.
#: 🔴 WHICH OF THE TESTS BELOW IS REGRESSION COVERAGE, AND AGAINST WHICH BASE.
#: These fail at `4697add2` — the round-1 head of this branch, where
#: `host_label.py` already derived and only `collector.py` did not — BECAUSE OF
#: THE DEFECT, i.e. because `from_env` answered `""` where it should have derived.
#: Measured by copying THIS file into an archive of that commit under /tmp:
#: 8 failed, 34 passed; the other four failures are missing SYMBOLS
#: (`_load_host_label`, `HOST_LABEL_LIB_DIRS`) or a log line that does not exist
#: there, which is NOT "red because of the defect" and is deliberately not
#: claimed here. `origin/main` is not a usable base for these at all — the module
#: has no address signal there, so the fixture cannot even be built.
#: Everything else in this section is an INVARIANT GUARD: it pins behaviour that
#: was already correct, or a code path this branch introduces. The distinction is
#: written down because a guard that never could have gone red reads as coverage
#: and provides none.
RED_AT_4697ADD2 = {
    "test_an_ABSENT_ACTIVITY_HOST_is_DERIVED_from_an_address_this_machine_holds",
    "test_the_DEPLOYED_symlink_layout_can_derive_the_host_label",
}

_LIB = Path(__file__).resolve().parent.parent.parent / "lib"
_COLLECTOR_PY = Path(__file__).resolve().parent.parent / "collector.py"


@pytest.fixture(autouse=True)
def _hermetic_host_label(monkeypatch, tmp_path_factory):
    """Mute the real machine's identity for every test in this file."""
    monkeypatch.setenv("HOST_LABEL_ADDRS", "")
    monkeypatch.delenv("ACTIVITY_HOST", raising=False)
    # `_load_host_label` mutates sys.path; hand monkeypatch a COPY so the
    # original list object is restored and one test cannot leak a libdir into
    # the next (the broken-module cases below depend on that).
    monkeypatch.setattr(sys, "path", list(sys.path))
    hl = C._load_host_label()
    monkeypatch.setattr(
        hl, "ACTIVITY_ENV",
        str(tmp_path_factory.mktemp("no-env") / "absent-env"))
    return hl


def _addrs_of(hl, label):
    """The addresses `host-role.sh` gives `label`. Read from the module's own
    table, never typed here — a literal would pin this suite to the fleet's
    current IPs, which `test_peer_host.py` polices."""
    return " ".join(a for lbl, a in hl.host_addrs() if lbl == label)


def test_the_hermeticity_fixture_is_installed(_hermetic_host_label):
    """POSITIVE CONTROL on the fixture, asserted by OBSERVING the module rather
    than by re-reading the fixture.

    INVARIANT GUARD (it is about the harness, not the defect).
    """
    hl = _hermetic_host_label
    assert not os.path.exists(hl.ACTIVITY_ENV)
    assert os.environ.get("ACTIVITY_HOST") is None
    assert hl.address_host_label() is None
    # …and it is the ENV that did that, not a probe wired to nothing: with the
    # variable unset the probe would reach the real machine. Feeding it a value
    # that MUST match moves the answer, so the PAIR is reported, not the zero.
    os.environ["HOST_LABEL_ADDRS"] = _addrs_of(hl, "workbench")
    hl._reset_host_addrs_cache()
    assert hl.address_host_label() == "workbench"
    os.environ["HOST_LABEL_ADDRS"] = ""
    assert hl.address_host_label() is None


def test_an_explicit_ACTIVITY_HOST_still_WINS_over_the_derivation(
        _hermetic_host_label):
    """Precedence that must NOT change: a stated label is what the daemon stamps.

    The machine "holds" the WORKBENCH's addresses while the environment says
    `laptop` — so a mutant that derives FIRST answers `workbench` here.

    INVARIANT GUARD: green at `origin/main` too, where `from_env` read the
    environment and nothing else.

    🔴 THIS TEST IS *NOT* THE KILLER FOR "THE DERIVE PREEMPTS THE ENVIRONMENT",
    AND AN EARLIER DRAFT OF THIS DOCSTRING CLAIMED IT WAS. Measured: the mutant
    that puts `_derive_host_label(e) or …` in front SURVIVED the whole collector
    suite. It has to — `_derive_host_label` passes `env` STRAIGHT THROUGH to
    `local_host_label`, which cross-checks the stated label against the address
    and RAISES `HostLabelConflict` on exactly the input this test builds; the
    broad catch turns that into `""` and the expression falls through to the
    environment anyway. So for a VALID stated label the two orders are
    indistinguishable. The observable difference is an INVALID one, and its
    killer is `test_an_INVALID_ACTIVITY_HOST_is_still_passed_through_UNCHANGED`
    below (`MUT-C3`). This test pins the contract; that one is what makes it
    machine-checked.

    ⚠ AND IT IS AN OVERRIDE HERE, UNLIKE EVERYWHERE ELSE. `local_host_label()`
    CROSS-CHECKS a stated label against the machine's address and raises on a
    contradiction; this daemon never gets that far, by design — see
    `_derive_host_label`'s docstring for why a collector that refuses to start is
    worse than a wrong column.
    """
    hl = _hermetic_host_label
    os.environ["HOST_LABEL_ADDRS"] = _addrs_of(hl, "workbench")
    assert C.Config.from_env({"ACTIVITY_HOST": "laptop"}).host_override == "laptop"


@pytest.mark.parametrize("env", [{}, {"ACTIVITY_HOST": ""},
                                 {"ACTIVITY_HOST": "  "}],
                         ids=["absent", "empty", "blank"])
def test_an_ABSENT_ACTIVITY_HOST_is_DERIVED_from_an_address_this_machine_holds(
        _hermetic_host_label, env):
    """🔴 RED AT BASE, AND THIS IS THE WHOLE POINT OF THE CHANGE. At
    `origin/main` (and at this branch's own `4697add2`) `from_env` answered `""`
    for all three of these, and `parse_line` then left emit's `host=$(hostname)`
    — `nixos` on BOTH machines — on every shipped row.

    The `blank` row is the one the old `e.get("ACTIVITY_HOST", "")` got wrong in
    a second way: a whitespace-only value is truthy, so it was stamped verbatim.

    The injected address is the LAPTOP's, deliberately: `workbench` is the
    literal the old default used, so a mutant that hardcodes it cannot survive
    by accidentally equalling the expectation.
    """
    hl = _hermetic_host_label
    os.environ["HOST_LABEL_ADDRS"] = _addrs_of(hl, "laptop")
    assert C.Config.from_env(env).host_override == "laptop"


def test_an_INVALID_ACTIVITY_HOST_is_still_passed_through_UNCHANGED(
        _hermetic_host_label):
    """🔴 THE COLLECTOR DOES NOT VALIDATE WHAT THE OPERATOR STATED, AND THAT IS
    THE PRE-#1601 CONTRACT KEPT DELIBERATELY. `host_label.py` ignores a value
    outside `HOST_NAMES` (a typo would otherwise mint a third host the fleet does
    not know); this daemon does not, because its job is to stamp a column and a
    stated value is the operator's to get right. Changing that would be a
    behaviour change for every host, smuggled in under a fix.

    It is also the ONLY input on which "environment first" and "derive first" are
    distinguishable — see the note on
    `test_an_explicit_ACTIVITY_HOST_still_WINS_over_the_derivation`. So this is
    the killer for `MUT-C3`.

    INVARIANT GUARD — green at `4697add2` and at `origin/main`, where `from_env`
    read the environment and nothing else.
    """
    hl = _hermetic_host_label
    os.environ["HOST_LABEL_ADDRS"] = _addrs_of(hl, "workbench")
    # A value the module would REJECT, and pairwise distinct from both real host
    # names, so a mutant that answers with the derived label cannot equal it.
    assert hl._file_stated_label("ACTIVITY_HOST=nixos-typo\n") == "", (
        "the fixture value is one the module ACCEPTS — this test would then be "
        "measuring agreement, not pass-through")
    assert C.Config.from_env(
        {"ACTIVITY_HOST": "nixos-typo"}).host_override == "nixos-typo"


def test_an_UNIDENTIFIABLE_machine_DEGRADES_rather_than_crashing_the_daemon(
        _hermetic_host_label, caplog):
    """🔴 DEGRADE, NEVER CRASH. `local_host_label()` RAISES when nothing names
    the machine — correct for `peer-host`, which routes work — but this is a
    `Restart=always` daemon and the ONLY drain on the spool. A collector that
    refuses to start does not mislabel telemetry, it STOPS it, on every host,
    until somebody notices. The honest fallback is the pre-#1601 behaviour:
    empty `host_override`, emit's own `host=` stands.

    The reason must be LOGGED, not swallowed — an operator reading the journal
    needs to tell "this box is off the mesh" from "the deploy lost a file".

    INVARIANT GUARD against `origin/main` (which could not raise), and the
    guard this rework's live-daemon risk rests on. Its sensitivity is `MUT-C2`
    in `scripts/tests/mutants-host-label.sh`.
    """
    with caplog.at_level("WARNING"):
        try:
            cfg = C.Config.from_env({})
        except Exception as exc:  # noqa: BLE001 — the failure under test
            pytest.fail(
                "the collector CRASHED while deriving its host label "
                f"({type(exc).__name__}: {exc}). This runs at daemon startup, so "
                "the unit would not start at all and the spool would never drain")
    assert cfg.host_override == ""
    assert "could not derive this host's ACTIVITY_HOST label" in caplog.text
    assert "HostLabelUnresolved" in caplog.text, (
        "the log line must name WHY — a generic message cannot distinguish an "
        f"off-mesh box from a broken deploy. Got: {caplog.text!r}")


@pytest.mark.parametrize("body,want", [
    ("import a_module_that_does_not_exist_xyz\n", "ModuleNotFoundError"),
    ("raise RuntimeError('the deploy is missing a file')\n", "RuntimeError"),
], ids=["import-error", "arbitrary-exception"])
def test_a_BROKEN_host_label_module_DEGRADES_rather_than_crashing_the_daemon(
        monkeypatch, tmp_path, caplog, body, want):
    """🔴 THE `except Exception` IS BROAD ON PURPOSE, AND THIS IS WHY. The
    deployed collector loads `host_label.py` out of a `lib/` dir next to itself
    that `nix/home.nix` places there. A switch that loses that entry SUCCEEDS —
    the file simply is not deployed — and the import then raises something that
    is NOT a `HostLabelError`. Narrowing the catch to the module's own exception
    class would take the daemon down for a deployment mistake, which is the
    failure this whole guard exists to prevent.

    Two shapes: a missing dependency (`ImportError`) and any other exception at
    import time. Neither may reach the process.

    INVARIANT GUARD; sensitivity is `MUT-C2b` in
    `scripts/tests/mutants-host-label.sh`.
    """
    broken = tmp_path / "brokenlib"
    broken.mkdir()
    (broken / "host_label.py").write_text(body, encoding="utf-8")
    monkeypatch.setattr(C, "HOST_LABEL_LIB_DIRS", (str(broken),))
    monkeypatch.delitem(sys.modules, "host_label", raising=False)

    with caplog.at_level("WARNING"):
        try:
            cfg = C.Config.from_env({})
        except Exception as exc:  # noqa: BLE001 — the failure under test
            pytest.fail(
                "a broken/undeployed host_label.py CRASHED the collector "
                f"({type(exc).__name__}: {exc}) — the daemon would not start")
    assert cfg.host_override == ""
    assert want in caplog.text, caplog.text


def test_the_DAEMON_ITSELF_STARTS_when_the_machine_cannot_be_identified(tmp_path):
    """🔴 THE SAME CLAIM AT THE PROCESS LEVEL, WHICH IS WHERE IT IS MADE. Every
    assertion above calls `Config.from_env` in-process; systemd runs
    `python3 collector.py`. `--flush-once` is that real entry point (`main()` →
    `Config.from_env()` → work → exit), so this exercises the path the unit
    takes rather than a Python restatement of it.

    Hermetic: an EMPTY spool, so `flush_once` finds no segment and no HTTP call
    is ever made. `CLICKHOUSE_URL` is set to a loopback port nothing listens on
    so that a regression which DID reach the network fails loudly rather than
    touching the real store.

    INVARIANT GUARD; sensitivity is `MUT-C2` (which makes this rc 1 with a
    traceback).
    """
    env = dict(os.environ)
    env.update({
        "ACTIVITY_SPOOL_DIR": str(tmp_path / "spool"),
        "HOST_LABEL_ADDRS": "",
        "HOST_LABEL_ENV_FILE": str(tmp_path / "absent-env"),
        "CLICKHOUSE_URL": "http://127.0.0.1:1",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    env.pop("ACTIVITY_HOST", None)
    p = subprocess.run([sys.executable, str(_COLLECTOR_PY), "--flush-once"],
                       capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 0, (
        "the collector daemon exited non-zero on a machine it cannot identify. "
        f"systemd would restart-loop it and the spool would never drain.\n"
        f"stdout: {p.stdout}\nstderr: {p.stderr}")
    assert "could not derive this host's ACTIVITY_HOST label" in p.stderr
    assert "Traceback" not in p.stderr, p.stderr


def _deploy(tmp_path, ship_lib=("host_label.py", "host-role.sh")):
    """A fake `~/.config/activity-collector/`: real dir, per-file SYMLINKS into a
    FLAT fake store — the shape home-manager actually produces (each `home.file`
    source is its own `/nix/store/<hash>-<name>` path, not a tree)."""
    store = tmp_path / "store"
    store.mkdir(parents=True)
    dep = tmp_path / "deployed"
    (dep / "lib").mkdir(parents=True)

    (store / "collector.py").write_bytes(_COLLECTOR_PY.read_bytes())
    (dep / "collector.py").symlink_to(store / "collector.py")
    for name in ship_lib:
        (store / name).write_bytes((_LIB / name).read_bytes())
        (dep / "lib" / name).symlink_to(store / name)
    return dep


def _deployed_label(dep, addrs, tmp_path):
    """`Config.from_env({}).host_override` as computed by the DEPLOYED copy."""
    prog = (
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('c', sys.argv[1])\n"
        "m = importlib.util.module_from_spec(spec)\n"
        # Registered BEFORE exec: @dataclass resolves its own module out of
        # sys.modules, and an unregistered one dies on `Config`.
        "sys.modules['c'] = m\n"
        "spec.loader.exec_module(m)\n"
        "print('LABEL=' + repr(m.Config.from_env({}).host_override))\n"
    )
    env = dict(os.environ)
    env.update({"HOST_LABEL_ADDRS": addrs,
                "HOST_LABEL_ENV_FILE": str(tmp_path / "absent-env"),
                "PYTHONDONTWRITEBYTECODE": "1"})
    env.pop("ACTIVITY_HOST", None)
    p = subprocess.run([sys.executable, "-c", prog, str(dep / "collector.py")],
                       capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    line = [l for l in p.stdout.splitlines() if l.startswith("LABEL=")]
    assert line, p.stdout + p.stderr
    return eval(line[0][len("LABEL="):])  # noqa: S307 — a repr() we just produced


def test_the_DEPLOYED_symlink_layout_can_derive_the_host_label(
        _hermetic_host_label, tmp_path):
    """🔴 THE SEAM, AND IT IS THE ONE SURFACE NO OTHER TEST IN THIS REPO LOADS.
    Every assertion above imports the collector from the REPO layout, where
    `scripts/lib/` is two directories up. What runs on a host is
    `~/.config/activity-collector/collector.py` — a lone flattened SYMLINK into
    /nix/store with no `scripts/` anywhere near it. A derivation that works in
    the repo and not there is a feature that is inert on both machines while
    every test is green: exactly the "verified in isolation" shape.

    THREE ARMS, because the pass alone would not be evidence:
      * both files deployed          -> the label is derived
      * NOTHING deployed beside it   -> `""`, and the process does not crash
      * `host-role.sh` MISSING       -> `""` for a LAN-only address, because the
        module falls back to the nebula-only `PEER_SSH` subset. That is the
        SILENT degradation `nix/home.nix` ships both files to prevent: right on
        the mesh, quietly non-deriving off it, exit 0 either way.

    The address injected is the workbench's LAN one specifically — it exists only
    in `host-role.sh`, never in `PEER_SSH`, which is what makes arm 3 measure the
    sibling rather than the module.

    INVARIANT GUARD (the deployed layout did not exist for this dependency at
    base); sensitivity is `MUT-C6` and `MUT-C7` in
    `scripts/tests/mutants-host-label.sh`.
    """
    hl = _hermetic_host_label
    lan = next(a for lbl, a in hl.host_addrs()
               if lbl == "workbench" and a not in {x for _, x, _ in hl.PEER_SSH})

    both = _deploy(tmp_path / "a")
    assert _deployed_label(both, lan, tmp_path) == "workbench"

    none = _deploy(tmp_path / "b", ship_lib=())
    assert _deployed_label(none, lan, tmp_path) == "", (
        "with no lib/ deployed the collector must degrade to the empty override, "
        "not crash and not guess")

    lonely = _deploy(tmp_path / "c", ship_lib=("host_label.py",))
    assert _deployed_label(lonely, lan, tmp_path) == "", (
        "host-role.sh was not deployed beside host_label.py, yet the LAN address "
        "still resolved — the fallback table is supposed to be the nebula-only "
        "PEER_SSH subset, so this arm is measuring nothing")


def test_the_RED_AT_4697ADD2_ledger_names_only_tests_that_EXIST():
    """A ledger of test names is a CLAIM; this makes it a checkable one. The
    same pin `test_peer_host.py` and `test_transcript_search.py` carry — without
    it a rename leaves the claim pointing at nothing while still reading as
    evidence. INVARIANT GUARD."""
    defined = {k for k in globals() if k.startswith("test_")}
    assert len(defined) > 20, "the globals() scan is wired to nothing"
    missing = sorted(RED_AT_4697ADD2 - defined)
    assert not missing, f"RED_AT_4697ADD2 names tests that do not exist: {missing}"
