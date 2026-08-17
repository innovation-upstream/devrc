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
  * **…and the header is only READ when the TCP PEER is a trusted proxy**
    (`load_trusted_proxies` / `peer_is_trusted`) — see the next section, which
    is the defect that made the rest of this list forgeable.

PHASE 1.5b — `CF-Connecting-IP` WAS TRUSTED FROM ANY PEER
----------------------------------------------------------
🔴 THE HEADER ABOVE IS CALLER-SUPPLIED DATA. Everything the previous section
claims about it — "Cloudflare overwrites it on every request it proxies" — is
true of requests that ARRIVE FROM CLOUDFLARE, and says nothing about a request
that did not. The first version read it from whoever connected, so ANY peer that
could address the pod on 8102 (a pod in the cluster, a `kubectl port-forward`, a
second IngressRoute) could send a header naming a THIRD PARTY and five bad
tokens, and that third party was locked out for fifteen minutes — seeing a 401
indistinguishable from a wrong credential. Measured against the deployed pod
before this fix: five forged requests, then the victim's own valid request.

Fixing it in the network layer alone was rejected: a NetworkPolicy is a
different repo, a different review, and cannot be exercised without a cluster.
The rule this file lives by is that a guard you cannot watch fail is not a
guard. So the primary fix is HERE, and it is hermetic:

  * `SUBSYSTEM_STORE_TRUSTED_PROXIES` — an explicit allowlist of peer addresses
    or CIDRs. **REQUIRED**: no default, and the process refuses to start
    without it (`EXIT_CONFIG`), for the same reason a short token does. A
    default would be a guess about somebody's cluster, and a guess that is
    wrong in the permissive direction is exactly the defect.
  * `resolve_client` is the whole rule, and it is the standard reverse-proxy
    one: **trusted peer -> the header is the client; untrusted peer -> the TCP
    PEER is the client and the header is not read at all.** The property that
    matters is "a forged header must never name a THIRD PARTY", and bucketing
    the forger under its own address satisfies it exactly — such a caller can
    only lock out itself.
  * 🔴 An untrusted peer is NOT REFUSED, and an earlier version of this branch
    got that wrong. Refusing is stricter than the property needs and it broke
    the phase-1 acceptance procedure outright: `kubectl port-forward` presents
    peer `127.0.0.1`, the pod allowlists the node's Cilium internal address, so
    every byte-identity run became a 401 — the phase-1 criterion, with no
    documented way left to run it. It also turned one wrong address in one env
    var into a total outage that `/healthz` hid. **Distrust is expressed by
    ignoring what the caller claims, not by hanging up on them.**
  * The audit line carries `peer=trusted|untrusted` as its OWN field, so
    direct-to-pod access stays greppable without being spelled as an
    authentication failure. `status=untrusted-peer` + `auth=fail` — the earlier
    shape — put every port-forward into the Loki auth-fail alert.
  * `/healthz` is answered BEFORE any of this, so the kubelet probe — which
    comes from the node and carries no `CF-Connecting-IP` — is untouched. A
    readiness probe broken by a security guard is how the guard gets deleted.

⚠ WHAT THIS CANNOT DO. The pod sees the address of whatever last hop connected
to it — in this deployment the in-cluster gateway, never Cloudflare's own
address. So this proves "the request came through the gateway", NOT "the
request came through Cloudflare". Anything that can occupy that hop can still
forge the header. Narrowing WHO can occupy it is the NetworkPolicy's job, and
it is the second layer, not this one.
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
import math
import os
import re
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

# 🔴 …AND IT IS ONLY READ FROM THESE PEERS. Caller-supplied data is only as
# trustworthy as the hop that overwrote it, so the header is honoured only when
# the TCP peer is one of the proxies the operator named. No default value: see
# `load_trusted_proxies`.
ENV_TRUSTED_PROXIES = "SUBSYSTEM_STORE_TRUSTED_PROXIES"

# 🔴 A FLOOR ON HOW WIDE ONE ENTRY MAY BE, keyed by address family.
#
# Refusing only `/0` checks ONE ENTRY IN ISOLATION and is walkable two ways, both
# measured. (a) The two halves of the address space, each written as a `/1`,
# parse clean and together trust every IPv4 peer — no single entry is a default
# route, so a per-entry `/0` check sees nothing wrong. (b) The realistic one: a
# pod CIDR like `10.244.0.0/16` is accepted and hands the client identity to
# EVERY POD IN THE CLUSTER, which is verbatim the attacker in this module's own
# threat model.
#
# (The upper half is deliberately not SPELLED anywhere in this repo — it is
# routable space, and `scripts/tests/test_no_public_ips.py` refuses IP literals
# in a PUBLIC repo. It caught the first draft of this very comment. The test
# that exercises case (a) builds the address arithmetically for the same reason.)
#
# The audit that found it suggested evaluating the UNION of the entries. A floor
# is the stronger rule and subsumes it: with /24 as the minimum, no set of
# entries an operator would plausibly type can cover the space, and the check
# stays local to one entry so the error can NAME the offending one. Two guards
# remain rather than one because their diagnostics differ — `/0` is "you
# disabled it", a wide prefix is "you meant a smaller range".
#
# The numbers: a /24 is 256 addresses, generous for a proxy tier; a v6 /64 is one
# LAN segment, which is the smallest unit an operator is given. An operator who
# genuinely needs wider lists several entries, which is a deliberate act rather
# than a typo.
MIN_TRUSTED_PREFIX = {4: 24, 6: 64}

# §2b: "Rate-limit + lock out on repeated 401s". The defaults are HERE, in code,
# so a deployment that sets no env still gets them; each is overridable.
DEFAULT_MAX_FAILURES = 5
DEFAULT_FAILURE_WINDOW_S = 60.0
DEFAULT_LOCKOUT_S = 900.0

ENV_MAX_FAILURES = "SUBSYSTEM_STORE_MAX_FAILURES"
ENV_FAILURE_WINDOW = "SUBSYSTEM_STORE_FAILURE_WINDOW_S"
ENV_LOCKOUT = "SUBSYSTEM_STORE_LOCKOUT_S"

# 🔴 REAL bounds on both tables, enforced in `RateLimiter._evict` — see the
# note there for why the earlier version was a bound in name only. Active
# lockouts are NEVER evicted for space; the lockout table is instead capped, and
# at the cap new lockouts are refused rather than old ones released.
MAX_TRACKED_CLIENTS = 4096
MAX_TRACKED_LOCKOUTS = 16384

# How much of an unwanted request body this server will read and throw away
# before giving up and closing the connection instead. There is no endpoint that
# accepts a body at all, so this exists only so that a small one does not
# desynchronise a keep-alive connection (see `_drain_body`).
MAX_DRAIN_BYTES = 1 << 20

# …and how long it will spend doing so. A byte limit alone is not a bound: a
# caller dripping one byte at a time satisfies it while holding a thread forever.
DRAIN_DEADLINE_S = 10.0

# A scope name, as it may appear in a URL path.
#
# 🔴 NO DOT. That makes traversal impossible BY CONSTRUCTION rather than by
# excluding `.` and `..` by name — a structural guard instead of a spelled one,
# which is the difference between "the two spellings I thought of are blocked"
# and "the character that enables them cannot appear".
#
# The first draft DID permit dots (on the reasoning that refs contain them) and
# excluded `..` by name beside it. A mutation sweep then removed the dot from
# this class and the ENTIRE SUITE STAYED GREEN — no test had a dotted path
# component at all, because refs travel in the QUERY STRING, not the path. So
# the permissive class was never justified by a real caller.
#
# MEASURED before tightening, since this could break a real scope: all 8 scopes
# in the live store match `[A-Za-z0-9_-]+`, and 0 contain a dot. (Counts only —
# the names are client-confidential and this repo is PUBLIC.)
SAFE_PATH_COMPONENT = re.compile(r"[A-Za-z0-9_-]+")

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


def sole_header(headers: Any, name: str) -> str | None:
    """The value of `name` iff it appears EXACTLY ONCE. Otherwise `None`.

    🔴 ONE RULE, ONE PLACE. This predicate existed twice, open-coded, and was
    correct at one site and wrong at the other — which is the shape a duplicated
    predicate always takes. `client_ip` rejected a duplicated `CF-Connecting-IP`
    ("a caller trying to smuggle a second value past a proxy that appends rather
    than overwrites"); twenty lines away `_drain_body` used a bare
    `headers.get("Content-Length")`, which silently takes the FIRST value.

    A final audit walked that with a working smuggle: `Content-Length: 0`
    followed by `Content-Length: 154` on a request that answers 200 — so the
    connection legitimately stays open, nothing is drained, and the body is
    served as the next request with store content in the response. The
    `_drain_body` comment claiming "any framing this function does not fully
    understand ends the connection" was false while a third framing walked it.

    `headers.get_all` returns None for absent, a list otherwise.
    """
    try:
        values = headers.get_all(name)
    except AttributeError:  # a plain dict, in a unit test
        single = headers.get(name)
        values = None if single is None else [single]
    if not values or len(values) != 1:
        return None
    return values[0]


def client_ip(headers: Any) -> str | None:
    """The client's address, or `None` — and `None` means REFUSE, not "unknown".

    🔴 THIS FUNCTION IS ONLY CALLED FOR A TRUSTED PEER. It parses a header,
    which is caller-supplied bytes; the thing that makes those bytes an identity
    is `peer_is_trusted`, and `resolve_client` is the ONE place that asks. For
    an untrusted peer this function is not called at all — not called and its
    answer discarded, but never reached — so a forged `CF-Connecting-IP` cannot
    become a bucket key by any path. There is exactly one call site, and that is
    the guard.

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
    raw = sole_header(headers, CLIENT_IP_HEADER)
    if raw is None:
        return None
    try:
        address = ipaddress.ip_address(raw.strip())
    except ValueError:
        return None
    return str(rate_limit_key(address))


def trusted_network(item: Any) -> Any:
    """Parse ONE allowlist entry into a network, or RAISE.

    🔴 ONE RULE, ONE PLACE. Both `load_trusted_proxies` (the env string) and
    `build_server` (a caller's sequence) need exactly this parse and exactly
    this refusal, and a predicate open-coded at two call sites is wrong at one
    of them — which is how `sole_header` came to exist twenty lines from a bare
    `.get()`. A `/0` reaching the server through the programmatic door would be
    just as total as one reaching it through the env door.
    """
    if isinstance(item, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
        network = item
    else:
        try:
            # `strict=False` so `10.1.2.3/24` is accepted as the /24 it names,
            # rather than refused for having host bits set — an operator writing
            # a CIDR from memory means the network, and refusing here would push
            # them towards the /0 the next guard exists to stop.
            network = ipaddress.ip_network(str(item), strict=False)
        except ValueError as exc:
            raise ValueError(
                f"{ENV_TRUSTED_PROXIES}: {item!r} is not an IP address or CIDR ({exc})"
            ) from None
    if network.prefixlen == 0:
        raise ValueError(
            f"{ENV_TRUSTED_PROXIES}: {item!r} trusts every peer, which is the "
            f"defect this setting exists to close. Name the proxy's address"
        )
    floor = MIN_TRUSTED_PREFIX[network.version]
    if network.prefixlen < floor:
        raise ValueError(
            f"{ENV_TRUSTED_PROXIES}: {item!r} is too broad — /{network.prefixlen} "
            f"covers {network.num_addresses} peers; the floor is /{floor}. A "
            f"trusted proxy is a host or a small tier, and a wide range here "
            f"hands the client identity to everything inside it. List the "
            f"individual addresses, or several narrower CIDRs"
        )
    return network


def load_trusted_proxies(env: dict[str, str]) -> tuple[Any, ...]:
    """Resolve the peer allowlist that makes `CF-Connecting-IP` readable. OR RAISE.

    🔴 THERE IS NO DEFAULT, AND THAT IS THE POINT. A default would be a guess
    about somebody else's network, and the only guess that keeps every
    deployment working is a permissive one — which is the defect this function
    exists to close, shipped as a constant. So an unset or empty variable is a
    misconfiguration and exits `EXIT_CONFIG` at startup, visible in a
    CrashLoopBackOff, exactly like a token file that is missing.

    Accepts addresses and CIDRs, comma- or whitespace-separated:

        SUBSYSTEM_STORE_TRUSTED_PROXIES=10.0.0.1,10.1.0.0/24

    Guard order — each reachable by an input no earlier guard rejects:
      1. the variable is set and non-blank -> "no trusted proxies"
      2. every item parses as an address/CIDR -> "not an IP address or CIDR"
      3. no item is a DEFAULT ROUTE -> "trusts every peer"

    🔴 Guard 3 is the one that matters. `0.0.0.0/0` (or `::/0`) is "trust
    anybody who can reach me" spelled as configuration — the pre-fix behaviour,
    reachable by an operator who wanted to silence a 401 during a rollout and
    never took it out. Requiring the variable to be SET would not catch it;
    only refusing the value does. A `/0` is refused BY PREFIX LENGTH, not by
    matching the two spellings, so `0.0.0.0/0`, `::/0` and any future
    equivalent are one rule rather than a list somebody has to extend.
    """
    raw = env.get(ENV_TRUSTED_PROXIES)
    if raw is None or not raw.strip():
        raise ValueError(
            f"no trusted proxies: set ${ENV_TRUSTED_PROXIES} to the address(es) "
            f"or CIDR(s) of the proxy that terminates public traffic. The "
            f"{CLIENT_IP_HEADER} header is only read from those peers, and there "
            f"is deliberately no default"
        )
    networks = [
        trusted_network(item) for item in re.split(r"[,\s]+", raw.strip()) if item
    ]
    if not networks:
        raise ValueError(
            f"no trusted proxies: ${ENV_TRUSTED_PROXIES} resolved to no entries"
        )
    return tuple(networks)


def peer_address(client_address: Any) -> Any:
    """The TCP peer, normalised, or `None` — and `None` means REFUSE.

    🔴 IPv4-MAPPED IS UNWRAPPED, because a dual-stack listener reports a v4
    caller as `::ffff:10.0.0.1` and an allowlist written as `10.0.0.1/32` would
    then never match — a fail-CLOSED break rather than a hole, but a break that
    reads as "the guard is wrong" and gets widened until it is.

    🔴 IT DOES NOT GO THROUGH `rate_limit_key`, and must not. That function
    aggregates IPv6 to its **/64** on purpose, because an attacker picks freely
    inside their own allocation — the exact reasoning that makes it WRONG here:
    a /64 of trusted proxies is 2**64 peers the operator did not name. Two
    functions that both normalise an address, deliberately differently.
    """
    if not isinstance(client_address, (tuple, list)) or not client_address:
        return None
    raw = client_address[0]
    if not isinstance(raw, str):
        return None
    try:
        # An IPv6 link-local peer carries a scope id (`fe80::1%eth0`) that
        # `ip_address` will not parse. The zone is a local interface name, not
        # part of the identity being allowlisted.
        address = ipaddress.ip_address(raw.split("%", 1)[0].strip())
    except ValueError:
        return None
    mapped = getattr(address, "ipv4_mapped", None)
    return mapped if mapped is not None else address


def peer_is_trusted(peer: Any, trusted: Sequence[Any]) -> bool:
    """Is this peer one of the proxies whose `CF-Connecting-IP` we will read?

    🔴 A BARE STRING IS REFUSED, LOUDLY, for the same reason `authorize` refuses
    one: iterating a `str` yields CHARACTERS, and every character would fail the
    membership test — so a caller who passed `"10.0.0.1"` instead of
    `("10.0.0.1",)` would get a server that refuses every request and a
    misconfiguration that looks like an attack. Fail loudly at the call instead.
    """
    if isinstance(trusted, (str, bytes)):
        raise TypeError(
            "trusted must be a SEQUENCE of networks, not one string: iterating a "
            "str yields characters, and no character is an address"
        )
    if peer is None:
        return False
    for network in trusted:
        # ⚠ NO VERSION GATE, AND THE FIRST DRAFT HAD ONE WITH A FALSE COMMENT
        # BESIDE IT: `peer.version == network.version and peer in network`,
        # explained as "`IPv4Address in IPv6Network` raises TypeError". It does
        # not. `ipaddress._BaseNetwork.__contains__` returns False across
        # families — measured on 3.12.13 and 3.14.7, both directions. The clause
        # was therefore dead code, and a mutation sweep found it by surviving
        # its removal. Removed rather than kept: two guards reaching one outcome
        # cannot be told apart by any test, and the comment describing the one
        # that never fires is what a maintainer would have believed.
        if peer in network:
            return True
    return False


def resolve_client(
    headers: Any, client_address: Any, trusted: Sequence[Any]
) -> tuple[str | None, bool]:
    """Decide WHICH address this request is bucketed under, and whether the peer
    was a trusted proxy. `(None, trusted?)` means refuse.

    🔴 THIS IS THE WHOLE OF THE PHASE-1.5b RULE, AND IT IS THE STANDARD
    REVERSE-PROXY ONE:

        peer IS trusted     -> the bucket is `CF-Connecting-IP` (fail closed if
                               it is absent, unparseable or duplicated)
        peer is NOT trusted -> the bucket is the PEER'S OWN ADDRESS, and the
                               header is not read AT ALL

    The security property is *a forged header must never name a THIRD PARTY*.
    The second line satisfies it completely: a caller whose header is ignored
    can only ever lock out **itself**, which is the definition of a rate limit
    working. Whoever forges from an untrusted peer is charged for their own
    traffic and nobody else's.

    🔴 AN EARLIER VERSION REFUSED THE UNTRUSTED PEER OUTRIGHT (a uniform 401,
    `status=untrusted-peer`). That is stricter than the property requires and it
    was a deploy blocker, for two measured reasons:

      * it broke the PHASE-1 ACCEPTANCE PROCEDURE. `kubectl port-forward`
        presents peer `127.0.0.1` while the deployment allowlists the node's
        Cilium internal address, so every request through the documented
        byte-identity flow became `401` — and byte-identity is *the* phase-1
        criterion, so there was no documented way left to run it.
      * it turned a plausible config mistake (one wrong address in one env var)
        into a TOTAL OUTAGE, with `/healthz` still answering — so the pod stayed
        Ready and the alert pointed at credentials rather than at configuration.

    Being refused is not the same as being distrusted. Distrust is expressed by
    ignoring what the caller claims, which is what this does.

    ⚠ `None` PEER (a `client_address` that is not an address at all) still
    refuses: there is no bucket to charge, which is exactly the `no-client-ip`
    condition, so it is reported as that rather than as a fourth vocabulary
    item. Unreachable over a real TCP socket; reachable, and tested, here.

    Both branches normalise through `rate_limit_key`, so one caller is one
    bucket whichever door it came in by — the same aggregation rule, not a
    second copy of it.
    """
    peer = peer_address(client_address)
    if peer_is_trusted(peer, trusted):
        return client_ip(headers), True
    if peer is None:
        return None, False
    return str(rate_limit_key(peer)), False


def rate_limit_key(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> Any:
    """Collapse an address to the unit a lockout should apply to.

    🔴 A FULL IPv6 ADDRESS IS NOT A CLIENT, IT IS A CHOICE. An ordinary
    residential allocation is a /64 — 2**64 addresses the same person can pick
    freely — so keying on the full address gives one attacker 2**64 buckets and
    the lockout becomes decorative. Worse, it is also the cheapest way to grow
    the failure table without bound. So IPv6 is aggregated to its **/64**.

    🔴 An IPv4-MAPPED IPv6 ADDRESS IS THE SAME CLIENT AS ITS IPv4 FORM. Left
    alone, one IPv4 caller gets a free second bucket simply by reaching the edge
    over v6 — and the guard that claimed "one caller is one bucket" only ever
    compared two spellings of the mapped form, so it did not see this.
    """
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return address.ipv4_mapped
        return ipaddress.ip_network(f"{address}/64", strict=False)
    return address


_AUDIT_SAFE = re.compile(r"[^\x20-\x7e]")


def audit_field(value: Any, *, limit: int = 256) -> str:
    """Make `value` safe to put in a whitespace-delimited log record.

    🔴 THE AUDIT LINE IS A LOG-INJECTION SINK, AND IT IS REACHED BEFORE AUTH.
    The request path is `unquote()`d, so `%0a` becomes a REAL NEWLINE — and this
    record is one f-string with no escaping. An unauthenticated caller could
    therefore emit a second, syntactically perfect line of their choosing:

        GET /api/v1/x%0astore-api%20audit%20…%20token=<any>%20auth=ok%20…

    That is not a cosmetic defect. This module's whole claim is that the `token=`
    fingerprint proves which credential a client used, and the README's rotation
    procedure says to delete the old token once its fingerprint stops appearing.
    A caller who can forge that line can keep any fingerprint alive forever
    (blocking rotation), fabricate an `auth=ok` from an address of their choice,
    and drown or forge the Loki auth-fail alert.

    So every field is passed through here: non-printable characters — CR, LF, NUL,
    tabs, escapes — become `?`, spaces become `_` so a value cannot split into
    two fields, and the result is length-capped. Percent-encoding rather than
    replacement would be reversible, but reversibility is not what the log needs;
    unforgeable record boundaries are.
    """
    text = str(value)
    if len(text) > limit:
        text = text[:limit] + "...truncated"
    return _AUDIT_SAFE.sub("?", text).replace(" ", "_")


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
        # 🔴 `nan` and `inf` PARSE, and both walk straight through `<= 0`
        # (`nan <= 0` is False). Measured consequences: a nan WINDOW silently
        # disables the limiter entirely, because `t > now - nan` is False for
        # every recorded failure; a nan or inf LOCKOUT makes it permanent. Both
        # are exactly the "misconfiguration that defaults is invisible forever"
        # this function's docstring exists to prevent, arriving through the one
        # comparison that does not order them.
        if not math.isfinite(value):
            raise ValueError(f"{name} must be a finite number, got {raw!r}")
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
                # 🔴 PRUNE EXPIRED LOCKOUTS BEFORE ASKING WHETHER THERE IS ROOM.
                # Found by a test, not by reading: `_evict` ran AFTER this check,
                # so a table full of ALREADY-EXPIRED entries reported "no room"
                # and refused a lockout that should have been granted. Once the
                # cap is reached once, that would persist for as long as new
                # failures kept arriving — the cap turning into a permanent
                # disable of the whole lockout mechanism.
                for stale in [k for k, v in self._locked_until.items() if v <= now]:
                    del self._locked_until[stale]
                if (
                    key in self._locked_until
                    or len(self._locked_until) < MAX_TRACKED_LOCKOUTS
                ):
                    self._locked_until[key] = now + self.lockout_s
                    self._failures.pop(key, None)
                    self._evict(now)
                    return True
                # 🔴 THE TABLE IS FULL, SO NO LOCKOUT WAS CREATED — SAY SO.
                # The first version returned True here regardless, and popped
                # the streak. Measured consequences, both bad: the audit log
                # wrote `status=lockout-triggered` for a client that was NOT
                # locked out (the log lying about the one event the operator
                # alerts on), and popping the streak every `max_failures`
                # failures meant no state accumulated at all — unlimited brute
                # force, for an attacker who had first filled the table.
                #
                # Keeping the streak is what makes this degrade safely: the
                # client stays AT the threshold, so every subsequent failure
                # retries the lockout and takes it the moment a slot frees.
                self._failures[key] = recent
                self._evict(now)
                return False
            self._failures[key] = recent
            self._evict(now)
            return False

    def record_success(self, key: str) -> None:
        """🔴 DELIBERATELY A NO-OP. A success does NOT forgive a failure streak.

        It used to. That was my invention, not the specification — which says
        five failed auths per client per minute — and it created two attacks,
        both of which turn on the key being an ADDRESS rather than an identity:

          * an attacker holding ANY accepted token (including the old one that
            overlap rotation deliberately keeps live) interleaves one success
            per four guesses and brute-forces the rest of the set forever;
          * an attacker sharing a NAT with a legitimate client is never locked
            out at all, because the victim's ordinary traffic keeps resetting
            the counter on their behalf.

        The sliding window already provides the forgiveness this was reaching
        for: four typos age out after `window_s` on their own. Kept as a method
        rather than deleted so the call site still reads as a decision.
        """
        return

    def _evict(self, now: float) -> None:
        """Bound BOTH tables. Called under `self._lock`.

        🔴 THIS USED TO BE A BOUND IN NAME ONLY, and the comment saying otherwise
        was false. It dropped only entries whose whole streak had already aged
        out of the window — so INSIDE the window nothing was evictable and the
        table grew without limit (measured: 20,000 keys held against a cap of
        4,096). `_locked_until` had no cap at all (measured: 5,000). It also ran
        `max(times)` over every entry on every failed auth, under the global
        lock, which is O(n) per request once the table is large.

        Now: expired lockouts go first (free), then aged-out failure streaks,
        and only if the table is STILL over the cap are the oldest live streaks
        dropped — oldest-first, so the client closest to being locked out is the
        last to be forgotten.

        ⚠ ACTIVE LOCKOUTS ARE STILL NEVER DROPPED FOR SPACE. Evicting one is a
        bypass dressed as memory hygiene. `_locked_until` is instead bounded by
        construction: an entry costs an attacker `max_failures` requests to
        create, and each one expires on its own; at the cap, new lockouts are
        refused rather than old ones released — a bounded, stated failure mode,
        and the safe direction is not obvious enough to leave implicit.
        """
        for key in [k for k, v in self._locked_until.items() if v <= now]:
            del self._locked_until[key]
        if len(self._failures) <= MAX_TRACKED_CLIENTS:
            return
        cutoff = now - self.window_s
        for key in [
            k for k, times in self._failures.items() if not times or times[-1] <= cutoff
        ]:
            del self._failures[key]
        if len(self._failures) <= MAX_TRACKED_CLIENTS:
            return
        # Still over: drop the OLDEST live streaks. `times` is append-ordered, so
        # `times[-1]` is that key's most recent failure — no scan of the list.
        overflow = len(self._failures) - MAX_TRACKED_CLIENTS
        for key in sorted(self._failures, key=lambda k: self._failures[k][-1])[:overflow]:
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
    # 🔴 WITHOUT THIS, `timeout` IS None AND A HALF-OPEN CONNECTION PINS A THREAD
    # FOREVER. Measured: 50 slowloris connections held 50 live threads, and
    # `ThreadingHTTPServer` caps neither threads nor memory. Cloudflare shields
    # the public side, but nothing shields a caller that reaches the pod
    # directly — which is the same exposure the CF-Connecting-IP trust rests on.
    timeout = 15
    # Suppress the default `BaseHTTP/x.y Python/3.n` banner. An unauthenticated
    # request should not be able to read the interpreter version off the wire.
    server_version = SERVER_BANNER
    sys_version = ""

    # Injected by `build_server`.
    store_root: str = DEFAULT_STORE
    expected_tokens: tuple[str, ...] = ()
    # 🔴 EMPTY MEANS BELIEVE NOBODY'S HEADER, not "believe everybody's". A
    # subclass that never reaches `build_server` (and `build_server` refuses an
    # empty set) inherits a server that ignores `CF-Connecting-IP` from every
    # peer and buckets every caller under its own address — the safe direction,
    # because no caller can then name another. The dangerous default would be
    # silent and would look like it worked.
    trusted_proxies: tuple[Any, ...] = ()
    limiter: RateLimiter | None = None
    audit: Callable[[str], None] = staticmethod(lambda line: print(line, flush=True))

    # Per-request identity, reset at the top of every dispatch so a keep-alive
    # connection cannot carry the previous request's fingerprint into this
    # request's audit line.
    _client_ip: str | None = None
    _token_fp: str | None = None
    # `None` = not established yet (a request line too malformed to get that
    # far). Rendered as `peer=-`, never silently as "trusted".
    _peer_trusted: bool | None = None

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
        if code != 200:
            # 🔴 A REJECTED REQUEST NEVER KEEPS ITS CONNECTION. Belt to
            # `_drain_body`'s braces: if framing was ever mis-read, the socket
            # is already untrustworthy and reusing it is the smuggling primitive
            # itself. Costs one TCP handshake per 401, on a path nobody legitimate
            # walks twice.
            self.close_connection = True
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if self.close_connection:
            # Setting the flag alone closes the socket but tells the PEER
            # nothing, so a pooling proxy keeps the entry and discovers the
            # close on its next use. Say it on the wire.
            self.send_header("Connection", "close")
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

        🔴 `peer=` IS ITS OWN FIELD, AND THAT IS THE POINT. Reaching the pod
        without going through the gateway is worth detecting, but it is NOT an
        authentication failure and must not be spelled as one — an earlier
        version emitted it as `status=untrusted-peer` with `auth=fail`, which
        put every port-forward into the Loki auth-fail alert and taught the
        operator to ignore it. Fixing that at the emitter is the fix; fixing it
        in the alert query would leave the next consumer to rediscover it.

        `peer=untrusted` also tells the reader how to interpret `ip=`: trusted
        means the field came from `CF-Connecting-IP`, untrusted means it is the
        TCP peer and the header was never read.
        """
        peer_state = (
            "-"
            if self._peer_trusted is None
            else ("trusted" if self._peer_trusted else "untrusted")
        )
        self.audit(
            "store-api audit "
            f"ts={datetime.now(timezone.utc).isoformat(timespec='seconds')} "
            f"ip={audit_field(self._client_ip or '-')} "
            f"peer={peer_state} "
            f"method={audit_field(self.command, limit=16)} "
            f"path={audit_field(path)} "
            f"token={audit_field(self._token_fp or '-', limit=16)} "
            f"auth={'ok' if self._token_fp else 'fail'} "
            f"result={int(result)} status={audit_field(status, limit=32)}"
        )

    # --- methods ----------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        self._handle()

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle()

    def _drain_body(self) -> None:
        """🔴 READ AND DISCARD THE ENTITY BODY. THIS IS A SMUGGLING FIX.

        `BaseHTTPRequestHandler` never reads a request body, and this server
        keeps connections alive (HTTP/1.1). So an unread body stayed in the
        socket buffer and was parsed as THE NEXT REQUEST on the same connection.
        Measured: one `POST` whose body was a complete
        `GET /api/v1/recall/<scope>` with a valid bearer returned TWO responses,
        the second carrying store content — from a request the caller never
        appeared to make.

        Behind a proxy that pools upstream connections (Traefik does, by
        default) that is CL.0 request smuggling: a POST body holding a PARTIAL
        request line desynchronises the connection, and the NEXT victim request
        completes the attacker's line — carrying the VICTIM's `Authorization`
        header to a scope the attacker chose.

        Belt and braces, because either alone is one mistake from failing:
        drain the declared body here, and `_respond` sets `close_connection` on
        every non-200 so a desynchronised connection cannot be reused at all.
        """
        headers = getattr(self, "headers", None)
        if headers is None:
            return
        # 🔴 ANY FRAMING THIS FUNCTION DOES NOT FULLY UNDERSTAND ENDS THE
        # CONNECTION. A delta audit measured the first version being walked by
        # two framings it silently ignored — `Transfer-Encoding: chunked` (no
        # Content-Length at all, so the body stayed queued) and a NEGATIVE
        # Content-Length — each producing two responses on one socket with store
        # content in the second, on a 200 where the connection legitimately
        # stays open so the close-on-non-200 belt does not apply. No endpoint
        # here accepts a body, so refusing to reason about exotic framing costs
        # nothing and is the only answer that cannot be walked by a third
        # framing nobody has thought of yet.
        if headers.get("Transfer-Encoding"):
            self.close_connection = True
            return
        if headers.get_all("Content-Length") is None:
            return
        # 🔴 EXACTLY ONE Content-Length, via the SAME predicate `client_ip` uses.
        # A bare `.get()` takes the first value, so `Content-Length: 0` followed
        # by `Content-Length: 154` drained nothing and smuggled the body — on a
        # 200, where the close-on-non-200 belt does not apply. Measured.
        raw_length = sole_header(headers, "Content-Length")
        if raw_length is None:
            self.close_connection = True
            return
        try:
            length = int(raw_length)
        except ValueError:
            # A malformed length means the framing is already unknowable. Do not
            # guess; `_respond` will close the connection.
            self.close_connection = True
            return
        if length < 0:
            # Negative lengths are not "no body" — they are a caller telling two
            # different stories to two different parsers.
            self.close_connection = True
            return
        if length == 0:
            return
        if length > MAX_DRAIN_BYTES:
            # Too big to swallow politely. Closing is the only safe answer —
            # leaving it queued is exactly the desync above.
            self.close_connection = True
            return
        # 🔴 BOUNDED IN TIME, NOT JUST IN BYTES. `timeout` is per-recv, so a
        # caller dripping one byte every 10s held a thread for as long as it
        # liked — measured at 60s for a 6-byte body, i.e. months for a 1 MiB
        # one. The read loop this fix introduced needed its own deadline.
        deadline = time.monotonic() + DRAIN_DEADLINE_S
        remaining = length
        while remaining > 0:
            if time.monotonic() > deadline:
                self.close_connection = True
                return
            # 🔴 `read1`, NOT `read`. `rfile` is a BufferedReader and `read(n)`
            # blocks until it has ALL n bytes (or EOF) — so the deadline check
            # above never got control back and was decorative: a caller dripping
            # a byte at a time inside the per-recv timeout still held the thread
            # for the whole body. Found by the test for the deadline failing,
            # which is the only reason it was found at all. `read1` returns after
            # one underlying recv, which is what makes the loop a loop.
            chunk = self.rfile.read1(min(remaining, 65536))
            if not chunk:
                self.close_connection = True
                return
            remaining -= len(chunk)

    def _reject_write(self) -> None:
        """🔴 PHASE 1 IS READ-ONLY, and that is enforced here, not documented.

        A write endpoint lands in phase 3 with its own append semantics (§2c).
        Until then a POST/PUT/PATCH/DELETE must not fall through to the GET
        router and be served as a read — a mutation that silently succeeded as a
        no-op read is indistinguishable from one that worked.

        🔴 IT NO LONGER SHORT-CIRCUITS THE CLIENT-IP AND LOCKOUT CHECKS. It used
        to answer 405 before either, which made it a free, unauthenticated,
        UNMETERED channel: 31 anonymous POSTs with no token and no
        `CF-Connecting-IP` produced 31 audit lines and counted for nothing —
        enough to drown the Loki auth-fail alert this design relies on. A write
        attempt from an unidentifiable or locked-out client is now the same
        uniform 401 as everything else, and the 405 is reserved for a caller who
        got that far.
        """
        self._client_ip = None
        self._token_fp = None
        self._peer_trusted = None
        path = self._request_path()
        if path is None:
            return
        self._drain_body()
        if not self._identify_and_meter(path):
            return
        try:
            # 🔴 AUTHENTICATE BEFORE ANSWERING 405. Otherwise an anonymous
            # POST flood is still free: identified, not locked out, and never
            # counted — 405 after 405 with nothing charged. A write attempt
            # with no valid credential is not a "wrong method", it is an
            # unauthorised request, and it is answered and charged as one.
            self._token_fp = authorize(
                self.headers.get("Authorization"), self.expected_tokens
            )
        except _Rejected:
            self._refuse(path, self._count_failure(self.limiter, self._client_ip))
            return
        self._respond(405, b"read-only\n", headers={"Allow": "GET, HEAD"})
        self._audit(path, 405, "method-not-allowed")

    do_POST = do_PUT = do_PATCH = do_DELETE = _reject_write  # noqa: N815

    def send_error(self, code: int, message=None, explain=None) -> None:  # noqa: D102
        """🔴 EVERY unhandled method answers the ONE uniform 401, not a 501 page.

        `BaseHTTPRequestHandler.send_error` renders a ~350-byte HTML page that
        echoes the method back — pre-auth, unmetered, unaudited, and a fourth
        distinct shape for what the docstring calls "ONE 401 response,
        byte-identical for every rejection". `OPTIONS`, `TRACE` and any invented
        verb reached it. They now look exactly like a bad token.

        🔴 THE FIRST VERSION OF THIS OVERRIDE REOPENED, IN THE SAME COMMIT, THE
        TWO DEFECTS THE REST OF THAT COMMIT CLOSED. A delta audit measured both:

          * it read `self.path`, which `parse_request` assigns only AFTER five of
            its own `send_error` calls — so `GET\r\n\r\n` (six bytes), a bad
            HTTP version, or a four-word request line raised an unhandled
            AttributeError: no audit record, no response, a ~25-line traceback
            per request. That is verbatim what `_request_path` exists to prevent,
            reintroduced one screen below it, and made cheaper.
          * it never metered, so 30 `FROBNICATE` requests wrote 30 audit lines
            and counted for nothing — the same free channel `_reject_write` had
            just been reordered to close, widened to every other verb.

        So: `getattr` for everything, because on this path NOTHING is guaranteed
        to exist; meter when there are headers to identify a client from; and
        when there are not, refuse AND close, which bounds an unidentifiable
        caller to one request per TCP handshake.
        """
        self._client_ip = None
        self._token_fp = None
        self._peer_trusted = None
        path = self._raw_path()
        if getattr(self, "headers", None) is None:
            # The request line itself was unparseable, so there is no header to
            # identify anyone by. Answer, audit, and do not keep the connection.
            #
            # ⚠ PROVABLY REDUNDANT TODAY, AND KEPT ANYWAY. A mutation sweep
            # showed deleting this line changes nothing: `_respond` sets
            # `close_connection` for every non-200, so the 401 below already
            # closes. It stays because it is one line of defence on a
            # smuggling-adjacent property, and because a future edit to
            # `_respond`'s rule must not silently turn an unidentifiable caller
            # into a keep-alive channel. Recorded as an EQUIVALENT MUTANT rather
            # than left as an unexplained survivor.
            self.close_connection = True
            self._unauthorized()
            self._audit(path, 401, "malformed-request")
            return
        self._drain_body()
        if not self._identify_and_meter(path):
            return
        self._refuse(path, self._count_failure(self.limiter, self._client_ip))

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

    def _raw_path(self) -> str:
        """The request target, never parsed. Safe to log; safe on any input.

        🔴 `getattr`, NOT `self.path`. `path` is an INSTANCE attribute assigned
        by `parse_request`, and five of that method's own `send_error` calls
        happen BEFORE the assignment — so on a malformed request line the
        attribute does not exist at all and `self.path` raises AttributeError.
        There is no class-level default to fall back on.
        """
        raw = getattr(self, "path", None)
        return raw if isinstance(raw, str) else "-"

    def _request_path(self) -> str | None:
        """The decoded path, or `None` having already answered.

        🔴 `urlsplit` RAISES on a malformed absolute-form target. Measured:
        `GET http://[ HTTP/1.1` produced an unhandled `ValueError: Invalid IPv6
        URL`, no response at all, a killed connection and a ~20-line traceback
        in the pod log — pre-auth, unauthenticated, unmetered, and a cheaper
        log-flood than any of the paths that ARE metered. Absolute-form targets
        are mandatory-to-accept, so this is a request a conforming client may
        legitimately send; it must be a uniform 401, not a crash.
        """
        try:
            return unquote(urlsplit(self.path).path)
        except ValueError:
            self._client_ip = None
            self._token_fp = None
            self._peer_trusted = None
            self._unauthorized()
            self._audit(self._raw_path(), 401, "malformed-target")
            return None

    def _identify_and_meter(self, path: str) -> bool:
        """Establish the client and charge the lockout. False = already answered.

        Shared by the read router and the write rejection, because a rule
        enforced at one call site and not the other is the failure this whole
        file keeps finding: writes used to skip both checks entirely.
        """
        ip, trusted = resolve_client(
            self.headers, getattr(self, "client_address", None), self.trusted_proxies
        )
        self._peer_trusted = trusted
        if ip is None:
            # 🔴 FAIL CLOSED. The alternative — bucketing every unidentified
            # request under one shared key — is the failure the whole
            # `CF-Connecting-IP` design exists to avoid: one abuser would then
            # lock out everybody. Nothing is counted here, precisely because
            # there is no bucket to count into.
            self._refuse(path, "no-client-ip")
            return False
        self._client_ip = ip
        limiter = self.limiter
        if limiter is not None and limiter.locked_out(ip):
            # Checked BEFORE the token: a lockout that a valid credential could
            # walk through would not be a lockout, and an attacker who guesses
            # one token mid-run must not have the record wiped.
            self._refuse(path, "locked-out")
            return False
        return True

    def _handle(self) -> None:
        self._client_ip = None
        self._token_fp = None
        self._peer_trusted = None
        path = self._request_path()
        if path is None:
            return
        self._drain_body()

        # 🔴 BEFORE EVERYTHING, and it is the ONLY thing before auth.
        # Unauthenticated by design (§2b) and it says nothing but "ok". It is
        # also the kubelet's probe, so it must not be rate-limited or require a
        # `CF-Connecting-IP` the kubelet has no reason to send.
        if path == HEALTH_PATH:
            self._respond(200, HEALTH_BODY)
            return

        if not self._identify_and_meter(path):
            return
        ip = self._client_ip
        assert ip is not None  # set by `_identify_and_meter` on the True path
        limiter = self.limiter

        if not path.startswith(API_PREFIX):
            # Not an API path and not health. Answered with the SAME uniform 401
            # as a bad token: a 404 here would let an unauthenticated caller map
            # the URL space, which is the enumeration surface §2b forbids.
            #
            # 🔴 BUT IT IS NOT COUNTED, AND THAT IS A CORRECTION. It used to be,
            # on the reasoning that URL probing is the same attack as token
            # probing. Combined with a success no longer forgiving a streak,
            # that measured as a legitimate client HOLDING THE RIGHT TOKEN
            # locking itself out for 15 minutes by requesting `/favicon.ico`,
            # `/`, `/robots.txt`, `/metrics` and `/api/v1` — five ordinary wrong
            # paths, one of them a missing trailing slash away from the real
            # prefix, with nothing able to forgive it.
            #
            # The specification says five failed AUTHS per minute. A request to
            # a path that never reaches the token check is not a failed auth.
            # Volumetric URL probing is what the Traefik middleware (10/s) and
            # the Cloudflare WAF rule are for; this layer is the only one that
            # can see a WRONG CREDENTIAL, and that is what it counts.
            self._refuse(path, "unauthorized")
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

        params = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        rest = path[len(API_PREFIX) :]
        parts = [p for p in rest.split("/") if p]

        if any(not SAFE_PATH_COMPONENT.fullmatch(part) for part in parts):
            # 🔴 A PATH COMPONENT REACHES THE FILESYSTEM. `%2e%2e` decoded to
            # `..`, and `scope_revision` then read `<store>/../.git/HEAD` and put
            # the result in `X-Store-Revision`. Harmless in the pod (the store
            # root is `/data`, whose parent holds nothing) and authenticated-only
            # — but "harmless because of where it happens to be mounted" is not a
            # property this file should rely on. Authenticated, so it may be told
            # what it did wrong.
            self._respond(
                400, b"bad request: invalid path component\n",
                headers={"X-Store-Status": "bad-request"},
            )
            self._audit(path, 400, "bad-request")
            return

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
    trusted_proxies: Sequence[Any],
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
    if isinstance(trusted_proxies, (str, bytes)):
        raise TypeError(
            "trusted_proxies must be a SEQUENCE of networks, not one string"
        )
    # 🔴 REQUIRED, AND NON-EMPTY. `trusted_proxies` has no default in this
    # signature on purpose: a caller that forgot it fails at the call rather
    # than getting a server that 401s everything for a reason nobody can see.
    # An explicitly EMPTY sequence is refused here as a misconfiguration —
    # the class default is empty so that the *unreachable* path still fails
    # closed, but reaching it deliberately is a mistake worth naming.
    networks = tuple(trusted_network(item) for item in trusted_proxies)
    if not networks:
        raise ValueError(
            "trusted_proxies is empty: no peer could ever be trusted, so every "
            f"request would be a 401. Set ${ENV_TRUSTED_PROXIES}"
        )

    class _Handler(StoreRequestHandler):
        pass

    _Handler.store_root = store_root
    _Handler.expected_tokens = tuple(tokens)
    _Handler.trusted_proxies = networks
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
        trusted_proxies = load_trusted_proxies(dict(os.environ))
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
        trusted_proxies=trusted_proxies,
        limiter=limiter,
    )
    # 🔴 The startup line prints every FINGERPRINT, in the order the file lists
    # them, and never a token. It is what makes an overlap rotation checkable:
    # the operator reads these ids, then greps the audit log for the one that
    # should have stopped appearing before deleting its line from the secret.
    print(
        f"subsystem-store-api: listening on {args.host}:{args.port} "
        f"store={args.store} token-ids={','.join(token_id(t) for t in tokens)} "
        f"lockout={max_failures}/{window_s:g}s->{lockout_s:g}s "
        # 🔴 PRINTED, so "which peers may set CF-Connecting-IP" is a fact in the
        # pod log rather than a value nobody can read back out of a running
        # container. It is configuration, not a credential.
        f"trusted-proxies={','.join(str(n) for n in trusted_proxies)}",
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
