#!/usr/bin/env python3
"""`cairn append` / `cairn put` — the write verbs criterion 9 routes through.

🔴 WHAT THIS FILE IS ACTUALLY GUARDING: THAT A FAILED WRITE IS DISTINGUISHABLE
FROM A SUCCESSFUL ONE, AND FROM A STALE READ. The read half of this CLI is built
to degrade — an unreachable pod is served from cache at exit 0, because a stale
answer beats no answer. The write half must do the exact opposite: there is no
cache to write into, no spool, and no such thing as a stale write, so an
unreachable pod is a refusal at a distinct non-zero code. If those two behaviours
ever converge, a caller reads "the store was unreachable, here is the cache" as
"your bullet landed" and the record is silently lost — which is the precise
failure the whole cutover exists to close, arriving through the tool built to
close it.

Every test here therefore pins a CODE and a SENTENCE, not merely "non-zero".
"""

from __future__ import annotations

import http.server
import importlib.util
import ipaddress
import json
import os
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
CAIRN_CLI = REPO / "scripts" / "cairn"
SERVER_PY = REPO / "scripts" / "subsystem-store-api" / "server.py"
GOOD_TOKEN = "w" * 20 + "R" * 20 + "t" * 8
LOOPBACK = ipaddress.ip_network("127.0.0.1/32")
SESSION = "test-session-01"


def _load_api():
    """Import `server.py` by path. `sys.modules[...]` BEFORE `exec_module`.

    Without that line the first `@dataclass` in the file raises
    `AttributeError: 'NoneType' object has no attribute '__dict__'` under
    `from __future__ import annotations` — see `test_cairn_cli._load_api` for
    the full mechanism. Restated as a one-liner rather than re-derived.
    """
    sys.path.insert(0, str(REPO / "scripts" / "lib"))
    spec = importlib.util.spec_from_file_location("srv_w", SERVER_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _entry(service: str, scope: str, *nuance: str) -> str:
    return "\n".join([
        "---",
        f"service: {service}",
        f"scope: {scope}",
        "sensitivity: internal",
        "---",
        "",
        "## What it is",
        f"The {service} thing.",
        "",
        "## Pointers",
        f"- `{scope}: src/{service}.py`",
        "",
        "## Nuance / work-history",
        *nuance,
        "",
    ])


@pytest.fixture
def store(tmp_path: Path) -> Path:
    root = tmp_path / "store"
    (root / "widget-cfg").mkdir(parents=True)
    (root / "widget-cfg" / "thing-alpha.md").write_text(
        _entry("thing-alpha", "widget-cfg", "- 2026-01-02: the probe lies for 40s.")
    )
    (root / "widget-cfg" / "thing-beta.md").write_text(
        _entry("thing-beta", "widget-cfg", "- 2026-01-03: the sidecar drops its lease.")
    )
    return root


class _Shim(http.server.BaseHTTPRequestHandler):
    """Forwards every verb upstream, adding the header the real edge adds.

    🔴 THE SHIM EXISTS BECAUSE THE SERVER FAILS CLOSED WITHOUT `CF-Connecting-IP`
    and the client must never send one — in production Cloudflare sets it, and a
    client that sent its own would produce a duplicate and be refused. Teaching
    the client to send it to make a test pass would be testing a client we must
    not ship. It also records every forwarded request so a test can assert on the
    headers the CLIENT sent, which is the only way to see the User-Agent.
    """

    upstream = ""
    seen: list = []

    def _proxy(self, verb: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        type(self).seen.append({
            "verb": verb,
            "path": self.path,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": body,
        })
        req = urllib.request.Request(self.upstream + self.path, data=body, method=verb)
        for key, value in self.headers.items():
            if key.lower() not in ("host", "cf-connecting-ip", "content-length"):
                req.add_header(key, value)
        req.add_header("CF-Connecting-IP", "203.0.113.9")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                out, code, headers = resp.read(), resp.status, dict(resp.headers)
        except urllib.error.HTTPError as exc:
            out, code, headers = exc.read(), exc.code, dict(exc.headers)
        self.send_response(code)
        for key, value in headers.items():
            if key.lower() not in ("transfer-encoding", "connection", "content-length"):
                self.send_header(key.lower(), value)
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_GET(self):  # noqa: N802
        self._proxy("GET")

    def do_POST(self):  # noqa: N802
        self._proxy("POST")

    def do_PUT(self):  # noqa: N802
        self._proxy("PUT")

    def log_message(self, *_args):  # keep pytest output readable
        return


@pytest.fixture
def live(store: Path):
    api = _load_api()
    record = api.TokenRecord(token=GOOD_TOKEN, identity="tester", scopes=("widget-cfg",))
    httpd = api.build_server(
        host="127.0.0.1", port=0, store_root=str(store), tokens=(record,),
        trusted_proxies=(LOOPBACK,), limiter=None, audit=None,
    )
    upstream = f"http://127.0.0.1:{httpd.server_address[1]}"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    handler = type("S", (_Shim,), {"upstream": upstream, "seen": []})
    shim = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=shim.serve_forever, daemon=True).start()
    try:
        yield SimpleNamespace(
            base=f"http://127.0.0.1:{shim.server_address[1]}",
            store=store, api=api, handler=handler,
        )
    finally:
        shim.shutdown()
        shim.server_close()
        httpd.shutdown()
        httpd.server_close()


def _dead_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _cairn_ast() -> "ast.Module":
    import ast as _ast

    return _ast.parse(CAIRN_CLI.read_text())


def _module_constants() -> dict[str, int]:
    """Module-level `NAME = <int>` from `cairn`, read by AST.

    🔴 NOT `exec`, AND NOT A REGEX. `exec`ing the file (even a prefix of it)
    fails on `__file__`, which is absent from a synthetic namespace — measured,
    it raised `NameError` at `Path(__file__)`. A regex over the source would
    silently miss a re-spelling. The AST answers the question the test is
    actually asking: what integer does this module bind to this name?
    """
    import ast as _ast

    out: dict[str, int] = {}
    for node in _cairn_ast().body:
        if isinstance(node, _ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, _ast.Name) and isinstance(node.value, _ast.Constant):
                if isinstance(node.value.value, int):
                    out[target.id] = node.value.value
    return out


def _write_status_table() -> dict[int, int]:
    """`_WRITE_STATUS_EXITS` as a real dict, read by AST for the same reason."""
    import ast as _ast

    for node in _cairn_ast().body:
        targets = getattr(node, "targets", []) or (
            [node.target] if isinstance(node, _ast.AnnAssign) else []
        )
        for target in targets:
            if isinstance(target, _ast.Name) and target.id == "_WRITE_STATUS_EXITS":
                return {
                    k.value: v.id if isinstance(v, _ast.Name) else v.value
                    for k, v in zip(node.value.keys, node.value.values)
                }
    raise AssertionError("`_WRITE_STATUS_EXITS` is gone from scripts/cairn")


def run_cairn(*args: str, url: str | None, cache: Path, token: str = GOOD_TOKEN):
    env = dict(os.environ)
    env["SUBSYSTEM_STORE_TOKEN"] = token
    # A path that does not exist, so the developer's real credentials can never
    # make a test pass. A test that reads live credentials is not a test.
    env["SUBSYSTEM_STORE_CONFIG"] = str(cache.parent / "no-such-config")
    env["SUBSYSTEM_STORE_URL"] = url or f"http://127.0.0.1:{_dead_port()}"
    return subprocess.run(
        [sys.executable, str(CAIRN_CLI), "--cache", str(cache), "--timeout", "5", *args],
        capture_output=True, text=True, env=env, timeout=120,
    )


# =============================================================================


class TestAppendLands:
    def test_a_bullet_is_appended_and_the_status_is_named(self, live, tmp_path):
        proc = run_cairn(
            "append", "--scope", "widget-cfg", "--ref", "thing-alpha",
            "--text", "the lease renewal races the probe",
            "--session", SESSION,
            url=live.base, cache=tmp_path / "c",
        )
        assert proc.returncode == 0, proc.stderr
        assert "appended" in proc.stdout, proc.stdout
        # 🔴 THE POSITIVE CONTROL, and it is not optional. `RULES.md` on this
        # exact API: "reading '0 occurrences' off the response is a FALSE GREEN —
        # nothing was written. Every write probe needs a positive control proving
        # the bullet landed." So the assertion is on the FILE, not the exit code.
        landed = (live.store / "widget-cfg" / "thing-alpha.md").read_text()
        assert "the lease renewal races the probe" in landed
        assert "[cairn: tester/" in landed, (
            "the bullet landed without the attribution trailer the actor guarantee "
            "is about"
        )

    def test_the_ACTOR_comes_from_the_token_and_the_body_cannot_set_it(self, live, tmp_path):
        """A forged `actor` must not reach the file — and this CLI has no flag for it.

        Two independent claims, both needed: the server discards a body `actor`
        (its guarantee), and this client offers no way to send one (ours). The
        second is what stops a caller believing they control attribution.
        """
        proc = run_cairn(
            "append", "--scope", "widget-cfg", "--ref", "thing-alpha",
            "--text", "attribution comes from the credential",
            "--session", SESSION, url=live.base, cache=tmp_path / "c",
        )
        assert proc.returncode == 0, proc.stderr
        landed = (live.store / "widget-cfg" / "thing-alpha.md").read_text()
        assert "[cairn: tester/" in landed
        help_text = subprocess.run(
            [sys.executable, str(CAIRN_CLI), "append", "--help"],
            capture_output=True, text=True, timeout=60,
        ).stdout
        assert "--actor" not in help_text

    def test_a_REPEAT_of_the_same_text_reports_duplicate_not_appended(self, live, tmp_path):
        """Idempotence is what makes a retry after a timeout safe — and it must SAY so.

        The server recognises a bullet by content hash. A client that printed
        `appended` either way would make a re-POST look like a second record;
        one that printed nothing would let a caller believe a genuinely new
        bullet landed when the store had recognised and dropped it.
        """
        args = ("append", "--scope", "widget-cfg", "--ref", "thing-beta",
                "--text", "the same observation twice", "--session", SESSION)
        first = run_cairn(*args, url=live.base, cache=tmp_path / "c")
        second = run_cairn(*args, url=live.base, cache=tmp_path / "c")
        assert first.returncode == 0 and second.returncode == 0
        assert "appended" in first.stdout and "duplicate" not in first.stdout
        assert "duplicate" in second.stdout
        body = (live.store / "widget-cfg" / "thing-beta.md").read_text()
        assert body.count("the same observation twice") == 1

    def test_an_ALIAS_resolves_as_well_as_a_filename(self, live, tmp_path):
        (live.store / "widget-cfg" / "thing-gamma.md").write_text("\n".join([
            "---", "service: thing-gamma", "scope: widget-cfg",
            "sensitivity: internal", "aliases: [gamma-box]", "---", "",
            "## What it is", "g", "", "## Pointers", "- `widget-cfg: g.py`", "",
            "## Nuance / work-history", "- 2026-01-04: g.", "",
        ]))
        proc = run_cairn(
            "append", "--scope", "widget-cfg", "--ref", "gamma-box",
            "--text", "reached through the alias tier", "--session", SESSION,
            url=live.base, cache=tmp_path / "c",
        )
        assert proc.returncode == 0, proc.stderr
        assert "reached through the alias tier" in (
            live.store / "widget-cfg" / "thing-gamma.md"
        ).read_text()


class TestAWriteNeverDegrades:
    def test_an_unreachable_store_REFUSES_at_7_and_says_nothing_was_queued(self, tmp_path):
        """🔴 THE CENTRAL CONTRACT. Not exit 0, not exit 3, and the message says why.

        Exit 3 is the READ verdict "store-unreachable, no cache" — a statement
        about what was displayed. Reusing it here would let a caller read
        "nothing was shown" as "the bullet landed", because on the read path 3 is
        routinely accompanied by a served cache and a zero.
        """
        proc = run_cairn(
            "append", "--scope", "widget-cfg", "--ref", "thing-alpha",
            "--text", "this must not be queued", "--session", SESSION,
            url=None, cache=tmp_path / "c",
        )
        assert proc.returncode == 7, (proc.returncode, proc.stderr)
        assert "did NOT happen" in proc.stderr
        assert "Nothing was queued" in proc.stderr

    def test_an_unreachable_store_writes_NOTHING_to_the_cache(self, tmp_path):
        """A spool would be invisible and would look exactly like success later."""
        cache = tmp_path / "c"
        cache.mkdir()
        before = sorted(p.name for p in cache.rglob("*"))
        run_cairn(
            "append", "--scope", "widget-cfg", "--ref", "thing-alpha",
            "--text", "no spool", "--session", SESSION, url=None, cache=cache,
        )
        assert sorted(p.name for p in cache.rglob("*")) == before

    def test_a_READ_still_degrades_to_the_cache_while_the_write_refuses(self, live, tmp_path):
        """The asymmetry itself, measured at both points rather than asserted.

        🔴 A ONE-SIDED TEST WOULD NOT SEE THIS. Asserting only that the write
        refuses is consistent with a client where BOTH halves refuse, which would
        break `/resume` offline — the thing the design promises keeps working.
        So the same cache is exercised for a read (must serve, exit 0) and a
        write (must refuse, exit 7) against the same dead host.
        """
        cache = tmp_path / "c"
        primed = run_cairn("sync", url=live.base, cache=cache)
        assert primed.returncode == 0, primed.stderr
        read = run_cairn("recall", "--scope", "widget-cfg", url=None, cache=cache)
        assert read.returncode == 0, read.stderr
        assert "SERVED FROM CACHE" in read.stdout
        write = run_cairn(
            "append", "--scope", "widget-cfg", "--ref", "thing-alpha",
            "--text", "still refused", "--session", SESSION, url=None, cache=cache,
        )
        assert write.returncode == 7, write.stderr


class TestRefusalsAreDistinguishable:
    @pytest.mark.parametrize(
        "args, code, needle",
        [
            # A multi-line bullet would either be attached to this bullet as a
            # continuation or start a second, UNATTRIBUTED one.
            (("--ref", "thing-alpha", "--text", "line one\nline two"), 6, "ONE line"),
            # A leading `- ` would start a bullet the server did not render, so
            # the attribution trailer would be on the wrong one.
            (("--ref", "thing-alpha", "--text", "- already a bullet"), 6, "markdown bullet"),
            # An empty bullet: refused before anything is resolved.
            (("--ref", "thing-alpha", "--text", "   "), 6, "non-empty string"),
        ],
    )
    def test_each_refusal_carries_its_own_code_and_the_servers_own_sentence(
        self, live, tmp_path, args, code, needle
    ):
        base = ["append", "--session", SESSION, "--scope", "widget-cfg"]
        proc = run_cairn(*base, *args, url=live.base, cache=tmp_path / "c")
        assert proc.returncode == code, (proc.returncode, proc.stdout, proc.stderr)
        assert needle in proc.stderr, proc.stderr

    def test_a_REFUSED_scope_and_an_ABSENT_one_are_INDISTINGUISHABLE_to_the_client(
        self, live, tmp_path
    ):
        """🔴 THIS ASSERTS A SAMENESS, AND THE SAMENESS IS THE SECURITY PROPERTY.

        `server._not_found` answers the SAME bytes and the SAME
        `X-Store-Status: not-found` for a scope outside the credential's
        allowlist, a scope that never existed, a ref that resolves to nothing,
        and an entry the loader could not parse — criterion 3's enumeration
        property applied to the write verbs. A client that reported them
        differently would rebuild the enumeration API the server took apart, out
        of information the server deliberately withheld.

        So the test is written the way the property is: three inputs from three
        different causes, one identical observable. It fails if a future client
        (or server) starts leaking WHICH cause it was — which is what a naive
        "surface the audit status to the user" change would do.
        """
        outs = []
        for scope, ref in (
            ("widget-cfg", "no-such-entry-here"),   # ref that resolves to nothing
            ("scope-that-never-existed", "thing-alpha"),
            ("hidden-scope", "thing-alpha"),        # outside the allowlist
        ):
            proc = run_cairn(
                "append", "--scope", scope, "--ref", ref, "--text", "x",
                "--session", SESSION, url=live.base, cache=tmp_path / "c",
            )
            assert proc.returncode == 6, (scope, ref, proc.returncode, proc.stderr)
            outs.append(proc.stderr)
        assert "not-found" in outs[0]
        assert len(set(outs)) == 1, (
            f"the three causes produced {len(set(outs))} different messages; the "
            f"server answers all of them identically and the client must not "
            f"re-derive the difference: {outs}"
        )

    def test_a_READ_ONLY_image_is_reported_as_an_OPERATOR_problem_not_a_caller_one(
        self, tmp_path
    ):
        """405 `read-only` is what a pod with no write path answers.

        🔴 THIS IS THE ONE THAT READS LIKE A WRONG URL. Phase 3's own retraction
        turned on it: `POST`/`PUT` both answered `405 read-only` on the deployed
        image while `main` had the write path, and the doc concluded the writes
        were "403 on the live pod". The client must print the server's
        `X-Store-Status` so the difference is on screen.
        """
        class ReadOnly(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = b"method not allowed: this store is read-only\n"
                self.send_response(405)
                self.send_header("x-store-status", "read-only")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_a):
                return

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ReadOnly)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            proc = run_cairn(
                "append", "--scope", "widget-cfg", "--ref", "thing-alpha",
                "--text", "x", "--session", SESSION,
                url=f"http://127.0.0.1:{srv.server_address[1]}", cache=tmp_path / "c",
            )
        finally:
            srv.shutdown()
            srv.server_close()
        assert proc.returncode == 6, (proc.returncode, proc.stderr)
        assert "read-only" in proc.stderr
        assert "REFUSED" in proc.stderr

    def test_a_412_gets_its_OWN_code_so_a_blind_retry_is_not_the_remedy(self, tmp_path):
        """A precondition failure is neither "fix your request" nor "retry"."""
        class Stale(http.server.BaseHTTPRequestHandler):
            def do_PUT(self):  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                body = b"precondition failed: the entry has revision deadbeefdeadbeef\n"
                self.send_response(412)
                self.send_header("x-store-status", "revision-mismatch")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_a):
                return

        target = tmp_path / "new.md"
        target.write_text("---\nservice: x\nscope: widget-cfg\n---\n")
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Stale)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            proc = run_cairn(
                "put", "--scope", "widget-cfg", "--ref", "thing-alpha",
                "--file", str(target), "--if-match", "0123456789abcdef",
                url=f"http://127.0.0.1:{srv.server_address[1]}", cache=tmp_path / "c",
            )
        finally:
            srv.shutdown()
            srv.server_close()
        assert proc.returncode == 8, (proc.returncode, proc.stderr)
        assert "revision-mismatch" in proc.stderr


class TestPutReplacesBehindAPrecondition:
    def test_put_derives_the_revision_from_a_LIVE_sync_and_the_replace_lands(
        self, live, tmp_path
    ):
        replacement = tmp_path / "replacement.md"
        replacement.write_text(_entry(
            "thing-beta", "widget-cfg",
            "- 2026-01-03: RESOLVED abc1234: the sidecar drops its lease.",
        ))
        proc = run_cairn(
            "put", "--scope", "widget-cfg", "--ref", "thing-beta",
            "--file", str(replacement), url=live.base, cache=tmp_path / "c",
        )
        assert proc.returncode == 0, (proc.stdout, proc.stderr)
        assert "derived If-Match" in proc.stderr
        on_disk = (live.store / "widget-cfg" / "thing-beta.md").read_text()
        assert "RESOLVED abc1234" in on_disk

    def test_put_REFUSES_to_derive_a_revision_it_could_not_refresh(self, live, tmp_path):
        """A precondition from bytes we could not confirm is the one case where
        the cache is worse than nothing: it turns the operator's edit into a
        guaranteed 412 while looking like a normal run."""
        cache = tmp_path / "c"
        assert run_cairn("sync", url=live.base, cache=cache).returncode == 0
        replacement = tmp_path / "r.md"
        replacement.write_text(_entry("thing-beta", "widget-cfg", "- 2026-01-03: x."))
        proc = run_cairn(
            "put", "--scope", "widget-cfg", "--ref", "thing-beta",
            "--file", str(replacement), url=None, cache=cache,
        )
        assert proc.returncode != 0
        assert "refusing to PUT" in proc.stderr


class TestTheRequestItself:
    def test_every_write_request_carries_the_User_Agent_the_edge_requires(
        self, live, tmp_path
    ):
        """🔴 MEASURED IN PRODUCTION, NOT DEFENSIVE. urllib's default UA is 403'd
        by the edge in front of this host, and the 403 arrives looking like a bad
        token and like the store being down. The read path learned this once; the
        write path must not have to learn it again, which is why the header lives
        in one helper and why this test reads what the CLIENT actually sent."""
        live.handler.seen.clear()
        run_cairn(
            "append", "--scope", "widget-cfg", "--ref", "thing-alpha",
            "--text", "header check", "--session", SESSION,
            url=live.base, cache=tmp_path / "c",
        )
        writes = [r for r in live.handler.seen if r["verb"] == "POST"]
        assert writes, "no POST reached the shim"
        for req in writes:
            assert req["headers"].get("user-agent") == "subsystem-store-client/1"
            assert req["headers"].get("authorization", "").startswith("Bearer ")

    def test_the_body_carries_text_and_session_and_NO_actor(self, live, tmp_path):
        live.handler.seen.clear()
        run_cairn(
            "append", "--scope", "widget-cfg", "--ref", "thing-alpha",
            "--text", "body shape", "--session", SESSION,
            url=live.base, cache=tmp_path / "c",
        )
        posts = [r for r in live.handler.seen if r["verb"] == "POST"]
        payload = json.loads(posts[0]["body"])
        assert payload == {"text": "body shape", "session": SESSION}

    def test_session_is_REQUIRED_with_no_default_and_no_env_fallback(self, live, tmp_path):
        """A default would attach a value nobody chose to a durable record, and
        an env fallback would attribute one agent's bullet to whatever the shell
        last exported. The server refuses a missing session with a 400; this
        refuses it before a request is built."""
        env_probe = subprocess.run(
            [sys.executable, str(CAIRN_CLI), "append", "--scope", "widget-cfg",
             "--ref", "thing-alpha", "--text", "x"],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "CLAUDE_SESSION_ID": "leaked-from-the-env",
                 "CAIRN_SESSION": "also-leaked"},
        )
        assert env_probe.returncode == 2
        assert "--session" in env_probe.stderr


class TestExitCodesDoNotOverlap:
    def test_the_write_codes_are_disjoint_from_every_read_code(self):
        """Pinned as a SET, not as four separate assertions, because the hazard
        is a future verb reusing a number rather than any one of them being
        wrong today."""
        ns = _module_constants()
        reads = {ns["EXIT_OK"], ns["EXIT_UNREACHABLE_NO_CACHE"], ns["EXIT_USAGE"],
                 ns["EXIT_REFRESH_FAILED"], ns["EXIT_CORRUPT"]}
        writes = {ns["EXIT_WRITE_REFUSED"], ns["EXIT_WRITE_UNREACHABLE"],
                  ns["EXIT_WRITE_PRECONDITION"]}
        assert len(writes) == 3, writes
        assert reads & writes == set(), (
            f"a write code collides with a read code: {reads & writes}. A caller "
            f"cannot then tell a refused write from a served-from-cache read."
        )

    def test_every_write_status_the_server_can_emit_is_MAPPED(self):
        """🔴 AN UNMAPPED STATUS MUST NOT BECOME A PASS. The table is walked
        against the codes `server.py` actually responds with on a write route, so
        adding a refusal there without mapping it here fails HERE rather than in
        production as a zero exit over a write that never landed."""
        table = _write_status_table()
        server_src = SERVER_PY.read_text()
        # The statuses the two write handlers respond with, read off the source
        # rather than restated: `self._respond(<code>,` inside the write half.
        write_half = server_src.split("def _append_bullet", 1)[1]
        import re as _re
        emitted = {int(m) for m in _re.findall(r"self\._respond\(\s*(\d{3})", write_half)}
        emitted.discard(200)
        missing = sorted(emitted - set(table))
        assert not missing, (
            f"server.py's write handlers can answer {missing}, which "
            f"`_WRITE_STATUS_EXITS` does not map. An unmapped code falls through "
            f"to 'unrecognised', which is loud — but a deliberate status deserves "
            f"a deliberate exit code."
        )
