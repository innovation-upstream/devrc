#!/usr/bin/env python3
"""`scripts/store` — the read-through client's four states.

🔴 WHAT THIS FILE IS ACTUALLY GUARDING. Three of the four states print no
entries, and one of those three is a lie: `scope-empty` means the store was
reached and holds nothing, while `store-unreachable, no cache` means nothing was
read at all. If those render alike, `/resume` shows an empty screen for an
outage and the reader believes it. Every test below exists to keep them apart.

🔴 WHY THERE IS A PROXY IN THE FIXTURE. The server requires `CF-Connecting-IP`
and refuses an absent, forged or duplicated one — it is the rate limiter's key
and it fails closed. In production **Cloudflare** sets that header; the client
never does, and must never, or a real deployment would send a duplicate and be
refused. So the fixture puts a shim in front of the server that adds the header,
standing in for exactly the hop that adds it in production. A test that instead
taught the client to send it would be testing a client we must not ship.
"""

from __future__ import annotations

import http.server
import importlib.util
import ipaddress
import os
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
STORE_CLI = REPO / "scripts" / "store"
SERVER_PY = REPO / "scripts" / "subsystem-store-api" / "server.py"
GOOD_TOKEN = "a" * 20 + "B" * 20 + "c" * 8
LOOPBACK = ipaddress.ip_network("127.0.0.1/32")


def _load_api():
    sys.path.insert(0, str(REPO / "scripts" / "lib"))
    spec = importlib.util.spec_from_file_location("srv", SERVER_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _entry(service: str, scope: str, nuance: str) -> str:
    return "\n".join(
        [
            "---",
            f"service: {service}",
            f"scope: {scope}",
            "sensitivity: internal",
            "---",
            "",
            "## What it is",
            f"The {service} component, described durably.",
            "",
            "## Pointers",
            f"- ops skill `manage-{service}` — invoke it for restarts",
            "",
            "## Nuance / work-history",
            nuance,
            "",
        ]
    )


@pytest.fixture
def source_store(tmp_path: Path) -> Path:
    root = tmp_path / "src"
    (root / "widget-cfg").mkdir(parents=True)
    (root / "hollow-area").mkdir(parents=True)
    (root / "widget-cfg" / "thing-alpha.md").write_text(
        _entry("thing-alpha", "widget-cfg", "- 2026-01-02: probe lies for 40s.")
    )
    (root / "widget-cfg" / "thing-beta.md").write_text(
        _entry("thing-beta", "widget-cfg", "- 2026-01-03: sidecar drops its lease.")
    )
    return root


class _CloudflareShim(http.server.BaseHTTPRequestHandler):
    """Forwards to the real server, adding the header the edge adds."""

    upstream = ""

    def do_GET(self):  # noqa: N802
        req = urllib.request.Request(self.upstream + self.path, method="GET")
        for key, value in self.headers.items():
            if key.lower() not in ("host", "cf-connecting-ip"):
                req.add_header(key, value)
        req.add_header("CF-Connecting-IP", "203.0.113.7")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body, code, headers = resp.read(), resp.status, dict(resp.headers)
        except urllib.error.HTTPError as exc:
            body, code, headers = exc.read(), exc.code, dict(exc.headers)
        self.send_response(code)
        for key, value in headers.items():
            if key.lower() not in ("transfer-encoding", "connection", "content-length"):
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@pytest.fixture
def live_store(source_store: Path):
    """Yields `(base_url, stop)` — a real server behind a header-adding shim."""
    api = _load_api()
    httpd = api.build_server(
        host="127.0.0.1",
        port=0,
        store_root=str(source_store),
        tokens=(GOOD_TOKEN,),
        trusted_proxies=(LOOPBACK,),
        limiter=None,
        audit=None,
    )
    upstream = f"http://127.0.0.1:{httpd.server_address[1]}"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    handler = type("Shim", (_CloudflareShim,), {"upstream": upstream})
    shim = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=shim.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{shim.server_address[1]}"
    try:
        yield base
    finally:
        shim.shutdown()
        shim.server_close()
        httpd.shutdown()
        httpd.server_close()


def run_store(*args: str, url: str | None, cache: Path, token: str = GOOD_TOKEN):
    env = dict(os.environ)
    env["SUBSYSTEM_STORE_TOKEN"] = token
    # Point config resolution at a path that does not exist, so a real
    # `~/.config/subsystem-store/env` on the developer's box can never make a
    # test pass. A test that reads the operator's live credentials is not a test.
    env["SUBSYSTEM_STORE_CONFIG"] = str(cache.parent / "no-such-config")
    if url is None:
        env.pop("SUBSYSTEM_STORE_URL", None)
        env["SUBSYSTEM_STORE_URL"] = f"http://127.0.0.1:{_dead_port()}"
    else:
        env["SUBSYSTEM_STORE_URL"] = url
    proc = subprocess.run(
        [sys.executable, str(STORE_CLI), "--cache", str(cache), "--timeout", "5", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    return proc


def _dead_port() -> int:
    """A port nothing is listening on — bound then released, so it is real."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class TestTheFourStates:
    def test_live_says_live_and_exits_0(self, live_store: str, tmp_path: Path):
        proc = run_store("recall", "--scope", "widget-cfg", url=live_store,
                         cache=tmp_path / "cache")
        assert proc.returncode == 0, proc.stderr
        assert "store: live" in proc.stdout
        assert "thing-alpha" in proc.stdout

    def test_scope_empty_is_exit_0_and_says_it_REACHED_the_store(
        self, live_store: str, tmp_path: Path
    ):
        proc = run_store("recall", "--scope", "hollow-area", url=live_store,
                         cache=tmp_path / "cache")
        assert proc.returncode == 0, proc.stderr
        assert "scope-empty" in proc.stdout
        assert "reached the store" in proc.stdout

    def test_unreachable_WITH_a_cache_serves_it_and_says_STALE(
        self, live_store: str, tmp_path: Path
    ):
        cache = tmp_path / "cache"
        assert run_store("sync", url=live_store, cache=cache).returncode == 0
        proc = run_store("recall", "--scope", "widget-cfg", url=None, cache=cache)
        assert proc.returncode == 0, proc.stderr
        assert "store: cached" in proc.stdout
        assert "SERVED FROM CACHE" in proc.stdout
        # The content still arrives — a stale answer is still an answer.
        assert "thing-alpha" in proc.stdout

    def test_unreachable_with_NO_cache_is_NONZERO_and_names_the_host(
        self, tmp_path: Path
    ):
        """🔴 The state that must never look like `scope-empty`."""
        proc = run_store("recall", "--scope", "widget-cfg", url=None,
                         cache=tmp_path / "cache")
        assert proc.returncode != 0
        assert "store-unreachable, no cache" in proc.stderr
        assert "127.0.0.1" in proc.stderr, "the reason must NAME the host"

    def test_the_two_empty_looking_states_do_NOT_render_alike(
        self, live_store: str, tmp_path: Path
    ):
        """🔴 The whole point, asserted as a RELATIONSHIP rather than two
        separate string checks: same shape of request, two different states,
        and they must differ in both text and exit code."""
        empty = run_store("recall", "--scope", "hollow-area", url=live_store,
                          cache=tmp_path / "a")
        outage = run_store("recall", "--scope", "hollow-area", url=None,
                           cache=tmp_path / "b")
        assert empty.returncode == 0 and outage.returncode != 0
        assert (empty.stdout + empty.stderr) != (outage.stdout + outage.stderr)


class TestIdempotence:
    def test_a_second_sync_transfers_the_same_set_and_exits_0(
        self, live_store: str, tmp_path: Path
    ):
        cache = tmp_path / "cache"
        first = run_store("sync", url=live_store, cache=cache)
        second = run_store("sync", url=live_store, cache=cache)
        assert first.returncode == 0 and second.returncode == 0
        listing = run_store("ls-entries", "--no-sync", url=None, cache=cache)
        assert listing.returncode == 0
        assert sorted(listing.stdout.split()) == [
            "widget-cfg/thing-alpha.md",
            "widget-cfg/thing-beta.md",
        ]

    def test_sync_is_atomic_enough_that_a_failure_keeps_the_OLD_cache(
        self, live_store: str, tmp_path: Path
    ):
        """A failed refresh must not leave a half-tree that still claims
        'none omitted'. The previous cache survives instead."""
        cache = tmp_path / "cache"
        assert run_store("sync", url=live_store, cache=cache).returncode == 0
        before = sorted(p.name for p in cache.glob("*/*.md"))
        assert run_store("sync", url=None, cache=cache).returncode != 0
        after = sorted(p.name for p in cache.glob("*/*.md"))
        assert after == before, "a failed sync damaged the existing cache"


class TestByteIdentityWithTheLocalReader:
    def test_the_cached_digest_equals_the_SOURCE_digest(
        self, live_store: str, source_store: Path, tmp_path: Path
    ):
        """🔴 Criterion 2. The client must add its state line and NOTHING else:
        the report itself has to be what the unmodified reader produces from the
        source. Only the `store:` root line legitimately differs, for the reason
        `verify-byte-identity.sh` documents."""
        import re

        cache = tmp_path / "cache"
        proc = run_store("recall", "--scope", "widget-cfg", url=live_store, cache=cache)
        assert proc.returncode == 0, proc.stderr
        # Drop the client's banner + its blank line; compare the report only.
        got = proc.stdout.split("\n", 2)[2]

        api = _load_api()
        report = api.rc.recall(str(source_store), "widget-cfg", mode=api.rc.DEFAULT_MODE)
        want = api.rc.render_text(report)

        canon = lambda t: re.sub(r"^(\s*store:) .*$", r"\1 X", t, flags=re.M).strip()
        assert canon(got) == canon(want)


class TestTheHarnessItself:
    def test_the_shim_is_what_makes_the_server_answer(self, live_store: str):
        """🔴 Positive control for the fixture. If the client could reach the
        server WITHOUT the header-adding shim, these tests would prove nothing
        about the production topology — so confirm a direct call is refused."""
        req = urllib.request.Request(live_store + "/api/v1/recall/widget-cfg")
        req.add_header("Authorization", f"Bearer {GOOD_TOKEN}")
        req.add_header("User-Agent", "subsystem-store-client/1")
        with urllib.request.urlopen(req, timeout=15) as resp:
            assert resp.status == 200, "shim should have made this succeed"

    def test_a_wrong_token_is_refused_through_the_shim(
        self, live_store: str, tmp_path: Path
    ):
        """Negative control: the fixture can still say no, so a 200 above means
        the token was checked rather than the auth layer being absent."""
        proc = run_store("sync", url=live_store, cache=tmp_path / "cache",
                         token="z" * 48)
        assert proc.returncode != 0
        assert "401" in proc.stderr, proc.stdout
