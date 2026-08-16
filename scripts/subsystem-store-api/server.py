#!/usr/bin/env python3
"""Read-only HTTP layer over the EXISTING subsystem-store reader. Phase 1.

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT
---------------------------------------------
`claudedocs/proposal-subsystem-store-homelab.md` §1: "the pod is the existing
code, unmodified, pointed at a PVC". This module is the thin part. It imports
`subsystem_recall` and returns `render_text()` / `render_search()` verbatim.

🔴 IT REIMPLEMENTS NO RENDERING. Not the digest, not the index page, not the
`MALFORMED` block, not the sensitivity fold, not the four-state discrimination.
Every one of those already exists and is already tested; a second copy here
would be a fork that passes its own tests while disagreeing with the CLI, which
is exactly the drift the proposal's "same code" premise rests on avoiding. The
one thing this file adds to the body is a trailing newline, because the CLI's
`print()` adds one and the phase-1 acceptance criterion is byte-identity with
the CLI's stdout.

⚠ THE BODY IS NOT PATH-INDEPENDENT. `render_text` prints `  store: <root>` —
one line, and the only line in the whole render that names the store root. The
pod serves from `/data` and the workbench reads `~/.claude/analyze-service-index`,
so remote and local bytes CANNOT be identical on that line and byte-identity has
to be asserted modulo exactly it. `verify-byte-identity.sh` does that
mechanically: it canonicalises that one line on both sides AND asserts the raw
diff is exactly one line, so a second divergence cannot hide inside the excuse.

PHASE 1 SCOPE — read-only, cluster-internal, no ingress
-------------------------------------------------------
GET is the only method that reaches a handler. There is no append endpoint, no
`PUT`, no `If-Match`; those are phase 3 (§2c) and writing them now would put an
unreviewed write path on a store whose only copy is on the workbench.

THE FOUR-STATE RULE, WHICH IS THE WHOLE POINT (§3)
--------------------------------------------------
🔴 `scope-empty` (reached the store, genuinely nothing recorded) and
`store-unreachable` (read nothing at all) MUST NOT render alike. The reader
already refuses to conflate them — `load_store` raises `StoreMissingError`
rather than returning an empty index — and this layer's job is to not throw that
away by catching everything into one 200. So:

    reached the store, nothing recorded  -> 200, X-Store-Status: scope-empty
    could not read the store at all      -> 503, X-Store-Status: store-unreachable

A 200 is a claim that the store was read. Only the first of those can make it.

AUTH (§2b). Phase 1 has no ingress, so nothing here faces the internet yet —
but the token exists NOW, because an auth layer first exercised on the day it
becomes internet-reachable is an auth layer nobody has watched deny anything.

  * bearer token, `hmac.compare_digest`, never `==`
  * ONE 401 response, byte-identical for every rejection — no token, malformed
    header, wrong token, and (because auth runs BEFORE routing) unknown scope
    and unknown ref too. An unauthenticated caller cannot use this endpoint to
    learn which scopes exist. An error that discriminates is an enumeration API.
  * the token is read from a FILE by default. Measured previously and recorded
    in memory: the agent exec sandbox strips env vars from agent-run commands,
    so `$SUBSYSTEM_STORE_TOKEN` is the fallback, never the primary.

NOT here, on purpose, and tracked to phase 1.5: rate-limiting / lockout on
repeated 401s, and separate read/write tokens. Both are (B-required) hardening
for the moment an IngressRoute exists; phase 1 creates none.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit

_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(_LIB))

import subsystem_recall as rc  # noqa: E402

# --- Constants that the tests pin LITERALLY -------------------------------------
#
# Every one of these is a contract with a caller, so the tests must not import
# them and assert `x == x`. They are spelled out again, by hand, in the test
# file (`claude/RULES.md`: never derive a test's expectation from the
# implementation it tests).

API_PREFIX = "/api/v1/"
HEALTH_PATH = "/healthz"

# 🔴 ONE body, for every rejection. Terse: no scope, no ref, no reason.
UNAUTHORIZED_BODY = b"unauthorized\n"
HEALTH_BODY = b"ok\n"

# The health endpoint says NOTHING (§2b): no version, no scope count, no store
# revision. It is unauthenticated, so anything it reveals is public.
SERVER_BANNER = "subsystem-store"

# 256 bits, base64url'd without padding, is 43 characters. A shorter token is
# refused at STARTUP rather than served: a store that came up with a weak token
# is worse than one that did not come up at all, because it looks healthy.
MIN_TOKEN_CHARS = 43

DEFAULT_STORE = "/data"
DEFAULT_TOKEN_FILE = "/run/secrets/subsystem-store/token"
DEFAULT_PORT = 8102

EXIT_CONFIG = 78  # sysexits.h EX_CONFIG — a misconfiguration, not a crash.


class _Rejected(Exception):
    """Auth said no. Carries nothing: the response body is a constant."""


def token_id(token: str) -> str:
    """A stable, non-reversible handle for the audit log.

    🔴 The log must be able to say WHICH token was used without ever holding the
    token. A truncated sha256 does that; the token itself in a log line is the
    leak the log exists to detect.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def load_token(token_file: str | None, env: dict[str, str]) -> str:
    """Resolve the bearer token. FILE FIRST, env only as a fallback.

    Guard order — each reachable by an input no earlier guard rejects:
      1. some source at all      -> "no token source"
      2. the file is readable    -> "token file unreadable"
      3. non-empty after strip   -> "token is empty"
      4. long enough             -> "token is too short"

    Guard 4 is reachable with a perfectly readable, non-empty file, which is the
    case that matters: a hand-typed token passes 1-3 and is exactly what 4 is for.
    """
    raw: str | None = None
    if token_file:
        path = Path(token_file)
        if not path.is_file():
            raise ValueError(f"token file unreadable: {path} is not a file")
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"token file unreadable: {path} ({exc})") from exc
    elif env.get("SUBSYSTEM_STORE_TOKEN"):
        raw = env["SUBSYSTEM_STORE_TOKEN"]
    else:
        raise ValueError(
            "no token source: pass --token-file, or set $SUBSYSTEM_STORE_TOKEN. "
            "The API is not served without one"
        )

    token = raw.strip()
    if not token:
        raise ValueError("token is empty: the source resolved to whitespace only")
    if len(token) < MIN_TOKEN_CHARS:
        raise ValueError(
            f"token is too short: {len(token)} chars, need >= {MIN_TOKEN_CHARS} "
            f"(256 bits base64url). A short token is a guessable one"
        )
    return token


def presented_token(header: str | None) -> str:
    """Pull the bearer credential out of an Authorization header.

    Returns "" for anything that is not a well-formed `Bearer <x>`, so the caller
    has one thing to compare and cannot accidentally branch on WHY it was absent.
    """
    if not header:
        return ""
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return ""
    return parts[1].strip()


def authorize(header: str | None, expected: str) -> None:
    """Constant-time bearer check. Raises `_Rejected`; never returns a reason.

    🔴 `hmac.compare_digest`, NOT `==`. A public endpoint makes a byte-at-a-time
    timing oracle practically exploitable, and the difference is invisible in
    every functional test — which is why the test for this asserts on the CALL,
    and there is a behavioural test either side of it.
    """
    got = presented_token(header)
    if not hmac.compare_digest(got.encode("utf-8"), expected.encode("utf-8")):
        raise _Rejected()


def scope_revision(store_root: str | Path, scope: str) -> str:
    """The scope's git HEAD, read from the filesystem — `git` is never spawned.

    §3 (Determinism): "have every response carry a `store-revision:` line (the
    scope's git HEAD)", so an agent can quote `scope@sha` and have it be
    checkable later. Reading `.git` directly keeps this module's no-subprocess,
    no-network property, which `subsystem_recall` documents as load-bearing for
    the `/resume` hot path.

    Returns "unknown" for every failure — an absent repo, a detached or
    unresolvable ref, an unreadable file. 🔴 "unknown" is honest; a fabricated
    sha would be quoted into a report and believed.
    """
    git = Path(store_root) / scope / ".git"
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"
    if not head.startswith("ref:"):
        return head if head else "unknown"
    ref = head.split(":", 1)[1].strip()
    try:
        return (git / ref).read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        pass
    try:
        packed = (git / "packed-refs").read_text(encoding="utf-8")
    except OSError:
        return "unknown"
    for line in packed.splitlines():
        if line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[1].strip() == ref:
            return parts[0]
    return "unknown"


def _int_param(params: dict[str, list[str]], name: str) -> int | None:
    """Parse an optional int query param, or raise ValueError with the param name.

    🔴 It raises rather than silently defaulting. A `?limit=abc` that quietly
    became the default is a caller believing a setting took effect — the same
    class `subsystem_recall.main` rejects `--limit` + `--list` for.
    """
    values = params.get(name)
    if not values:
        return None
    try:
        return int(values[-1])
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {values[-1]!r}") from None


def _float_param(params: dict[str, list[str]], name: str) -> float | None:
    values = params.get(name)
    if not values:
        return None
    try:
        return float(values[-1])
    except ValueError:
        raise ValueError(f"{name} must be a number, got {values[-1]!r}") from None


class StoreRequestHandler(BaseHTTPRequestHandler):
    """One GET router. Everything else is a 405."""

    protocol_version = "HTTP/1.1"
    # Suppress the default `BaseHTTP/x.y Python/3.n` banner. An unauthenticated
    # request should not be able to read the interpreter version off the wire.
    server_version = SERVER_BANNER
    sys_version = ""

    # Injected by `serve()`.
    store_root: str = DEFAULT_STORE
    expected_token: str = ""
    audit: Callable[[str], None] = staticmethod(lambda line: print(line, flush=True))

    # --- plumbing ---------------------------------------------------------------

    def version_string(self) -> str:
        # `server_version + " " + sys_version` would emit a trailing space and,
        # if `sys_version` were ever restored, the interpreter version. One
        # constant, no concatenation.
        return SERVER_BANNER

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D102
        # The audit line below is the record. The default access log would be a
        # second, differently-shaped one that nobody reads.
        return

    def _respond(
        self,
        code: int,
        body: bytes,
        *,
        content_type: str = "text/plain; charset=utf-8",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _unauthorized(self) -> None:
        """🔴 THE ONLY 401 IN THIS FILE, so every rejection is byte-identical."""
        self._respond(
            401,
            UNAUTHORIZED_BODY,
            headers={"WWW-Authenticate": 'Bearer realm="subsystem-store"'},
        )

    def _audit(self, path: str, result: int, status: str, authed: bool) -> None:
        self.audit(
            "store-api audit "
            f"ts={datetime.now(timezone.utc).isoformat(timespec='seconds')} "
            f"method={self.command} path={path} "
            f"token={token_id(self.expected_token) if authed else '-'} "
            f"auth={'ok' if authed else 'fail'} result={result} status={status}"
        )

    # --- methods ----------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        self._handle()

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle()

    def _reject_write(self) -> None:
        """🔴 PHASE 1 IS READ-ONLY, and that is enforced here, not documented.

        A write endpoint lands in phase 3 with its own append semantics (§2c).
        Until then a POST/PUT/PATCH/DELETE must not fall through to the GET
        router and be served as a read — a mutation that silently succeeded as a
        no-op read is indistinguishable from one that worked.
        """
        self._respond(
            405,
            b"read-only\n",
            headers={"Allow": "GET, HEAD"},
        )
        self._audit(urlsplit(self.path).path, 405, "method-not-allowed", authed=False)

    do_POST = do_PUT = do_PATCH = do_DELETE = _reject_write  # noqa: N815

    # --- the router -------------------------------------------------------------

    def _handle(self) -> None:
        split = urlsplit(self.path)
        path = unquote(split.path)

        # 🔴 BEFORE AUTH, and it is the ONLY thing before auth. Unauthenticated
        # by design (§2b) and it says nothing but "ok".
        if path == HEALTH_PATH:
            self._respond(200, HEALTH_BODY)
            return

        if not path.startswith(API_PREFIX):
            # Not an API path and not health. Answered with the SAME uniform 401
            # as a bad token: a 404 here would let an unauthenticated caller map
            # the URL space, which is the enumeration surface §2b forbids.
            self._unauthorized()
            self._audit(path, 401, "unauthorized", authed=False)
            return

        try:
            authorize(self.headers.get("Authorization"), self.expected_token)
        except _Rejected:
            self._unauthorized()
            self._audit(path, 401, "unauthorized", authed=False)
            return

        params = parse_qs(split.query, keep_blank_values=True)
        rest = path[len(API_PREFIX) :]
        parts = [p for p in rest.split("/") if p]

        try:
            if len(parts) == 2 and parts[0] == "recall":
                self._recall(parts[1], params)
                return
            if len(parts) == 2 and parts[0] == "search":
                self._search(parts[1], params)
                return
        except ValueError as exc:
            # A caller error, and the caller is authenticated, so it may be told
            # what it did wrong.
            body = f"bad request: {exc}\n".encode("utf-8")
            self._respond(400, body, headers={"X-Store-Status": "bad-request"})
            self._audit(path, 400, "bad-request", authed=True)
            return
        except (rc.StoreMissingError, rc.EntryUnreadableError) as exc:
            # 🔴 THE STATE THIS WHOLE DESIGN EXISTS TO KEEP SEPARATE. The store
            # was NOT read. Not a 200, not an empty digest, not "nothing recorded
            # yet" — a 503 that says so, carrying the reader's own sentence.
            body = f"{exc}\n".encode("utf-8")
            self._respond(
                503,
                body,
                headers={"X-Store-Status": "store-unreachable", "X-Store-Exit": "3"},
            )
            self._audit(path, 503, "store-unreachable", authed=True)
            return

        self._respond(404, b"no such endpoint\n", headers={"X-Store-Status": "no-route"})
        self._audit(path, 404, "no-route", authed=True)

    # --- handlers ---------------------------------------------------------------

    def _serve_report(
        self,
        path: str,
        scope: str,
        status: str,
        label: str,
        malformed: Any,
        text: str,
    ) -> None:
        # `_exit_for` is the CLI's OWN exit decision, reused rather than
        # re-derived: one rule, one place. It writes its stderr sentence into the
        # pod log, which is where a malformed-entry reject should be visible —
        # and it needs the REAL malformed tuple, or that sentence would count 0
        # rejects on the one status that exists to report them.
        code = rc._exit_for(status, label, malformed)
        body = (text + "\n").encode("utf-8")
        self._respond(
            200,
            body,
            headers={
                "X-Store-Status": status,
                "X-Store-Exit": str(code),
                "X-Store-Revision": scope_revision(self.store_root, scope),
            },
        )
        self._audit(path, 200, status, authed=True)

    def _recall(self, scope: str, params: dict[str, list[str]]) -> None:
        mode_values = params.get("mode")
        mode = mode_values[-1] if mode_values else rc.DEFAULT_MODE
        ref_values = params.get("ref")
        limit = _int_param(params, "limit")
        page = _int_param(params, "page")
        report = rc.recall(
            self.store_root,
            scope,
            ref=ref_values[-1] if ref_values else None,
            limit=limit if limit is not None else rc.DEFAULT_ENTRY_LIMIT,
            mode=mode,
            page=page if page is not None else 1,
        )
        self._serve_report(
            urlsplit(self.path).path,
            scope,
            report.status,
            f"{report.scope}/",
            report.malformed,
            rc.render_text(report),
        )

    def _search(self, scope: str, params: dict[str, list[str]]) -> None:
        query_values = params.get("q")
        if not query_values or not query_values[-1].strip():
            raise ValueError("q is required and must be non-empty")
        context = _int_param(params, "context")
        max_hits = _int_param(params, "max_hits")
        threshold = _float_param(params, "threshold")
        report = rc.search(
            self.store_root,
            scope,
            query_values[-1],
            context=context if context is not None else rc.CONTEXT_BULLET,
            threshold=(
                threshold if threshold is not None else rc.DEFAULT_SEARCH_THRESHOLD
            ),
            max_hits=max_hits if max_hits is not None else rc.DEFAULT_MAX_HITS,
            all_scopes=params.get("all_scopes", ["0"])[-1] not in ("0", "", "false"),
        )
        self._serve_report(
            urlsplit(self.path).path,
            scope,
            report.status,
            report.label,
            report.malformed,
            rc.render_search(report),
        )


def build_server(
    *,
    host: str,
    port: int,
    store_root: str,
    token: str,
    audit: Callable[[str], None] | None = None,
) -> ThreadingHTTPServer:
    """Wire a server without starting it — so a test can bind :0 and drive it."""

    class _Handler(StoreRequestHandler):
        pass

    _Handler.store_root = store_root
    _Handler.expected_token = token
    if audit is not None:
        _Handler.audit = staticmethod(audit)
    return ThreadingHTTPServer((host, port), _Handler)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="subsystem-store-api",
        description="Read-only HTTP layer over the subsystem store. Phase 1.",
    )
    p.add_argument("--store", default=os.environ.get("SUBSYSTEM_STORE_ROOT", DEFAULT_STORE))
    p.add_argument("--host", default=os.environ.get("SUBSYSTEM_STORE_HOST", "0.0.0.0"))
    p.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("SUBSYSTEM_STORE_PORT", DEFAULT_PORT)),
    )
    p.add_argument(
        "--token-file",
        default=os.environ.get("SUBSYSTEM_STORE_TOKEN_FILE", DEFAULT_TOKEN_FILE),
        help=(
            "file holding the bearer token (mode 0600). FILE FIRST: the agent exec "
            "sandbox strips env vars, so $SUBSYSTEM_STORE_TOKEN is the fallback"
        ),
    )
    args = p.parse_args(argv)

    token_file = args.token_file
    if token_file and not Path(token_file).is_file() and os.environ.get(
        "SUBSYSTEM_STORE_TOKEN"
    ):
        # The default path does not exist and an env token does: use it, and SAY
        # SO. Falling back silently is how a deployment that lost its secret mount
        # keeps serving on a token nobody meant to be authoritative.
        print(
            f"subsystem-store-api: token file {token_file} absent; "
            f"falling back to $SUBSYSTEM_STORE_TOKEN",
            file=sys.stderr,
        )
        token_file = None

    try:
        token = load_token(token_file, dict(os.environ))
    except ValueError as exc:
        print(f"subsystem-store-api: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    httpd = build_server(
        host=args.host, port=args.port, store_root=args.store, token=token
    )
    print(
        f"subsystem-store-api: listening on {args.host}:{args.port} "
        f"store={args.store} token-id={token_id(token)}",
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
