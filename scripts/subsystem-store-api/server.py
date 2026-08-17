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

PHASE 1.5 — THE (B-REQUIRED) HARDENING, ADDED BEFORE ANY INGRESS EXISTS
------------------------------------------------------------------------
Exposure **B** was decided by the operator: public `store.zacx.dev`, Cloudflare-
proxied, and `/api/*` carries NO edge auth because Authelia's 302 is unusable
from a CLI. So the bearer token is the ONLY thing between the public internet
and client-confidential content, and §2b's hardening stops being optional:

  * **A token SET, not a token** (`load_tokens`). Rotation is by overlap —
    current + previous both accepted — because a single-token rotation has a
    window in which every client is broken, which is why nobody performs it.
  * **The audit log names WHICH fingerprint matched** (`token=<12 hex>`). This
    is the whole safety of overlap rotation: without it, "the old token is
    unused now" is a guess, and removing it is a coin flip. Fingerprint only —
    the token itself in a log line is the leak the log exists to detect.
  * **The client is keyed on `CF-Connecting-IP`** (`client_ip`), which is
    trustworthy ONLY because Cloudflare is the sole public ingress. 🔴
    `X-Forwarded-For` is NEVER read: it is caller-supplied, so keying on it
    lets one attacker rotate through a million buckets AND lets them lock out
    a third party by forging theirs. Behind CF + Traefik the TCP peer address
    is the gateway's for everybody, so keying on THAT is the mirror failure —
    one abuser locks out the world. An absent or unparseable `CF-Connecting-IP`
    therefore FAILS CLOSED (uniform 401), rather than being bucketed into a
    shared "unknown" that reproduces exactly that hazard.
  * **Rate limit + lockout in the app** (`RateLimiter`): 5 failed auths per
    client IP per minute, then a 15-minute lockout. Defaults live here in code;
    all three are tunable by env. Cloudflare's WAF and the Traefik middleware
    are the outer two layers, not the only ones.

Still NOT here, and still tracked forward: separate read/write tokens (there is
no write path until phase 3, so a write-scoped token would today be a label on
a capability that does not exist).
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Sequence
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
# Generate one with `python3 -c 'import secrets; print(secrets.token_urlsafe(43))'`
# — 43 BYTES of entropy renders as 58 characters, comfortably over this floor.
MIN_TOKEN_CHARS = 43

# Overlap rotation needs two (current + previous); three covers a rotation that
# overlaps another. Beyond that a "set" is an accumulation nobody has retired,
# and every one of them is a live credential — so the file is refused rather
# than served, at STARTUP, for the same reason a short token is.
MAX_TOKENS = 4

# 🔴 THE ONLY HEADER THIS SERVER WILL ACCEPT AS A CLIENT IDENTITY, and it is
# trustworthy for exactly one reason: Cloudflare is the sole public ingress and
# overwrites it on every request it proxies. `X-Forwarded-For` is deliberately
# absent from this file — see the module docstring.
CLIENT_IP_HEADER = "CF-Connecting-IP"

# §2b: "Rate-limit + lock out on repeated 401s". The defaults are HERE, in code,
# so a deployment that sets no env still gets them; each is overridable.
DEFAULT_MAX_FAILURES = 5
DEFAULT_FAILURE_WINDOW_S = 60.0
DEFAULT_LOCKOUT_S = 900.0

ENV_MAX_FAILURES = "SUBSYSTEM_STORE_MAX_FAILURES"
ENV_FAILURE_WINDOW = "SUBSYSTEM_STORE_FAILURE_WINDOW_S"
ENV_LOCKOUT = "SUBSYSTEM_STORE_LOCKOUT_S"

# A bound on the failure table so a flood of distinct keys cannot grow it
# without limit. Active lockouts are NEVER evicted — evicting one is a bypass.
MAX_TRACKED_CLIENTS = 4096

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


def load_tokens(token_file: str | None, env: dict[str, str]) -> list[str]:
    """Resolve the bearer token SET. FILE FIRST, env only as a fallback.

    🔴 A SET, NOT A TOKEN, and that is the whole of rotation (§2b: "token
    rotation must be a one-command operation"). One token per line — the CURRENT
    one first, the PREVIOUS one below it. Rotation is then: add the new line,
    watch the audit log until every client's `token=` fingerprint has moved,
    then delete the old line. There is no window in which a client is broken,
    which is the reason single-token rotations never actually get performed.

    Guard order — each reachable by an input no earlier guard rejects:
      1. some source at all      -> "no token source"
      2. the file is readable    -> "token file unreadable"
      3. at least one token      -> "token is empty"
      4. not an accumulation     -> "too many tokens"
      5. every token long enough -> "token N of M is too short"

    Guard 5 names the POSITION, never the token. A file whose second line was
    truncated by an editor passes 1-4 and is exactly what 5 is for; saying
    "one of them is short" would leave the operator grepping a secret by hand.
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

    # `.split()` on any whitespace: a base64url token contains none, so this
    # accepts one-per-line (the documented shape) and survives a trailing
    # newline, CRLF, or an operator who used spaces.
    tokens: list[str] = []
    for candidate in raw.split():
        if candidate not in tokens:  # de-duplicated, ORDER PRESERVED
            tokens.append(candidate)

    if not tokens:
        raise ValueError("token is empty: the source resolved to whitespace only")
    if len(tokens) > MAX_TOKENS:
        raise ValueError(
            f"too many tokens: {len(tokens)}, max {MAX_TOKENS}. Every line is a "
            f"live credential; retire the old ones instead of accumulating them"
        )
    for index, token in enumerate(tokens, start=1):
        if len(token) < MIN_TOKEN_CHARS:
            raise ValueError(
                f"token {index} of {len(tokens)} is too short: {len(token)} chars, "
                f"need >= {MIN_TOKEN_CHARS} (256 bits base64url). A short token is "
                f"a guessable one"
            )
    return tokens


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


def authorize(header: str | None, expected: Sequence[str]) -> str:
    """Constant-time bearer check against a token SET. Returns the fingerprint
    of the token that matched; raises `_Rejected` and never returns a reason.

    🔴 `hmac.compare_digest`, NOT `==`. A public endpoint makes a byte-at-a-time
    timing oracle practically exploitable, and the difference is invisible in
    every functional test — which is why the test for this asserts on the CALL,
    and there is a behavioural test either side of it.

    🔴 NO EARLY EXIT. The loop runs to the end whether or not it has already
    matched, so the response time does not encode WHICH token was presented —
    a `break` on the first hit would make "you used the old one" measurable from
    outside, and during an overlap window that is precisely the fact an attacker
    wants. It is also why the match is accumulated rather than returned inline.

    🔴 A BARE STRING IS REFUSED, LOUDLY. `for token in "abc…"` iterates
    CHARACTERS, so passing one token as a `str` would authorize any caller who
    presented a single character of it. A type annotation is not a code path;
    this check is.
    """
    if isinstance(expected, (str, bytes)):
        raise TypeError(
            "expected must be a SEQUENCE of tokens, not one string: iterating a "
            "str yields characters, and a one-character token would authorize"
        )
    got = presented_token(header).encode("utf-8")
    matched: str | None = None
    for token in expected:
        if hmac.compare_digest(got, token.encode("utf-8")):
            matched = token
    if matched is None:
        raise _Rejected()
    return token_id(matched)


def client_ip(headers: Any) -> str | None:
    """The client's address, or `None` — and `None` means REFUSE, not "unknown".

    🔴 `CF-Connecting-IP` ONLY. Cloudflare sets it on every proxied request and
    overwrites whatever the caller sent, which is what makes it trustworthy —
    and it is trustworthy for that reason alone, so the moment Cloudflare stops
    being the sole ingress this function stops being sound. `X-Forwarded-For` is
    never consulted: it is caller-supplied, so an attacker keyed on it gets a
    fresh bucket per request AND can lock out a third party by forging theirs.

    Three ways to get `None`, all of them fail-closed at the call site:
      * absent            — not a Cloudflare-proxied request
      * not an IP address — a forged or mangled value
      * more than one     — a caller trying to smuggle a second value past a
                            proxy that appends rather than overwrites

    Returns the NORMALISED form (`ipaddress`), so an IPv4-mapped address written
    in upper and lower case cannot become two buckets for one attacker. (The
    example that belongs here is spelled out in the test rather than in this
    docstring: `test_no_public_ips.py` rejects an IP literal in a PUBLIC repo,
    and it is right to — see `TestClientIpIsCloudflareOnly::
    test_the_address_is_NORMALISED_so_one_caller_is_one_bucket`, which uses the
    RFC 5737 documentation range.)
    """
    try:
        values = headers.get_all(CLIENT_IP_HEADER)
    except AttributeError:  # a plain dict, in a unit test
        single = headers.get(CLIENT_IP_HEADER)
        values = None if single is None else [single]
    if not values or len(values) != 1:
        return None
    try:
        return str(ipaddress.ip_address(values[0].strip()))
    except ValueError:
        return None


def limiter_settings(env: dict[str, str]) -> tuple[int, float, float]:
    """Read the three rate-limit knobs from env, or RAISE.

    🔴 It raises rather than silently defaulting, for the same reason
    `_int_param` does: a typo'd `SUBSYSTEM_STORE_MAX_FAILURES=fve` that quietly
    became 5 is an operator believing a setting took effect. A misconfiguration
    at startup is `EXIT_CONFIG`, visible in a CrashLoopBackOff; a
    misconfiguration that defaults is invisible forever.
    """

    def _num(name: str, default: float, cast: Callable[[str], Any]) -> Any:
        raw = env.get(name)
        if raw is None or raw == "":
            return default
        try:
            value = cast(raw)
        except ValueError:
            raise ValueError(f"{name} must be a number, got {raw!r}") from None
        if value <= 0:
            raise ValueError(f"{name} must be positive, got {raw!r}")
        return value

    return (
        _num(ENV_MAX_FAILURES, DEFAULT_MAX_FAILURES, int),
        _num(ENV_FAILURE_WINDOW, DEFAULT_FAILURE_WINDOW_S, float),
        _num(ENV_LOCKOUT, DEFAULT_LOCKOUT_S, float),
    )


class RateLimiter:
    """N failed auths per client per window -> a lockout of `lockout_s`.

    §2b (B-required): "Rate-limit + lock out on repeated 401s, at the Traefik
    middleware layer *and* in the app. Cloudflare's WAF is the third layer, not
    the only one." This is the innermost of the three, and the only one that
    knows an auth actually FAILED rather than that a request arrived.

    Thread-safe on purpose: the server is a `ThreadingHTTPServer`, so a
    dict-mutating limiter without a lock would drop failures under exactly the
    concurrency a credential-stuffing run produces.

    The clock is injectable so a test can prove the window and the lockout
    EXPIRE, rather than sleeping 15 minutes or asserting only the easy half.
    """

    def __init__(
        self,
        *,
        max_failures: int = DEFAULT_MAX_FAILURES,
        window_s: float = DEFAULT_FAILURE_WINDOW_S,
        lockout_s: float = DEFAULT_LOCKOUT_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_failures = max_failures
        self.window_s = window_s
        self.lockout_s = lockout_s
        self.clock = clock
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def locked_out(self, key: str) -> bool:
        """True while `key` is serving a lockout. Expired lockouts are dropped."""
        now = self.clock()
        with self._lock:
            until = self._locked_until.get(key)
            if until is None:
                return False
            if until <= now:
                del self._locked_until[key]
                self._failures.pop(key, None)
                return False
            return True

    def record_failure(self, key: str) -> bool:
        """Count one failed auth. Returns True iff THIS one started a lockout."""
        now = self.clock()
        with self._lock:
            recent = [t for t in self._failures.get(key, []) if t > now - self.window_s]
            recent.append(now)
            if len(recent) >= self.max_failures:
                self._locked_until[key] = now + self.lockout_s
                self._failures.pop(key, None)
                self._evict(now)
                return True
            self._failures[key] = recent
            self._evict(now)
            return False

    def record_success(self, key: str) -> None:
        """A good credential clears the failure streak — but NOT a live lockout.

        🔴 The asymmetry is the point. Forgiving a streak keeps a legitimate
        client from being locked out by sporadic typos across an hour; forgiving
        a LOCKOUT would mean an attacker who eventually guessed one token could
        wipe the record of having guessed, and `locked_out` is checked before
        this is ever reached anyway.
        """
        with self._lock:
            self._failures.pop(key, None)

    def _evict(self, now: float) -> None:
        """Bound the failure table. Called under `self._lock`.

        Only entries whose whole streak has aged out of the window are dropped,
        and ACTIVE LOCKOUTS ARE NEVER TOUCHED — evicting a lockout is a bypass
        dressed as memory hygiene.
        """
        for key in [k for k, v in self._locked_until.items() if v <= now]:
            del self._locked_until[key]
        if len(self._failures) <= MAX_TRACKED_CLIENTS:
            return
        stale = [
            key
            for key, times in self._failures.items()
            if not times or max(times) <= now - self.window_s
        ]
        for key in stale:
            del self._failures[key]


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

    # Injected by `build_server`.
    store_root: str = DEFAULT_STORE
    expected_tokens: tuple[str, ...] = ()
    limiter: RateLimiter | None = None
    audit: Callable[[str], None] = staticmethod(lambda line: print(line, flush=True))

    # Per-request identity, reset at the top of every dispatch so a keep-alive
    # connection cannot carry the previous request's fingerprint into this
    # request's audit line.
    _client_ip: str | None = None
    _token_fp: str | None = None

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

    def _audit(self, path: str, result: int, status: str) -> None:
        """One line per `/api/*` request. `/healthz` is deliberately not audited.

        🔴 `token=` is the fingerprint of the token that ACTUALLY MATCHED, not
        of the one the server was configured with. With a token SET those differ,
        and the difference is the entire safety of overlap rotation: it is the
        only evidence that every client has moved off the old credential before
        it is deleted. Nothing here is derived from `authed` any more — the
        fingerprint's presence IS the authentication result, so the two cannot
        disagree.
        """
        self.audit(
            "store-api audit "
            f"ts={datetime.now(timezone.utc).isoformat(timespec='seconds')} "
            f"ip={self._client_ip or '-'} "
            f"method={self.command} path={path} "
            f"token={self._token_fp or '-'} "
            f"auth={'ok' if self._token_fp else 'fail'} "
            f"result={result} status={status}"
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
        self._client_ip = client_ip(self.headers)
        self._token_fp = None
        self._respond(
            405,
            b"read-only\n",
            headers={"Allow": "GET, HEAD"},
        )
        self._audit(urlsplit(self.path).path, 405, "method-not-allowed")

    do_POST = do_PUT = do_PATCH = do_DELETE = _reject_write  # noqa: N815

    # --- the router -------------------------------------------------------------

    @staticmethod
    def _count_failure(limiter: RateLimiter | None, ip: str) -> str:
        """Record one failed auth and name the audit status it produced."""
        if limiter is None:
            return "unauthorized"
        return "lockout-triggered" if limiter.record_failure(ip) else "unauthorized"

    def _refuse(self, path: str, status: str) -> None:
        """The uniform 401, plus the audit line that says which reason it was.

        🔴 THE WIRE DOES NOT DISCRIMINATE; THE LOG DOES. Every rejection below —
        no client IP, locked out, not an API path, bad token — is the same code,
        the same body and the same header set, because an error that
        discriminates is an enumeration API (§2b). The `status=` field exists so
        the operator can still tell a credential-stuffing run from a
        misconfigured client, in a place the attacker cannot read.
        """
        self._unauthorized()
        self._audit(path, 401, status)

    def _handle(self) -> None:
        split = urlsplit(self.path)
        path = unquote(split.path)
        self._client_ip = None
        self._token_fp = None

        # 🔴 BEFORE EVERYTHING, and it is the ONLY thing before auth.
        # Unauthenticated by design (§2b) and it says nothing but "ok". It is
        # also the kubelet's probe, so it must not be rate-limited or require a
        # `CF-Connecting-IP` the kubelet has no reason to send.
        if path == HEALTH_PATH:
            self._respond(200, HEALTH_BODY)
            return

        ip = client_ip(self.headers)
        if ip is None:
            # 🔴 FAIL CLOSED. The alternative — bucketing every unidentified
            # request under one shared key — is the failure the whole
            # `CF-Connecting-IP` design exists to avoid: one abuser would then
            # lock out everybody. Nothing is counted here, precisely because
            # there is no bucket to count into.
            self._refuse(path, "no-client-ip")
            return
        self._client_ip = ip

        limiter = self.limiter
        if limiter is not None and limiter.locked_out(ip):
            # Checked BEFORE the token: a lockout that a valid credential could
            # walk through would not be a lockout, and an attacker who guesses
            # one token mid-run must not have the record wiped.
            self._refuse(path, "locked-out")
            return

        if not path.startswith(API_PREFIX):
            # Not an API path and not health. Answered with the SAME uniform 401
            # as a bad token: a 404 here would let an unauthenticated caller map
            # the URL space, which is the enumeration surface §2b forbids. It
            # counts toward the lockout for the same reason — URL probing from
            # one address is the same attack as token probing from one address.
            self._refuse(path, self._count_failure(limiter, ip))
            return

        try:
            self._token_fp = authorize(
                self.headers.get("Authorization"), self.expected_tokens
            )
        except _Rejected:
            self._refuse(path, self._count_failure(limiter, ip))
            return
        if limiter is not None:
            limiter.record_success(ip)

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
            self._audit(path, 400, "bad-request")
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
            self._audit(path, 503, "store-unreachable")
            return

        self._respond(404, b"no such endpoint\n", headers={"X-Store-Status": "no-route"})
        self._audit(path, 404, "no-route")

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
        self._audit(path, 200, status)

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
    tokens: Sequence[str],
    limiter: RateLimiter | None = None,
    audit: Callable[[str], None] | None = None,
) -> ThreadingHTTPServer:
    """Wire a server without starting it — so a test can bind :0 and drive it.

    🔴 `tokens` is a SEQUENCE, and a bare `str` is refused here as well as in
    `authorize`. The keyword was renamed from `token=` on purpose: a caller
    still passing the old name now fails loudly at the call, instead of silently
    configuring the empty default set and rejecting every request — or, worse,
    being iterated character-by-character somewhere downstream.
    """
    if isinstance(tokens, (str, bytes)):
        raise TypeError("tokens must be a SEQUENCE of tokens, not one string")

    class _Handler(StoreRequestHandler):
        pass

    _Handler.store_root = store_root
    _Handler.expected_tokens = tuple(tokens)
    _Handler.limiter = limiter if limiter is not None else RateLimiter()
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
            "file holding the bearer token SET, ONE PER LINE, current first "
            "(mode 0600). FILE FIRST: the agent exec sandbox strips env vars, so "
            "$SUBSYSTEM_STORE_TOKEN is the fallback"
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
        tokens = load_tokens(token_file, dict(os.environ))
        max_failures, window_s, lockout_s = limiter_settings(dict(os.environ))
    except ValueError as exc:
        print(f"subsystem-store-api: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    limiter = RateLimiter(
        max_failures=max_failures, window_s=window_s, lockout_s=lockout_s
    )
    httpd = build_server(
        host=args.host,
        port=args.port,
        store_root=args.store,
        tokens=tokens,
        limiter=limiter,
    )
    # 🔴 The startup line prints every FINGERPRINT, in the order the file lists
    # them, and never a token. It is what makes an overlap rotation checkable:
    # the operator reads these ids, then greps the audit log for the one that
    # should have stopped appearing before deleting its line from the secret.
    print(
        f"subsystem-store-api: listening on {args.host}:{args.port} "
        f"store={args.store} token-ids={','.join(token_id(t) for t in tokens)} "
        f"lockout={max_failures}/{window_s:g}s->{lockout_s:g}s",
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
