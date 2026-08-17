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

import hashlib
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


@contextmanager
def running(store_root, token=GOOD_TOKEN):
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
        token=token,
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


def fetch(url: str, *, token: str | None = None, method: str = "GET", auth_header=None):
    """Return (code, headers, body-bytes) without raising on 4xx/5xx."""
    req = urllib.request.Request(url, method=method)
    if auth_header is not None:
        req.add_header("Authorization", auth_header)
    elif token is not None:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


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
    """`load_token` refuses to serve on a token that is absent, empty or weak.

    🔴 Each case is built so that every EARLIER guard passes: the empty-token
    case uses a file that exists and is readable, and the too-short case uses a
    file that exists, is readable and is non-empty. A test that tripped an
    earlier guard would be green with the guard it names deleted.
    """

    def test_no_source_at_all_names_the_two_ways_to_supply_one(self):
        with pytest.raises(ValueError) as exc:
            api.load_token(None, {})
        assert "no token source" in str(exc.value)
        assert "--token-file" in str(exc.value)
        assert "SUBSYSTEM_STORE_TOKEN" in str(exc.value)

    def test_a_missing_file_is_not_confused_with_an_absent_one(self, tmp_path: Path):
        with pytest.raises(ValueError) as exc:
            api.load_token(str(tmp_path / "nope"), {})
        assert "token file unreadable" in str(exc.value)

    def test_a_readable_file_of_whitespace_is_rejected_as_EMPTY(self, tmp_path: Path):
        # Guard 1 and 2 both pass here: the source exists and reads fine.
        path = tmp_path / "tok"
        path.write_text("   \n\t\n")
        with pytest.raises(ValueError) as exc:
            api.load_token(str(path), {})
        assert "token is empty" in str(exc.value)

    def test_a_short_but_perfectly_valid_file_is_rejected_as_TOO_SHORT(
        self, tmp_path: Path
    ):
        # Guards 1-3 all pass: the file exists, reads, and is non-empty. This is
        # the hand-typed-token case, which is exactly what guard 4 is for.
        path = tmp_path / "tok"
        path.write_text("hunter2\n")
        with pytest.raises(ValueError) as exc:
            api.load_token(str(path), {})
        assert "token is too short" in str(exc.value)
        # 43 chars = 256 bits base64url, pinned LITERALLY (§2b).
        assert "43" in str(exc.value)

    def test_the_floor_is_43_characters(self):
        # A literal, not `api.MIN_TOKEN_CHARS` — importing it would assert x == x.
        assert api.MIN_TOKEN_CHARS == 43

    def test_a_token_of_exactly_the_floor_is_accepted(self, tmp_path: Path):
        path = tmp_path / "tok"
        path.write_text("z" * 43 + "\n")
        assert api.load_token(str(path), {}) == "z" * 43

    def test_env_is_the_FALLBACK_not_the_primary(self, tmp_path: Path):
        # Both sources present: the FILE wins. The agent exec sandbox strips env
        # vars from agent-run commands, so an env token that quietly overrode a
        # mounted secret would make the deployed token unknowable.
        path = tmp_path / "tok"
        path.write_text("f" * 50)
        assert api.load_token(str(path), {"SUBSYSTEM_STORE_TOKEN": "e" * 50}) == "f" * 50

    def test_env_is_used_when_no_file_is_named(self):
        assert api.load_token(None, {"SUBSYSTEM_STORE_TOKEN": "e" * 50}) == "e" * 50


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
        api.authorize(f"Bearer {GOOD_TOKEN}", GOOD_TOKEN)
        assert len(seen) == 1
        # 🔴 And with the RIGHT arguments, in the right order: presented first,
        # expected second, both as bytes. A spy that only counted calls would be
        # green for `compare_digest(expected, expected)`, which always says yes.
        assert seen[0] == (GOOD_TOKEN.encode(), GOOD_TOKEN.encode())

    def test_BEHAVIOURAL_it_accepts_the_right_token_and_rejects_a_near_miss(self):
        api.authorize(f"Bearer {GOOD_TOKEN}", GOOD_TOKEN)  # no raise
        with pytest.raises(api._Rejected):
            api.authorize(f"Bearer {GOOD_TOKEN[:-1]}X", GOOD_TOKEN)
        with pytest.raises(api._Rejected):
            api.authorize(None, GOOD_TOKEN)

    def test_a_PREFIX_of_the_token_is_rejected(self):
        with pytest.raises(api._Rejected):
            api.authorize(f"Bearer {GOOD_TOKEN[:10]}", GOOD_TOKEN)

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
