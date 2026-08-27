"""Tests for scripts/subsystem-store-api/ — the phase-1 HTTP layer, seed and verifier.

WHAT IS BEING PROTECTED
-----------------------
`claudedocs/proposal-subsystem-store-homelab.md` phase 1: build the pod, seed
`/data` from the local store, serve a READ-ONLY API cluster-internally, and prove
the remote digest is byte-identical to the local one. The local store stays
authoritative and untouched.

🔴 THESE ARE INVARIANT GUARDS, NOT REGRESSION TESTS, AND THE DISTINCTION IS
NOT COSMETIC. There is no pre-existing defect here: `server.py` did not exist
before this branch, so every test in this file is trivially red at the base ref
(the module is absent) and that red proves NOTHING — it is a collection error,
not a caught bug. `claude/RULES.md`: "a guard pinning an invariant the bug never
violated is an invariant guard: label it as one, don't count it as regression
coverage." So the meaningful evidence for this file is the MUTATION matrix in
the PR body — each guard broken on purpose, watched to fail with THAT guard's
own error, and reached by a case no earlier check rejects — plus the two
comparators below that are exercised in both directions in-band.

WHAT IS EXERCISED IN BOTH DIRECTIONS, IN-BAND
---------------------------------------------
  * `TestAuthControls` — a valid token accepted AND no-token/wrong-token/
    near-miss-token watched to be rejected. An auth layer never seen to deny is
    not known to be an auth layer.
  * `TestByteIdentityVerifier` — the phase-1 acceptance comparator run against
    identical stores (PASS) and against a store differing by ONE character
    (FAIL, naming the scope). A comparator that always says PASS is
    indistinguishable from one that works.
  * `TestSeedIsNonDestructive` — the tree hasher is shown to CHANGE when the
    source is deliberately modified, before its "unchanged" verdict is believed.

🔴 NO TEST HERE READS THE REAL STORE. `~/.claude/analyze-service-index/` is
client-confidential, has no off-machine backup, and this repo is PUBLIC. Every
fixture below is synthetic, under `tmp_path`, with names invented for this file
and pairwise-distinct fields so a renderer that surfaced the wrong section
cannot pass by coincidence.

🔴 EXPECTATIONS ARE PINNED LITERALLY, never imported from the module under test.
`UNAUTHORIZED_BODY`, the 43-character token floor, the header names and the
status strings are all spelled again here by hand. Importing them would assert
`x == x` and stay green through a rename that broke every caller.
"""

from __future__ import annotations

import ast
import hashlib
import http.client
import importlib.util
from testlib import hermetic_git  # noqa: E402
import io
import os
import re
import shutil
import tarfile
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
API_DIR = ROOT / "scripts" / "subsystem-store-api"
SERVER_PATH = API_DIR / "server.py"
SEED_PATH = API_DIR / "seed.sh"
VERIFY_PATH = API_DIR / "verify-byte-identity.sh"
RECALL_PATH = ROOT / "scripts" / "lib" / "subsystem_recall.py"


def _load_server():
    """Import `server.py` by path — its directory name has a hyphen in it."""
    spec = importlib.util.spec_from_file_location("subsystem_store_server", SERVER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


api = _load_server()

# 🔴 THE SAME MODULE OBJECT THE SERVER IMPORTED FROM, not a second load.
# `server.py` puts `scripts/lib` on `sys.path` and imports `subsystem_resolver`,
# so by the line above it is in `sys.modules`; re-loading it by path would give a
# SECOND module whose `_LOADER_ENTRY_ACTIONS` is a different dict, and a mutation
# aimed at the one the loader actually reads would survive against it.
resolver = sys.modules["subsystem_resolver"]
assert resolver.classify_path is api.classify_path, (
    "the server no longer shares the resolver's classifier — two copies of "
    "'what IS this path' is the defect the move was made to prevent"
)


# =============================================================================
# Synthetic fixtures — realistic SHAPES, invented names, pairwise-distinct text.
# =============================================================================

SCOPE = "widget-cfg"
OTHER_SCOPE = "gizmo-notes"
EMPTY_SCOPE = "hollow-area"
BROKEN_SCOPE = "rubble-pile"

# Distinct on purpose: no substring is shared between the three sections, so a
# handler that served `## What it is` instead of `## Pointers` cannot pass.
WHAT_IT_IS = "A durable description that recall must never surface."
POINTER_LINE = "- ops skill `manage-widget` — invoke it for restarts"
NUANCE_LINE = "- 2026-01-02: the readiness probe lies for 40s after a reload."
OTHER_NUANCE = "- 2026-01-03: the sidecar drops its lease during a rollout."

# A token that clears the 43-character floor pinned literally below.
GOOD_TOKEN = "a" * 20 + "B" * 20 + "c" * 8  # 48 chars


def _entry(
    service: str,
    scope: str,
    *,
    sensitivity: str | None = "internal",
    nuance: str = NUANCE_LINE,
) -> str:
    lines = ["---", f"service: {service}", f"scope: {scope}"]
    if sensitivity is not None:
        lines.append(f"sensitivity: {sensitivity}")
    lines += [
        "---",
        "",
        "## What it is",
        WHAT_IT_IS,
        "",
        "## Pointers",
        POINTER_LINE,
        "",
        "## Nuance / work-history",
        nuance,
        "",
    ]
    return "\n".join(lines)


@pytest.fixture
def store(tmp_path: Path) -> Path:
    """A synthetic store: two populated scopes, one empty, one all-malformed."""
    root = tmp_path / "store"
    (root / SCOPE).mkdir(parents=True)
    (root / OTHER_SCOPE).mkdir(parents=True)
    (root / EMPTY_SCOPE).mkdir(parents=True)
    (root / BROKEN_SCOPE).mkdir(parents=True)
    (root / SCOPE / "thing-alpha.md").write_text(_entry("thing-alpha", SCOPE))
    (root / OTHER_SCOPE / "thing-beta.md").write_text(
        _entry("thing-beta", OTHER_SCOPE, nuance=OTHER_NUANCE)
    )
    # No front matter at all: the loader collects it as MALFORMED.
    (root / BROKEN_SCOPE / "thing-gamma.md").write_text("no front matter here\n")
    return root


# RFC 5737 TEST-NET-3, and three DISTINCT addresses: a test that used one
# address for the client and the same one for the spoofed header could not tell
# "keyed on CF-Connecting-IP" from "keyed on anything at all".
CLIENT_IP = "203.0.113.7"
OTHER_IP = "203.0.113.99"
SPOOF_IP = "198.51.100.4"  # TEST-NET-2 — the value a forged XFF would carry

# The peer allowlist every in-process server below is built with. The harness
# binds on loopback, so loopback IS the "trusted proxy" for these tests — and it
# is spelled here rather than defaulted inside `build_server`, because a default
# there would be the very hole `SUBSYSTEM_STORE_TRUSTED_PROXIES` exists to close.
LOOPBACK_PROXY = "127.0.0.1/32"
# A proxy allowlist that loopback is NOT in. Used to drive the untrusted-peer
# path without needing a second network interface. TEST-NET-1, distinct from
# every client address above.
NOT_LOOPBACK_PROXY = "192.0.2.0/24"


@contextmanager
def running(
    store_root,
    token=GOOD_TOKEN,
    *,
    tokens=None,
    limiter=None,
    trusted_proxies=(LOOPBACK_PROXY,),
):
    """Bind a real server on :0 and drive it over a real socket.

    Deliberately not a handler-level unit test: the response CODE, the header
    set and the exact bytes on the wire are what every claim in this file is
    about, and an in-process call to a handler method cannot observe them.

    🔴 THE YIELDED `audit` IS AN `AuditLog`, NOT A BARE LIST, so `await_audit`
    works on it. The response-does-not-imply-the-line race `drain_output`
    documents is NOT a property of the subprocess pipe — it is a property of
    `ThreadingHTTPServer`, and this in-process server has exactly the same one.
    """
    audit = AuditLog()
    httpd = api.build_server(
        host="127.0.0.1",
        port=0,
        store_root=str(store_root),
        tokens=(token,) if tokens is None else tuple(tokens),
        trusted_proxies=tuple(trusted_proxies),
        limiter=limiter,
        audit=audit.append,
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", audit
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)


def fetch(
    url: str,
    *,
    token: str | None = None,
    method: str = "GET",
    auth_header=None,
    client_ip: str | None = CLIENT_IP,
    extra_headers: dict[str, str] | None = None,
):
    """Return (code, headers, body-bytes) without raising on 4xx/5xx.

    🔴 `CF-Connecting-IP` is sent BY DEFAULT because the server requires it —
    it is the rate limiter's key, and an absent one fails closed. Pass
    `client_ip=None` to exercise exactly that.
    """
    req = urllib.request.Request(url, method=method)
    if auth_header is not None:
        req.add_header("Authorization", auth_header)
    elif token is not None:
        req.add_header("Authorization", f"Bearer {token}")
    if client_ip is not None:
        req.add_header("CF-Connecting-IP", client_ip)
    for key, value in (extra_headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def _raw_request(host: str, path: str, headers: list[tuple[str, str]]) -> int:
    """GET `path` with headers put on the wire VERBATIM, duplicates included.

    `urllib.request.Request.add_header` stores headers in a dict, so it silently
    collapses a repeated header to one — which makes it structurally incapable
    of expressing the "two `CF-Connecting-IP`s" case. `http.client.putheader`
    can.
    """
    conn = http.client.HTTPConnection(host, timeout=15)
    try:
        conn.putrequest("GET", path, skip_host=True, skip_accept_encoding=True)
        conn.putheader("Host", host)
        for key, value in headers:
            conn.putheader(key, value)
        conn.endheaders()
        resp = conn.getresponse()
        resp.read()
        return resp.status
    finally:
        conn.close()


def _executable_tokens(path: Path) -> str:
    """`path`'s source with COMMENTS and DOCSTRINGS removed, string literals kept.

    A header name IS a string literal, so a scan that dropped strings could not
    see one; a scan that kept comments would trip over the paragraphs explaining
    why a header is refused. `ast` drops comments by construction and the walk
    below drops docstrings, leaving exactly what executes.
    """
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", None)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def _comparable(headers: dict) -> tuple:
    """Header set with the two fields that legitimately vary stripped."""
    return tuple(
        sorted((k.lower(), v) for k, v in headers.items() if k.lower() != "date")
    )


AUDIT_PREFIX = "store-api audit "


def _audit_lines(all_lines: "list[str]") -> "list[str]":
    """The audit subset of a stream. ONE copy, shared by both record types.

    `Drained` (a subprocess's stdout) and `AuditLog` (an in-process server's
    sink) differ in where the lines come from and in nothing else that matters
    here; open-coding the filter twice is how the two drift apart.
    """
    return [ln for ln in all_lines if ln.startswith(AUDIT_PREFIX)]


class AuditLog(list):
    """Every line an IN-PROCESS `running()` server has emitted so far.

    🔴 IT IS A REAL `list` BECAUSE FORTY-ODD ASSERTIONS READ IT AS ONE, and it
    carries `Drained`'s read surface because `await_audit` is the one helper in
    this file that knows how to wait for a line. The race is NOT a property of
    the subprocess pipe: `_respond` runs before `_audit` on a
    `ThreadingHTTPServer`, so an in-process `fetch()` returning proves exactly
    as little about the audit line as a subprocess one does. Before this class
    existed the in-process sites had no way to wait at all, and the only thing
    standing between them and a red run was `shutdown()`'s 0.5s poll interval —
    a `sleep` nobody wrote down. Measured: with the handler's `_audit` delayed
    past that interval, the teardown "barrier" vanishes entirely.

    🔴 `closed` IS `None`, NOT AN UNSET `Event`, AND THAT IS THE HONEST VALUE.
    A `Drained` reaches EOF when the pipe closes, which is a real "no more lines
    are coming" signal. This stream has none: `ThreadingHTTPServer` runs its
    handlers as DAEMON threads, which `socketserver._Threads.append` refuses to
    track, so `server_close()` joins nothing and a handler can still append
    after teardown returns. Setting a `closed` event there would be a lie that
    turns "the line is a little late" into a hard failure; `await_audit` reads
    the sentinel and simply waits out its deadline instead.
    """

    closed = None

    @property
    def all(self) -> "list[str]":
        return list(self)

    @property
    def audit(self) -> "list[str]":
        return _audit_lines(list(self))

    @property
    def text(self) -> str:
        return "\n".join(self)


class Drained:
    """Everything a running store-api process has printed so far.

    🔴 Keeps the FULL output, not just the audit lines. The audit subset is what
    most assertions want, but at least one caller asserts that a credential
    appears NOWHERE in stdout — a check that silently weakens if it is narrowed
    to the audit lines, since a token leaked on a non-audit line would then pass.
    That caller must `wait_closed()` first: a line printed during SHUTDOWN (a
    SIGTERM handler, an atexit hook) reaches the pipe after the last assertion
    would otherwise have read it, and a credential leaked there must still fail.
    """

    def __init__(self) -> None:
        self.all: list[str] = []
        self.closed = threading.Event()   # set when the pipe reaches EOF

    @property
    def audit(self) -> "list[str]":
        return _audit_lines(self.all)

    @property
    def text(self) -> str:
        """The whole stream, for `x not in out` style assertions."""
        return "\n".join(self.all)

    def wait_closed(self, timeout: float = 15.0) -> bool:
        """Block until the process's stdout reaches EOF. Returns whether it did.

        Read `text` only AFTER this. Without it the reader thread may still be
        draining, so an assertion over the whole stream is racing the very lines
        it is meant to inspect — the same class of bug as asserting on an audit
        line before it is printed, one layer out. It is the reason
        `drain_output` returns something joinable at all: a helper that cannot
        be waited on just relocates the race into every caller.
        """
        return self.closed.wait(timeout)


def drain_output(proc) -> Drained:
    """Start draining a RUNNING process's stdout; returns the growing record.

    🔴 THE RESPONSE DOES NOT IMPLY THE LOG LINE, and every test that reads audit
    output has to be written around that. A handler writes its response and only
    THEN calls `_audit()`, on a ThreadingHTTPServer — so `fetch`/`fetch_from`
    returning means the response was written, not that the handler thread has
    reached its `print`. Any test that calls `proc.terminate()` on the client's
    return is racing the line it is about to assert on.

    Draining also keeps the pipe buffer from becoming a SECOND timing
    dependency. Teardown stays with `running_subprocess`.

    History, and why this is a function rather than a fourth copy: #544 found the
    race, measured it at 3/20 red locally plus two consecutive reds in the nix
    sandbox, and fixed ONE site inline. The other two kept the defect and one of
    them duly failed in CI at 2026-08-23T00:37Z (`devrc-ci-jxf5j`) with
    `IndexError: list index out of range` — an empty list indexed at [-1], on a
    tree whose only change was to an unrelated test. That is the open-coded
    predicate from claude/RULES.md: wrong at N-1 sites, and re-fixed one site at
    a time until it is consolidated.
    """
    out = Drained()

    def _run() -> None:
        try:
            for raw in proc.stdout:                 # ends when the pipe closes
                out.all.append(raw.rstrip("\n"))
        finally:
            out.closed.set()                        # EOF, even if the read raised

    threading.Thread(target=_run, daemon=True).start()
    return out


def await_audit(out: "Drained | AuditLog", n: int, timeout: float = 15.0) -> "list[str]":
    """Wait for at least `n` audit lines, then return them. RAISES if they never
    arrive.

    🔴 IT TAKES EITHER RECORD, and that is the whole reason there is only one of
    these. `Drained` wraps a subprocess's stdout; `AuditLog` is what an
    in-process `running()` server appends to. The hazard is identical in both —
    `_respond` runs before `_audit` — so a second waiting helper for the second
    shape would be the open-coded predicate `claude/RULES.md` warns about, wrong
    at N-1 sites.

    🔴 It raises rather than returning short, so that `[-1]` on the result is
    always safe. Returning whatever had arrived is what produced the CI failure
    this helper exists to prevent — `IndexError: list index out of range`, an
    empty list indexed at [-1], which names neither the expectation nor the
    actual. Consolidating a footgun into one place does not disarm it; this does.

    🔴 IT GUARANTEES A FLOOR, AND THE CEILING IS NOT WHERE IT LOOKS. This used to
    say "more lines than expected remains the caller's to catch", which is only
    half true and the misleading half. The value returned is a SNAPSHOT taken
    while the process is still running, so a caller's `== 3` against it cannot
    see a fourth record emitted afterwards — during shutdown, for instance.
    Measured: with the server patched to emit one extra audit line at SIGTERM,
    the racy pre-helper code FAILED and the snapshot check PASSES.

    A caller that means "exactly N, ever" must re-read `out.audit` after
    `out.wait_closed()`. `test_the_STDOUT_audit_stream_names_the_matched_
    fingerprint` does both and is the worked example.
    """
    # `closed` is None for an `AuditLog`: that stream has no EOF to short-circuit
    # on, so the loop simply runs to its deadline. See `AuditLog`.
    closed = out.closed
    deadline = time.time() + timeout
    while len(out.audit) < n and time.time() < deadline:
        if closed is not None and closed.is_set() and len(out.audit) < n:
            break                                   # the pipe is done; no more coming
        time.sleep(0.02)
    lines = out.audit
    ended = closed is not None and closed.is_set()
    assert len(lines) >= n, (
        f"expected at least {n} `{AUDIT_PREFIX}` line(s) within {timeout:g}s, got "
        f"{len(lines)}{' (stdout closed early)' if ended else ''}.\n"
        f"full stdout:\n{out.text}")
    return lines


def tree_hash(root: Path) -> str:
    """Content + relative-path digest of a whole tree. Order-stable."""
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(str(path.relative_to(root)).encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


# =============================================================================
# 1. Token loading — four guards, each with its OWN sentence, each REACHABLE.
# =============================================================================


def loaded(token_file, env, **kwargs) -> list[tuple]:
    """`load_tokens` as PLAIN TUPLES — `(token, identity, scopes)` per row.

    🔴 A tuple, not the `TokenRecord` itself, and deliberately: importing the
    dataclass and asserting `== TokenRecord(...)` would re-derive the expected
    value from the implementation under test, and a field renamed on both sides
    would stay green. Spelling the three facts out here means the assertion
    breaks when the SHAPE changes, which is when someone should look.

    `warn=` is swallowed by default so the legacy-mode banner does not spray
    stderr across every guard test; the tests that are ABOUT that banner pass
    their own sink and read it.
    """
    kwargs.setdefault("warn", lambda _line: None)
    return [(r.token, r.identity, r.scopes) for r in api.load_tokens(token_file, env, **kwargs)]


def exc_of(call) -> ValueError:
    """The `ValueError` `call()` raises, so a MESSAGE assertion is one line.

    Only for tests whose subject is the wording; `pytest.raises` stays inline
    wherever the fact under test is that it raised AT ALL, because that is a
    different claim and reads better spelled out.
    """
    with pytest.raises(ValueError) as exc:
        call()
    return exc.value


class TestTokenLoadingGuards:
    """`load_tokens` refuses to serve on a token that is absent, empty or weak.

    🔴 Each case is built so that every EARLIER guard passes: the empty-token
    case uses a file that exists and is readable, and the too-short case uses a
    file that exists, is readable and is non-empty. A test that tripped an
    earlier guard would be green with the guard it names deleted.
    """

    def test_no_source_at_all_names_the_two_ways_to_supply_one(self):
        with pytest.raises(ValueError) as exc:
            api.load_tokens(None, {})
        assert "no token source" in str(exc.value)
        assert "--token-file" in str(exc.value)
        assert "SUBSYSTEM_STORE_TOKEN" in str(exc.value)

    def test_a_missing_file_is_not_confused_with_an_absent_one(self, tmp_path: Path):
        with pytest.raises(ValueError) as exc:
            api.load_tokens(str(tmp_path / "nope"), {})
        assert "token file unreadable" in str(exc.value)

    def test_a_readable_file_of_whitespace_is_rejected_as_EMPTY(self, tmp_path: Path):
        # Guard 1 and 2 both pass here: the source exists and reads fine.
        path = tmp_path / "tok"
        path.write_text("   \n\t\n")
        with pytest.raises(ValueError) as exc:
            api.load_tokens(str(path), {})
        assert "token is empty" in str(exc.value)

    def test_a_short_but_perfectly_valid_file_is_rejected_as_TOO_SHORT(
        self, tmp_path: Path
    ):
        # Guards 1-3 all pass: the file exists, reads, and is non-empty. This is
        # the hand-typed-token case, which is exactly what guard 4 is for.
        path = tmp_path / "tok"
        path.write_text("hunter2\n")
        with pytest.raises(ValueError) as exc:
            api.load_tokens(str(path), {})
        assert "is too short" in str(exc.value)
        # The position is named even when there is only one — a message that
        # said "the token" would have to change shape the day a second appears.
        assert "token on line 1 of 1" in str(exc.value)
        # 43 chars = 256 bits base64url, pinned LITERALLY (§2b).
        assert "43" in str(exc.value)

    def test_the_floor_is_43_characters(self):
        # A literal, not `api.MIN_TOKEN_CHARS` — importing it would assert x == x.
        assert api.MIN_TOKEN_CHARS == 43

    def test_a_token_of_exactly_the_floor_is_accepted(self, tmp_path: Path):
        path = tmp_path / "tok"
        path.write_text("z" * 43 + "\n")
        # 🔴 A BARE ROW IS THE LEGACY RECORD: identity `legacy`, `scopes=None`
        # meaning UNRESTRICTED. Pinned here rather than only in the phase-3
        # section, because this is the shape criterion 10's rollback re-adds and
        # the whole migration rests on it still loading.
        assert loaded(str(path), {}) == [("z" * 43, "legacy", None)]

    def test_env_is_the_FALLBACK_not_the_primary(self, tmp_path: Path):
        # Both sources present: the FILE wins. The agent exec sandbox strips env
        # vars from agent-run commands, so an env token that quietly overrode a
        # mounted secret would make the deployed token unknowable.
        path = tmp_path / "tok"
        path.write_text("f" * 50)
        assert loaded(str(path), {"SUBSYSTEM_STORE_TOKEN": "e" * 50}) == [
            ("f" * 50, "legacy", None)
        ]

    def test_env_is_used_when_no_file_is_named(self):
        assert loaded(None, {"SUBSYSTEM_STORE_TOKEN": "e" * 50}) == [
            ("e" * 50, "legacy", None)
        ]


# =============================================================================
# 2. Auth — the POSITIVE and NEGATIVE controls, reported as a pair.
# =============================================================================


class TestAuthControls:
    """🔴 REPORTED AS A PAIR. A 200 from a valid token says nothing on its own —
    a handler that ignores the header entirely produces exactly that 200. The
    rejections below are what make the acceptance mean something.
    """

    def test_POSITIVE_a_valid_token_gets_a_real_digest(self, store: Path):
        with running(store) as (base, _):
            code, headers, body = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
        assert code == 200
        assert headers["X-Store-Status"] == "recalled"
        # A NON-ZERO count, watched to move: the pointer line is present.
        assert POINTER_LINE.encode() in body
        assert len(body) > 500

    def test_NEGATIVE_no_authorization_header_at_all(self, store: Path):
        with running(store) as (base, _):
            code, _headers, body = fetch(f"{base}/api/v1/recall/{SCOPE}")
        assert code == 401
        assert body == b"unauthorized\n"

    def test_NEGATIVE_a_wrong_token(self, store: Path):
        with running(store) as (base, _):
            code, _h, body = fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)
        assert code == 401
        assert body == b"unauthorized\n"

    def test_NEGATIVE_a_NEAR_MISS_token_of_the_right_length(self, store: Path):
        # One character different, same length — the case a length check or a
        # prefix comparison would wave through.
        near = GOOD_TOKEN[:-1] + ("d" if GOOD_TOKEN[-1] != "d" else "e")
        assert len(near) == len(GOOD_TOKEN) and near != GOOD_TOKEN
        with running(store) as (base, _):
            code, _h, body = fetch(f"{base}/api/v1/recall/{SCOPE}", token=near)
        assert code == 401
        assert body == b"unauthorized\n"

    def test_NEGATIVE_a_valid_token_under_the_wrong_scheme(self, store: Path):
        with running(store) as (base, _):
            code, _h, _b = fetch(
                f"{base}/api/v1/recall/{SCOPE}", auth_header=f"Basic {GOOD_TOKEN}"
            )
        assert code == 401

    def test_NEGATIVE_the_token_as_a_bare_header_value(self, store: Path):
        with running(store) as (base, _):
            code, _h, _b = fetch(f"{base}/api/v1/recall/{SCOPE}", auth_header=GOOD_TOKEN)
        assert code == 401

    def test_the_401_carries_a_WWW_Authenticate_challenge(self, store: Path):
        with running(store) as (base, _):
            _c, headers, _b = fetch(f"{base}/api/v1/recall/{SCOPE}")
        assert headers["WWW-Authenticate"].startswith("Bearer ")


class TestUniform401:
    """🔴 AN ERROR THAT DISCRIMINATES IS AN ENUMERATION API (§2b).

    An unauthenticated caller must not be able to learn which scopes exist, which
    refs exist, or which URLs are routes, by reading the differences between
    rejections. So every rejection is byte-identical — body, code AND header set.
    """

    def _reject(self, base: str, path: str):
        return fetch(f"{base}{path}", token="w" * 48)

    def test_bad_token_unknown_scope_and_unknown_ref_are_INDISTINGUISHABLE(
        self, store: Path
    ):
        with running(store) as (base, _):
            known = self._reject(base, f"/api/v1/recall/{SCOPE}")
            unknown_scope = self._reject(base, "/api/v1/recall/no-such-scope-anywhere")
            unknown_ref = self._reject(base, f"/api/v1/recall/{SCOPE}?ref=no-such-ref")
            not_a_route = self._reject(base, "/api/v1/nonsense/whatever")
            not_api = self._reject(base, "/admin")

        responses = [known, unknown_scope, unknown_ref, not_a_route, not_api]
        codes = {r[0] for r in responses}
        bodies = {r[2] for r in responses}
        headers = {_comparable(r[1]) for r in responses}
        assert codes == {401}
        assert bodies == {b"unauthorized\n"}
        # One header shape across all five. `Content-Length` is part of it, so a
        # body that leaked a scope name would show up here even if the assertion
        # above were somehow satisfied.
        assert len(headers) == 1, f"401s differ in headers: {headers}"

    def test_the_401_body_names_no_scope_no_ref_and_no_path(self, store: Path):
        with running(store) as (base, _):
            _c, _h, body = self._reject(base, f"/api/v1/recall/{SCOPE}?ref=thing-alpha")
        text = body.decode()
        assert SCOPE not in text
        assert "thing-alpha" not in text
        assert "scope" not in text.lower()


class TestConstantTimeComparison:
    """§2b: "Constant-time token comparison … a `==` on a secret is a timing
    oracle that a public endpoint makes practically exploitable."

    🔴 STRUCTURAL **AND** BEHAVIOURAL, because neither alone holds. A structural
    check ("it calls compare_digest") type-checks past a call with the wrong
    arguments; a behavioural check (right token in, wrong token out) is passed
    just as well by `==`. Both, or the guard is walkable.
    """

    def test_STRUCTURAL_authorize_delegates_to_hmac_compare_digest(self, monkeypatch):
        seen: list[tuple] = []
        real = api.hmac.compare_digest

        def spy(a, b):
            seen.append((a, b))
            return real(a, b)

        monkeypatch.setattr(api.hmac, "compare_digest", spy)
        api.authorize(f"Bearer {GOOD_TOKEN}", (GOOD_TOKEN,))
        assert len(seen) == 1
        # 🔴 And with the RIGHT arguments, in the right order: presented first,
        # expected second, both as bytes. A spy that only counted calls would be
        # green for `compare_digest(expected, expected)`, which always says yes.
        assert seen[0] == (GOOD_TOKEN.encode(), GOOD_TOKEN.encode())

    def test_BEHAVIOURAL_it_accepts_the_right_token_and_rejects_a_near_miss(self):
        api.authorize(f"Bearer {GOOD_TOKEN}", (GOOD_TOKEN,))  # no raise
        with pytest.raises(api._Rejected):
            api.authorize(f"Bearer {GOOD_TOKEN[:-1]}X", (GOOD_TOKEN,))
        with pytest.raises(api._Rejected):
            api.authorize(None, (GOOD_TOKEN,))

    def test_a_PREFIX_of_the_token_is_rejected(self):
        with pytest.raises(api._Rejected):
            api.authorize(f"Bearer {GOOD_TOKEN[:10]}", (GOOD_TOKEN,))

    def test_the_source_contains_no_equality_test_against_the_token(self):
        # A cheap second reading of the same property. It is NOT the guard —
        # `test_STRUCTURAL_...` is — but a `==` reintroduced during a refactor
        # would leave the spy green if the spy call were also left in place.
        src = SERVER_PATH.read_text()
        assert "== expected" not in src
        assert "expected ==" not in src
        assert "hmac.compare_digest" in src


# =============================================================================
# 3. The health endpoint says NOTHING.
# =============================================================================


class TestHealthSaysNothing:
    """§2b: "Health endpoint stays unauthenticated but says nothing — `200 ok`,
    no version, no scope count, no store revision."
    """

    def test_it_answers_without_a_token(self, store: Path):
        with running(store) as (base, _):
            code, _h, body = fetch(f"{base}/healthz")
        assert code == 200
        assert body == b"ok\n"

    def test_it_reveals_no_scope_count_no_revision_and_no_store_path(self, store: Path):
        with running(store) as (base, _):
            _c, headers, body = fetch(f"{base}/healthz")
        text = body.decode()
        assert SCOPE not in text and str(store) not in text
        assert "X-Store-Status" not in headers
        assert "X-Store-Revision" not in headers

    def test_it_does_not_leak_the_python_version_in_the_Server_header(self, store: Path):
        with running(store) as (base, _):
            _c, headers, _b = fetch(f"{base}/healthz")
        assert headers["Server"] == "subsystem-store"
        assert "Python" not in headers["Server"]


# =============================================================================
# 4. 🔴 THE FOUR STATES. This is the defect class the whole design exists to
#    avoid: an unreachable store rendering as "nothing recorded yet".
# =============================================================================


class TestFourStates:
    """§3: `scope-empty` and `store-unreachable` must NEVER render alike.

    The reader already refuses to conflate them (`load_store` raises rather than
    returning an empty index). This layer's job is not to throw that away by
    catching everything into one cheerful 200.
    """

    def test_recalled_is_200_with_content(self, store: Path):
        with running(store) as (base, _):
            code, headers, body = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
        assert (code, headers["X-Store-Status"], headers["X-Store-Exit"]) == (
            200,
            "recalled",
            "0",
        )
        assert NUANCE_LINE.encode() in body

    def test_scope_absent_is_200_and_says_NOTHING_RECORDED_YET(self, store: Path):
        with running(store) as (base, _):
            code, headers, body = fetch(
                f"{base}/api/v1/recall/never-heard-of-it", token=GOOD_TOKEN
            )
        assert code == 200
        assert headers["X-Store-Status"] == "scope-absent"
        assert b"NOTHING RECORDED YET" in body

    def test_scope_empty_is_200_and_the_store_WAS_read(self, store: Path):
        with running(store) as (base, _):
            code, headers, _b = fetch(
                f"{base}/api/v1/recall/{EMPTY_SCOPE}", token=GOOD_TOKEN
            )
        assert code == 200
        assert headers["X-Store-Status"] == "scope-empty"
        assert headers["X-Store-Exit"] == "0"

    def test_store_unreachable_is_503_and_NOT_a_200(self, tmp_path: Path):
        # The store root does not exist. Nothing was read, so nothing may be
        # concluded — and a 200 is a claim that the store WAS read.
        with running(tmp_path / "absent") as (base, _):
            code, headers, body = fetch(
                f"{base}/api/v1/recall/{EMPTY_SCOPE}", token=GOOD_TOKEN
            )
        assert code == 503
        assert headers["X-Store-Status"] == "store-unreachable"
        assert headers["X-Store-Exit"] == "3"
        assert b"NOT 'nothing recorded yet'" in body

    def test_scope_empty_and_store_unreachable_SHARE_NOTHING(
        self, store: Path, tmp_path: Path
    ):
        """🔴 THE DISCRIMINATOR, asserted as a difference rather than two facts.

        Two separate assertions elsewhere in this class can both pass while the
        two states still render alike to a caller that reads only one field.
        This one fails if they agree on the code, the status header OR the body.
        """
        with running(store) as (base, _):
            empty = fetch(f"{base}/api/v1/recall/{EMPTY_SCOPE}", token=GOOD_TOKEN)
        with running(tmp_path / "absent") as (base, _):
            gone = fetch(f"{base}/api/v1/recall/{EMPTY_SCOPE}", token=GOOD_TOKEN)

        assert empty[0] != gone[0], "same HTTP status code"
        assert empty[1]["X-Store-Status"] != gone[1]["X-Store-Status"]
        assert empty[2] != gone[2], "same body"
        # And specifically: the unreachable one must NOT be the reassuring text.
        assert b"NOTHING RECORDED YET" not in gone[2]

    def test_an_unreadable_scope_is_a_FIFTH_state_not_folded_into_empty(
        self, store: Path
    ):
        # Every file in the scope is malformed. The store WAS reached (200), but
        # nothing in this scope could be read — exit 3, and the body says so.
        with running(store) as (base, _):
            code, headers, body = fetch(
                f"{base}/api/v1/recall/{BROKEN_SCOPE}", token=GOOD_TOKEN
            )
        assert code == 200
        assert headers["X-Store-Status"] == "scope-unreadable"
        assert headers["X-Store-Exit"] == "3"
        assert b"NOTHING RECORDED YET" not in body


# =============================================================================
# 5. The reader's degradations survive the HTTP layer.
# =============================================================================


class TestMalformedDegradationSurvivesHTTP:
    """A scope with one bad entry still serves the good ones AND names the reject.

    Fail-closed here cost the whole scope once already (2 good entries and 1 bad
    one served ZERO); the reader was changed to degrade instead. An HTTP layer
    that turned a partial read into a 500 would undo that silently.
    """

    def test_two_good_entries_and_one_reject_serve_BOTH_and_NAME_the_reject(
        self, store: Path
    ):
        (store / SCOPE / "thing-delta.md").write_text(
            _entry("thing-delta", SCOPE, nuance="- 2026-01-04: a second good entry.")
        )
        (store / SCOPE / "thing-wrecked.md").write_text("not front matter\n")
        with running(store) as (base, _):
            code, headers, body = fetch(
                f"{base}/api/v1/recall/{SCOPE}?mode=list", token=GOOD_TOKEN
            )
        text = body.decode()
        assert code == 200
        assert headers["X-Store-Exit"] == "0", "a partial read still served content"
        assert "thing-alpha" in text and "thing-delta" in text
        assert "thing-wrecked" in text
        assert "MALFORMED" in text

    def test_the_pod_LOG_counts_the_real_rejects_not_an_empty_tuple(
        self, store: Path, capfd
    ):
        """`_exit_for` writes the CLI's own one-line summary to stderr, which in
        a pod is the log. It takes the malformed tuple, so handing it an empty
        one would print "all 0 entry files are MALFORMED" — a sentence that is
        both false and reassuring, on the one status that exists to report
        rejects. Reached with the all-malformed scope, since that is the only
        status `_exit_for` prints for at all.
        """
        (store / BROKEN_SCOPE / "thing-shattered.md").write_text("also not front matter\n")
        capfd.readouterr()
        with running(store) as (base, _):
            code, headers, _b = fetch(
                f"{base}/api/v1/recall/{BROKEN_SCOPE}", token=GOOD_TOKEN
            )
        err = capfd.readouterr().err
        assert (code, headers["X-Store-Exit"]) == (200, "3")
        assert "all 2 entry files" in err, err


class TestSensitivityFailSafeSurvivesHTTP:
    """An absent or unknown sensitivity marker folds to `client-confidential`.

    🔴 The fail-safe is the reason it is safe to serve this store at all. A
    rendering path that dropped it would hand an entry to a caller with no mark
    on it, and unmarked reads as unrestricted.
    """

    @pytest.mark.parametrize(
        "marker", [None, "totally-made-up-level", "", "public-ish"]
    )
    def test_an_absent_or_unknown_marker_reads_client_confidential(
        self, store: Path, marker
    ):
        (store / SCOPE / "thing-unmarked.md").write_text(
            _entry("thing-unmarked", SCOPE, sensitivity=marker)
        )
        with running(store) as (base, _):
            code, _h, body = fetch(
                f"{base}/api/v1/recall/{SCOPE}?mode=list", token=GOOD_TOKEN
            )
        assert code == 200
        text = body.decode()
        assert "thing-unmarked" in text
        # The fold is per-entry; find the row and read ITS label, not the
        # caveat's generic sentence, which would be a spelled guard satisfied by
        # prose that has nothing to do with this entry.
        row = next(ln for ln in text.splitlines() if "thing-unmarked" in ln)
        assert "client-confidential" in row


# =============================================================================
# 6. Phase 1 is READ-ONLY, and that is enforced rather than documented.
# =============================================================================


class TestReadOnlyPhase1:
    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    def test_every_write_method_is_405_even_with_a_VALID_token(
        self, store: Path, method: str
    ):
        with running(store) as (base, _):
            code, headers, body = fetch(
                f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, method=method
            )
        assert code == 405
        assert headers["Allow"] == "GET, HEAD"
        assert body == b"read-only\n"

    def test_a_full_read_workload_leaves_the_store_BYTE_IDENTICAL(self, store: Path):
        """Behavioural, not a grep for `open(..., "w")`.

        A spelled guard on the source would be satisfied by a write performed
        through any other spelling; hashing the tree is not.
        """
        before = tree_hash(store)
        with running(store) as (base, _):
            for path in (
                f"/api/v1/recall/{SCOPE}",
                f"/api/v1/recall/{SCOPE}?mode=list",
                f"/api/v1/recall/{SCOPE}?mode=full&limit=5",
                f"/api/v1/recall/{SCOPE}?ref=thing-alpha",
                f"/api/v1/recall/{BROKEN_SCOPE}",
                f"/api/v1/search/{SCOPE}?q=readiness+probe",
                "/api/v1/recall/never-heard-of-it",
                "/healthz",
            ):
                fetch(f"{base}{path}", token=GOOD_TOKEN)
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, method="POST")
        assert tree_hash(store) == before

    def test_the_hasher_can_SEE_a_change(self, store: Path):
        """Positive control for the assertion above. A hasher wired to nothing
        reports "unchanged" for a tree that was rewritten wholesale."""
        before = tree_hash(store)
        (store / SCOPE / "thing-alpha.md").write_text(
            _entry("thing-alpha", SCOPE, nuance="- 2026-01-09: moved.")
        )
        assert tree_hash(store) != before


# =============================================================================
# 7. Search, and query parameters that must not silently default.
# =============================================================================


# 🔴 THE PINNED DECISION TABLE, as a named constant so the completeness guard
# and the per-cell guard read the SAME list. Introspecting the parametrize mark
# to get it was clever and wrong; two literals would drift.
#
# THREE contexts now: the `/snapshot` scope-root scan, the `/snapshot` entry
# scan, and — since the entry-kind guard landed — `subsystem_resolver`'s INDEX
# LOADER, which is a different context and therefore a different column.
#
# 🔴 THE LOADER COLUMN IS THE NARROW RULING, CELL BY CELL. It refuses exactly
# `broken-link` (the Emacs `.#entry.md` lock file, which used to 503 the whole
# store) and the two shapes of "an `open()` on this never returns" — `other`
# (the fifo itself) and `link-to-other` (a symlink pointing at one), each of
# which was measured HANGING the request thread. It TAKES everything else —
# most pointedly `link-to-file`, which the loader has always read. Copying the
# `_ENTRY_ACTIONS` column wholesale is the over-broad form that was explicitly
# rejected, and flipping any remaining TAKE here to REFUSE is a behaviour change
# for ordinary callers; every one of those flips is a mutant this column kills.
DECISION_TABLE = [
    ("KIND_BROKEN_LINK", "REFUSE", "REFUSE", "REFUSE"),
    ("KIND_LINK_TO_DIR", "REFUSE", "REFUSE", "TAKE"),
    ("KIND_LINK_TO_FILE", "SKIP", "REFUSE", "TAKE"),
    # 🔴 CLOSED, AND IT WAS THE SAME DEFECT — NOT A LESSER ONE. This cell was
    # `TAKE` for one round, pinned as a named residual because the ruling that
    # created the loader column named `other` and `broken-link` and nothing
    # else. Then it was MEASURED on that tip: a `link-to-fifo.md` symlink in a
    # scope the caller never asked for wedged `GET /api/v1/recall/<other-scope>`
    # for 25s under an UNRESTRICTED legacy token — the request thread gone, on a
    # `replicas: 1` / `strategy: Recreate` service. `open()` blocks identically
    # whether the fifo is reached directly or through a link, so the loader
    # column now refuses BOTH shapes of it. `link-to-file` stays `TAKE` — that
    # is still the upper bound, and still the point of the narrow form.
    ("KIND_LINK_TO_OTHER", "SKIP", "REFUSE", "REFUSE"),
    ("KIND_DIRECTORY", "TAKE", "REFUSE", "TAKE"),
    ("KIND_REGULAR_FILE", "SKIP", "TAKE", "TAKE"),
    ("KIND_OTHER", "SKIP", "REFUSE", "REFUSE"),
    # 🔴 "I could not look" must never share a cell with "nothing is there".
    # In the LOADER it is TAKE, so `read_text` raises and the four-state rule's
    # "the store was not fully READ" is preserved — a DIFFERENT fact from "this
    # entry is malformed", which is what REFUSE would report it as.
    ("KIND_INDETERMINATE", "REFUSE", "REFUSE", "TAKE"),
    ("KIND_ABSENT", "SKIP", "SKIP", "TAKE"),
]

# The LEDGER of every action table that exists, so "all three contexts" is a
# claim something checks rather than a number in a test name.
ALL_ACTION_TABLES = [
    ("subsystem_store_server._ROOT_ACTIONS", api._ROOT_ACTIONS),
    ("subsystem_store_server._ENTRY_ACTIONS", api._ENTRY_ACTIONS),
    ("subsystem_resolver._LOADER_ENTRY_ACTIONS", resolver._LOADER_ENTRY_ACTIONS),
]


class TestClassifierIsTotal:
    """🔴 THE GUARD THAT IS SUPPOSED TO END THE ROUND-N LOOP.

    Four audit rounds found the same defect shape in `_snapshot`, each time in a
    NEW input class that the previous round's sequence of `if`s did not decide:
    symlinked entries, symlinked scope dirs, symlinked non-scopes, then dangling
    links and symlink loops. Every fix added an arm; none made the rule total,
    so the next class fell through the same gap and rendered as `scope-empty —
    nothing recorded` at exit 0.

    These tests pin the classification itself rather than its instances:
      1. every path lands in exactly one kind (totality of `classify_path`);
      2. every kind is mapped explicitly in BOTH contexts (no default);
      3. the fallthrough RAISES, so an unmapped kind is a failure, not a skip.

    Adding a kind therefore breaks (2) until somebody decides, per context,
    whether it is TAKE, SKIP or REFUSE — which is the decision the last four
    rounds each made implicitly, by omission, and got wrong.
    """

    def test_every_kind_is_mapped_in_ALL_THREE_contexts(self):
        for name, actions in ALL_ACTION_TABLES:
            missing = api.ALL_KINDS - set(actions)
            extra = set(actions) - api.ALL_KINDS
            assert not missing, f"{name} does not decide: {sorted(missing)}"
            assert not extra, f"{name} maps unknown kinds: {sorted(extra)}"

    def test_the_TABLE_LEDGER_names_every_action_table_that_exists(self):
        """🔴 A SEAM GUARD, NOT A TOTALITY ONE — the test above is only as wide
        as the list it iterates.

        A fourth context that maps kinds would be structurally invisible to
        every assertion in this class: nothing here reads the modules, so a new
        table simply would not be checked, and "every kind is mapped in all
        three contexts" would keep passing while the fourth defaulted. This
        asserts the LEDGER against what the two modules actually define, so the
        set GROWING or SHRINKING is a failure either way.
        """
        found = {
            f"{mod.__name__}.{name}"
            for mod in (api, resolver)
            for name, value in vars(mod).items()
            if name.endswith("_ACTIONS") and isinstance(value, dict)
        }
        assert found == {name for name, _t in ALL_ACTION_TABLES}, (
            f"the action-table ledger is out of date: {sorted(found)}"
        )

    def test_the_pinned_table_covers_EVERY_kind(self):
        """🔴 Two-way pin on the parametrize list itself.

        `test_the_decision_table_is_pinned`'s docstring says "a silent flip of
        any single cell fails", but nothing asserted its parametrize list
        covered `ALL_KINDS` — so a future kind, mapped correctly in both dicts,
        would have an UNPINNED cell while the docstring claimed otherwise. Same
        idiom as `test_waiting_windows.py`'s `set(KIND_BAND) == set(ALL_KINDS)`.
        """
        pinned = {getattr(api, name) for name, _r, _e, _l in DECISION_TABLE}
        assert pinned == api.ALL_KINDS, (
            f"unpinned cells: {sorted(api.ALL_KINDS - pinned)}"
        )

    def test_an_unstatable_ROOT_child_is_REFUSED_by_the_CLASSIFIER(
        self, store: Path, tmp_path: Path
    ):
        """🔴 The cell this whole commit exists for had only a CONSTANTS pin —
        the shape `TestEntryTableCellsHaveBehaviour`'s own docstring calls
        insufficient ("exactly the kind a future edit updates ALONGSIDE the code
        it was meant to stop"). Three ENTRY cells got behavioural tests; the
        ROOT cell did not.

        🔴 IT ASSERTS THE MESSAGE, NOT THE STATUS, AND THAT IS THE WHOLE POINT.
        With the store root at 0o600 the tar block ALSO fails (on `.seed-stamp`),
        so a test asserting only `503` passes even with this cell flipped to
        SKIP — green for the wrong reason. The classifier's refusal is emitted
        BEFORE the tar block and names the kind, so pinning `indeterminate
        refused` is what discriminates. Verified by mutation, not assumed.
        """
        if os.geteuid() == 0:
            pytest.skip("root ignores directory permissions; unreachable as root")
        store.chmod(0o600)  # readable, NOT searchable -> every child lstat EACCES
        try:
            with running(store) as (base, _):
                code, _h, body = fetch(f"{base}/api/v1/snapshot", token=GOOD_TOKEN)
        finally:
            store.chmod(0o755)
        assert code == 503, body[:300]
        assert b"indeterminate refused" in body, body[:300]

    def test_every_mapped_action_is_a_known_action(self):
        for name, actions in ALL_ACTION_TABLES:
            for kind, action in actions.items():
                assert action in (api.SKIP, api.TAKE, api.REFUSE), (name, kind, action)

    def test_an_unmapped_kind_RAISES_rather_than_defaulting(self):
        """🔴 The fallthrough is the whole mechanism. If `action_for` returned a
        default, adding a kind would silently inherit SKIP — which is exactly
        how a dangling scope link became `scope-empty` at exit 0."""
        with pytest.raises(AssertionError, match="unclassified path kind"):
            api.action_for("a-kind-nobody-mapped", api._ROOT_ACTIONS)

    @pytest.mark.parametrize(
        "kind,expected_root,expected_entry,expected_loader", DECISION_TABLE
    )
    def test_the_decision_table_is_pinned(
        self, kind, expected_root, expected_entry, expected_loader
    ):
        """Pins the table itself, so a silent flip of any single cell fails.

        Every previous round's bug was one cell of this table being wrong or
        absent; asserting the table makes each cell a named, reviewable decision
        instead of an emergent property of statement order.

        ⚠ A CONSTANTS PIN IS NOT A BEHAVIOUR TEST, and this class's own history
        says so — three ENTRY cells needed behavioural tests before they were
        believed. The two loader cells that DO something have them below
        (`TestTheLoaderRefusesHostileEntriesByKind`); this asserts the decision
        is written down, not that it fires.
        """
        k = getattr(api, kind)
        assert api._ROOT_ACTIONS[k] == getattr(api, expected_root)
        assert api._ENTRY_ACTIONS[k] == getattr(api, expected_entry)
        assert resolver._LOADER_ENTRY_ACTIONS[k] == getattr(api, expected_loader)

    def test_every_REFUSED_loader_kind_HAS_a_reason_and_no_other_kind_does(self):
        """🔴 THE SEAM BETWEEN THE TWO DICTS, which nothing else reads together.

        `load_index` looks the reason up by `_LOADER_REFUSAL_REASON[kind]` —
        an unguarded subscript — so flipping a cell to REFUSE without writing
        its sentence turns a hostile entry into a `KeyError` out of the loader:
        a 500 with no `X-Store-Status`, for a shape whose whole fix was to make
        it a NAMED malformed row. That is how the `link-to-other` cell would
        have landed, and reviewing the table alone cannot see it.

        Asserted as SET EQUALITY, not containment, so it fails when the ledger
        GROWS as well as when it shrinks — a reason left behind for a kind that
        went back to TAKE is dead prose claiming a guard that is gone.
        """
        refused = {
            k
            for k, action in resolver._LOADER_ENTRY_ACTIONS.items()
            if action == api.REFUSE
        }
        assert refused == set(resolver._LOADER_REFUSAL_REASON), (
            "the loader's REFUSE cells and its refusal sentences have drifted"
        )
        assert refused == {api.KIND_BROKEN_LINK, api.KIND_OTHER, api.KIND_LINK_TO_OTHER}

    def test_classify_returns_the_right_kind_for_each_REAL_path(self, tmp_path: Path):
        """🔴 The table above is only meaningful if `classify_path` actually
        produces these kinds from real filesystem objects. Built with `os.mkfifo`
        and real symlinks — not mocks — because the whole bug class came from
        `pathlib` predicates DEREFERENCING in ways a mock would not reproduce.
        """
        (tmp_path / "plain.md").write_text("x")
        (tmp_path / "adir").mkdir()
        (tmp_path / "to_file").symlink_to(tmp_path / "plain.md")
        (tmp_path / "to_dir").symlink_to(tmp_path / "adir", target_is_directory=True)
        (tmp_path / "dangling").symlink_to(tmp_path / "nope")
        (tmp_path / "loop").symlink_to(tmp_path / "loop")
        os.mkfifo(tmp_path / "afifo")
        (tmp_path / "to_fifo").symlink_to(tmp_path / "afifo")

        assert api.classify_path(tmp_path / "plain.md") == api.KIND_REGULAR_FILE
        assert api.classify_path(tmp_path / "adir") == api.KIND_DIRECTORY
        assert api.classify_path(tmp_path / "to_file") == api.KIND_LINK_TO_FILE
        assert api.classify_path(tmp_path / "to_dir") == api.KIND_LINK_TO_DIR
        # 🔴 The r4 regression lived here: `is_dir()` is False for BOTH of these,
        # so the old code skipped them and the scope read as empty.
        assert api.classify_path(tmp_path / "dangling") == api.KIND_BROKEN_LINK
        assert api.classify_path(tmp_path / "loop") == api.KIND_BROKEN_LINK
        assert api.classify_path(tmp_path / "afifo") == api.KIND_OTHER
        assert api.classify_path(tmp_path / "to_fifo") == api.KIND_LINK_TO_OTHER
        assert api.classify_path(tmp_path / "never-existed") == api.KIND_ABSENT

    def test_an_UNSTATABLE_path_is_INDETERMINATE_not_OTHER(self, tmp_path: Path):
        """🔴 The last cell of the four-round loop.

        Every pathlib predicate returns False when the stat itself fails, so an
        EACCES child fell into KIND_OTHER — the FIFO bucket — and was SKIPPED at
        the root. MEASURED end-to-end before this fix, store root at 0o600
        (readable, not searchable, so readdir works and every lstat gives
        EACCES): snapshot answered 200 / X-Store-Exit: 0 / entries=0, and the
        client printed "scope-empty — nothing recorded". An unreadable store
        rendering as an empty one, which is the defect this client exists for.
        """
        if os.geteuid() == 0:
            pytest.skip("root ignores directory permissions; unreachable as root")
        parent = tmp_path / "locked"
        parent.mkdir()
        (parent / "child.md").write_text("x")
        parent.chmod(0o600)  # readable, NOT searchable -> lstat(child) = EACCES
        try:
            assert api.classify_path(parent / "child.md") == api.KIND_INDETERMINATE
        finally:
            parent.chmod(0o755)

    def test_classify_is_exhaustive_over_a_real_directory(self, tmp_path: Path):
        """Totality, behaviourally: every child of a directory containing one of
        each shape classifies into `ALL_KINDS`, and between them they cover it."""
        (tmp_path / "plain.md").write_text("x")
        (tmp_path / "adir").mkdir()
        (tmp_path / "to_file").symlink_to(tmp_path / "plain.md")
        (tmp_path / "to_dir").symlink_to(tmp_path / "adir", target_is_directory=True)
        (tmp_path / "dangling").symlink_to(tmp_path / "nope")
        os.mkfifo(tmp_path / "afifo")
        (tmp_path / "to_fifo").symlink_to(tmp_path / "afifo")

        seen = {api.classify_path(p) for p in tmp_path.iterdir()}
        # ABSENT and INDETERMINATE cannot be produced by iterating a readable
        # directory — they are the two "the stat failed" answers — so they are
        # covered by their own tests above rather than here. Named, not omitted.
        reachable_by_iteration = api.ALL_KINDS - {api.KIND_ABSENT, api.KIND_INDETERMINATE}
        assert seen <= api.ALL_KINDS, f"produced an unknown kind: {seen - api.ALL_KINDS}"
        assert seen == reachable_by_iteration, (
            f"fixture misses kinds: {reachable_by_iteration - seen}"
        )


class TestSnapshotRoute:
    """Phase 2's cache-fill route: `GET /api/v1/snapshot` ships the entry files.

    🔴 The property under test is NOT "a tar came back". It is that a digest
    rendered from the EXTRACTED copy is byte-identical to one rendered from the
    source — because that is what makes a client's offline answer the same
    answer, and it is the thing a plausible tar gets silently wrong.
    """

    @staticmethod
    def _members(body: bytes) -> dict[str, tarfile.TarInfo]:
        with tarfile.open(fileobj=io.BytesIO(body), mode="r") as tar:
            return {m.name: m for m in tar.getmembers()}

    @staticmethod
    def _extract(body: bytes, dest: Path) -> Path:
        with tarfile.open(fileobj=io.BytesIO(body), mode="r") as tar:
            tar.extractall(dest)
        return dest

    def test_it_ships_every_entry_and_counts_them(self, store: Path):
        with running(store) as (base, _):
            code, headers, body = fetch(f"{base}/api/v1/snapshot", token=GOOD_TOKEN)
        assert code == 200
        assert headers["X-Store-Status"] == "snapshot"
        assert headers["Content-Type"] == "application/gzip"
        members = self._members(body)
        on_disk = {
            f"{p.parent.name}/{p.name}" for p in store.glob("*/*.md") if p.is_file()
        }
        assert on_disk, "fixture bug: the store has no entries to ship"
        assert on_disk <= set(members), f"missing: {on_disk - set(members)}"
        # The server's own count must agree with what it actually wrote.
        assert int(headers["X-Store-Entries"]) == len(on_disk)

    def test_the_archive_is_SMALLER_than_the_payload_it_carries(self, store: Path):
        """The gzip change was made on a measured claim — PAX spends ~2 KB of
        headers on a ~200-byte entry, so an uncompressed tar of 305 small
        entries measured **10.1x** the markdown it carried. Assert the property
        rather than trusting the commit message: with many small entries the
        archive must not exceed the raw bytes.
        """
        for i in range(40):
            (store / SCOPE / f"bulk-{i:03d}.md").write_text(
                _entry(f"bulk-{i:03d}", SCOPE, nuance=f"- 2026-03-01: item {i}.")
            )
        raw = sum(p.stat().st_size for p in store.glob("*/*.md"))
        with running(store) as (base, _):
            _c, _h, body = fetch(f"{base}/api/v1/snapshot", token=GOOD_TOKEN)
        assert len(body) < raw, (
            f"archive {len(body)}B vs {raw}B of markdown — compression is off "
            f"or PAX overhead is dominating again"
        )

    def test_a_digest_from_the_EXTRACTED_copy_is_BYTE_IDENTICAL(
        self, store: Path, tmp_path: Path
    ):
        """🔴 The criterion this route exists to satisfy.

        Rendered from the source vs rendered from the extracted copy. The one
        line that legitimately differs is `store: <root>`, for exactly the
        reason `verify-byte-identity.sh` documents — so it is canonicalised on
        BOTH sides and nothing else is.
        """
        with running(store) as (base, _):
            _c, _h, body = fetch(f"{base}/api/v1/snapshot", token=GOOD_TOKEN)
        copy = self._extract(body, tmp_path / "cache")

        def digest(root: Path) -> str:
            report = api.rc.recall(str(root), SCOPE, mode=api.rc.DEFAULT_MODE)
            text = api.rc.render_text(report)
            return re.sub(r"^(\s*store:) .*$", r"\1 X", text, flags=re.M)

        assert digest(copy) == digest(store)

    def test_mtimes_are_PRESERVED_not_normalised(self, store: Path, tmp_path: Path):
        """🔴 Load-bearing, and the reason a "reproducible" tar is WRONG here.

        The reader orders the index newest-first by entry-file mtime. Normalising
        mtimes — the usual reproducibility move — reorders every digest rendered
        from the copy, with no error and nothing missing: it just reads as a
        stale cache.

        The fixture mtimes are pairwise distinct AND deliberately anti-aligned
        with alphabetical order, so a tar that dropped mtimes cannot accidentally
        reproduce the right ordering.
        """
        # This test owns its ordering requirement rather than depending on the
        # shared fixture's shape — which holds ONE entry in this scope, so the
        # ordering claim would have been vacuous against it.
        for name in ("thing-beta", "thing-gamma"):
            (store / SCOPE / f"{name}.md").write_text(
                _entry(name, SCOPE, nuance=f"- 2026-02-01: {name} distinct nuance.")
            )
        entries = sorted(store.glob(f"{SCOPE}/*.md"))
        assert len(entries) >= 3, "fixture bug: need several entries to order"
        # 🔴 FRACTIONAL, AND SHARING ONE WHOLE SECOND. An earlier version of this
        # test used whole-second mtimes a day apart and PASSED against a server
        # that truncated with `int()` — the truncation was invisible because the
        # fixture had no fraction to lose. Entries written in the same second is
        # the NORMAL case (a `/handoff` writes several at once), and there the
        # truncation makes every entry tie, so the reader's ref tie-break
        # silently reorders the index. Fixture values must be able to see the
        # mutation, or a green result is a claim about the fixture.
        base_t = 1_700_000_000
        for i, path in enumerate(reversed(entries)):
            stamp = base_t + 0.1 * (i + 1)  # same second, distinct fractions
            os.utime(path, (stamp, stamp))
        want = {f"{p.parent.name}/{p.name}": p.stat().st_mtime for p in entries}
        assert len({int(v) for v in want.values()}) == 1, (
            "fixture bug: the mtimes must share a whole second, or truncation "
            "is not exercised"
        )

        with running(store) as (base, _):
            _c, _h, body = fetch(f"{base}/api/v1/snapshot", token=GOOD_TOKEN)
        members = self._members(body)
        for name, mtime in want.items():
            assert members[name].mtime == pytest.approx(mtime, abs=1e-4), (
                f"{name}: snapshot lost sub-second mtime precision"
            )

        copy = self._extract(body, tmp_path / "cache")
        for name, mtime in want.items():
            assert (copy / name).stat().st_mtime == pytest.approx(mtime, abs=1e-4)

    def test_the_index_ORDER_survives_a_round_trip(self, store: Path, tmp_path: Path):
        """The behavioural consequence of the mtime test above.

        Preserving mtimes is a means; this is the end. Asserted separately
        because a structural mtime check passes for a tar whose ordering the
        reader still disagrees with — e.g. if the reader's tie-break changed.
        """
        for name in ("thing-beta", "thing-gamma"):
            (store / SCOPE / f"{name}.md").write_text(
                _entry(name, SCOPE, nuance=f"- 2026-02-01: {name} distinct nuance.")
            )
        base_t = 1_700_000_000
        for i, path in enumerate(sorted(store.glob(f"{SCOPE}/*.md"))):
            stamp = base_t + 0.1 * (i + 1)
            os.utime(path, (stamp, stamp))

        with running(store) as (base, _):
            _c, _h, body = fetch(f"{base}/api/v1/snapshot", token=GOOD_TOKEN)
        copy = self._extract(body, tmp_path / "cache")

        def index_order(root: Path) -> list[str]:
            report = api.rc.recall(str(root), SCOPE, mode=api.rc.DEFAULT_MODE)
            return [
                line.split()[0]
                for line in api.rc.render_text(report).splitlines()
                if line.startswith("  ") and line.strip() and "nuance" in line
            ]

        source_order = index_order(store)
        assert len(source_order) >= 3, f"fixture bug: got {source_order}"
        assert index_order(copy) == source_order

    def test_the_mtime_assertion_can_SEE_a_normalised_tar(self, store: Path):
        """Positive control for the test above: build the tar the WRONG way and
        watch the same comparison fail. Without this, a snapshot that happened to
        preserve mtimes and one whose mtimes were never checked look alike."""
        entries = sorted(store.glob(f"{SCOPE}/*.md"))
        os.utime(entries[0], (1_700_000_000, 1_700_000_000))
        want = int(entries[0].stat().st_mtime)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(f"{SCOPE}/{entries[0].name}")
            info.size = entries[0].stat().st_size
            info.mtime = 0  # the mutation: normalised, as a "reproducible" tar would
            with entries[0].open("rb") as fh:
                tar.addfile(info, fh)
        members = self._members(buf.getvalue())
        got = members[f"{SCOPE}/{entries[0].name}"].mtime
        assert got != want, "the control cannot distinguish a normalised tar"

    def test_scope_filter_ships_only_that_scope(self, store: Path):
        with running(store) as (base, _):
            _c, _h, body = fetch(
                f"{base}/api/v1/snapshot?scope={SCOPE}", token=GOOD_TOKEN
            )
        names = [n for n in self._members(body) if n.endswith(".md")]
        assert names, "filtered snapshot shipped nothing"
        assert all(n.startswith(f"{SCOPE}/") for n in names), names

    def test_an_invalid_scope_is_a_400_not_a_traversal(self, store: Path):
        with running(store) as (base, _):
            code, headers, _b = fetch(
                f"{base}/api/v1/snapshot?scope=../../etc", token=GOOD_TOKEN
            )
        assert code == 400
        assert headers["X-Store-Status"] == "bad-request"

    def test_it_requires_a_token(self, store: Path):
        with running(store) as (base, _):
            code, _h, _b = fetch(f"{base}/api/v1/snapshot")
        assert code == 401

    def test_it_ships_no_dot_dirs_and_no_non_markdown(self, store: Path):
        (store / SCOPE / "notes.txt").write_text("not an entry")
        (store / ".git").mkdir(exist_ok=True)
        (store / ".git" / "HEAD").write_text("ref: refs/heads/main")
        with running(store) as (base, _):
            _c, _h, body = fetch(f"{base}/api/v1/snapshot", token=GOOD_TOKEN)
        names = set(self._members(body))
        assert not any(n.startswith(".git") for n in names), names
        assert not any(n.endswith(".txt") for n in names), names

    def test_taking_a_snapshot_leaves_the_store_BYTE_IDENTICAL(self, store: Path):
        before = tree_hash(store)
        with running(store) as (base, _):
            fetch(f"{base}/api/v1/snapshot", token=GOOD_TOKEN)
            fetch(f"{base}/api/v1/snapshot?scope={SCOPE}", token=GOOD_TOKEN)
        assert tree_hash(store) == before


class TestSearchOverHTTP:
    def test_POSITIVE_a_query_that_must_hit_DOES(self, store: Path):
        with running(store) as (base, _):
            code, headers, body = fetch(
                f"{base}/api/v1/search/{SCOPE}?q=readiness+probe", token=GOOD_TOKEN
            )
        assert code == 200
        assert headers["X-Store-Status"] == "search-hit"
        assert b"readiness probe" in body

    def test_NEGATIVE_a_query_that_must_MISS_reports_no_match_not_a_hit(
        self, store: Path
    ):
        # Reported beside the hit above: a zero from a searcher never seen to
        # return non-zero is indistinguishable from a searcher wired to nothing.
        with running(store) as (base, _):
            code, headers, _b = fetch(
                f"{base}/api/v1/search/{SCOPE}?q=zzqqxx+nonesuch", token=GOOD_TOKEN
            )
        assert code == 200
        assert headers["X-Store-Status"] == "search-no-match"

    def test_a_missing_query_is_a_400_not_an_empty_search(self, store: Path):
        with running(store) as (base, _):
            code, headers, body = fetch(f"{base}/api/v1/search/{SCOPE}", token=GOOD_TOKEN)
        assert code == 400
        assert headers["X-Store-Status"] == "bad-request"
        assert b"q is required" in body


class TestQueryParamsNeverSilentlyDefault:
    """A `?limit=abc` that quietly became the default is a caller believing a
    setting took effect — the class `subsystem_recall.main` rejects flag
    combinations for.
    """

    @pytest.mark.parametrize(
        "query,needle",
        [
            ("limit=abc", b"limit must be an integer"),
            ("page=two", b"page must be an integer"),
            ("mode=sideways", b"mode must be one of"),
            ("limit=0", b"limit must be an int >= 1"),
            ("page=0", b"page must be an int >= 1"),
        ],
    )
    def test_a_bad_parameter_is_a_400_naming_the_parameter(
        self, store: Path, query: str, needle: bytes
    ):
        with running(store) as (base, _):
            code, headers, body = fetch(
                f"{base}/api/v1/recall/{SCOPE}?{query}", token=GOOD_TOKEN
            )
        assert code == 400
        assert headers["X-Store-Status"] == "bad-request"
        assert needle in body

    def test_a_GOOD_parameter_still_works(self, store: Path):
        # The controls above are only evidence if the same shape can succeed.
        with running(store) as (base, _):
            code, headers, _b = fetch(
                f"{base}/api/v1/recall/{SCOPE}?limit=3&mode=full", token=GOOD_TOKEN
            )
        assert code == 200
        assert headers["X-Store-Status"] == "recalled"


# =============================================================================
# 8. The revision header — "unknown" rather than a fabricated sha.
# =============================================================================


class TestScopeRevision:
    def _git(self, path: Path, *args: str):
        subprocess.run(
            ["git", "-C", str(path), *args],
            check=True,
            capture_output=True,
            env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "HOME": str(path),
                 **hermetic_git.MAINTENANCE_OFF},
        )

    def test_a_real_scope_repo_yields_its_HEAD_sha(self, store: Path):
        scope_dir = store / SCOPE
        self._git(scope_dir, "init", "-q", "-b", "main")
        self._git(scope_dir, "config", "user.email", "t@example.invalid")
        self._git(scope_dir, "config", "user.name", "T")
        self._git(scope_dir, "add", "thing-alpha.md")
        self._git(scope_dir, "commit", "-qm", "seed")
        head = subprocess.run(
            ["git", "-C", str(scope_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        assert api.scope_revision(store, SCOPE) == head
        with running(store) as (base, _):
            _c, headers, _b = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
        assert headers["X-Store-Revision"] == head

    def test_a_scope_with_no_repo_reports_unknown_NOT_a_made_up_sha(self, store: Path):
        assert api.scope_revision(store, OTHER_SCOPE) == "unknown"

    def test_an_absent_scope_reports_unknown(self, store: Path):
        assert api.scope_revision(store, "never-heard-of-it") == "unknown"

    def test_a_dangling_ref_reports_unknown(self, store: Path):
        git = store / SCOPE / ".git"
        git.mkdir(parents=True)
        (git / "HEAD").write_text("ref: refs/heads/nowhere\n")
        assert api.scope_revision(store, SCOPE) == "unknown"

    def test_a_packed_ref_is_resolved(self, store: Path):
        git = store / SCOPE / ".git"
        git.mkdir(parents=True)
        (git / "HEAD").write_text("ref: refs/heads/main\n")
        (git / "packed-refs").write_text(
            "# pack-refs with: peeled fully-peeled sorted\n"
            "1111111111111111111111111111111111111111 refs/heads/main\n"
        )
        assert api.scope_revision(store, SCOPE) == "1" * 40


# =============================================================================
# 8b. The snapshot stamp — every report dates the COPY it is serving.
#
# 🔴 REGRESSION, NOT AN INVARIANT GUARD. Measured on the live public endpoint
# 2026-08-20, four days after cutover: an authed GET returned 200 with
# `ALL 5 entries in devrc/, none omitted` while the source held 9, and one
# served entry was a 40-day-old copy of a file edited that morning. Every
# existing check passed — reachability, auth, client-IP chain, firewall — because
# none of them compares the served bytes to the source. The defect was that a
# stale snapshot and the live source produce byte-identical-looking answers.
#
# 🔴 WHICH OF THESE ARE REGRESSION TESTS, HONESTLY — 3 of 11, not 11.
# Measured by running this class against `main` at `19756d5`: 11 failed, but
# only THREE failed on BEHAVIOUR. The other eight raise `AttributeError: module
# has no attribute 'snapshot_freshness' / 'SEED_STAMP_NAME'`, which is the
# symbol not existing, not the defect being caught — the same "red at base is a
# collection error and proves nothing" this file's own header records about
# `server.py`. Those eight are INVARIANT GUARDS on the four-state contract; they
# are worth having and they are not evidence.
#
# The three that genuinely bite, each with an assertion failure at base:
#   * test_a_report_that_does_not_date_itself_is_the_regression  (body undated)
#   * test_the_stamp_is_on_search_too_not_only_recall            (body undated)
#   * test_seed_sh_writes_a_stamp_and_puts_it_IN_THE_ARCHIVE     (no stamp)
# =============================================================================


class TestSnapshotStamp:
    def _fresh(self, store: Path):
        return api.snapshot_freshness(store)

    def test_a_report_that_does_not_date_itself_is_the_regression(self, store: Path):
        """The whole point: the body — not just a header — must say SNAPSHOT.

        Asserted on the BODY because the measured failure was an agent reading
        rendered text; a caller that pipes the body never sees a header.
        """
        with running(store) as (base, _):
            _c, _h, body = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
        text = body.decode()
        assert "SNAPSHOT, NOT THE SOURCE" in text
        # It must come BEFORE the completeness claim it qualifies.
        assert text.index("SNAPSHOT, NOT THE SOURCE") < text.index("none omitted")

    def test_the_stamp_is_on_search_too_not_only_recall(self, store: Path):
        """Both routes go through `_serve_report`; pin that they both stamp.

        If a future route stamps only recall, the copy it serves is undated on
        exactly the surface a caller reaches for when an entry seems missing.
        """
        with running(store) as (base, _):
            _c, headers, body = fetch(
                f"{base}/api/v1/search/{SCOPE}?q=alpha", token=GOOD_TOKEN
            )
        assert "SNAPSHOT, NOT THE SOURCE" in body.decode()
        assert "X-Store-Snapshot" in headers

    def test_newest_entry_is_the_newest_mtime_and_MOVES_when_content_changes(
        self, store: Path
    ):
        """A value that cannot move is indistinguishable from a hardcoded string.

        Feeds a timestamp the fixture CANNOT already equal and watches the
        output follow it (RULES.md: the mechanical control for a constant).
        """
        header_before, _ = self._fresh(store)
        target = time.time() + 86_400  # a day ahead: no fixture file can hold it
        os.utime(store / SCOPE / "thing-alpha.md", (target, target))
        header_after, _ = self._fresh(store)

        assert header_before != header_after
        stamp = datetime.fromtimestamp(target, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        assert f"newest={stamp}" in header_after

    def test_entry_files_counts_entries_and_not_the_stamp_file(self, store: Path):
        """The count must be of *.md, or it drifts against seed.sh's own number.

        `.seed-stamp` is not an entry; counting it would make the API and the
        seeder disagree by exactly one and read as a lost file.
        """
        header_before, _ = self._fresh(store)
        assert "entry-files=3" in header_before
        (store / api.SEED_STAMP_NAME).write_text("2026-08-20T00:00:00Z\n")
        header_after, _ = self._fresh(store)
        assert "entry-files=3" in header_after

    def test_a_seeded_stamp_is_read_and_reported(self, store: Path):
        (store / api.SEED_STAMP_NAME).write_text(
            "2026-08-20T17:30:00Z staged_entries=71 host=box\n"
        )
        header, prose = self._fresh(store)
        assert "seeded=2026-08-20T17:30:00Z staged_entries=71 host=box" in header
        assert "UNSTAMPED" not in header

    def test_an_ABSENT_stamp_is_named_never_omitted(self, store: Path):
        """The failure this whole block exists to prevent is a SILENT absence."""
        header, prose = self._fresh(store)
        assert "seeded=UNSTAMPED" in header
        assert "SNAPSHOT, NOT THE SOURCE" in prose

    def test_an_EMPTY_stamp_is_UNREADABLE_and_distinct_from_absent(
        self, store: Path
    ):
        """Two mechanisms, two names — an empty file is not a missing one."""
        (store / api.SEED_STAMP_NAME).write_text("   \n")
        header, _ = self._fresh(store)
        assert "seeded=UNREADABLE" in header
        assert "UNSTAMPED" not in header

    def test_an_EMPTY_store_says_NONE_and_zero_not_a_fabricated_date(
        self, tmp_path: Path
    ):
        """`newest=NONE entry-files=0` must be distinguishable from UNREADABLE."""
        empty = tmp_path / "empty-store"
        empty.mkdir()
        header, _ = self._fresh(empty)
        assert "newest=NONE" in header
        assert "entry-files=0" in header
        assert "UNREADABLE" not in header

    def test_an_UNREADABLE_scope_is_UNREADABLE_not_an_empty_store(
        self, store: Path
    ):
        """🔴 The two zeros that must never be confused.

        A store that cannot be WALKED and a store that is genuinely EMPTY both
        yield "no entries". This file's own header calls that out; the stamp
        would be worthless if it collapsed them.
        """
        if os.geteuid() == 0:
            pytest.skip("root ignores directory permissions; the case is unreachable")
        locked = store / SCOPE
        mode = locked.stat().st_mode
        locked.chmod(0o000)
        try:
            header, _ = self._fresh(store)
        finally:
            locked.chmod(mode)
        assert "UNREADABLE" in header
        assert "newest=NONE" not in header

    def test_the_header_and_the_prose_carry_the_SAME_facts(self, store: Path):
        """One derivation, two renderings — they must not drift apart.

        A header saying `seeded=UNSTAMPED` beside prose implying a known date is
        the shape where a reader believes whichever they happened to read.
        """
        (store / api.SEED_STAMP_NAME).write_text("2026-08-20T17:30:00Z\n")
        header, prose = self._fresh(store)
        for field in ("2026-08-20T17:30:00Z", "entry-files=3"):
            assert field in header
            assert field.replace("entry-files=", "entry-files=") in prose

    def test_seed_sh_writes_a_stamp_and_puts_it_IN_THE_ARCHIVE(
        self, tmp_path: Path, store: Path
    ):
        """🔴 The member list globs `*/` — directories only.

        A top-level stamp file is silently dropped from the tar unless named,
        which would push undated content while reporting OK. Drives the real
        script's stage half, then asserts the stamp is both written and listed
        as a tar member by the script's own source.
        """
        stage = tmp_path / "stage"
        seed = API_DIR / "seed.sh"
        proc = subprocess.run(
            [str(seed), "--store", str(store), "--stage", str(stage)],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        stamp = stage / ".seed-stamp"
        assert stamp.exists(), "seed.sh staged no .seed-stamp"
        assert re.match(
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z staged_entries=\d+ host=",
            stamp.read_text().strip(),
        ), stamp.read_text()
        # The stage half never tars; pin the member list from the source, since
        # this is exactly the line whose omission is silent.
        assert 'members+=(".seed-stamp")' in seed.read_text()


# =============================================================================
# 9. The audit log — §2b: "timestamp, path, token id (not the token), result".
# =============================================================================


class TestAuditLog:
    def test_every_api_request_writes_exactly_one_line(self, store: Path):
        with running(store) as (base, audit):
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
            fetch(f"{base}/api/v1/recall/{SCOPE}")  # rejected
        assert len(audit) == 2

    def test_health_is_NOT_audited(self, store: Path):
        # It is unauthenticated and says nothing; logging it would bury the
        # /api/* lines the log exists for under kubelet probe traffic.
        with running(store) as (base, audit):
            fetch(f"{base}/healthz")
        assert audit == []

    def test_the_line_carries_timestamp_path_result_and_a_token_ID(self, store: Path):
        with running(store) as (base, audit):
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
        line = audit[0]
        assert "ts=2" in line
        assert f"path=/api/v1/recall/{SCOPE}" in line
        assert "result=200" in line
        assert "auth=ok" in line
        assert f"token={api.token_id(GOOD_TOKEN)}" in line

    def test_the_log_NEVER_contains_the_token_itself(self, store: Path):
        with running(store) as (base, audit):
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
            fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)
        joined = "\n".join(audit)
        assert GOOD_TOKEN not in joined
        assert "w" * 48 not in joined, "a rejected token was echoed into the log"

    def test_a_rejected_request_is_logged_as_a_FAILURE_with_no_token_id(
        self, store: Path
    ):
        with running(store) as (base, audit):
            fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)
        assert "auth=fail" in audit[0]
        assert "token=-" in audit[0]
        assert "result=401" in audit[0]

    def test_the_token_id_is_a_DIGEST_not_a_prefix_of_the_token(self):
        tid = api.token_id(GOOD_TOKEN)
        assert len(tid) == 12
        assert tid not in GOOD_TOKEN
        assert api.token_id(GOOD_TOKEN) == api.token_id(GOOD_TOKEN)
        assert api.token_id(GOOD_TOKEN) != api.token_id(GOOD_TOKEN + "x")


# =============================================================================
# 10. The seed — 🔴 the local store is the ONLY copy.
# =============================================================================


def run_seed(*args: str) -> subprocess.CompletedProcess:
    # `bash <script>` rather than the shebang: `/usr/bin/env` does not exist in
    # the nix sandbox that gates merges (see test_runtime_shebangs.py).
    return subprocess.run(
        ["bash", str(SEED_PATH), *args], capture_output=True, text=True, timeout=120
    )


class TestSeedIsNonDestructive:
    def test_the_SOURCE_tree_is_byte_identical_after_a_seed(
        self, store: Path, tmp_path: Path
    ):
        before = tree_hash(store)
        result = run_seed("--store", str(store), "--stage", str(tmp_path / "stage"))
        assert result.returncode == 0, result.stderr
        assert tree_hash(store) == before

    def test_POSITIVE_CONTROL_the_hasher_sees_a_one_character_change(self, store: Path):
        """🔴 Reported BESIDE the verdict above. "unchanged" from a hasher never
        watched to change is indistinguishable from a hasher wired to nothing."""
        before = tree_hash(store)
        path = store / SCOPE / "thing-alpha.md"
        path.write_text(path.read_text().replace("40s", "41s"))
        assert tree_hash(store) != before

    def test_seeding_over_a_populated_stage_removes_only_STAGE_files(
        self, store: Path, tmp_path: Path
    ):
        stage = tmp_path / "stage"
        stage.mkdir()
        (stage / "leftover-from-an-older-run.md").write_text("stale\n")
        before = tree_hash(store)
        assert run_seed("--store", str(store), "--stage", str(stage)).returncode == 0
        assert not (stage / "leftover-from-an-older-run.md").exists()
        assert tree_hash(store) == before

    def test_the_stage_is_a_faithful_copy_APART_FROM_THE_STAMP(
        self, store: Path, tmp_path: Path
    ):
        """The stage mirrors the source byte-for-byte, plus EXACTLY one file.

        🔴 THIS ASSERTION GOT NARROWER, NOT WEAKER, AND THAT IS THE POINT.
        It used to be a bare `tree_hash(stage) == tree_hash(store)`, which
        `.seed-stamp` breaks: the stage is deliberately no longer a pure
        byte-copy, because a copy that cannot say when it was taken is the
        entire defect the stamp exists to fix (`server.snapshot_freshness`, and
        the incident in the README). RULES.md — "when a test documents a
        contract, ask whether the contract is right": the contract changed, so
        the test states the NEW one exactly rather than being loosened to
        "mostly the same", which would have surrendered the property actually
        worth keeping — that nothing ELSE ever appears in the stage.

        So: the extra-path set is pinned to exactly `{.seed-stamp}` (it fails if
        that set GROWS *or* SHRINKS), and with the stamp removed the remaining
        tree is still hashed byte-for-byte against the source.
        """
        stage = tmp_path / "stage"
        assert run_seed("--store", str(store), "--stage", str(stage)).returncode == 0

        stamp = stage / ".seed-stamp"
        assert stamp.exists(), "the stage carries no stamp — seed.sh did not date it"

        staged = {p.relative_to(stage) for p in stage.rglob("*") if p.is_file()}
        source = {p.relative_to(store) for p in store.rglob("*") if p.is_file()}
        assert staged - source == {Path(".seed-stamp")}, (
            f"unexpected extra path(s) in the stage: {staged - source}"
        )
        assert not source - staged, f"the stage is MISSING: {source - staged}"

        stamp.unlink()
        assert tree_hash(stage) == tree_hash(store)

    def test_the_summary_prints_the_COUNT_beside_what_produced_it(
        self, store: Path, tmp_path: Path
    ):
        out = run_seed("--store", str(store), "--stage", str(tmp_path / "stage")).stdout
        assert "seed: STAGED scopes=4 entries=3" in out
        assert f"from={store}" in out

    def test_a_run_without_push_SAYS_it_proved_nothing_about_a_pod(
        self, store: Path, tmp_path: Path
    ):
        out = run_seed("--store", str(store), "--stage", str(tmp_path / "stage")).stdout
        assert "PUSH skipped" in out
        assert "proves nothing about any pod" in out


class TestSeedGuards:
    """Each guard reachable by an input no earlier guard rejects, each with its
    OWN exit code and sentence — so a test cannot pass because a NEIGHBOURING
    guard fired."""

    def test_an_absent_store_root_exits_3_and_says_nothing_was_pushed(
        self, tmp_path: Path
    ):
        r = run_seed("--store", str(tmp_path / "nope"), "--stage", str(tmp_path / "s"))
        assert r.returncode == 3
        assert "store root not found" in r.stderr
        assert "nothing was pushed" in r.stderr
        assert not (tmp_path / "s").exists(), "an absent source must stage NOTHING"

    def test_an_EXISTING_but_scopeless_root_exits_4_not_3(self, tmp_path: Path):
        # Guard 1 passes here — the directory exists. This is the silent-wipe
        # case: staging an empty tree over a populated /data.
        empty = tmp_path / "empty-root"
        empty.mkdir()
        r = run_seed("--store", str(empty), "--stage", str(tmp_path / "s"))
        assert r.returncode == 4
        assert "NO scope directories" in r.stderr
        assert "refusing" in r.stderr

    def test_missing_arguments_exit_2(self, tmp_path: Path):
        assert run_seed("--stage", str(tmp_path / "s")).returncode == 2
        assert run_seed("--store", str(tmp_path)).returncode == 2

    def test_a_bad_push_target_is_rejected_BEFORE_any_kubectl_call(
        self, store: Path, tmp_path: Path
    ):
        r = run_seed(
            "--store", str(store), "--stage", str(tmp_path / "s"), "--push", "no-slash"
        )
        assert r.returncode == 2
        assert "namespace" in r.stderr


class TestSeedNeverAddsARemote:
    """🔴 THE POLICY THIS MIGRATION MUST NOT QUIETLY BECOME.

    The store's README forbids a git remote on any scope, and three tests in
    `test_analyze_service_index_commit.py` enforce it. Replication here happens
    over HTTP, not `git push` — so a seed must leave both the source AND the
    staged copy with zero remotes. This is the behavioural check; the three
    existing tests are untouched by this branch.
    """

    def _remotes(self, path: Path) -> str:
        return subprocess.run(
            ["git", "-C", str(path), "remote"], capture_output=True, text=True
        ).stdout.strip()

    def test_neither_the_source_nor_the_stage_gains_a_remote(
        self, store: Path, tmp_path: Path
    ):
        scope_dir = store / SCOPE
        env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "HOME": str(tmp_path),
               **hermetic_git.MAINTENANCE_OFF}
        for args in (
            ["init", "-q", "-b", "main"],
            ["config", "user.email", "t@example.invalid"],
            ["config", "user.name", "T"],
            ["add", "thing-alpha.md"],
            ["commit", "-qm", "seed"],
        ):
            subprocess.run(
                ["git", "-C", str(scope_dir), *args], check=True, capture_output=True,
                env=env,
            )
        assert self._remotes(scope_dir) == ""

        stage = tmp_path / "stage"
        assert run_seed("--store", str(store), "--stage", str(stage)).returncode == 0

        assert self._remotes(scope_dir) == "", "the SOURCE gained a remote"
        assert self._remotes(stage / SCOPE) == "", "the STAGE gained a remote"

    def test_the_remote_probe_can_SEE_a_remote(self, store: Path, tmp_path: Path):
        """Positive control: an empty `git remote` from a probe that never works
        is indistinguishable from a repo with no remotes."""
        scope_dir = store / OTHER_SCOPE
        env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "HOME": str(tmp_path),
               **hermetic_git.MAINTENANCE_OFF}
        subprocess.run(
            ["git", "-C", str(scope_dir), "init", "-q", "-b", "main"],
            check=True, capture_output=True, env=env,
        )
        subprocess.run(
            ["git", "-C", str(scope_dir), "remote", "add", "origin",
             "https://example.invalid/x.git"],
            check=True, capture_output=True, env=env,
        )
        assert self._remotes(scope_dir) == "origin"


# =============================================================================
# 11. 🔴 THE PHASE-1 ACCEPTANCE COMPARATOR, exercised in BOTH directions.
# =============================================================================


def run_verify(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(VERIFY_PATH), *args], capture_output=True, text=True, timeout=300
    )


@pytest.fixture
def token_file(tmp_path: Path) -> Path:
    path = tmp_path / "token"
    path.write_text(GOOD_TOKEN + "\n")
    path.chmod(0o600)
    return path


class TestByteIdentityVerifier:
    def test_POSITIVE_identical_stores_PASS_for_every_scope(
        self, store: Path, token_file: Path
    ):
        with running(store) as (base, _):
            r = run_verify(
                "--store", str(store), "--url", base, "--token-file", str(token_file)
            )
        assert r.returncode == 0, r.stdout + r.stderr
        # Every scope compared, and the count printed BESIDE the verdict.
        assert "verify: scopes=4 pass=4 fail=0" in r.stdout
        for scope in (SCOPE, OTHER_SCOPE, EMPTY_SCOPE, BROKEN_SCOPE):
            assert f"PASS scope={scope}" in r.stdout

    def test_NEGATIVE_a_ONE_CHARACTER_divergence_FAILS_and_names_the_scope(
        self, store: Path, tmp_path: Path, token_file: Path
    ):
        """🔴 The control that makes the PASS above mean anything.

        The served copy differs from the local one by a single character inside
        one entry, in one scope. A comparator that always says PASS, or that
        compares the wrong thing, is green here.
        """
        served = tmp_path / "served"
        subprocess.run(
            ["cp", "-a", str(store), str(served)], check=True, capture_output=True
        )
        path = served / SCOPE / "thing-alpha.md"
        path.write_text(path.read_text().replace("40s", "41s"))

        with running(served) as (base, _):
            r = run_verify(
                "--store", str(store), "--url", base, "--token-file", str(token_file)
            )
        assert r.returncode == 1
        assert f"FAIL scope={SCOPE}" in r.stdout
        # The other scopes are still identical, so the failure is attributed and
        # not a blanket red.
        assert f"PASS scope={OTHER_SCOPE}" in r.stdout
        assert "pass=3 fail=1" in r.stdout

    def test_NEGATIVE_a_MISSING_entry_on_the_remote_FAILS(
        self, store: Path, tmp_path: Path, token_file: Path
    ):
        # A seed that half-copied. Different shape from the mutation above:
        # nothing is wrong with any served byte, there is simply less of it.
        served = tmp_path / "served"
        subprocess.run(
            ["cp", "-a", str(store), str(served)], check=True, capture_output=True
        )
        (served / OTHER_SCOPE / "thing-beta.md").unlink()
        with running(served) as (base, _):
            r = run_verify(
                "--store", str(store), "--url", base, "--token-file", str(token_file)
            )
        assert r.returncode == 1
        assert f"FAIL scope={OTHER_SCOPE}" in r.stdout
        # 🔴 AND it kept going. A failing scope must not abort the sweep: the
        # first version of this script died inside `diff | head` under
        # `set -o pipefail` and reported one FAIL with three scopes silently
        # uncompared, which reads as a narrower problem than it is.
        assert "verify: scopes=4" in r.stdout
        assert "pass=3 fail=1" in r.stdout

    def test_every_permitted_difference_is_ACCOUNTED_FOR_not_merely_small(
        self, store: Path, tmp_path: Path, token_file: Path
    ):
        """The pod serves `/data`; the workbench serves `~/.claude/…`.

        🔴 RENAMED, BECAUSE THE OLD NAME BECAME FALSE. This was
        `test_the_STORE_ROOT_line_is_the_only_permitted_difference`, asserting a
        flat `raw-diff-lines=2 store-root-lines=2`. The snapshot block
        (`server.snapshot_freshness`) is a SECOND legitimate difference — the
        remote dates the copy it serves and the local CLI, reading the
        authoritative store, correctly does not — so "the only permitted
        difference" stopped being true the moment that shipped. RULES.md: "a
        comment is a claim too"; a name is louder than a comment.

        The replacement is STRONGER than a bumped constant. It asserts the raw
        difference is FULLY DECOMPOSED by its two named causes:

            raw == store_root_lines + 2 * snapshot_lines

        (the block contributes its prose line AND its blank separator, and both
        appear one-sided in the diff). An unexplained differing line therefore
        still fails — which a hardcoded `raw-diff-lines=4` would not, since it
        would go on passing if a store-root line vanished and some other
        difference appeared in its place.
        """
        served = tmp_path / "served-elsewhere"
        subprocess.run(
            ["cp", "-a", str(store), str(served)], check=True, capture_output=True
        )
        with running(served) as (base, _):
            r = run_verify(
                "--store", str(store), "--url", base, "--token-file", str(token_file)
            )
        assert r.returncode == 0, r.stdout + r.stderr

        rows = re.findall(
            r"raw-diff-lines=(\d+) store-root-lines=(\d+) snapshot-line=(\d+)",
            r.stdout,
        )
        assert rows, f"the verifier printed no evidence triples:\n{r.stdout}"
        for raw, store_root, snapshot in rows:
            assert int(raw) == int(store_root) + 2 * int(snapshot), (
                f"unaccounted differing lines: raw={raw} "
                f"store-root={store_root} snapshot={snapshot}"
            )
        # …and the two causes are each genuinely PRESENT, or the identity above
        # is satisfiable by a run that compared nothing (0 == 0 + 2*0).
        assert any(int(s) == 1 for _r, _sr, s in rows), "no snapshot line observed"
        assert any(int(sr) == 2 for _r, sr, _s in rows), "no store-root line observed"

    def test_an_UNREACHABLE_pod_FAILS_rather_than_comparing_nothing(
        self, store: Path, token_file: Path
    ):
        # Nothing is listening. A comparator that treated an empty body as
        # "identical to an empty local render" would report success here.
        r = run_verify(
            "--store", str(store),
            "--url", "http://127.0.0.1:1",
            "--token-file", str(token_file),
        )
        assert r.returncode == 1
        assert "FAIL" in r.stdout

    def test_a_WRONG_token_FAILS_rather_than_reporting_identity(
        self, store: Path, tmp_path: Path
    ):
        bad = tmp_path / "bad-token"
        bad.write_text("q" * 48 + "\n")
        with running(store) as (base, _):
            r = run_verify(
                "--store", str(store), "--url", base, "--token-file", str(bad)
            )
        assert r.returncode == 1
        assert "remote HTTP 401" in r.stdout

    def test_a_200_with_an_EMPTY_body_FAILS_rather_than_comparing_equal(
        self, store: Path, token_file: Path
    ):
        """🔴 Two empty streams `cmp` equal. That is the shape in which a proxy,
        a misrouted ingress or a half-written response reads as byte-identity.

        The stub answers 200 to everything with a zero-length body — a realistic
        failure, not a textbook one: it is what a Traefik route pointed at the
        wrong service returns.
        """
        import http.server

        class Blank(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):  # noqa: D102
                return

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Blank)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            r = run_verify(
                "--store", str(store),
                "--url", f"http://127.0.0.1:{httpd.server_address[1]}",
                "--token-file", str(token_file),
            )
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=10)
        assert r.returncode == 1, r.stdout
        assert "empty render" in r.stdout
        assert "pass=0 fail=4" in r.stdout

    def test_ZERO_scopes_is_a_FAILURE_not_an_all_clear(
        self, tmp_path: Path, token_file: Path
    ):
        """🔴 The silent zero. A comparison over no scopes passes trivially, and
        `pass=0 fail=0` reads exactly like success."""
        empty = tmp_path / "empty-store"
        empty.mkdir()
        r = run_verify(
            "--store", str(empty),
            "--url", "http://127.0.0.1:1",
            "--token-file", str(token_file),
        )
        assert r.returncode == 4
        assert "nothing was compared" in r.stderr

    def test_missing_inputs_are_refused(self, store: Path, token_file: Path):
        assert run_verify("--url", "http://x", "--token-file", str(token_file)).returncode == 2
        assert run_verify("--store", str(store), "--token-file", str(token_file)).returncode == 2
        assert run_verify("--store", str(store), "--url", "http://x").returncode == 2


# =============================================================================
# 12. The end-to-end shape phase 1 actually ships: seed, then verify.
# =============================================================================


class TestSeedThenVerify:
    def test_a_seeded_copy_serves_byte_identical_digests(
        self, store: Path, tmp_path: Path, token_file: Path
    ):
        """The phase-1 acceptance path in miniature, with the real scripts.

        Not a substitute for running it against the pod — a `--stage` directory
        is not a PVC and this machine is not the cluster — but it proves the two
        scripts compose, which is the seam neither of them owns alone.
        """
        stage = tmp_path / "stage"
        seeded = run_seed("--store", str(store), "--stage", str(stage))
        assert seeded.returncode == 0, seeded.stderr

        with running(stage) as (base, _):
            r = run_verify(
                "--store", str(store), "--url", base, "--token-file", str(token_file)
            )
        assert r.returncode == 0, r.stdout + r.stderr
        assert "pass=4 fail=0" in r.stdout

    def test_a_seed_that_MISSED_a_scope_is_caught_by_the_verifier(
        self, store: Path, tmp_path: Path, token_file: Path
    ):
        # The composition's own negative control: the two scripts agreeing is
        # only evidence if a broken seed makes them disagree.
        stage = tmp_path / "stage"
        assert run_seed("--store", str(store), "--stage", str(stage)).returncode == 0
        subprocess.run(
            ["rm", "-rf", str(stage / OTHER_SCOPE)], check=True, capture_output=True
        )
        with running(stage) as (base, _):
            r = run_verify(
                "--store", str(store), "--url", base, "--token-file", str(token_file)
            )
        assert r.returncode == 1
        assert f"FAIL scope={OTHER_SCOPE}" in r.stdout


# =============================================================================
# 13. Phase scope — what this branch must NOT contain.
# =============================================================================


class TestPhaseOneScope:
    """🔴 Phase 1 is cluster-internal: read-only, no ingress, no write path.

    §4 phase 1.5 is explicit that the IngressRoute is "the last thing to land,
    not the first" — it is the moment the store becomes internet-reachable. This
    guard is structural (it reads the shipped files), so a write endpoint or an
    exposure added here fails a test rather than a review.
    """

    def test_the_server_declares_no_write_handler(self):
        src = SERVER_PATH.read_text()
        # `do_POST` etc. exist ONLY as aliases of the 405 rejecter.
        assert "do_POST = do_PUT = do_PATCH = do_DELETE = _reject_write" in src
        for handler in ("def do_POST", "def do_PUT", "def do_PATCH", "def do_DELETE"):
            assert handler not in src

    # 🔴 THE LEDGER. Adding a route means adding it HERE, on purpose, in the
    # same commit. That is the whole point of the guard below.
    ROUTES: tuple[str, ...] = ("recall", "search", "snapshot")

    def test_the_route_ledger_is_the_whole_route_set(self, store: Path):
        """🔴 REWRITTEN TWICE, and the FIRST rewrite was still a spelled guard.

        v1 was named `test_the_only_routes_are_recall_and_search` and claimed to
        "walk the endpoint list". It probed four hardcoded non-routes, so adding
        `/api/v1/snapshot` left every test in this class green.

        v2 replaced that with `re.findall(r'parts\\[0\\]\\s*==\\s*"([a-z0-9-]+)"')`
        and was described in its PR as "derives the accepted set from the
        router". It does not — it derives the set from ONE SPELLING. An audit
        MEASURED the hole: adding `parts[0] == "raw_dump"` (an underscore, which
        the character class excludes) left all six tests in this class PASSING.
        Single quotes, `parts[0] in (...)`, reversed operands, uppercase and
        dict dispatch walk past it too. Its "positive control" fed it the one
        spelling it catches, so the control could not reveal any of that.

        v3 parses the ROUTER'S AST and collects every string compared against a
        `parts[...]` subscript — `==` in either operand order, and `in` over a
        tuple/list. Spelling is now irrelevant: quotes, case and underscores are
        all the same node to `ast`.

        🔴 WHAT THIS STILL CANNOT SEE, stated rather than implied: a route whose
        name never appears as a literal in a comparison against `parts` — a dict
        or table dispatch (`ROUTES[parts[0]]`), a computed name, or a
        `startswith` prefix match. `test_no_table_dispatch_on_parts` below closes
        the table case; a computed name remains uncovered, and the behavioural
        test cannot cover it either because it cannot guess the name.
        """
        assert set(api.API_ROUTES) == set(self.ROUTES), (
            f"router dispatches {sorted(api.API_ROUTES)} but the ledger says "
            f"{sorted(self.ROUTES)} — add it to ROUTES on purpose, or remove it"
        )

    @pytest.mark.parametrize(
        "path",
        [
            f"/api/v1/recall/{SCOPE}/extra",   # ledgered head, too many parts
            "/api/v1/recall",                  # ledgered head, too few
            "/api/v1/snapshot/anything/at/all",
            "/api/v1/search",
        ],
    )
    def test_a_ledgered_head_with_the_WRONG_arity_404s(self, store: Path, path: str):
        """🔴 The dispatcher's one numeric field had NO test.

        `if len(parts) == arity` mutated to `>=` SURVIVED all 318 tests, and
        that mutant serves `200 recalled` for `/recall/<scope>/extra` and
        `200 snapshot` for `/snapshot/anything/at/all`. The existing
        `test_anything_outside_the_ledger_404s` only probes heads OUTSIDE the
        table, so a ledgered head with the wrong component count was unreachable
        by every guard in this file. Arity is the table's other half.
        """
        with running(store) as (base, _):
            code, headers, _b = fetch(f"{base}{path}", token=GOOD_TOKEN)
        assert code == 404, f"{path} answered {code}"
        assert headers["X-Store-Status"] == "no-route"

    def test_every_ledgered_route_actually_dispatches(self):
        """Structural companion: the table's handlers must EXIST and be bound.

        A table is only as good as its rows — a typo'd handler name would make a
        ledgered route 500 rather than serve, and the equality check above
        cannot see that.
        """
        for name, (handler, arity) in api.API_ROUTES.items():
            assert hasattr(api.StoreRequestHandler, handler), f"{name} -> missing {handler}"
            assert arity >= 1, f"{name} has arity {arity}"

    def test_anything_outside_the_ledger_404s(self, store: Path):
        """Behavioural companion to the structural ledger above.

        The ledger reads the source; this proves the server actually refuses.
        Both are needed: a structural check type-checks past a router that
        accepts a name it never dispatches, and a behavioural sample cannot see
        a route it did not think to name.
        """
        with running(store) as (base, _):
            for path in (
                "/api/v1/entry/x/y/bullets",
                "/api/v1/scopes",
                "/api/v1/sync",
                "/api/v1/",
            ):
                code, headers, _b = fetch(f"{base}{path}", token=GOOD_TOKEN)
                assert code == 404, f"{path} answered {code}"
                assert headers["X-Store-Status"] == "no-route"

    def test_the_router_reads_the_table_rather_than_its_own_spelling(self):
        """🔴 WHY THE SOURCE-PARSING LEDGER IS GONE, recorded so nobody rebuilds it.

        Three versions of this guard read the router as TEXT and each was
        defeated by a re-spelling while the whole suite stayed green:

          v1  four hardcoded non-route probes  -> missed `/snapshot` entirely
          v2  regex `parts\\[0\\] == "([a-z0-9-]+)"` -> missed `"raw_dump"`
              (underscore); its own positive control fed it the one spelling it
              caught, so it could not reveal that
          v3  AST walk over comparisons against `parts` -> missed
              `head = parts[0]; head == "x"` and `parts[0] in NAME`, both one
              ordinary refactor away, and it was file-scoped so a rewrite of an
              unrelated `parts` local (server.py has two) would have produced a
              FALSE failure naming a header value as a route

        v3 and its companion `test_no_table_dispatch_on_parts` are BOTH GONE —
        this docstring described them for a round after they were deleted, which
        is the same "reads as coverage while providing none" failure the guard
        itself exists to prevent. What runs now is the assertion below.

        Each fix made the pattern-matching cleverer, which is the wrong axis.
        The route set is now DATA the dispatcher reads (`API_ROUTES`), so
        "what does the router accept" is answered by reading the router's own
        table instead of guessing how it was written. There is no spelling left
        to miss, and no source text to parse.
        """
        src = SERVER_PATH.read_text()
        assert "API_ROUTES.get(parts[0])" in src, (
            "the dispatcher no longer reads API_ROUTES — the ledger test above "
            "would then be asserting against a table nothing uses"
        )

    def test_the_snapshot_route_added_NO_write_verb(self, store: Path):
        """Phase 2 adds a READ route. The write guard above must be untouched by
        it — stated as its own test so "phase 2 stayed read-only" is a checked
        claim rather than a sentence in a commit message."""
        with running(store) as (base, _):
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                code, headers, body = fetch(
                    f"{base}/api/v1/snapshot", token=GOOD_TOKEN, method=method
                )
                assert code == 405, f"{method} answered {code}"
                assert headers["Allow"] == "GET, HEAD"
                assert body == b"read-only\n"

    def test_the_image_copies_every_module_it_needs(self):
        """🔴 THE DOCKERFILE ENUMERATES ITS `COPY`s, AND THE LIST ROTTED SILENTLY.

        `subsystem_touch` gained `from git_mainline import …` in #677
        (2026-08-21). The Dockerfile's hand-written list was not updated, so
        every image built after that commit contained code that could not
        import — while the RUNNING pod stayed healthy, because its image
        predates the change. The defect was therefore invisible from production
        and invisible from CI, and surfaced only when somebody next rebuilt:
        `ModuleNotFoundError: No module named 'git_mainline'`, caught by
        `build-push.sh`'s own import control at deploy time.

        This computes the TRANSITIVE closure of local `scripts/lib` imports from
        the entrypoints and asserts the Dockerfile covers it, so the next added
        import fails here — in CI, on the commit that adds it — rather than at
        the next deploy, which may be months later and someone else's problem.
        """
        lib = ROOT / "scripts" / "lib"
        dockerfile = (API_DIR / "Dockerfile").read_text()

        def local_imports(path: Path) -> set[str]:
            found = set()
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    found.add(node.module.split(".")[0])
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        found.add(alias.name.split(".")[0])
            return {m for m in found if (lib / f"{m}.py").exists()}

        # Entrypoints: what the server imports directly.
        needed, queue = set(), ["subsystem_recall"]
        while queue:
            mod = queue.pop()
            if mod in needed:
                continue
            needed.add(mod)
            queue.extend(local_imports(lib / f"{mod}.py"))

        # 🔴 TWO LISTS, AND CHECKING ONLY ONE IS A GUARD NARROWER THAN THE
        # HAZARD. The first version of this test checked only the COPY lines —
        # and would have passed while the build still failed, because
        # `Dockerfile.dockerignore` is an ALLOWLIST (`**` then explicit `!`
        # unignores) and an un-listed file never reaches the build context at
        # all. Measured: with the COPY added but the ignore-file untouched,
        # `docker build` fails with `"/scripts/lib/git_mainline.py": not found`.
        # Both lists must cover the closure, so both are asserted.
        ignorefile = (API_DIR / "Dockerfile.dockerignore").read_text()
        copied = set(re.findall(r"COPY scripts/lib/(\w+)\.py", dockerfile))
        unignored = set(re.findall(r"!scripts/lib/(\w+)\.py", ignorefile))

        assert not (needed - copied), (
            f"Dockerfile does not COPY {sorted(needed - copied)} — the image "
            f"would build and then fail to import at runtime."
        )
        assert not (needed - unignored), (
            f"Dockerfile.dockerignore does not un-ignore "
            f"{sorted(needed - unignored)} — it is an allowlist, so the file "
            f"never reaches the build context and COPY fails outright."
        )

    def test_nothing_in_this_directory_writes_to_the_store(self):
        for path in sorted(API_DIR.iterdir()):
            if not path.is_file():
                continue
            text = path.read_text()
            assert "git push" not in text, f"{path.name} reaches for git push"
            assert "remote add" not in text, f"{path.name} configures a git remote"


# =============================================================================
# 14. PHASE 1.5 — the (B-required) hardening.
#
# 🔴 WHAT THESE ARE, HONESTLY. `server.py` EXISTS at the base ref, so unlike
# every section above, some of these are real regressions: a request with no
# `CF-Connecting-IP` and a valid token is served 200 at base and 401 here, and a
# valid token after five failures is served 200 at base and refused here. Those
# two are red at base for the RIGHT reason. The token-SET tests are NOT: they
# call `build_server(tokens=…)` / `load_tokens`, which do not exist at base, so
# their red is an API error and proves nothing. The PR body reports which is
# which, and the mutation matrix is the evidence for the second group.
# =============================================================================


SECOND_TOKEN = "q" * 20 + "R" * 20 + "s" * 8  # 48 chars, disjoint from GOOD_TOKEN
THIRD_TOKEN = "m" * 20 + "N" * 20 + "o" * 8


class TestTokenSetAndOverlapRotation:
    """§2b: "Token rotation must be a one-command operation and must be
    exercised once before cutover." Overlap is what makes it one command: the
    server accepts current AND previous, so no client is ever broken.
    """

    def _write(self, tmp_path: Path, *tokens: str) -> str:
        path = tmp_path / "tokens"
        path.write_text("\n".join(tokens) + "\n")
        return str(path)

    def test_two_tokens_load_as_a_set_IN_FILE_ORDER(self, tmp_path: Path):
        path = self._write(tmp_path, GOOD_TOKEN, SECOND_TOKEN)
        assert [r[0] for r in loaded(path, {})] == [GOOD_TOKEN, SECOND_TOKEN]

    def test_a_duplicated_line_collapses_and_order_is_kept(self, tmp_path: Path):
        path = self._write(tmp_path, SECOND_TOKEN, GOOD_TOKEN, SECOND_TOKEN)
        assert [r[0] for r in loaded(path, {})] == [SECOND_TOKEN, GOOD_TOKEN]

    def test_TWO_LEGACY_ROWS_do_NOT_trip_the_duplicate_identity_guard(
        self, tmp_path: Path
    ):
        """🔴 THE EXEMPTION, EXERCISED — it is what keeps rotation working.

        Both bare rows carry identity `legacy`, so a duplicate-identity check
        written without the exemption refuses the ordinary current+previous
        overlap file: the very shape guards 1-5 exist to support, and the shape
        criterion 10's rollback restores. Distinct tokens, one identity, and it
        must LOAD.
        """
        path = self._write(tmp_path, GOOD_TOKEN, SECOND_TOKEN)
        rows = loaded(path, {})
        assert [r[1] for r in rows] == ["legacy", "legacy"]
        assert [r[2] for r in rows] == [None, None]

    def test_the_cap_is_FOUR(self):
        # Literal, not `api.MAX_TOKENS` — importing it would assert x == x.
        assert api.MAX_TOKENS == 4

    def test_a_FIFTH_token_is_refused_at_startup(self, tmp_path: Path):
        # Every earlier guard passes: the file exists, reads, is non-empty, and
        # every one of the five tokens clears the length floor. Only the cap can
        # reject this input.
        five = [chr(ord("a") + i) * 48 for i in range(5)]
        path = self._write(tmp_path, *five)
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {})
        assert "too many tokens" in str(exc.value)
        assert "5" in str(exc.value)

    def test_FOUR_tokens_are_accepted_the_boundary_is_not_off_by_one(
        self, tmp_path: Path
    ):
        four = [chr(ord("a") + i) * 48 for i in range(4)]
        path = self._write(tmp_path, *four)
        assert [r[0] for r in loaded(path, {})] == four

    def test_the_CAP_counts_CREDENTIALS_not_ROWS(self, tmp_path: Path):
        """🔴 A REGRESSION THIS BRANCH INTRODUCED, MEASURED. Removing the
        pre-parse dedup left guard 4 counting physical rows, so four distinct
        tokens plus ONE verbatim duplicate line answered `too many tokens: 5,
        max 4` — for a file holding four credentials that loaded fine before.

        It also contradicted guard 11, whose own comment calls a duplicated row
        "the rotation shape, and it is legitimate": the file guard 11 accepts is
        the file guard 4 was refusing.

        FIVE rows, FOUR credentials. It must load, and the collapse must leave
        exactly the four.
        """
        four = [chr(ord("a") + i) * 48 for i in range(4)]
        path = self._write(tmp_path, *four, four[0])
        assert [r[0] for r in loaded(path, {})] == four

    def test_FIVE_COPIES_of_ONE_token_is_ONE_credential(self, tmp_path: Path):
        """The far end of the same claim, and the one that made the old wording
        plainly wrong: five identical lines are one credential, and "too many
        tokens: 5" was a sentence about a file with one token in it.
        """
        path = self._write(tmp_path, *([GOOD_TOKEN] * 5))
        assert [r[0] for r in loaded(path, {})] == [GOOD_TOKEN]

    def test_the_CAP_still_counts_the_DISTINCT_ones_and_says_so(
        self, tmp_path: Path
    ):
        """🔴 THE UPPER BOUND: counting credentials must not become counting
        nothing. Five DISTINCT tokens with one of them repeated is still five
        credentials, and the number in the message is the credential count — not
        the row count (6) it would have been, and not a constant.
        """
        five = [chr(ord("a") + i) * 48 for i in range(5)]
        path = self._write(tmp_path, *five, five[2])
        message = str(exc_of(lambda: api.load_tokens(path, {})))
        assert "too many tokens: 5, max 4" in message, message

    def test_a_SHORT_SECOND_token_names_its_POSITION_and_never_the_token(
        self, tmp_path: Path
    ):
        # 🔴 The reachable case that a "is the token long enough" check written
        # against `raw.strip()` would wave straight through: the FIRST token is
        # fine, so the file passes every earlier guard.
        path = self._write(tmp_path, GOOD_TOKEN, "hunter2")
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {})
        message = str(exc.value)
        assert "token on line 2 of 2 is too short" in message
        assert "hunter2" not in message, "the secret was echoed into the error"
        assert "43" in message

    def test_BOTH_tokens_in_the_set_authorize_over_HTTP(self, store: Path):
        with running(store, tokens=(GOOD_TOKEN, SECOND_TOKEN)) as (base, _):
            for token in (GOOD_TOKEN, SECOND_TOKEN):
                code, _h, body = fetch(f"{base}/api/v1/recall/{SCOPE}", token=token)
                assert code == 200, f"{token[:3]}… was refused"
                assert POINTER_LINE.encode() in body

    def test_a_token_OUTSIDE_the_set_is_still_refused(self, store: Path):
        with running(store, tokens=(GOOD_TOKEN, SECOND_TOKEN)) as (base, _):
            code, _h, body = fetch(f"{base}/api/v1/recall/{SCOPE}", token=THIRD_TOKEN)
        assert code == 401
        assert body == b"unauthorized\n"

    def test_the_audit_line_names_WHICH_fingerprint_matched(self, store: Path):
        """🔴 THE ONE THING THAT MAKES OVERLAP ROTATION SAFE.

        Without this, "nobody is using the old token any more" is a guess and
        deleting it is a coin flip. A log that named the SERVER's token instead
        of the MATCHED one would be green on a single-token deployment and
        useless on the only deployment shape that needs it.
        """
        with running(store, tokens=(GOOD_TOKEN, SECOND_TOKEN)) as (base, audit):
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=SECOND_TOKEN)
        assert len(audit) == 2
        first, second = api.token_id(GOOD_TOKEN), api.token_id(SECOND_TOKEN)
        assert first != second
        assert f"token={first}" in audit[0]
        assert f"token={second}" in audit[1]
        assert "auth=ok" in audit[0] and "auth=ok" in audit[1]
        # And never the credential itself, on either line.
        joined = "\n".join(audit)
        assert GOOD_TOKEN not in joined and SECOND_TOKEN not in joined

    def test_authorize_compares_against_EVERY_token_with_no_early_exit(
        self, monkeypatch
    ):
        """A `break` on the first match would make "which token did you use"
        measurable from outside — during an overlap window that is exactly the
        fact an attacker wants. The FIRST token matches here, so a short-circuit
        would show up as one call instead of three.
        """
        seen: list[tuple] = []
        real = api.hmac.compare_digest

        def spy(a, b):
            seen.append((a, b))
            return real(a, b)

        monkeypatch.setattr(api.hmac, "compare_digest", spy)
        got = api.authorize(
            f"Bearer {GOOD_TOKEN}", (GOOD_TOKEN, SECOND_TOKEN, THIRD_TOKEN)
        )
        # 🔴 The RECORD, and the fingerprint is read off it — `authorize`
        # returns identity and allowlist alongside the match now, so the audit
        # line and the scope filter come from one decision.
        assert got.fingerprint == api.token_id(GOOD_TOKEN)
        assert got.token == GOOD_TOKEN
        assert len(seen) == 3, f"short-circuited after {len(seen)} comparisons"

    def test_authorize_REFUSES_a_bare_string_rather_than_iterating_CHARACTERS(self):
        """🔴 `for token in "abc…"` yields "a", "b", "c". Without this guard, a
        caller who passed one token as a `str` would authorize anybody who
        presented a SINGLE CHARACTER of it — a total auth bypass that no
        functional test with a correct caller would ever surface.
        """
        with pytest.raises(TypeError) as exc:
            api.authorize(f"Bearer {GOOD_TOKEN}", GOOD_TOKEN)
        assert "SEQUENCE" in str(exc.value)
        # And the hazard it describes is real: one character is not the token.
        with pytest.raises(api._Rejected):
            api.authorize(f"Bearer {GOOD_TOKEN[0]}", (GOOD_TOKEN,))

    def test_build_server_refuses_a_bare_string_too(self, store: Path):
        with pytest.raises(TypeError) as exc:
            api.build_server(
                host="127.0.0.1",
                port=0,
                store_root=str(store),
                tokens=GOOD_TOKEN,
                trusted_proxies=(LOOPBACK_PROXY,),
            )
        # 🔴 The MESSAGE, not just the type. At the base ref this call raises
        # `TypeError: unexpected keyword argument 'tokens'` — so a bare
        # `pytest.raises(TypeError)` is GREEN AT BASE for a completely different
        # reason, which is a vacuous guard. Pinning the sentence makes the test
        # a statement about the guard rather than about the signature.
        assert "SEQUENCE" in str(exc.value)

    def test_a_ROTATION_end_to_end_old_still_works_then_stops(self, store: Path):
        """The proposal's pre-cutover requirement, in-band: add the new token,
        watch BOTH work and the fingerprints diverge, then remove the old one
        and watch it be REFUSED. A rotation path never run is not a rotation
        path — and the last step is the one that is usually skipped.
        """
        # Step 1: only the old token exists.
        with running(store, tokens=(GOOD_TOKEN,)) as (base, audit):
            assert fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)[0] == 200
            assert fetch(f"{base}/api/v1/recall/{SCOPE}", token=SECOND_TOKEN)[0] == 401
        # Step 2: OVERLAP — both accepted, and the log tells them apart.
        with running(store, tokens=(SECOND_TOKEN, GOOD_TOKEN)) as (base, audit):
            assert fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)[0] == 200
            assert fetch(f"{base}/api/v1/recall/{SCOPE}", token=SECOND_TOKEN)[0] == 200
        assert f"token={api.token_id(GOOD_TOKEN)}" in audit[0]
        assert f"token={api.token_id(SECOND_TOKEN)}" in audit[1]
        # Step 3: the old token is REMOVED. 🔴 This is the assertion that makes
        # the whole exercise mean something.
        with running(store, tokens=(SECOND_TOKEN,)) as (base, _):
            assert fetch(f"{base}/api/v1/recall/{SCOPE}", token=SECOND_TOKEN)[0] == 200
            code, _h, body = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
        assert code == 401
        assert body == b"unauthorized\n"

    def test_the_startup_banner_prints_FINGERPRINTS_never_tokens(
        self, tmp_path: Path, store: Path, capsys, monkeypatch
    ):
        path = self._write(tmp_path, GOOD_TOKEN, SECOND_TOKEN)
        started: dict = {}

        class _Fake:
            def serve_forever(self_inner):
                raise KeyboardInterrupt

            def server_close(self_inner):
                pass

        def fake_build(**kwargs):
            started.update(kwargs)
            return _Fake()

        monkeypatch.setattr(api, "build_server", fake_build)
        monkeypatch.setenv("SUBSYSTEM_STORE_TRUSTED_PROXIES", LOOPBACK_PROXY)
        rc = api.main(["--store", str(store), "--port", "0", "--token-file", path])
        assert rc == 0
        out = capsys.readouterr().out
        assert api.token_id(GOOD_TOKEN) in out
        assert api.token_id(SECOND_TOKEN) in out
        assert GOOD_TOKEN not in out and SECOND_TOKEN not in out
        assert [r.token for r in started["tokens"]] == [GOOD_TOKEN, SECOND_TOKEN]


class TestClientIpIsCloudflareOnly:
    """§0.2: `/api/*` has no edge auth, so the app is the only place a client can
    be identified — and it can only be identified correctly.
    """

    def test_the_audit_line_carries_the_CF_Connecting_IP(self, store: Path):
        with running(store) as (base, audit):
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, client_ip=CLIENT_IP)
        assert f"ip={CLIENT_IP}" in audit[0]

    def test_a_spoofed_X_Forwarded_For_does_NOT_win(self, store: Path):
        """🔴 Both headers present, DIFFERENT values. The CF one must be the one
        that is recorded and keyed on; the forged one must not appear anywhere.
        """
        with running(store) as (base, audit):
            code, _h, _b = fetch(
                f"{base}/api/v1/recall/{SCOPE}",
                token=GOOD_TOKEN,
                client_ip=CLIENT_IP,
                extra_headers={"X-Forwarded-For": SPOOF_IP},
            )
        assert code == 200
        assert f"ip={CLIENT_IP}" in audit[0]
        assert SPOOF_IP not in audit[0], "a caller-supplied address was trusted"

    def test_X_Forwarded_For_ALONE_fails_CLOSED(self, store: Path):
        """The header an attacker controls cannot substitute for the one
        Cloudflare overwrites — not even as a fallback.
        """
        with running(store) as (base, audit):
            code, _h, body = fetch(
                f"{base}/api/v1/recall/{SCOPE}",
                token=GOOD_TOKEN,
                client_ip=None,
                extra_headers={"X-Forwarded-For": SPOOF_IP},
            )
        assert code == 401
        assert body == b"unauthorized\n"
        assert "status=no-client-ip" in audit[0]
        assert SPOOF_IP not in audit[0]

    def test_a_MISSING_CF_Connecting_IP_fails_closed_even_with_a_VALID_token(
        self, store: Path
    ):
        with running(store) as (base, audit):
            code, _h, body = fetch(
                f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, client_ip=None
            )
        assert code == 401
        assert body == b"unauthorized\n"
        assert "auth=fail" in audit[0] and "ip=-" in audit[0]

    def test_a_MANGLED_CF_Connecting_IP_fails_closed(self, store: Path):
        for value in ("not-an-ip", "", "203.0.113.7, 198.51.100.4", "999.1.1.1"):
            with running(store) as (base, _):
                code, _h, _b = fetch(
                    f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, client_ip=value
                )
            assert code == 401, f"{value!r} was accepted as a client address"

    def test_TWO_CF_Connecting_IP_headers_fail_closed(self, store: Path):
        """A proxy that APPENDS rather than overwrites would let a caller smuggle
        a second value past it. Refuse rather than pick one.
        """
        # 🔴 `urllib`'s `add_header` OVERWRITES, so it cannot express this at
        # all — a test written with it would send ONE header and pass with the
        # guard deleted. Raw `putheader` twice is the only way to put two on the
        # wire, and the control below proves the shape reaches the server.
        with running(store) as (base, _):
            host = base.split("//", 1)[1]
            two = _raw_request(
                host,
                f"/api/v1/recall/{SCOPE}",
                [
                    ("Authorization", f"Bearer {GOOD_TOKEN}"),
                    ("CF-Connecting-IP", CLIENT_IP),
                    ("CF-Connecting-IP", SPOOF_IP),
                ],
            )
            # POSITIVE CONTROL on the harness: the same call shape with ONE
            # header must be served, or the 401 above would prove only that
            # `_raw_request` is broken.
            one = _raw_request(
                host,
                f"/api/v1/recall/{SCOPE}",
                [
                    ("Authorization", f"Bearer {GOOD_TOKEN}"),
                    ("CF-Connecting-IP", CLIENT_IP),
                ],
            )
        assert one == 200, "the raw-request harness cannot reach a 200 at all"
        assert two == 401

    def test_unidentified_requests_are_NOT_bucketed_together(self, store: Path):
        """🔴 THE HAZARD THE FAIL-CLOSED EXISTS TO AVOID. Bucketing every
        unidentified caller under one shared key means one abuser locks out
        everybody. Twenty rejected no-IP requests — four times the failure
        budget — must leave an identified client completely unaffected.
        """
        with running(store) as (base, audit):
            for _ in range(20):
                assert fetch(f"{base}/api/v1/recall/{SCOPE}", client_ip=None)[0] == 401
            code, _h, body = fetch(
                f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, client_ip=CLIENT_IP
            )
            # 🔴 WAIT FOR ALL TWENTY-ONE LINES. `fetch` returning means the
            # RESPONSE was written; `_audit()` runs after it, on a handler
            # thread. See `drain_output` — the hazard is the server's, not the
            # subprocess pipe's, and this site is in-process.
            lines = await_audit(audit, 21)
        assert code == 200, "an unidentifiable caller locked out an identified one"
        assert POINTER_LINE.encode() in body
        assert len(lines) == 21, lines
        # 🔴 THE ASSERTION THAT MAKES THIS TEST MEAN ANYTHING, and it was missing.
        # An audit found this test VACUOUS against the very hazard it names:
        # under the mutant `ip = "unknown"` (bucket every unidentified caller
        # under one shared key) the flood locks out `"unknown"` while the final
        # request above uses a DIFFERENT key — so it stayed green. What actually
        # distinguishes fail-closed from a shared bucket is that the twentieth
        # unidentified request is STILL `no-client-ip` and never `locked-out`:
        # nothing was counted, because there was no bucket to count into.
        #
        # 🔴 SELECTED BY IDENTITY, NEVER BY POSITION. This used to slice
        # `audit[:20]`, which assumes the twenty rejections were APPENDED before
        # the twenty-first request's line. They are not ordered: twenty-one
        # handler threads each write their response and then race to append, so
        # the `status=recalled` record can land anywhere in the list. Measured on
        # an unmodified tree under CPU load: 2/50 red; with the handler's
        # `_audit` delayed for `no-client-ip` only, 1/1 — `{'no-client-ip',
        # 'recalled'}`. A row is not yours because it is FIRST.
        unidentified = [ln for ln in lines if " ip=- " in ln]
        assert len(unidentified) == 20, (
            f"expected twenty unidentified records, got {len(unidentified)} — "
            f"an unidentified caller was given an identity:\n" + "\n".join(lines))
        statuses = {line.split("status=")[1].split()[0] for line in unidentified}
        assert statuses == {"no-client-ip"}, statuses
        assert not any("locked-out" in line for line in lines)
        assert not any("lockout-triggered" in line for line in lines)

    def test_the_address_is_NORMALISED_so_one_caller_is_one_bucket(self):
        assert api.client_ip({"CF-Connecting-IP": "::FFFF:203.0.113.7"}) == api.client_ip(
            {"CF-Connecting-IP": "::ffff:203.0.113.7"}
        )
        assert api.client_ip({"CF-Connecting-IP": " 203.0.113.7 "}) == "203.0.113.7"
        assert api.client_ip({}) is None
        assert api.client_ip({"CF-Connecting-IP": "nope"}) is None

    def test_health_needs_NO_client_ip_because_the_kubelet_sends_none(
        self, store: Path
    ):
        with running(store) as (base, audit):
            code, _h, body = fetch(f"{base}/healthz", client_ip=None)
        assert (code, body) == (200, b"ok\n")
        assert audit == []

    def test_the_source_never_reads_X_Forwarded_For(self):
        """Secondary, not the guard — `test_a_spoofed_X_Forwarded_For_does_NOT_win`
        is. A behavioural test alone would stay green if XFF were consulted only
        when `CF-Connecting-IP` is absent, which this catches directly.

        🔴 It reads CODE, not text. Comments and docstrings in `server.py`
        discuss `X-Forwarded-For` at length — that is the documentation of why
        it is refused — so a substring scan over the file would be red for the
        wrong reason and get "fixed" by deleting the explanation. Tokenising and
        dropping COMMENT/STRING tokens leaves only what actually executes.
        """
        code = _executable_tokens(SERVER_PATH)
        assert "X-Forwarded-For" not in code
        assert "x-forwarded-for" not in code.lower()
        # POSITIVE CONTROL: the tokeniser CAN see a header string in real code —
        # otherwise the assertion above is a fact about the tokeniser.
        assert "CF-Connecting-IP" in code


class TestRateLimiterUnit:
    """Injected clock, so the WINDOW and the LOCKOUT are both watched to expire.

    A limiter tested only in the "locks out" direction is half a guard: one that
    never released would take the whole store down on a typo.
    """

    def _limiter(self, now: list[float], **kwargs):
        return api.RateLimiter(clock=lambda: now[0], **kwargs)

    def test_the_defaults_are_5_per_60s_then_900s(self):
        # Literals (§: 5 failures / minute -> 15-minute lockout), never imported.
        assert api.DEFAULT_MAX_FAILURES == 5
        assert api.DEFAULT_FAILURE_WINDOW_S == 60.0
        assert api.DEFAULT_LOCKOUT_S == 900.0

    def test_four_failures_do_NOT_lock_and_the_fifth_DOES(self):
        now = [1000.0]
        lim = self._limiter(now)
        for i in range(4):
            assert lim.record_failure("a") is False, f"locked after {i + 1}"
            assert lim.locked_out("a") is False
        assert lim.record_failure("a") is True
        assert lim.locked_out("a") is True

    def test_failures_OUTSIDE_the_window_do_not_accumulate(self):
        now = [1000.0]
        lim = self._limiter(now)
        for _ in range(4):
            assert lim.record_failure("a") is False
        now[0] += 61.0  # the four have aged out
        for _ in range(4):
            assert lim.record_failure("a") is False
        assert lim.locked_out("a") is False

    def test_the_lockout_EXPIRES(self):
        now = [1000.0]
        lim = self._limiter(now)
        for _ in range(5):
            lim.record_failure("a")
        assert lim.locked_out("a") is True
        now[0] += 899.0
        assert lim.locked_out("a") is True, "released early"
        now[0] += 2.0
        assert lim.locked_out("a") is False

    def test_a_lockout_is_PER_KEY_not_global(self):
        now = [1000.0]
        lim = self._limiter(now)
        for _ in range(5):
            lim.record_failure("a")
        assert lim.locked_out("a") is True
        assert lim.locked_out("b") is False

    def test_a_success_does_NOT_forgive_the_streak(self):
        """🔴 INVERTED BY AN AUDIT FINDING, and the old behaviour was mine, not
        the spec's. Forgiving a streak on success created two attacks, both
        because the key is an ADDRESS and not an identity: an attacker holding
        ANY accepted token — including the old one overlap rotation keeps live —
        interleaves one success per four guesses and brute-forces forever; and
        an attacker behind the same NAT as a legitimate client is never locked
        out, because the victim's own traffic keeps resetting them.
        """
        now = [1000.0]
        lim = self._limiter(now)
        for _ in range(4):
            lim.record_failure("a")
        lim.record_success("a")
        assert lim.record_failure("a") is True, "a success forgave the streak"
        assert lim.locked_out("a") is True

    def test_the_WINDOW_is_what_forgives_a_streak(self):
        """The forgiveness the inverted test above was reaching for, done by the
        mechanism that cannot be driven by an attacker: four typos age out.
        """
        now = [1000.0]
        lim = self._limiter(now)
        for _ in range(4):
            assert lim.record_failure("a") is False
        now[0] += 61.0
        for _ in range(4):
            assert lim.record_failure("a") is False
        assert lim.locked_out("a") is False

    def test_a_success_does_NOT_clear_a_LIVE_lockout(self):
        now = [1000.0]
        lim = self._limiter(now)
        for _ in range(5):
            lim.record_failure("a")
        lim.record_success("a")
        assert lim.locked_out("a") is True

    def test_eviction_NEVER_releases_a_live_lockout(self):
        """🔴 The failure table is bounded, and a bound is a deletion policy —
        so the question is what it is allowed to delete. Flooding it with more
        distinct keys than it will hold must not buy an attacker their way out
        of a lockout they already earned. Reachable: `MAX_TRACKED_CLIENTS` + 1
        distinct keys, each with one failure, is exactly one eviction pass.
        """
        now = [1000.0]
        lim = self._limiter(now)
        for _ in range(5):
            lim.record_failure("victim")
        assert lim.locked_out("victim") is True
        # Age every subsequent failure past the window so they are all evictable
        # — the eviction path only ever considers stale entries.
        for i in range(api.MAX_TRACKED_CLIENTS + 1):
            now[0] += 0.001
            lim.record_failure(f"flood-{i}")
        now[0] += 61.0
        lim.record_failure("one-more")
        assert lim.locked_out("victim") is True, "a flood released a live lockout"

    def test_the_thresholds_are_tunable(self):
        now = [1000.0]
        lim = self._limiter(now, max_failures=2, window_s=10.0, lockout_s=30.0)
        assert lim.record_failure("a") is False
        assert lim.record_failure("a") is True
        now[0] += 31.0
        assert lim.locked_out("a") is False


class TestLimiterSettings:
    def test_an_empty_env_yields_the_code_defaults(self):
        assert api.limiter_settings({}) == (5, 60.0, 900.0)

    def test_env_overrides_all_three(self):
        env = {
            "SUBSYSTEM_STORE_MAX_FAILURES": "9",
            "SUBSYSTEM_STORE_FAILURE_WINDOW_S": "30",
            "SUBSYSTEM_STORE_LOCKOUT_S": "120",
        }
        assert api.limiter_settings(env) == (9, 30.0, 120.0)

    def test_a_TYPO_raises_rather_than_silently_defaulting(self):
        with pytest.raises(ValueError) as exc:
            api.limiter_settings({"SUBSYSTEM_STORE_MAX_FAILURES": "fve"})
        assert "SUBSYSTEM_STORE_MAX_FAILURES" in str(exc.value)

    def test_a_NON_POSITIVE_value_raises(self):
        # Reachable past the parse guard: "0" parses fine and would disable the
        # limiter — or lock everyone out on request one, depending on the
        # comparison. Neither is a setting anybody meant.
        with pytest.raises(ValueError) as exc:
            api.limiter_settings({"SUBSYSTEM_STORE_LOCKOUT_S": "0"})
        assert "positive" in str(exc.value)

    def test_main_EXITS_78_on_a_bad_limiter_setting(
        self, store: Path, tmp_path: Path, monkeypatch, capsys
    ):
        """🔴 `build_server` IS STUBBED TO RAISE, AND THAT IS THE POINT, NOT
        TIDINESS. Found by the mutation sweep: with the parse guard broken to
        `return default`, `main` sails past the check and reaches
        `serve_forever()` — so this test does not FAIL, it HANGS, forever, and
        every test after it in the run is silently truncated (claude/RULES.md:
        "a known-red slow test eats the suite budget"). A guard whose mutant
        hangs the suite is worse than one with no test at all, because the
        symptom reads as infrastructure. The stub turns that hang into an
        immediate, named failure.
        """

        def _must_not_be_reached(**kwargs):
            raise AssertionError(
                "main() reached build_server on a bad SUBSYSTEM_STORE_MAX_FAILURES "
                "— the limiter setting was accepted instead of exiting 78"
            )

        monkeypatch.setattr(api, "build_server", _must_not_be_reached)
        path = tmp_path / "tok"
        path.write_text(GOOD_TOKEN)
        monkeypatch.setenv("SUBSYSTEM_STORE_MAX_FAILURES", "lots")
        # Set, so the failure under test is the LIMITER setting and not the
        # trusted-proxy one — two guards reaching one rc are indistinguishable.
        monkeypatch.setenv("SUBSYSTEM_STORE_TRUSTED_PROXIES", LOOPBACK_PROXY)
        rc = api.main(["--store", str(store), "--port", "0", "--token-file", str(path)])
        assert rc == 78
        assert "SUBSYSTEM_STORE_MAX_FAILURES" in capsys.readouterr().err


class TestLockoutOverHTTP:
    """The limiter wired into the router — the layer that knows an auth FAILED.

    🔴 A genuine regression against the base ref: at base, a valid token after
    five wrong ones is served a 200.
    """

    def test_five_failures_lock_out_a_VALID_token_from_the_same_client(
        self, store: Path
    ):
        with running(store) as (base, audit):
            for _ in range(5):
                assert fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)[0] == 401
            code, _h, body = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
        assert code == 401, "a locked-out client was served with a valid token"
        assert body == b"unauthorized\n"
        assert "status=lockout-triggered" in audit[4]
        assert "status=locked-out" in audit[5]

    def test_FOUR_failures_do_not_lock_out_the_boundary_is_not_off_by_one(
        self, store: Path
    ):
        with running(store) as (base, _):
            for _ in range(4):
                assert fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)[0] == 401
            code, _h, body = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
        assert code == 200
        assert POINTER_LINE.encode() in body

    def test_the_lockout_is_PER_CLIENT_not_a_global_kill_switch(self, store: Path):
        """🔴 The failure the `CF-Connecting-IP` keying exists to prevent: one
        abuser must not take the store down for everyone else.
        """
        with running(store) as (base, _):
            for _ in range(6):
                fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48, client_ip=CLIENT_IP)
            locked = fetch(
                f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, client_ip=CLIENT_IP
            )
            other = fetch(
                f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, client_ip=OTHER_IP
            )
        assert locked[0] == 401
        assert other[0] == 200, "an unrelated client was caught in someone else's lockout"

    def test_a_LOCKED_OUT_response_is_BYTE_IDENTICAL_to_an_ordinary_401(
        self, store: Path
    ):
        """The log discriminates; the wire must not. An attacker who could see
        the lockout land would know exactly how to pace a stuffing run.
        """
        with running(store) as (base, audit):
            ordinary = fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)
            for _ in range(5):
                fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)
            locked = fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)
        assert ordinary[0] == locked[0] == 401
        assert ordinary[2] == locked[2]
        assert _comparable(ordinary[1]) == _comparable(locked[1])
        # …and the audit log DOES tell them apart, or the property is vacuous.
        assert "status=unauthorized" in audit[0]
        assert "status=locked-out" in audit[-1]

    def test_a_SUCCESS_does_NOT_buy_more_GUESSES(self, store: Path):
        """🔴 THE INTERLEAVE ATTACK, over HTTP. An attacker holding one accepted
        token — the old one, during an overlap rotation — must not be able to
        spend it to reset the budget and keep guessing the rest of the set.
        Four wrong, one right, one wrong: the sixth request is the fifth FAILURE
        inside the window, so it locks out.
        """
        with running(store, tokens=(GOOD_TOKEN, SECOND_TOKEN)) as (base, audit):
            for _ in range(4):
                fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)
            assert fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)[0] == 200
            fetch(f"{base}/api/v1/recall/{SCOPE}", token="x" * 48)
            code, _h, _b = fetch(f"{base}/api/v1/recall/{SCOPE}", token=SECOND_TOKEN)
        assert code == 401, "a valid token reset the guessing budget"
        assert "status=lockout-triggered" in audit[5]

    def test_a_WRONG_PATH_does_NOT_lock_out_a_client_holding_the_RIGHT_token(
        self, store: Path
    ):
        """🔴 INVERTED BY A DELTA AUDIT, which measured the previous behaviour
        locking out a legitimate client. Counting path probes AND removing
        success-forgiveness combined into: five ordinary wrong paths — one of
        them `/api/v1`, a missing trailing slash from the real prefix — and a
        client holding the correct token was dead for 15 minutes with nothing
        able to forgive it.

        The specification says five failed AUTHS per minute. A request that
        never reaches the token check is not a failed auth. Volumetric probing
        belongs to the Traefik (10/s) and Cloudflare layers.
        """
        with running(store) as (base, audit):
            for path in ("/favicon.ico", "/", "/robots.txt", "/metrics", "/api/v1"):
                assert fetch(f"{base}{path}", token=GOOD_TOKEN)[0] == 401
            code, _h, body = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
        assert code == 200, "a valid client locked itself out on wrong paths"
        assert POINTER_LINE.encode() in body
        assert not any("locked-out" in line for line in audit)
        # …and they are still REFUSED and logged, or this would be a hole.
        assert sum("status=unauthorized" in line for line in audit) == 5

    def test_a_WRONG_TOKEN_still_counts_even_on_a_path_that_does_not_exist(
        self, store: Path
    ):
        """The other half: the exemption above is for the PATH check, not for
        auth. An `/api/v1/...` request with a bad token is a failed auth no
        matter how nonsensical the route.
        """
        with running(store) as (base, _):
            for _ in range(5):
                assert fetch(f"{base}/api/v1/nonsense/x", token="w" * 48)[0] == 401
            code, _h, _b = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
        assert code == 401

    def test_the_health_probe_is_never_rate_limited(self, store: Path):
        with running(store) as (base, _):
            for _ in range(10):
                fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)
            code, _h, body = fetch(f"{base}/healthz", client_ip=None)
        assert (code, body) == (200, b"ok\n"), "a lockout took the readiness probe down"

    def test_POST_is_STILL_405_and_not_swallowed_by_the_new_ordering(
        self, store: Path
    ):
        """Phase 3 owns writes. The 405 sits ahead of the client-IP and lockout
        checks, so none of the phase-1.5 plumbing can turn a mutation into a
        read — pinned here because that ordering is now load-bearing.
        """
        with running(store) as (base, audit):
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                code, headers, body = fetch(
                    f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, method=method
                )
                assert code == 405, f"{method} answered {code}"
                assert body == b"read-only\n"
                assert headers["Allow"] == "GET, HEAD"
        assert all("status=method-not-allowed" in line for line in audit)


# =============================================================================
# 15. The REAL entrypoint, driven as a SUBPROCESS.
#
# 🔴 THIS SECTION EXISTS TO BE RED AT THE BASE REF FOR THE RIGHT REASON.
# Everything in section 14 that touches the server calls `build_server(tokens=…)`
# or `load_tokens`, neither of which exists at base — so its red is an
# AttributeError, which is a collection error wearing a failure's clothes and
# proves nothing (the same trap phase 1's header calls out).
#
# The COMMAND LINE, by contrast, is unchanged between the two refs:
# `server.py --store --host --port --token-file` parses identically at base. So
# a test that spawns the real process and drives it over a real socket runs on
# BOTH trees, and its failure at base is a statement about BEHAVIOUR:
#
#   * base serves 200 to a valid token with no `CF-Connecting-IP` at all
#   * base serves 200 to a valid token after five wrong ones from one address
#   * base treats a TWO-LINE token file as ONE 97-character token, so neither
#     line authorises anything — an overlap rotation is impossible
#
# It is also the only test here that reads the audit line off the process's
# STDOUT, which is the stream Loki actually ingests. The in-process `audit`
# callback used everywhere above is a different code path.
# =============================================================================


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


#: 🔴 TWO LOOPBACK ADDRESSES, WHICH IS WHAT MAKES THE REGRESSION MEASURABLE.
#: `127.0.0.0/8` is entirely local on Linux, so a client can BIND `127.0.0.2` as
#: its source and reach a server listening on `127.0.0.1` — and the server sees
#: a genuinely different peer. Without that, every request in a hermetic test
#: comes from the same address, "trusted peer" and "untrusted peer" cannot both
#: appear against ONE running process, and the victim's 401 is identical on both
#: trees. That identical 401 is exactly what a first draft of this file asserted,
#: and it passed at the base ref: a vacuous guard on the one defect that matters.
TRUSTED_PEER = "127.0.0.1"
UNTRUSTED_PEER = "127.0.0.2"


def fetch_from(
    source_ip: str,
    base: str,
    path: str,
    *,
    token: str | None = None,
    client_ip: str | None = CLIENT_IP,
) -> int:
    """GET `path` with the TCP source address bound to `source_ip`. Returns the
    status code.

    `urllib` cannot express a source address; `http.client.HTTPConnection` can.
    """
    host, _, port = base.removeprefix("http://").partition(":")
    conn = http.client.HTTPConnection(
        host, int(port), timeout=15, source_address=(source_ip, 0)
    )
    try:
        headers = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if client_ip is not None:
            headers["CF-Connecting-IP"] = client_ip
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        resp.read()
        return resp.status
    finally:
        conn.close()


def _child_env(trusted_proxies: str | None) -> dict[str, str]:
    """The spawned server's environment. `None` REMOVES the variable.

    🔴 It pops rather than skipping the set: `os.environ` is inherited, so a
    developer who happens to export `SUBSYSTEM_STORE_TRUSTED_PROXIES` in their
    shell would otherwise make the "unset" test pass for the wrong reason — and
    on the day it mattered it would be the CI runner's environment deciding.
    """
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    env.pop("SUBSYSTEM_STORE_TRUSTED_PROXIES", None)
    if trusted_proxies is not None:
        env["SUBSYSTEM_STORE_TRUSTED_PROXIES"] = trusted_proxies
    return env


@contextmanager
def running_subprocess(
    store_root: Path,
    token_file: Path,
    *,
    trusted_proxies: str | None = LOOPBACK_PROXY,
    host: str = "127.0.0.1",
):
    """Spawn the REAL `server.py` process and wait for it to answer /healthz.

    `trusted_proxies` goes in as `$SUBSYSTEM_STORE_TRUSTED_PROXIES`. It is a
    string, not a list, so a test can pass a deliberately malformed value; pass
    `None` to leave the variable UNSET, which is how the startup refusal is
    exercised. 🔴 The base ref ignores this variable entirely, which is what
    makes the tests below behavioural rather than AttributeErrors.
    """
    import time

    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            str(SERVER_PATH),
            "--store",
            str(store_root),
            "--host",
            host,
            "--port",
            str(port),
            "--token-file",
            str(token_file),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_child_env(trusted_proxies),
    )
    base = f"http://{host}:{port}"
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            if proc.poll() is not None:
                out, err = proc.communicate()
                raise AssertionError(f"server exited {proc.returncode}: {err or out}")
            try:
                if fetch(f"{base}/healthz", client_ip=None)[0] == 200:
                    break
            except Exception:
                time.sleep(0.1)
        else:
            raise AssertionError("server never became healthy")
        yield base, proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()
            proc.wait(timeout=10)


class TestTheDeployedEntrypoint:
    """🔴 RED AT THE BASE REF BEHAVIOURALLY, not by AttributeError."""

    @pytest.fixture
    def token_file(self, tmp_path: Path) -> Path:
        path = tmp_path / "token"
        path.write_text(GOOD_TOKEN + "\n")
        return path

    @pytest.fixture
    def rotating_token_file(self, tmp_path: Path) -> Path:
        """Two tokens, one per line — the overlap-rotation shape."""
        path = tmp_path / "tokens"
        path.write_text(f"{SECOND_TOKEN}\n{GOOD_TOKEN}\n")
        return path

    def test_POSITIVE_CONTROL_the_spawned_process_serves_a_real_digest(
        self, store: Path, token_file: Path
    ):
        """Before any zero or any 401 below is believed: this call shape CAN
        return a 200 with content from a process spawned exactly this way.
        """
        with running_subprocess(store, token_file) as (base, _proc):
            code, headers, body = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
        assert code == 200
        assert headers["X-Store-Status"] == "recalled"
        assert POINTER_LINE.encode() in body

    def test_a_valid_token_with_NO_client_ip_is_REFUSED(
        self, store: Path, token_file: Path
    ):
        """Base serves this 200. The store would be reachable by anything that
        held the token, from anywhere, with no address recorded against it.
        """
        with running_subprocess(store, token_file) as (base, _proc):
            code, _h, body = fetch(
                f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, client_ip=None
            )
        assert code == 401
        assert body == b"unauthorized\n"

    def test_five_wrong_tokens_LOCK_OUT_the_right_one(
        self, store: Path, token_file: Path
    ):
        """Base serves the sixth request 200 — an unlimited online guessing
        budget against the one credential protecting the whole store.
        """
        with running_subprocess(store, token_file) as (base, _proc):
            for _ in range(5):
                assert fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)[0] == 401
            code, _h, _b = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
        assert code == 401, "an unlimited guessing budget survived"

    def test_a_TWO_LINE_token_file_authorises_BOTH_lines(
        self, store: Path, rotating_token_file: Path
    ):
        """Base reads the whole file as ONE 97-character token, so NEITHER line
        works and an overlap rotation cannot be performed at all.
        """
        with running_subprocess(store, rotating_token_file) as (base, _proc):
            for token in (GOOD_TOKEN, SECOND_TOKEN):
                code, _h, _b = fetch(f"{base}/api/v1/recall/{SCOPE}", token=token)
                assert code == 200, f"{token[:3]}… was refused during overlap"
            outside = fetch(f"{base}/api/v1/recall/{SCOPE}", token=THIRD_TOKEN)[0]
        assert outside == 401, "the set accepted a token that is not in it"

    def test_the_STDOUT_audit_stream_names_the_matched_fingerprint(
        self, store: Path, rotating_token_file: Path
    ):
        """🔴 The stream Loki ingests, not the in-process callback — and the
        field the `SubsystemStoreAuthFailSpike` rule keys on.

        Base prints the CONFIGURED token's id on every line, so during an
        overlap it cannot tell you which credential a client actually used,
        which is the one fact that makes retiring the old one safe.
        """
        # Drain and WAIT — a returned response does not imply its audit line.
        # See `drain_output`.
        with running_subprocess(store, rotating_token_file) as (base, proc):
            out = drain_output(proc)
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=SECOND_TOKEN)
            fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)
            lines = await_audit(out, 3)

        assert len(lines) == 3, f"expected 3 audit lines, got {len(lines)}: {out.text}"
        assert f"token={api.token_id(GOOD_TOKEN)}" in lines[0]
        assert f"token={api.token_id(SECOND_TOKEN)}" in lines[1]
        assert api.token_id(GOOD_TOKEN) != api.token_id(SECOND_TOKEN)
        # The failure line: no fingerprint, `auth=fail`, and the client address —
        # the three fields the Loki alert selects on.
        assert "auth=fail" in lines[2] and "token=-" in lines[2]
        assert f"ip={CLIENT_IP}" in lines[2]
        assert "result=401" in lines[2]
        # And never a credential, on any line. 🔴 Asserted against the WHOLE
        # stream (`out.text`), not the audit subset — a token leaked on a
        # non-audit line must still fail this. `wait_closed()` first, so a line
        # printed during SHUTDOWN is inside the stream being asserted on rather
        # than still in flight.
        out.wait_closed()
        assert GOOD_TOKEN not in out.text and SECOND_TOKEN not in out.text
        assert "w" * 48 not in out.text
        # 🔴 AND THE CEILING, AFTER THE STREAM IS CLOSED. `lines` above is a
        # SNAPSHOT taken while the process was still running, so `== 3` on it
        # cannot see a FOURTH record emitted later — during shutdown, say. That
        # gap is real: with the server patched to emit one extra audit line at
        # SIGTERM, the pre-helper code failed and the snapshot check passes.
        # Three requests must produce three records, not "at least three".
        assert len(out.audit) == 3, (
            f"the closed stream holds {len(out.audit)} audit records for 3 "
            f"requests — an extra one was emitted after the snapshot:\n{out.text}")


# =============================================================================
# 16. THE AUDIT ROUND — one test per finding, each red before its fix.
#
# 🔴 EVERY TEST BELOW EXISTS BECAUSE A REVIEW FOUND A REAL DEFECT IN SECTIONS
# 14-15, NOT BECAUSE OF A STYLE NOTE. Two were critical: an unauthenticated
# caller could FORGE audit lines (defeating the exact property the token-set
# design rests on), and an unread request body could be re-parsed as the next
# request on a keep-alive connection (CL.0 smuggling). A fix made in response to
# a review is a code change like any other, so each one is pinned here.
# =============================================================================


class TestAuditLogCannotBeForged:
    """🔴 CRITICAL. `unquote()` turns `%0a` into a REAL newline and the audit
    record is one f-string. An unauthenticated caller could emit a second,
    syntactically perfect `auth=ok` line naming any fingerprint and any address.

    Why that is not cosmetic: the README's rotation procedure says to delete the
    old token once its fingerprint stops appearing in the log. A caller who can
    keep any fingerprint appearing forever can block rotation indefinitely, and
    one who can forge `auth=fail` at will can drown or fabricate the Loki alert.
    """

    FORGED = (
        "store-api%20audit%20ts=2026-01-01T00:00:00+00:00%20ip=198.51.100.4"
        "%20method=GET%20path=/api/v1/recall/x%20token=deadbeef1234%20auth=ok"
        "%20result=200%20status=recalled"
    )

    def test_a_NEWLINE_in_the_path_cannot_open_a_second_record(self, store: Path):
        with running(store) as (base, audit):
            code, _h, _b = fetch(f"{base}/api/v1/x%0a{self.FORGED}")
        assert code == 401
        # ONE request, ONE record — the property nothing asserted before.
        assert len(audit) == 1, f"the request produced {len(audit)} audit entries"
        assert "\n" not in audit[0], "a newline survived into the audit record"
        assert "\r" not in audit[0]
        # 🔴 ASSERT THE PARSED FIELDS, NOT THE SPELLING. The escaped text still
        # CONTAINS the characters `auth=ok` inside the path value — a substring
        # check would be red for a record that is perfectly safe, and would then
        # be "fixed" by scrubbing the path into uselessness. What matters is
        # that a splitter sees one `auth` field and it says `fail`.
        fields = [part for part in audit[0].split() if "=" in part]
        keys = [part.split("=", 1)[0] for part in fields]
        assert keys.count("auth") == 1, f"more than one auth field: {audit[0]}"
        assert keys.count("token") == 1
        parsed = dict(part.split("=", 1) for part in fields)
        assert parsed["auth"] == "fail"
        assert parsed["token"] == "-"

    def test_the_forged_text_cannot_reach_the_log_as_SEPARATE_FIELDS(
        self, store: Path
    ):
        """Escaping newlines alone is not enough — a SPACE also opens a new
        field, so `path=/x auth=ok` would parse as two fields to any splitter.
        """
        with running(store) as (base, audit):
            fetch(f"{base}/api/v1/x%20auth=ok%20token=deadbeef1234")
        line = audit[0]
        # 🔴 COUNT THE FIELDS; DO NOT `dict()` THEM. A round-2 mutation sweep
        # caught this test being vacuous: `dict()` lets the LAST occurrence win,
        # and the genuine `auth=fail` the server appends is always last — so
        # under the mutant that stops escaping spaces (leaving the forged
        # `auth=ok` in the line as its own field) the assertions still passed.
        # What a log consumer sees is a DUPLICATE key, so that is what to assert.
        keys = [part.split("=", 1)[0] for part in line.split() if "=" in part]
        assert keys.count("auth") == 1, f"the record has {keys.count('auth')} auth fields"
        assert keys.count("token") == 1, f"the record has {keys.count('token')} token fields"
        parsed = dict(part.split("=", 1) for part in line.split() if "=" in part)
        assert parsed["auth"] == "fail"
        assert parsed["token"] == "-"

    def test_control_characters_are_neutralised_but_the_path_is_still_LEGIBLE(
        self, store: Path
    ):
        with running(store) as (base, audit):
            fetch(f"{base}/api/v1/recall/{SCOPE}%00%09%1b[31m", token=GOOD_TOKEN)
        line = audit[0]
        assert "\x00" not in line and "\x1b" not in line and "\t" not in line
        # A log that scrubbed everything would be safe and useless.
        assert SCOPE in line

    def test_an_ABSURDLY_long_path_cannot_flood_one_record(self, store: Path):
        with running(store) as (base, audit):
            fetch(f"{base}/api/v1/{'z' * 4000}")
        assert len(audit[0]) < 1000, "one request wrote an unbounded log record"
        assert "truncated" in audit[0]

    def test_POSITIVE_CONTROL_an_ordinary_path_is_logged_verbatim(self, store: Path):
        """Without this, every assertion above is satisfied by a `_audit` that
        logs nothing at all.
        """
        with running(store) as (base, audit):
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
        assert f"path=/api/v1/recall/{SCOPE}" in audit[0]
        assert f"token={api.token_id(GOOD_TOKEN)}" in audit[0]


class TestNoRequestSmuggling:
    """🔴 CRITICAL. The server keeps connections alive and never read request
    bodies, so a body was parsed as the NEXT request on the same socket.

    Behind a proxy that pools upstream connections — Traefik does by default —
    that is CL.0 smuggling: a POST body holding a partial request line
    desynchronises the connection and the next VICTIM request completes the
    attacker's line, carrying the victim's `Authorization` header to a scope the
    attacker chose.
    """

    def _raw(self, host: str, payload: bytes, expect: int = 2) -> list[bytes]:
        """Write raw bytes on ONE socket and read every response that comes back."""
        import socket

        host_name, port = host.split(":")
        with socket.create_connection((host_name, int(port)), timeout=10) as sock:
            sock.sendall(payload)
            sock.settimeout(5)
            chunks = []
            try:
                while True:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
            except (TimeoutError, OSError):
                pass
        return b"".join(chunks).split(b"HTTP/1.1 ")[1:]

    def test_a_POST_BODY_is_not_served_as_the_next_request(self, store: Path):
        """The end-to-end property. ⚠ It is defended by BOTH layers, so a sweep
        shows it killed by the connection-close mutant and NOT by the
        no-drain one — the 405 closes the socket either way. The drain's own
        guard is the GET case below, where the response is a 200 and the
        connection legitimately stays open. Recorded because a reader who
        assumed this test covers the drain would delete the other one.
        """
        with running(store) as (base, _):
            host = base.split("//", 1)[1]
            smuggled = (
                f"GET /api/v1/recall/{SCOPE} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"Authorization: Bearer {GOOD_TOKEN}\r\n"
                f"CF-Connecting-IP: {CLIENT_IP}\r\n\r\n"
            ).encode()
            payload = (
                f"POST /api/v1/recall/{SCOPE} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"CF-Connecting-IP: {CLIENT_IP}\r\n"
                f"Content-Length: {len(smuggled)}\r\n\r\n"
            ).encode() + smuggled
            responses = self._raw(host, payload)
        assert len(responses) == 1, (
            f"the body was re-parsed as a request: {len(responses)} responses"
        )
        assert POINTER_LINE.encode() not in b"".join(responses)

    def test_a_GET_with_a_body_does_not_desynchronise_the_connection(
        self, store: Path
    ):
        """🔴 THIS is the drain's guard, not the POST case above. `/healthz`
        answers 200, so the connection is deliberately kept alive and the ONLY
        thing standing between the body and the next request is `_drain_body`.
        Confirmed by mutation: removing the drain kills exactly this test.
        """
        with running(store) as (base, _):
            host = base.split("//", 1)[1]
            smuggled = (
                f"GET /api/v1/recall/{SCOPE} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"Authorization: Bearer {GOOD_TOKEN}\r\n"
                f"CF-Connecting-IP: {CLIENT_IP}\r\n\r\n"
            ).encode()
            payload = (
                f"GET /healthz HTTP/1.1\r\nHost: {host}\r\n"
                f"Content-Length: {len(smuggled)}\r\n\r\n"
            ).encode() + smuggled
            responses = self._raw(host, payload)
        assert len(responses) == 1, "a GET body was re-parsed as a request"
        assert POINTER_LINE.encode() not in b"".join(responses)

    def test_POSITIVE_CONTROL_the_raw_harness_CAN_see_two_responses(
        self, store: Path
    ):
        """🔴 Otherwise "1 response" is a fact about the socket reader. Two
        genuinely pipelined requests must come back as two.
        """
        with running(store) as (base, _):
            host = base.split("//", 1)[1]
            one = (
                f"GET /healthz HTTP/1.1\r\nHost: {host}\r\n\r\n"
            ).encode()
            responses = self._raw(host, one + one)
        assert len(responses) == 2, f"the harness cannot see two: {len(responses)}"

    def test_a_rejected_request_does_not_keep_its_connection(self, store: Path):
        with running(store) as (base, _):
            host = base.split("//", 1)[1]
            bad = (
                f"GET /api/v1/recall/{SCOPE} HTTP/1.1\r\nHost: {host}\r\n"
                f"CF-Connecting-IP: {CLIENT_IP}\r\n\r\n"
            ).encode()
            responses = self._raw(host, bad + bad)
        assert len(responses) == 1, "a 401 left the connection open for reuse"
        assert b"Connection: close" in responses[0]


class TestWritesAreMeteredLikeEverythingElse:
    """A write attempt used to answer 405 BEFORE the client-IP and lockout
    checks: 31 anonymous POSTs with no token and no `CF-Connecting-IP` produced
    31 audit lines and counted for nothing. That is a free, unauthenticated,
    unbounded channel for drowning the Loki alert this design depends on.
    """

    def test_a_POST_with_NO_client_ip_is_a_401_not_a_405(self, store: Path):
        with running(store) as (base, audit):
            code, _h, body = fetch(
                f"{base}/api/v1/recall/{SCOPE}", method="POST", client_ip=None
            )
        assert code == 401
        assert body == b"unauthorized\n"
        assert "status=no-client-ip" in audit[0]

    def test_POST_probing_COUNTS_toward_the_lockout(self, store: Path):
        with running(store) as (base, _):
            for _ in range(5):
                fetch(f"{base}/api/v1/recall/{SCOPE}", method="POST")
            code, _h, _b = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
        assert code == 401, "unauthenticated POSTs were an unmetered channel"

    def test_a_LOCKED_OUT_client_cannot_even_learn_that_writes_are_405(
        self, store: Path
    ):
        with running(store) as (base, _):
            for _ in range(5):
                fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)
            code, _h, body = fetch(
                f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, method="POST"
            )
        assert (code, body) == (401, b"unauthorized\n")

    def test_an_IDENTIFIED_caller_still_gets_the_405(self, store: Path):
        """The read-only guarantee is unchanged for anyone who gets that far —
        this is the positive control on the reordering.
        """
        with running(store) as (base, _):
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                code, headers, body = fetch(
                    f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, method=method
                )
                assert code == 405, f"{method} answered {code}"
                assert body == b"read-only\n"
                assert headers["Allow"] == "GET, HEAD"


class TestMalformedTargetsAndUnknownMethods:
    def test_an_absolute_form_target_that_breaks_urlsplit_is_a_401_not_a_CRASH(
        self, store: Path
    ):
        """`GET http://[ HTTP/1.1` raised an unhandled ValueError: no response,
        a killed connection, and a ~20-line traceback per request in the pod log
        — cheaper than any metered path. Absolute-form is mandatory-to-accept.
        """
        import socket

        with running(store) as (base, audit):
            host = base.split("//", 1)[1]
            with socket.create_connection(tuple(host.split(":")[:1]) + (int(host.split(":")[1]),), timeout=10) as sock:
                sock.sendall(
                    f"GET http://[ HTTP/1.1\r\nHost: {host}\r\n"
                    f"CF-Connecting-IP: {CLIENT_IP}\r\n\r\n".encode()
                )
                sock.settimeout(5)
                data = b""
                try:
                    while True:
                        chunk = sock.recv(65536)
                        if not chunk:
                            break
                        data += chunk
                except (TimeoutError, OSError):
                    pass
        assert data, "the request got no response at all"
        assert b"401" in data.split(b"\r\n")[0], data.split(b"\r\n")[0]
        assert b"unauthorized" in data
        assert any("status=malformed-target" in line for line in audit)

    def test_an_UNKNOWN_method_is_the_same_uniform_401_not_a_501_page(
        self, store: Path
    ):
        import socket

        for verb in ("OPTIONS", "TRACE", "FROBNICATE"):
            with running(store) as (base, _):
                host = base.split("//", 1)[1]
                name, port = host.split(":")
                with socket.create_connection((name, int(port)), timeout=10) as sock:
                    sock.sendall(
                        f"{verb} /api/v1/recall/{SCOPE} HTTP/1.1\r\nHost: {host}\r\n"
                        f"CF-Connecting-IP: {CLIENT_IP}\r\n\r\n".encode()
                    )
                    sock.settimeout(5)
                    data = b""
                    try:
                        while True:
                            chunk = sock.recv(65536)
                            if not chunk:
                                break
                            data += chunk
                    except (TimeoutError, OSError):
                        pass
            assert b"401" in data.split(b"\r\n")[0], f"{verb}: {data[:80]!r}"
            assert b"unauthorized\n" in data
            assert verb.encode() not in data, f"{verb} was echoed back to the caller"

    def test_a_traversal_component_is_refused_before_it_reaches_the_disk(
        self, store: Path
    ):
        with running(store) as (base, _):
            code, headers, _b = fetch(
                f"{base}/api/v1/recall/%2e%2e", token=GOOD_TOKEN
            )
        assert code == 400
        assert headers["X-Store-Status"] == "bad-request"

    def test_a_NORMAL_scope_name_is_still_accepted(self, store: Path):
        """The positive control on that guard — a guard that refused everything
        would pass the traversal test above and break every real caller.
        """
        with running(store) as (base, _):
            assert fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)[0] == 200
            code, _h, _b = fetch(
                f"{base}/api/v1/recall/{SCOPE}?ref=thing-alpha", token=GOOD_TOKEN
            )
        assert code == 200

    def test_a_DOT_in_a_path_component_is_refused_at_all(self, store: Path):
        """🔴 ADDED BECAUSE A MUTANT SURVIVED. Removing `.` from the character
        class left the whole suite green: nothing had a dotted PATH component,
        because refs travel in the query string. So the permissive class was
        never justified by a caller, and the guard is now structural — no dot at
        all, which makes `..` impossible to spell rather than excluded by name.

        Measured before tightening: all 8 scopes in the live store match
        `[A-Za-z0-9_-]+` and 0 contain a dot (counts only — the names are
        client-confidential and this repo is public).
        """
        with running(store) as (base, _):
            for probe in ("with.dot", "..", ".", "a.b.c", "%2e%2e%2f"):
                code, _h, _b = fetch(
                    f"{base}/api/v1/recall/{probe}", token=GOOD_TOKEN
                )
                assert code == 400, f"{probe!r} was accepted as a path component"

    def test_the_ref_QUERY_parameter_may_still_contain_a_dot(self, store: Path):
        """The path is strict; the query string is not, and must not become so —
        that is the distinction the surviving mutant exposed.
        """
        with running(store) as (base, _):
            code, _h, _b = fetch(
                f"{base}/api/v1/recall/{SCOPE}?ref=thing.alpha.v2", token=GOOD_TOKEN
            )
        assert code == 200


class TestRateLimitKeyIsAClientNotAnAddress:
    """An IPv6 /64 is one client's free choice of 2**64 addresses. Keying on the
    full address makes the lockout decorative and is the cheapest way to grow the
    failure table without bound.
    """

    def test_two_addresses_in_ONE_v6_slash_64_are_ONE_bucket(self, store: Path):
        a = "2001:db8:1:2::1"
        b = "2001:db8:1:2:ffff:ffff:ffff:ffff"
        with running(store) as (base, _):
            for _ in range(3):
                fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48, client_ip=a)
            for _ in range(2):
                fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48, client_ip=b)
            code, _h, _b = fetch(
                f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, client_ip=a
            )
        assert code == 401, "an attacker got a fresh bucket by changing host bits"

    def test_a_DIFFERENT_slash_64_is_a_DIFFERENT_bucket(self, store: Path):
        """The negative half: aggregating to /64 must not aggregate the world.
        Without this, `return 0` would pass the test above.
        """
        with running(store) as (base, _):
            for _ in range(6):
                fetch(
                    f"{base}/api/v1/recall/{SCOPE}",
                    token="w" * 48,
                    client_ip="2001:db8:1:2::1",
                )
            code, _h, _b = fetch(
                f"{base}/api/v1/recall/{SCOPE}",
                token=GOOD_TOKEN,
                client_ip="2001:db8:9:9::1",
            )
        assert code == 200, "an unrelated /64 was caught in someone else's lockout"

    def test_a_v4_MAPPED_address_is_the_SAME_bucket_as_its_v4_form(self):
        assert api.client_ip({"CF-Connecting-IP": "::ffff:203.0.113.7"}) == api.client_ip(
            {"CF-Connecting-IP": "203.0.113.7"}
        )

    def test_v4_is_NOT_aggregated(self):
        """A /64 rule misapplied to v4 would collapse whole ISPs into one
        bucket. Two adjacent v4 addresses stay two clients.
        """
        first = api.client_ip({"CF-Connecting-IP": "203.0.113.7"})
        second = api.client_ip({"CF-Connecting-IP": "203.0.113.8"})
        assert first != second


class TestTheTablesAreActuallyBounded:
    """`MAX_TRACKED_CLIENTS` was a bound in name only: it dropped just the
    entries that had already aged out, so INSIDE the window nothing was
    evictable. Measured before the fix: 20,000 tracked against a cap of 4,096.
    """

    def _limiter(self, now: list[float]):
        return api.RateLimiter(clock=lambda: now[0])

    def test_the_failure_table_stays_under_the_cap_with_NON_stale_entries(self):
        now = [1000.0]
        lim = self._limiter(now)
        for i in range(api.MAX_TRACKED_CLIENTS * 2):
            now[0] += 0.0001  # every entry stays well inside the window
            lim.record_failure(f"k{i}")
        assert len(lim._failures) <= api.MAX_TRACKED_CLIENTS, len(lim._failures)

    def test_a_LIVE_lockout_survives_that_flood(self):
        """Bounding must never be a release valve — the whole reason the first
        version only dropped stale entries.
        """
        now = [1000.0]
        lim = self._limiter(now)
        for _ in range(5):
            lim.record_failure("victim")
        assert lim.locked_out("victim") is True
        for i in range(api.MAX_TRACKED_CLIENTS * 2):
            now[0] += 0.0001
            lim.record_failure(f"k{i}")
        assert lim.locked_out("victim") is True

    def test_the_lockout_table_is_bounded_too(self):
        now = [1000.0]
        lim = self._limiter(now)
        for i in range(api.MAX_TRACKED_LOCKOUTS + 500):
            for _ in range(5):
                lim.record_failure(f"lk{i}")
            now[0] += 0.0001
        assert len(lim._locked_until) <= api.MAX_TRACKED_LOCKOUTS

    def test_the_client_CLOSEST_to_a_lockout_is_the_LAST_forgotten(self):
        """Oldest-first eviction, so the flood does not launder the attacker's
        own streak out of the table.
        """
        now = [1000.0]
        lim = self._limiter(now)
        lim.record_failure("early")
        for i in range(api.MAX_TRACKED_CLIENTS * 2):
            now[0] += 0.0001
            lim.record_failure(f"k{i}")
        assert "early" not in lim._failures
        assert f"k{api.MAX_TRACKED_CLIENTS * 2 - 1}" in lim._failures


class TestNonFiniteLimiterSettings:
    """`nan <= 0` is False, so both walked through the "must be positive" guard —
    the exact "misconfiguration that defaults is invisible forever" the function
    exists to prevent, arriving through the one comparison that does not order
    them. Measured before the fix: a nan WINDOW silently disabled the limiter
    entirely; a nan or inf LOCKOUT made it permanent.
    """

    @pytest.mark.parametrize("value", ["nan", "inf", "-inf", "NaN", "Infinity"])
    @pytest.mark.parametrize(
        "name",
        [
            "SUBSYSTEM_STORE_FAILURE_WINDOW_S",
            "SUBSYSTEM_STORE_LOCKOUT_S",
        ],
    )
    def test_a_non_finite_value_is_REFUSED(self, name: str, value: str):
        with pytest.raises(ValueError) as exc:
            api.limiter_settings({name: value})
        assert name in str(exc.value)

    def test_a_FINITE_value_is_still_accepted(self):
        assert api.limiter_settings(
            {"SUBSYSTEM_STORE_LOCKOUT_S": "42.5"}
        ) == (5, 60.0, 42.5)


class TestSlowlorisCannotPinAThreadForever:
    def test_the_handler_declares_a_socket_TIMEOUT(self):
        # `timeout = None` is the stdlib default and means "wait forever".
        # Measured before the fix: 50 half-open connections held 50 threads.
        assert api.StoreRequestHandler.timeout is not None
        assert 0 < api.StoreRequestHandler.timeout <= 60

    def test_a_HALF_OPEN_connection_is_dropped_rather_than_held(self, store: Path):
        """Behavioural, not a constant check: send headers with no terminating
        blank line and watch the server give up on its own.
        """
        import socket
        import time

        with running(store) as (base, _):
            host = base.split("//", 1)[1]
            name, port = host.split(":")
            sock = socket.create_connection((name, int(port)), timeout=10)
            try:
                sock.sendall(b"GET /healthz HTTP/1.1\r\n")  # never finished
                sock.settimeout(api.StoreRequestHandler.timeout + 10)
                started = time.monotonic()
                data = sock.recv(65536)  # returns b"" when the server closes
                elapsed = time.monotonic() - started
            finally:
                sock.close()
        assert data == b"", f"expected a close, got {data[:60]!r}"
        assert elapsed < api.StoreRequestHandler.timeout + 5


# =============================================================================
# 17. THE DELTA AUDIT ROUND — the fixes in section 16 introduced three
# criticals, in the same commit that closed two.
#
# 🔴 THIS IS THE POINT OF RE-AUDITING THE DELTA, and it is not a formality:
# every round in this repo's history has found a real defect in the PRECEDING
# round's fix. Here the `send_error` override written to make unknown verbs
# uniform reopened BOTH defects the rest of that commit closed — an unhandled
# crash on a malformed request line, and a free unmetered channel — one screen
# below the code that exists to prevent them.
# =============================================================================


def _speak(host: str, payload: bytes, *, wait: float = 3.0) -> bytes:
    """Write raw bytes to a socket and read whatever comes back."""
    import socket

    name, port = host.split(":")
    with socket.create_connection((name, int(port)), timeout=10) as sock:
        sock.sendall(payload)
        sock.settimeout(wait)
        data = b""
        try:
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        except (TimeoutError, OSError):
            pass
    return data


class TestMalformedRequestLinesDoNotCrash:
    """🔴 `parse_request` assigns `self.path` only AFTER five of its own
    `send_error` calls, and `path` has no class-level default — so an override
    that read `self.path` raised AttributeError on every malformed request LINE.
    Measured: 6 of 7 probe shapes crashed, no audit record, ~25 lines of
    traceback each, from a six-byte request.
    """

    # Each is a request LINE the stdlib rejects BEFORE assigning self.path.
    SHAPES = [
        # A three-component version: `parse_request` raises on the split.
        # ⚠ THREE components, deliberately not four. A four-component
        # version string is indistinguishable from an IPv4 literal, and
        # `test_no_public_ips` reads it as one — correctly, in a PUBLIC
        # repo. This comment names the shape rather than quoting it, for
        # the same reason: an explanation that quotes the banned value is
        # the banned value.
        b"GET /x HTTP/1.1.1\r\n\r\n",
        b"GET /x HTTP/2.0\r\n\r\n",
        b"GET\r\n\r\n",
        b"POST /x\r\n\r\n",
        b"GET /x y HTTP/1.1\r\n\r\n",
    ]

    @pytest.mark.parametrize("shape", SHAPES, ids=lambda s: s.split(b"\r\n")[0].decode())
    def test_it_answers_instead_of_crashing(self, store: Path, shape: bytes):
        with running(store) as (base, audit):
            data = _speak(base.split("//", 1)[1], shape)
        assert data, "the request got no response at all"
        assert b"unauthorized\n" in data
        # And it was RECORDED — a crash produces no audit line, which is what
        # made this invisible to every wire-level assertion.
        assert len(audit) == 1, f"{len(audit)} audit lines for one request"
        assert "auth=fail" in audit[0]

    def test_the_audit_line_survives_a_missing_request_path(self, store: Path):
        with running(store) as (base, audit):
            _speak(base.split("//", 1)[1], b"GET\r\n\r\n")
        assert "path=-" in audit[0], audit[0]
        assert "status=malformed-request" in audit[0]
        # 🔴 `peer=-`, THE THIRD VALUE OF THAT FIELD, AND THE ONLY ONE NOTHING
        # ASSERTED. Six bytes is too little to have headers, so `send_error`
        # answers before `_identify_and_meter` ever runs and `_peer_trusted` is
        # still `None`. Found by a mutation sweep with NO `-k` selector:
        # rendering that `None` as `trusted` survived a fully green suite —
        # `peer=trusted|untrusted` were asserted eleven times and `peer=-` zero.
        #
        # The untested direction is the dangerous one. It makes the audit log
        # assert TRUST about a request whose peer was never evaluated, which is
        # the one claim this field exists to let an operator rely on.
        assert "peer=-" in audit[0], audit[0]
        assert "peer=trusted" not in audit[0], audit[0]

    def test_POSITIVE_CONTROL_a_WELL_FORMED_unknown_verb_still_works(
        self, store: Path
    ):
        """The shape the previous round DID test — the one where `self.path`
        happens to be set, which is exactly why the crash stayed invisible.
        """
        with running(store) as (base, audit):
            data = _speak(
                base.split("//", 1)[1],
                f"FROBNICATE /api/v1/recall/{SCOPE} HTTP/1.1\r\n"
                f"Host: h\r\nCF-Connecting-IP: {CLIENT_IP}\r\n\r\n".encode(),
            )
        assert b"401" in data.split(b"\r\n")[0]
        assert len(audit) == 1


class TestUnknownVerbsAreMeteredToo:
    """The second half of the same regression: the override answered without
    ever metering, so 30 `FROBNICATE`s wrote 30 audit lines and counted for
    nothing — the free channel `_reject_write` had just been reordered to close,
    widened to every verb that is not one of the six with a handler.
    """

    def _verb(self, base: str, verb: str, ip: str | None = CLIENT_IP) -> bytes:
        headers = f"Host: h\r\n" + (f"CF-Connecting-IP: {ip}\r\n" if ip else "")
        return _speak(
            base.split("//", 1)[1],
            f"{verb} /api/v1/recall/{SCOPE} HTTP/1.1\r\n{headers}\r\n".encode(),
        )

    def test_unknown_verb_probing_COUNTS_toward_the_lockout(self, store: Path):
        with running(store) as (base, _):
            for _ in range(5):
                assert b"unauthorized" in self._verb(base, "FROBNICATE")
            code, _h, _b = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
        assert code == 401, "unknown verbs were an unmetered channel"

    def test_an_unknown_verb_with_NO_client_ip_fails_closed(self, store: Path):
        with running(store) as (base, audit):
            data = self._verb(base, "OPTIONS", ip=None)
        assert b"unauthorized" in data
        assert "status=no-client-ip" in audit[0]

    def test_a_MALFORMED_request_line_cannot_be_a_keep_alive_channel(
        self, store: Path
    ):
        """It cannot be metered — there are no headers to identify anyone by —
        so it is bounded the only other way: one request per TCP handshake.
        """
        with running(store) as (base, _):
            data = _speak(base.split("//", 1)[1], b"GET\r\n\r\n" + b"GET\r\n\r\n")
        assert data.count(b"unauthorized\n") == 1, "the connection was reused"


class TestSmugglingViaOtherFramings:
    """🔴 The drain understood exactly ONE framing, and a delta audit walked it
    with two others — each producing two responses on one socket with store
    content in the second, on a 200 where the connection legitimately stays open
    so the close-on-non-200 belt does not apply.
    """

    def _smuggled(self, host: str) -> bytes:
        return (
            f"GET /api/v1/recall/{SCOPE} HTTP/1.1\r\nHost: {host}\r\n"
            f"Authorization: Bearer {GOOD_TOKEN}\r\n"
            f"CF-Connecting-IP: {CLIENT_IP}\r\n\r\n"
        ).encode()

    def test_a_CHUNKED_body_cannot_smuggle_a_request(self, store: Path):
        with running(store) as (base, _):
            host = base.split("//", 1)[1]
            body = self._smuggled(host)
            payload = (
                f"GET /healthz HTTP/1.1\r\nHost: {host}\r\n"
                f"Transfer-Encoding: chunked\r\n\r\n"
                f"{len(body):x}\r\n"
            ).encode() + body + b"\r\n0\r\n\r\n"
            data = _speak(host, payload)
        assert data.count(b"HTTP/1.1 ") <= 1, "chunked framing smuggled a request"
        assert POINTER_LINE.encode() not in data

    def test_a_NEGATIVE_content_length_cannot_smuggle_a_request(self, store: Path):
        with running(store) as (base, _):
            host = base.split("//", 1)[1]
            payload = (
                f"GET /healthz HTTP/1.1\r\nHost: {host}\r\n"
                f"Content-Length: -5\r\n\r\n"
            ).encode() + self._smuggled(host)
            data = _speak(host, payload)
        assert data.count(b"HTTP/1.1 ") <= 1, "a negative length smuggled a request"
        assert POINTER_LINE.encode() not in data

    def test_POSITIVE_CONTROL_the_probe_CAN_see_a_second_response(self, store: Path):
        with running(store) as (base, _):
            host = base.split("//", 1)[1]
            one = f"GET /healthz HTTP/1.1\r\nHost: {host}\r\n\r\n".encode()
            data = _speak(host, one + one)
        assert data.count(b"HTTP/1.1 ") == 2, "the probe cannot see two responses"

    def test_a_DRIPPED_body_cannot_hold_a_thread_indefinitely(self, store: Path):
        """`timeout` is per-RECV, so a caller that sends a byte before each
        timeout expires satisfies it forever — measured holding a thread 60s for
        a SIX-byte body. The read loop needed its own total deadline.

        🔴 THE FIRST VERSION OF THIS TEST WAS VACUOUS, and a mutation sweep said
        so: it sent ONE byte and then waited, so the per-recv timeout ended the
        connection at ~15s either way and the assertion (`< deadline + 15`) was
        satisfied with the deadline deleted. A drip test has to actually DRIP —
        fast enough that the per-recv timeout never fires — or it measures the
        socket timeout and calls it the deadline.
        """
        import socket
        import threading
        import time

        assert api.DRAIN_DEADLINE_S <= 30
        interval = api.DRAIN_DEADLINE_S / 4
        assert interval < api.StoreRequestHandler.timeout, (
            "the drip must outpace the per-recv timeout, or this measures that"
        )
        with running(store) as (base, _):
            name, port = base.split("//", 1)[1].split(":")
            sock = socket.create_connection((name, int(port)), timeout=10)
            stop = threading.Event()

            def drip():
                # 200 bytes at `interval` apart would take 50 * DEADLINE if the
                # deadline did not exist.
                for _ in range(200):
                    if stop.wait(interval):
                        return
                    try:
                        sock.sendall(b"x")
                    except OSError:
                        return

            try:
                sock.sendall(
                    f"POST /api/v1/recall/{SCOPE} HTTP/1.1\r\nHost: h\r\n"
                    f"CF-Connecting-IP: {CLIENT_IP}\r\n"
                    f"Content-Length: 200\r\n\r\n".encode()
                )
                started = time.monotonic()
                dripper = threading.Thread(target=drip, daemon=True)
                dripper.start()
                sock.settimeout(api.DRAIN_DEADLINE_S * 6)
                try:
                    sock.recv(65536)
                except (TimeoutError, OSError):
                    pass
                elapsed = time.monotonic() - started
            finally:
                stop.set()
                sock.close()
        # Without the deadline the drip keeps the connection alive far past this.
        assert elapsed < api.DRAIN_DEADLINE_S * 3, (
            f"a dripped body held the thread for {elapsed:.1f}s"
        )


class TestTheLockoutCapDoesNotLIE:
    """At the cap, `record_failure` returned True regardless AND popped the
    streak: the audit log claimed `lockout-triggered` for a client that was not
    locked out, and no state accumulated at all — unlimited brute force for an
    attacker who had first filled the table.

    🔴 The cap is monkeypatched DOWN rather than filled. Filling the real one is
    81,920 calls, which made the suite 45s slower and — worse — forced the
    "a slot frees" case to advance the clock past the lockout, which also aged
    out the attacker's own streak and tested nothing. A small cap reaches the
    same branch with the states actually distinguishable.
    """

    CAP = 8

    def _full(self, now: list[float], monkeypatch, **kwargs):
        monkeypatch.setattr(api, "MAX_TRACKED_LOCKOUTS", self.CAP)
        lim = api.RateLimiter(clock=lambda: now[0], **kwargs)
        for i in range(self.CAP):
            for _ in range(5):
                lim.record_failure(f"filler{i}")
            now[0] += 0.0001
        assert len(lim._locked_until) == self.CAP
        return lim

    def test_at_the_cap_it_reports_FALSE_rather_than_a_lockout_it_did_not_make(
        self, monkeypatch
    ):
        now = [1000.0]
        lim = self._full(now, monkeypatch)
        results = [lim.record_failure("attacker") for _ in range(5)]
        assert results[-1] is False, "it claimed a lockout the table had no room for"
        assert lim.locked_out("attacker") is False

    def test_at_the_cap_the_streak_is_KEPT_so_state_still_accumulates(
        self, monkeypatch
    ):
        """The half that matters for brute force: popping the streak meant every
        five failures started over, so nothing ever accumulated. Keeping it holds
        the client AT the threshold, so the lockout lands the moment a slot frees.

        The lockout is deliberately SHORTER than the window here, so a filler can
        expire while the attacker's streak is still live — the only arrangement
        in which "a slot frees" is observable at all.
        """
        now = [1000.0]
        lim = self._full(now, monkeypatch, lockout_s=5.0, window_s=600.0)
        for _ in range(9):
            lim.record_failure("attacker")
        assert len(lim._failures.get("attacker", [])) >= 5, "the streak was reset"
        now[0] += 6.0  # fillers expire; the attacker's streak is still in-window
        assert lim.record_failure("attacker") is True
        assert lim.locked_out("attacker") is True

    def test_EXPIRED_lockouts_are_released_so_the_table_DRAINS(self, monkeypatch):
        """🔴 The release loop had ZERO coverage — a surviving mutant in the
        previous sweep — and it is the only thing that drains `_locked_until`,
        i.e. the only reason the cap above is not permanent.
        """
        now = [1000.0]
        lim = self._full(now, monkeypatch, lockout_s=5.0, window_s=600.0)
        now[0] += 6.0
        lim.record_failure("anyone")
        assert len(lim._locked_until) < self.CAP, "the table never drained"


class TestEveryAuditFieldIsEscaped:
    def test_the_METHOD_is_escaped_not_just_the_path(self, store: Path):
        """🔴 A surviving mutant with zero coverage: a verb can carry control
        bytes, and the method field went into the record unescaped.
        """
        with running(store) as (base, audit):
            _speak(
                base.split("//", 1)[1],
                f"FROB\x1b[31mNICATE /api/v1/recall/{SCOPE} HTTP/1.1\r\n"
                f"Host: h\r\nCF-Connecting-IP: {CLIENT_IP}\r\n\r\n".encode(),
            )
        assert audit, "no audit line was written"
        assert "\x1b" not in audit[0], "an escape sequence reached the log"
        assert "\n" not in audit[0]


# =============================================================================
# 18. THE FINAL AUDIT ROUND — a third framing, and the loop nothing looped.
# =============================================================================


class TestDuplicateContentLengthCannotSmuggle:
    """🔴 A WORKING SMUGGLE, found by a final audit, in the function whose own
    comment claimed no framing could walk it.

    `Content-Length: 0` followed by `Content-Length: 154`: a bare `.get()` takes
    the FIRST value, so nothing is drained and the body is served as the next
    request — on a `/healthz` 200, where the connection legitimately stays open
    and the close-on-non-200 belt does not apply.

    The predicate that catches it already existed TWENTY LINES AWAY, in
    `client_ip`, rejecting a duplicated `CF-Connecting-IP` for exactly this
    reason. Open-coded twice, correct at one site and wrong at the other — which
    is the shape a duplicated predicate always takes. It is now one function.
    """

    def _smuggled(self, host: str) -> bytes:
        return (
            f"GET /api/v1/recall/{SCOPE} HTTP/1.1\r\nHost: {host}\r\n"
            f"Authorization: Bearer {GOOD_TOKEN}\r\n"
            f"CF-Connecting-IP: {CLIENT_IP}\r\n\r\n"
        ).encode()

    def test_two_content_length_headers_cannot_smuggle_a_request(self, store: Path):
        with running(store) as (base, _):
            host = base.split("//", 1)[1]
            body = self._smuggled(host)
            payload = (
                f"GET /healthz HTTP/1.1\r\nHost: {host}\r\n"
                f"Content-Length: 0\r\nContent-Length: {len(body)}\r\n\r\n"
            ).encode() + body
            data = _speak(host, payload)
        assert data.count(b"HTTP/1.1 ") <= 1, "a duplicated Content-Length smuggled"
        assert POINTER_LINE.encode() not in data

    def test_the_LARGER_value_first_is_refused_too(self, store: Path):
        """Order must not matter — a guard that only looked at the first value
        would pass the test above by accident if the smaller one came second.
        """
        with running(store) as (base, _):
            host = base.split("//", 1)[1]
            body = self._smuggled(host)
            payload = (
                f"GET /healthz HTTP/1.1\r\nHost: {host}\r\n"
                f"Content-Length: {len(body)}\r\nContent-Length: 0\r\n\r\n"
            ).encode() + body
            data = _speak(host, payload)
        assert data.count(b"HTTP/1.1 ") <= 1
        assert POINTER_LINE.encode() not in data

    def test_the_shared_predicate_is_used_by_BOTH_sites(self):
        """Structural, and deliberately so: the behavioural tests above and the
        duplicate-`CF-Connecting-IP` test elsewhere would both stay green if the
        two sites drifted apart again into two correct-today copies. What is
        being pinned is that there is ONE predicate.
        """
        code = _executable_tokens(SERVER_PATH)
        assert code.count("get_all") <= 2, (
            "header-multiplicity logic is open-coded again; it belongs in "
            "sole_header()"
        )
        assert "sole_header" in code

    def test_sole_header_directly(self):
        assert api.sole_header({"X": "1"}, "X") == "1"
        assert api.sole_header({}, "X") is None


class TestTheDrainLoopActuallyLOOPS:
    """🔴 A GAP THIS BRANCH CREATED. Under the old `read(n)` the drain consumed
    the whole body in one call; `read1` is what made the loop bookkeeping
    load-bearing — and every other smuggling test sends headers and body in ONE
    `sendall`, so the body arrives in one segment, the loop runs once, and the
    accumulator is never exercised. Two mutants survived the whole suite.

    🔴 THE DELIVERY SHAPE DECIDES WHICH DIRECTION IS OBSERVABLE, and a first
    version of these tests missed that. Under-consumption (a broken accumulator)
    is only visible when the body arrives in SEVERAL segments; over-consumption
    (a dropped `min(remaining, …)` clamp) is only visible when the body and the
    NEXT request arrive in ONE segment, because `read1` can only over-read bytes
    that are already buffered. One shape cannot see both.
    """

    def _exchange(self, store: Path, *, segmented: bool, fill: int = 4000) -> bytes:
        import socket
        import time

        with running(store) as (base, _):
            host = base.split("//", 1)[1]
            name, port = host.split(":")
            smuggled = (
                f"GET /api/v1/recall/{SCOPE} HTTP/1.1\r\nHost: {host}\r\n"
                f"Authorization: Bearer {GOOD_TOKEN}\r\n"
                f"CF-Connecting-IP: {CLIENT_IP}\r\n\r\n"
            ).encode()
            body = b"F" * fill + smuggled
            follow = f"GET /healthz HTTP/1.1\r\nHost: {host}\r\n\r\n".encode()
            head = (
                f"GET /healthz HTTP/1.1\r\nHost: {host}\r\n"
                f"Content-Length: {len(body)}\r\n\r\n"
            ).encode()
            with socket.create_connection((name, int(port)), timeout=10) as sock:
                if segmented:
                    # Separate sends with gaps: each `read1` returns only what
                    # has arrived, so the loop MUST iterate and accumulate.
                    sock.sendall(head)
                    time.sleep(0.05)
                    for at in range(0, len(body), 512):
                        sock.sendall(body[at : at + 512])
                        time.sleep(0.01)
                    time.sleep(0.05)
                    sock.sendall(follow)
                else:
                    # One segment: everything is buffered at once, so a `read1`
                    # without the clamp will swallow `follow` too.
                    sock.sendall(head + body + follow)
                sock.settimeout(4)
                data = b""
                try:
                    while True:
                        chunk = sock.recv(65536)
                        if not chunk:
                            break
                        data += chunk
                except (TimeoutError, OSError):
                    pass
        return data

    def test_a_SEGMENTED_body_is_drained_WHOLE(self, store: Path):
        """UNDER-consumption. A broken accumulator stops after the first
        `read1`, leaving the tail to be parsed as the next request — which both
        loses the caller's real next request and can serve the smuggled one.
        """
        data = self._exchange(store, segmented=True)
        assert POINTER_LINE.encode() not in data, "a segmented body leaked store content"
        assert data.count(b"HTTP/1.1 200") == 2, (
            f"expected both /healthz answers, got {data.count(b'HTTP/1.1 200')}: "
            f"{data[:140]!r}"
        )

    def test_a_SINGLE_SEGMENT_body_is_drained_NO_FURTHER(self, store: Path):
        """OVER-consumption, the direction the segmented case structurally
        cannot see. Without the `min(remaining, …)` clamp the drain reads the
        following pipelined request out of the buffer and answers it never —
        fail-safe for smuggling, but it silently breaks keep-alive, and "safe by
        accident in one direction" is not a property to leave untested.
        """
        data = self._exchange(store, segmented=False)
        assert POINTER_LINE.encode() not in data
        assert data.count(b"HTTP/1.1 200") == 2, (
            f"the drain ate the next request: {data.count(b'HTTP/1.1 200')} "
            f"responses, {data[:140]!r}"
        )

    def test_POSITIVE_CONTROL_both_shapes_answer_TWICE_when_correct(
        self, store: Path
    ):
        """Both assertions above are `== 2`, so a server that answered nothing
        would fail them — but a HARNESS that could never see two would fail them
        identically. This pins that the shapes themselves are well-formed.
        """
        for segmented in (True, False):
            data = self._exchange(store, segmented=segmented, fill=16)
            assert data.count(b"ok\n") == 2, f"segmented={segmented}: {data[:140]!r}"


# =============================================================================
# 18. `CF-Connecting-IP` WAS TRUSTED FROM ANY PEER (phase 1.5b)
#
# 🔴 THIS SECTION IS THE ONE PLACE IN THIS FILE WITH REAL REGRESSION COVERAGE,
# and only the `TestTrustedProxyOverTheRealProcess` half is. The distinction
# matters and the file's header explains why: `server.py` did not exist before
# phase 1, so everything else here is red at ITS base for a collection error.
# Phase 1.5b's base ref is different — `server.py` exists, it parses the same
# command line, and it IGNORES `$SUBSYSTEM_STORE_TRUSTED_PROXIES` completely.
# So a test that spawns the real process with that variable set runs on BOTH
# trees and its failure at base is a statement about BEHAVIOUR:
#
#   * base honours a `CF-Connecting-IP` from an untrusted peer, and locks the
#     named third party out in five requests   <- THE DEFECT
#   * base serves 200 to a valid token from an untrusted peer
#   * base STARTS with the variable unset
#
# The in-process classes below are invariant guards. They are labelled as such
# and their evidence is the mutation matrix in the PR body, not their red.
# =============================================================================


class TestTrustedProxyOverTheRealProcess:
    """🔴 RED AT THE BASE REF BEHAVIOURALLY, not by AttributeError."""

    @pytest.fixture
    def token_file(self, tmp_path: Path) -> Path:
        path = tmp_path / "token"
        path.write_text(GOOD_TOKEN + "\n")
        return path

    def test_POSITIVE_CONTROL_a_TRUSTED_peer_still_gets_its_digest(
        self, store: Path, token_file: Path
    ):
        """Before any 401 below is read as "the guard fired": this exact call
        shape, against a process spawned exactly this way, CAN return a 200 with
        store content. Without it a server that refused everything — including
        one that failed to read its store at all — would pass every assertion in
        this class.
        """
        with running_subprocess(store, token_file) as (base, _proc):
            code, headers, body = fetch(
                f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, client_ip=CLIENT_IP
            )
        assert code == 200, body
        assert headers.get("X-Store-Status") == "recalled"
        assert POINTER_LINE.encode() in body

    def test_THE_DEFECT_a_forged_header_from_an_untrusted_peer_locks_out_a_victim(
        self, store: Path, token_file: Path
    ):
        """🔴 THE WHOLE POINT OF THIS BRANCH, and the one test whose red at base
        is the bug rather than the diff.

        ONE running process, TWO peers. `127.0.0.1` is the allowlisted proxy;
        `127.0.0.2` is anything that can address the pod directly. The attacker
        binds `127.0.0.2` and sends five bad tokens while CLAIMING, in the
        header, to be `SPOOF_IP`. Then the legitimate proxy forwards a request
        for that same `SPOOF_IP` client, with a VALID token.

          base: the five are charged to SPOOF_IP -> the victim is 401 for 15 min
          HEAD: the five are charged to 127.0.0.2, the forger's own address
                -> the victim gets its 200

        ⚠ "not one of them reaches the limiter" is what this line used to say,
        and it described the refuse-outright design that was replaced. They DO
        reach it now, and should: five failed auths from one caller is a real
        lockout — of that caller. The property is only ever about WHOSE bucket,
        which is what the sibling
        `test_THE_DEFECT_the_five_forged_attempts_are_CHARGED_TO_THE_FORGER`
        reads out of the audit line.

        🔴 THE FIRST DRAFT OF THIS TEST WAS VACUOUS AND PASSED AT BASE. It ran
        every request from ONE address and asserted the victim saw a 401 — which
        is true on both trees, because the wire deliberately does not
        discriminate. A test of a lockout has to observe the VICTIM getting
        through, and that needs a second peer.
        """
        with running_subprocess(
            store,
            token_file,
            trusted_proxies=f"{TRUSTED_PEER}/32",
            host=TRUSTED_PEER,
        ) as (base, _proc):
            for _ in range(5):
                attempt = fetch_from(
                    UNTRUSTED_PEER,
                    base,
                    f"/api/v1/recall/{SCOPE}",
                    token="w" * 48,
                    client_ip=SPOOF_IP,
                )
                assert attempt == 401, attempt
            victim = fetch_from(
                TRUSTED_PEER,
                base,
                f"/api/v1/recall/{SCOPE}",
                token=GOOD_TOKEN,
                client_ip=SPOOF_IP,
            )
        assert victim == 200, (
            f"the victim got {victim}: five requests from an UNTRUSTED peer, "
            f"forging CF-Connecting-IP, locked out a third party"
        )

    def test_POSITIVE_CONTROL_the_same_five_from_a_TRUSTED_peer_DO_lock_out(
        self, store: Path, token_file: Path
    ):
        """🔴 THE OTHER HALF, and without it the test above is satisfied by a
        server with no lockout at all — including one where the limiter was
        deleted outright. Identical shape, identical count, ONE difference: the
        five bad tokens come from the ALLOWLISTED peer, so they are a real
        client failing auth and the lockout must fire.
        """
        with running_subprocess(
            store,
            token_file,
            trusted_proxies=f"{TRUSTED_PEER}/32",
            host=TRUSTED_PEER,
        ) as (base, _proc):
            for _ in range(5):
                fetch_from(
                    TRUSTED_PEER,
                    base,
                    f"/api/v1/recall/{SCOPE}",
                    token="w" * 48,
                    client_ip=SPOOF_IP,
                )
            victim = fetch_from(
                TRUSTED_PEER,
                base,
                f"/api/v1/recall/{SCOPE}",
                token=GOOD_TOKEN,
                client_ip=SPOOF_IP,
            )
        assert victim == 401, (
            f"got {victim}: five FAILED AUTHS from a real client did not lock "
            f"it out, so the test above proves nothing"
        )

    def test_THE_DEFECT_the_five_forged_attempts_are_CHARGED_TO_THE_FORGER(
        self, store: Path, token_file: Path
    ):
        """The status-code half cannot see WHO was charged. At base the five
        forged attempts are booked against `ip=198.51.100.7` — the victim — and
        the fifth is `status=lockout-triggered`. At HEAD every one is booked
        against the forger's own address with `peer=untrusted`. Read off the
        process's real STDOUT, which is the stream Loki ingests.

        ⚠ THIS IS NOT "the attempts do not reach the limiter" ANY MORE. They do,
        and they should: an untrusted caller failing auth five times is a real
        failed auth and gets a real lockout — of ITSELF. The property is only
        ever about WHOSE bucket.
        """
        # 🔴 WAIT FOR THE AUDIT LINES; DO NOT ASSUME THE RESPONSE IMPLIES THEM.
        # The measurement that found this race (#544): 3/20 red locally and two
        # consecutive reds in the nix sandbox, always `assert 4 == 5` with four
        # identical audit lines. Re-running was the wrong answer — a ~15% flaky
        # gate is the thing that teaches everyone to click through a red run.
        # The mechanism and the reason this is now shared rather than copied are
        # on `drain_output`.
        with running_subprocess(
            store,
            token_file,
            trusted_proxies=f"{TRUSTED_PEER}/32",
            host=TRUSTED_PEER,
        ) as (base, proc):
            out = drain_output(proc)
            for _ in range(5):
                fetch_from(
                    UNTRUSTED_PEER,
                    base,
                    f"/api/v1/recall/{SCOPE}",
                    token="w" * 48,
                    client_ip=SPOOF_IP,
                )
            lines = await_audit(out, 5)
        assert len(lines) == 5, lines
        # 🔴 THE ASSERTION THAT IS THE WHOLE DEFECT: the forged address never
        # becomes an identity. A fix that recorded the spoofed value but declined
        # to COUNT it would pass every status check and fail this one.
        assert all(f"ip={SPOOF_IP}" not in ln for ln in lines), lines
        assert all(f"ip={UNTRUSTED_PEER}" in ln for ln in lines), lines
        assert all("peer=untrusted" in ln for ln in lines), lines
        # …and the forger locks out ITSELF, which is a rate limiter working.
        #
        # 🔴 READ AS A MULTISET, NEVER AS `lines[-1]`. `await_audit` guarantees
        # the five lines EXIST; it cannot guarantee the ORDER they were appended
        # in, and it never could. The five requests are issued sequentially and
        # the limiter is charged before the response, so the fifth is the one
        # that trips — but each handler writes its response and only then races
        # to `_audit()`, so request 5's record can be appended before request
        # 4's. `lines[-1]` then holds a plain `unauthorized` and the test is red
        # on a tree with nothing wrong with it. The multiset says exactly what
        # the sentence above says — five attempts, one of them the lockout — and
        # says it about all five records instead of one.
        statuses = sorted(ln.split("status=")[1].split()[0] for ln in lines)
        assert statuses == ["lockout-triggered"] + ["unauthorized"] * 4, (
            f"expected four unauthorized and one lockout-triggered, got "
            f"{statuses}:\n" + "\n".join(lines))

    def test_an_untrusted_peer_LOCKING_ITSELF_OUT_does_not_touch_the_victim(
        self, store: Path, token_file: Path
    ):
        """The pair to the test above, and the one that stops "charge the peer"
        from being a euphemism for "do not charge anything". The forger is
        locked out — its own next request is refused — while the client it named
        is served normally through the trusted proxy.
        """
        with running_subprocess(
            store,
            token_file,
            trusted_proxies=f"{TRUSTED_PEER}/32",
            host=TRUSTED_PEER,
        ) as (base, _proc):
            for _ in range(5):
                fetch_from(
                    UNTRUSTED_PEER,
                    base,
                    f"/api/v1/recall/{SCOPE}",
                    token="w" * 48,
                    client_ip=SPOOF_IP,
                )
            forger = fetch_from(
                UNTRUSTED_PEER,
                base,
                f"/api/v1/recall/{SCOPE}",
                token=GOOD_TOKEN,
                client_ip=SPOOF_IP,
            )
            victim = fetch_from(
                TRUSTED_PEER,
                base,
                f"/api/v1/recall/{SCOPE}",
                token=GOOD_TOKEN,
                client_ip=SPOOF_IP,
            )
        assert forger == 401, f"the forger was not locked out: {forger}"
        assert victim == 200, f"the victim was collateral: {victim}"

    def test_a_TRUSTED_peers_HEADER_actually_separates_two_clients(
        self, store: Path, token_file: Path
    ):
        """🔴 THE ONE PROPERTY EVERY OTHER PROCESS-LEVEL TEST HERE IS BLIND TO,
        and a mutation sweep is what showed it: replacing the allowlist `main`
        passes to `build_server` with a hardcoded one that matches NOBODY
        survived the whole class. Every test either drove an untrusted peer (a
        mutant that trusts nobody agrees) or drove one client through a trusted
        peer (bucketing it under the peer instead of its header is invisible
        when there is only one client).

        So: five failures through the trusted proxy on behalf of ONE client,
        then a DIFFERENT client through the SAME proxy. It must be served — the
        header, not the peer, is what separated them. If the proxy's allowlist
        were not in force, both would share the peer's bucket and the second
        client would be collateral.
        """
        with running_subprocess(
            store,
            token_file,
            trusted_proxies=f"{TRUSTED_PEER}/32",
            host=TRUSTED_PEER,
        ) as (base, _proc):
            for _ in range(5):
                fetch_from(
                    TRUSTED_PEER,
                    base,
                    f"/api/v1/recall/{SCOPE}",
                    token="w" * 48,
                    client_ip=SPOOF_IP,
                )
            same_client = fetch_from(
                TRUSTED_PEER,
                base,
                f"/api/v1/recall/{SCOPE}",
                token=GOOD_TOKEN,
                client_ip=SPOOF_IP,
            )
            other_client = fetch_from(
                TRUSTED_PEER,
                base,
                f"/api/v1/recall/{SCOPE}",
                token=GOOD_TOKEN,
                client_ip=OTHER_IP,
            )
        # Both halves, or the assertion is satisfied by a server with no limiter
        # (everything 200) or one that locked the whole proxy out (everything
        # 401).
        assert same_client == 401, f"the guilty client was not locked out: {same_client}"
        assert other_client == 200, (
            f"an innocent client behind the same proxy got {other_client} — the "
            f"bucket was the PEER, not the header"
        )

    def test_a_VALID_token_from_an_untrusted_peer_is_SERVED_but_bucketed_under_the_PEER(
        self, store: Path, token_file: Path
    ):
        """🔴 THIS REPLACES A TEST THAT PINNED THE WRONG BEHAVIOUR. It used to
        assert a 401, which was stricter than the security property needs and
        broke the phase-1 acceptance procedure outright: `verify-byte-identity.sh`
        runs through `kubectl port-forward`, which presents peer `127.0.0.1`
        while the pod allowlists the node's Cilium internal address, so every
        byte-identity run — THE phase-1 criterion — became a 401.

        Serving it is safe because the header it sent is IGNORED: the request is
        booked against the peer, so the caller can only ever spend its own
        budget. Distrust is expressed by disbelieving the caller, not by hanging
        up on them.

        🔴 The replacement is NOT weaker than what it replaces. The property the
        old test was reaching for — a forged header must never name a third
        party — is pinned by
        `test_THE_DEFECT_a_forged_header_from_an_untrusted_peer_locks_out_a_victim`
        and by the `ip=` assertions above, both of which are still RED at base.
        """
        with running_subprocess(
            store,
            token_file,
            trusted_proxies=f"{TRUSTED_PEER}/32",
            host=TRUSTED_PEER,
        ) as (base, proc):
            # 🔴 THIS SITE IS WHY `drain_output` EXISTS. It used to terminate on
            # the client's return and read the corpse's stdout, so a slow handler
            # lost the line and `[...][-1]` raised `IndexError: list index out of
            # range` — an index into an empty list, not a useful assertion.
            # MEASURED 2026-08-23T00:37Z on `devrc-ci-jxf5j`, in the nix sandbox, on a
            # tree whose only change was to an unrelated test, while the same
            # commit passed a local `nix build`.
            out = drain_output(proc)
            code = fetch_from(
                UNTRUSTED_PEER,
                base,
                f"/api/v1/recall/{SCOPE}",
                token=GOOD_TOKEN,
                client_ip=SPOOF_IP,
            )
            # `await_audit` RAISES if the line never arrives, so `[-1]` below
            # cannot be the IndexError this whole change exists to remove.
            lines = await_audit(out, 1)
        assert code == 200, code
        line = lines[-1]
        assert f"ip={UNTRUSTED_PEER}" in line, line
        assert f"ip={SPOOF_IP}" not in line, line
        assert "peer=untrusted" in line, line
        # 🔴 AND IT IS NOT SPELLED AS AN AUTH FAILURE. The earlier shape emitted
        # `status=untrusted-peer auth=fail`, which put every port-forward into
        # the Loki auth-fail alert and trained the operator to ignore it.
        assert "auth=ok" in line, line
        assert "auth=fail" not in line, line
        assert "status=untrusted-peer" not in line, line

    def test_healthz_is_answered_for_an_UNTRUSTED_peer_so_the_kubelet_probe_lives(
        self, store: Path, token_file: Path
    ):
        """🔴 THE FAILURE MODE THAT GETS A SECURITY GUARD DELETED. The kubelet
        probes from the node, sends no `CF-Connecting-IP`, and is not the
        gateway — so if the peer gate ran before `/healthz` the pod would never
        become Ready and the guard would be reverted within the hour.

        `running_subprocess` already waits on `/healthz` to decide the server is
        up, so an ordinary spawn would prove this by accident. This one spawns
        with an allowlist that excludes loopback ENTIRELY and asserts the body.
        """
        with running_subprocess(
            store, token_file, trusted_proxies=NOT_LOOPBACK_PROXY
        ) as (base, _proc):
            code, _headers, body = fetch(f"{base}{'/healthz'}", client_ip=None)
        assert code == 200
        assert body == b"ok\n"

    def test_the_process_REFUSES_TO_START_with_the_variable_unset(
        self, store: Path, token_file: Path
    ):
        """Exit 78, on stderr, naming the variable. At base the process starts
        happily and serves — which is the whole reason this must not default.
        """
        proc = subprocess.run(
            [
                sys.executable,
                str(SERVER_PATH),
                "--store",
                str(store),
                "--host",
                "127.0.0.1",
                "--port",
                str(_free_port()),
                "--token-file",
                str(token_file),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=_child_env(None),
        )
        assert proc.returncode == 78, (proc.returncode, proc.stdout, proc.stderr)
        assert "SUBSYSTEM_STORE_TRUSTED_PROXIES" in proc.stderr
        assert "no trusted proxies" in proc.stderr

    def test_the_process_REFUSES_TO_START_on_a_DEFAULT_ROUTE(
        self, store: Path, token_file: Path
    ):
        """`0.0.0.0/0` is the pre-fix behaviour spelled as configuration, and it
        is the value an operator reaches for at 2am. Requiring the variable to
        be SET does not catch it; only refusing the value does.
        """
        proc = subprocess.run(
            [
                sys.executable,
                str(SERVER_PATH),
                "--store",
                str(store),
                "--host",
                "127.0.0.1",
                "--port",
                str(_free_port()),
                "--token-file",
                str(token_file),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=_child_env("0.0.0.0/0"),
        )
        assert proc.returncode == 78, (proc.returncode, proc.stdout, proc.stderr)
        assert "trusts every peer" in proc.stderr

    def test_the_startup_line_NAMES_the_trusted_proxies(
        self, store: Path, token_file: Path
    ):
        """"Which peers may set the client identity" must be readable out of a
        running pod. It is configuration, not a credential.
        """
        with running_subprocess(
            store, token_file, trusted_proxies=NOT_LOOPBACK_PROXY
        ) as (_base, proc):
            proc.terminate()
            stdout, _err = proc.communicate(timeout=15)
        assert f"trusted-proxies={NOT_LOOPBACK_PROXY}" in stdout, stdout


# =============================================================================
# THE SEAM GUARDS for the audit-line race that `drain_output`/`await_audit` close.
#
# 🔴 THESE WALK THE AST INTERPROCEDURALLY, AND THAT IS NOT GOLD-PLATING — an
# earlier, single-function version of this guard was walked FIVE ways in an
# adversarial audit, each verified against the real guard with a verbatim racy
# shape as the positive control:
#
#   E1  terminate/communicate moved into a module-level helper   -> passed
#   E2  the prefix read via a different module constant          -> passed
#   E3  proc.send_signal(SIGTERM) instead of terminate           -> passed
#   E4  _c = proc.communicate; _c()   (bound-method alias)       -> passed
#   E5  a racy function merely NAMED `_drain` (an exclusion)     -> passed
#
# E4 is the instructive one: binding the method makes `proc.communicate` an
# `Attribute` inside an `Assign`, never a `Call`, so a walker looking for calls
# never sees it. E5 is worse than a hole — the exclusion list it exploited was
# also DEAD CODE (removing it entirely left the guard green), so it bought
# nothing and granted a permanent bypass. It is gone; the sanctioned helpers are
# not excluded by NAME, they simply never both kill and read.
#
# A guard that reads as coverage while providing little is worse than none,
# because it stops the next person looking.
# =============================================================================

_KILLERS = ("terminate", "kill", "communicate", "send_signal", "wait")

# 🔴 THE ONE SANCTIONED KILLER, AND WHY THIS IS NOT E5's BYPASS IN A NEW COAT.
# `running_subprocess` terminates in its `finally` — that IS the design, and every
# correct call site delegates teardown to it. So its kills must not propagate to
# its callers, or the guard flags exactly the three tests that are RIGHT.
#
# The difference from the exclusion list this replaces: that one skipped functions
# by NAME, so any function called `_drain` inherited a permanent exemption it had
# not earned. This names the context manager whose whole contract is teardown, and
# `test_the_teardown_owner_really_is_one` below FAILS if the named function stops
# being a killer or stops being a context manager — so the entry cannot rot into a
# free pass for something that no longer does the job.
_TEARDOWN_OWNERS = frozenset({"running_subprocess"})


def _module_tree() -> ast.Module:
    return ast.parse(Path(__file__).read_text())


def _audit_prefix_names(tree: ast.Module) -> set[str]:
    """Every module-level name bound to a string that IS the audit prefix.

    Closes E2. Reading the prefix through a second constant is reading the
    prefix; the guard must not care which name you spell it with.
    """
    names = {"AUDIT_PREFIX"}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str) and node.value.value == AUDIT_PREFIX:
                names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return names


def _direct_kills(fn: ast.AST) -> set[str]:
    """Killer verbs reached directly in this function body.

    Counts a CALL (`proc.terminate()`) and also a bare ATTRIBUTE reference
    (`_c = proc.communicate`) — closing E4, where the alias is never a Call.
    """
    hits: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Attribute) and node.attr in _KILLERS:
            hits.add(node.attr)
    return hits


def _direct_reads(fn: ast.AST, prefix_names: set[str]) -> bool:
    """Does this function body reach the audit records directly?"""
    for node in ast.walk(fn):
        if isinstance(node, ast.Constant) and node.value == AUDIT_PREFIX:
            return True
        if isinstance(node, ast.Name) and node.id in prefix_names:
            return True
        # `.audit` on the drained record, and the helper that returns it
        if isinstance(node, ast.Attribute) and node.attr == "audit":
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "await_audit":
            return True
    return False


def _called_names(fn: ast.AST) -> set[str]:
    return {
        n.func.id for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }


def _functions(tree: ast.Module) -> dict:
    return {
        n.name: n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _transitive(seed: dict, funcs: dict) -> dict:
    """Propagate a per-function property through the call graph to a fixed point.

    Closes E1 and E5: moving the terminate into a helper, or naming the helper
    something the guard used to skip, no longer hides it — the property follows
    the calls rather than the spelling.
    """
    prop = dict(seed)
    for _ in range(len(funcs) + 1):
        changed = False
        for name, node in funcs.items():
            if prop.get(name):
                continue
            if any(prop.get(c) for c in _called_names(node)):
                prop[name] = True
                changed = True
        if not changed:
            break
    return prop


def test_no_test_reads_an_AUDIT_LINE_from_a_process_it_just_terminated():
    """🔴 THE SEAM GUARD. The hazard is a RELATIONSHIP inside one test — reading
    audit records out of a stream while also being the thing that killed the
    process producing them. `_respond` runs before `_audit`, so the client's
    return proves nothing about the line, and terminating on it races the
    emission.

    🔴 WHAT IT DELIBERATELY PERMITS:
    `test_the_startup_line_NAMES_the_trusted_proxies` terminates and reads stdout
    too, but reads the STARTUP line — which `running_subprocess` has already
    synchronised on, because a `/healthz` ANSWER requires `serve_forever()` and
    the startup `print(..., flush=True)` runs before it. Verified in `server.py`,
    not assumed. It is permitted by the READ condition (it never touches the
    audit records), not by a name exclusion — so the permission cannot rot into
    a bypass the way E5's exclusion list did.

    🔴 WHAT IT STILL CANNOT SEE, stated so it is not read as more than it is: a
    racy read that never reaches the prefix, `.audit` or `await_audit` — slicing
    stdout positionally, or matching a substring of a record. Killing via a
    non-`_KILLERS` route (`os.kill(proc.pid, ...)`) is also unseen.
    """
    tree = _module_tree()
    funcs = _functions(tree)
    prefix_names = _audit_prefix_names(tree)

    kills_seed = {n: bool(_direct_kills(f)) for n, f in funcs.items()}
    reads_seed = {n: _direct_reads(f, prefix_names) for n, f in funcs.items()}

    # Positive control BEFORE the teardown owner is removed — the detectors must
    # be able to see the real thing, or every result below is a vacuous zero.
    assert reads_seed["await_audit"], "the read detector sees nothing — it is broken"
    assert kills_seed["running_subprocess"], "the kill detector sees nothing — it is broken"

    # Teardown owners neither kill (for propagation) nor pass killing to callers.
    graph = {n: f for n, f in funcs.items() if n not in _TEARDOWN_OWNERS}
    kills = _transitive({n: v for n, v in kills_seed.items() if n in graph}, graph)
    reads = _transitive({n: v for n, v in reads_seed.items() if n in graph}, graph)

    offenders = sorted(
        f"{n} (line {funcs[n].lineno}) kills via "
        f"{sorted(_direct_kills(funcs[n])) or 'a callee'}"
        for n in graph
        if kills.get(n) and reads.get(n) and n != "await_audit"
    )
    assert not offenders, (
        "these functions both terminate the server and read its audit records — "
        "the response does not imply the line was written. Use "
        "`drain_output(proc)` + `await_audit(out, n)` and leave teardown to "
        "`running_subprocess`:\n  " + "\n  ".join(offenders)
    )


def test_the_teardown_owner_really_is_one():
    """🔴 The entry in `_TEARDOWN_OWNERS` is an exemption, and an exemption that
    stops being earned is exactly how the previous version of this guard was
    walked (E5: a racy function merely NAMED `_drain` inherited a skip).

    So the exemption is checked rather than trusted: each named function must
    still (a) exist, (b) actually kill the process, and (c) be a context manager,
    which is what makes "teardown belongs to it" true. If someone empties its
    `finally`, or the name goes stale, this fails instead of silently widening
    the guard's blind spot.
    """
    tree = _module_tree()
    funcs = _functions(tree)

    for name in _TEARDOWN_OWNERS:
        assert name in funcs, f"_TEARDOWN_OWNERS names {name!r}, which does not exist"
        node = funcs[name]
        assert _direct_kills(node), (
            f"{name} is exempted as the teardown owner but no longer kills the "
            "process — the exemption is now a free pass for nothing"
        )
        decorators = {
            d.id if isinstance(d, ast.Name) else getattr(d, "attr", "")
            for d in node.decorator_list
        }
        assert "contextmanager" in decorators, (
            f"{name} is exempted as the teardown owner but is not a context "
            f"manager (decorators: {sorted(decorators)}), so callers are not "
            "actually delegating teardown to it"
        )


def test_every_audit_reading_test_goes_through_the_shared_helper():
    """The anti-vacuity half: the guard above passes trivially if the tests stop
    reading audit records altogether, so this fails when the coverage SHRINKS.

    🔴 IT COUNTS CALL SITES, NOT FUNCTION NAMES. The earlier version counted the
    names of functions containing a call, and an audit showed one site inside a
    nested `def` contributed TWO — so two real sites could satisfy a threshold of
    three. The count is now `drain_output(...)` call expressions.

    🔴 THE THRESHOLD IS EXERCISED BY ITS OWN MUTANT. A sweep that only ever
    deletes the helper drives the count to 0, which kills `>= 1`, `>= 2` and
    `>= 3` identically — so the threshold looks verified while a `>= 1` mutant
    survives and permits two of the three sites to regress. The companion test
    below removes exactly ONE site and requires this to go red.
    """
    assert _drain_output_call_sites() == 3, (
        f"expected exactly 3 `drain_output(...)` call sites, found "
        f"{_drain_output_call_sites()}. A reader was added or deleted; if that is "
        "intended, update this count AND check the guard above still has teeth."
    )


def _drain_output_call_sites(source: "str | None" = None) -> int:
    """`drain_output(...)` CALL expressions — not the functions containing them."""
    tree = ast.parse(source if source is not None else Path(__file__).read_text())
    return sum(
        1 for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "drain_output"
    )


def test_the_call_site_THRESHOLD_is_load_bearing_not_decorative():
    """🔴 The mutant the threshold's own sweep cannot supply.

    Deleting the helper everywhere drives the count to 0 and kills every
    threshold equally. This removes ONE site from a COPY of the source and
    asserts the count actually moves to 2 — the case that separates `>= 3` from
    `>= 1`, and the reason the assertion above is `== 3` rather than a floor.
    """
    src = Path(__file__).read_text()
    assert _drain_output_call_sites(src) == 3, "fixture drift: the real count moved"

    one_removed = src.replace("out = drain_output(proc)", "out = None  # mutant", 1)
    assert one_removed != src, "the mutation did not apply — this test is vacuous"
    assert _drain_output_call_sites(one_removed) == 2, (
        "removing one call site did not move the count, so the threshold cannot "
        "distinguish three readers from two"
    )


class TestTrustedProxyAllowlistParsing:
    """Invariant guards on `trusted_network` / `load_trusted_proxies`.

    Each guard below is reachable by an input no earlier guard rejects, which is
    the property a mutation sweep can otherwise not distinguish from a guard
    that never executes.
    """

    # 🔴 THREE GUARDS REACH "no trusted proxies", SO THAT PHRASE CANNOT TELL
    # THEM APART — and asserting it was how two mutants survived. Each test
    # below pins the SENTENCE ITS OWN GUARD emits. Measured: with guard 1
    # weakened to `if raw is None`, a blank value falls through to the
    # empty-result guard and raises a message that still contains
    # "no trusted proxies", so the sweep scored it SURVIVED.
    UNSET_SENTENCE = "set $SUBSYSTEM_STORE_TRUSTED_PROXIES to the address(es)"
    NO_ENTRIES_SENTENCE = "resolved to no entries"

    def test_an_UNSET_variable_raises_rather_than_defaulting(self):
        with pytest.raises(ValueError) as exc:
            api.load_trusted_proxies({})
        assert self.UNSET_SENTENCE in str(exc.value)

    def test_a_BLANK_variable_raises_from_THE_SAME_guard_not_a_later_one(self):
        """Reachable past a presence-only guard 1: the variable IS set, to
        whitespace. And it must be guard 1 that catches it — a fall-through to
        the empty-result guard is a different message and a different bug.
        """
        with pytest.raises(ValueError) as exc:
            api.load_trusted_proxies({"SUBSYSTEM_STORE_TRUSTED_PROXIES": "  \t "})
        assert self.UNSET_SENTENCE in str(exc.value)
        assert self.NO_ENTRIES_SENTENCE not in str(exc.value)

    def test_a_NON_BLANK_value_that_yields_NO_ENTRIES_raises_too(self):
        """🔴 REACHABILITY for the empty-result guard, which nothing else here
        reaches: `","` is not blank, so guard 1 passes it, and the split then
        yields nothing but empty strings. Without this the guard is deletable
        and the sweep says so — measured, it survived.
        """
        with pytest.raises(ValueError) as exc:
            api.load_trusted_proxies({"SUBSYSTEM_STORE_TRUSTED_PROXIES": ",,"})
        assert self.NO_ENTRIES_SENTENCE in str(exc.value)
        assert self.UNSET_SENTENCE not in str(exc.value)

    def test_a_NON_ADDRESS_entry_names_the_offending_item(self):
        with pytest.raises(ValueError) as exc:
            api.load_trusted_proxies(
                {"SUBSYSTEM_STORE_TRUSTED_PROXIES": "192.0.2.1, gateway"}
            )
        message = str(exc.value)
        # 🔴 THE WHOLE COMPUTED PREFIX, PINNED AS ONE STRING — and that is a
        # correction, not style. The first version asserted `"'gateway'" in
        # message`, which SURVIVED a mutant that replaced the `{item!r}` slot
        # with the literal word "an entry": `ip_network`'s own ValueError
        # already contains `'gateway'`, so the assertion was reading the
        # exception's static prose and never the computed slot at all. A guard
        # on WORDS is walkable by a value that spells the same words somewhere
        # else in the sentence.
        assert message.startswith(
            "SUBSYSTEM_STORE_TRUSTED_PROXIES: 'gateway' is not an IP address or CIDR ("
        ), message
        # And the VALID sibling must not be blamed.
        assert "192.0.2.1'" not in message

    def test_a_DEFAULT_ROUTE_is_refused_in_BOTH_families(self):
        """The refusal is by PREFIX LENGTH, so it is one rule rather than a list
        of spellings somebody has to extend. Both families, because a guard
        written against `"0.0.0.0/0"` as a string passes the v4 case and lets
        `::/0` straight through.
        """
        for spelling in ("0.0.0.0/0", "::/0"):
            with pytest.raises(ValueError) as exc:
                api.load_trusted_proxies(
                    {"SUBSYSTEM_STORE_TRUSTED_PROXIES": spelling}
                )
            assert "trusts every peer" in str(exc.value), spelling

    def test_A_WIDE_PREFIX_IS_REFUSED_because_the_slash_zero_guard_reads_ONE_entry(self):
        """🔴 THIS REPLACES A TEST THAT PINNED A WALK AS CORRECT. It used to
        assert `0.0.0.0/1` was ACCEPTED — "the guard is not a ban on CIDRs" —
        which is true and useless, because the two halves of the address space,
        each written as a `/1`, parse clean and together trust every IPv4 peer.
        Refusing only `/0` inspects one entry in isolation and cannot see the
        union.

        The realistic misconfiguration is worse than the contrived one: a pod
        CIDR is exactly the shape an operator reaches for, and it hands the
        client identity to every pod in the cluster — verbatim the attacker in
        this module's threat model.

        ⚠ THE UPPER HALF IS BUILT ARITHMETICALLY, NOT WRITTEN. It is routable
        space, and `test_no_public_ips.py` refuses an IP literal in a PUBLIC
        repo — it caught the first draft of this test and of the comment in
        `server.py`. Widening that allowlist would have been the failure mode,
        not the fix.
        """
        import ipaddress

        upper_half = f"{ipaddress.ip_address(1 << 31)}/1"
        for spelling in (
            f"0.0.0.0/1,{upper_half}",  # the union that covers everything
            "10.244.0.0/16",  # a pod CIDR: every pod in the cluster
            "2001:db8::/48",  # the v6 mirror, which a v4-only floor would miss
        ):
            with pytest.raises(ValueError) as exc:
                api.load_trusted_proxies({"SUBSYSTEM_STORE_TRUSTED_PROXIES": spelling})
            assert "too broad" in str(exc.value), spelling

    def test_THE_FLOOR_ITSELF_IS_ACCEPTED_so_the_guard_is_not_a_ban_on_CIDRs(self):
        """The negative half, at the BOUNDARY rather than somewhere comfortable.
        Without it, `raise` on every network with a prefix would pass the test
        above — and the setting would be unusable for the one deployment shape
        (a proxy subnet) it exists to serve. Measured at both ends of both
        families: the floor passes, one bit wider fails.
        """
        import ipaddress

        assert api.load_trusted_proxies(
            {"SUBSYSTEM_STORE_TRUSTED_PROXIES": "198.51.100.0/24"}
        ) == (ipaddress.ip_network("198.51.100.0/24"),)
        assert api.load_trusted_proxies(
            {"SUBSYSTEM_STORE_TRUSTED_PROXIES": "2001:db8::/64"}
        ) == (ipaddress.ip_network("2001:db8::/64"),)
        for one_bit_wider in ("198.51.100.0/23", "2001:db8::/63"):
            with pytest.raises(ValueError) as exc:
                api.load_trusted_proxies(
                    {"SUBSYSTEM_STORE_TRUSTED_PROXIES": one_bit_wider}
                )
            assert "too broad" in str(exc.value), one_bit_wider

    def test_the_TOO_BROAD_message_is_NOT_the_default_route_message(self):
        """Two guards, two diagnostics, and they must not collapse into one: `/0`
        is "you disabled it", a wide prefix is "you meant a smaller range". A
        test asserting a phrase both emit could not tell them apart — which is
        exactly how two mutants survived an earlier round of this file.
        """
        with pytest.raises(ValueError) as zero:
            api.load_trusted_proxies({"SUBSYSTEM_STORE_TRUSTED_PROXIES": "0.0.0.0/0"})
        with pytest.raises(ValueError) as wide:
            api.load_trusted_proxies({"SUBSYSTEM_STORE_TRUSTED_PROXIES": "10.0.0.0/8"})
        assert "trusts every peer" in str(zero.value)
        assert "too broad" not in str(zero.value)
        assert "too broad" in str(wide.value)
        assert "trusts every peer" not in str(wide.value)
        # And the wide one NAMES the entry and the floor, or the operator cannot
        # act on it.
        assert "'10.0.0.0/8'" in str(wide.value)
        assert "/24" in str(wide.value)

    def test_COMMAS_AND_WHITESPACE_both_separate(self):
        import ipaddress

        assert api.load_trusted_proxies(
            {"SUBSYSTEM_STORE_TRUSTED_PROXIES": "192.0.2.1,198.51.100.0/24  203.0.113.9"}
        ) == (
            ipaddress.ip_network("192.0.2.1/32"),
            ipaddress.ip_network("198.51.100.0/24"),
            ipaddress.ip_network("203.0.113.9/32"),
        )

    def test_a_CIDR_WITH_HOST_BITS_is_taken_as_the_network_it_names(self):
        """`strict=False`, deliberately: refusing `198.51.100.7/24` would push an
        operator towards the `/0` the guard above exists to stop.
        """
        import ipaddress

        assert api.load_trusted_proxies(
            {"SUBSYSTEM_STORE_TRUSTED_PROXIES": "198.51.100.7/24"}
        ) == (ipaddress.ip_network("198.51.100.0/24"),)


class TestPeerAddressNormalisation:
    """`peer_address` turns a socket's `client_address` into an identity, or
    `None` — and `None` means refuse.
    """

    def test_an_IPv4_MAPPED_peer_matches_its_IPv4_allowlist_entry(self):
        """🔴 A dual-stack listener reports a v4 caller as `::ffff:198.51.100.4`.
        Without unwrapping, an allowlist written the obvious way never matches
        and the operator widens it until it does.
        """
        trusted = api.load_trusted_proxies(
            {"SUBSYSTEM_STORE_TRUSTED_PROXIES": "198.51.100.4"}
        )
        peer = api.peer_address(("::ffff:198.51.100.4", 4242, 0, 0))
        assert api.peer_is_trusted(peer, trusted) is True

    def test_it_does_NOT_aggregate_IPv6_to_a_slash_64_the_way_rate_limit_key_does(self):
        """🔴 THE SEAM BETWEEN TWO NORMALISERS. `rate_limit_key` collapses IPv6 to
        its /64 on purpose — an attacker picks freely inside their allocation.
        Reusing it here would trust 2**64 peers the operator never named. Two
        addresses in ONE /64: one allowlisted, the other must NOT be trusted.
        """
        trusted = api.load_trusted_proxies(
            {"SUBSYSTEM_STORE_TRUSTED_PROXIES": "2001:db8:1:2::1"}
        )
        assert api.peer_is_trusted(api.peer_address(("2001:db8:1:2::1", 1, 0, 0)), trusted)
        sibling = api.peer_address(("2001:db8:1:2:ffff:ffff:ffff:ffff", 1, 0, 0))
        assert api.peer_is_trusted(sibling, trusted) is False

    def test_an_IPv6_SCOPE_ID_does_not_make_the_peer_unparseable(self):
        """A link-local peer arrives as `fe80::1%eth0`, which `ip_address`
        refuses. The zone is a local interface name, not part of the identity.
        """
        assert str(api.peer_address(("fe80::1%eth0", 1, 0, 0))) == "fe80::1"

    @pytest.mark.parametrize(
        "client_address",
        [None, (), "127.0.0.1", ("not-an-address", 1), (127, 1), ({"a": 1}, 1)],
    )
    def test_every_shape_that_is_not_an_address_is_None_not_a_crash(self, client_address):
        """This runs on the PRE-AUTH path, where an unhandled exception is a
        cheaper log flood than any request that IS metered — the defect
        `_request_path` already exists to prevent, one screen away.
        """
        assert api.peer_address(client_address) is None

    def test_None_is_never_trusted(self):
        trusted = api.load_trusted_proxies(
            {"SUBSYSTEM_STORE_TRUSTED_PROXIES": "192.0.2.1"}
        )
        assert api.peer_is_trusted(None, trusted) is False

    def test_a_v6_peer_against_a_v4_allowlist_is_False_not_a_TypeError(self):
        """An allowlist holding one family and a peer from the other is an
        ordinary deployment, not an error.

        ⚠ THE NAME RECORDS A CLAIM THAT WAS WRONG. `peer_is_trusted` used to
        carry an explicit version gate, commented "`IPv4Address in IPv6Network`
        raises TypeError". It does not — `ipaddress` compares versions itself
        and returns False (measured on 3.12.13 and 3.14.7). The gate was dead
        code; a mutation sweep found it by surviving its removal. The BEHAVIOUR
        is still worth pinning, so the test stays and the guard is gone.
        """
        trusted = api.load_trusted_proxies(
            {"SUBSYSTEM_STORE_TRUSTED_PROXIES": "192.0.2.0/24"}
        )
        assert api.peer_is_trusted(api.peer_address(("2001:db8::1", 1, 0, 0)), trusted) is False

    def test_a_BARE_STRING_allowlist_is_refused_LOUDLY(self):
        """Iterating a `str` yields characters, so `"192.0.2.1"` as an allowlist
        would refuse every request — a misconfiguration wearing an attack's
        clothes. A type annotation is not a code path; this check is.
        """
        with pytest.raises(TypeError) as exc:
            api.peer_is_trusted(api.peer_address(("192.0.2.1", 1)), "192.0.2.1")
        assert "SEQUENCE" in str(exc.value)


class TestTrustedProxyOverHTTP:
    """The gate as the wire sees it. Invariant guards: `build_server(trusted_
    proxies=…)` does not exist at base, so their red there is an AttributeError.
    """

    def test_a_BAD_TOKEN_from_an_untrusted_peer_is_the_SAME_uniform_401(
        self, store: Path
    ):
        """🔴 The wire must not discriminate. Once a request DOES fail auth, the
        401 an untrusted peer sees must be byte-identical to the one a trusted
        peer sees — otherwise the response is an oracle for "which hop is
        trusted", which is the enumeration surface §2b forbids.
        """
        with running(store, trusted_proxies=(NOT_LOOPBACK_PROXY,)) as (base, _audit):
            untrusted = fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)
        with running(store) as (base, _audit):
            trusted = fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)
        assert untrusted[0] == trusted[0] == 401
        assert untrusted[2] == trusted[2] == b"unauthorized\n"
        assert _comparable(untrusted[1]) == _comparable(trusted[1])

    def test_the_LOG_ANNOTATES_the_peer_WITHOUT_calling_it_an_auth_failure(
        self, store: Path
    ):
        """🔴 THIS REPLACES A TEST THAT PINNED THE WRONG SHAPE. It asserted
        `status=untrusted-peer` together with `auth=fail` — which meant every
        `kubectl port-forward` landed in the Loki auth-fail alert, and an alert
        that fires on the documented acceptance procedure is one the operator
        learns to ignore.

        Direct-to-pod access must still be greppable, so it is its OWN field.
        The request itself succeeds.
        """
        with running(store, trusted_proxies=(NOT_LOOPBACK_PROXY,)) as (base, audit):
            code, _h, _b = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
        assert code == 200, code
        assert len(audit) == 1
        assert "peer=untrusted" in audit[0], audit[0]
        assert "auth=ok" in audit[0], audit[0]
        assert "result=200" in audit[0], audit[0]
        assert "status=untrusted-peer" not in audit[0], audit[0]

    def test_a_TRUSTED_peer_is_annotated_too_so_the_field_is_not_write_only(
        self, store: Path
    ):
        """The other half. A field that only ever takes one value cannot tell a
        reader that the OTHER case did not occur — `peer=trusted` is what makes
        the absence of `peer=untrusted` mean something.
        """
        with running(store) as (base, audit):
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
        assert "peer=trusted" in audit[0], audit[0]
        assert "peer=untrusted" not in audit[0], audit[0]

    def test_a_WRITE_verb_from_an_untrusted_peer_is_METERED_under_the_peer(
        self, store: Path
    ):
        """🔴 ONE RULE, BOTH DOORS. `_reject_write` and `_handle` share
        `_identify_and_meter` precisely because a check enforced at one call site
        and not the other is the failure this file keeps finding — writes used to
        skip the client-IP and lockout checks entirely. A write from an untrusted
        peer therefore gets the ordinary 405, annotated, exactly once.
        """
        limiter = api.RateLimiter(max_failures=5, window_s=600.0, lockout_s=600.0)
        with running(
            store, limiter=limiter, trusted_proxies=(NOT_LOOPBACK_PROXY,)
        ) as (base, audit):
            code, _h, body = fetch(
                f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, method="POST"
            )
        assert code == 405, (code, body)
        assert body == b"read-only\n"
        assert "peer=untrusted" in audit[0], audit[0]
        assert f"ip={TRUSTED_PEER}" in audit[0], audit[0]
        # 🔴 EXACTLY ONE LINE, AND NOTHING CHARGED for a request that AUTHENTICATED
        # — a round-2 correction, not belt and braces. A mutant that mis-handles
        # the identify step's return value answers a SECOND response here and
        # charges the limiter under a `None` key; the GET path hides that on an
        # internal assert.
        assert len(audit) == 1, audit
        assert limiter._failures == {} and limiter._locked_until == {}

    def test_an_UNKNOWN_VERB_from_an_untrusted_peer_is_METERED_under_the_peer(
        self, store: Path
    ):
        """The third door: `send_error`, which every unhandled method reaches.
        An unknown verb never authenticates, so it IS a charged failure — under
        the peer's own address, which is the whole point.
        """
        limiter = api.RateLimiter(max_failures=5, window_s=600.0, lockout_s=600.0)
        with running(
            store, limiter=limiter, trusted_proxies=(NOT_LOOPBACK_PROXY,)
        ) as (base, audit):
            code, _h, body = fetch(
                f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, method="FROBNICATE"
            )
        assert code == 401, (code, body)
        assert body == b"unauthorized\n"
        assert "peer=untrusted" in audit[0], audit[0]
        assert len(audit) == 1, audit
        assert list(limiter._failures) == [TRUSTED_PEER], limiter._failures

    def test_the_header_is_NOT_READ_AT_ALL_from_an_untrusted_peer(self, store: Path):
        """🔴 THE ONE ORDERING THAT CAN BE WRONG WHILE EVERY TEST ABOVE STAYS
        GREEN. Two requests from the same untrusted peer, one sending a forged
        `CF-Connecting-IP` and one sending none at all, must be booked under the
        SAME bucket — the peer's. If the header were consulted at all, the forged
        request would land somewhere else and the forger would get a free second
        budget by rotating the header.
        """
        with running(store, trusted_proxies=(NOT_LOOPBACK_PROXY,)) as (base, audit):
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, client_ip=SPOOF_IP)
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, client_ip=None)
        assert len(audit) == 2, audit
        assert all(f"ip={TRUSTED_PEER}" in ln for ln in audit), audit
        assert all(f"ip={SPOOF_IP}" not in ln for ln in audit), audit
        # …and the absent header is NOT the `no-client-ip` refusal either: that
        # rule applies only where the header IS the identity.
        assert all("status=no-client-ip" not in ln for ln in audit), audit

    def test_an_untrusted_peer_IS_charged_to_ITS_OWN_bucket(self, store: Path):
        """🔴 THIS REPLACES A TEST THAT ASSERTED NOTHING WAS CHARGED. Under the
        old refuse-outright design that was true; under this one it would mean an
        untrusted peer had an unlimited budget, which is worse than the defect
        being fixed. Five failed auths from one untrusted peer must lock out that
        peer — and the bucket must be keyed on the PEER, never on the header.
        """
        limiter = api.RateLimiter(max_failures=5, window_s=600.0, lockout_s=600.0)
        with running(
            store, limiter=limiter, trusted_proxies=(NOT_LOOPBACK_PROXY,)
        ) as (base, audit):
            for _ in range(5):
                fetch(
                    f"{base}/api/v1/recall/{SCOPE}", token="w" * 48, client_ip=SPOOF_IP
                )
            after = fetch(
                f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN, client_ip=SPOOF_IP
            )
        assert after[0] == 401, "an untrusted peer had an unlimited budget"
        assert list(limiter._locked_until) == [TRUSTED_PEER], limiter._locked_until
        assert SPOOF_IP not in limiter._locked_until, limiter._locked_until
        assert "status=lockout-triggered" in audit[4], audit[4]

    def test_healthz_answers_an_untrusted_peer(self, store: Path):
        with running(store, trusted_proxies=(NOT_LOOPBACK_PROXY,)) as (base, audit):
            code, _h, body = fetch(f"{base}/healthz", client_ip=None)
        assert (code, body) == (200, b"ok\n")
        assert audit == [], "the probe path must not audit, or Loki fills with noise"

    def test_a_CIDR_entry_admits_a_peer_INSIDE_it(self, store: Path):
        """POSITIVE CONTROL for the CIDR arm. Every other test in this class
        uses a /32-equivalent, so `return False` for any prefixed network would
        pass all of them.
        """
        # `/24`, not the `/8` this used to say: a /8 is now refused by the
        # prefix floor, and a test that quietly relied on it would have turned
        # into a fact about the floor rather than about the CIDR arm.
        with running(store, trusted_proxies=("127.0.0.0/24",)) as (base, audit):
            code, _h, body = fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
        assert code == 200, (code, body)
        assert "peer=trusted" in audit[0], audit[0]

    def test_build_server_REFUSES_an_empty_allowlist(self, store: Path):
        with pytest.raises(ValueError) as exc:
            api.build_server(
                host="127.0.0.1",
                port=0,
                store_root=str(store),
                tokens=(GOOD_TOKEN,),
                trusted_proxies=(),
            )
        assert "empty" in str(exc.value)

    def test_build_server_REFUSES_a_bare_string_allowlist(self, store: Path):
        with pytest.raises(TypeError) as exc:
            api.build_server(
                host="127.0.0.1",
                port=0,
                store_root=str(store),
                tokens=(GOOD_TOKEN,),
                trusted_proxies=LOOPBACK_PROXY,
            )
        assert "SEQUENCE" in str(exc.value)

    def test_build_server_REFUSES_a_default_route_through_the_PROGRAMMATIC_door(
        self, store: Path
    ):
        """🔴 The `/0` refusal lives in `trusted_network`, which BOTH doors go
        through. A guard placed only in the env parser would be walked by any
        caller constructing the server directly — and this file's own harness is
        such a caller.
        """
        with pytest.raises(ValueError) as exc:
            api.build_server(
                host="127.0.0.1",
                port=0,
                store_root=str(store),
                tokens=(GOOD_TOKEN,),
                trusted_proxies=("0.0.0.0/0",),
            )
        assert "trusts every peer" in str(exc.value)

    def test_the_HANDLER_CLASS_DEFAULT_trusts_nobody(self):
        """`build_server` refuses an empty allowlist, so the class attribute is
        not reachable through it — which is exactly why it is pinned here. A
        subclass that never went through `build_server` must fail CLOSED, and
        `()` read as "unset, allow all" is the shape that mistake takes.
        """
        assert api.StoreRequestHandler.trusted_proxies == ()
        assert (
            api.peer_is_trusted(
                api.peer_address(("127.0.0.1", 1)),
                api.StoreRequestHandler.trusted_proxies,
            )
            is False
        )
        # …and "trusts nobody" now means "believes nobody's header", so the
        # bucket falls back to the peer rather than to the forged value.
        assert api.resolve_client(
            {"CF-Connecting-IP": SPOOF_IP},
            ("127.0.0.1", 1),
            api.StoreRequestHandler.trusted_proxies,
        ) == ("127.0.0.1", False)


class TestResolveClientIsTheWholeRule:
    """`resolve_client` is the one place the trusted/untrusted decision is made.
    Extracted as a pure function precisely so the branches a real TCP socket
    cannot produce are reachable here.
    """

    TRUSTED = ("198.51.100.9",)

    def _trusted(self):
        return api.load_trusted_proxies(
            {"SUBSYSTEM_STORE_TRUSTED_PROXIES": self.TRUSTED[0]}
        )

    def test_a_TRUSTED_peer_is_bucketed_on_the_HEADER(self):
        assert api.resolve_client(
            {"CF-Connecting-IP": CLIENT_IP}, (self.TRUSTED[0], 9), self._trusted()
        ) == (CLIENT_IP, True)

    def test_an_UNTRUSTED_peer_is_bucketed_on_ITSELF_and_the_header_is_ignored(self):
        """The two halves of one assertion: the answer IS the peer, and it is
        NOT the header. Either alone is satisfied by a bug — returning `None`
        satisfies "not the header", and echoing the header back satisfies
        neither but would pass a test that only checked the flag.
        """
        key, trusted = api.resolve_client(
            {"CF-Connecting-IP": SPOOF_IP}, (OTHER_IP, 9), self._trusted()
        )
        assert key == OTHER_IP
        assert key != SPOOF_IP
        assert trusted is False

    def test_a_TRUSTED_peer_with_NO_header_still_FAILS_CLOSED(self):
        """The fail-closed rule survives the redesign: where the header IS the
        identity, its absence is still a refusal, not a fallback to the peer.
        Otherwise a trusted proxy that stopped setting the header would silently
        bucket the whole internet under one key.
        """
        assert api.resolve_client({}, (self.TRUSTED[0], 9), self._trusted()) == (
            None,
            True,
        )

    def test_a_PEER_THAT_IS_NOT_AN_ADDRESS_refuses_rather_than_crashing(self):
        """🔴 REACHABLE ONLY HERE. A real TCP socket always yields an address, so
        this branch cannot be driven over the wire — which is the argument for
        extracting the function rather than leaving the logic inline where the
        branch would be untestable and therefore unverified.
        """
        for bogus in (None, (), ("not-an-address", 9), (127, 9)):
            assert api.resolve_client(
                {"CF-Connecting-IP": SPOOF_IP}, bogus, self._trusted()
            ) == (None, False), bogus

    def test_an_UNTRUSTED_v6_peer_is_aggregated_to_its_slash_64(self):
        """Both branches normalise through `rate_limit_key`, or the peer branch
        would hand an IPv6 caller 2**64 free buckets — the exact hazard
        `rate_limit_key` exists for, reintroduced through the new door.
        """
        key, trusted = api.resolve_client(
            {}, ("2001:db8:1:2:ffff:ffff:ffff:ffff", 9, 0, 0), self._trusted()
        )
        assert key == "2001:db8:1:2::/64"
        assert trusted is False


# =============================================================================
# 16. PHASE 3, CRITERIA 1-3 — two-token authorization on the READ path.
#
# 🔴 WHICH OF THESE ARE REGRESSION TESTS, HONESTLY. `server.py` and the absent
# path both EXIST at the base ref, and the leak is real there, so the tests in
# `TestEnumerationChannelsAreClosed` and `TestRefusedIsIndistinguishableFromAbsent`
# are genuine regressions: they go red at base for the RIGHT reason (the body
# names scopes the caller may not see) rather than by API error. The GUARD tests
# in `TestScopedTokenRowGuards` are NOT: they call a parser that does not accept
# a three-field row at base, so their red is a shape error and proves nothing —
# the mutation matrix in the PR body is their evidence.
#
# 🔴 AND THE WRITE PATH IS NOT HERE. Criteria 4-10 add no verb in this branch;
# `TestPhaseOneScope.test_the_server_declares_no_write_handler` is untouched and
# still the thing that has to be broken on purpose when the write path lands.
# =============================================================================


# Pairwise-distinct, and distinct from every scope constant already in this
# file AND from every literal any assertion below names. Invented for this
# section so a renderer that surfaced the wrong scope cannot pass by
# coincidence.
ALLOW_SCOPE = "kelp-forest"     # zach may read it
DENY_SCOPE = "quartz-mine"      # dana may read it; zach may not
THIRD_SCOPE = "lantern-bay"     # nobody in these tests may read it
PHANTOM_SCOPE = "never-quarried"  # never exists on disk, at any point

# One distinctive sentence per scope, sharing no substring, so "did content from
# a scope I cannot see reach me" is answerable by a single `in` on the body.
KELP_NUANCE = "- 2026-03-04: the tide gauge drifts 3cm after a spring flood."
QUARTZ_NUANCE = "- 2026-03-05: the drill head overheats past 900 revolutions."
LANTERN_NUANCE = "- 2026-03-06: the beacon lamp browns out on a westerly gale."

ZACH_TOKEN = "k" * 20 + "L" * 20 + "m" * 8   # 48 chars
DANA_TOKEN = "p" * 20 + "Q" * 20 + "r" * 8   # 48 chars, disjoint
# 🔴 TOKEN-LENGTH AND ALL-LOWERCASE, so it PASSES the identity charset check and
# can be rejected ONLY by the length cap. That is what lets the "a token can
# never be an identity" test measure the arithmetic it claims to measure rather
# than an incidental uppercase letter. `secrets.token_urlsafe` really can emit
# an all-lowercase string, so this is a shape, not a contrivance.
LOWER_TOKEN = "s" * 24 + "t" * 24            # 48 chars, [a-z] only

# The identity class, spelled again here BY HAND. Importing
# `api.IDENTITY_COMPONENT` would make the fixture's precondition and the code
# under test the same expression, so a wrong class would satisfy both.
IDENTITY_CHARSET = re.compile(r"[a-z0-9][a-z0-9-]*")

# A fixed instant, applied to every entry file, so two stores built from
# different scope NAMES still date identically. `snapshot_freshness` reports the
# newest entry mtime and the entry-file COUNT store-wide, and both are shared
# across an allowlist — see the module docstring's residual-leak note. Holding
# them constant is what lets the byte-identity claim below be about scope
# EXISTENCE and nothing else.
FIXED_MTIME = 1_767_225_600.0  # 2026-01-01T00:00:00Z, an arbitrary round instant


def _scoped_record(token: str, identity: str, *scopes: str):
    return api.TokenRecord(token=token, identity=identity, scopes=tuple(scopes))


ZACH = _scoped_record(ZACH_TOKEN, "zach", ALLOW_SCOPE)
DANA = _scoped_record(DANA_TOKEN, "dana", DENY_SCOPE)


def _build_store(root: Path, scopes: "dict[str, str]", *, malformed: str = "") -> Path:
    """A store holding one entry per named scope, every mtime pinned.

    `scopes` maps scope name -> the nuance line that scope's single entry
    carries. `malformed` optionally names a scope that also gets a front-matter-
    less file, which is what puts a row on `index.malformed`.
    """
    root.mkdir(parents=True, exist_ok=True)
    for scope, nuance in scopes.items():
        (root / scope).mkdir(parents=True, exist_ok=True)
        entry = root / scope / f"{scope}-entry.md"
        entry.write_text(_entry(f"{scope}-entry", scope, nuance=nuance))
    if malformed:
        (root / malformed / "broken-shard.md").write_text("no front matter here\n")
    for path in sorted(root.rglob("*.md")):
        os.utime(path, (FIXED_MTIME, FIXED_MTIME))
    return root


@pytest.fixture
def scoped_store(tmp_path: Path) -> Path:
    """Three populated scopes, and the malformed row lives in a DENIED one.

    The malformed placement is the point: `malformed_elsewhere` is rendered on
    EVERY status, so a reject sitting in a scope the caller cannot name is the
    channel that leaks without any miss ever happening.
    """
    return _build_store(
        tmp_path / "store",
        {
            ALLOW_SCOPE: KELP_NUANCE,
            DENY_SCOPE: QUARTZ_NUANCE,
            THIRD_SCOPE: LANTERN_NUANCE,
        },
        malformed=DENY_SCOPE,
    )


class TestScopedTokenRowGuards:
    """🔴 SIX NEW GUARDS, AND EACH INPUT PASSES EVERY EARLIER ONE.

    Guards 1-5 (no source / unreadable / empty / too many / too short) are
    unchanged and covered by `TestTokenLoadingGuards` and
    `TestTokenSetAndOverlapRotation` above. Every file below therefore uses
    tokens of 48 characters and at most four rows, so the only guard that can
    reject it is the one the test names — a test that went red because a
    DIFFERENT guard fired would be green with the guard it names deleted, which
    is the failure mode this class is shaped against.

    Each assertion pins the guard's OWN sentence, not merely `ValueError`.
    """

    def _write(self, tmp_path: Path, *rows: str) -> str:
        path = tmp_path / "tokens"
        path.write_text("\n".join(rows) + "\n")
        return str(path)

    def test_GUARD_6_a_row_with_two_fields_is_MALFORMED_not_two_tokens(
        self, tmp_path: Path
    ):
        """🔴 THE FORMAT CHANGE, MADE LOUD. Under the old whole-file `.split()`
        this line was TWO credentials. Under the row format it is one row with a
        field count that means nothing, and the process refuses to start rather
        than pick a reading.
        """
        path = self._write(tmp_path, f"{ZACH_TOKEN} zach")
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {}, warn=lambda _l: None)
        assert "malformed token row on line 1 of 1" in str(exc.value)
        assert "2 fields" in str(exc.value)
        # And never the credential itself, on the one file whose whole content
        # is credentials.
        assert ZACH_TOKEN not in str(exc.value)

    def test_GUARD_6_is_reached_by_a_FOUR_field_row_too(self, tmp_path: Path):
        # The other side of the `not in (1, 3)` boundary. A guard tested only
        # from below is a guard tested on one side of its condition.
        path = self._write(tmp_path, f"{ZACH_TOKEN} zach {ALLOW_SCOPE} extra")
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {}, warn=lambda _l: None)
        assert "malformed token row on line 1 of 1" in str(exc.value)
        assert "4 fields" in str(exc.value)
        # 🔴 THE NEGATIVE HALF OF THE TYPO HINT, and it is what stops the hint
        # becoming noise. There is no comma anywhere in this row, so a
        # comma-spacing explanation would be a wrong guess printed with the same
        # confidence as the count. A hint that fires unconditionally is a hint
        # nobody reads.
        assert "NO SPACES" not in str(exc.value)

    def test_GUARD_6_a_SPACE_AFTER_A_COMMA_is_told_what_it_actually_did(
        self, tmp_path: Path
    ):
        """🔴 FAIL-CLOSED IS NOT THE SAME AS DIAGNOSTIC. `<tok> zach a, b` is a
        four-field row because the space after the comma split the scope list,
        and the row is correctly refused — but "4 fields, expected 1 or 3" sends
        the operator to count fields on a line that looks like it has three.

        The refusal is unchanged: same guard, same prefix, same count. Only the
        sentence after it is new, and it is conditional on a comma actually
        being present past the identity, so it cannot be printed as a guess.
        """
        path = self._write(
            tmp_path, f"{ZACH_TOKEN} zach {ALLOW_SCOPE}, {DENY_SCOPE}"
        )
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {}, warn=lambda _l: None)
        message = str(exc.value)
        # The guard is NOT weakened — it still refuses, still by field count.
        assert "malformed token row on line 1 of 1" in message
        assert "4 fields" in message
        # …and now says what to do about it.
        assert "NO SPACES" in message
        assert "`alpha,beta`, not `alpha, beta`" in message
        assert ZACH_TOKEN not in message

    def test_GUARD_7_an_identity_outside_the_charset_is_refused(self, tmp_path: Path):
        # Passes guard 6: three fields. Fails only on the identity's spelling.
        path = self._write(tmp_path, f"{ZACH_TOKEN} Za_ch {ALLOW_SCOPE}")
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {}, warn=lambda _l: None)
        assert "invalid identity in token row on line 1 of 1" in str(exc.value)
        assert "Za_ch" in str(exc.value)

    def test_GUARD_7_a_TOKEN_can_never_be_read_as_an_identity(self, tmp_path: Path):
        """🔴 THE STRUCTURAL HALF OF THE FORMAT CHANGE, AND THE FIXTURE HAS TO
        REACH IT. Three tokens on one line used to be three credentials; now it
        is a three-field row, and what stops the second one being read as an
        identity is ARITHMETIC — the cap is below the token floor, 48 > 32.

        So the identity here is `LOWER_TOKEN`, which is token-SHAPED and passes
        the charset check outright: only the LENGTH cap can reject it. An
        earlier version used a token containing uppercase, which meant the
        charset half did all the work and the length cap — the half the
        docstring is about — was never exercised at all.
        """
        path = self._write(tmp_path, f"{ZACH_TOKEN} {LOWER_TOKEN} {ALLOW_SCOPE}")
        assert IDENTITY_CHARSET.fullmatch(LOWER_TOKEN), (
            "the fixture must pass the CHARSET check, or this test measures the "
            "charset half and not the length cap it claims to"
        )
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {}, warn=lambda _l: None)
        assert "invalid identity in token row on line 1 of 1" in str(exc.value)
        # 32 and 43, pinned LITERALLY — the cap must stay under the floor or
        # this whole property evaporates silently.
        assert api.MAX_IDENTITY_CHARS == 32
        assert api.MIN_TOKEN_CHARS == 43
        assert api.MAX_IDENTITY_CHARS < api.MIN_TOKEN_CHARS
        assert len(LOWER_TOKEN) > api.MAX_IDENTITY_CHARS

    def test_GUARD_8_a_mapped_row_may_not_claim_the_legacy_identity(
        self, tmp_path: Path
    ):
        # Passes 6 (three fields) and 7 (`legacy` is a well-formed identity).
        # Only the reservation can reject it.
        path = self._write(tmp_path, f"{ZACH_TOKEN} legacy {ALLOW_SCOPE}")
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {}, warn=lambda _l: None)
        assert "reserved identity in token row on line 1 of 1" in str(exc.value)
        assert "'legacy'" in str(exc.value)

    def test_GUARD_9_an_explicitly_empty_allowlist_is_refused(self, tmp_path: Path):
        # Three fields, a valid non-reserved identity, and a third field holding
        # no scope name at all. Every earlier guard passes.
        path = self._write(tmp_path, f"{ZACH_TOKEN} zach ,")
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {}, warn=lambda _l: None)
        assert "empty scope allowlist in token row on line 1 of 1" in str(exc.value)
        assert "'zach'" in str(exc.value)

    def test_GUARD_10_a_scope_no_URL_could_name_is_refused(self, tmp_path: Path):
        # A dot is outside the path-component class, so `kelp.forest` could
        # never be requested — an allowlist entry that would sit inert forever.
        # Passes 9: the list is non-empty.
        path = self._write(tmp_path, f"{ZACH_TOKEN} zach kelp.forest")
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {}, warn=lambda _l: None)
        assert "invalid scope in token row on line 1 of 1" in str(exc.value)
        assert "kelp.forest" in str(exc.value)

    def test_GUARD_10_also_catches_an_EMPTY_entry_inside_a_real_list(
        self, tmp_path: Path
    ):
        """The reachable case guard 9 cannot see: the list is not empty, so
        `any(...)` is satisfied, and one entry still names nothing.
        """
        path = self._write(
            tmp_path, f"{ZACH_TOKEN} zach {ALLOW_SCOPE},,{DENY_SCOPE}"
        )
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {}, warn=lambda _l: None)
        assert "invalid scope in token row on line 1 of 1" in str(exc.value)

    def test_GUARD_10_also_catches_an_entry_that_FOLDS_AWAY_to_nothing(
        self, tmp_path: Path
    ):
        """🔴 THE HALF THE CHARACTER CLASS CANNOT SEE, and the reason the guard
        checks the FOLDED value rather than the typed one.

        `-` and `___` are inside `[A-Za-z0-9_-]+`, so they are perfectly namable
        in a URL — and `normalize_ref` folds both to the EMPTY STRING, which
        matches no index key. Such an entry is a grant that reads as working and
        does nothing, which is exactly what this guard's sentence promises to
        prevent. A guard narrower than its own description is worse than none,
        because it stops anyone looking.
        """
        for typo in ("-", "___", "--"):
            path = self._write(tmp_path, f"{ZACH_TOKEN} zach {typo}")
            with pytest.raises(ValueError) as exc:
                api.load_tokens(path, {}, warn=lambda _l: None)
            assert "invalid scope in token row on line 1 of 1" in str(exc.value), typo
            assert "folds away" in str(exc.value), typo

    def test_GUARD_11_the_MIGRATION_SHAPE_is_refused_not_silently_collapsed(
        self, tmp_path: Path
    ):
        """🔴 THE FAIL-OPEN THIS GUARD EXISTS FOR, AND IT WAS LIVE.

        "Scope a credential its holder already has" is the migration's own first
        step, and the natural way to write it is to leave the bare line and add a
        mapped one below. The loader used to drop the second row BEFORE parsing
        it — keyed on the token, order preserved — so this file loaded as ONE
        record, `identity='legacy' scopes=None`: UNRESTRICTED. No error. The
        mapped row did not exist, and the only signal was a banner reading
        "1 of 1 token rows are bare" over a two-line file.

        The two authorities are named in the message because the whole content
        of the complaint is that they DISAGREE — and neither an identity nor a
        scope name is a credential, so naming them keeps guard 5's property.
        """
        path = self._write(
            tmp_path, ZACH_TOKEN, f"{ZACH_TOKEN} zach {ALLOW_SCOPE}"
        )
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {}, warn=lambda _l: None)
        message = str(exc.value)
        assert "duplicate token on lines 1 and 2" in message
        # Both authorities, spelled out — the unrestricted one by name, so an
        # operator reading the pod log can see WHICH reading they nearly got.
        assert "legacy (UNRESTRICTED)" in message
        assert f"zach ({ALLOW_SCOPE})" in message
        # …and never the credential itself, on the one file whose whole content
        # is credentials.
        assert ZACH_TOKEN not in message

    def test_GUARD_11_a_DUPLICATE_TOKEN_ROW_no_longer_bypasses_guards_6_to_10(
        self, tmp_path: Path
    ):
        """🔴 THE SECOND HALF OF THE SAME DEFECT: a dropped row was never
        VALIDATED either.

        This second row carries an invalid identity AND an invalid scope. Under
        the pre-fix loader the whole row vanished before guard 6 ran and the file
        loaded clean as `zach/{ALLOW_SCOPE}`. Every row must reach the ladder, so
        the row's OWN first failure — guard 7 — is what must fire, and the
        assertion names guard 7's sentence rather than merely `ValueError`: a
        test satisfied by any raise would be green with guard 7 deleted.
        """
        path = self._write(
            tmp_path,
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE}",
            f"{ZACH_TOKEN} Za_CH_BAD !!!!",
        )
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {}, warn=lambda _l: None)
        message = str(exc.value)
        assert "invalid identity in token row on line 2 of 2" in message
        assert "Za_CH_BAD" in message

    def test_GUARD_11_collapses_ONE_GRANT_SPELLED_TWO_WAYS(self, tmp_path: Path):
        """🔴 THE OTHER DIRECTION — OVER-REFUSING IS ALSO A FAILURE, and this is
        the case a purely TEXTUAL collapse gets wrong.

        `Kelp_Forest` and `kelp-forest` are the same grant: the parser folds both
        to one scope. The rows are not identical as text and ARE identical as
        records, and it is the record that decides. A guard comparing raw lines
        would refuse a file that says one unambiguous thing.
        """
        path = self._write(
            tmp_path,
            f"{ZACH_TOKEN} zach Kelp_Forest",
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE}",
        )
        assert loaded(path, {}) == [(ZACH_TOKEN, "zach", (ALLOW_SCOPE,))]

    def test_GUARD_11_fires_even_when_the_IDENTITY_agrees(self, tmp_path: Path):
        """One token, one identity, two DIFFERENT allowlists. Guard 12 would also
        see this pair — and would tell the operator to invent `zach-prev`, which
        is the wrong advice for what is one credential written twice. Guard 11
        runs first precisely so the more specific complaint wins.
        """
        path = self._write(
            tmp_path,
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE}",
            f"{ZACH_TOKEN} zach {DENY_SCOPE}",
        )
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {}, warn=lambda _l: None)
        assert "duplicate token on lines 1 and 2" in str(exc.value)
        assert "duplicate identity" not in str(exc.value)

    def test_GUARD_11_runs_BEFORE_12_so_an_IDENTICAL_MAPPED_ROW_still_loads(
        self, tmp_path: Path
    ):
        """🔴 THE ORDER IS LOAD-BEARING, AND THIS IS WHAT BREAKS IF IT INVERTS.

        One mapped row pasted twice, verbatim. Guard 11 collapses it to a single
        record; if guard 12 ran first it would see two rows claiming `zach` and
        refuse the file. Nothing about this input is ambiguous, so it must load.
        """
        path = self._write(
            tmp_path,
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE}",
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE}",
        )
        assert loaded(path, {}) == [(ZACH_TOKEN, "zach", (ALLOW_SCOPE,))]

    def test_GUARD_12_two_rows_naming_ONE_identity_are_refused(self, tmp_path: Path):
        # Two rows, both well-formed, both with real allowlists, and two DISTINCT
        # tokens so guard 11 cannot be what fires — every earlier guard passes on
        # every row. Only the cross-row identity check can see this.
        path = self._write(
            tmp_path,
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE}",
            f"{DANA_TOKEN} zach {DENY_SCOPE}",
        )
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {}, warn=lambda _l: None)
        assert "duplicate identity 'zach'" in str(exc.value)
        assert "on lines 1 and 2" in str(exc.value)

    def test_GUARD_12_names_the_PHYSICAL_rows_across_a_collapse(
        self, tmp_path: Path
    ):
        """The index an operator is told to look at must be the LINE they can
        see. Row 2 collapses into row 1, so the clash is between rows 1 and 3 —
        a position in the post-collapse list would call it "rows 1 and 2" and
        send them to the wrong line.
        """
        path = self._write(
            tmp_path,
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE}",
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE}",
            f"{DANA_TOKEN} zach {DENY_SCOPE}",
        )
        with pytest.raises(ValueError) as exc:
            api.load_tokens(path, {}, warn=lambda _l: None)
        assert "token rows on lines 1 and 3 both claim it" in str(exc.value)

    def test_GUARD_12_names_the_PHYSICAL_LINE_when_A_BLANK_LINE_SHIFTS_IT(
        self, tmp_path: Path
    ):
        """🔴 THE FIXTURE ABOVE HAS NO BLANK LINE, SO IT PINS THE COLLAPSE
        DIMENSION AND NOTHING ELSE — while its name and the comment beside the
        code both claim PHYSICAL LINES. On a file with no blank line the ordinal
        over non-blank rows and the line number are the same number, so it reads
        as coverage for a claim it cannot see.

        This is that claim, reproduced: rows on lines 2, 4 and 6. Lines 2 and 4
        are identical and collapse; the identity clash is between lines 2 and 6.
        Counting non-blank rows says "1 and 3" — a real message this code emitted
        — and sends the operator to two lines that hold nothing.
        """
        path = self._write(
            tmp_path,
            "",
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE}",
            "",
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE}",
            "",
            f"{DANA_TOKEN} zach {DENY_SCOPE}",
        )
        message = str(exc_of(lambda: api.load_tokens(path, {}, warn=lambda _l: None)))
        assert "token rows on lines 2 and 6 both claim it" in message
        assert "1 and 3" not in message, (
            "the ordinal over non-blank rows leaked into the message"
        )

    def test_GUARD_11_names_the_PHYSICAL_LINE_when_A_BLANK_LINE_SHIFTS_IT(
        self, tmp_path: Path
    ):
        """The same claim for guard 11 — "every guard's message must use them
        consistently" is only true if every guard is measured. Rows on lines 2
        and 4, disagreeing, so guard 11 fires rather than 12.
        """
        path = self._write(
            tmp_path,
            "",
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE}",
            "",
            f"{ZACH_TOKEN} zach {DENY_SCOPE}",
        )
        message = str(exc_of(lambda: api.load_tokens(path, {}, warn=lambda _l: None)))
        assert "duplicate token on lines 2 and 4" in message
        assert "1 and 2" not in message

    @pytest.mark.parametrize(
        "row,sentence",
        [
            ("hunter2", "token on line 4 of 4 is too short"),
            # Two TOKEN-LENGTH fields, so guard 5 passes and guard 6 is what
            # fires — `aaa bbb` would have been caught by the length floor first
            # and this row would have measured guard 5 twice.
            (f"{ZACH_TOKEN} {LOWER_TOKEN}", "malformed token row on line 4 of 4"),
            (f"{ZACH_TOKEN} Za_ch {ALLOW_SCOPE}", "invalid identity in token row on line 4 of 4"),
            (f"{ZACH_TOKEN} legacy {ALLOW_SCOPE}", "reserved identity in token row on line 4 of 4"),
            (f"{ZACH_TOKEN} zach ,", "empty scope allowlist in token row on line 4 of 4"),
            (f"{ZACH_TOKEN} zach ___", "invalid scope in token row on line 4 of 4"),
        ],
    )
    def test_GUARDS_5_to_10_ALL_name_the_PHYSICAL_LINE(
        self, tmp_path: Path, row: str, sentence: str
    ):
        """🔴 EVERY GUARD IN THE LADDER, NOT THE ONE THAT WAS CONVENIENT. The
        index was wrong in ALL of them — it is one loop — so a fix measured at
        one site is a fix that can be half-applied at the others and still look
        green. The bad row sits on physical line 4 behind two blank lines and a
        good row, so an ordinal over non-blank rows would say "2".
        """
        path = self._write(tmp_path, "", f"{DANA_TOKEN} dana {DENY_SCOPE}", "", row)
        message = str(exc_of(lambda: api.load_tokens(path, {}, warn=lambda _l: None)))
        assert sentence in message, message
        assert "of 2" not in message, (
            "`total` is still a count of non-blank rows, so 'line 4 of 2' or "
            f"'line 2 of 2' can be printed for a 4-line file: {message}"
        )

    def test_GUARD_11_collapses_a_scope_list_written_in_A_DIFFERENT_ORDER(
        self, tmp_path: Path
    ):
        """🔴 ORDER IS A SPELLING, NOT A DISAGREEMENT, and it was measured being
        refused: `<tok> zach alpha,beta` and `<tok> zach beta,alpha` answered
        "two different authorities — zach (alpha,beta) and zach (beta,alpha)".
        Both grant the same SET, so there IS a defined answer and guard 11 may
        not claim there is none. Guard 11's own comment already promised this
        ("rows that merely SPELL one grant differently ... are recognised as the
        same grant") while the code delivered it for case-folding only.

        The FIRST row's spelling is what survives, which is the same rule the
        rest of the collapse follows.
        """
        path = self._write(
            tmp_path,
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE},{DENY_SCOPE}",
            f"{ZACH_TOKEN} zach {DENY_SCOPE},{ALLOW_SCOPE}",
        )
        assert loaded(path, {}) == [
            (ZACH_TOKEN, "zach", (ALLOW_SCOPE, DENY_SCOPE))
        ]

    def test_GUARD_11_a_GENUINE_disagreement_is_STILL_refused(
        self, tmp_path: Path
    ):
        """🔴 THE UPPER BOUND ON THE FIX ABOVE. Comparing SETS must not become
        comparing nothing: `alpha,beta` and `alpha` are two different grants and
        one is strictly wider, which is the fail-open direction. Same shape as
        the collapse above — one token, one identity, two scope lists — so a
        mutant that returned a constant key, or dropped `scopes` from the key
        entirely, passes the test above and dies here.
        """
        path = self._write(
            tmp_path,
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE},{DENY_SCOPE}",
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE}",
        )
        message = str(exc_of(lambda: api.load_tokens(path, {}, warn=lambda _l: None)))
        assert "duplicate token on lines 1 and 2" in message
        assert f"zach ({ALLOW_SCOPE},{DENY_SCOPE})" in message
        assert f"zach ({ALLOW_SCOPE})" in message

    def test_GUARD_11_a_BARE_row_never_collapses_into_a_MAPPED_one(
        self, tmp_path: Path
    ):
        """A bare row and a mapped row on ONE token are two authorities, and the
        refusal names the unrestricted one so the operator can see which reading
        they nearly got.

        ⚠ WHAT THIS DOES **NOT** PIN, said because the obvious reading of the
        name is wrong: it is not a test of `_authority_key`'s `None`-vs-empty
        asymmetry. The two rows differ in IDENTITY (`legacy` vs `zach`), so the
        identity component alone decides, and a mutant folding `None` into the
        empty frozenset SURVIVES this — measured. See `_authority_key`'s own
        note for why that mutant is unreachable rather than uncaught.
        """
        path = self._write(
            tmp_path, ZACH_TOKEN, f"{ZACH_TOKEN} zach {ALLOW_SCOPE}"
        )
        message = str(exc_of(lambda: api.load_tokens(path, {}, warn=lambda _l: None)))
        assert "duplicate token on lines 1 and 2" in message
        assert "legacy (UNRESTRICTED)" in message

    def test_a_WELL_FORMED_mapped_file_loads_with_its_allowlist(self, tmp_path: Path):
        """The positive control for all six guards above. Without it, a parser
        that rejected EVERYTHING would pass every test in this class.
        """
        path = self._write(
            tmp_path,
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE},{DENY_SCOPE}",
            f"{DANA_TOKEN} dana {DENY_SCOPE}",
        )
        assert loaded(path, {}) == [
            (ZACH_TOKEN, "zach", (ALLOW_SCOPE, DENY_SCOPE)),
            (DANA_TOKEN, "dana", (DENY_SCOPE,)),
        ]

    def test_an_allowlist_entry_is_FOLDED_the_way_the_reader_folds_a_scope(
        self, tmp_path: Path
    ):
        """🔴 An allowlist entry and the index key it must match cannot be
        allowed to disagree about case or `_` vs `-`: an entry that never
        matched would be a silently inert grant, which reads as a working one.
        """
        path = self._write(tmp_path, f"{ZACH_TOKEN} zach Kelp_Forest")
        assert loaded(path, {}) == [(ZACH_TOKEN, "zach", (ALLOW_SCOPE,))]


class TestLegacyRowsSurviveTheMigration:
    """🔴 CRITERION 10's REQUIREMENT, WHICH IS WHY THIS IS NOT A PREFERENCE.

    The old shared token has to keep working while clients move onto mapped
    rows, and the rollback is putting that one line back — a rollback that
    needed a code change would not be one.
    """

    def _write(self, tmp_path: Path, *rows: str) -> str:
        path = tmp_path / "tokens"
        path.write_text("\n".join(rows) + "\n")
        return str(path)

    def test_a_MIXED_file_of_legacy_and_mapped_rows_loads(self, tmp_path: Path):
        path = self._write(
            tmp_path,
            GOOD_TOKEN,                            # legacy, unrestricted
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE}",    # mapped
        )
        assert loaded(path, {}) == [
            (GOOD_TOKEN, "legacy", None),
            (ZACH_TOKEN, "zach", (ALLOW_SCOPE,)),
        ]

    def test_a_LEGACY_row_reads_EVERY_scope_over_HTTP(self, scoped_store: Path):
        """The unrestricted half, exercised rather than asserted from the shape:
        a bare token is served content from a scope no mapped row names.
        """
        with running(scoped_store, tokens=(GOOD_TOKEN,)) as (base, _):
            for scope, nuance in (
                (ALLOW_SCOPE, KELP_NUANCE),
                (DENY_SCOPE, QUARTZ_NUANCE),
                (THIRD_SCOPE, LANTERN_NUANCE),
            ):
                code, _h, body = fetch(f"{base}/api/v1/recall/{scope}", token=GOOD_TOKEN)
                assert code == 200, scope
                assert nuance.encode() in body, scope

    def test_the_startup_warning_NAMES_legacy_mode_and_its_fingerprints(
        self, tmp_path: Path
    ):
        """🔴 A one-line, loud, greppable statement that the store is running
        with an unrestricted credential. Without it "the migration is finished"
        is a guess, which is the same failure the `token=` fingerprint exists to
        stop for rotation.
        """
        path = self._write(
            tmp_path, GOOD_TOKEN, f"{ZACH_TOKEN} zach {ALLOW_SCOPE}"
        )
        warnings: list[str] = []
        api.load_tokens(path, {}, warn=warnings.append)
        assert len(warnings) == 1, warnings
        line = warnings[0]
        assert "LEGACY MODE" in line
        assert "1 of 2" in line
        assert api.token_id(GOOD_TOKEN) in line
        # NEGATIVE CONTROL on the same line: the mapped row is not legacy, so
        # its fingerprint must NOT be named as unrestricted.
        assert api.token_id(ZACH_TOKEN) not in line
        # …and never a credential.
        assert GOOD_TOKEN not in line and ZACH_TOKEN not in line

    def test_NO_warning_when_EVERY_row_is_mapped(self, tmp_path: Path):
        """🔴 THE POSITIVE CONTROL'S PARTNER. A warner that fires unconditionally
        would pass the test above and teach the operator to ignore the line.
        """
        path = self._write(
            tmp_path,
            f"{ZACH_TOKEN} zach {ALLOW_SCOPE}",
            f"{DANA_TOKEN} dana {DENY_SCOPE}",
        )
        warnings: list[str] = []
        api.load_tokens(path, {}, warn=warnings.append)
        assert warnings == []


class TestEnumerationChannelsAreClosed:
    """🔴 FOUR CHANNELS, MEASURED ON THE DEPLOYED POD, EACH WITH ITS OWN TEST.

    A per-route "is this scope yours" check would close NONE of the first three:
    they all fire on requests for a scope the caller IS allowed, or on a request
    that names no scope at all. Every test here drives `zach`, whose allowlist
    is `ALLOW_SCOPE` alone, and asserts on the names and CONTENT of the two
    scopes he may not see.
    """

    def test_CHANNEL_1_known_scopes_names_only_the_callers_own(
        self, scoped_store: Path
    ):
        """`scope-absent` renders "scopes the store does hold: …". At base that
        sentence enumerated the whole store to anybody holding any token.
        """
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, body = fetch(
                f"{base}/api/v1/recall/{PHANTOM_SCOPE}", token=ZACH_TOKEN
            )
        assert code == 200
        assert headers["X-Store-Status"] == "scope-absent"
        text = body.decode()
        assert ALLOW_SCOPE in text, "the caller's own scope vanished — over-filtered"
        assert DENY_SCOPE not in text
        assert THIRD_SCOPE not in text

    def test_CHANNEL_2_malformed_elsewhere_names_no_denied_scope(
        self, scoped_store: Path
    ):
        """🔴 THE CHANNEL THAT FIRES ON A SUCCESSFUL READ. The "(+N further
        malformed entries in OTHER scopes …)" block is rendered on EVERY status,
        so this leaks on a perfectly ordinary 200 for a scope the caller owns —
        no miss, nothing refused, nothing to notice.
        """
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, body = fetch(
                f"{base}/api/v1/recall/{ALLOW_SCOPE}", token=ZACH_TOKEN
            )
        assert code == 200
        assert headers["X-Store-Status"] == "recalled"
        text = body.decode()
        assert KELP_NUANCE in text, "the caller's own content vanished"
        assert DENY_SCOPE not in text
        # ⚠ `assert "broken-shard" not in text` WAS HERE AND HAS BEEN DELETED AS
        # VACUOUS, not moved. `render_malformed` emits `elsewhere` as a COUNT
        # with its scopes named and DELIBERATELY never a filename ("naming the
        # scopes makes it actionable without putting another scope's filenames,
        # which are client-identifying, on this screen"). So no filename from
        # another scope is rendered at base OR at HEAD, for ANY token — the
        # assertion could not have failed and was reading as coverage while
        # providing none. The line above is the real guard for this channel; the
        # filename half is pinned where it CAN fail, in the positive control
        # below, which drives the reading that does render the block.
    def test_CHANNEL_2_POSITIVE_CONTROL_a_legacy_token_DOES_see_it(
        self, scoped_store: Path
    ):
        """🔴 WITHOUT THIS THE TEST ABOVE IS A ZERO FROM A CHECK THAT MIGHT SEE
        NOTHING. The fixture must actually PRODUCE a `malformed_elsewhere` block
        naming the denied scope, or "the name is absent" is satisfied by a
        renderer that never emits the block at all.
        """
        with running(scoped_store, tokens=(GOOD_TOKEN,)) as (base, _):
            _c, _h, body = fetch(
                f"{base}/api/v1/recall/{ALLOW_SCOPE}", token=GOOD_TOKEN
            )
        text = body.decode()
        assert DENY_SCOPE in text, (
            "the fixture produced no cross-scope malformed block, so the "
            "negative assertion above proves nothing"
        )
        # 🔴 AND THE BLOCK IS A COUNT, NOT A ROW — pinned HERE because this is
        # the only reading in which the block is rendered at all, so it is the
        # only place the claim can fail. `render_malformed`'s contract is that a
        # scope OUTSIDE the one being recalled contributes its name and a number
        # and never a filename, because filenames are client-identifying. An
        # unrestricted caller is the widest reading there is: if `broken-shard`
        # is absent from THIS body, no narrower caller can see it either.
        assert "broken-shard" not in text
        assert "1 further malformed entry in OTHER scopes" in text

    def test_CHANNEL_3_all_scopes_search_narrows_to_the_callers_own(
        self, scoped_store: Path
    ):
        """🔴 THE ONE A PER-SCOPE CHECK STRUCTURALLY CANNOT COVER. `?all_scopes=1`
        NAMES NO SCOPE, so there is nothing for such a check to refuse — it
        searches the CONTENT of every scope in the store.
        """
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, _h, body = fetch(
                f"{base}/api/v1/search/{ALLOW_SCOPE}?q=drill+head+overheats"
                f"&all_scopes=1",
                token=ZACH_TOKEN,
            )
        assert code == 200
        text = body.decode()
        assert QUARTZ_NUANCE not in text
        assert DENY_SCOPE not in text
        assert THIRD_SCOPE not in text
        # 🔴 NARROWED IS NOT EMPTIED, AND WITHOUT THESE THREE THE TEST ABOVE IS
        # SATISFIED BY A SEARCH THAT LOOKED AT NOTHING. Measured: with
        # `scopes_searched` forced to `()` the body renders "searched 0 entries
        # in (none)" — which contains no denied scope name and no denied content,
        # so every assertion above passes while the feature is entirely inert.
        #
        # The caller's OWN scope must be named, the placeholder must be absent,
        # and the COUNT must have moved off zero. Three independent facts,
        # because a renderer could satisfy any one of them by accident.
        assert ALLOW_SCOPE in text, "the caller's own scope was not searched"
        assert "(none)" not in text
        assert "searched 1 entry" in text

    def test_CHANNEL_3_POSITIVE_CONTROL_a_legacy_token_DOES_find_it(
        self, scoped_store: Path
    ):
        """The query has to be one that HITS, or the narrowed search is
        indistinguishable from a query nothing matches.
        """
        with running(scoped_store, tokens=(GOOD_TOKEN,)) as (base, _):
            code, headers, body = fetch(
                f"{base}/api/v1/search/{ALLOW_SCOPE}?q=drill+head+overheats"
                f"&all_scopes=1",
                token=GOOD_TOKEN,
            )
        assert code == 200
        assert headers["X-Store-Status"] == "search-hit"
        assert QUARTZ_NUANCE in body.decode()

    def test_CHANNEL_4_the_snapshot_tar_carries_only_allowed_members(
        self, scoped_store: Path
    ):
        """🔴 ASSERTED OVER EXTRACTED MEMBER NAMES, NOT A STATUS CODE. This route
        never builds an index, so the filter that closes channels 1-3 does not
        reach it; a 200 here says nothing about what is inside the archive.
        """
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            code, headers, body = fetch(f"{base}/api/v1/snapshot", token=ZACH_TOKEN)
        assert code == 200
        with tarfile.open(fileobj=io.BytesIO(body), mode="r") as tar:
            names = sorted(tar.getnames())
        assert names == [f"{ALLOW_SCOPE}/{ALLOW_SCOPE}-entry.md"], names
        # The server's own count must describe the same filtered set, or
        # `cairn::install_snapshot`'s mismatch check refuses every scoped pull.
        assert headers["X-Store-Entries"] == "1"

    def test_CHANNEL_4_POSITIVE_CONTROL_a_legacy_token_gets_every_member(
        self, scoped_store: Path
    ):
        with running(scoped_store, tokens=(GOOD_TOKEN,)) as (base, _):
            code, headers, body = fetch(f"{base}/api/v1/snapshot", token=GOOD_TOKEN)
        assert code == 200
        with tarfile.open(fileobj=io.BytesIO(body), mode="r") as tar:
            names = sorted(tar.getnames())
        assert names == sorted(
            [f"{s}/{s}-entry.md" for s in (ALLOW_SCOPE, DENY_SCOPE, THIRD_SCOPE)]
            + [f"{DENY_SCOPE}/broken-shard.md"]
        ), names
        assert headers["X-Store-Entries"] == "4"

    def test_a_scope_FILTERED_snapshot_of_a_denied_scope_ships_nothing(
        self, scoped_store: Path
    ):
        """`?scope=` reaches the filesystem directly, so it is its own door into
        the same store — and it must answer for a denied scope exactly what it
        answers for one that never existed.

        🔴 COMPARED AS BYTES AND AS A HEADER SET, the way
        `TestRefusedIsIndistinguishableFromAbsent` compares its pair. This test
        used to check three NAMED facts — the code, one header and the member
        list — which is a claim about the three things somebody thought of. The
        docstring promises "exactly what it answers", and exactly is a byte
        comparison: a fourth `X-Store-*` header, or a differing tar footer, would
        discriminate a refused scope from an absent one while every named
        assertion stayed green.
        """
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            denied = fetch(
                f"{base}/api/v1/snapshot?scope={DENY_SCOPE}", token=ZACH_TOKEN
            )
            phantom = fetch(
                f"{base}/api/v1/snapshot?scope={PHANTOM_SCOPE}", token=ZACH_TOKEN
            )

        def store_headers(headers: dict) -> tuple:
            return tuple(
                sorted(
                    (k, v) for k, v in headers.items() if k.lower().startswith("x-store")
                )
            )

        assert denied[0] == phantom[0] == 200
        assert store_headers(denied[1]) == store_headers(phantom[1]), (
            f"X-Store-* headers differ:\n denied ={store_headers(denied[1])}\n"
            f" phantom={store_headers(phantom[1])}"
        )
        assert denied[2] == phantom[2], (
            "the tar BYTES differ — a refused scope is distinguishable from an "
            f"absent one:\n denied ={denied[2]!r}\n phantom={phantom[2]!r}"
        )
        # 🔴 AND THE SHARED ANSWER IS THE EMPTY ARCHIVE, not two identical
        # errors: byte-identity between two 503s would satisfy everything above
        # while serving nothing. Same reasoning as the recall/search pair.
        assert denied[1]["X-Store-Entries"] == "0"
        for body in (denied[2], phantom[2]):
            with tarfile.open(fileobj=io.BytesIO(body), mode="r") as tar:
                assert tar.getnames() == []

    def test_TWO_TOKENS_ON_ONE_SERVER_each_see_only_their_own(
        self, scoped_store: Path
    ):
        """🔴 THE SEAM, NOT THE COMPONENT. Both records are configured on ONE
        server, so this fails if the allowlist is resolved from anything other
        than the record that authenticated THIS request — a module-level cache,
        the first configured row, or a value left on the handler by the previous
        request on a keep-alive connection.
        """
        with running(scoped_store, tokens=(ZACH, DANA)) as (base, audit):
            zach_own = fetch(f"{base}/api/v1/recall/{ALLOW_SCOPE}", token=ZACH_TOKEN)
            zach_other = fetch(f"{base}/api/v1/recall/{DENY_SCOPE}", token=ZACH_TOKEN)
            dana_own = fetch(f"{base}/api/v1/recall/{DENY_SCOPE}", token=DANA_TOKEN)
            dana_other = fetch(f"{base}/api/v1/recall/{ALLOW_SCOPE}", token=DANA_TOKEN)

        assert zach_own[1]["X-Store-Status"] == "recalled"
        assert KELP_NUANCE.encode() in zach_own[2]
        assert dana_own[1]["X-Store-Status"] == "recalled"
        assert QUARTZ_NUANCE.encode() in dana_own[2]
        # …and each is told the OTHER's scope does not exist.
        assert zach_other[1]["X-Store-Status"] == "scope-absent"
        assert dana_other[1]["X-Store-Status"] == "scope-absent"
        assert QUARTZ_NUANCE.encode() not in zach_other[2]
        assert KELP_NUANCE.encode() not in dana_other[2]
        # The audit line says WHOSE request each was, which is the only record
        # that can answer "who read what" after the fact.
        assert "identity=zach" in audit[0] and "identity=zach" in audit[1]
        assert "identity=dana" in audit[2] and "identity=dana" in audit[3]

    def test_the_audit_line_still_carries_the_FINGERPRINT_not_only_the_identity(
        self, scoped_store: Path
    ):
        """🔴 `identity=` is ADDITIVE. Overlap rotation is checkable only through
        `token=`: two rows can hold one holder's current and previous credential,
        and the identity cannot tell them apart.
        """
        with running(scoped_store, tokens=(ZACH,)) as (base, audit):
            fetch(f"{base}/api/v1/recall/{ALLOW_SCOPE}", token=ZACH_TOKEN)
        assert f"token={api.token_id(ZACH_TOKEN)}" in audit[0]
        assert "identity=zach" in audit[0]
        assert "auth=ok" in audit[0]
        assert ZACH_TOKEN not in audit[0]

    def test_a_REJECTED_request_names_no_identity(self, scoped_store: Path):
        with running(scoped_store, tokens=(ZACH,)) as (base, audit):
            fetch(f"{base}/api/v1/recall/{ALLOW_SCOPE}", token="w" * 48)
        assert "identity=-" in audit[0]
        assert "auth=fail" in audit[0]


class TestRefusedIsIndistinguishableFromAbsent:
    """🔴 CRITERION 3, PROVEN RATHER THAN ASSUMED — and the comparison is built
    so that scope EXISTENCE is the only thing that varies.

    Two responses to `recall/<DENY_SCOPE>` from the SAME token at the SAME store
    path: once when that scope holds an entry, once when it never existed. The
    entry-file COUNT and the newest mtime are held constant across the two (the
    scope is rebuilt under a different name), because `X-Store-Snapshot` is
    store-wide and would otherwise differ for a reason that is not the one under
    test. See the module docstring's residual-leak note.
    """

    def _phases(self, tmp_path: Path):
        """Yields a builder for phase A (denied scope present) and phase B (it
        never existed), both at ONE path so `  store: <root>` cannot differ."""
        root = tmp_path / "store"

        def present():
            if root.exists():
                shutil.rmtree(root)
            return _build_store(
                root, {ALLOW_SCOPE: KELP_NUANCE, DENY_SCOPE: QUARTZ_NUANCE}
            )

        def absent():
            shutil.rmtree(root)
            # Same file COUNT, same mtimes, different scope name — so the only
            # fact that moved is whether DENY_SCOPE is on disk.
            return _build_store(
                root, {ALLOW_SCOPE: KELP_NUANCE, THIRD_SCOPE: LANTERN_NUANCE}
            )

        return root, present, absent

    def _ask(self, root: Path, token, path: str):
        with running(root, tokens=(token,)) as (base, _):
            code, headers, body = fetch(
                f"{base}{path}",
                token=token.token if hasattr(token, "token") else token,
            )
        store_headers = tuple(
            sorted((k, v) for k, v in headers.items() if k.lower().startswith("x-store"))
        )
        return code, store_headers, body

    def test_RECALL_a_refused_scope_is_BYTE_IDENTICAL_to_one_that_never_existed(
        self, tmp_path: Path
    ):
        root, present, absent = self._phases(tmp_path)
        present()
        refused = self._ask(root, ZACH, f"/api/v1/recall/{DENY_SCOPE}")
        absent()
        never = self._ask(root, ZACH, f"/api/v1/recall/{DENY_SCOPE}")

        assert refused[0] == never[0] == 200
        assert refused[1] == never[1], (
            f"X-Store-* headers differ:\n refused={refused[1]}\n absent ={never[1]}"
        )
        assert refused[2] == never[2], (
            "response bodies differ — a refused scope is distinguishable from an "
            "absent one:\n"
            f"refused: {refused[2]!r}\nabsent : {never[2]!r}"
        )
        # 🔴 And the shared answer is the ABSENT report, not two identical
        # errors: byte-identity between two 401s or two 503s would satisfy
        # everything above while serving nothing.
        assert dict(refused[1])["X-Store-Status"] == "scope-absent"
        assert b"NOTHING RECORDED YET" in refused[2].upper()
        assert QUARTZ_NUANCE.encode() not in refused[2]

    SEARCH_QUERY = f"/api/v1/search/{DENY_SCOPE}?q=drill+head+overheats"

    def test_SEARCH_a_refused_scope_is_BYTE_IDENTICAL_to_one_that_never_existed(
        self, tmp_path: Path
    ):
        root, present, absent = self._phases(tmp_path)
        present()
        refused = self._ask(root, ZACH, self.SEARCH_QUERY)
        absent()
        never = self._ask(root, ZACH, self.SEARCH_QUERY)

        assert refused[0] == never[0] == 200
        assert refused[1] == never[1], f"{refused[1]} != {never[1]}"
        assert refused[2] == never[2]
        assert dict(refused[1])["X-Store-Status"] == "scope-absent"

    def test_POSITIVE_CONTROL_the_RECALL_comparison_CAN_see_the_difference(
        self, tmp_path: Path
    ):
        """🔴 WITHOUT THIS, THE RECALL TEST ABOVE IS SATISFIED BY A SERVER THAT
        ANSWERS THE SAME BYTES TO EVERYTHING.

        The same two phases, driven by an UNRESTRICTED legacy token: present ->
        `recalled` with the entry's content, absent -> `scope-absent`. If this
        pair did not differ, the equality above would be measuring the harness
        rather than the fix.
        """
        root, present, absent = self._phases(tmp_path)
        present()
        seen = self._ask(root, GOOD_TOKEN, f"/api/v1/recall/{DENY_SCOPE}")
        absent()
        gone = self._ask(root, GOOD_TOKEN, f"/api/v1/recall/{DENY_SCOPE}")

        assert dict(seen[1])["X-Store-Status"] == "recalled"
        assert dict(gone[1])["X-Store-Status"] == "scope-absent"
        assert seen[2] != gone[2]
        assert QUARTZ_NUANCE.encode() in seen[2]

    def test_POSITIVE_CONTROL_the_SEARCH_comparison_CAN_see_the_difference(
        self, tmp_path: Path
    ):
        """🔴 THE SEARCH PATH HAD NO POSITIVE CONTROL AT ALL, and the recall one
        does not cover it: they are different routes, different renderers and
        different statuses.

        An equality between two responses is only evidence if the pair CAN
        differ. This server fail-closes to an EMPTY body without
        `SUBSYSTEM_STORE_TRUSTED_PROXIES` and a `CF-Connecting-IP` header, and
        two empty bodies compare identical — so "byte-identical" is exactly the
        assertion a broken harness satisfies best. Same two phases, same query,
        an UNRESTRICTED token: present -> `search-hit` carrying the matched
        nuance, absent -> `scope-absent`. Both non-empty, and different.
        """
        root, present, absent = self._phases(tmp_path)
        present()
        found = self._ask(root, GOOD_TOKEN, self.SEARCH_QUERY)
        absent()
        gone = self._ask(root, GOOD_TOKEN, self.SEARCH_QUERY)

        assert found[0] == gone[0] == 200
        assert dict(found[1])["X-Store-Status"] == "search-hit"
        assert dict(gone[1])["X-Store-Status"] == "scope-absent"
        assert found[2] != gone[2]
        assert QUARTZ_NUANCE.encode() in found[2]
        # …and neither body is the empty string, which is what a fail-closed
        # server returns and what would make the equality above vacuous.
        assert found[2] and gone[2]


class TestScopeRevisionIsGatedByConstruction:
    """🔴 THE ONE HEADER THE INDEX FILTER CANNOT REACH.

    `X-Store-Revision` is read off `<store>/<scope>/.git/HEAD`, a path the index
    knows nothing about. Today no scope in the served copy is a git repo, so it
    answers "unknown" for everything and the leak is LATENT — which is exactly
    the state in which a guard gets skipped. So the fixture MAKES a denied scope
    a real repo and asserts the header still cannot tell it from an absent one.
    """

    def _git(self, path: Path, *args: str):
        subprocess.run(
            ["git", "-C", str(path), *args],
            check=True,
            capture_output=True,
            env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "HOME": str(path),
                 **hermetic_git.MAINTENANCE_OFF},
        )

    def _repo(self, store: Path, scope: str) -> str:
        scope_dir = store / scope
        self._git(scope_dir, "init", "-q", "-b", "main")
        self._git(scope_dir, "config", "user.email", "t@example.invalid")
        self._git(scope_dir, "config", "user.name", "T")
        self._git(scope_dir, "add", f"{scope}-entry.md")
        self._git(scope_dir, "commit", "-qm", "seed")
        return subprocess.run(
            ["git", "-C", str(scope_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    def test_POSITIVE_CONTROL_the_fixture_really_is_a_repo_and_the_header_shows_it(
        self, scoped_store: Path
    ):
        """A "the header said unknown" assertion is worthless against a fixture
        that could never have produced a sha. This proves it could.
        """
        head = self._repo(scoped_store, DENY_SCOPE)
        assert len(head) == 40
        assert api.scope_revision(scoped_store, DENY_SCOPE) == head
        with running(scoped_store, tokens=(GOOD_TOKEN,)) as (base, _):
            _c, headers, _b = fetch(
                f"{base}/api/v1/recall/{DENY_SCOPE}", token=GOOD_TOKEN
            )
        assert headers["X-Store-Revision"] == head

    def test_a_DENIED_scopes_revision_is_unknown_even_though_it_HAS_one(
        self, scoped_store: Path
    ):
        head = self._repo(scoped_store, DENY_SCOPE)
        assert api.scope_revision(
            scoped_store, DENY_SCOPE, visible_scopes=(ALLOW_SCOPE,)
        ) == "unknown"
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            _c, denied, _b = fetch(
                f"{base}/api/v1/recall/{DENY_SCOPE}", token=ZACH_TOKEN
            )
            _c2, phantom, _b2 = fetch(
                f"{base}/api/v1/recall/{PHANTOM_SCOPE}", token=ZACH_TOKEN
            )
        assert denied["X-Store-Revision"] == "unknown"
        assert denied["X-Store-Revision"] == phantom["X-Store-Revision"]
        assert head not in denied["X-Store-Revision"]

    def test_the_callers_OWN_scope_still_reports_its_sha(self, scoped_store: Path):
        """🔴 OVER-FILTERING IS ALSO A FAILURE. A gate that answered "unknown"
        for everything would pass the test above and silently delete the
        determinism guarantee `scope@sha` exists for.
        """
        head = self._repo(scoped_store, ALLOW_SCOPE)
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            _c, headers, _b = fetch(
                f"{base}/api/v1/recall/{ALLOW_SCOPE}", token=ZACH_TOKEN
            )
        assert headers["X-Store-Revision"] == head

    def test_visible_scopes_None_is_UNRESTRICTED_matching_every_other_seam(
        self, scoped_store: Path
    ):
        head = self._repo(scoped_store, DENY_SCOPE)
        assert api.scope_revision(scoped_store, DENY_SCOPE, visible_scopes=None) == head
        # …and an EMPTY sequence is the opposite, not a synonym for None.
        assert api.scope_revision(
            scoped_store, DENY_SCOPE, visible_scopes=()
        ) == "unknown"


class TestTheReaderNarrowingItself:
    """`load_store`'s `visible_scopes`, unit-level — the one site both readers
    take their index from, so this is where over- and under-filtering show up
    without an HTTP layer in the way.
    """

    def test_None_is_unrestricted_and_an_EMPTY_SEQUENCE_is_the_opposite(
        self, scoped_store: Path
    ):
        """🔴 THE ASYMMETRY, PINNED. `None` and `()` are both falsy, so a guard
        written `if visible_scopes:` would treat an empty allowlist as
        unrestricted — a total bypass that every functional test with a
        populated allowlist would pass.
        """
        _s, wide = api.rc.load_store(scoped_store, verb="recalled")
        assert set(wide.scopes) == {ALLOW_SCOPE, DENY_SCOPE, THIRD_SCOPE}
        _s, none_visible = api.rc.load_store(
            scoped_store, verb="recalled", visible_scopes=()
        )
        assert none_visible.scopes == ()
        assert len(none_visible) == 0
        assert none_visible.malformed == ()

    def test_the_MALFORMED_tuple_is_narrowed_beside_by_scope(
        self, scoped_store: Path
    ):
        """Both public fields, or `malformed_outside` still names a denied scope
        while `scopes` does not — the half-fix that reads as a whole one.
        """
        _s, wide = api.rc.load_store(scoped_store, verb="recalled")
        assert [m.scope for m in wide.malformed] == [DENY_SCOPE]
        _s, narrow = api.rc.load_store(
            scoped_store, verb="recalled", visible_scopes=(ALLOW_SCOPE,)
        )
        assert narrow.scopes == (ALLOW_SCOPE,)
        assert narrow.malformed == ()
        assert narrow.malformed_outside((ALLOW_SCOPE,)) == ()

    def test_an_allowlist_entry_is_normalised_against_the_index_key(
        self, scoped_store: Path
    ):
        _s, narrow = api.rc.load_store(
            scoped_store, verb="recalled", visible_scopes=("Kelp_Forest",)
        )
        assert narrow.scopes == (ALLOW_SCOPE,)

    def test_a_MISSING_store_still_RAISES_rather_than_narrowing_to_empty(
        self, tmp_path: Path
    ):
        """🔴 THE FOUR-STATE RULE SURVIVES THE FILTER. `store-unreachable` and
        "you may see nothing" both produce an empty index; only the first may be
        a raise, and collapsing them is the exact conflation this whole module
        exists to prevent. A filter applied BEFORE the load would have done it.

        ⚠ RENAMED. This used to be called `test_an_UNREADABLE_store_still_RAISES…`
        and passes a store root that does not EXIST — a different condition, a
        different error type and a different line of `load_store`. The
        genuinely-unreadable path is `TestUnreadableEntriesInDeniedScopes` below,
        which is where the interesting behaviour is.
        """
        with pytest.raises(api.rc.StoreMissingError):
            api.rc.load_store(
                tmp_path / "absent", verb="recalled", visible_scopes=(ALLOW_SCOPE,)
            )

    def test_recall_and_search_BOTH_take_the_narrowing(self, scoped_store: Path):
        """One kwarg, two callers — and a threading bug that reached only one of
        them would leave `/search` wide open while `/recall` looked fixed.
        """
        rep = api.rc.recall(
            scoped_store, DENY_SCOPE, visible_scopes=(ALLOW_SCOPE,)
        )
        assert rep.status == "scope-absent"
        assert rep.known_scopes == (ALLOW_SCOPE,)
        sea = api.rc.search(
            scoped_store, ALLOW_SCOPE, "drill head overheats",
            all_scopes=True, visible_scopes=(ALLOW_SCOPE,),
        )
        assert sea.scopes_searched == (ALLOW_SCOPE,)
        assert sea.known_scopes == (ALLOW_SCOPE,)
        assert sea.total_hits == 0

    def test_the_CLI_DEFAULT_is_still_unrestricted(self, scoped_store: Path):
        """The local reader must not have acquired an allowlist by accident:
        `cairn` and `/resume` call these with no such argument and read the whole
        store, and a default of `()` here would empty every local recall.
        """
        assert api.rc.recall(scoped_store, DENY_SCOPE).status == "recalled"
        assert set(api.rc.recall(scoped_store, ALLOW_SCOPE).known_scopes) == {
            ALLOW_SCOPE, DENY_SCOPE, THIRD_SCOPE
        }


# The name in the 503 body an unreadable entry used to produce. Distinct from
# every other literal in this file, so "did this filename reach the caller" is a
# single `in` and cannot be satisfied by coincidence.
LOCKED_ENTRY = "sealed-adit.md"
# An Emacs lock file: a DANGLING symlink whose name starts with a dot and ends
# `.md`. `Path.glob("*.md")` matches a leading dot — measured, not assumed, and
# pinned by a test below — so it IS a candidate entry, and this exact shape has
# been observed 503ing `/api/v1/recall/<scope>` in practice.
EMACS_LOCK = ".#sealed-adit.md"


def _make_unreadable(store: Path, scope: str, kind: str) -> Path:
    """Put ONE hostile candidate entry into `<store>/<scope>/`.

    `kind` is `perm` (a mode-000 regular file), `emacs` (the dangling lock-file
    symlink) or `fifo` (a named pipe, which blocks `open()` until somebody
    writes). All three are real shapes seen on a real store, not contrivances.
    """
    target = store / scope / (EMACS_LOCK if kind == "emacs" else LOCKED_ENTRY)
    if kind == "perm":
        target.write_text(_entry("sealed-adit", scope, nuance="- sealed."))
        os.chmod(target, 0o000)
    elif kind == "emacs":
        os.symlink("zach@host.4242:1767225600", target)
    elif kind == "fifo":
        os.mkfifo(target)
    else:  # pragma: no cover - a typo in a test argument is a test bug
        raise AssertionError(kind)
    return target


class TestTheLoaderItselfTakesTheAllowlist:
    """🔴 `load_index` DIRECTLY — the surface `load_store` hides.

    `load_store` narrows its RESULT as well, so through that door three separate
    mutations of the loader's own filter are invisible: an empty allowlist read
    as unrestricted, a denied scope's NAME registered before the skip, and a
    directory name compared unfolded. Each was measured SURVIVING a sweep that
    only drove `load_store`, and each is a real defect for the OTHER callers of
    this function — `subsystem_touch`, and anything that hands the result
    somewhere the post-filter is not.

    The seam guard is the pair: these tests plus the `load_store` ones above.
    Neither half alone pins the loader.
    """

    def _store(self, tmp_path: Path, *scope_dirs: str) -> Path:
        """A store whose scope DIRECTORY NAMES are exactly as given.

        Spelled by hand rather than through `_build_store`, because one test
        below needs a directory whose name does NOT equal its own folded form
        and that fixture is the whole point of it.
        """
        root = tmp_path / "store"
        for name in scope_dirs:
            (root / name).mkdir(parents=True)
            (root / name / f"{name}-entry.md").write_text(
                _entry(f"{name}-entry", name, nuance=f"- 2026-03-04: {name} drifts.")
            )
        return root

    def test_POSITIVE_CONTROL_no_allowlist_loads_every_scope(self, tmp_path: Path):
        """Without this the three tests below are satisfied by a loader that
        returns an empty index for everything.
        """
        root = self._store(tmp_path, ALLOW_SCOPE, DENY_SCOPE)
        index = api.rc.load_index(root, on_malformed="collect")
        assert set(index.scopes) == {ALLOW_SCOPE, DENY_SCOPE}

    def test_an_EMPTY_allowlist_registers_NO_scope_not_EVERY_scope(
        self, tmp_path: Path
    ):
        """🔴 THE ASYMMETRY, PINNED AT THE LOADER TOO. `None` and `()` are both
        falsy, so a filter written `if allowed and …` treats "you may see
        nothing" as "you may see everything" — a total bypass, and one that every
        test with a POPULATED allowlist passes.

        `load_store` re-narrows its result, which is why this mutation survives
        entirely when measured through that door. Here there is nothing in the
        way.
        """
        root = self._store(tmp_path, ALLOW_SCOPE, DENY_SCOPE)
        index = api.rc.load_index(root, on_malformed="collect", visible_scopes=())
        assert index.scopes == ()
        assert len(index) == 0

    def test_a_denied_scopes_NAME_is_not_registered_either(self, tmp_path: Path):
        """🔴 SKIPPING THE READ IS NOT SKIPPING THE SCOPE. A filter placed one
        line too late still appends the directory name to `extra_scopes`, so the
        denied scope arrives on `index.scopes` — the `known_scopes` enumeration
        channel — having never been opened. It reads as fixed and leaks the one
        fact the channel was about: that the scope EXISTS.

        Invisible through `load_store`, which drops the key again on the way out.
        """
        root = self._store(tmp_path, ALLOW_SCOPE, DENY_SCOPE)
        index = api.rc.load_index(
            root, on_malformed="collect", visible_scopes=(ALLOW_SCOPE,)
        )
        assert index.scopes == (ALLOW_SCOPE,)

    def test_the_DIRECTORY_NAME_is_FOLDED_before_it_is_compared(
        self, tmp_path: Path
    ):
        """🔴 OVER-FILTERING, AND THE FIXTURE HAS TO REACH IT. Every other store
        in this file has directory names that are already their own folded form,
        so a filter comparing the RAW `scope_dir.name` behaves identically and
        the mutation survives. Here the directory really is spelled
        `Kelp_Forest`: the index key it produces is `kelp-forest`, so an
        allowlist naming `kelp-forest` must still match it, and an unfolded
        comparison silently empties the caller's OWN scope.
        """
        raw_dir = "Kelp_Forest"
        assert api.rc.normalize_ref(raw_dir) == ALLOW_SCOPE != raw_dir, (
            "the fixture directory must NOT already equal its folded form, or "
            "this test measures nothing"
        )
        root = self._store(tmp_path, raw_dir, DENY_SCOPE)
        index = api.rc.load_index(
            root, on_malformed="collect", visible_scopes=(ALLOW_SCOPE,)
        )
        assert index.scopes == (ALLOW_SCOPE,)
        assert len(index) == 1


class TestUnreadableEntriesInDeniedScopes:
    """🔴 THE INDEX LOADER USED TO OPEN EVERY FILE IN THE STORE BEFORE THE
    ALLOWLIST WAS APPLIED, AND SCOPED CALLERS MADE THAT THREE DEFECTS AT ONCE.

    `load_store` narrowed the index it got BACK; `load_index` had already walked
    and read the whole store to build it. So one unreadable entry in a scope a
    caller may not see:

      * put that file's FULL PATH — and therefore the denied scope's name — into
        a 503 body that caller could read. A scoped reader had no other way to
        learn the name existed, which is the exact enumeration surface the rest
        of this section closes;
      * broke `/recall` and `/search` for EVERY caller, including the ones whose
        own scopes were perfectly readable;
      * and for a FIFO named `*.md`, blocked the request thread on `open()`
        indefinitely. `/snapshot` already refused that by kind
        (`_ENTRY_ACTIONS[KIND_OTHER] == REFUSE`); the index path did not.

    Fixed in TWO steps, and the split matters because they cover different
    callers:

      1. `visible_scopes` pushed DOWN into `load_index`, so a denied scope dir
         is never descended into at all. Protects a SCOPED caller only.
      2. an entry-KIND check before `open()`
         (`subsystem_resolver._LOADER_ENTRY_ACTIONS`), which protects EVERY
         caller including the unrestricted bare legacy token the pod runs.

    🔴 AND THE LIMIT OF (2) IS TESTED TOO, not just stated: it is NARROW. A
    `chmod 000` regular file still raises for an unrestricted caller —
    `test_an_UNREADABLE_REGULAR_FILE_still_RAISES_and_THAT_is_the_residual` is
    the honest record of what did NOT close.
    """

    def _store(self, tmp_path: Path, kind: str) -> Path:
        store = _build_store(
            tmp_path / "store",
            {ALLOW_SCOPE: KELP_NUANCE, DENY_SCOPE: QUARTZ_NUANCE},
        )
        _make_unreadable(store, DENY_SCOPE, kind)
        return store

    def test_the_FIXTURE_really_is_a_candidate_entry_glob_matches_a_dotfile(
        self, tmp_path: Path
    ):
        """🔴 THE POSITIVE CONTROL FOR THE EMACS SHAPE. If `glob("*.md")` did not
        match a leading dot, `.#sealed-adit.md` would never be opened, every
        assertion about it would be vacuous, and the whole case would be a story
        about a file the loader never sees.
        """
        d = tmp_path / "scope"
        d.mkdir()
        os.symlink("dangling", d / EMACS_LOCK)
        assert [p.name for p in d.glob("*.md")] == [EMACS_LOCK]

    @pytest.mark.parametrize("kind", ["perm", "emacs"])
    def test_a_SCOPED_caller_is_unaffected_by_an_unreadable_DENIED_entry(
        self, tmp_path: Path, kind: str
    ):
        store = self._store(tmp_path, kind)
        _s, index = api.rc.load_store(
            store, verb="recalled", visible_scopes=(ALLOW_SCOPE,)
        )
        assert index.scopes == (ALLOW_SCOPE,)
        assert index.malformed == ()

    def test_POSITIVE_CONTROL_the_same_file_in_the_CALLERS_OWN_scope_still_RAISES(
        self, tmp_path: Path
    ):
        """🔴 WITHOUT THIS THE TEST ABOVE IS SATISFIED BY A FIXTURE THAT CREATED
        NOTHING UNREADABLE. It also pins the half that must NOT change: the
        four-state rule. "I could not read your store" and "you may see nothing"
        both produce an empty index, and only the first may raise.

        ⚠ `perm` ONLY, and the parametrize over `emacs` that used to be here was
        DELETED rather than left to fail: the entry-kind guard now refuses a
        broken link before `open()`, so that shape no longer raises for ANY
        caller. Its own-scope behaviour is asserted below, as a collected
        malformed row.
        """
        store = self._store(tmp_path, "perm")
        with pytest.raises(api.rc.EntryUnreadableError):
            api.rc.load_store(store, verb="recalled", visible_scopes=(DENY_SCOPE,))

    def test_the_BROKEN_LINK_in_the_CALLERS_OWN_scope_is_REPORTED_not_fatal(
        self, tmp_path: Path
    ):
        """The other side of the guard: refusing an entry must not silently
        empty the scope that holds it. The caller who OWNS the hostile file
        still gets their good entry, and still gets told about the bad one.
        """
        store = self._store(tmp_path, "emacs")
        _s, index = api.rc.load_store(
            store, verb="recalled", visible_scopes=(DENY_SCOPE,)
        )
        assert index.scopes == (DENY_SCOPE,)
        assert len(index) == 1
        assert [m.label for m in index.malformed] == [f"{DENY_SCOPE}/{EMACS_LOCK}"]

    def test_the_UNRESTRICTED_reading_of_a_BROKEN_LINK_no_longer_DIES(
        self, tmp_path: Path
    ):
        """🔴 THE INVERSION. This test used to be
        `test_the_UNRESTRICTED_reading_is_UNCHANGED_and_that_is_the_residual`
        and asserted `pytest.raises(EntryUnreadableError)` — the honest record
        of a residual the allowlist pushdown could not reach. The residual was
        CLOSED by the entry-kind guard, so the same input must now assert the
        opposite, and the name has to say which.

        `visible_scopes=None` still skips no SCOPE — that half is unchanged and
        is why this matters: the pod runs a bare legacy token, which is
        unrestricted. What changed is that the candidate's KIND is decided
        before `open()`, so the Emacs lock file costs its own entry and nothing
        else.
        """
        store = self._store(tmp_path, "emacs")
        _s, index = api.rc.load_store(store, verb="recalled")
        # The OTHER scope's content survived, which is the DoS half.
        assert set(index.scopes) == {ALLOW_SCOPE, DENY_SCOPE}
        assert len(index) == 2, "a good entry was dropped along with the bad one"
        # …and the bad entry is REPORTED, not silently skipped: a dropped entry
        # is indistinguishable from one nobody ever wrote, which is the exact
        # conflation this store guards against everywhere else.
        assert [m.label for m in index.malformed] == [f"{DENY_SCOPE}/{EMACS_LOCK}"]
        assert "broken symlink" in index.malformed[0].reason

    def test_an_UNREADABLE_REGULAR_FILE_still_RAISES_and_THAT_is_the_residual(
        self, tmp_path: Path
    ):
        """🔴 THE HALF THAT IS **NOT** CLOSED, kept as its own named test rather
        than left implied by the parametrize list this used to share.

        A `chmod 000` regular file classifies as `regular-file`, which the
        loader TAKES — deliberately. `read_text` then raises, and an OSError
        fails closed in both policies: "the store was not fully READ" is a
        different fact from "this entry is malformed", and only the second has
        an honest degraded form. So for THIS shape an unrestricted caller is
        exactly as exposed as before: 503, and the path in the body
        (`test_POSITIVE_CONTROL_a_LEGACY_token_DOES_still_get_the_503`).

        It is also the negative control for the test above: if the kind guard
        were quietly widened to refuse everything it could not read, this would
        go green-by-collapse and the four-state rule would be gone.
        """
        store = self._store(tmp_path, "perm")
        with pytest.raises(api.rc.EntryUnreadableError):
            api.rc.load_store(store, verb="recalled")

    def test_the_503_body_NAMED_the_denied_scope_and_its_PATH_over_HTTP(
        self, tmp_path: Path
    ):
        """🔴 THE DISCLOSURE ITSELF, DRIVEN THROUGH THE SERVER — the layer where
        it was a leak rather than an exception type.

        `zach` may see `ALLOW_SCOPE` only. He asks for his OWN scope, which is
        readable, and used to be answered `503 index entry unreadable: under …
        (PermissionError: … '<store>/quartz-mine/sealed-adit.md')`. Both halves
        matter: the status is a denial of service he did not cause, and the body
        names a scope and a filename he is not allowed to know exist.
        """
        store = self._store(tmp_path, "perm")
        with running(store, tokens=(ZACH,)) as (base, _):
            code, headers, body = fetch(
                f"{base}/api/v1/recall/{ALLOW_SCOPE}", token=ZACH_TOKEN
            )
        text = body.decode()
        assert code == 200, f"{code}: {text[:400]}"
        assert headers["X-Store-Status"] == "recalled"
        assert KELP_NUANCE in text, "the caller's own content vanished"
        assert DENY_SCOPE not in text
        assert LOCKED_ENTRY not in text
        # …and therefore not the hostile file's path either. Spelled out because
        # the PATH is what the 503 carried and the two assertions above are only
        # its components.
        #
        # 🔴 NEITHER `"unreadable" not in text` NOR `str(store) not in text` WOULD
        # DO, and both were tried: the caveat block explains what
        # `unstamped/unreadable/none` mean on EVERY report, and the store ROOT is
        # printed on every report as `  store: <root>`. Both are present on a
        # perfectly healthy 200, so both would be red for a reason that has
        # nothing to do with this defect. What leaked was the scope name and the
        # filename UNDER the root, which is what is asserted.
        assert str(store / DENY_SCOPE / LOCKED_ENTRY) not in text

    def test_POSITIVE_CONTROL_a_LEGACY_token_DOES_still_get_the_503(
        self, tmp_path: Path
    ):
        """🔴 THE FIXTURE MUST ACTUALLY PRODUCE AN UNREADABLE ENTRY. Without this
        the assertions above are a zero from a check that might see nothing — a
        `chmod 000` that silently failed (running as root, an exotic filesystem)
        would leave every one of them green against a perfectly healthy store.

        It is also the honest record of the residual: unrestricted callers still
        get the 503, and still get the path in it.
        """
        store = self._store(tmp_path, "perm")
        with running(store, tokens=(GOOD_TOKEN,)) as (base, _):
            code, headers, body = fetch(
                f"{base}/api/v1/recall/{ALLOW_SCOPE}", token=GOOD_TOKEN
            )
        assert code == 503, f"the fixture is readable after all: {code}"
        assert headers["X-Store-Status"] == "store-unreachable"
        text = body.decode()
        assert LOCKED_ENTRY in text and DENY_SCOPE in text

    def test_a_FIFO_named_md_in_a_DENIED_scope_no_longer_HANGS_the_reader(
        self, tmp_path: Path
    ):
        """🔴 THE MOST SERIOUS OF THE THREE, AND THE ONE A NORMAL TEST CANNOT
        ASSERT: a wedged thread produces no exception and no value, so there is
        nothing to `pytest.raises` on. It is measured in a CHILD PROCESS under a
        wall-clock deadline, because a test that can hang is a suite that can
        hang.

        On a `replicas: 1` Deployment an `open()` that never returns is worse
        than the 503: the worker is gone and the next request queues behind it.

        🔴 THE UNRESTRICTED ARM IS AN INVERSION. It used to be the NEGATIVE
        CONTROL — "the unrestricted load of the SAME store must still hang", the
        residual the allowlist pushdown could not reach. The entry-kind guard
        closed it, so the same probe must now COMPLETE, load both scopes, and
        report the FIFO as a malformed entry. A control that asserts a hazard is
        still live cannot survive the hazard being fixed; it has to be re-aimed
        or it becomes a test pinning the bug.

        What keeps the timeout meaningful instead is
        `test_a_SYMLINK_to_a_FIFO_no_longer_HANGS_and_the_DEADLINE_still_SEES_one`
        below. ⚠ That test's control had to be re-aimed too: it used to point at
        `link-to-other`, a kind the loader still TAKES — and it does not any
        more, because that shape was measured wedging a live request thread for
        25s. No remaining TAKE cell blocks, so the control is now a bare
        `open()` of the fifo itself, which is the syscall in question rather
        than a proxy for it.
        """
        store = self._store(tmp_path, "fifo")
        probe = (
            "import sys;"
            f"sys.path.insert(0, {str(RECALL_PATH.parent)!r});"
            "import subsystem_recall as rc;"
            f"vs = None if sys.argv[1] == 'unrestricted' else ({ALLOW_SCOPE!r},);"
            f"_s, i = rc.load_store({str(store)!r}, verb='recalled', visible_scopes=vs);"
            "print('SCOPES=' + ','.join(i.scopes));"
            "print('MALFORMED=' + ','.join(m.label for m in i.malformed))"
        )

        def run(arg: str, deadline: float):
            """The completed process, or `None` meaning it blew the deadline."""
            try:
                return subprocess.run(
                    [sys.executable, "-c", probe, arg],
                    capture_output=True, text=True, timeout=deadline,
                )
            except subprocess.TimeoutExpired:
                return None

        scoped = run("scoped", 30.0)
        assert scoped is not None, (
            "the scoped reader HUNG on a FIFO in a scope it may not see — the "
            "allowlist is not reaching `load_index`"
        )
        assert scoped.returncode == 0, scoped.stderr[-600:]
        assert f"SCOPES={ALLOW_SCOPE}\n" in scoped.stdout, scoped.stdout
        # The denied scope was never descended into, so its FIFO is not even
        # reported — step 1 still does its own job.
        assert "MALFORMED=\n" in scoped.stdout, scoped.stdout

        unrestricted = run("unrestricted", 15.0)
        assert unrestricted is not None, (
            "the UNRESTRICTED reader HUNG on the FIFO — the entry-kind guard is "
            "not reaching `load_index`, and on a `replicas: 1` Deployment that "
            "is a worker that never comes back"
        )
        assert unrestricted.returncode == 0, unrestricted.stderr[-600:]
        assert f"SCOPES={ALLOW_SCOPE},{DENY_SCOPE}\n" in unrestricted.stdout
        assert f"MALFORMED={DENY_SCOPE}/{LOCKED_ENTRY}\n" in unrestricted.stdout

    def test_a_SYMLINK_to_a_FIFO_no_longer_HANGS_and_the_DEADLINE_still_SEES_one(
        self, tmp_path: Path
    ):
        """🔴 THE SECOND INVERSION, AND THE PROBE'S OWN POSITIVE CONTROL, in one
        test — because the two have to move together.

        This test used to be
        `test_a_TAKEN_kind_still_HANGS_which_is_why_the_REFUSE_cells_exist`, and
        asserted that a symlink POINTING AT a fifo blocks the reader forever. It
        was the honest record of the `link-to-other` residual the narrow ruling
        left open, and simultaneously the positive control proving the deadline
        machinery can observe a hang at all.

        The residual was then MEASURED rather than reasoned about: on the tip
        that carried it, an unrestricted (bare legacy) `GET
        /api/v1/recall/<scope>` against a store holding one `link-to-fifo.md` in
        a DIFFERENT scope wedged for 25s and the request thread never came back,
        while `/healthz` answered 200 throughout — so the process was up and the
        worker was gone. `open()` blocks the same whether the fifo is reached
        directly or through a link. The cell is now REFUSE, so the same input
        must assert the opposite: complete, load both scopes, and REPORT the
        link as a malformed entry.

        🔴 AND THAT LEAVES A HOLE THIS TEST MUST FILL ITSELF. Every timing
        assertion in the class is now "it did NOT hang", which is the reassuring
        zero `claude/RULES.md` calls indistinguishable from a harness wired to
        nothing — and no kind the loader still TAKES blocks, so the old control
        cannot simply be re-aimed at another cell. The control below therefore
        drives the SAME deadline machinery at a bare `open()` of the SAME fifo,
        which must blow it. A fixture that made no real fifo, or a subprocess
        runner that never blocks, fails there and takes the vacuous green with
        it.
        """
        store = _build_store(
            tmp_path / "store",
            {ALLOW_SCOPE: KELP_NUANCE, DENY_SCOPE: QUARTZ_NUANCE},
        )
        real_fifo = tmp_path / "a-real-fifo"
        os.mkfifo(real_fifo)
        link = store / DENY_SCOPE / LOCKED_ENTRY
        os.symlink(real_fifo, link)
        assert api.classify_path(link) == api.KIND_LINK_TO_OTHER, (
            "the fixture is not the kind this test is about"
        )

        def under_deadline(source: str, seconds: float):
            """The completed process, or `None` meaning it blew the deadline."""
            try:
                return subprocess.run(
                    [sys.executable, "-c", source],
                    capture_output=True, text=True, timeout=seconds,
                )
            except subprocess.TimeoutExpired:
                return None

        # 🔴 THE POSITIVE CONTROL FIRST, so a runner that cannot observe a block
        # fails before the assertion that depends on it means anything. A bare
        # `open()` of the fifo — no store, no loader — is the syscall the guard
        # exists to keep the request thread out of.
        blocked = under_deadline(
            f"open({str(real_fifo)!r}); print('OPENED')", 10.0
        )
        assert blocked is None, (
            "a bare `open()` of the fifo RETURNED, so this fixture is not a "
            "blocking fifo and the deadline below measures nothing — every "
            "'it did not hang' assertion in this class would be vacuous. "
            f"stdout={None if blocked is None else blocked.stdout!r}"
        )

        probe = (
            "import sys;"
            f"sys.path.insert(0, {str(RECALL_PATH.parent)!r});"
            "import subsystem_recall as rc;"
            f"_s, i = rc.load_store({str(store)!r}, verb='recalled');"
            "print('SCOPES=' + ','.join(i.scopes));"
            "print('MALFORMED=' + ','.join(m.label for m in i.malformed))"
        )
        done = under_deadline(probe, 30.0)
        assert done is not None, (
            "an UNRESTRICTED read of a store holding a SYMLINK-to-FIFO entry "
            "HUNG — `_LOADER_ENTRY_ACTIONS[KIND_LINK_TO_OTHER]` is not REFUSE, "
            "or the guard is not reaching `load_index`. On a `replicas: 1` "
            "Deployment that is a worker that never comes back"
        )
        assert done.returncode == 0, done.stderr[-600:]
        assert f"SCOPES={ALLOW_SCOPE},{DENY_SCOPE}\n" in done.stdout, done.stdout
        # REPORTED, not skipped — a dropped entry is indistinguishable from one
        # nobody ever wrote, which is the conflation this store exists to avoid.
        assert f"MALFORMED={DENY_SCOPE}/{LOCKED_ENTRY}\n" in done.stdout, done.stdout


class TestTheLoaderRefusesHostileEntriesByKind:
    """🔴 THE NARROW ENTRY-KIND GUARD — what it closes, and what it must not.

    The allowlist pushdown protects a SCOPED caller. `visible_scopes=None` skips
    nothing, and the live pod runs a BARE LEGACY token, which is unrestricted —
    so on the deployed configuration a single `.#entry.md` lock file 503'd every
    recall and a single FIFO wedged the worker. That is the configuration these
    tests drive.

    🔴 AND THE GUARD IS NARROW BY DECISION, NOT BY ACCIDENT. Mirroring
    `/snapshot`'s `_ENTRY_ACTIONS` wholesale also refuses a symlink to a regular
    file, a directory and an unstat-able path — all of which this loader reads or
    fails on TODAY, so the broad form is a behaviour change for every local CLI
    caller. `test_a_SYMLINKED_entry_is_STILL_READ…` is the test that kills the
    broad form; without it "refuse hostile kinds" has no upper bound.

    ⚠ NARROW IS NOT FROZEN. The guard has THREE cells, not the two it shipped
    with: `link-to-other` (a symlink pointing at a fifo/socket/device) was left
    TAKE for one round as a named residual, then measured wedging an
    unrestricted `/recall` for 25s and closed. That widening is still inside the
    upper bound — it refuses a shape no legitimate entry has, and
    `link-to-file`, the cell the narrow ruling was actually about, is untouched.
    """

    def _hostile(self, tmp_path: Path) -> Path:
        """One store, BOTH refused shapes, in a scope that is not the one asked
        for — the arrangement the operator reproduced: unrestricted token, a
        dangling `.#lock.md` in `bravo`, and a recall for `alpha`.
        """
        store = _build_store(
            tmp_path / "store",
            {ALLOW_SCOPE: KELP_NUANCE, DENY_SCOPE: QUARTZ_NUANCE},
        )
        _make_unreadable(store, DENY_SCOPE, "emacs")
        _make_unreadable(store, DENY_SCOPE, "fifo")
        return store

    def test_an_UNRESTRICTED_recall_of_ANOTHER_scope_is_200_not_503(
        self, tmp_path: Path
    ):
        """🔴 THE MEASURED SYMPTOM, DRIVEN THROUGH THE SERVER ON THE LIVE
        CREDENTIAL SHAPE. Before the guard this exact request answered `503
        index entry unreadable … '<store>/quartz-mine/.#sealed-adit.md'`.

        `GOOD_TOKEN` is a BARE row, so the record is `legacy`/unrestricted —
        the pod's own configuration, not a scoped token that would be protected
        by the allowlist pushdown instead.
        """
        store = self._hostile(tmp_path)
        with running(store, tokens=(GOOD_TOKEN,)) as (base, _):
            code, headers, body = fetch(
                f"{base}/api/v1/recall/{ALLOW_SCOPE}", token=GOOD_TOKEN
            )
        text = body.decode()
        assert code == 200, f"{code}: {text[:400]}"
        assert headers["X-Store-Status"] == "recalled"
        assert KELP_NUANCE in text, "the caller's own content vanished"

    def test_the_REFUSED_entries_are_SURFACED_not_silently_dropped(
        self, tmp_path: Path
    ):
        """🔴 A SKIP RENDERS AS "NOTHING RECORDED", which is the conflation this
        whole store exists to avoid — so the 200 above is only correct if the
        two refused files are still ACCOUNTED FOR.

        Asserted on the index rather than on the rendered body, because the body
        deliberately reports cross-scope defects as a COUNT with scopes named and
        never a filename; the per-file facts live here.
        """
        store = self._hostile(tmp_path)
        _s, index = api.rc.load_store(store, verb="recalled")
        labels = sorted(m.label for m in index.malformed)
        assert labels == sorted(
            [f"{DENY_SCOPE}/{EMACS_LOCK}", f"{DENY_SCOPE}/{LOCKED_ENTRY}"]
        )
        reasons = " ".join(m.reason for m in index.malformed)
        assert "broken symlink" in reasons
        assert "not a regular file" in reasons
        # …and the good entry in that same scope is still served.
        assert len(index.entries(DENY_SCOPE)) == 1

    def test_a_SYMLINKED_entry_is_STILL_READ_the_guard_is_NOT_the_broad_one(
        self, tmp_path: Path
    ):
        """🔴 THE UPPER BOUND ON THE GUARD, AND THE MUTANT IT EXISTS TO KILL.

        `_ENTRY_ACTIONS[KIND_LINK_TO_FILE]` is REFUSE, because `/snapshot` will
        not follow a link out of the store. The LOADER has always read one, and
        a `<scope>/<slug>.md -> ../shared/<slug>.md` symlink is an ordinary way
        to keep one entry in two places. Copying `/snapshot`'s column here is the
        over-broad form that was rejected on this PR; flipping that one cell to
        REFUSE turns this entry into a malformed row and fails here.

        The entry's CONTENT is asserted, not merely its presence: a guard that
        refused it would still leave the scope registered.
        """
        store = _build_store(tmp_path / "store", {ALLOW_SCOPE: KELP_NUANCE})
        real = tmp_path / "outside" / "linked-entry.md"
        real.parent.mkdir()
        real.write_text(
            _entry("linked-entry", ALLOW_SCOPE, nuance="- 2026-03-06: via a link.")
        )
        link = store / ALLOW_SCOPE / "linked-entry.md"
        os.symlink(real, link)
        assert api.classify_path(link) == api.KIND_LINK_TO_FILE, (
            "the fixture is not a symlink-to-regular-file, so this measures "
            "nothing about the cell it names"
        )

        _s, index = api.rc.load_store(store, verb="recalled")
        assert index.malformed == (), (
            "a symlinked entry was refused — the guard has been widened to the "
            "broad `_ENTRY_ACTIONS` form the narrow ruling rejected"
        )
        assert sorted(e.slug for e in index.entries(ALLOW_SCOPE)) == sorted(
            ["linked-entry", f"{ALLOW_SCOPE}-entry"]
        )

    def test_under_RAISE_a_refused_entry_RAISES_the_same_class_as_any_other(
        self, tmp_path: Path
    ):
        """🔴 THE POLICY IS `on_malformed`'s, NOT THE GUARD'S. The WRITER's probe
        loads with `RAISE` precisely because it must not modify a store it read
        only part of, and that is as true of a fifo as of a wrapped `aliases:`
        line. A guard that collected unconditionally would silently hand the
        writer a partial index.
        """
        store = self._hostile(tmp_path)
        with pytest.raises(api.rc.MalformedEntryError) as exc:
            api.rc.load_index(store)
        assert "malformed index entry" in str(exc.value)
        assert exc.value.source in (EMACS_LOCK, LOCKED_ENTRY)

    def test_a_BOGUS_policy_is_still_a_ValueError_not_a_refusal(
        self, tmp_path: Path
    ):
        """The guard branches on `on_malformed` BEFORE `build_index` validates
        it, so the predicate is shared (`_check_on_malformed`) rather than
        spelled twice. Spelled twice, a bogus policy on a hostile store would be
        answered with a complaint about the first fifo instead of about the
        policy — a message that sends the operator to the wrong file.
        """
        store = self._hostile(tmp_path)
        with pytest.raises(ValueError, match="on_malformed must be one of"):
            api.rc.load_index(store, on_malformed="collct")

    def test_the_REFUSED_row_is_filed_under_the_FOLDED_scope(self, tmp_path: Path):
        """🔴 THE SCOPE ON A `MalformedEntry` IS THE NORMALIZED ONE — that is
        `MalformedEntry`'s own contract, and `malformed_in` compares against
        `normalize_ref(scope)`. A refusal filed under the RAW directory name
        matches no scope, so it vanishes from `malformed_in` and surfaces only
        through the store-wide count: reported, but not against the scope that
        holds it, which is where the operator will look.

        The fixture directory really is spelled `Kelp_Forest`. Every other store
        in this file is already its own folded form, so an unfolded mutant
        survives them all.
        """
        raw_dir = "Kelp_Forest"
        assert api.rc.normalize_ref(raw_dir) == ALLOW_SCOPE != raw_dir
        store = _build_store(tmp_path / "store", {raw_dir: KELP_NUANCE})
        _make_unreadable(store, raw_dir, "emacs")

        _s, index = api.rc.load_store(store, verb="recalled")
        assert [m.label for m in index.malformed_in(ALLOW_SCOPE)] == [
            f"{ALLOW_SCOPE}/{EMACS_LOCK}"
        ]
        assert index.malformed_outside([ALLOW_SCOPE]) == ()

    def test_a_CLEAN_store_is_UNCHANGED_by_the_guard(self, tmp_path: Path):
        """The positive control. Every assertion above is about a hostile store;
        without this, a loader that refused EVERY candidate would satisfy the
        `.malformed` ones and only fail on content nobody asserted.
        """
        store = _build_store(
            tmp_path / "store",
            {ALLOW_SCOPE: KELP_NUANCE, DENY_SCOPE: QUARTZ_NUANCE},
        )
        _s, index = api.rc.load_store(store, verb="recalled")
        assert index.malformed == ()
        assert len(index) == 2
        assert set(index.scopes) == {ALLOW_SCOPE, DENY_SCOPE}


class TestScopeFilteringIsNotAWriteVerb:
    """🔴 CRITERIA 4-10 ARE NOT IN THIS BRANCH, AND THAT IS ASSERTED, NOT SAID.

    `TestPhaseOneScope` already pins the write guard and the route ledger; this
    is the same claim restated for the change that landed here, so a future
    branch that adds a verb "while it is touching auth anyway" fails a test in
    the section that added scoping.
    """

    def test_the_route_set_did_NOT_grow(self):
        assert set(api.API_ROUTES) == {"recall", "search", "snapshot"}

    def test_every_write_verb_is_STILL_405_on_EVERY_ROUTE_with_a_SCOPED_token(
        self, scoped_store: Path
    ):
        """🔴 EVERY ROUTE, ENUMERATED FROM `API_ROUTES` ITSELF — not `/recall`
        alone, which is what this used to probe.

        The two guards in this class were jointly walkable. A `do_POST` added on
        `/snapshot` passed BOTH: the route SET is unchanged, so
        `test_the_route_set_did_NOT_grow` is green, and the only path this test
        exercised was `/recall`. Driving the table means a route added to
        `API_ROUTES` is automatically probed, and a verb added to one existing
        route is caught by the route it was added to.
        """
        # A path per route, so the request reaches the route rather than the
        # `bad request: invalid path component` branch. Keyed on the ledger, and
        # asserted TOTAL below, so a new route cannot be silently unprobed.
        paths = {
            "recall": f"/api/v1/recall/{ALLOW_SCOPE}",
            "search": f"/api/v1/search/{ALLOW_SCOPE}?q=tide",
            "snapshot": "/api/v1/snapshot",
        }
        assert set(paths) == set(api.API_ROUTES), (
            "a route exists that this test has no path for, so it would go "
            f"unprobed: {sorted(set(api.API_ROUTES) ^ set(paths))}"
        )
        with running(scoped_store, tokens=(ZACH,)) as (base, _):
            for route, path in sorted(paths.items()):
                for method in ("POST", "PUT", "PATCH", "DELETE"):
                    code, _h, body = fetch(
                        f"{base}{path}", token=ZACH_TOKEN, method=method
                    )
                    assert code == 405, f"{method} {route} answered {code}"
                    assert body == b"read-only\n", f"{method} {route}: {body!r}"

    def test_a_full_scoped_read_workload_leaves_the_store_BYTE_IDENTICAL(
        self, scoped_store: Path
    ):
        before = tree_hash(scoped_store)
        with running(scoped_store, tokens=(ZACH, DANA)) as (base, _):
            for token in (ZACH_TOKEN, DANA_TOKEN):
                for path in (
                    f"/api/v1/recall/{ALLOW_SCOPE}",
                    f"/api/v1/recall/{DENY_SCOPE}",
                    f"/api/v1/recall/{PHANTOM_SCOPE}",
                    f"/api/v1/search/{ALLOW_SCOPE}?q=tide&all_scopes=1",
                    "/api/v1/snapshot",
                ):
                    fetch(f"{base}{path}", token=token)
        assert tree_hash(scoped_store) == before
