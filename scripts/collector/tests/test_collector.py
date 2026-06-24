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
