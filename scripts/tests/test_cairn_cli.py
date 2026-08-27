#!/usr/bin/env python3
"""`scripts/cairn` — the read-through client's four states.

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
import time
import tarfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
CAIRN_CLI = REPO / "scripts" / "cairn"
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
    # 🔴 A SECOND *POPULATED* SCOPE, and it is load-bearing. The scope-filter
    # regression test asserts that `sync --scope X` does not narrow the shared
    # cache — but with `hollow-area` empty, a filtered cache and a complete one
    # hold the SAME `*/*.md` set, so the test passed against the broken code and
    # both re-threading mutants survived the whole suite. The measured original
    # failure was 305 entries -> 2 and needs a second scope with content in it.
    (root / "gizmo-notes").mkdir(parents=True)
    (root / "widget-cfg" / "thing-alpha.md").write_text(
        _entry("thing-alpha", "widget-cfg", "- 2026-01-02: probe lies for 40s.")
    )
    (root / "widget-cfg" / "thing-beta.md").write_text(
        _entry("thing-beta", "widget-cfg", "- 2026-01-03: sidecar drops its lease.")
    )
    (root / "gizmo-notes" / "other-thing.md").write_text(
        _entry("other-thing", "gizmo-notes", "- 2026-01-04: a different scope.")
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
                # 🔴 LOWERCASED, LIKE THE REAL EDGE. This shim used to forward
                # header names in whatever case the in-process server sent, so
                # it reproduced production's TOPOLOGY but not its
                # NORMALISATION — and HTTP/2 (which Cloudflare speaks) sends
                # every header name lowercased. That gap let a real defect ship:
                # the client did `dict(resp.headers).get("X-Store-Entries")`,
                # which returns None against a lowercase wire, so the freshness
                # stamp, the revision and — worst — the truncated-transfer count
                # check were all silently inert in production while 358 tests
                # passed. Found by the first live call after deploy, not here.
                self.send_header(key.lower(), value)
        self.send_header("content-length", str(len(body)))
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


def run_cairn(*args: str, url: str | None, cache: Path, token: str = GOOD_TOKEN):
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
        [sys.executable, str(CAIRN_CLI), "--cache", str(cache), "--timeout", "5", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    return proc



ORPHAN_GRACE = 3600  # mirrors scripts/cairn::ORPHAN_GRACE_SECONDS


def _fetch_snapshot_bytes(base: str) -> bytes:
    """The real snapshot body, so truncation fixtures are built from real bytes."""
    req = urllib.request.Request(base + "/api/v1/snapshot")
    req.add_header("Authorization", f"Bearer {GOOD_TOKEN}")
    req.add_header("User-Agent", "subsystem-store-client/1")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read()


def _dead_port() -> int:
    """A port nothing is listening on — bound then released, so it is real."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class TestTheFourStates:
    def test_live_says_live_and_exits_0(self, live_store, tmp_path: Path):
        proc = run_cairn("recall", "--scope", "widget-cfg", url=live_store.base,
                         cache=tmp_path / "cache")
        assert proc.returncode == 0, proc.stderr
        assert "cairn: live" in proc.stdout
        assert "thing-alpha" in proc.stdout

    def test_scope_empty_is_exit_0_and_says_it_REACHED_the_store(
        self, live_store, tmp_path: Path
    ):
        proc = run_cairn("recall", "--scope", "hollow-area", url=live_store.base,
                         cache=tmp_path / "cache")
        assert proc.returncode == 0, proc.stderr
        assert "scope-empty" in proc.stdout
        assert "reached the store" in proc.stdout

    def test_unreachable_WITH_a_cache_serves_it_and_says_STALE(
        self, live_store, tmp_path: Path
    ):
        cache = tmp_path / "cache"
        assert run_cairn("sync", url=live_store.base, cache=cache).returncode == 0
        proc = run_cairn("recall", "--scope", "widget-cfg", url=None, cache=cache)
        assert proc.returncode == 0, proc.stderr
        assert "cairn: cached" in proc.stdout
        assert "SERVED FROM CACHE" in proc.stdout
        # The content still arrives — a stale answer is still an answer.
        assert "thing-alpha" in proc.stdout

    def test_unreachable_with_NO_cache_is_NONZERO_and_names_the_host(
        self, tmp_path: Path
    ):
        """🔴 The state that must never look like `scope-empty`."""
        proc = run_cairn("recall", "--scope", "widget-cfg", url=None,
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
        empty = run_cairn("recall", "--scope", "hollow-area", url=live_store.base,
                          cache=tmp_path / "a")
        outage = run_cairn("recall", "--scope", "hollow-area", url=None,
                           cache=tmp_path / "b")
        assert empty.returncode == 0 and outage.returncode != 0
        assert (empty.stdout + empty.stderr) != (outage.stdout + outage.stderr)


class TestIdempotence:
    def test_a_second_sync_transfers_the_same_set_and_exits_0(
        self, live_store, tmp_path: Path
    ):
        cache = tmp_path / "cache"
        first = run_cairn("sync", url=live_store.base, cache=cache)
        second = run_cairn("sync", url=live_store.base, cache=cache)
        assert first.returncode == 0 and second.returncode == 0
        listing = run_cairn("ls-entries", "--no-sync", url=None, cache=cache)
        assert listing.returncode == 0
        assert sorted(listing.stdout.split()) == [
            "gizmo-notes/other-thing.md",
            "widget-cfg/thing-alpha.md",
            "widget-cfg/thing-beta.md",
        ]

    def test_sync_is_atomic_enough_that_a_failure_keeps_the_OLD_cache(
        self, live_store, tmp_path: Path
    ):
        """A failed refresh must not leave a half-tree that still claims
        'none omitted'. The previous cache survives instead."""
        cache = tmp_path / "cache"
        assert run_cairn("sync", url=live_store.base, cache=cache).returncode == 0
        before = sorted(p.name for p in cache.glob("*/*.md"))
        assert run_cairn("sync", url=None, cache=cache).returncode != 0
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
        proc = run_cairn("recall", "--scope", "widget-cfg", url=live_store.base, cache=cache)
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
                self.send_header("content-type", content_type)
                self.send_header("content-length", str(len(body)))
                for k, v in (headers or {}).items():
                    # Lowercased like the real edge — see `_CloudflareShim`.
                    # Without this, a test can pass against a client whose
                    # header lookup only works for the case the FIXTURE happens
                    # to send, which is exactly how the production defect hid.
                    self.send_header(k.lower(), v)
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
            return run_cairn("sync", url=url, cache=cache)
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
        test that the comparison exists.

        🔴 AND IT DID NOT, IN PRODUCTION, FOR THE WHOLE OF #863. The client read
        the header with a case-SENSITIVE lookup, so behind Cloudflare (which
        lowercases every name over HTTP/2) `declared` was always None and this
        guard skipped the comparison entirely — while this very test passed
        locally, because the fixture sent the case the broken code wanted. Both
        test servers now lowercase, so this test finally exercises the shape
        production actually delivers. Found by a live call, not by 358 tests.
        """
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
        assert run_cairn("sync", url=live_store.base, cache=cache).returncode == 0
        proc = self._run_against(
            b"<html>Just a moment...</html>", "text/html", cache
        )
        assert "Traceback" not in proc.stderr, proc.stderr
        assert proc.returncode != 0                      # sync could not refresh
        assert "did not return an archive" in proc.stderr
        # and the good cache survived
        assert sorted(p.name for p in cache.glob("*/*.md")) == [
            "other-thing.md",
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
        proc = run_cairn("sync", url=live_store.base, cache=tmp_path / "cache")
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
                    run_cairn("sync", url=live_store.base, cache=cache)
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

    @pytest.mark.parametrize("cmd", [("sync",), ("validate",), ("ls-entries",)])
    def test_the_cache_stays_complete(self, live_store, tmp_path: Path, cmd):
        cache = tmp_path / "cache"
        assert run_cairn("sync", url=live_store.base, cache=cache).returncode == 0
        before = sorted(p.name for p in cache.glob("*/*.md"))
        run_cairn(*cmd, "--scope", "widget-cfg", url=live_store.base, cache=cache)
        after = sorted(p.name for p in cache.glob("*/*.md"))
        assert after == before, f"{cmd[0]} --scope narrowed the shared cache"

    @pytest.mark.parametrize("cmd", [("sync",), ("validate",)])
    def test_the_OTHER_scope_still_answers_afterwards(
        self, live_store, tmp_path: Path, cmd
    ):
        """🔴 The PROPERTY, not the artifact. `test_the_cache_stays_complete`
        counts files; this asserts the consequence the original defect actually
        had — after a scoped command, an offline recall of a DIFFERENT scope
        must still find it, rather than reporting "nothing recorded yet" at
        exit 0 from a cache that was quietly narrowed.
        """
        cache = tmp_path / "cache"
        assert run_cairn("sync", url=live_store.base, cache=cache).returncode == 0
        run_cairn(*cmd, "--scope", "widget-cfg", url=live_store.base, cache=cache)
        proc = run_cairn(
            "recall", "--scope", "gizmo-notes", "--no-sync", url=None, cache=cache
        )
        assert proc.returncode == 0, proc.stderr
        assert "other-thing" in proc.stdout, proc.stdout

    def test_the_stamp_records_that_the_cache_is_COMPLETE(
        self, live_store, tmp_path: Path
    ):
        cache = tmp_path / "cache"
        assert run_cairn("sync", url=live_store.base, cache=cache).returncode == 0
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
        proc = run_cairn(
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
            proc = run_cairn(
                "recall", "--scope", "hollow-area", url=live_store.base,
                cache=tmp_path / "cache",
            )
            combined = proc.stdout + proc.stderr
            assert "scope-empty" not in combined, combined
            assert proc.returncode != 0, combined
            assert "hollow-area" in combined, combined
        finally:
            locked.chmod(0o755)


class TestSymlinkedScopeIsRefusedNotDropped:
    def test_a_symlinked_scope_dir_is_NOT_reported_as_scope_empty(
        self, source_store: Path, live_store, tmp_path: Path
    ):
        """🔴 THE DEFECT THE PREVIOUS FIX ROUND INTRODUCED.

        The symlink guard added to stop the server following links FILTERED
        symlinked scope dirs out of the candidate list, so they never reached
        the `unreadable` report and never reached the tar. Measured: a symlinked
        scope holding 2 entries rendered as `scope-empty — nothing recorded` at
        exit 0, while the PREVIOUS commit served them. The headline defect of
        this whole client, reintroduced one level up by the guard added to close
        it at the entry level. A guard that SKIPS is a silent omission.
        """
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "sneaky.md").write_text(
            _entry("sneaky", "linked-scope", "- 2026-01-09: lives elsewhere.")
        )
        (source_store / "linked-scope").symlink_to(outside, target_is_directory=True)

        proc = run_cairn(
            "recall", "--scope", "linked-scope", url=live_store.base,
            cache=tmp_path / "cache",
        )
        combined = proc.stdout + proc.stderr
        assert "scope-empty" not in combined, combined
        assert proc.returncode != 0, combined
        assert "linked-scope" in combined, combined


class TestValidateActuallyRuns:
    def test_validate_exits_0_on_a_clean_cache(self, live_store, tmp_path: Path):
        """🔴 `cairn validate` NEVER WORKED — it passed `--validate` to the
        READER, which has no such flag, so every invocation exited 2 with
        `unrecognized arguments`. The test written to close that gap asserted
        only that the cache directory was unchanged, so it passed green over a
        command that failed on every input. Assert the OUTCOME."""
        cache = tmp_path / "cache"
        assert run_cairn("sync", url=live_store.base, cache=cache).returncode == 0
        proc = run_cairn("validate", "--no-sync", url=None, cache=cache)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "unrecognized arguments" not in (proc.stdout + proc.stderr)

    def test_validate_exits_NONZERO_on_a_malformed_cache(
        self, source_store: Path, live_store, tmp_path: Path
    ):
        """Negative control: a validator that never fails is not a validator."""
        (source_store / "widget-cfg" / "broken.md").write_text(
            "aliases: [wrapped,\n  list]\nno front matter at all\n"
        )
        cache = tmp_path / "cache"
        assert run_cairn("sync", url=live_store.base, cache=cache).returncode == 0
        proc = run_cairn("validate", "--no-sync", url=None, cache=cache)
        assert proc.returncode != 0, proc.stdout + proc.stderr


class TestArchiveSizeAndTruncation:
    def test_a_truncated_GZIP_serves_the_cache_instead_of_a_traceback(
        self, live_store, tmp_path: Path
    ):
        """🔴 The gzip switch reopened the crash finding one exception type over.
        A short body used to raise `tarfile.ReadError` (handled); compressed it
        raises `EOFError`, which was not, so it escaped as a traceback at exit 1
        with a healthy cache unused."""
        cache = tmp_path / "cache"
        assert run_cairn("sync", url=live_store.base, cache=cache).returncode == 0
        good = _fetch_snapshot_bytes(live_store.base)
        truncated = good[: len(good) // 2]
        proc = TestHostileOrBrokenArchives()._run_against(
            truncated, "application/gzip", cache
        )
        assert "Traceback" not in proc.stderr, proc.stderr
        assert proc.returncode != 0
        assert sorted(p.name for p in cache.glob("*/*.md")), "cache was destroyed"

    def test_a_decompression_BOMB_is_refused(self, tmp_path: Path):
        """🔴 Gzip removed the natural bound: before it, the response body
        limited what could be extracted. Measured on the previous commit: a
        203,934-byte body wrote 209,715,200 bytes to disk and reported
        `live … 1 entries`, exit 0."""
        buf = io.BytesIO()
        payload = b"\0" * (300 * 1024 * 1024)
        with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as tar:
            info = tarfile.TarInfo("widget-cfg/bomb.md")
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        body = buf.getvalue()
        assert len(body) < 2 * 1024 * 1024, "fixture is not actually compressed"
        proc = TestHostileOrBrokenArchives()._run_against(
            body, "application/gzip", tmp_path / "cache"
        )
        assert proc.returncode != 0, proc.stdout
        assert "REFUSED" in proc.stderr and "ceiling" in proc.stderr, proc.stderr
        assert not list((tmp_path / "cache").glob("*/*.md"))


class TestNonRegularFilesInTheStore:
    def test_a_directory_named_md_is_REFUSED_by_name_not_by_errno(
        self, source_store: Path, live_store, tmp_path: Path
    ):
        """🔴 RENAMED — the old name claimed a property the code does not have.

        It was `test_a_directory_named_md_does_not_503_the_whole_store`, and it
        does 503 the whole store: the client always requests unfiltered, so one
        bad file in ANY scope denies every scope. (The SERVER isolates correctly
        — raw `?scope=healthy` returns 200 — but no client path reaches that.)
        The body only ever asserted the refusal WORDING, which is the real,
        deliberate property. A test whose name asserts availability while its
        body asserts classification is the "description wider than the
        implementation" pattern, in a test written to close a finding about it.

        What is pinned: the failure is a CLASSIFIED refusal naming the offender,
        not a raw `[Errno 21] Is a directory` escaping from an unguarded open.
        """
        (source_store / "widget-cfg" / "trap.md").mkdir()
        proc = run_cairn(
            "recall", "--scope", "gizmo-notes", url=live_store.base,
            cache=tmp_path / "cache",
        )
        combined = proc.stdout + proc.stderr
        # 🔴 ASSERT THE REFUSAL WORDING, not merely "it did not crash". At
        # 44eb5841 this produced `store unreadable: [Errno 21] Is a directory:
        # …/trap.md` — which also names the file and also avoids the literal
        # word "IsADirectoryError", so a test asserting only those two things
        # passed against the broken code. It has to pin the classified refusal.
        # The refusal now speaks the classifier's vocabulary (`directory
        # refused`) rather than an ad-hoc sentence. What is pinned is unchanged:
        # a CLASSIFIED refusal naming the offender, not a raw `[Errno 21] Is a
        # directory` escaping from an unguarded `open()`.
        assert "directory refused" in combined, combined
        assert "trap.md" in combined, combined
        assert "Errno 21" not in combined, combined


class TestEntryTableCellsHaveBehaviour:
    """🔴 Three `_ENTRY_ACTIONS` cells were pinned ONLY by the constants test.

    Measured: flipping `KIND_LINK_TO_FILE` REFUSE -> TAKE and deselecting
    `test_the_decision_table_is_pinned` left **342 passed, 0 failed**, while the
    server happily shipped a file from OUTSIDE the store:

        unmutated: 503 "widget-cfg/innocent.md: link-to-file refused"
        mutant:    200, member content "BEGIN OPENSSH PRIVATE KEY …"

    `entry_broken_link -> SKIP` and `entry_other -> SKIP` survived the same way,
    the latter being the FIFO whose own comment says it "blocked open() forever,
    leaking a handler thread permanently". A constants assertion is exactly the
    kind a future edit updates ALONGSIDE the code it was meant to stop, so each
    cell needs an observable of its own.
    """

    def _snapshot_raw(self, base: str) -> bytes | None:
        """The raw body on a 200, else None — so a leak assertion can DECOMPRESS
        rather than grep a gzip stream for plaintext it can never contain."""
        req = urllib.request.Request(base + "/api/v1/snapshot")
        req.add_header("Authorization", f"Bearer {GOOD_TOKEN}")
        req.add_header("User-Agent", "subsystem-store-client/1")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read()
        except urllib.error.HTTPError:
            return None

    def _snapshot_members(self, base: str) -> tuple[int, str]:
        req = urllib.request.Request(base + "/api/v1/snapshot")
        req.add_header("Authorization", f"Bearer {GOOD_TOKEN}")
        req.add_header("User-Agent", "subsystem-store-client/1")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")

    def test_a_symlinked_entry_never_ships_an_OFF_STORE_file(
        self, source_store: Path, live_store, tmp_path: Path
    ):
        """🔴 The security-relevant cell. `/snapshot` walks EVERY scope in one
        authenticated GET, where `/recall` makes you name one."""
        secret = tmp_path / "id_ed25519"
        secret.write_text("BEGIN OPENSSH PRIVATE KEY — outside the store")
        (source_store / "widget-cfg" / "innocent.md").symlink_to(secret)

        # 🔴 THE LEAK CHECK RUNS FIRST, AND THE ORDER IS THE WHOLE FIX.
        #
        # v1 of this line was `assert "OPENSSH PRIVATE KEY" not in body` — which
        # CANNOT fail on a 200, because `/snapshot` ships `w:gz` and the
        # plaintext is never in the raw bytes. v2 decompressed correctly but sat
        # BELOW `assert code == 503`, which dominates it: with the mutant the
        # test dies on the status line and the leak block never executes.
        # Measured both ways — unmutated, `raw is None` so the block was skipped;
        # mutated, pytest never reached it. So v2 left the suite byte-for-byte
        # as it was before v1 was "fixed": an assertion that cannot fail became
        # an assertion that does not run.
        #
        # Hoisted above the status assert, and no `if raw is not None` guard —
        # that guard was a second inertia path (`_snapshot_raw` returns None on
        # ANY HTTPError, so a 429 from the limiter would silently skip it too).
        raw = self._snapshot_raw(live_store.base)
        if raw is not None:  # a 200 came back: it MUST NOT carry the secret
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r") as tar:
                names = tar.getnames()
                for member in tar.getmembers():
                    handle = tar.extractfile(member)
                    content = handle.read() if handle is not None else b""
                    assert b"OPENSSH PRIVATE KEY" not in content, member.name
                assert "widget-cfg/innocent.md" not in names, names
            raise AssertionError(
                "/snapshot answered 200 for a store containing a symlinked "
                f"entry; it must refuse. members={names}"
            )

        code, body = self._snapshot_members(live_store.base)
        assert code == 503, body[:300]
        assert "innocent.md" in body and "link-to-file refused" in body, body[:300]

    def test_a_FIFO_named_md_is_refused_rather_than_opened(
        self, source_store: Path, live_store, tmp_path: Path
    ):
        """A FIFO blocks `open()` forever and leaks a handler thread on a
        threading server. Refused means the request RETURNS."""
        os.mkfifo(source_store / "widget-cfg" / "pipe.md")
        code, body = self._snapshot_members(live_store.base)
        assert code == 503, body[:300]
        assert "pipe.md" in body and "other refused" in body, body[:300]

    def test_a_dangling_ENTRY_link_is_refused_not_skipped(
        self, source_store: Path, live_store, tmp_path: Path
    ):
        """Non-dotfile, so the name rules do not filter it first — this reaches
        the type table, unlike the Emacs lock-file case."""
        (source_store / "widget-cfg" / "gone.md").symlink_to(tmp_path / "nowhere")
        code, body = self._snapshot_members(live_store.base)
        assert code == 503, body[:300]
        assert "gone.md" in body and "broken-link refused" in body, body[:300]


class TestOrphanStagingIsReaped:
    def test_an_old_staging_dir_is_removed_by_the_next_sync(
        self, live_store, tmp_path: Path
    ):
        """🔴 Per-run staging names fixed the truncation race but removed the
        self-healing the fixed name had: an audit SIGKILLed three syncs and the
        orphans survived every later clean run. With a sync timer they grow
        without bound in `~/.cache`."""
        cache = tmp_path / "cache"
        orphan = cache.parent / f"{cache.name}.new-deadbeef"
        orphan.mkdir(parents=True)
        (orphan / "junk.md").write_text("left behind by a SIGKILL")
        old = time.time() - (ORPHAN_GRACE + 60)
        os.utime(orphan, (old, old))

        assert run_cairn("sync", url=live_store.base, cache=cache).returncode == 0
        assert not orphan.exists(), "orphan staging dir survived a clean sync"

    def test_an_old_DOT_OLD_orphan_is_also_removed(self, live_store, tmp_path: Path):
        """The `.old-*` half of the glob had no test: dropping that prefix
        SURVIVED the whole suite, so half the reaper was unguarded."""
        cache = tmp_path / "cache"
        orphan = cache.parent / f"{cache.name}.old-deadbeef"
        orphan.mkdir(parents=True)
        old = time.time() - (ORPHAN_GRACE + 60)
        os.utime(orphan, (old, old))
        assert run_cairn("sync", url=live_store.base, cache=cache).returncode == 0
        assert not orphan.exists(), ".old- orphan survived a clean sync"

    def test_a_symlinked_orphan_is_UNLINKED_not_counted_as_reaped(
        self, live_store, tmp_path: Path
    ):
        """🔴 The fix for this was exactly as invisible as the bug: BOTH mutants
        (`lstat`->`stat`, and dropping the unlink arm) survived all 333 tests.

        `rmtree` on a symlink is a silent no-op, so the old code counted the
        orphan as reaped while never removing it — and the count is the only
        evidence, which is what made the miscount invisible. Asserts the link is
        gone AND the target survives, because unlinking the wrong one is the
        obvious way to "fix" this and lose data.
        """
        cache = tmp_path / "cache"
        cache.parent.mkdir(parents=True, exist_ok=True)
        target = tmp_path / "precious"
        target.mkdir()
        (target / "keep.md").write_text("must survive")
        link = cache.parent / f"{cache.name}.new-symlinked"
        link.symlink_to(target, target_is_directory=True)
        old = time.time() - (ORPHAN_GRACE + 60)
        os.utime(link, (old, old), follow_symlinks=False)

        assert run_cairn("sync", url=live_store.base, cache=cache).returncode == 0
        assert not link.exists() and not link.is_symlink(), "symlinked orphan survived"
        assert (target / "keep.md").read_text() == "must survive", "reaper ate the target"

    def test_a_RECENT_staging_dir_is_left_alone(self, live_store, tmp_path: Path):
        """🔴 Negative control, and it pins the grace period FROM BELOW — the
        direction the docstring's whole safety argument rests on. Shrinking the
        grace to 1 second SURVIVED the previous suite, because the fixture aged
        its "recent" dir ~0 s and so sat on its own boundary: a 1-second grace
        would delete a live concurrent sync's staging dir with the suite green.

        Aged to just INSIDE the window instead, so the assertion is about the
        grace period rather than about scheduling luck.
        """
        cache = tmp_path / "cache"
        fresh = cache.parent / f"{cache.name}.new-inflight"
        fresh.mkdir(parents=True)
        recent = time.time() - (ORPHAN_GRACE // 2)
        os.utime(fresh, (recent, recent))
        assert run_cairn("sync", url=live_store.base, cache=cache).returncode == 0
        assert fresh.exists(), "reaper deleted a possibly-live staging dir"


class TestNotAScopeIsSkippedNotRefused:
    """🔴 The predicate the `unreadable` list must encode: REFUSE a thing that
    is a scope/entry but cannot be served safely; SKIP a thing that is not one.

    Round 3 got the order wrong and tested `is_symlink()` before `is_dir()`, so
    a symlinked `README.md` at the store root — not a scope, never was — took
    the whole snapshot from exit 0 to exit 3 for every caller and every scope,
    while a plain `README.md` in the same place was still skipped silently. Two
    spellings of "not a scope", opposite outcomes.
    """

    def test_a_symlinked_FILE_at_the_store_root_is_skipped(
        self, source_store: Path, live_store, tmp_path: Path
    ):
        target = tmp_path / "notes.md"
        target.write_text("not a scope, just a file")
        (source_store / "README.md").symlink_to(target)
        proc = run_cairn(
            "recall", "--scope", "widget-cfg", url=live_store.base,
            cache=tmp_path / "cache",
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "thing-alpha" in proc.stdout

    def test_a_plain_FILE_at_the_store_root_is_skipped_too(
        self, source_store: Path, live_store, tmp_path: Path
    ):
        """The other half of the relationship — asserted so the two cannot drift
        apart again. Either both skip or the guard is inconsistent."""
        (source_store / "README.md").write_text("not a scope")
        proc = run_cairn(
            "recall", "--scope", "widget-cfg", url=live_store.base,
            cache=tmp_path / "cache",
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_an_editor_lock_file_does_not_deny_the_SNAPSHOT_path(
        self, source_store: Path, live_store, tmp_path: Path
    ):
        """🔴 An Emacs lock file is `.#entry.md` — a DANGLING SYMLINK whose name
        ends in `.md`. The entry-level symlink refusal made one open buffer 503
        the entire store for every caller. Reproduced at two earlier commits, so
        it predates this fix; it shares the root cause above.

        ⚠ NAMED FOR THE PATH IT ACTUALLY COVERS. `/api/v1/recall/<scope>` is
        STILL affected: `load_index` uses `scope_dir.glob("*.md")`, and pathlib
        glob DOES match a leading dot, so a lock file still 503s that route. The
        fix lives in `scripts/lib/subsystem_resolver.py`, which this card's
        non-goals forbid touching ("the local store is alive and heavily used;
        this task moves files between hosts"). Calling this
        `..._does_not_deny_the_whole_store` would be the exact
        description-wider-than-implementation defect an earlier round was
        renamed to close. Closing condition: `cairn recall` and
        `/api/v1/recall/<scope>` both serve a scope containing `.#x.md`.
        """
        (source_store / "widget-cfg" / ".#thing-alpha.md").symlink_to(
            "zach@nixos.12345:1700000000"
        )
        proc = run_cairn(
            "recall", "--scope", "widget-cfg", url=live_store.base,
            cache=tmp_path / "cache",
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "thing-alpha" in proc.stdout

    @pytest.mark.parametrize("shape", ["dangling", "loop"])
    def test_a_BROKEN_scope_pointer_is_refused_not_silently_skipped(
        self, source_store: Path, live_store, tmp_path: Path, shape: str
    ):
        """🔴 THE ROUND-4 REGRESSION, pinned in both directions.

        `is_dir()` returns False for a dangling symlink AND for a symlink loop —
        `pathlib` swallows ENOENT and ELOOP — so reordering the guard to check
        `is_dir()` first made both vanish from the snapshot silently. Measured
        across the two commits, same fixture:

            bc2364eb: dangling scope link -> rc 3, "symlink refused"
            a17c5df7: dangling scope link -> rc 0, "scope-empty — nothing recorded"

        A directory entry named `<scope>` exists at the store root and nothing
        anywhere reported that its target was gone. That is this PR's headline
        defect, one state over, and the suite could not see it in EITHER
        direction — the mutant flipping it back survived all 333 tests.
        """
        if shape == "dangling":
            (source_store / "linked").symlink_to(tmp_path / "does-not-exist")
        else:
            (source_store / "linked").symlink_to(source_store / "linked")

        proc = run_cairn(
            "recall", "--scope", "linked", url=live_store.base,
            cache=tmp_path / "cache",
        )
        combined = proc.stdout + proc.stderr
        assert "scope-empty" not in combined, combined
        assert proc.returncode != 0, combined
        assert "linked/: broken-link refused" in combined, combined

    def test_a_symlinked_SCOPE_is_still_refused(
        self, source_store: Path, live_store, tmp_path: Path
    ):
        """Negative control for the reorder. Skipping non-scopes must not have
        also started skipping symlinked SCOPES — that was round 2's defect and
        this reorder is exactly the shape of change that could undo its fix."""
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "sneaky.md").write_text(_entry("sneaky", "linked", "- x."))
        (source_store / "linked").symlink_to(outside, target_is_directory=True)
        proc = run_cairn(
            "recall", "--scope", "linked", url=live_store.base,
            cache=tmp_path / "cache",
        )
        combined = proc.stdout + proc.stderr
        assert proc.returncode != 0, combined
        assert "scope-empty" not in combined, combined


class TestInodeBomb:
    def test_an_archive_with_too_MANY_members_is_refused(self, tmp_path: Path):
        """🔴 The byte ceiling bounds nothing here: `m.size` is 0 for an empty
        member. Measured against the previous commit — a 282,282-byte body
        carrying 60,000 zero-length `*.md` members wrote 60,000 files into the
        cache and reported `live … 60000 entries`, exit 0."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as tar:
            for i in range(60_000):
                tar.addfile(tarfile.TarInfo(f"widget-cfg/e{i:06d}.md"), io.BytesIO(b""))
        proc = TestHostileOrBrokenArchives()._run_against(
            buf.getvalue(), "application/gzip", tmp_path / "cache"
        )
        assert proc.returncode != 0, proc.stdout
        assert "REFUSED" in proc.stderr and "member" in proc.stderr, proc.stderr
        assert not list((tmp_path / "cache").glob("*/*.md"))

    def test_the_byte_ceiling_sums_rather_than_maxes(self, tmp_path: Path):
        """🔴 `sum` -> `max` SURVIVED the previous suite because the bomb fixture
        was a SINGLE member, which makes the two indistinguishable. Many
        moderate members is the case that tells them apart."""
        buf = io.BytesIO()
        chunk = b"\0" * (4 * 1024 * 1024)
        with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as tar:
            for i in range(80):  # 320 MB total, no single member over the cap
                info = tarfile.TarInfo(f"widget-cfg/big{i:03d}.md")
                info.size = len(chunk)
                tar.addfile(info, io.BytesIO(chunk))
        proc = TestHostileOrBrokenArchives()._run_against(
            buf.getvalue(), "application/gzip", tmp_path / "cache"
        )
        assert proc.returncode != 0, proc.stdout[:400]
        assert "ceiling" in proc.stderr, proc.stderr


class TestValidateScopeGuard:
    def test_validate_on_a_scope_the_cache_does_NOT_hold_is_nonzero(
        self, live_store, tmp_path: Path
    ):
        """🔴 The silent zero `cmd_validate` was rewritten to close, still open
        on the explicit `--scope` branch: it printed the writer's own "NOTHING
        WAS CHECKED — a zero here is NOT a clean bill of health" and exited 0.
        The fix covered the case it was looking at, not the predicate."""
        cache = tmp_path / "cache"
        assert run_cairn("sync", url=live_store.base, cache=cache).returncode == 0
        proc = run_cairn(
            "validate", "--scope", "no-such-scope", "--no-sync", url=None, cache=cache
        )
        assert proc.returncode != 0, proc.stdout + proc.stderr
        assert "no-such-scope" in proc.stderr, proc.stderr


class TestSearchOverTheClient:
    """🔴 `cairn search` had ZERO tests anywhere in the repo, so the fix that
    gave search its own `_exit_for` label had no regression test at all."""

    def test_search_finds_a_hunk_and_exits_0(self, live_store, tmp_path: Path):
        proc = run_cairn(
            "search", "lease", "--scope", "widget-cfg", url=live_store.base,
            cache=tmp_path / "cache",
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "thing-beta" in proc.stdout, proc.stdout

    def test_search_on_an_all_malformed_scope_names_the_SCOPE_not_the_query(
        self, source_store: Path, live_store, tmp_path: Path
    ):
        """🔴 `_exit_for` was handed the QUERY where the reader passes
        `SearchReport.label`, so the failure sentence read "`lease` holds 1
        entry file" — naming the search term as if it were a scope path."""
        broken = source_store / "rubble-pile"
        broken.mkdir()
        (broken / "junk.md").write_text("no front matter, nothing parseable\n")
        proc = run_cairn(
            "search", "lease", "--scope", "rubble-pile", url=live_store.base,
            cache=tmp_path / "cache",
        )
        combined = proc.stdout + proc.stderr
        assert "`lease`" not in combined, combined
        assert "rubble-pile" in combined, combined

    def test_the_search_label_is_the_READERS_label_not_a_lookalike(
        self, source_store: Path, live_store, tmp_path: Path
    ):
        """🔴 The previous version of the test above pinned only "the query is
        not named", which `label = f"{report.scope}/"` also satisfies — so that
        mutant SURVIVED all 333 tests. `SearchReport.label` is a PROPERTY over
        `scopes_searched`, and for `--all-scopes` it differs from any single
        scope, which is the case that tells the two apart.
        """
        # 🔴 EVERY scope must be unreadable, because the label only reaches the
        # output through `_exit_for`'s FAILURE sentence — a successful search
        # prints no label at all, so an all-scopes search that finds anything
        # cannot discriminate. This is the fixture the property actually needs.
        for existing in source_store.glob("*/*.md"):
            existing.unlink()
        for scope in ("widget-cfg", "gizmo-notes"):
            (source_store / scope / "junk.md").write_text("nothing parseable\n")
        broken = source_store / "rubble-pile"
        broken.mkdir()
        (broken / "junk.md").write_text("no front matter, nothing parseable\n")
        # 🔴 `--all-scopes` IS THE DISCRIMINATING CASE, and the previous version
        # of this test named it in its docstring and then did not use it:
        #     all_scopes=False -> report.label == f"{report.scope}/"  IDENTICAL
        #     all_scopes=True  -> "gizmo-notes/, hollow-area/, …" vs "(all scopes)/"
        # so the fixture could only ever produce the lookalike's own value, and
        # the `report.label -> f"{report.scope}/"` mutant passed 349/349. A
        # fixture that cannot distinguish the mutant from the fix is not a test.
        cache = tmp_path / "cache"
        proc = run_cairn(
            "search", "lease", "--scope", "rubble-pile", "--all-scopes",
            url=live_store.base, cache=cache,
        )
        # 🔴 EXPECTATION DERIVED FROM WHAT THE CLI ACTUALLY READ — the CACHE, not
        # the source. An EMPTY scope directory never ships in the tar (only
        # `*.md` members do), so `hollow-area` exists in the source and not in
        # the cache, and a label computed from the source names a scope the CLI
        # could not have searched. Comparing against the wrong store is how a
        # correct implementation fails a test.
        api = _load_api()
        report = api.rc.search(
            str(cache), "rubble-pile", "lease",
            context=api.rc.CONTEXT_BULLET,
            threshold=api.rc.DEFAULT_SEARCH_THRESHOLD,
            max_hits=api.rc.DEFAULT_MAX_HITS,
            all_scopes=True,
        )
        assert report.label != f"{report.scope}/", (
            "fixture bug: label and the lookalike are identical here, so this "
            "cannot see the mutant"
        )
        # 🔴 STDERR ONLY. `render_search`'s caveat ALSO prints the label to
        # stdout, so asserting over `stdout + stderr` passes whatever
        # `_exit_for` was handed — measured: the `label -> f"{scope}/"` mutant
        # survived that version of this test. The one-line sentence on stderr is
        # the only output `_exit_for` produces, so it is the only place that
        # discriminates. Third time this file has had to move an assertion off a
        # string that two different code paths can produce.
        assert report.label in proc.stderr, (report.label, proc.stderr)


class TestHeaderCaseInsensitivity:
    """🔴 THE DEFECT THAT SHIPPED. Found by the first live call after deploy,
    not by 358 tests and seven audit rounds.

    `dict(resp.headers)` discards the case-insensitivity of
    `email.message.Message`. HTTP/2 — which Cloudflare speaks — lowercases every
    header name, so in production `headers.get("X-Store-Entries")` returned
    None while the wire carried `x-store-entries: 75`. Measured against the live
    pod. Three things went silently wrong, and the third is the serious one:

        snapshot=UNSTAMPED      the provenance stamp the whole design rests on
        revision=unknown        on every cached banner
        the COUNT CROSS-CHECK NEVER FIRED — the guard against a truncated
        transfer was inert in the only environment that matters

    BOTH test servers (`_CloudflareShim` and the one-shot `_serve_once`) now
    lowercase, so every header-dependent test exercises the production shape. These pin the property directly, so a future refactor back
    to `dict(...)` fails here rather than in six months on a live call.
    """

    def test_the_stamp_records_the_servers_headers_not_placeholders(
        self, live_store, tmp_path: Path
    ):
        cache = tmp_path / "cache"
        assert run_cairn("sync", url=live_store.base, cache=cache).returncode == 0
        stamp = (cache / ".sync-stamp").read_text()
        # The freshness stamp is the header that was silently lost. It must now
        # carry the server's own value, not the placeholder.
        assert "snapshot=UNSTAMPED" not in stamp, stamp
        assert "snapshot=seeded=" in stamp, stamp
        assert "entries=" in stamp, stamp
        # ⚠ `revision=unknown` is CORRECT, and it is DEAD OUTPUT — pinned here
        # so that is on the record rather than mistaken for information.
        # `/snapshot` sets `X-Store-Revision` on no path, and cairn always
        # fetches unscoped (deliberately — a scope-filtered cache is the
        # silent-zero this client exists to prevent), so this field can never
        # carry a value. My first version of this test asserted the opposite and
        # failed: the assertion was wrong, not the code.
        # CLOSING CONDITION for making it live: `_snapshot` sets
        # `scope_revision(root, scope)` when `?scope=` is present — a revision
        # IS well-defined there — AND some caller uses a scoped fetch. Neither
        # is true today, so the honest move is to say the field is inert rather
        # than leave a reader inferring freshness from "unknown".
        assert "revision=unknown" in stamp, stamp


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
        proc = run_cairn("sync", url=live_store.base, cache=tmp_path / "cache",
                         token="z" * 48)
        assert proc.returncode != 0
        assert "401" in proc.stderr, proc.stdout
