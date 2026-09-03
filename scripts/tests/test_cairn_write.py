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
import importlib.machinery
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
sys.path.insert(0, str(REPO / "scripts"))
from testlib import hang_mechanism, store_siting  # noqa: E402
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
def store(tmp_path: Path):
    # 🔴 The root comes from `testlib.store_siting`, not from `tmp_path` directly:
    # this file stands up the real store server, so its writes fsync INSIDE the
    # request and a contended disk fails the gate on PRs that cannot reach this
    # test. That is not hypothetical here — `TestAppendLands` below is the test
    # that went red in CI on a docs-only PR. Falls back to `tmp_path` wherever no
    # tmpfs is usable, so it can never be worse than the original.
    with store_siting.store_root(tmp_path) as root:
        yield _populate_store(root)


def _populate_store(root: Path) -> Path:
    (root / "widget-cfg").mkdir(parents=True)
    (root / "widget-cfg" / "thing-alpha.md").write_text(
        _entry("thing-alpha", "widget-cfg", "- 2026-01-02: the probe lies for 40s.")
    )
    (root / "widget-cfg" / "thing-beta.md").write_text(
        _entry("thing-beta", "widget-cfg", "- 2026-01-03: the sidecar drops its lease.")
    )
    # 🔴 A SECOND SCOPE THAT EXISTS ON DISK AND IS **NOT** IN THE TOKEN'S
    # ALLOWLIST, AND IT IS LOAD-BEARING. The indistinguishability test below
    # claims to exercise a REFUSED scope beside an ABSENT one; without this
    # directory both of its "refused" cases were merely absent, so they took the
    # same branch whether or not `visible_scopes` filtering existed at all —
    # a sameness that held with the guard deleted. `hidden-scope` is refused by
    # the ALLOWLIST here, which is the case that was never being reached.
    (root / "hidden-scope").mkdir(parents=True)
    (root / "hidden-scope" / "secret-thing.md").write_text(
        _entry("secret-thing", "hidden-scope", "- 2026-01-04: not yours to see.")
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


def why_the_write_failed(proc: subprocess.CompletedProcess, live=None) -> str:
    """The assertion message for a write that was supposed to land.

    🔴 EXIT 7 HERE HAS TWO CAUSES AND THE BARE ASSERTION NAMES NEITHER. `cairn`
    passes `--timeout 5`, and `server.py:_replace_bytes` fsyncs the file and then
    the parent directory INSIDE the request, before the response is written. So a
    single fsync slower than five seconds makes the client report the store
    UNREACHABLE, and the gate prints

        AssertionError: cairn: the write did NOT happen — ... unreachable: timed out
        assert 7 == 0

    for an I/O stall — a sentence about this client's code, describing the disk.
    Measured in CI on `devrc-ci-jfg67` (2026-09-02) and reproduced on the dev host
    by stalling `os.fsync` past the bound. A gate that reports a code failure for an
    I/O stall trains everyone to click through, which is the actual cost.

    So the message carries the two facts that separate the causes: whether any
    server thread is parked in an unbounded wait RIGHT NOW
    (`testlib.hang_mechanism`), and which filesystem the store was sited on — the
    standing precondition, since `testlib.store_siting` falls back to disk in five
    documented ways and says nothing when it does.

    🔴 This is DIAGNOSIS, NOT TOLERANCE. It changes no bound, retries nothing, and
    the assertion fails exactly as it did before; only the message is wider. It is
    also NOT a proof of an I/O stall: a stall that had already cleared reads the
    same as a genuine code failure, and `hang_mechanism.report` says
    `NO_LEDGERED_STALL` for both rather than guessing between them.
    """
    note = ""
    if live is not None:
        note = (
            f"\nstore={live.store} fs={store_siting.mount_fstype(live.store)} "
            f"(tmpfs = the contended-disk mitigation was in force)"
        )
    return (
        f"exit={proc.returncode}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
        + hang_mechanism.report(note)
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
        assert proc.returncode == 0, why_the_write_failed(proc, live)
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
        assert proc.returncode == 0, why_the_write_failed(proc, live)
        on_disk = (live.store / "widget-cfg" / "thing-alpha.md").read_text()
        assert "[cairn: tester/" in on_disk
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
        assert first.returncode == 0, why_the_write_failed(first, live)
        assert second.returncode == 0, why_the_write_failed(second, live)
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
        assert proc.returncode == 0, why_the_write_failed(proc, live)
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

        🔴 THE THIRD CASE ONLY BECAME REAL WHEN THE FIXTURE GREW A `hidden-scope`
        DIRECTORY. Before that it named a scope that simply did not exist, so all
        three inputs took the same "absent" branch and the assertion held whether
        or not `visible_scopes` filtering existed — a sameness that survives
        deleting the guard it claims to pin. `hidden-scope` is now ON DISK and
        excluded by the token's allowlist, which is the case the property is
        about, and its ENTRY is named so the ref would resolve if the scope were
        visible.
        """
        # POSITIVE CONTROL ON THE FIXTURE ITSELF: the refused scope must actually
        # be THERE. If this file is ever removed, the third case silently becomes
        # a fourth copy of the second and the test goes back to proving nothing.
        assert (live.store / "hidden-scope" / "secret-thing.md").is_file(), (
            "the allowlist-refused case is not on disk, so it is merely ABSENT — "
            "the sameness below would hold with `visible_scopes` deleted"
        )
        outs = []
        for scope, ref in (
            ("widget-cfg", "no-such-entry-here"),   # ref that resolves to nothing
            ("scope-that-never-existed", "thing-alpha"),
            ("hidden-scope", "secret-thing"),       # EXISTS on disk, allowlist-refused
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

    @pytest.mark.parametrize(
        "code, status, want",
        [
            # 🔴 THE MAPPING'S SEMANTICS, NOT ITS MEMBERSHIP. The table test
            # asserts every status server.py can emit is PRESENT in
            # `_WRITE_STATUS_EXITS`; it is blind to a row pointing at the wrong
            # code. A sweep proved it: `500: EXIT_WRITE_REFUSED` SURVIVED a green
            # suite. These drive the real client against a real socket.
            (500, "internal-error", 7),   # `_backstop` — retry IS the remedy
            (503, "store-unreachable", 7),
            (429, "rate-limited", 7),
            (422, "entry-shape", 6),      # the entry has no nuance heading
            (400, "bad-request", 6),
            (404, "not-found", 6),
        ],
    )
    def test_each_status_maps_to_the_code_whose_REMEDY_is_right(
        self, tmp_path, code, status, want
    ):
        """6 means "change your request"; 7 means "try again". A 500 answered as
        6 tells the caller to change a byte-identical request that would very
        likely have succeeded — and a 400 answered as 7 invites an infinite
        retry of a bullet that can never be accepted."""
        class Fixed(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                body = b"fixed\n"
                self.send_response(code)
                self.send_header("x-store-status", status)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_a):
                return

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fixed)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            proc = run_cairn(
                "append", "--scope", "widget-cfg", "--ref", "thing-alpha",
                "--text", "x", "--session", SESSION,
                url=f"http://127.0.0.1:{srv.server_address[1]}", cache=tmp_path / "c",
            )
        finally:
            srv.shutdown(); srv.server_close()
        assert proc.returncode == want, (
            f"HTTP {code} [{status}] exited {proc.returncode}, wanted {want}. "
            f"6 and 7 carry opposite remedies."
        )
        assert status in proc.stderr

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
        assert proc.returncode == 0, why_the_write_failed(proc, live)
        assert "derived If-Match" in proc.stderr
        on_disk = (live.store / "widget-cfg" / "thing-beta.md").read_text()
        assert "RESOLVED abc1234" in on_disk

    @pytest.mark.parametrize("prime_the_cache", [True, False])
    def test_put_REFUSES_at_7_whether_or_not_a_CACHE_EXISTS(
        self, live, tmp_path, prime_the_cache
    ):
        """🔴 BOTH SIDES OF THE CACHE, AND THE SECOND ONE IS WHERE THE BUG WAS.

        This test used to prime the cache and assert `returncode != 0`. Both
        halves were wrong, and together they hid a real defect for a whole audit
        round: `!= 0` cannot see WHICH code, and priming the cache exercised only
        the branch that already returned 7.

        With NO cache — the FIRST run on a fresh host, the commonest way to reach
        this path — `resolve_state` hands back `EXIT_UNREACHABLE_NO_CACHE` (3),
        and the code returned that `hint` verbatim. So a WRITE returned the READ
        verdict "store-unreachable, no cache", which this whole file exists to
        make impossible, while four comments and the design doc said it could not
        happen. Measured before the fix: no cache -> 3, cache -> 7.

        The file's own rule is "pin a CODE and a SENTENCE, not merely non-zero" —
        stated at the top and broken here. Both are pinned now, on both sides.
        """
        cache = tmp_path / "c"
        if prime_the_cache:
            assert run_cairn("sync", url=live.base, cache=cache).returncode == 0
        replacement = tmp_path / "r.md"
        replacement.write_text(_entry("thing-beta", "widget-cfg", "- 2026-01-03: x."))
        proc = run_cairn(
            "put", "--scope", "widget-cfg", "--ref", "thing-beta",
            "--file", str(replacement), url=None, cache=cache,
        )
        assert proc.returncode == 7, (
            f"cache={prime_the_cache}: a write returned {proc.returncode}; 3 is the "
            f"READ code for 'nothing was displayed' and must never answer a write"
        )
        assert "refusing to PUT" in proc.stderr
        assert "Nothing was queued" in proc.stderr

    def test_put_with_a_MISSING_file_refuses_BEFORE_it_spends_a_sync(
        self, live, tmp_path
    ):
        """Exit 1 with a traceback is in neither table, and it arrived after a
        full snapshot download — for the commonest typo this verb has."""
        proc = run_cairn(
            "put", "--scope", "widget-cfg", "--ref", "thing-beta",
            "--file", str(tmp_path / "does-not-exist.md"),
            url=live.base, cache=tmp_path / "c",
        )
        assert proc.returncode == 2, (proc.returncode, proc.stderr)
        assert "cannot read --file" in proc.stderr
        assert "Traceback" not in proc.stderr


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

    def test_no_request_builder_ANYWHERE_bypasses_this_helper(self):
        """🔴 THE STRUCTURAL GUARD `_apply_standard_headers`' DOCSTRING NAMES.

        That docstring used to name a test called
        `test_every_request_builder_sets_the_UA`. It did not exist — anywhere.
        The only real check was the behavioural one below, which watches the
        APPEND path, so a new read verb building its own `Request` was unguarded
        by the very sentence claiming to guard it. That is the
        "reading as coverage while providing none" shape, and it is worse than
        no claim because it stops anyone looking.

        So: an AST walk over both files, finding every function that constructs a
        `urllib.request.Request`, and asserting each also calls
        `_apply_standard_headers`. A missing User-Agent is 403'd by the edge, and
        that 403 arrives looking like a bad token AND like the store being down.
        """
        import ast as _ast

        offenders = []
        checked = 0
        for path in (CAIRN_CLI, REPO / "scripts" / "cairn-cutover.py"):
            tree = _ast.parse(path.read_text())
            for node in _ast.walk(tree):
                if not isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                    continue
                calls = [
                    n for n in _ast.walk(node)
                    if isinstance(n, _ast.Call)
                ]
                builds = any(
                    isinstance(c.func, _ast.Attribute) and c.func.attr == "Request"
                    for c in calls
                )
                if not builds:
                    continue
                checked += 1
                applies = any(
                    (isinstance(c.func, _ast.Attribute)
                     and c.func.attr == "_apply_standard_headers")
                    or (isinstance(c.func, _ast.Name)
                        and c.func.id == "_apply_standard_headers")
                    for c in calls
                )
                if not applies:
                    offenders.append(f"{path.name}::{node.name}")
        # POSITIVE CONTROL: the scan must have FOUND request builders. A zero
        # here is a scan wired to nothing, and it would pass forever.
        assert checked >= 3, (
            f"the AST scan found only {checked} request builder(s) — it is not "
            f"looking at what it claims to look at"
        )
        assert not offenders, (
            f"these build a urllib Request without going through "
            f"`_apply_standard_headers`: {offenders}. urllib's default User-Agent "
            f"is 403'd by the edge in front of this host."
        )

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


# The entry `_populate_store` does NOT create. Distinct from every service name
# above so an assertion cannot pass by matching a neighbour.
NEW_SERVICE = "thing-gamma"
NEW_NUANCE = "- 2026-05-14: the shim retries once before it gives up."


def _new_entry_file(tmp_path: Path, service: str = NEW_SERVICE,
                    scope: str = "widget-cfg") -> Path:
    path = tmp_path / f"{service}.md"
    path.write_text(_entry(service, scope, NEW_NUANCE))
    return path


class TestCreateMakesAnEntryThatDidNotExist:
    """🔴 THE VERB THAT CLOSES THE STRANDING. `append` and `put` both resolve an
    EXISTING ref, so before `create` the only route to a new entry was a local
    write into `~/.claude/analyze-service-index/` — which, once reads moved to
    the pod cache, put the content on one host and in front of nobody. Measured
    2026-09-02: five whole entries.

    Every test pins a CODE and a SENTENCE, and the landing tests assert the
    FILE, exactly as `TestAppendLands` does — an exit code cannot tell a write
    that landed from one that did not.
    """

    def test_a_new_entry_is_created_and_is_READABLE_through_the_read_path(
        self, live, tmp_path
    ):
        """🔴 THE POSITIVE CONTROL IS THE READ, NOT THE FILE. The defect being
        fixed is content that exists on disk and reaches no reader, so "the bytes
        are in the store root" is exactly the claim that was TRUE the whole time
        content was being stranded. The recall below is what makes it evidence."""
        source = _new_entry_file(tmp_path)
        target = live.store / "widget-cfg" / f"{NEW_SERVICE}.md"
        assert not target.exists()
        proc = run_cairn(
            "create", "--scope", "widget-cfg", "--ref", NEW_SERVICE,
            "--file", str(source), url=live.base, cache=tmp_path / "c",
        )
        assert proc.returncode == 0, (proc.returncode, proc.stderr)
        assert "created" in proc.stdout, proc.stdout
        assert target.read_bytes() == source.read_bytes()
        seen = run_cairn(
            "recall", "--scope", "widget-cfg", url=live.base, cache=tmp_path / "c",
        )
        assert seen.returncode == 0, seen.stderr
        assert NEW_SERVICE in seen.stdout, seen.stdout

    def test_creating_over_an_EXISTING_entry_exits_9_and_writes_NOTHING(
        self, live, tmp_path
    ):
        target = live.store / "widget-cfg" / "thing-alpha.md"
        before = target.read_bytes()
        source = _new_entry_file(tmp_path, service="thing-alpha")
        assert source.read_bytes() != before
        proc = run_cairn(
            "create", "--scope", "widget-cfg", "--ref", "thing-alpha",
            "--file", str(source), url=live.base, cache=tmp_path / "c",
        )
        assert proc.returncode == 9, (proc.returncode, proc.stdout, proc.stderr)
        assert "already-exists" in proc.stderr, proc.stderr
        assert target.read_bytes() == before, "a create OVERWROTE an existing entry"

    def test_exit_9_is_NOT_the_precondition_code_8(self, live, tmp_path):
        """🔴 THE SERVER ANSWERS 412 FOR BOTH, AND THE REMEDIES ARE OPPOSITE.
        8 says the entry moved — re-sync, re-derive, re-apply, and a retry is
        right. 9 says it is already there — the identical retry loops forever.
        The two are asserted DIFFERENT against each other in one test, rather
        than each asserted equal to its own constant, because the hazard is that
        they COLLAPSE."""
        source = _new_entry_file(tmp_path, service="thing-alpha")
        exists = run_cairn(
            "create", "--scope", "widget-cfg", "--ref", "thing-alpha",
            "--file", str(source), url=live.base, cache=tmp_path / "c",
        )
        stale = run_cairn(
            "put", "--scope", "widget-cfg", "--ref", "thing-alpha",
            "--file", str(source), "--if-match", "0" * 16,
            url=live.base, cache=tmp_path / "c",
        )
        assert stale.returncode == 8, (stale.returncode, stale.stderr)
        assert exists.returncode == 9, (exists.returncode, exists.stderr)
        assert exists.returncode != stale.returncode

    def test_it_sends_If_None_Match_STAR_and_no_If_Match(self, live, tmp_path):
        """The wire fact, read off what the CLIENT actually sent — a header the
        client believes it sends is not a header on the wire."""
        source = _new_entry_file(tmp_path)
        live.handler.seen.clear()
        assert run_cairn(
            "create", "--scope", "widget-cfg", "--ref", NEW_SERVICE,
            "--file", str(source), url=live.base, cache=tmp_path / "c",
        ).returncode == 0
        puts = [r for r in live.handler.seen if r["verb"] == "PUT"]
        assert len(puts) == 1, live.handler.seen
        assert puts[0]["headers"].get("if-none-match") == "*"
        assert "if-match" not in puts[0]["headers"]
        assert puts[0]["path"] == f"/api/v1/entry/widget-cfg/{NEW_SERVICE}"
        assert puts[0]["body"] == source.read_bytes()

    def test_it_makes_NO_request_at_all_when_the_file_cannot_be_read(
        self, live, tmp_path
    ):
        """🔴 THE `cmd_put` LESSON, APPLIED HERE RATHER THAN REDISCOVERED: a
        missing `--file` must report ITSELF (rc 2), never the store. The
        assertion is on the REQUESTS the server saw, not only on the code — a
        version that read the file after building the request would still exit 2
        while having already sent it."""
        live.handler.seen.clear()
        proc = run_cairn(
            "create", "--scope", "widget-cfg", "--ref", NEW_SERVICE,
            "--file", str(tmp_path / "no-such-entry.md"),
            url=live.base, cache=tmp_path / "c",
        )
        assert proc.returncode == 2, (proc.returncode, proc.stderr)
        assert "cannot read --file" in proc.stderr
        assert live.handler.seen == [], live.handler.seen

    def test_an_unreachable_store_is_7_and_says_nothing_was_written(self, tmp_path):
        """A write NEVER degrades: there is no cache to write into and no spool,
        so an outage is a refusal at the write code, never a read code."""
        source = _new_entry_file(tmp_path)
        proc = run_cairn(
            "create", "--scope", "widget-cfg", "--ref", NEW_SERVICE,
            "--file", str(source), url=None, cache=tmp_path / "c",
        )
        assert proc.returncode == 7, (proc.returncode, proc.stderr)
        assert "the write did NOT happen" in proc.stderr
        assert "Nothing was queued and nothing was written locally" in proc.stderr

    def test_it_offers_NO_if_match_and_NO_no_sync(self):
        """Both would be meaningless: a create has no prior bytes to base a
        precondition on, and it reads no cache. Absent rather than accepted-and-
        ignored, so a caller cannot believe they set something."""
        help_text = subprocess.run(
            [sys.executable, str(CAIRN_CLI), "create", "--help"],
            capture_output=True, text=True, timeout=60,
        ).stdout
        assert "--if-match" not in help_text, help_text
        assert "--no-sync" not in help_text, help_text
        assert "--ref" in help_text and "--file" in help_text


class TestTheAlreadyExistsTokenIsTheDISCRIMINATOR:
    """🔴 A SECOND MAPPING TABLE EXISTS ONLY BECAUSE ONE STATUS CODE CARRIES TWO
    OUTCOMES. These pin it against LITERALS built here, never against the
    module's own constants — `<constant> in <output>` is the shape that let four
    renamed constants survive in this PR family, because the constant agreed with
    itself.
    """

    def _token_table(self) -> "dict[str, object]":
        import ast as _ast

        for node in _cairn_ast().body:
            targets = getattr(node, "targets", []) or (
                [node.target] if isinstance(node, _ast.AnnAssign) else []
            )
            for target in targets:
                if isinstance(target, _ast.Name) and \
                        target.id == "_WRITE_STATUS_TOKEN_EXITS":
                    return {
                        k.value: (v.id if isinstance(v, _ast.Name) else v.value)
                        for k, v in zip(node.value.keys, node.value.values)
                    }
        raise AssertionError("`_WRITE_STATUS_TOKEN_EXITS` is gone from scripts/cairn")

    def test_the_already_exists_token_maps_to_its_own_exit_code(self):
        table = self._token_table()
        assert table == {"already-exists": "EXIT_WRITE_EXISTS"}, table
        assert _module_constants()["EXIT_WRITE_EXISTS"] == 9

    def test_the_token_the_SERVER_emits_is_the_token_the_CLIENT_keys_on(self):
        """🔴 THE SEAM. Both sides are read from their own source and compared
        to each other; neither is compared to a constant it defines. A rename on
        one side alone fails here, which is exactly the failure two hermetically
        tested components cannot see."""
        server_src = SERVER_PY.read_text()
        assert '"X-Store-Status": "already-exists"' in server_src, (
            "server.py no longer answers the `already-exists` token, so the "
            "client's discriminator keys on nothing"
        )
        assert set(self._token_table()) == {"already-exists"}

    def test_an_UNKNOWN_token_falls_through_to_the_code_table(self):
        """A token not in the table is advisory routing that did not apply, never
        a pass: the HTTP code still decides."""
        cairn = _load_cairn()
        refused = cairn._classify(412, "precondition-failed", "moved")
        assert refused.exit_code == 8, refused.exit_code
        assert cairn._classify(503, "store-unreachable", "down").exit_code == 7
        assert cairn._classify(412, "already-exists", "there").exit_code == 9


def _load_cairn():
    """Import `scripts/cairn` (no `.py` suffix) as a module."""
    spec = importlib.util.spec_from_loader(
        "cairn_cli_w",
        importlib.machinery.SourceFileLoader("cairn_cli_w", str(CAIRN_CLI)),
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestExitCodesDoNotOverlap:
    def test_the_write_codes_are_disjoint_from_every_read_code(self):
        """Pinned as a SET, not as four separate assertions, because the hazard
        is a future verb reusing a number rather than any one of them being
        wrong today."""
        ns = _module_constants()
        reads = {ns["EXIT_OK"], ns["EXIT_UNREACHABLE_NO_CACHE"], ns["EXIT_USAGE"],
                 ns["EXIT_REFRESH_FAILED"], ns["EXIT_CORRUPT"]}
        writes = {ns["EXIT_WRITE_REFUSED"], ns["EXIT_WRITE_UNREACHABLE"],
                  ns["EXIT_WRITE_PRECONDITION"], ns["EXIT_WRITE_EXISTS"]}
        assert len(writes) == 4, writes
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
        import re as _re

        # 🔴 THE WHOLE SERVER, NOT TWO METHOD BODIES. This used to split at
        # `def _append_bullet` and regex the remainder, which yields ONLY
        # `_append_bullet` and `_replace_entry` — emitting {400,412,422,428,503},
        # all five already mapped. So it was green while the statuses that
        # actually reach a write caller from OTHER layers were invisible to it:
        # 404 (`_not_found`, defined ABOVE the split), 405 (`read-only`), 401/403
        # (auth), and 500 (`_backstop`) — and 500 was genuinely unmapped, falling
        # through to "change your request" for a condition whose remedy is a
        # retry. A scan whose docstring says "on a write route" must not be
        # bounded by where one method happens to sit in the file.
        emitted = {int(m) for m in _re.findall(r"self\._respond\(\s*(\d{3})", server_src)}
        emitted |= {int(m) for m in _re.findall(r"self\.send_response\(\s*(\d{3})", server_src)}
        # 🔴 `201` JOINED THIS SET ON 2026-09-03 BECAUSE IT IS A SUCCESS, NOT
        # BECAUSE IT WAS INCONVENIENT. `PUT … If-None-Match: *` answers
        # `201 created`; `urlopen` returns any 2xx rather than raising, so it
        # never reaches `_classify` and mapping it to a WRITE-FAILED exit code
        # would be a lie about a write that landed. The `already-exists` refusal
        # that shares this route is a `412`, and it IS mapped — through
        # `_WRITE_STATUS_TOKEN_EXITS`, which `test_the_already_exists_token_maps`
        # pins.
        emitted -= {200, 201, 204, 206, 304}   # successes and a conditional-GET code
        # POSITIVE CONTROL: the scan must see the statuses this PR learned about
        # the hard way. A regex that matched nothing would pass vacuously.
        for known in (404, 405, 500):
            assert known in emitted, (
                f"the status scan did not see {known}, which server.py demonstrably "
                f"emits — the pattern is wrong, not the table"
            )
        missing = sorted(emitted - set(table))
        assert not missing, (
            f"server.py can answer {missing} on a route this client writes to, and "
            f"`_WRITE_STATUS_EXITS` does not map them. An unmapped code falls "
            f"through to 'unrecognised' => EXIT_WRITE_REFUSED, which tells the "
            f"caller to change a request that may only need retrying."
        )
