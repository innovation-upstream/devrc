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
import os
import secrets
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
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
    """
    audit: list[str] = []
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
        assert "token 1 of 1" in str(exc.value)
        # 43 chars = 256 bits base64url, pinned LITERALLY (§2b).
        assert "43" in str(exc.value)

    def test_the_floor_is_43_characters(self):
        # A literal, not `api.MIN_TOKEN_CHARS` — importing it would assert x == x.
        assert api.MIN_TOKEN_CHARS == 43

    def test_a_token_of_exactly_the_floor_is_accepted(self, tmp_path: Path):
        path = tmp_path / "tok"
        path.write_text("z" * 43 + "\n")
        assert api.load_tokens(str(path), {}) == ["z" * 43]

    def test_env_is_the_FALLBACK_not_the_primary(self, tmp_path: Path):
        # Both sources present: the FILE wins. The agent exec sandbox strips env
        # vars from agent-run commands, so an env token that quietly overrode a
        # mounted secret would make the deployed token unknowable.
        path = tmp_path / "tok"
        path.write_text("f" * 50)
        assert api.load_tokens(str(path), {"SUBSYSTEM_STORE_TOKEN": "e" * 50}) == [
            "f" * 50
        ]

    def test_env_is_used_when_no_file_is_named(self):
        assert api.load_tokens(None, {"SUBSYSTEM_STORE_TOKEN": "e" * 50}) == ["e" * 50]


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
            env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "HOME": str(path)},
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

    def test_the_stage_is_a_faithful_copy(self, store: Path, tmp_path: Path):
        stage = tmp_path / "stage"
        assert run_seed("--store", str(store), "--stage", str(stage)).returncode == 0
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
        env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "HOME": str(tmp_path)}
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
        env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "HOME": str(tmp_path)}
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

    def test_the_STORE_ROOT_line_is_the_only_permitted_difference(
        self, store: Path, tmp_path: Path, token_file: Path
    ):
        """The pod serves `/data`; the workbench serves `~/.claude/…`. The
        verifier canonicalises exactly one line — and proves it was exactly one,
        by counting the RAW differing lines too."""
        served = tmp_path / "served-elsewhere"
        subprocess.run(
            ["cp", "-a", str(store), str(served)], check=True, capture_output=True
        )
        with running(served) as (base, _):
            r = run_verify(
                "--store", str(store), "--url", base, "--token-file", str(token_file)
            )
        assert r.returncode == 0, r.stdout + r.stderr
        # 2 raw differing lines: one `<`, one `>`, both the store-root line.
        assert "raw-diff-lines=2 store-root-lines=2" in r.stdout

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

    def test_the_only_routes_are_recall_and_search(self, store: Path):
        """Behavioural, not a grep: an authenticated GET to anything else 404s.

        A structural read of the router would be satisfied by a route added
        through a different spelling; this walks the endpoint list.
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
        assert api.load_tokens(path, {}) == [GOOD_TOKEN, SECOND_TOKEN]

    def test_a_duplicated_line_collapses_and_order_is_kept(self, tmp_path: Path):
        path = self._write(tmp_path, SECOND_TOKEN, GOOD_TOKEN, SECOND_TOKEN)
        assert api.load_tokens(path, {}) == [SECOND_TOKEN, GOOD_TOKEN]

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
        assert api.load_tokens(path, {}) == four

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
        assert "token 2 of 2 is too short" in message
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
        assert got == api.token_id(GOOD_TOKEN)
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
        assert started["tokens"] == [GOOD_TOKEN, SECOND_TOKEN]


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
        assert code == 200, "an unidentifiable caller locked out an identified one"
        assert POINTER_LINE.encode() in body
        # 🔴 THE ASSERTION THAT MAKES THIS TEST MEAN ANYTHING, and it was missing.
        # An audit found this test VACUOUS against the very hazard it names:
        # under the mutant `ip = "unknown"` (bucket every unidentified caller
        # under one shared key) the flood locks out `"unknown"` while the final
        # request above uses a DIFFERENT key — so it stayed green. What actually
        # distinguishes fail-closed from a shared bucket is that the twentieth
        # unidentified request is STILL `no-client-ip` and never `locked-out`:
        # nothing was counted, because there was no bucket to count into.
        statuses = {
            line.split("status=")[1].split()[0] for line in audit[:20]
        }
        assert statuses == {"no-client-ip"}, statuses
        assert not any("locked-out" in line for line in audit)
        assert not any("lockout-triggered" in line for line in audit)

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
        with running_subprocess(store, rotating_token_file) as (base, proc):
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=GOOD_TOKEN)
            fetch(f"{base}/api/v1/recall/{SCOPE}", token=SECOND_TOKEN)
            fetch(f"{base}/api/v1/recall/{SCOPE}", token="w" * 48)
            proc.terminate()
            stdout, _stderr = proc.communicate(timeout=15)

        lines = [ln for ln in stdout.splitlines() if ln.startswith("store-api audit ")]
        assert len(lines) == 3, f"expected 3 audit lines, got {len(lines)}: {stdout}"
        assert f"token={api.token_id(GOOD_TOKEN)}" in lines[0]
        assert f"token={api.token_id(SECOND_TOKEN)}" in lines[1]
        assert api.token_id(GOOD_TOKEN) != api.token_id(SECOND_TOKEN)
        # The failure line: no fingerprint, `auth=fail`, and the client address —
        # the three fields the Loki alert selects on.
        assert "auth=fail" in lines[2] and "token=-" in lines[2]
        assert f"ip={CLIENT_IP}" in lines[2]
        assert "result=401" in lines[2]
        # And never a credential, on any line.
        assert GOOD_TOKEN not in stdout and SECOND_TOKEN not in stdout
        assert "w" * 48 not in stdout


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
          HEAD: not one of them reaches the limiter -> the victim gets its 200

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
        with running_subprocess(
            store,
            token_file,
            trusted_proxies=f"{TRUSTED_PEER}/32",
            host=TRUSTED_PEER,
        ) as (base, proc):
            for _ in range(5):
                fetch_from(
                    UNTRUSTED_PEER,
                    base,
                    f"/api/v1/recall/{SCOPE}",
                    token="w" * 48,
                    client_ip=SPOOF_IP,
                )
            proc.terminate()
            stdout, _err = proc.communicate(timeout=15)
        lines = [ln for ln in stdout.splitlines() if ln.startswith("store-api audit ")]
        assert len(lines) == 5, stdout
        # 🔴 THE ASSERTION THAT IS THE WHOLE DEFECT: the forged address never
        # becomes an identity. A fix that recorded the spoofed value but declined
        # to COUNT it would pass every status check and fail this one.
        assert all(f"ip={SPOOF_IP}" not in ln for ln in lines), lines
        assert all(f"ip={UNTRUSTED_PEER}" in ln for ln in lines), lines
        assert all("peer=untrusted" in ln for ln in lines), lines
        # …and the forger locks out ITSELF, which is a rate limiter working.
        assert "status=lockout-triggered" in lines[-1], lines[-1]

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
            code = fetch_from(
                UNTRUSTED_PEER,
                base,
                f"/api/v1/recall/{SCOPE}",
                token=GOOD_TOKEN,
                client_ip=SPOOF_IP,
            )
            proc.terminate()
            stdout, _err = proc.communicate(timeout=15)
        assert code == 200, code
        line = [ln for ln in stdout.splitlines() if ln.startswith("store-api audit ")][-1]
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
