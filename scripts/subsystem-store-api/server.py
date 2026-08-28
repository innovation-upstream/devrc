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
⚠ THIS SECTION DESCRIBES A STATE THAT NO LONGER HOLDS, and it is kept because
every guard below was shaped by it. It read: "GET is the only method that
reaches a handler. There is no append endpoint, no `PUT`, no `If-Match`; those
are phase 3 (§2c)". Phase 3's criteria 4-7 landed; see the write-path section at
the bottom of this docstring for exactly what changed and what deliberately did
not. `/recall`, `/search` and `/snapshot` are still GET-only.

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

PHASE 3, CRITERIA 1-3 — TWO-TOKEN AUTHORIZATION, FIRST ON THE **READ** PATH
----------------------------------------------------------------------------
This landed first, on reads only. What changed is WHO a token is and WHAT it may
see; the write path (criteria 4-7) then reused every line of it rather than
growing a second answer.

  * **A token file row maps token -> identity -> scope allowlist**
    (`load_tokens`, `TokenRecord`). A BARE token line is still valid and means
    identity `legacy` with UNRESTRICTED scope — that is the migration and the
    rollback, not a courtesy — and any legacy row makes the process shout on
    stderr at startup.
  * **Every read route refuses a scope outside the caller's allowlist**, and it
    does so by narrowing the INDEX at `subsystem_recall.load_store`, the one
    site both readers load from.
  * **A refused scope is byte-identical to a nonexistent one.**

🔴 THE ABSENT PATH WAS ALREADY AN ENUMERATION ORACLE, WHICH IS WHY A PER-ROUTE
"is this scope yours" CHECK WOULD NOT HAVE BEEN ENOUGH. Measured on the
deployed pod: `GET /api/v1/recall/<never-existed>` answers **200**, status
`scope-absent`, and the body ends `scopes the store does hold: <every scope>`.
Four distinct channels carried it:

    1. `known_scopes`        rendered on every `scope-absent` report
    2. `malformed_elsewhere` names OTHER scopes on EVERY status, not just a miss
    3. `search?all_scopes=1` searches the CONTENT of every scope and NAMES NONE,
                             so a per-scope refusal check has nothing to refuse
    4. the `/snapshot` tar   ships the entry files themselves

1-3 all derive from the single `SubsystemIndex` that `load_store` returns, so
filtering THAT closes all three at once and cannot be forgotten by a route.
4 does not go through the index at all — `_snapshot` walks the store root — so
it is filtered separately, at the candidate list.

⚠ WHAT IS STILL SHARED, STATED RATHER THAN LEFT TO BE FOUND: `X-Store-Snapshot`
(and the freshness prose that opens every body) is STORE-WIDE. It carries a
total `entry-files=` count over scopes the caller cannot name. That is a
deliberate carry-over — it is the freshness guarantee the whole snapshot design
rests on, and scoping it would make a caller's view of staleness a function of
its own allowlist — but it IS a residual count leak, and it is the reason the
byte-identity claim below is about a REFUSED scope versus an ABSENT one, never
about two different stores.

PHASE 3, CRITERIA 4-7 — THE WRITE PATH (§2c)
---------------------------------------------
🔴 THE READ-ONLY GUARD WAS BROKEN ON PURPOSE, ONCE, AND CONVERTED RATHER THAN
DELETED. `do_POST = do_PUT = do_PATCH = do_DELETE = _reject_write` is now
`= _write`, which is the SAME function with a route lookup in front of its 405
tail: a verb with no row in `WRITE_ROUTES` and a path with no row both take the
identical answer they took before. `TestPhaseOneScope` still fails when a route
OR a verb appears outside its ledger — the ledgers simply now name the write
rows too.

  * **`POST /api/v1/entry/<scope>/<ref>/bullets`** appends ONE attributed bullet
    to `## Nuance / work-history`. 🔴 THE ACTOR IS THE AUTHENTICATED IDENTITY
    AND THE BODY CANNOT SUPPLY IT — an `actor` key is accepted and discarded,
    because a client-supplied actor lets any token-holder attribute a bullet to
    somebody else. The SESSION is caller-supplied, because it is correlation
    data rather than an identity claim, and it is validated as hostile input.
  * 🔴 **A LEGACY (BARE, UNMAPPED) TOKEN MAY NOT WRITE.** It has no identity, so
    there is no actor to derive and "every appended bullet records actor and
    session" cannot be satisfied. It is refused with its OWN error, which makes
    the token-file migration a prerequisite for writes rather than an
    afterthought. READS from a legacy token are unchanged.
  * **Commutative and idempotent** (`append_bullet`), and this is the ship gate:
    the store is not re-derivable, so a lost append is lost forever. Two writers
    appending different bullets both survive — the read-modify-write is under
    `_EntryLock`, a side-file `flock` that survives the temp-file-and-rename the
    write itself uses. Re-POSTing the same CONTENT is a no-op that writes not one
    byte.
  * **`PUT /api/v1/entry/<scope>/<ref>`** replaces the whole file behind a
    REQUIRED `If-Match`; a stale revision is a 412 and the file is untouched.
    ⚠ **THE ATTRIBUTION GUARANTEE ABOVE IS A CLAIM ABOUT `POST /bullets`
    ONLY.** A PUT writes the caller's bytes verbatim — a body containing
    `- <date>: OPEN: … [cairn: someone-else/…]` lands exactly as sent — and this
    server does not check it. Decided, not overlooked: PUT exists for the
    whole-file rewrites the store needs (`## Pointers`, `OPEN:` ->
    `RESOLVED <sha>:`), and enforcing per-bullet attribution would mean diffing
    the old bullet set against the new one and refusing legitimate rewrites
    whenever that diff was wrong. See `replace_entry`.
    ⚠ THE REVISION IS THE ENTRY'S CONTENT HASH, NOT `scope_revision`, AND THAT IS
    A DELIBERATE DEVIATION FROM THE CARD'S WORDING — no scope in the served copy
    is a git repo, so `scope_revision` answers "unknown" for all of them and a
    precondition keyed on it could never refuse anything. See `entry_revision`.
  * **Writes go through the SAME auth path as reads.** Same
    `_identify_and_meter`, same `authorize`, same uniform 401, same lockout. A
    write to a scope outside the caller's allowlist is refused, and 🔴 THAT
    REFUSAL IS BYTE-IDENTICAL TO A WRITE TO A SCOPE THAT DOES NOT EXIST — closed
    at the index (`rc.load_store`), exactly as criteria 1-3 closed it for reads,
    rather than by a per-route "is this scope yours" check with its own answer.

Still NOT here, and still tracked forward: criteria 8-10 — the re-seed, the
cache cutover and the retirement of the legacy credential. Those are OPERATIONS,
not code, and criterion 10 in particular is now load-bearing: until the shared
bare token is replaced by mapped rows, NOTHING can write.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
import io
import ipaddress
import json
import math
import os
import re
import sys
import tarfile
import tempfile
import threading
import time
import traceback
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import parse_qs, unquote, urlsplit

_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(_LIB))

import subsystem_recall as rc  # noqa: E402

# 🔴 THE PATH CLASSIFIER IS IMPORTED, NOT DEFINED HERE — it moved into
# `subsystem_resolver` when `load_index` grew an entry-kind guard of its own.
# "What IS this path" spelled at N sites is wrong at N-1 of them, and the two
# sites GUARDED BY THIS CLASSIFIER are `/snapshot` and the index loader:
# disagreeing about a FIFO named `*.md` is what costs a request thread. The
# three ACTION tables stay with their contexts (`_ROOT_ACTIONS`/`_ENTRY_ACTIONS`
# below, `_LOADER_ENTRY_ACTIONS` there), because an action is a property of the
# context and not of the path.
#
# ⚠ TWO GUARDED SITES IS NOT TWO SITES — this comment used to say it was, and
# was wrong. `subsystem_touch.census()` globs `*.md` and `read_text`s the result
# with no kind check, so it still hangs on a fifo. It is unguarded BY RULING,
# not by oversight: it is CLI-only and NOTHING in this server imports
# `subsystem_touch`, so no request thread can reach it.
#
# Re-exported deliberately (hence the `F401`): `KIND_*`, `SKIP/TAKE/REFUSE`,
# `ALL_KINDS`, `classify_path` and `action_for` are read off THIS module by
# `TestClassifierIsTotal`, and the tables below are written in terms of them.
#
# 🔴 `_is_fence` AND `entry_mapping` ARE IMPORTED, PRIVACY AND ALL, RATHER THAN
# RE-SPELLED. The write path has to answer two questions the reader already
# answers: "is this line inside a fenced block" (so a `## Nuance / work-history`
# written INSIDE a fence is not mistaken for the real heading, exactly as
# `extract_sections` treats it) and "would the loader accept these bytes as an
# entry" (the PUT validity gate). A second copy of either is the duplicated
# predicate this file keeps finding: the day one learns a new fence spelling or
# a new identity field, the writer starts accepting what the reader rejects.
from subsystem_resolver import _is_fence  # noqa: E402
from subsystem_resolver import (  # noqa: E402,F401
    ALL_KINDS,
    KIND_ABSENT,
    KIND_BROKEN_LINK,
    KIND_DIRECTORY,
    KIND_INDETERMINATE,
    KIND_LINK_TO_DIR,
    KIND_LINK_TO_FILE,
    KIND_LINK_TO_OTHER,
    KIND_OTHER,
    KIND_REGULAR_FILE,
    REFUSE,
    SKIP,
    TAKE,
    action_for,
    classify_path,
    entry_mapping,
)

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

# 🔴 THE IDENTITY OF A LEGACY ROW, AND IT MEANS UNRESTRICTED SCOPE.
#
# A bare token line — no identity, no allowlist — is the shape the file had
# before this change, and it MUST keep loading: the migration puts the mapped
# rows in beside the old shared token, and the rollback is re-adding that one
# line. A format that refused it would make the rollback a code change.
#
# It is spelled here as a constant because three different things have to agree
# on it: the parser that assigns it, the guard that refuses a MAPPED row from
# claiming it, and the startup warning that names it.
LEGACY_IDENTITY = "legacy"

# 🔴 32, AND THE NUMBER IS LOAD-BEARING RATHER THAN TIDY: it is BELOW
# `MIN_TOKEN_CHARS`, so a token can never be a well-formed identity. That makes
# "the operator put three tokens on one line" structurally impossible to read as
# "token, identity, scopes" — the second field would be at least 43 characters
# and this cap refuses it. A cap ABOVE the token floor would have made that
# misreading silent, and a silent misreading of a credential file is exactly the
# failure the guard ladder below exists for.
MAX_IDENTITY_CHARS = 32

# Lowercase, digits and dashes, starting on an alphanumeric. Deliberately
# NARROWER than the scope class below (no `_`, no uppercase): an identity is
# quoted into the audit log and compared for duplicates, so two spellings of one
# name would be two identities to the parser and one to the operator.
IDENTITY_COMPONENT = re.compile(r"[a-z0-9][a-z0-9-]*")

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

# --- The write path (phase 3, criteria 4-7) -------------------------------------

# 🔴 A SESSION ID IS CORRELATION DATA, NOT AN IDENTITY CLAIM, so unlike the actor
# it IS caller-supplied — and it is therefore validated as hostile input before
# it is written into a curated file. The class is narrower than a token and
# wider than an identity: agent session ids are uuids and short hex handles.
# `fullmatch` on this class is what stops a newline, a markdown control
# character or a `]` from breaking the attribution trailer it is written into.
SESSION_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")

# One bullet, one line. A multi-line append is refused rather than accepted and
# reflowed: `parse_journal_bullets` attaches every non-bullet line to the bullet
# above it, so a caller that sent an embedded newline would silently get ONE
# bullet whose second line is prose it thought was separate — and a caller that
# sent a leading `- ` would get TWO bullets from one POST, only one of which
# carries an attribution trailer. Both are content the store cannot account for.
BULLET_TEXT_MAX = 2000

# 🔴 EVERY CHARACTER `str.splitlines()` TREATS AS A LINE BREAK — TEN OF THEM,
# NOT TWO. The validator used to read `if "\n" in text or "\r" in text`, which is
# a membership test on two characters standing in for a predicate about ten.
# MEASURED, with a paired control on the same payload: a literal `U+2028` in
# `text` was accepted `200 appended`, and the ONE line the server rendered became
# TWO on the next read — the first carrying the caller's prose with **no
# attribution trailer at all** (criterion 4 says every appended bullet records
# actor and session), the second an `OPEN:`-marked bullet whose leading
# `[cairn: …]` an operator reads as somebody ELSE's attribution. One `200` by
# `zach`, two bullets, one of them forged-looking. The same payload with a plain
# `" - "` separator stayed one line, which is what makes it the character and not
# the shape.
#
# `U+2028`/`U+2029` are `Zl`/`Zp`; the other eight are `Cc` and would also be
# caught by `_FORBIDDEN_CATEGORIES` below — but the line-break clause runs FIRST
# so a caller who embedded a newline is told about the newline rather than about
# a category name.
LINE_BREAK_CHARS = "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"

# 🔴 CONTROL AND FORMATTING CHARACTERS ARE REFUSED, AND EACH OF THESE WAS
# MEASURED LANDING IN THE CURATED FILE AT `200 appended`:
#
#   * `\x00`   makes `git` and `grep` treat the entry as BINARY, so the file
#              silently drops out of every text tool the store is read with.
#   * `\x1b[…` rewrites the terminal of whoever renders a recall digest.
#   * `U+202E` (and the isolates) reorder the rendered line, so what an operator
#              reads is not what is stored — visual spoofing of a curated record.
#   * `U+200B` is invisible AND defeats idempotency: two bullets that read
#              identically hash differently, so a retry double-records.
#
# One category test rather than a list of characters, because a list is walkable
# by the next character nobody thought of — the exact failure the two-character
# newline check above already demonstrated. `Cs` (lone surrogates, reachable
# through a JSON `\ud800` escape) and `Co` (private use) are included for the
# same reason. `Cn` (unassigned) is NOT: it would refuse whatever the running
# Python's Unicode tables have not caught up with, which is a refusal that
# changes with the base image.
#
# ⚠ `\t` IS `Cc` AND IS THEREFORE REFUSED. Decided, not incidental: a bullet is
# ONE line of prose, `content_hash` collapses runs of whitespace anyway, and a
# named 400 is better than a tab that renders differently everywhere.
_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co"})

# 🔴 THE ATTRIBUTION IS A SUFFIX, AND THE POSITION IS LOAD-BEARING RATHER THAN
# COSMETIC. The store's bullet grammar is a PREFIX grammar:
# `_JOURNAL_DATE` reads `- YYYY-MM-DD` and `_JOURNAL_OPENNESS` reads
# `- [YYYY-MM-DD: ]OPEN:` / `RESOLVED <sha>:` — both anchored at position 0 with
# an EXACT terminator. Writing the actor between the date and the text
# (`- 2026-08-27 (zach): OPEN: …`) parses as NO MARKER, which is precisely the
# near-miss class `_NEAR_MISS_MARKER` exists to report: the badge silently stops
# rendering and a vanished badge looks like success. A suffix leaves every
# prefix rule untouched, so an appended `OPEN:` bullet still declares itself.
ATTRIBUTION = " [cairn: {actor}/{session}]"

# The parser for the suffix above, and it is deliberately the SAME shape written
# by `render_bullet` rather than a looser one: a trailer this cannot read is not
# an attribution, so its bullet's content hash is computed over the whole line
# and simply will not collide with a fresh append. Anchored at end-of-line.
_ATTRIBUTION_RE = re.compile(
    r"[ \t]*\[cairn: (?P<actor>[a-z0-9][a-z0-9-]{0,31})"
    r"/(?P<session>[A-Za-z0-9][A-Za-z0-9_.-]{0,63})\]\Z"
)

# `- YYYY-MM-DD: ` — the dated bullet opener this writer emits and the one the
# corpus already uses. Stripped before hashing so a bullet re-POSTed on a later
# day is still recognised as the same CONTENT.
_BULLET_OPENER_RE = re.compile(r"\A[-*][ \t]+(?:\d{4}-\d{2}-\d{2}:[ \t]+)?")

# How much of a bullet's content hash is carried. 16 hex characters is 64 bits —
# far past any collision an append stream could reach, and short enough to read
# in an audit line.
CONTENT_HASH_CHARS = 16

# 🔴 A DETERMINISTIC INTERLEAVE POINT, CALLED INSIDE THE CRITICAL SECTION, and it
# is a no-op in every deployment. `claude/RULES.md`: a concurrent-append test
# driven by two threads racing on wall-clock timing proves nothing on the run
# where they happen not to overlap, and the defect it guards against DESTROYS
# CONTENT rather than availability — so the overlap has to be forced, not hoped
# for. This is the seam that forces it, and it is the same shape as `audit=` and
# `warn=`: injected behaviour with an inert default.
#
# It sits AFTER the read and BEFORE the write, which is exactly the window a
# missing lock leaves open.
_WRITE_INTERLEAVE: "Callable[[], None]" = lambda: None  # noqa: E731

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


@dataclass(frozen=True)
class TokenRecord:
    """One credential, and what it is allowed to SEE. Phase 3, criterion 1.

    🔴 THE TOKEN AND ITS AUTHORITY ARE ONE OBJECT ON PURPOSE. The alternative —
    a token list beside a `dict[token, scopes]` — is two structures that a route
    can consult one of. Every consumer here is handed the record `authorize`
    matched, so "which token was this" and "what may it see" cannot be answered
    from different places and disagree.

    `scopes is None` means UNRESTRICTED, and it is reachable ONLY from a legacy
    bare-token row. An EMPTY tuple is its opposite — nothing is visible — and
    that asymmetry is why the handler's per-request default is `()` rather than
    `None`: a route that failed to set the field must see nothing, not
    everything.
    """

    token: str
    identity: str
    scopes: tuple[str, ...] | None

    @property
    def fingerprint(self) -> str:
        """What the audit log carries. Never the token — see `token_id`."""
        return token_id(self.token)

    @property
    def is_legacy(self) -> bool:
        return self.scopes is None


def as_token_record(item: "str | TokenRecord") -> TokenRecord:
    """Normalize one configured credential. ONE PLACE, and it is the same rule.

    A bare `str` becomes the legacy record — identity `legacy`, unrestricted —
    because that IS what a bare token line means (see `LEGACY_IDENTITY`). It is
    not a compatibility shim bolted onto the callers: `load_tokens`,
    `build_server` and `authorize` all route through here, so the meaning of a
    bare token cannot come to differ between the parser and the checker.
    """
    if isinstance(item, TokenRecord):
        return item
    return TokenRecord(token=item, identity=LEGACY_IDENTITY, scopes=None)


def _authority_of(record: TokenRecord) -> str:
    """How a record's AUTHORITY reads in an error message. Never the token.

    Used only by the duplicate-token guard, whose whole job is to say that two
    rows disagree — so it has to be able to say what they disagree ABOUT, and
    the identity and the scope list are the two facts that are not secrets.
    """
    if record.scopes is None:
        return f"{record.identity} (UNRESTRICTED)"
    return f"{record.identity} ({','.join(record.scopes)})"


def _authority_key(record: TokenRecord) -> tuple[str, frozenset[str] | None]:
    """What two rows must AGREE ON to be one grant rather than two authorities.

    🔴 A **SET** OF SCOPES, NOT THE TUPLE, and that is a fix. `TokenRecord` is a
    frozen dataclass, so `record == record` compares `scopes` positionally —
    which made `<tok> zach alpha,beta` and `<tok> zach beta,alpha` a refusal
    reading "two different authorities — zach (alpha,beta) and zach
    (beta,alpha)". Both grant the same set; there is a defined answer, so guard
    11 must not claim there is none. Guard 11's own comment already promised
    this ("rows that merely SPELL one grant differently are recognised as the
    same grant") and delivered it for case/`_`-folding only.

    The TOKEN is not in the key: the collapse is already keyed on it, so every
    pair this compares shares one by construction.

    `None` (unrestricted) is kept as `None` rather than folded to an empty
    frozenset, because an empty allowlist is its OPPOSITE everywhere else in
    this file — the same asymmetry as `visible_scope_set`.

    ⚠ EQUIVALENT MUTANT, MEASURED, RECORDED RATHER THAN LEFT TO READ AS PINNED.
    `frozenset(record.scopes or ())` — which collapses `None` into the empty set
    — SURVIVES the whole suite (56/56 in the token classes, 0 failures). It is
    unreachable, and by construction rather than by luck: two rows only reach
    this comparison sharing a token, `legacy` is the ONLY identity a bare row
    gets and guard 8 refuses a mapped row that claims it, so a `None` is only
    ever compared with another `None`; and guard 9 refuses a mapped row with an
    empty allowlist, so no non-None empty set exists to confuse it. The spelling
    stays because it is the same asymmetry as every other allowlist site and a
    future guard-9 relaxation would make it load-bearing — but no test
    distinguishes the two today, and saying so is the honest form.
    """
    return (
        record.identity,
        None if record.scopes is None else frozenset(record.scopes),
    )


def _parse_token_row(fields: list[str], line: int, total: int) -> TokenRecord:
    """One non-empty line of the token file -> one record, or raise naming why.

    Guards 6-10 of `load_tokens`' ladder live here; the numbering and the
    reachability argument are in that function's docstring. Guards 11 and 12 are
    cross-row and cannot live here — see `load_tokens`.

    `line` is the PHYSICAL line number and `total` the file's physical line
    count, so "line 6 of 6" is something the operator can count to in an editor.
    Both are passed in rather than derived, because this function sees one row.
    """
    if len(fields) not in (1, 3):
        # 🔴 REFUSED, NOT REINTERPRETED, and this is where the format changed.
        # The previous parser was `raw.split()` over the WHOLE file, so two
        # tokens separated by a space were two credentials. Under the row format
        # that same line would read as `token identity scopes` with a token in
        # the identity field. `MAX_IDENTITY_CHARS` already makes that
        # impossible, so it would land here — and landing here is the point: a
        # line this parser cannot read is a startup failure, never a guess about
        # which of two readings the operator meant.
        #
        # ⚠ AND THE MESSAGE NAMES THE LIKELY TYPO WITHOUT ADMITTING IT. `<tok>
        # zach a, b` is FOUR fields, because the space after the comma splits
        # the scope list in two — a diagnostic of "4 fields, expected 1 or 3"
        # is correct and useless, and the operator's next move is to stare at a
        # line that looks like it has three of them. The hint is appended, never
        # substituted for the refusal, and it is CONDITIONAL on evidence in the
        # row (a comma past the identity field) rather than being guessed: the
        # `<tokenA> <tokenB>` case has no comma and still gets the sentence
        # about two tokens, which is ITS likely cause.
        hint = ""
        if len(fields) > 3 and any("," in f for f in fields[2:]):
            hint = (
                ". Field 3 is a comma-separated list with NO SPACES — write "
                "`alpha,beta`, not `alpha, beta`: a space is what separates the "
                "three fields, so `alpha, beta` is two of them"
            )
        raise ValueError(
            f"malformed token row on line {line} of {total}: {len(fields)} fields, "
            f"expected 1 (a bare legacy token) or 3 (token, identity, "
            f"comma-separated scopes). Whitespace separates the three FIELDS, "
            f"so two tokens on one line is no longer two tokens{hint}"
        )
    token = fields[0]
    if len(fields) == 1:
        return as_token_record(token)

    identity = fields[1]
    if (
        len(identity) > MAX_IDENTITY_CHARS
        or not IDENTITY_COMPONENT.fullmatch(identity)
    ):
        raise ValueError(
            f"invalid identity in token row on line {line} of {total}: {identity!r} — "
            f"expected lowercase letters, digits and dashes, starting on an "
            f"alphanumeric, at most {MAX_IDENTITY_CHARS} characters. The "
            f"identity is quoted into the audit log, so it must be one spelling"
        )
    if identity == LEGACY_IDENTITY:
        # 🔴 A MAPPED ROW MAY NOT CLAIM THE UNRESTRICTED NAME. `legacy` in the
        # audit log has to mean exactly one thing — "this request came in on the
        # old shared credential, which can see everything" — or the one line the
        # operator greps to know the migration is finished is ambiguous.
        raise ValueError(
            f"reserved identity in token row on line {line} of {total}: "
            f"{LEGACY_IDENTITY!r} is what a BARE token line is given, and it "
            f"means unrestricted scope. Name this row's holder instead"
        )

    raw_scopes = [part.strip() for part in fields[2].split(",")]
    if not any(raw_scopes):
        # Reachable with a bare `,`: three fields, a valid identity, and no
        # scope name anywhere in the third.
        raise ValueError(
            f"empty scope allowlist in token row on line {line} of {total} "
            f"({identity!r}): a credential that may see NO scope can never be "
            f"used. Remove the row, or name the scopes it may read"
        )
    # Normalized with the reader's OWN folding rule, so an allowlist entry and
    # the index key it must match cannot disagree about case or `_` vs `-`.
    scopes: list[str] = []
    for raw in raw_scopes:
        # 🔴 BOTH HALVES, AND THE SECOND IS NOT REDUNDANT — the guard has to be
        # as wide as its own sentence. The class alone accepts `-` and `___`,
        # which `normalize_ref` folds to the EMPTY STRING: an entry that is
        # perfectly namable in a URL and yet matches no index key, i.e. a grant
        # that reads as working and does nothing. That is the precise failure
        # this message claims to prevent, so it is checked on the value that
        # actually reaches the comparison, not on the text the operator typed.
        #
        # (The class alone DOES cover the empty string — `[A-Za-z0-9_-]+` needs
        # one character, so `fullmatch("")` is None. A `not raw or` in front was
        # provably redundant and was removed; this second clause is a different
        # claim and is reachable by an input the first accepts.)
        folded = rc.normalize_ref(raw)
        if not SAFE_PATH_COMPONENT.fullmatch(raw) or not folded:
            raise ValueError(
                f"invalid scope in token row on line {line} of {total} ({identity!r}): "
                f"{raw!r} — a scope must match {SAFE_PATH_COMPONENT.pattern} AND "
                f"still name something once folded the way the reader folds a "
                f"scope. An entry that no request could name, or that folds away "
                f"to nothing, is refused here rather than sitting inert"
            )
        scopes.append(folded)
    return TokenRecord(
        token=token,
        identity=identity,
        scopes=tuple(dict.fromkeys(scopes)),
    )


def load_tokens(
    token_file: str | None,
    env: dict[str, str],
    *,
    warn: Callable[[str], None] | None = None,
) -> list[TokenRecord]:
    """Resolve the bearer token SET. FILE FIRST, env only as a fallback.

    🔴 A SET, NOT A TOKEN, and that is the whole of rotation (§2b: "token
    rotation must be a one-command operation"). ONE ROW PER LINE — the CURRENT
    credential first, the PREVIOUS one below it. Rotation is then: add the new
    line, watch the audit log until every client's `token=` fingerprint has
    moved, then delete the old line. There is no window in which a client is
    broken, which is the reason single-token rotations never actually get
    performed.

    THE ROW FORMAT (phase 3, criterion 1)::

        <token>                                   # legacy: identity `legacy`,
                                                  # UNRESTRICTED scope
        <token>   <identity>   <scope>,<scope>    # mapped: named, scoped

    Both shapes may appear in one file, and that is not a concession — it is the
    migration and the rollback. The old shared token stays on its bare line
    while clients move onto mapped rows, and putting that line back is how the
    change is undone without a deploy. A file holding any legacy row emits a
    LOUD startup warning, because "unrestricted" is a state somebody has to be
    able to see from the pod log.

    Guard order — each reachable by an input no earlier guard rejects. `L` is a
    PHYSICAL LINE NUMBER and `T` the file's physical line count:
      1.  some source at all      -> "no token source"
      2.  the file is readable    -> "token file unreadable"
      3.  at least one token      -> "token is empty"
      4.  not an accumulation     -> "too many tokens"
      5.  every token long enough -> "token on line L of T is too short"
      6.  every row parses        -> "malformed token row on line L of T"
      7.  identity is well-formed -> "invalid identity in token row on line L of T"
      8.  identity is not taken   -> "reserved identity in token row on line L of T"
      9.  the allowlist is real   -> "empty scope allowlist in token row on line L of T"
      10. every scope is namable  -> "invalid scope in token row on line L of T"
      11. one authority per token -> "duplicate token on lines L and M"
      12. one row per identity    -> "duplicate identity"

    Guards 1-4 are unchanged in ORDER, and 1-3 in wording too: they are the ones
    an operator has already met, and 5 in particular is reached by a file whose
    second line an editor truncated.

    ⚠ 4 AND 5 DID CHANGE WORDING ON THIS BRANCH, and both were defects rather
    than polish. 4 counted physical ROWS, so a legitimately duplicated line
    counted twice against `MAX_TOKENS`; it counts DISTINCT tokens now. 5-12 said
    "N of M" over NON-BLANK rows while the comment beside them claimed physical
    lines — measurably false on any file with a blank line in it, and "the
    operator can find the line" is the whole reason an index is carried at all.
    Every one of them names a real line number now.

    Guard 5 names the POSITION, never the token — saying "one of them is short"
    would leave the operator grepping a secret by hand — and 6-12 keep that
    property for the same reason.

    🔴 EVERY ROW REACHES THE LADDER, AND THAT IS A FIX, NOT A STYLE CHOICE.
    This loop used to drop a line whose FIRST FIELD had already been seen —
    before parsing it, before validating it, silently. Two failures were
    measured, and both are fail-OPEN:

      * `<tok>` on one line and `<tok> zach alpha` on the next — the exact
        migration this format exists for, "scope a credential its holder
        already has" — loaded as ONE row, `identity=legacy scopes=None`, i.e.
        UNRESTRICTED. The mapped row simply did not exist. The only signal was
        a banner saying "1 of 1 token rows are bare" over a two-line file.
      * `<tok> zach alpha` then `<tok> Za_CH_BAD !!!!` loaded clean. The second
        row carried an invalid identity AND an invalid scope and was never
        validated, because it was dropped before guards 6-10 ran.

    That is guard 10's own defect class — a grant that reads as working and does
    nothing — one level up and in the unsafe direction, and it contradicted
    guard 12, which refuses two rows claiming one IDENTITY for having "no
    defined precedence" while two rows claiming one TOKEN silently picked one.
    So: parse first, collapse after, and only rows that are IDENTICAL collapse.

    🔴 GUARD 11 RUNS BEFORE GUARD 12, AND THE ORDER IS LOAD-BEARING. A file
    holding one row twice, verbatim, must collapse to one record — otherwise
    guard 12 would see two rows claiming one identity and refuse the ordinary
    "I pasted the line twice" file. Collapse first, then count identities.

    🔴 AND "TWICE" MEANS THE SAME GRANT, NOT THE SAME TEXT. Two rows collapse
    when their identity and their scope SET agree; case, `_` vs `-`, a repeated
    scope and the ORDER of the list are all spellings of one grant. See
    `_authority_key`.

    🔴 GUARD 12 EXEMPTS `legacy`, AND THAT IS NOT AN OVERSIGHT. Two legacy rows
    are an overlap rotation of the old shared token, which is the exact thing
    guards 1-5 were built to support. Two rows naming ONE mapped identity are
    different: if their allowlists disagree there is no defined answer, and if
    they agree the operator wanted `<identity>-prev`. Rotating a mapped
    credential therefore uses a second identity, which is also what makes the
    audit log able to say which of the two a client is still on.
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

    # Line-based now, because a row has internal structure. `.split()` per line
    # still absorbs a trailing newline, CRLF and any run of spaces or tabs
    # BETWEEN fields; a blank line is skipped rather than being an empty row.
    #
    # 🔴 NOTHING IS DROPPED HERE. See the docstring: a de-duplication that ran
    # before the parse made two fail-open readings possible.
    #
    # 🔴 AND THE INDEX CARRIED FORWARD IS THE PHYSICAL LINE NUMBER, MEASURED, NOT
    # THE ROW'S POSITION IN THIS LIST. The comment here used to CLAIM the two
    # were the same thing; the loop skips blank lines, so they are not, and the
    # claim was measurably false the moment a file had one. Reproduced on a
    # six-line file whose rows sat on lines 2, 4 and 6: guard 12 said "token rows
    # 1 and 3 both claim it" for a clash on lines 2 and 6 — and "the operator can
    # find the line" is the ENTIRE justification for carrying a pre-collapse
    # index through guard 12 at all. `total` is the physical line COUNT for the
    # same reason, so "line 6 of 6" is countable in an editor.
    lines = raw.splitlines()
    total = len(lines)
    rows: list[tuple[int, list[str]]] = []
    for lineno, text in enumerate(lines, start=1):
        fields = text.split()
        if not fields:
            continue
        rows.append((lineno, fields))

    if not rows:
        raise ValueError("token is empty: the source resolved to whitespace only")
    # 🔴 GUARD 4 COUNTS CREDENTIALS, NOT ROWS. Removing the pre-parse dedup left
    # this counting physical rows, and measured: 4 distinct tokens plus ONE
    # verbatim duplicate line answered "too many tokens: 5, max 4" for a file
    # holding 4 credentials that loaded fine before this branch — and five copies
    # of one token said the same for ONE. That contradicts guard 11, whose own
    # comment calls a duplicated row "the rotation shape, and it is legitimate".
    #
    # DISTINCT FIRST FIELDS is exactly the post-collapse count and needs no
    # parse: guard 11 collapses on `record.token`, which IS `fields[0]`, and
    # refuses outright any pair that shares one without agreeing. So for every
    # file that loads at all, this number and `len(collapsed)` are the same
    # number — computed here only because guard 4 must stay ahead of the parse.
    credentials = {fields[0] for _lineno, fields in rows}
    if len(credentials) > MAX_TOKENS:
        raise ValueError(
            f"too many tokens: {len(credentials)}, max {MAX_TOKENS}. Every "
            f"DISTINCT token is a live credential; retire the old ones instead "
            f"of accumulating them"
        )
    for lineno, fields in rows:
        if len(fields[0]) < MIN_TOKEN_CHARS:
            raise ValueError(
                f"token on line {lineno} of {total} is too short: "
                f"{len(fields[0])} chars, need >= {MIN_TOKEN_CHARS} (256 bits "
                f"base64url). A short token is a guessable one"
            )

    records = [_parse_token_row(fields, lineno, total) for lineno, fields in rows]

    # GUARD 11 — one token, one authority. Runs on PARSED records, so a row that
    # would be collapsed has already been through guards 6-10, and two rows that
    # merely SPELL one grant differently are recognised as the same grant rather
    # than as a disagreement. Three spellings are folded, and each was a measured
    # false refusal or would have been one:
    #   * case and `_` vs `-`  (`Kelp_Forest` == `kelp-forest`) — by the parser
    #   * a repeated scope     (`alpha,alpha` == `alpha`)       — by the parser
    #   * scope-list ORDER     (`alpha,beta` == `beta,alpha`)   — by
    #     `_authority_key`, which compares the SET. Measured before that: the two
    #     rows were refused as "two different authorities — zach (alpha,beta) and
    #     zach (beta,alpha)", which is one grant written twice.
    #
    # 🔴 COLLAPSE ONLY WHAT GRANTS THE SAME THING. Anything less than the
    # identity AND the scope SET agreeing is two authorities for one credential,
    # and picking one of them is what made the migration path fail open.
    first_seen: dict[str, tuple[int, TokenRecord]] = {}
    collapsed: list[tuple[int, TokenRecord]] = []
    # `strict=True`: `records` is built one-for-one from `rows` directly above,
    # and if a later edit ever makes it not, a SHORTER zip would silently drop
    # the tail — the rows nobody then checks for a duplicate token.
    for (lineno, _fields), record in zip(rows, records, strict=True):
        seen = first_seen.get(record.token)
        if seen is None:
            first_seen[record.token] = (lineno, record)
            collapsed.append((lineno, record))
            continue
        first_line, first_record = seen
        if _authority_key(record) == _authority_key(first_record):
            # The rotation shape, and it is legitimate: one row written twice.
            # Order is kept because the FIRST occurrence is the one retained —
            # which also decides which SPELLING of the scope list survives.
            continue
        raise ValueError(
            f"duplicate token on lines {first_line} and {lineno}: one credential "
            f"is given two different authorities — {_authority_of(first_record)} "
            f"and {_authority_of(record)} — and there is no defined precedence "
            f"between them. Scoping a token its holder already has means EDITING "
            f"the bare row, not adding a second one below it; a second holder "
            f"needs a second token"
        )
    # 🔴 REBOUND HERE, ONCE, so everything below — guard 12, the legacy banner's
    # "N of M", and the returned SET — reads the collapsed list. A second name
    # kept alongside `records` is how a later edit ends up counting one list and
    # returning the other.
    records = [record for _line, record in collapsed]

    # GUARD 12 — one row per mapped identity. Indexed by PHYSICAL LINE, carried
    # through the collapse above, so "lines 2 and 6" names what the operator can
    # see rather than positions in a list they cannot — and, since this branch,
    # rather than an ordinal over non-blank rows that a single blank line makes
    # wrong.
    by_identity: dict[str, int] = {}
    for lineno, record in collapsed:
        if record.is_legacy:
            continue
        first = by_identity.get(record.identity)
        if first is not None:
            raise ValueError(
                f"duplicate identity {record.identity!r}: token rows on lines "
                f"{first} and {lineno} both claim it, and their scope allowlists "
                f"have no defined precedence. Rotate a mapped credential under a "
                f"second identity ({record.identity}-prev), not a second row"
            )
        by_identity[record.identity] = lineno

    legacy = [r for r in records if r.is_legacy]
    if legacy:
        # 🔴 ONE LOUD LINE, EMITTED HERE RATHER THAN BY `main`. This is the only
        # place that knows a row was bare, and a caller obliged to re-derive it
        # is a caller that can forget to. Printed to stderr so it lands in the
        # pod log beside the startup banner.
        emit = warn if warn is not None else (lambda line: print(line, file=sys.stderr))
        emit(
            f"subsystem-store-api: 🔴 UNRESTRICTED-SCOPE LEGACY MODE — "
            f"{len(legacy)} of {len(records)} token rows are bare tokens with no "
            f"identity and NO scope allowlist (identity={LEGACY_IDENTITY!r}); "
            f"they can read EVERY scope in the store. Fingerprints: "
            f"{','.join(r.fingerprint for r in legacy)}. Give each holder its own "
            f"`<token> <identity> <scopes>` row and delete these lines"
        )
    return records


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


def authorize(
    header: str | None, expected: "Sequence[str | TokenRecord]"
) -> TokenRecord:
    """Constant-time bearer check against a token SET. Returns the RECORD that
    matched; raises `_Rejected` and never returns a reason.

    🔴 THE RECORD, NOT THE FINGERPRINT, and the change is the point of phase 3.
    The caller needs two facts about a request — who it is, for the audit line,
    and what it may SEE, for every read route — and they must come out of the
    SAME match. Returning only a fingerprint forced the scope lookup to be a
    second search keyed on something else, which is the shape that ends up
    consulting a stale or wider table than the one that authenticated.

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
    matched: TokenRecord | None = None
    for item in expected:
        # Normalized INSIDE the loop rather than in a comprehension above it, so
        # the loop still performs exactly one `compare_digest` per configured
        # credential and the no-early-exit property below is unchanged.
        record = as_token_record(item)
        if hmac.compare_digest(got, record.token.encode("utf-8")):
            matched = record
    if matched is None:
        raise _Rejected()
    return matched


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


def scope_revision(
    store_root: str | Path,
    scope: str,
    *,
    visible_scopes: Sequence[str] | None = None,
) -> str:
    """The scope's git HEAD, read from the filesystem — `git` is never spawned.

    §3 (Determinism): "have every response carry a `store-revision:` line (the
    scope's git HEAD)", so an agent can quote `scope@sha` and have it be
    checkable later. Reading `.git` directly keeps this module's no-subprocess,
    no-network property, which `subsystem_recall` documents as load-bearing for
    the `/resume` hot path.

    Returns "unknown" for every failure — an absent repo, a detached or
    unresolvable ref, an unreadable file. 🔴 "unknown" is honest; a fabricated
    sha would be quoted into a report and believed.

    🔴 IT IS ALSO A HEADER-LEVEL DISCRIMINATOR, SO IT IS GATED — and it is gated
    BY CONSTRUCTION rather than by the fact that it currently cannot leak.
    `X-Store-Revision` is computed from `<store>/<scope>/.git/HEAD`, a path
    outside the index entirely, so narrowing the INDEX (`load_store`'s
    `visible_scopes`) does not reach it. Today no scope in the served copy is a
    git repo, so it answers "unknown" for everything and the leak is LATENT —
    which is exactly the state in which a guard gets left out and the day a
    scope becomes a repo the header starts telling a caller which refused scopes
    exist. `visible_scopes=None` is unrestricted, matching every other seam here.
    """
    if visible_scopes is not None and rc.normalize_ref(scope) not in {
        rc.normalize_ref(s) for s in visible_scopes
    }:
        return "unknown"
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


SEED_STAMP_NAME = ".seed-stamp"


# =============================================================================
# 🔴 THE WRITE PRIMITIVES (phase 3, criteria 4-6). The store is NOT RE-DERIVABLE
# — it records gotchas, retracted theories and measurements that were true at a
# moment — so a LOST APPEND IS LOST FOREVER. Everything below is shaped by that
# one fact: the failure that matters here is silent content destruction, not
# unavailability, and unavailability is the direction every ambiguity resolves
# towards.
# =============================================================================


class EntryShapeError(Exception):
    """The target file cannot carry an appended bullet.

    Its own exception rather than a generic refusal because the two shapes it
    covers are both a request to write into a file whose structure this writer
    does not understand — no `## Nuance / work-history` heading, or bytes the
    index loader would not accept as an entry — and both must fail the write
    rather than reshape somebody's curated markdown.
    """


class PreconditionFailed(Exception):
    """`If-Match` named a revision the file no longer has. Carries the CURRENT
    one, because a client that cannot learn the new revision cannot retry."""

    def __init__(self, current: str) -> None:
        super().__init__(f"entry revision is {current}")
        self.current = current


def entry_revision(data: bytes) -> str:
    """The revision an `If-Match` is compared against: the entry file's content.

    🔴 THIS IS DELIBERATELY **NOT** `scope_revision`, AND THE DEVIATION IS THE
    ONLY THING THAT MAKES THE PRECONDITION A GUARD AT ALL. `scope_revision`
    reads `<store>/<scope>/.git/HEAD`; no scope in the served copy is a git repo,
    so it answers `"unknown"` for every scope in the store — a precondition
    keyed on it would be satisfied by every caller sending the literal string
    `unknown`, forever, and could never refuse a stale write. A guard that
    cannot fail is not a guard.

    The entry's own content hash is the value a lost-update check actually needs:
    it changes exactly when the bytes a caller based its edit on change. It is
    also derivable by any client OFFLINE — `/snapshot` ships the entry files, so
    a `cairn` cache can compute the revision of what it holds without a round
    trip and without a second endpoint existing to hand it out.
    """
    return hashlib.sha256(data).hexdigest()[:CONTENT_HASH_CHARS]


def content_hash(text: str) -> str:
    """The idempotency key for one bullet: its CONTENT, and nothing else.

    Whitespace is collapsed before hashing so a re-POST that differs only in
    wrapping is the same bullet; the DATE, the ACTOR and the SESSION are not in
    it, so the same observation re-sent by the same agent after a timeout — or
    on the next day — is recognised rather than duplicated.

    ⚠ AND THE CONSEQUENCE, STATED RATHER THAN LEFT TO BE MET: a genuinely NEW
    bullet whose text is byte-identical to one already in the entry is treated
    as already recorded and is NOT appended. That is the idempotency criterion
    working, not a bug — but it means "the drill head overheats" written twice
    six months apart records once. A caller that means both must say something
    different in the second, which the store's own convention (a leading date in
    the prose, a sha, a run id) already produces.
    """
    return hashlib.sha256(
        " ".join(text.split()).encode("utf-8")
    ).hexdigest()[:CONTENT_HASH_CHARS]


def bullet_content(lines: "Sequence[str]") -> str:
    """One stored bullet -> the CONTENT its hash is taken over.

    Strips the two things this writer adds and the corpus already uses: the
    `- YYYY-MM-DD: ` opener and the ` [cairn: actor/session]` trailer. A bullet
    carrying neither (most of the existing corpus) comes back as its own prose,
    which is what makes a fresh append idempotent against a hand-written bullet
    that says the same thing.
    """
    joined = " ".join(" ".join(line.split()) for line in lines).strip()
    joined = _BULLET_OPENER_RE.sub("", joined, count=1)
    joined = _ATTRIBUTION_RE.sub("", joined)
    return " ".join(joined.split())


def render_bullet(text: str, *, actor: str, session: str, today: str) -> str:
    """The line that goes on disk. ONE line, always attributed.

    🔴 `actor` IS THE AUTHENTICATED IDENTITY AND NOTHING ELSE. This function has
    no parameter a request body can reach; the caller passes `record.identity`,
    which came off the credential `authorize` matched. That is the whole of
    criterion 4's attribution guarantee, and it is structural: there is no field
    here for a client-supplied name to land in.

    ⚠ AND THE GUARANTEE IS EXACTLY AS WIDE AS THIS FUNCTION'S CALLERS. Only
    `append_bullet` (`POST /bullets`) renders through here; `replace_entry`
    (`PUT`) writes the caller's bytes verbatim and enforces nothing. Stated
    because "the actor comes from the token" reads like a property of the
    SERVER, and it is a property of one ROUTE.
    """
    return f"- {today}: {text.strip()}" + ATTRIBUTION.format(
        actor=actor, session=session
    )


def nuance_block(lines: "Sequence[str]") -> "tuple[int, str] | None":
    """`(insert index, that section's body)` for the FIRST nuance heading.

    🔴 ONE WALK, BECAUSE THE INSERTION SCOPE AND THE DEDUPE SCOPE MUST BE THE
    SAME SECTION. They were not: insertion took the FIRST
    `## Nuance / work-history` heading while the duplicate check read
    `rc.extract_sections`, which CONCATENATES every block sharing a heading. An
    entry carrying the heading twice therefore answered `200 duplicate` — writing
    nothing — for a genuinely new bullet that merely matched one already sitting
    in the SECOND section, a section this writer would never have inserted into.
    That is content loss in the direction the design says matters most, and it is
    silent: the response says the observation is already recorded.

    So the body returned here is the body of the block the index points INTO,
    and nothing else. A repeated heading is reported by `subsystem_touch
    --validate`; this writer simply refuses to reason about a section it is not
    writing to.

    A heading is what `_heading_blocks` says it is — `#` at column 0, outside a
    fence, compared `rstrip()`ed — because that is the parser every reader uses,
    and a writer that disagreed about where the section starts would insert into
    prose. The FIRST occurrence wins.
    """
    in_fence = False
    start: int | None = None
    body: list[str] = []
    for index, line in enumerate(lines):
        if _is_fence(line):
            in_fence = not in_fence
            if start is not None:
                body.append(line)
            continue
        if not in_fence and line.startswith("#"):
            if start is not None:
                # The next heading of any level ENDS the section — the same rule
                # `extract_sections` applies.
                break
            if line.rstrip() == rc.NUANCE_HEADING:
                start = index + 1
            continue
        if start is not None:
            body.append(line)
    if start is None:
        return None
    return start, "\n".join(body).strip("\n")


def nuance_insert_index(lines: "Sequence[str]") -> int | None:
    """The line index a new bullet is inserted AT, or `None` if there is no place.

    NEWEST-FIRST, immediately under the heading, because that is the store's own
    convention (`parse_journal_bullets`: "The store's convention is newest-first")
    and because appending at the END of the section would put a new bullet after
    whatever trailing prose or nested list the last bullet carries — attaching
    it, by `parse_journal_bullets`' own rule, to that bullet instead of starting
    a new one.

    A heading is what `_heading_blocks` says it is — `#` at column 0, outside a
    fence, compared `rstrip()`ed — because that is the parser every reader uses,
    and a writer that disagreed about where the section starts would insert into
    prose. The FIRST occurrence wins.

    A VIEW over `nuance_block`, never a second walker: two functions answering
    "where does the nuance section start" is exactly how the insertion point and
    the dedupe scope came to disagree.
    """
    block = nuance_block(lines)
    return None if block is None else block[0]


class _EntryLock:
    """Mutual exclusion for one entry file, held across a read-modify-write.

    🔴 THE LOCK IS ON A SEPARATE FILE, AND THAT IS NOT AN OVERSIGHT. The write
    itself is a temp-file-plus-`os.replace`, so the entry file's INODE changes on
    every append: a second writer that had `flock`ed the entry file directly
    would be holding a lock on an inode nobody is looking at any more, and both
    writers would proceed. A stable side file is the only thing both writers can
    agree on across a rename.

    The lock file is named `.<entry>.lock` — a leading dot AND no `.md` suffix,
    so it is invisible to all three of the store's walkers twice over
    (`load_index` globs `*.md`, `/snapshot` skips dotfiles and requires `.md`,
    `snapshot_freshness` counts `.md` only).
    """

    def __init__(self, entry_path: Path) -> None:
        self.path = entry_path.parent / f".{entry_path.name}.lock"
        self._fh: Any = None

    def __enter__(self) -> "_EntryLock":
        self._fh = open(self.path, "a+")  # noqa: SIM115 — released in __exit__
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *_exc: Any) -> None:
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None


def _fsync_dir(directory: Path) -> None:
    """`fsync` a DIRECTORY, so a rename inside it survives a crash.

    Best-effort: a filesystem that refuses a directory fd (or a store on one
    that has no such concept) must not turn a completed write into a 503. The
    bytes are already persisted by the file `fsync` in `_replace_bytes`; what is
    at risk here is only the rename's own metadata, and refusing to serve
    because we could not flush it would trade a rare durability gap for a
    certain availability one.
    """
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _replace_bytes(path: Path, data: bytes) -> None:
    """Write `data` to `path` so a reader sees the OLD file or the NEW one.

    🔴 NEVER `open(path, "w")`. A truncate-then-write leaves a window in which
    the entry is EMPTY or half-written, and a concurrent `/recall` reading it
    then serves a truncated entry as a complete one — the silent under-report
    this module is built against, produced by the writer instead of the reader.
    `os.replace` is atomic within a filesystem, and the temp file is created in
    the SAME directory precisely so it is on that filesystem.

    🔴 TWO FSYNCS, AND THE SECOND ONE IS NOT REDUNDANT. `fsync` on the FILE
    persists the bytes; the RENAME lives in the parent DIRECTORY and is a
    separate piece of metadata with its own writeback. Without the directory
    fsync a node that loses power after `os.replace` returns can come back with
    the old name still pointing at the old inode — the append is gone, and the
    client was told `200 appended`. Atomicity is a claim about what a concurrent
    READER can see; durability is a claim about what survives a crash, and only
    the first of those `os.replace` gives you for free.

    Both are best-effort against a directory that cannot be opened (some
    filesystems refuse `O_RDONLY` on a directory fd, and a store on one of them
    must still be writable) — but the failure is NOT swallowed silently for the
    file fsync, which is the one that decides whether the bytes exist at all.
    """
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".cairn-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    except BaseException:
        # A failed write must not leave a `.cairn-*.tmp` behind: it is invisible
        # to every reader, so nothing would ever report it and nothing would
        # ever clean it up.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append_bullet(
    path: Path, *, text: str, actor: str, session: str, today: str
) -> "tuple[str, str, str]":
    """Append one attributed bullet. Returns `(status, line, revision)`.

    `status` is `"appended"` or `"duplicate"`; on `"duplicate"` NOT ONE BYTE of
    the file is written, and `line` is the bullet already on disk.

    🔴 COMMUTATIVE AND IDEMPOTENT, WHICH IS THE SHIP GATE (criterion 5). Two
    writers appending DIFFERENT bullets to one entry must both survive, and the
    whole read-modify-write therefore happens under `_EntryLock` — an
    unsynchronised version reads the same original bytes twice and the second
    `os.replace` silently discards the first append. Re-appending the SAME
    content is a no-op decided by `content_hash` over the bullets already there,
    so a retried request cannot double-record.

    🔴 A SPLICE INTO THE ORIGINAL BYTES, NEVER A DECODE-AND-REJOIN. This used to
    read `original.decode("utf-8", errors="replace")`, `splitlines()`, and write
    back `"\\n".join(...) + "\\n"` — which silently REWROTE THE WHOLE FILE, and
    lossily. Three measured effects of one ordinary append to an unrelated line:

      * a byte that is not valid UTF-8 anywhere in the file became `U+FFFD`,
        permanently, at `200 appended` with no error — while `replace_entry`
        decodes the SAME bytes with `errors="strict"` and answers 422. The two
        write primitives disagreed about what a valid entry is, and the LOSSY
        one was the primitive advertised as purely additive.
      * every `\\r\\n` became `\\n`.
      * a file with no trailing newline gained one.

    Each of those also changes the entry's revision, invalidating every other
    client's `If-Match` for a change nobody asked for.

    So the output is `original[:offset] + inserted + original[offset:]` and the
    untouched region is byte-identical BY CONSTRUCTION rather than by a
    round-trip anyone has to trust. `offset` is the end of the heading line
    INCLUDING its terminator, and `inserted` carries that same terminator — so
    CRLF stays CRLF, and a heading that is the final line with no terminator at
    all gets `\\n<bullet>` appended, preserving "this file does not end in a
    newline" rather than quietly fixing it.

    The decode for ANALYSIS uses `surrogateescape`, which round-trips every byte
    sequence exactly; it decides WHERE to splice and whether the content is
    already there, and it is never the thing written back.
    """
    with _EntryLock(path):
        original = path.read_bytes()
        # 🔴 `surrogateescape`, not `replace`. `replace` is destructive on the
        # way IN, so any offset computed from it would describe a string that is
        # not this file. `surrogateescape` is a bijection with the bytes.
        text_in = original.decode("utf-8", errors="surrogateescape")
        lines = text_in.splitlines()
        block = nuance_block(lines)
        if block is None:
            raise EntryShapeError(
                f"entry has no `{rc.NUANCE_HEADING}` heading, so an appended "
                f"bullet would have nowhere to go"
            )
        insert_at, body = block
        wanted = content_hash(text)
        for existing in rc.parse_journal_bullets(body):
            if content_hash(bullet_content(existing.lines)) == wanted:
                return "duplicate", existing.lines[0], entry_revision(original)
        line = render_bullet(text, actor=actor, session=session, today=today)
        # 🔴 THE INTERLEAVE POINT: after the read, before the write. See
        # `_WRITE_INTERLEAVE`.
        _WRITE_INTERLEAVE()
        # `keepends=True` splits on EXACTLY the same set `splitlines()` above
        # does, so the two lists index alike and `insert_at` means the same line
        # in both. Anything else here is an off-by-one on a file the caller
        # cannot see.
        raw_lines = text_in.splitlines(keepends=True)
        heading = raw_lines[insert_at - 1]
        # Whatever `splitlines` treated as this line's break, VERBATIM — derived
        # by asking the same function, never by guessing a newline and never by
        # stripping a hand-written character class (which would also eat trailing
        # spaces the heading line is entitled to keep).
        without_break = heading.splitlines()[0] if heading else ""
        terminator = heading[len(without_break) :]
        offset = len(
            "".join(raw_lines[:insert_at]).encode("utf-8", errors="surrogateescape")
        )
        if terminator:
            inserted = (line + terminator).encode("utf-8")
        else:
            # The heading is the last line and the file does not end in a
            # newline. Appending `\n<bullet>` keeps it that way.
            inserted = ("\n" + line).encode("utf-8")
        data = original[:offset] + inserted + original[offset:]
        _replace_bytes(path, data)
        return "appended", line, entry_revision(data)


def parse_if_match(raw: str) -> "list[str]":
    """Every entity-tag in an `If-Match` header value, unquoted and lower-cased.

    🔴 RFC 9110 §13.1.1 SAYS `If-Match` IS A **LIST**, and this used to read it
    as one opaque string. So `If-Match: "stale", "<correct>"` — the exact header
    a client sends when it holds two candidate revisions, and the exact header a
    conformant HTTP library will build from a list — compared the WHOLE string
    against a 16-character hash and answered **412 forever**. Fail-closed, and
    still a bug: a conformant client could never succeed, and a client that
    cannot succeed re-sends without the precondition.

    Splitting on commas is safe HERE and would not be in general: an entity-tag
    may in principle contain a comma inside its quotes, but this server's tags
    are `sha256(...)[:16]` and cannot. The one place that matters is stated
    rather than left as a silent assumption.

    Case is folded because `hexdigest()` is lower-case and hex is not:
    `If-Match: "3F2A…"` names the same revision as `"3f2a…"`, and refusing it was
    a precondition failing for a reason the caller cannot see.
    """
    tags: "list[str]" = []
    for item in re.split(r"\s*,\s*", raw.strip()):
        item = item.strip()
        if not item:
            continue
        if item[:2].upper() == "W/":
            item = item[2:].strip()
        tags.append(item.strip('"').lower())
    return tags


def replace_entry(
    path: Path, *, data: bytes, if_match: "Sequence[str]", scope: str, filename: str
) -> str:
    """Whole-file replace behind an `If-Match` precondition. Returns the new rev.

    `if_match` is the LIST of candidate revisions the caller named (see
    `parse_if_match`); the write proceeds if ANY of them is the current one,
    which is what RFC 9110 §13.1.1 specifies.

    🔴 THE PRECONDITION IS CHECKED UNDER THE SAME LOCK THE WRITE HAPPENS UNDER,
    and checking it outside would make it decorative: two callers could both read
    revision R, both pass, and the second would overwrite the first — which is
    the exact lost update the precondition exists to refuse.

    🔴 THE NEW BYTES ARE VALIDATED BEFORE THEY LAND, through the index loader's
    OWN mapping (`entry_mapping` + `SubsystemEntry.from_mapping`). A PUT is the
    only primitive here that can destroy content rather than add to it, so a
    body the reader would classify as MALFORMED is refused instead of written:
    otherwise one bad PUT turns a served entry into a `MALFORMED` block and the
    content it replaced is gone.

    ⚠ **ATTRIBUTION IS NOT ENFORCED HERE, AND THAT IS A DECIDED LIMIT RATHER THAN
    AN OVERSIGHT.** Criterion 4's "every appended bullet records actor and
    session" is a claim about **`POST /bullets`** (`append_bullet` +
    `render_bullet`, where the actor is a keyword no request body can populate).
    A PUT writes the caller's bytes VERBATIM: a body containing
    `- 2026-08-27: OPEN: … [cairn: someone-else/sess-…]` lands exactly as sent,
    trailer included, and this server does not check it.

    Enforcing it was considered and DECLINED. PUT exists for the whole-file
    rewrites the store needs — editing `## Pointers`, turning an `OPEN:` bullet
    into `RESOLVED <sha>:` — and per-bullet attribution enforcement would have to
    diff the old bullet set against the new one to tell a legitimate rewrite from
    a forged trailer, refusing real edits whenever that diff was wrong. The
    holder of a PUT-capable token is trusted with the whole file's contents
    already; what is NOT acceptable is CLAIMING otherwise, which is why the claim
    is scoped to POST in the README, the module docstring and here.
    `TestPUTDoesNotEnforceAttribution` pins this limit so it cannot drift into
    being assumed.
    """
    with _EntryLock(path):
        original = path.read_bytes()
        current = entry_revision(original)
        if current not in if_match:
            raise PreconditionFailed(current)
        text = data.decode("utf-8", errors="strict")
        try:
            rc.SubsystemEntry.from_mapping(
                entry_mapping(text, filename=filename, scope=scope),
                source=filename,
            )
        except rc.MalformedEntryError as exc:
            raise EntryShapeError(f"the index loader would reject these bytes: {exc}")
        _WRITE_INTERLEAVE()
        _replace_bytes(path, data)
        return entry_revision(data)


# =============================================================================
# 🔴 THE ACTION TABLES. The CLASSIFIER they read moved to `subsystem_resolver`.
#
# Four consecutive audit rounds found the same shape of defect in `_snapshot`,
# and every fix added one more predicate to a sequence:
#
#   r1  entry symlinks followed          -> refuse symlinked ENTRIES
#   r2  symlinked SCOPE dirs filtered    -> silently omitted, read as scope-empty
#   r3  `is_symlink()` before `is_dir()` -> a symlinked README 503'd everything
#   r4  `is_dir()` first                 -> a DANGLING scope link vanished,
#                                           read as scope-empty at exit 0
#
# Each round the stated predicate ("refuse a thing that IS a scope but cannot be
# served safely; skip a thing that is not one") failed to DECIDE the next input
# class, because a broken pointer is neither. Adding an arm fixes the instance;
# it does not make the rule total, so the next class falls through the same gap.
#
# So: classify the path's TYPE exhaustively, in ONE place — now
# `subsystem_resolver.classify_path`, because the index loader needs the same
# answer and a second copy of it would be the predicate-at-two-sites shape — and
# have each context map EVERY kind to an action explicitly.
#
# 🔴 THE TABLES DID **NOT** MOVE WITH IT, AND THAT IS THE DESIGN. A kind's
# action is a property of the CONTEXT, not of the path: the loader's own table
# (`subsystem_resolver._LOADER_ENTRY_ACTIONS`) is deliberately NARROWER than
# `_ENTRY_ACTIONS` below — it TAKES a symlink-to-regular-file that `/snapshot`
# refuses, because the loader has always read one and refusing it would be a
# behaviour change for every local CLI caller. All three tables are asserted
# complete by `TestClassifierIsTotal`, and an unmapped kind raises rather than
# defaulting — a fallthrough is a test failure, not a silent skip. Name-based
# rules (dotfiles, the `.md` suffix) stay SEPARATE from type, because conflating
# them is what made the dotfile and symlink rules interfere.
# =============================================================================

_ROOT_ACTIONS: dict[str, str] = {
    # 🔴 A BROKEN POINTER IS A SCOPE THAT SHOULD BE THERE AND IS NOT. Skipping
    # it is the r4 regression: `is_dir()` is False for a dangling link AND for a
    # loop, so both vanished and the scope read as `scope-empty` at exit 0.
    KIND_BROKEN_LINK: REFUSE,
    KIND_LINK_TO_DIR: REFUSE,   # a real scope we will not follow off-store
    KIND_DIRECTORY: TAKE,
    KIND_LINK_TO_FILE: SKIP,    # a file is not a scope, link or no link (r3)
    KIND_REGULAR_FILE: SKIP,    # …and neither is a plain README.md
    KIND_LINK_TO_OTHER: SKIP,
    KIND_OTHER: SKIP,
    # 🔴 REFUSE, not SKIP. We do not know whether this is a scope, so we cannot
    # claim its absence — that claim is the whole defect class.
    KIND_INDETERMINATE: REFUSE,
    KIND_ABSENT: SKIP,  # it is genuinely gone; nothing to report
}

_ENTRY_ACTIONS: dict[str, str] = {
    # Inside a scope the name has ALREADY selected for `*.md`, so anything here
    # claims to be an entry. A claim we cannot serve is refused, not skipped.
    KIND_BROKEN_LINK: REFUSE,
    KIND_LINK_TO_DIR: REFUSE,
    KIND_LINK_TO_FILE: REFUSE,
    KIND_LINK_TO_OTHER: REFUSE,
    KIND_DIRECTORY: REFUSE,     # a directory named `*.md` (r3: it 503'd on open)
    KIND_OTHER: REFUSE,         # a FIFO named `*.md` blocked `open()` forever
    KIND_REGULAR_FILE: TAKE,
    KIND_INDETERMINATE: REFUSE,
    KIND_ABSENT: SKIP,
}


# 🔴 THE ROUTE TABLE, AND THE DISPATCHER READS IT. `name -> (handler, arity)`,
# where arity counts the path components including the route name itself, so
# `recall/<scope>` is 2 and `snapshot` is 1.
#
# It is a module-level constant so a test can assert `set(API_ROUTES)` against
# its ledger without reading this file as text. Two previous guards tried that —
# a regex, then an AST walk — and each was defeated by an ordinary re-spelling
# of the router while every test stayed green. One rule, one place: a route that
# is not in this dict is not dispatched, so a route that is not in the ledger
# cannot exist.
#
# 🔴 Adding a row here is ALSO adding a public, internet-reachable endpoint.
# `TestPhaseOneScope` fails until its `ROUTES` ledger is updated to match, which
# is the point: the update is where somebody has to think about it.
API_ROUTES: dict[str, tuple[str, int]] = {
    "recall": ("_recall", 2),
    "search": ("_search", 2),
    "snapshot": ("_snapshot", 1),
}

# 🔴 THE WRITE TABLE, AND IT IS KEYED ON `(METHOD, HEAD)` RATHER THAN ON HEAD
# ALONE. `POST .../bullets` and `PUT .../<ref>` are different operations on the
# same noun, so the METHOD is part of the route identity: keying on the head and
# branching on the verb inside the handler is the shape that lets a PUT reach an
# append. `PATCH` and `DELETE` appear in no row, which is how they stay refused
# — not by a separate rejecter they could be re-bound away from.
#
# `(handler, arity, tail)`: `arity` counts path components including the head,
# `tail` is the fixed trailing components. The handler receives the components
# BETWEEN them, so `entry/<scope>/<ref>/bullets` hands over `(scope, ref)` and a
# request that spells the tail differently does not dispatch at all.
#
# 🔴 THE READ LEDGER'S RULE APPLIES HERE UNCHANGED: adding a row is adding a
# public, internet-reachable WRITE endpoint, and `TestPhaseOneScope` fails until
# its ledger names it. That guard was NOT deleted when the write path landed —
# it was converted, and the write-verb half of it still fails when a verb is
# bound outside the ledger below.
WRITE_ROUTES: dict[tuple[str, str], tuple[str, int, tuple[str, ...]]] = {
    ("POST", "entry"): ("_append_bullet", 4, ("bullets",)),
    ("PUT", "entry"): ("_replace_entry", 3, ()),
}

# How deep to walk when dating the served copy. Deliberately the SAME depth
# `seed.sh` uses for its own `remote_entries` count (`find -maxdepth 2 -name
# '*.md'`), so the two numbers are answers to the same question and a
# disagreement between them means something real rather than a units mismatch.
_FRESHNESS_MAXDEPTH = 2


def snapshot_freshness(store_root: str | Path) -> tuple[str, str]:
    """`(header_value, prose_line)` dating the copy this process is serving.

    🔴 WHY THIS EXISTS, AND IT IS NOT A NICETY. This server does not serve the
    authoritative store — it serves a COPY pushed into a PVC by `seed.sh`, and
    NOTHING syncs that copy continuously (no CronJob, no timer; measured
    2026-08-20). Yet every report it renders opens with a line of the form
    "ALL N entries in `<scope>/`, none omitted" — a COMPLETENESS assertion, and
    a truthful one *about the bytes on this disk*. Off-mesh there is no way to
    tell that disk from the source.

    That combination was measured live on 2026-08-20, four days after cutover:
    the public endpoint answered 200 with `ALL 5 entries in devrc/, none
    omitted` while the source held **9**, and one served entry was a 40-day-old
    version of a file edited that morning. Nothing in the payload, the headers
    or the status was wrong; nothing in it was current either. `claude/RULES.md`
    → "a reassuring zero from a check that could not see anything".

    So every response now dates itself. Two INDEPENDENT facts, because each
    covers the other's blind spot:

      * `seeded` — when `seed.sh` last pushed, read from a stamp file it writes.
        Answers "how old is this COPY", which is the question a caller has.
      * `newest` — the newest entry mtime on this disk, derived from the files
        themselves and owing nothing to the stamp. Answers "how old is this
        CONTENT", and survives a store seeded by any other means.

    A quiet week makes `newest` old while the copy is perfectly current; a
    forgotten re-seed makes `seeded` old while `newest` merely lags. Neither
    alone is the answer, which is why both are printed and neither is derived
    from the other.

    🔴 EVERY FAILURE IS ITS OWN NAMED STATE — never a silent omission and never
    a fabricated date, for the same reason `scope_revision` returns "unknown"
    rather than inventing a sha. A missing stamp says `seeded=UNSTAMPED`, an
    unreadable one says `seeded=UNREADABLE`, and a walk that hit an error says
    `newest=UNREADABLE` — each distinguishable from the genuinely empty store,
    which says `newest=NONE entry-files=0`. An absent block would read as "this
    is the source"; that is the exact confusion the block exists to remove.
    """
    root = Path(store_root)

    seeded = "UNSTAMPED"
    try:
        text = (root / SEED_STAMP_NAME).read_text(encoding="utf-8").strip()
        seeded = text.splitlines()[0].strip() if text else "UNREADABLE"
    except FileNotFoundError:
        seeded = "UNSTAMPED"
    except OSError:
        seeded = "UNREADABLE"

    newest: float | None = None
    count = 0
    walk_failed = False

    def _walk_error(_exc: OSError) -> None:
        nonlocal walk_failed
        walk_failed = True

    # 🔴 `os.walk(onerror=...)`, NOT `Path.rglob`, AND THAT IS THE WHOLE POINT.
    # `rglob` swallows a permission error and yields nothing, so an UNREADABLE
    # scope is indistinguishable from an EMPTY one — it would report
    # `newest=NONE entry-files=0`, a confident zero from a walk that saw
    # nothing. That is the precise failure this function was written to stop it
    # committing itself, and the first draft committed it: caught by
    # `test_an_UNREADABLE_scope_is_UNREADABLE_not_an_empty_store`, which passed
    # only after this rewrite. `os.walk` is the one that can be TOLD to report.
    try:
        scopes = sorted(
            p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")
        )
    except OSError:
        scopes, walk_failed = [], True

    for scope in scopes:
        for dirpath, _dirnames, filenames in os.walk(scope, onerror=_walk_error):
            # Depth relative to the store root: `<scope>/<entry>.md` is 2.
            depth = len(Path(dirpath).relative_to(root).parts) + 1
            if depth > _FRESHNESS_MAXDEPTH:
                continue
            for name in filenames:
                if not name.endswith(".md"):
                    continue
                try:
                    mtime = os.stat(os.path.join(dirpath, name)).st_mtime
                except OSError:
                    walk_failed = True
                    continue
                count += 1
                if newest is None or mtime > newest:
                    newest = mtime

    if walk_failed:
        newest_text = "UNREADABLE"
    elif newest is None:
        newest_text = "NONE"
    else:
        newest_text = (
            datetime.fromtimestamp(newest, timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ")
        )

    header = f"seeded={seeded} newest={newest_text} entry-files={count}"
    prose = (
        f"🔴 SNAPSHOT, NOT THE SOURCE — seeded={seeded} newest-entry={newest_text} "
        f"entry-files={count}. This host serves a COPY of the authoritative store, "
        f"pushed by `seed.sh`; nothing syncs it continuously, so it can be "
        f"arbitrarily behind and it CANNOT KNOW BY HOW MUCH. The "
        f"\"none omitted\" below is true of THIS DISK and says nothing about the "
        f"source. Before trusting an absence — a missing entry, a missing badge, "
        f"a zero — re-run the read against the local store, or re-seed. "
        f"UNSTAMPED/UNREADABLE/NONE each mean the stated fact could not be "
        f"established, never that it is fine."
    )
    return header, prose


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
    expected_tokens: "tuple[TokenRecord, ...]" = ()
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
    # 🔴 THE PER-REQUEST SCOPE ALLOWLIST, AND ITS RESET VALUE IS `()`, NOT `None`.
    #
    # `None` is the UNRESTRICTED sentinel everywhere else in this file
    # (`load_store`, `scope_revision`, `TokenRecord.scopes`), which makes it the
    # one value this field must never default to: a route reached without a
    # successful `authorize` — today impossible, tomorrow one refactor away —
    # would then see the whole store. An empty tuple is the fail-closed
    # direction: nothing is visible until a matched record says otherwise, and a
    # legacy record is the ONLY thing that can put `None` here.
    #
    # ⚠ ALL FIVE `= ()` SITES ARE EQUIVALENT MUTANTS TODAY, RECORDED RATHER THAN
    # LEFT AS UNEXPLAINED SURVIVORS — the same treatment `send_error`'s
    # `close_connection` and its reset already get, and for the same reason: an
    # unexplained survivor reads either as a missing test or as a guard somebody
    # may delete. MEASURED, one site at a time, each against the FULL
    # `test_subsystem_store_api.py` suite: substituting `= None` at this
    # declaration or at any of the four resets (`_write`, `send_error`,
    # `_request_path`'s `ValueError` branch, `_handle`) leaves 375/375 passing.
    #
    # WHY, precisely — and it is a REACHABILITY fact, not a coverage gap: every
    # entry point into this handler assigns this field before any route can read
    # it. `_handle` and `_write` reset then either `authorize` (which
    # overwrites it from the matched record) or refuse and return; `send_error`
    # and the `_request_path` branch answer 401 and never reach a read route.
    # There is no path on which the RESET VALUE is what a route observes, so no
    # test can distinguish the two — writing one would mean inventing a caller
    # that does not exist.
    #
    # 🔴 SO THE COMMENTS BELOW DESCRIBE A DIRECTION, NOT A LIVE GUARD. They are
    # kept because the direction is the whole design — the day a refactor adds a
    # route that runs before `authorize`, `()` serves nothing and `None` serves
    # the entire store — but nobody should read them as "this is pinned by a
    # test". It is not, it cannot be while the resets are unreachable, and
    # saying so here is cheaper than a future reader re-deriving it from a
    # green sweep.
    _visible_scopes: "tuple[str, ...] | None" = ()
    # The matched credential's name, for the audit line. `-` until one matches.
    _identity: str | None = None

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
            # 🔴 ADDITIVE, AND `token=` IS UNTOUCHED. The fingerprint is what
            # makes an overlap rotation checkable and nothing may be derived
            # from the identity instead: two rows can hold one identity's
            # current and previous credential, and only `token=` tells them
            # apart. `identity=` answers the different question phase 3 adds —
            # WHOSE request this was, and therefore which allowlist applied.
            f"identity={audit_field(self._identity or '-', limit=MAX_IDENTITY_CHARS)} "
            f"auth={'ok' if self._token_fp else 'fail'} "
            f"result={int(result)} status={audit_field(status, limit=32)}"
        )

    # --- methods ----------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        self._handle()

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle()

    def _drain_body(self) -> None:
        """Read and discard the entity body. See `_consume_body`."""
        self._consume_body(keep=False)

    def _consume_body(self, *, keep: bool) -> "tuple[bool, bytes]":
        """🔴 READ THE ENTITY BODY. THIS IS A SMUGGLING FIX.

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

        🔴 THE WRITE PATH READS ITS BODY THROUGH THIS FUNCTION AND NO OTHER, and
        that is why it returns bytes instead of a second reader existing beside
        it. Every guard above — the `Transfer-Encoding` refusal, the exactly-one
        `Content-Length`, the negative length, the byte cap, the wall-clock
        deadline, `read1` — was written against a measured smuggling or
        thread-pinning attack, and a write handler that read `rfile` itself would
        be a second framing parser with none of them. `keep=False` (the drain)
        and `keep=True` (a write) differ in whether the chunks are retained and
        in nothing else.

        Returns `(framing_understood, body)`. `framing_understood` is False for
        every refusal above, so a caller that NEEDS the body can tell "there was
        no body" (`(True, b"")`) from "this framing was refused" — a distinction
        the drain never had to make and a write cannot do without.
        """
        chunks: list[bytes] = []
        headers = getattr(self, "headers", None)
        if headers is None:
            return False, b""
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
            return False, b""
        if headers.get_all("Content-Length") is None:
            return True, b""
        # 🔴 EXACTLY ONE Content-Length, via the SAME predicate `client_ip` uses.
        # A bare `.get()` takes the first value, so `Content-Length: 0` followed
        # by `Content-Length: 154` drained nothing and smuggled the body — on a
        # 200, where the close-on-non-200 belt does not apply. Measured.
        raw_length = sole_header(headers, "Content-Length")
        if raw_length is None:
            self.close_connection = True
            return False, b""
        try:
            length = int(raw_length)
        except ValueError:
            # A malformed length means the framing is already unknowable. Do not
            # guess; `_respond` will close the connection.
            self.close_connection = True
            return False, b""
        if length < 0:
            # Negative lengths are not "no body" — they are a caller telling two
            # different stories to two different parsers.
            self.close_connection = True
            return False, b""
        if length == 0:
            return True, b""
        if length > MAX_DRAIN_BYTES:
            # Too big to swallow politely. Closing is the only safe answer —
            # leaving it queued is exactly the desync above.
            self.close_connection = True
            return False, b""
        # 🔴 BOUNDED IN TIME, NOT JUST IN BYTES. `timeout` is per-recv, so a
        # caller dripping one byte every 10s held a thread for as long as it
        # liked — measured at 60s for a 6-byte body, i.e. months for a 1 MiB
        # one. The read loop this fix introduced needed its own deadline.
        deadline = time.monotonic() + DRAIN_DEADLINE_S
        remaining = length
        while remaining > 0:
            if time.monotonic() > deadline:
                self.close_connection = True
                return False, b""
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
                return False, b""
            remaining -= len(chunk)
            if keep:
                chunks.append(chunk)
        return True, b"".join(chunks)

    def _write(self) -> None:
        """🔴 THE ONE DOOR EVERY MUTATING VERB GOES THROUGH — including the ones
        that have no write route and are therefore still refused.

        THIS FUNCTION IS THE READ-ONLY GUARD BEING BROKEN ON PURPOSE (criterion
        7). It was `_reject_write`, whose whole body was "authenticate, then 405
        everything". What changed is that a request matching a row of
        `WRITE_ROUTES` now dispatches instead; EVERYTHING ELSE — every verb with
        no row, every path with no row — takes the identical 405 tail below, so
        `PATCH`, `DELETE` and a `POST` at a read route behave exactly as they did
        before this branch. That is deliberate: converting the guard must not
        quietly widen it.

        🔴 THE PROLOGUE IS UNCHANGED AND IS NOT NEGOTIABLE. It used to answer 405
        before the client-IP and lockout checks, which made it a free,
        unauthenticated, UNMETERED channel: 31 anonymous POSTs with no token and
        no `CF-Connecting-IP` produced 31 audit lines and counted for nothing —
        enough to drown the Loki auth-fail alert this design relies on. Writes go
        through the SAME `_identify_and_meter` and the SAME `authorize` as reads,
        so nothing about the write path is a second, weaker door.

        🔴 ORDER: ROUTE FIRST, THEN THE LEGACY REFUSAL. A bare (unmapped) token
        may not write — it has no identity, so there is no actor to attribute a
        bullet to — but that refusal is about the OPERATION, not about the verb.
        Refusing it before the route lookup would answer 403 to a legacy caller's
        `POST /api/v1/snapshot`, which is a wrong-method, and would change an
        answer this branch is supposed to leave alone.
        """
        self._client_ip = None
        self._token_fp = None
        self._peer_trusted = None
        # 🔴 RESET TO `()` — nothing visible — NOT to the unrestricted `None`.
        # See the class-level declaration: this is the fail-closed direction,
        # and it is the reset value precisely because a reset runs on paths
        # that never reach `authorize`. ⚠ EQUIVALENT MUTANT — `= None` here
        # passes the full suite, because nothing reads the field before it is
        # reassigned. Recorded at the declaration; not pinned by any test.
        self._visible_scopes = ()
        self._identity = None
        path = self._request_path()
        if path is None:
            return
        # 🔴 READ BEFORE AUTH, exactly as the drain did. The body has to leave the
        # socket whether or not the request is going to be served, or an
        # unauthenticated POST desynchronises the connection — see
        # `_consume_body`. Holding the bytes costs at most `MAX_DRAIN_BYTES`,
        # which is the bound the drain already enforced.
        framed, body = self._consume_body(keep=True)
        if not self._identify_and_meter(path):
            return
        try:
            # 🔴 AUTHENTICATE BEFORE ANSWERING ANYTHING. Otherwise an anonymous
            # POST flood is still free: identified, not locked out, and never
            # counted — 405 after 405 with nothing charged. A write attempt
            # with no valid credential is not a "wrong method", it is an
            # unauthorised request, and it is answered and charged as one.
            # 🔴 ONE MATCH, THREE FACTS. The fingerprint, the identity and the
            # scope allowlist all come off the SAME record `authorize` returned,
            # so no route can be authenticated against one credential and
            # authorised against another.
            record = authorize(
                self.headers.get("Authorization"), self.expected_tokens
            )
            self._token_fp = record.fingerprint
            self._identity = record.identity
            self._visible_scopes = record.scopes
        except _Rejected:
            self._refuse(path, self._count_failure(self.limiter, self._client_ip))
            return

        route = self._write_route(path)
        if route is not None:
            handler_name, parts, arity, tail_len = route
            if record.is_legacy:
                # 🔴 A LEGACY (BARE, UNMAPPED) TOKEN MAY NOT WRITE, AND THIS IS
                # THE DELIBERATE COST OF THE MIGRATION RATHER THAN AN OVERSIGHT.
                # Criterion 4 says every appended bullet records an ACTOR and a
                # SESSION; a bare row's identity is the constant `legacy`, which
                # names no holder, so there is no actor to derive and the
                # guarantee cannot be met. Attributing to `legacy` would put a
                # word in the store that reads like a person and is not one.
                # READS from a legacy token are unchanged — this refusal is on
                # the write routes only.
                #
                # It is answered to an AUTHENTICATED caller, so it may say what
                # it did wrong; it discriminates nothing about the store, because
                # a legacy row is UNRESTRICTED and this answer is the same for
                # every scope, existing or not.
                self._respond(
                    403,
                    b"forbidden: this credential has no identity, so a bullet "
                    b"written with it could not record an actor. Give the holder "
                    b"a `<token> <identity> <scopes>` row\n",
                    headers={"X-Store-Status": "legacy-cannot-write"},
                )
                self._audit(path, 403, "legacy-cannot-write")
                return
            if any(not SAFE_PATH_COMPONENT.fullmatch(p) for p in parts):
                # The same refusal the read router makes, for the same reason:
                # these components reach the filesystem. See `_handle`.
                self._respond(
                    400, b"bad request: invalid path component\n",
                    headers={"X-Store-Status": "bad-request"},
                )
                self._audit(path, 400, "bad-request")
                return
            if not framed:
                # `_consume_body` refused the framing (chunked, a duplicated or
                # negative `Content-Length`, an over-long or slow body). The
                # connection is already marked for close; the caller is told, and
                # NOTHING is written from bytes this server could not frame.
                self._respond(
                    400, b"bad request: unreadable request body\n",
                    headers={"X-Store-Status": "bad-request"},
                )
                self._audit(path, 400, "bad-request")
                return
            # 🔴 THE SLICE COMES FROM THE TABLE, NOT FROM THE REQUEST. It read
            # `parts[1 : len(parts) - tail_len]`, and `len(parts)` is
            # attacker-controlled: the handler's ARGUMENT COUNT was therefore a
            # function of the URL, correct only because `_write_route`'s
            # `len(parts) != arity` check happened to reject the mismatch first.
            # Proven load-bearing by relaxing that check — `PUT
            # /api/v1/entry/<scope>/<ref>/bullets` (4 parts) then matched the PUT
            # row (arity 3) and passed FIVE arguments to a four-parameter method:
            # unhandled `TypeError`, connection dropped, no response, no
            # `X-Store-Status`, no audit line. `arity` is a constant from
            # `WRITE_ROUTES`, so a wrong argument count is now structurally
            # impossible regardless of what any other check does.
            middle = parts[1 : arity - tail_len]
            try:
                getattr(self, handler_name)(*middle, body, path)
            except Exception:  # noqa: BLE001 — deliberate backstop, see below
                # 🔴 A METERED REQUEST MUST NEVER VANISH FROM THE AUDIT TRAIL.
                # An unhandled exception here drops the connection with no
                # response, no status header and NO AUDIT LINE — the mirror image
                # of the unmetered-405 channel this function's docstring says it
                # closed, and reachable by any token holder. Two separate
                # defects landed in exactly this shape (a `RecursionError` out of
                # `json.loads`, and the `TypeError` above), which is what makes a
                # backstop worth its cost.
                #
                # 🔴 THIS IS NOT A SUBSTITUTE FOR FIXING EITHER OF THEM AT ITS
                # OWN SITE, and both are fixed at their own site — the
                # `RecursionError` in `_append_bullet`'s JSON catch, the argument
                # count on the line above. A backstop that made the real fixes
                # look unnecessary would be masking, not surfacing.
                #
                # `Exception`, NOT `BaseException`: `KeyboardInterrupt` and
                # `SystemExit` must still terminate the process.
                #
                # The BODY IS A CONSTANT and carries no exception text. This is
                # internet-reachable; an exception string names paths, values and
                # types, and would be a new leak channel opened by the very guard
                # meant to close one. The traceback goes to the pod log only.
                traceback.print_exc(file=sys.stderr)
                self._respond(
                    500,
                    b"internal error\n",
                    headers={"X-Store-Status": "internal-error"},
                )
                self._audit(path, 500, "internal-error")
            return

        # 🔴 THE UNCHANGED TAIL. `read-only` is a claim about THIS ROUTE, not
        # about the server: `/api/v1/recall/<scope>` and `/api/v1/snapshot` have
        # no write verb and never will, and that is what this answers.
        self._respond(405, b"read-only\n", headers={"Allow": "GET, HEAD"})
        self._audit(path, 405, "method-not-allowed")

    def _write_route(self, path: str) -> "tuple[str, list[str], int, int] | None":
        """Match one request against `WRITE_ROUTES`, or `None`.

        Returns `(handler, parts, arity, tail_len)`. The ARITY is returned — not
        left implicit in `len(parts)` — so the dispatcher can size the handler's
        argument list from the TABLE. See the slice in `_write`.

        Split out so the dispatcher is one table lookup rather than a nest of
        conditions inside `_write`, and so the arity and the fixed TAIL are
        checked in the same place the row declares them — `if len(parts) == arity`
        mutated to `>=` once survived 318 tests on the read side.
        """
        if not path.startswith(API_PREFIX):
            return None
        parts = [p for p in path[len(API_PREFIX) :].split("/") if p]
        if not parts:
            return None
        row = WRITE_ROUTES.get((self.command, parts[0]))
        if row is None:
            return None
        handler_name, arity, tail = row
        if len(parts) != arity:
            return None
        if tail and tuple(parts[arity - len(tail) :]) != tail:
            return None
        return handler_name, parts, arity, len(tail)

    do_POST = do_PUT = do_PATCH = do_DELETE = _write  # noqa: N815

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
            and counted for nothing — the same free channel `_write` (then
            named `_reject_write`) had just been reordered to close, widened
            to every other verb.

        So: `getattr` for everything, because on this path NOTHING is guaranteed
        to exist; meter when there are headers to identify a client from; and
        when there are not, refuse AND close, which bounds an unidentifiable
        caller to one request per TCP handshake.
        """
        self._client_ip = None
        self._token_fp = None
        self._peer_trusted = None
        # 🔴 RESET TO `()` — nothing visible — NOT to the unrestricted `None`.
        # See the class-level declaration: this is the fail-closed direction,
        # and it is the reset value precisely because a reset runs on paths
        # that never reach `authorize`. ⚠ EQUIVALENT MUTANT — `= None` here
        # passes the full suite, because nothing reads the field before it is
        # reassigned. Recorded at the declaration; not pinned by any test.
        self._visible_scopes = ()
        self._identity = None
        # ⚠ THAT RESET IS AN EQUIVALENT MUTANT ON THIS PATH, RECORDED RATHER
        # THAN LEFT AS AN UNEXPLAINED SURVIVOR. A no-selector sweep showed
        # deleting it changes nothing observable, and the reason is worth
        # knowing: either `headers` is absent, and the branch below answers with
        # `_peer_trusted` still `None` (`peer=-`, which is now asserted), or
        # `headers` is present and `_identify_and_meter` RECOMPUTES the field a
        # few lines down. Kept because a future edit that stops recomputing must
        # not silently inherit the previous request's value.
        #
        # 🔴 SEPARATE, PRE-EXISTING, AND NOT FIXED HERE — reported instead,
        # because it predates this branch and fixing it means changing
        # `_raw_path`'s base behaviour: on a KEEP-ALIVE connection whose SECOND
        # request line is malformed, `self.headers` and `self.path` are still
        # the FIRST request's instance attributes. Measured, one connection:
        #
        #   ip=203.0.113.7 peer=trusted … path=/api/v1/recall/sc auth=ok  result=200
        #   ip=203.0.113.7 peer=trusted … path=/api/v1/recall/sc auth=fail result=401
        #                                      ^ the PREVIOUS request's path,
        #                                        and its CF-Connecting-IP
        #
        # The request is still refused, so this is log FIDELITY, not a bypass —
        # but it attributes a malformed request to whatever the last good one
        # claimed, on the log this design says is "the only thing that can
        # answer it" if the store is ever suspected of leaking.
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
            # 🔴 RESET TO `()` — nothing visible — NOT to the unrestricted `None`.
            # See the class-level declaration: this is the fail-closed direction,
            # and it is the reset value precisely because a reset runs on paths
            # that never reach `authorize`. ⚠ EQUIVALENT MUTANT — `= None` here
            # passes the full suite, because nothing reads the field before it is
            # reassigned. Recorded at the declaration; not pinned by any test.
            self._visible_scopes = ()
            self._identity = None
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
        # 🔴 RESET TO `()` — nothing visible — NOT to the unrestricted `None`.
        # See the class-level declaration: this is the fail-closed direction,
        # and it is the reset value precisely because a reset runs on paths
        # that never reach `authorize`. ⚠ EQUIVALENT MUTANT — `= None` here
        # passes the full suite, because nothing reads the field before it is
        # reassigned. Recorded at the declaration; not pinned by any test.
        self._visible_scopes = ()
        self._identity = None
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
            # 🔴 ONE MATCH, THREE FACTS. The fingerprint, the identity and the
            # scope allowlist all come off the SAME record `authorize` returned,
            # so no route can be authenticated against one credential and
            # authorised against another.
            record = authorize(
                self.headers.get("Authorization"), self.expected_tokens
            )
            self._token_fp = record.fingerprint
            self._identity = record.identity
            self._visible_scopes = record.scopes
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
            # 🔴 DISPATCH FROM THE DECLARED TABLE. Adding a route means adding a
            # row to `API_ROUTES`, because that table IS the dispatcher — there
            # is no second place to put one.
            #
            # This replaces a test that PARSED THIS SOURCE for route names, and
            # it replaces it because that approach was defeated twice. v2 used a
            # regex and missed `parts[0] == "raw_dump"` (underscore). v3 walked
            # the AST and still missed `head = parts[0]; head == "x"` and
            # `parts[0] in NAME` — one ordinary refactor away, with the literal
            # sitting right there. Every iteration was a guard on the SPELLING
            # of the router rather than on the router. A table the code actually
            # dispatches from cannot be out of date with the code, so the test
            # is now `set(API_ROUTES) == ledger` and spelling is not a variable.
            route = API_ROUTES.get(parts[0]) if parts else None
            if route is not None:
                handler_name, arity = route
                if len(parts) == arity:
                    getattr(self, handler_name)(
                        *(parts[1:arity]), params
                    )
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
        # 🔴 THE STAMP GOES IN THE BODY, NOT ONLY THE HEADER, AND IT GOES FIRST.
        # `_serve_report` is the one place every report — recall AND search —
        # passes through, so stamping here cannot be forgotten by a future route
        # (RULES.md: one rule, one place). A header alone would not do: the
        # measured failure was an AGENT reading the rendered text and believing
        # its "none omitted" line, and an agent that pipes the body never sees a
        # header. It precedes the report because a caveat printed after the
        # thing it qualifies has already been believed.
        fresh_header, fresh_prose = snapshot_freshness(self.store_root)
        body = (fresh_prose + "\n\n" + text + "\n").encode("utf-8")
        self._respond(
            200,
            body,
            headers={
                "X-Store-Status": status,
                "X-Store-Exit": str(code),
                # 🔴 GATED ON THE CALLER'S ALLOWLIST. This one header does NOT
                # come from the narrowed index — it is read off
                # `<store>/<scope>/.git/HEAD` — so it is the one place a refused
                # scope could still be told apart from an absent one. See
                # `scope_revision`.
                "X-Store-Revision": scope_revision(
                    self.store_root, scope, visible_scopes=self._visible_scopes
                ),
                "X-Store-Snapshot": fresh_header,
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
            visible_scopes=self._visible_scopes,
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
            # 🔴 AND THIS IS WHAT MAKES `?all_scopes=1` SAFE. That flag names no
            # scope, so a per-scope refusal check has nothing to refuse — it
            # would search the CONTENT of every scope in the store. Narrowing
            # the index makes "all scopes" mean "all the caller's scopes".
            visible_scopes=self._visible_scopes,
        )
        self._serve_report(
            urlsplit(self.path).path,
            scope,
            report.status,
            report.label,
            report.malformed,
            rc.render_search(report),
        )

    def _snapshot(self, params: dict[str, list[str]]) -> None:
        """Ship the store's entry files as a tar, so a client can hold a CACHE.

        🔴 WHY A SECOND READ ROUTE EXISTS AT ALL, given `/recall` already
        renders. `/recall` returns a RENDERED digest for one query. A client that
        can only replay rendered answers is online-only in practice: the laptop
        suspends and travels, and `/resume` must not turn into an error or — far
        worse — an empty screen when the pod is unreachable (§2d, which states
        outright that `subsystem_recall.py` keeps its no-network property and the
        WRAPPER owns the network). Serving the files lets the client run the
        unmodified local reader against its own copy, so an offline query it has
        never asked before still answers.

        🔴 THIS ROUTE IS READ-ONLY AND STAYS READ-ONLY. It adds a GET; it adds
        no write verb, and `WRITE_ROUTES` holds no row for `snapshot` — so
        every mutating verb here is still the 405 it always was, which
        `test_the_snapshot_route_added_NO_write_verb` pins behaviourally.
        (This paragraph used to say the write ALIASES were untouched. Phase 3
        criteria 4-7 rebound them to `_write`; what is untouched is this
        route's answer, which is the claim that was actually load-bearing.)

        🔴 MTIMES ARE PRESERVED, AND THAT IS LOAD-BEARING, NOT TIDINESS. The
        reader orders the index "NEWEST-FIRST by entry-file mtime". A tar built
        with normalised mtimes — the usual move for reproducibility — would
        reorder every digest rendered from the extracted copy, so the client's
        output would differ from the pod's for content that is byte-identical.
        That failure is invisible: no error, no missing entry, just a different
        order that reads as a stale cache. uid/gid/uname/gname/mode ARE
        normalised, since none of them reaches the reader.

        What is shipped is exactly what the reader consumes — `<scope>/<x>.md`
        at depth 2 — plus the seed stamp, so the client can date the copy for
        itself instead of trusting a header. Nothing else: no `.git`, no dot
        directories, no deeper paths.
        """
        root = Path(self.store_root)
        wanted = params.get("scope")
        scope_filter = wanted[-1] if wanted else None
        if scope_filter is not None and not SAFE_PATH_COMPONENT.fullmatch(scope_filter):
            # Same refusal as a bad path component: this value reaches the
            # filesystem, and the caller is authenticated so it may be told.
            self._respond(
                400,
                b"bad request: invalid scope\n",
                headers={"X-Store-Status": "bad-request"},
            )
            self._audit(urlsplit(self.path).path, 400, "bad-request")
            return

        # 🔴 THE FOURTH ENUMERATION CHANNEL, AND THE ONLY ONE `load_store` CANNOT
        # CLOSE. This route never builds an index — it walks the store root
        # directly — so the narrowing that covers `/recall` and `/search` does
        # not reach here at all. Without this line a caller allowed one scope
        # could download every scope's entry FILES, which is a wider leak than
        # any of the three channels the index filter closes.
        #
        # Applied as a filter on the CANDIDATE list rather than on the tar
        # members, so an out-of-allowlist scope is never classified, never
        # opened, and can never reach the `unreadable` list either — a refused
        # scope must not be able to 503 somebody else's snapshot, which is both
        # a leak and a denial of service.
        # 🔴 THE SAME PREDICATE THE INDEX LOADER USES, from the same function.
        # Three sites now narrow by allowlist — here, `load_index` (what is
        # OPENED) and `load_store` (the result shape) — and open-coding the fold
        # at each of them is how they come to disagree about `Kelp_Forest`.
        allowed = rc.visible_scope_set(self._visible_scopes)
        try:
            candidates = sorted(
                p
                for p in root.iterdir()
                if not p.name.startswith(".")
                and (scope_filter is None or p.name == scope_filter)
                and (allowed is None or rc.normalize_ref(p.name) in allowed)
            )
        except OSError as exc:
            # 🔴 The store was NOT read. Same state, same code and the same
            # reasoning as `_recall`'s: an empty tar and an unreadable store must
            # never render alike, because one of them is a lie.
            self._respond(
                503,
                f"store unreadable: {exc}\n".encode("utf-8"),
                headers={"X-Store-Status": "store-unreachable", "X-Store-Exit": "3"},
            )
            self._audit(urlsplit(self.path).path, 503, "store-unreachable")
            return

        def _member(path: Path, arcname: str) -> tarfile.TarInfo:
            st = path.stat()
            info = tarfile.TarInfo(arcname)
            info.size = st.st_size
            # 🔴 SUB-SECOND PRECISION IS PART OF "PRESERVED", and `int()` here
            # was a real bug caught by a test, not a hypothetical. Entries
            # written in the same second tie on a whole-second mtime, so the
            # reader falls back to its ref tie-break and the extracted copy
            # orders its index DIFFERENTLY from the source — same bytes, same
            # count, different order, no error. That is why the tar is opened
            # PAX_FORMAT below: ustar stores mtime as integer octal seconds and
            # structurally cannot carry the fraction.
            info.mtime = st.st_mtime  # float — see PAX_FORMAT below
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.type = tarfile.REGTYPE
            return info

        # 🔴 AN UNREADABLE SCOPE IS NOT AN EMPTY ONE, AND `Path.glob` CANNOT TELL
        # YOU WHICH IT SAW. `glob`/`iterdir` swallow `PermissionError` and yield
        # nothing, so the first version of this handler answered 200 with
        # `X-Store-Exit: 0` for a `chmod 000` scope, the tar silently omitted it,
        # and the client rendered `scope-empty — reached the store; nothing
        # recorded`. That is the exact lie this route's client exists to prevent,
        # and `snapshot_freshness` above already carries a long comment about it
        # (it uses `os.walk(onerror=...)` for this reason). Caught by an audit,
        # reproduced end-to-end, not hypothetical.
        #
        # So every directory read is EXPLICIT and every failure is COLLECTED —
        # never skipped — and any failure at all makes the whole response a 503
        # carrying the same `store-unreachable` state `_recall` uses. A partial
        # snapshot served as 200 is worse than no snapshot.
        unreadable: list[str] = []
        selected: list[tuple[Path, str]] = []
        scopes: list[Path] = []
        for candidate in candidates:
            # One classification, one mapping, no ordering to get wrong.
            kind = classify_path(candidate)
            action = action_for(kind, _ROOT_ACTIONS)
            if action == TAKE:
                scopes.append(candidate)
            elif action == REFUSE:
                unreadable.append(f"{candidate.name}/: {kind} refused")

        for scope in scopes:
            try:
                # 🔴 NAME RULES ARE SEPARATE FROM TYPE RULES, and keeping them
                # separate is the point. `*.md` and the dotfile skip decide
                # whether a path CLAIMS to be an entry; `classify_path` decides
                # whether the claim can be served. Conflating them is what made
                # an Emacs lock file — `.#entry.md`, a DANGLING SYMLINK whose
                # name ends in `.md` — 503 the entire store for every caller
                # because one buffer was open.
                names = sorted(
                    p
                    for p in scope.iterdir()
                    if p.name.endswith(".md") and not p.name.startswith(".")
                )
            except OSError as exc:
                unreadable.append(f"{scope.name}/: {exc.strerror or exc}")
                continue
            for entry in names:
                kind = classify_path(entry)
                action = action_for(kind, _ENTRY_ACTIONS)
                if action == REFUSE:
                    unreadable.append(f"{scope.name}/{entry.name}: {kind} refused")
                    continue
                if action != TAKE:
                    continue
                selected.append((entry, f"{scope.name}/{entry.name}"))

        if unreadable:
            body = ("store unreadable:\n  " + "\n  ".join(unreadable) + "\n").encode()
            self._respond(
                503,
                body,
                headers={"X-Store-Status": "store-unreachable", "X-Store-Exit": "3"},
            )
            self._audit(urlsplit(self.path).path, 503, "store-unreachable")
            return

        buf = io.BytesIO()
        count = 0
        try:
            # 🔴 GZIPPED, because PAX is expensive per member and these members
            # are tiny. MEASURED: 305 entries totalling 62,821 bytes of markdown
            # produced a 634,880-byte uncompressed tar — **10.1x the payload** —
            # since PAX spends ~2 KB of headers on a ~200-byte entry. The whole
            # tar is held in memory here and again on the client, and a timer
            # re-transfers the entire store every tick, so the multiplier is the
            # thing that matters, not the absolute size. The client opens with
            # mode="r", which auto-detects, so this needs no client change.
            with tarfile.open(
                fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT
            ) as tar:
                stamp = root / SEED_STAMP_NAME
                if stamp.is_file() and not stamp.is_symlink():
                    with stamp.open("rb") as fh:
                        tar.addfile(_member(stamp, SEED_STAMP_NAME), fh)
                for entry, arcname in selected:
                    with entry.open("rb") as fh:
                        tar.addfile(_member(entry, arcname), fh)
                    count += 1
        except OSError as exc:
            self._respond(
                503,
                f"store unreadable: {exc}\n".encode("utf-8"),
                headers={"X-Store-Status": "store-unreachable", "X-Store-Exit": "3"},
            )
            self._audit(urlsplit(self.path).path, 503, "store-unreachable")
            return

        fresh_header, _prose = snapshot_freshness(self.store_root)
        self._respond(
            200,
            buf.getvalue(),
            content_type="application/gzip",
            headers={
                "X-Store-Status": "snapshot",
                "X-Store-Exit": "0",
                "X-Store-Snapshot": fresh_header,
                # The SERVER's count of what it put in. The client compares its
                # own extracted count against this and refuses a mismatch —
                # `scripts/cairn::install_snapshot`. (An earlier version of this
                # comment claimed the disagreement was "visible" while NOTHING
                # compared them; the comparison now exists. A comment is a claim
                # too.) ⚠ On a `?scope=` request the counts still describe the
                # same filtered set, so the check holds there as well.
                # number nobody can compare against.
                "X-Store-Entries": str(count),
            },
        )
        self._audit(urlsplit(self.path).path, 200, "snapshot")

    # --- write handlers (criteria 4-6) ------------------------------------------

    def _not_found(self, path: str, status: str) -> None:
        """🔴 ONE 404 FOR EVERY WAY A WRITE TARGET CAN FAIL TO RESOLVE, and the
        uniformity is criterion 3's enumeration property applied to writes.

        A scope OUTSIDE the caller's allowlist, a scope that has never existed, a
        ref that resolves to nothing and an entry the loader could not parse all
        answer these exact bytes with these exact headers. The read path closed
        this at the INDEX — `load_store(visible_scopes=…)` simply does not
        contain a refused scope, so `index.entries()` raises the same
        `UnknownScopeError` it raises for one that was never there — and the
        write path reuses that narrowing rather than adding a second "is this
        scope yours" check with its own answer. A refusal that is distinguishable
        from an absence is an enumeration API, on a write verb just as on a read.

        The WIRE does not discriminate; the audit LOG does, via `status`, in a
        place the caller cannot read.
        """
        self._respond(
            404, b"not found\n", headers={"X-Store-Status": "not-found"}
        )
        self._audit(path, 404, status)

    def _resolve_writable(self, scope: str, ref: str, path: str) -> Any:
        """The entry a write targets, or `None` having already answered.

        Loads through `rc.load_store` — the SAME function both read routes use,
        with the SAME allowlist — so there is exactly one place that decides what
        a caller may see and it cannot come to disagree with itself.
        """
        try:
            _store, index = rc.load_store(
                self.store_root,
                verb="written",
                visible_scopes=self._visible_scopes,
            )
            entry, _tier = rc.resolve_ref_tiered(ref, index, scope)
        except rc.UnknownScopeError:
            # Refused OR absent — indistinguishable by construction, see above.
            self._not_found(path, "scope-unknown")
            return None
        except rc.AmbiguousRefError as exc:
            # The caller is authenticated AND may see this scope, so it may be
            # told: an ambiguous ref is a caller error with a defined remedy, and
            # it names only entries this caller can already read.
            self._respond(
                400,
                f"bad request: {exc}\n".encode("utf-8"),
                headers={"X-Store-Status": "bad-request"},
            )
            self._audit(path, 400, "bad-request")
            return None
        except (rc.StoreMissingError, rc.EntryUnreadableError) as exc:
            # 🔴 THE STORE WAS NOT READ. Never a 404 — "I could not look" and
            # "it is not there" are the four-state rule's two states and a write
            # must not conflate them any more than a read may.
            self._respond(
                503,
                f"{exc}\n".encode("utf-8"),
                headers={"X-Store-Status": "store-unreachable", "X-Store-Exit": "3"},
            )
            self._audit(path, 503, "store-unreachable")
            return None
        if entry is None:
            # No such ref — and a MALFORMED entry lands here too, because
            # `load_store` collects it onto `index.malformed` rather than into
            # `entries`. That is the fail-closed direction: this writer will not
            # edit a file the reader cannot parse.
            self._not_found(path, "ref-unknown")
            return None
        return entry

    def _entry_path(self, entry: Any) -> Path:
        """Located from the LOADER's own scope + filename, never rebuilt from the
        ref — `<slug>.<kind>.md` and `<slug>.md` are different files and only the
        loader knows which one this entry came from (`rc.read_entry` says the
        same thing for the read side)."""
        return Path(self.store_root) / entry.scope / entry.filename

    def _append_bullet(
        self, scope: str, ref: str, body: bytes, path: str
    ) -> None:
        """`POST /api/v1/entry/<scope>/<ref>/bullets` — criteria 4 and 5.

        🔴 THE ACTOR IS `record.identity`, DERIVED FROM THE TOKEN THAT
        AUTHENTICATED, AND THE REQUEST BODY HAS NO WAY TO REACH IT. An `actor`
        key in the JSON is accepted and DISCARDED — accepted because a client
        library may send one and a 400 there would be a compatibility trap,
        discarded because a client-supplied actor lets any token-holder attribute
        a bullet to somebody else, and an attribution nobody can trust is worse
        than none. `render_bullet` takes `actor` as a keyword the request cannot
        populate, so this is structural rather than a rule someone remembers.

        The SESSION is different and IS caller-supplied: it is correlation data,
        not an identity claim — it says which run of which agent wrote this, and
        the agent is the only thing that knows. It is validated against
        `SESSION_COMPONENT` before it goes anywhere near a file.
        """
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            # 🔴 `RecursionError` IS NOT A `ValueError`, AND JSON RAISES IT. A
            # 400 KB body of `[[[[…]]]]` blows the interpreter's recursion limit
            # inside `json.loads`, so the caller got a dropped connection with no
            # response, no `X-Store-Status` and — the part that matters — NO
            # AUDIT LINE, on a request that had already been metered. It is a
            # caller error like any other malformed body and is answered as one.
            self._respond(
                400,
                f"bad request: body must be JSON ({exc})\n".encode("utf-8"),
                headers={"X-Store-Status": "bad-request"},
            )
            self._audit(path, 400, "bad-request")
            return
        problem = _bullet_request_problem(payload)
        if problem is not None:
            self._respond(
                400,
                f"bad request: {problem}\n".encode("utf-8"),
                headers={"X-Store-Status": "bad-request"},
            )
            self._audit(path, 400, "bad-request")
            return
        entry = self._resolve_writable(scope, ref, path)
        if entry is None:
            return
        assert self._identity is not None  # set by `authorize` in `_write`
        try:
            status, line, revision = append_bullet(
                self._entry_path(entry),
                text=payload["text"],
                actor=self._identity,
                session=payload["session"],
                today=_today(),
            )
        except EntryShapeError as exc:
            self._respond(
                422,
                f"unprocessable: {exc}\n".encode("utf-8"),
                headers={"X-Store-Status": "entry-shape"},
            )
            self._audit(path, 422, "entry-shape")
            return
        except OSError as exc:
            self._respond(
                503,
                f"store unreadable: {exc}\n".encode("utf-8"),
                headers={"X-Store-Status": "store-unreachable", "X-Store-Exit": "3"},
            )
            self._audit(path, 503, "store-unreachable")
            return
        self._respond(
            200,
            (line + "\n").encode("utf-8"),
            headers={
                "X-Store-Status": status,
                "X-Cairn-Bullet": content_hash(payload["text"]),
                "ETag": f'"{revision}"',
            },
        )
        self._audit(path, 200, status)

    def _replace_entry(
        self, scope: str, ref: str, body: bytes, path: str
    ) -> None:
        """`PUT /api/v1/entry/<scope>/<ref>` — criterion 6, whole-file replace.

        🔴 `If-Match` IS REQUIRED, NOT OPTIONAL, and a request without one is
        refused 428 rather than served. An optional precondition is no
        precondition: the caller that most needs it — a retry after a timeout, on
        a store two agents share — is exactly the one that would omit it.

        🔴 `If-Match: *` IS REFUSED. It means "any current representation", which
        is the one value that turns the guard off while looking like it is on.

        The header is a LIST (RFC 9110 §13.1.1) and is read as one — see
        `parse_if_match`. Reading it as a single opaque string made
        `If-Match: "stale", "<correct>"` a permanent 412.

        ⚠ THIS ROUTE DOES NOT ENFORCE ATTRIBUTION. The bytes are written
        verbatim, forged `[cairn: …]` trailers included; criterion 4's guarantee
        is a claim about `POST /bullets` only. Decided, not overlooked — see
        `replace_entry`'s docstring for why enforcement was declined.
        """
        raw = sole_header(self.headers, "If-Match")
        if raw is None:
            self._respond(
                428,
                b"precondition required: send If-Match with the entry revision "
                b"(sha256 of the entry file, first 16 hex characters)\n",
                headers={"X-Store-Status": "precondition-required"},
            )
            self._audit(path, 428, "precondition-required")
            return
        if_match = parse_if_match(raw)
        if "*" in if_match:
            self._respond(
                400,
                b"bad request: If-Match: * is refused - it matches any revision, "
                b"which is the same as sending no precondition at all\n",
                headers={"X-Store-Status": "bad-request"},
            )
            self._audit(path, 400, "bad-request")
            return
        if not if_match:
            # A header that is present but names NO entity-tag (`If-Match:` or
            # `If-Match: ,`) is not a precondition. Refusing it is the same
            # answer `*` gets, for the same reason: it would gate nothing.
            self._respond(
                400,
                b"bad request: If-Match names no entity-tag\n",
                headers={"X-Store-Status": "bad-request"},
            )
            self._audit(path, 400, "bad-request")
            return
        entry = self._resolve_writable(scope, ref, path)
        if entry is None:
            return
        try:
            revision = replace_entry(
                self._entry_path(entry),
                data=body,
                if_match=if_match,
                scope=entry.scope,
                filename=entry.filename,
            )
        except PreconditionFailed as exc:
            # 🔴 THE FILE IS UNCHANGED, and the CURRENT revision rides the
            # response: a client told only "no" cannot retry, and a client that
            # cannot retry re-sends without the precondition.
            self._respond(
                412,
                b"precondition failed: the entry has changed since that revision\n",
                headers={
                    "X-Store-Status": "precondition-failed",
                    "ETag": f'"{exc.current}"',
                },
            )
            self._audit(path, 412, "precondition-failed")
            return
        except (EntryShapeError, UnicodeDecodeError) as exc:
            self._respond(
                422,
                f"unprocessable: {exc}\n".encode("utf-8"),
                headers={"X-Store-Status": "entry-shape"},
            )
            self._audit(path, 422, "entry-shape")
            return
        except OSError as exc:
            self._respond(
                503,
                f"store unreadable: {exc}\n".encode("utf-8"),
                headers={"X-Store-Status": "store-unreachable", "X-Store-Exit": "3"},
            )
            self._audit(path, 503, "store-unreachable")
            return
        self._respond(
            200,
            b"replaced\n",
            headers={"X-Store-Status": "replaced", "ETag": f'"{revision}"'},
        )
        self._audit(path, 200, "replaced")


def _today() -> str:
    """The date an appended bullet is stamped with. UTC, because the pod's local
    time is UTC and a bullet's date is compared against other bullets' dates by
    `subsystem_touch.EntryJournal.newest_date`, never against a wall clock."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _bullet_request_problem(payload: Any) -> str | None:
    """Validate an append request. Returns the sentence to refuse with, or None.

    🔴 A SEPARATE FUNCTION SO EVERY REFUSAL IS IN ONE PLACE AND NAMES ITSELF.
    Each clause is reachable by an input every earlier clause accepts, which is
    the same ladder discipline `load_tokens`' guards are built to.

    ⚠ `actor` IS NOT VALIDATED AND NOT REJECTED — it is simply never read. See
    `_append_bullet`.

    🔴 THE CHARACTER CLAUSES ARE A PREDICATE, NOT A DENYLIST. The line-break
    clause asks `str.splitlines()` itself how many lines the text becomes, and
    the control/format clause asks `unicodedata` for a CATEGORY — because the
    two-character `"\n" in text or "\r" in text` this replaced was walked by
    eight other characters `splitlines()` splits on, one of which (`U+2028`)
    produced a stored bullet with NO attribution trailer and a second one whose
    `[cairn: …]` reads as somebody else's. See `LINE_BREAK_CHARS` and
    `_FORBIDDEN_CATEGORIES`.
    """
    if not isinstance(payload, dict):
        return "the body must be a JSON object"
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        return "`text` is required and must be a non-empty string"
    if len(text) > BULLET_TEXT_MAX:
        return f"`text` is {len(text)} characters, max {BULLET_TEXT_MAX}"
    if len(text.splitlines()) > 1 or text.strip(LINE_BREAK_CHARS) != text:
        # 🔴 `len(splitlines()) > 1` IS THE HONEST PREDICATE — it asks the very
        # function that decides how many lines these bytes become, so it cannot
        # fall behind the ten characters that function splits on. The second
        # clause covers a LEADING or TRAILING break, which `splitlines` folds
        # away (`"a\n".splitlines()` is one element) and which would otherwise
        # open or close the bullet with an empty line.
        return (
            "`text` must be ONE line — an embedded newline would be attached to "
            "this bullet as a continuation, or would start a second, unattributed "
            "bullet"
        )
    for char in text:
        if unicodedata.category(char) in _FORBIDDEN_CATEGORIES:
            # Named by CODE POINT, because the offending character is by
            # definition one the caller cannot see in their own error message.
            return (
                f"`text` contains U+{ord(char):04X} "
                f"({unicodedata.category(char)}), a control or formatting "
                "character that must not be written into a curated entry"
            )
    if text.lstrip().startswith(("- ", "* ")):
        return (
            "`text` must not open a markdown bullet — the `- ` is added here, and "
            "a second one would start a bullet with no attribution trailer"
        )
    session = payload.get("session")
    if not isinstance(session, str) or not SESSION_COMPONENT.fullmatch(session):
        return (
            "`session` is required and must match "
            f"{SESSION_COMPONENT.pattern} — every appended bullet records the "
            "actor AND the session that wrote it"
        )
    return None


def build_server(
    *,
    host: str,
    port: int,
    store_root: str,
    tokens: "Sequence[str | TokenRecord]",
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

    An item may be a `TokenRecord` or a bare `str`; a bare one is the LEGACY
    record (unrestricted scope) by the same rule the token file uses, resolved
    through the one `as_token_record`. Normalizing here rather than in the
    handler means `expected_tokens` is a homogeneous tuple of records, so no
    request-path code has to ask what shape it was configured with.
    """
    if isinstance(tokens, (str, bytes)):
        raise TypeError("tokens must be a SEQUENCE of tokens, not one string")
    if isinstance(trusted_proxies, (str, bytes)):
        raise TypeError(
            "trusted_proxies must be a SEQUENCE of networks, not one string"
        )
    # 🔴 REQUIRED, AND NON-EMPTY. `trusted_proxies` has no default in this
    # signature on purpose: a caller that forgot it fails at the call rather
    # than getting a server whose client identity is silently wrong for a reason
    # nobody can see. An explicitly EMPTY sequence is refused here as a
    # misconfiguration — the class default is empty so that the *unreachable*
    # path still fails safe (no header is believed), but reaching it
    # deliberately is a mistake worth naming.
    #
    # ⚠ THE MESSAGE USED TO SAY "every request would be a 401". That was true of
    # the refuse-outright design this replaced, and it is false now: with no
    # trusted peers every request is SERVED and bucketed under its own address,
    # so one client's failures can lock out the others and nothing looks broken.
    # An operator-visible error string is a claim like any other.
    networks = tuple(trusted_network(item) for item in trusted_proxies)
    if not networks:
        raise ValueError(
            "trusted_proxies is empty: no peer could ever be trusted, so no "
            "CF-Connecting-IP would ever be believed and every caller would be "
            f"bucketed under the proxy's own address. Set ${ENV_TRUSTED_PROXIES}"
        )

    class _Handler(StoreRequestHandler):
        pass

    _Handler.store_root = store_root
    _Handler.expected_tokens = tuple(as_token_record(t) for t in tokens)
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
            "file holding the bearer token SET, ONE ROW PER LINE, current first "
            "(mode 0600). A row is `<token>` (legacy: unrestricted scope) or "
            "`<token> <identity> <scope>,<scope>`. FILE FIRST: the agent exec "
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
        # 🔴 `<fingerprint>:<identity>` — the fingerprint FIRST and unchanged in
        # form, because the rotation procedure in the README greps for it. The
        # identity is appended, not substituted: two rows can hold one holder's
        # current and previous credential, so the identity alone cannot tell an
        # operator which line to delete.
        f"store={args.store} "
        f"token-ids={','.join(f'{t.fingerprint}:{t.identity}' for t in tokens)} "
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
