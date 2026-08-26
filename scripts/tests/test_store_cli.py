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
import io
import ipaddress
import os
import socket
import subprocess
import sys
import tarfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

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
    # Both URLs are yielded so a control can prove the shim is what makes the
    # difference. The previous fixture exposed only `base`, which is why the
    # "direct call is refused" control below asserted nothing of the kind.
    base = f"http://127.0.0.1:{shim.server_address[1]}"
    try:
        yield SimpleNamespace(base=base, upstream=upstream)
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
    def test_live_says_live_and_exits_0(self, live_store, tmp_path: Path):
        proc = run_store("recall", "--scope", "widget-cfg", url=live_store.base,
                         cache=tmp_path / "cache")
        assert proc.returncode == 0, proc.stderr
        assert "store: live" in proc.stdout
        assert "thing-alpha" in proc.stdout

    def test_scope_empty_is_exit_0_and_says_it_REACHED_the_store(
        self, live_store, tmp_path: Path
    ):
        proc = run_store("recall", "--scope", "hollow-area", url=live_store.base,
                         cache=tmp_path / "cache")
        assert proc.returncode == 0, proc.stderr
        assert "scope-empty" in proc.stdout
        assert "reached the store" in proc.stdout

    def test_unreachable_WITH_a_cache_serves_it_and_says_STALE(
        self, live_store, tmp_path: Path
    ):
        cache = tmp_path / "cache"
        assert run_store("sync", url=live_store.base, cache=cache).returncode == 0
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
        self, live_store, tmp_path: Path
    ):
        """🔴 The whole point, asserted as a RELATIONSHIP rather than two
        separate string checks: same shape of request, two different states,
        and they must differ in both text and exit code."""
        empty = run_store("recall", "--scope", "hollow-area", url=live_store.base,
                          cache=tmp_path / "a")
        outage = run_store("recall", "--scope", "hollow-area", url=None,
                           cache=tmp_path / "b")
        assert empty.returncode == 0 and outage.returncode != 0
        assert (empty.stdout + empty.stderr) != (outage.stdout + outage.stderr)


class TestIdempotence:
    def test_a_second_sync_transfers_the_same_set_and_exits_0(
        self, live_store, tmp_path: Path
    ):
        cache = tmp_path / "cache"
        first = run_store("sync", url=live_store.base, cache=cache)
        second = run_store("sync", url=live_store.base, cache=cache)
        assert first.returncode == 0 and second.returncode == 0
        listing = run_store("ls-entries", "--no-sync", url=None, cache=cache)
        assert listing.returncode == 0
        assert sorted(listing.stdout.split()) == [
            "widget-cfg/thing-alpha.md",
            "widget-cfg/thing-beta.md",
        ]

    def test_sync_is_atomic_enough_that_a_failure_keeps_the_OLD_cache(
        self, live_store, tmp_path: Path
    ):
        """A failed refresh must not leave a half-tree that still claims
        'none omitted'. The previous cache survives instead."""
        cache = tmp_path / "cache"
        assert run_store("sync", url=live_store.base, cache=cache).returncode == 0
        before = sorted(p.name for p in cache.glob("*/*.md"))
        assert run_store("sync", url=None, cache=cache).returncode != 0
        after = sorted(p.name for p in cache.glob("*/*.md"))
        assert after == before, "a failed sync damaged the existing cache"


class TestByteIdentityWithTheLocalReader:
    def test_the_cached_digest_equals_the_SOURCE_digest(
        self, live_store, source_store: Path, tmp_path: Path
    ):
        """🔴 Criterion 2. The client must add its state line and NOTHING else:
        the report itself has to be what the unmodified reader produces from the
        source. Only the `store:` root line legitimately differs, for the reason
        `verify-byte-identity.sh` documents."""
        import re

        cache = tmp_path / "cache"
        proc = run_store("recall", "--scope", "widget-cfg", url=live_store.base, cache=cache)
        assert proc.returncode == 0, proc.stderr
        # Drop the client's banner + its blank line; compare the report only.
        got = proc.stdout.split("\n", 2)[2]

        api = _load_api()
        report = api.rc.recall(str(source_store), "widget-cfg", mode=api.rc.DEFAULT_MODE)
        want = api.rc.render_text(report)

        canon = lambda t: re.sub(r"^(\s*store:) .*$", r"\1 X", t, flags=re.M).strip()
        assert canon(got) == canon(want)


class TestHostileOrBrokenArchives:
    """🔴 Every guard here was previously UNTESTED — an audit had to hand-build
    hostile tars to prove they fired at all. A guard nobody has watched work is
    a claim.

    They all assert the same relationship: a bad ARCHIVE is loud and distinct
    from an OUTAGE. An outage is benign and gets absorbed into "served from
    cache" at exit 0; a server shipping a link or a traversal member must never
    be.
    """

    @staticmethod
    def _serve_once(body: bytes, content_type: str, headers: dict | None = None):
        """A one-shot server returning exactly `body` with a 200."""
        class _H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, f"http://127.0.0.1:{srv.server_address[1]}"

    @staticmethod
    def _tar_with(member: tarfile.TarInfo, payload: bytes = b"x") -> bytes:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as tar:
            member.size = len(payload)
            tar.addfile(member, io.BytesIO(payload))
        return buf.getvalue()

    def _run_against(self, body, ctype, cache, headers=None):
        srv, url = self._serve_once(body, ctype, headers)
        try:
            return run_store("sync", url=url, cache=cache)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_a_traversal_member_is_REFUSED_not_treated_as_an_outage(
        self, tmp_path: Path
    ):
        info = tarfile.TarInfo("../escaped.md")
        proc = self._run_against(
            self._tar_with(info), "application/gzip", tmp_path / "cache"
        )
        assert proc.returncode != 0
        assert "REFUSED" in proc.stderr, proc.stderr
        assert "cached" not in proc.stdout.lower()

    def test_a_symlink_member_is_REFUSED(self, tmp_path: Path):
        info = tarfile.TarInfo("widget-cfg/leak.md")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        proc = self._run_against(
            self._tar_with(info, b""), "application/gzip", tmp_path / "cache"
        )
        assert proc.returncode != 0
        assert "REFUSED" in proc.stderr, proc.stderr

    def test_a_count_disagreeing_with_the_header_is_REFUSED(self, tmp_path: Path):
        """The server's comment claims a truncated transfer is 'visible as a
        disagreement'. It is only visible if somebody compares — this is the
        test that the comparison exists."""
        info = tarfile.TarInfo("widget-cfg/one.md")
        proc = self._run_against(
            self._tar_with(info),
            "application/gzip",
            tmp_path / "cache",
            headers={"X-Store-Entries": "99"},
        )
        assert proc.returncode != 0
        assert "99" in proc.stderr and "REFUSED" in proc.stderr, proc.stderr

    def test_a_200_that_is_NOT_a_tar_serves_the_cache_instead_of_crashing(
        self, live_store, tmp_path: Path
    ):
        """🔴 Realistic BECAUSE of this PR's own Cloudflare finding: the edge can
        answer 200 with an HTML interstitial. This previously escaped every
        handler as a traceback at exit 1 with a healthy cache sitting unused."""
        cache = tmp_path / "cache"
        assert run_store("sync", url=live_store.base, cache=cache).returncode == 0
        proc = self._run_against(
            b"<html>Just a moment...</html>", "text/html", cache
        )
        assert "Traceback" not in proc.stderr, proc.stderr
        assert proc.returncode != 0                      # sync could not refresh
        assert "did not return an archive" in proc.stderr
        # and the good cache survived
        assert sorted(p.name for p in cache.glob("*/*.md")) == [
            "thing-alpha.md",
            "thing-beta.md",
        ]

    def test_a_legitimate_filename_containing_dots_is_NOT_refused(
        self, source_store: Path, live_store, tmp_path: Path
    ):
        """`".." in name` was over-broad: an ordinary entry called `a..b.md`
        aborted the whole sync and rendered as an outage. The server puts no
        constraint on entry filenames, so this is reachable with a real file."""
        (source_store / "widget-cfg" / "a..b.md").write_text(
            _entry("a..b", "widget-cfg", "- 2026-01-04: dots are legal.")
        )
        proc = run_store("sync", url=live_store.base, cache=tmp_path / "cache")
        assert proc.returncode == 0, proc.stderr + proc.stdout
        assert (tmp_path / "cache" / "widget-cfg" / "a..b.md").is_file()


class TestConcurrentSync:
    def test_ten_concurrent_syncs_never_leave_a_SHORT_cache(
        self, source_store: Path, live_store, tmp_path: Path
    ):
        """🔴 MEASURED FAILURE BEFORE THE FIX: 3 of 10 trials left 183/305,
        256/305 and 292/305 entries with no error, and others died with
        `FileExistsError`. The staging dir had a fixed name and was rmtree'd at
        the top of every run, so concurrent runs shared it. The reader then
        rendered that partial tree under its own 'none omitted' header.
        """
        # 🔴 THE SIZE IS THE TEST. A first version used 40 entries and PASSED
        # against the broken code — the extract window was too small for two
        # runs to collide, so it asserted nothing. The audit reproduced the
        # failure at 305 entries, so that is the fixture size: a race test whose
        # window is smaller than the race is a green that means nothing.
        for i in range(303):
            (source_store / "widget-cfg" / f"bulk-{i:03d}.md").write_text(
                _entry(f"bulk-{i:03d}", "widget-cfg", f"- 2026-01-05: item {i}.")
            )
        expected = len(list(source_store.glob("*/*.md")))
        assert expected >= 300, f"fixture too small to race: {expected}"
        cache = tmp_path / "cache"

        results: list = []
        threads = [
            threading.Thread(
                target=lambda: results.append(
                    run_store("sync", url=live_store.base, cache=cache)
                )
            )
            for _ in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(r.returncode == 0 for r in results), [
            (r.returncode, r.stderr[-300:]) for r in results if r.returncode != 0
        ]
        assert not any("Traceback" in r.stderr for r in results)
        got = len(list(cache.glob("*/*.md")))
        assert got == expected, f"cache truncated: {got}/{expected} entries"


class TestScopeFilterNeverNarrowsTheSharedCache:
    """🔴 `sync --scope X` and `validate --scope X` used to REPLACE the whole
    cache with one scope — measured 305 entries down to 2 — after which an
    offline recall of any other scope reported 'nothing recorded yet' at exit 0,
    a claim about the STORE derived from a filtered cache."""

    @pytest.mark.parametrize("cmd", [("sync",), ("validate",)])
    def test_the_cache_stays_complete(self, live_store, tmp_path: Path, cmd):
        cache = tmp_path / "cache"
        assert run_store("sync", url=live_store.base, cache=cache).returncode == 0
        before = sorted(p.name for p in cache.glob("*/*.md"))
        run_store(*cmd, "--scope", "widget-cfg", url=live_store.base, cache=cache)
        after = sorted(p.name for p in cache.glob("*/*.md"))
        assert after == before, f"{cmd[0]} --scope narrowed the shared cache"

    def test_the_stamp_records_that_the_cache_is_COMPLETE(
        self, live_store, tmp_path: Path
    ):
        cache = tmp_path / "cache"
        assert run_store("sync", url=live_store.base, cache=cache).returncode == 0
        assert "coverage=ALL" in (cache / ".sync-stamp").read_text()


class TestReaderExitCodePassesThrough:
    def test_an_all_malformed_scope_exits_NONZERO_like_the_reader(
        self, source_store: Path, live_store, tmp_path: Path
    ):
        """The module docstring promised the reader's codes pass through while
        the code hardcoded 0. `/resume` branches on the CODE, so a machine
        consumer was told 'fine' for a scope nothing could be read from."""
        broken = source_store / "rubble-pile"
        broken.mkdir()
        (broken / "junk.md").write_text("no front matter, no headings, nothing\n")
        proc = run_store(
            "recall", "--scope", "rubble-pile", url=live_store.base,
            cache=tmp_path / "cache",
        )
        api = _load_api()
        report = api.rc.recall(str(source_store), "rubble-pile", mode=api.rc.DEFAULT_MODE)
        want = api.rc._exit_for(report.status, f"{report.scope}/", report.malformed)
        assert proc.returncode == want, (
            f"store exited {proc.returncode}, reader would exit {want}"
        )


class TestUnreadableScope:
    def test_an_unreadable_scope_is_NOT_reported_as_scope_empty(
        self, source_store: Path, live_store, tmp_path: Path
    ):
        """🔴 THE HEADLINE DEFECT. `Path.glob` swallows PermissionError and
        returns [], so a chmod-000 scope answered 200 / exit 0 with the scope
        silently omitted, and the client printed 'reached the store; nothing
        recorded'. That is the exact lie this whole client exists to prevent.
        """
        if os.geteuid() == 0:
            pytest.skip("root ignores directory permissions; the guard is unreachable")
        locked = source_store / "hollow-area"
        locked.chmod(0o000)
        try:
            proc = run_store(
                "recall", "--scope", "hollow-area", url=live_store.base,
                cache=tmp_path / "cache",
            )
            combined = proc.stdout + proc.stderr
            assert "scope-empty" not in combined, combined
            assert proc.returncode != 0, combined
            assert "hollow-area" in combined, combined
        finally:
            locked.chmod(0o755)


class TestTheHarnessItself:
    def test_the_shim_is_what_makes_the_server_answer(self, live_store):
        """🔴 Positive control for the fixture, REWRITTEN.

        The previous version's docstring said "confirm a direct call is
        refused"; its body issued a request through the SHIM and asserted 200,
        never touching the upstream — the fixture did not even expose that URL.
        The property held, but the named control was inert, which is the
        "description claims coverage the body does not provide" failure.

        This version drives BOTH legs and asserts they differ: direct → 401,
        through the shim → 200. If the client could reach the server without the
        header-adding hop, every test in this file would be about a topology
        production does not have.
        """
        def _get(base: str) -> int:
            req = urllib.request.Request(base + "/api/v1/recall/widget-cfg")
            req.add_header("Authorization", f"Bearer {GOOD_TOKEN}")
            req.add_header("User-Agent", "subsystem-store-client/1")
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    return resp.status
            except urllib.error.HTTPError as exc:
                return exc.code

        direct = _get(live_store.upstream)
        shimmed = _get(live_store.base)
        assert direct == 401, f"direct call was NOT refused (got {direct})"
        assert shimmed == 200, f"shimmed call did not succeed (got {shimmed})"

    def test_a_wrong_token_is_refused_through_the_shim(
        self, live_store, tmp_path: Path
    ):
        """Negative control: the fixture can still say no, so a 200 above means
        the token was checked rather than the auth layer being absent."""
        proc = run_store("sync", url=live_store.base, cache=tmp_path / "cache",
                         token="z" * 48)
        assert proc.returncode != 0
        assert "401" in proc.stderr, proc.stdout
