"""The CLI end of the `bw://` tab reference (scripts/browser-bridge/browser).

Fully HEADLESS: no Brave, no extension, no real bridge. A stub loopback server
stands in for server.py — it answers /whoami with a host label the test chooses
and records the exact JSON body the CLI POSTs to /cmd. So every assertion here
is about the WIRE SHAPE the reference produced, which is the thing that can be
silently wrong.

WHAT A WRONG ANSWER LOOKS LIKE HERE, and why the assertions are shaped this way:

  A reference is one opaque token that expands into ``--instance`` and ``--tab``.
  If it expands into the WRONG pair, nothing anywhere reports an error — the
  command reaches a real browser, drives a real tab, and returns a perfectly
  ordinary envelope. The operator finds out when a screenshot shows the wrong
  page. There is no envelope field, no op and no server route that can catch it
  after the fact, so the parse itself is the only place to catch it.

  That makes the three interesting cases: (a) a well-formed reference must land
  on EXACTLY the target it names, (b) a malformed one must be refused rather
  than split into whichever three fields fall out, and (c) a reference minted on
  the OTHER host must fail loudly, because the labels are unique per host and
  not across hosts — ``main`` exists on both machines.

THE FORMAT IS PINNED IN TWO PLACES ON PURPOSE. ``bw://workbench/main/12345``
appears here and in ``tests/tab_ref.test.mjs``, which asserts the extension
BUILDS exactly that string from the same inputs. Neither end can be changed
alone without turning the other red — the two files are the seam, and the seam
is where a format lives when no single component owns it.

RED-AT-BASE MATRIX. Measured against ``d202ef59`` (the commit before this
change), with only the three new test files copied in:

    pytest  scripts/browser-bridge/tests/test_browser_tab_ref.py
            29 FAILED / 4 passed at d202ef59  →  33 passed at HEAD
    node    tests/tab_ref.test.mjs + tests/action_click.test.mjs
            34 FAILED / 0 passed at d202ef59  →  34 passed at HEAD

The 4 that were already green are labelled ``INVARIANT GUARD`` in place: they
pin behaviour this change must NOT take away, and none of them is evidence the
feature works.

Run: nix develop ~/workspace/devrc -c python3 -m pytest \\
       scripts/browser-bridge/tests/test_browser_tab_ref.py -q
"""
import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from cli_budget import CLI_TIMEOUT_S  # noqa: E402

BB = Path(__file__).resolve().parent.parent          # scripts/browser-bridge
CLI = BB / "browser"

# The canonical reference. Same literal as tests/tab_ref.test.mjs.
CANONICAL = "bw://workbench/main/12345"
CANONICAL_INSTANCE = "main"
CANONICAL_TAB = 12345

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("curl") is None,
    reason="the browser CLI is bash and talks to the bridge with curl")


class _Stub(BaseHTTPRequestHandler):
    """Records /cmd bodies; answers /whoami with whatever host label is set."""
    bodies: list = []
    host_label = "workbench"
    whoami_hits = 0

    def _reply(self, code, payload):
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode()
        try:
            self.bodies.append(json.loads(raw))
        except ValueError:
            self.bodies.append({"__unparseable__": raw})
        self._reply(200, {"ok": True, "result": {"id": "c", "ok": True,
                                                 "data": {"value": 1}}})

    def do_GET(self):
        if self.path.startswith("/whoami"):
            type(self).whoami_hits += 1
            host = ({"label": self.host_label} if self.host_label is not None
                    else None)
            self._reply(200, {"ok": True, "host": host, "instances": []})
            return
        self._reply(200, {"ok": True, "instances": []})

    def log_message(self, *a):
        pass


@pytest.fixture
def bridge(tmp_path):
    class _Handler(_Stub):
        bodies = []                                  # fresh per test
        host_label = "workbench"
        whoami_hits = 0

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    tokfile = tmp_path / "token"
    tokfile.write_text("test-token-abc123\n")

    env = dict(os.environ)
    env.update({
        "BROWSER_BRIDGE_HOST": "127.0.0.1",
        "BROWSER_BRIDGE_PORT": str(srv.server_address[1]),
        "BROWSER_BRIDGE_TOKEN_FILE": str(tokfile),
        "CLAUDE_CODE_SESSION_ID": "pytest-tab-ref",
        "HOME": str(tmp_path),
    })
    # The env-default targeting knobs must not leak in from the developer's own
    # shell: BB_INSTANCE would seed INSTANCE and make the override tests lie.
    for k in ("BB_INSTANCE", "BB_TAB", "BB_FRAME"):
        env.pop(k, None)

    class _Bridge:
        handler = _Handler
        bodies = _Handler.bodies

        @staticmethod
        def run(*args, **kw):
            e = dict(env)
            e.update(kw.pop("env", {}))
            return subprocess.run(["bash", str(CLI), *args], env=e,
                                  capture_output=True, text=True,
                                  timeout=CLI_TIMEOUT_S)

    try:
        yield _Bridge
    finally:
        srv.shutdown()
        srv.server_close()


# --------------------------------------------------------------------------- #
# It routes — and routes to EXACTLY what it names
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("argv, label", [
    ([CANONICAL, "text"], "leading position"),
    (["text", CANONICAL], "trailing position (the natural paste order)"),
])
def test_a_reference_expands_into_target_and_tab(bridge, argv, label):
    r = bridge.run(*argv)
    assert r.returncode == 0, f"{label}: {r.stderr}"
    assert len(bridge.bodies) == 1, f"{label}: {bridge.bodies}"
    body = bridge.bodies[0]
    assert body["op"] == "text", label
    assert body["target"] == CANONICAL_INSTANCE, label
    assert body["tab"] == CANONICAL_TAB, label


def test_both_positions_produce_a_byte_identical_request(bridge):
    """The invariant, stated as one assertion rather than inferred from two.

    Two spellings of the same reference that differ ANYWHERE in the body is a
    bug even when both happen to route today — it means one of them is carrying
    something the other is not.
    """
    bridge.run(CANONICAL, "text")
    bridge.run("text", CANONICAL)
    assert len(bridge.bodies) == 2
    assert bridge.bodies[0] == bridge.bodies[1]


def test_the_reference_is_stripped_from_the_subcommand_arguments(bridge):
    """It must not ALSO arrive as the subcommand's own positional.

    `screenshot`'s positional is an output PATH. A reference left in argv would
    be taken as one, and the failure would be a file-write error naming a path
    the operator never typed — or, worse, a written file.
    """
    r = bridge.run("text", CANONICAL, "main")
    assert r.returncode == 0, r.stderr
    body = bridge.bodies[-1]
    assert body["target"] == CANONICAL_INSTANCE
    # `main` here is `text`'s CSS-selector positional and must survive untouched.
    assert body.get("selector") == "main", body


def test_an_unlabelled_profiles_full_uuid_survives_the_round_trip(bridge):
    """The whole auto-id, not a prefix — the server matches targets EXACTLY."""
    uuid = "9f1c7b2e-4a55-4c31-8de0-6b0f2a7c1d34"
    r = bridge.run(f"bw://workbench/{uuid}/7", "text")
    assert r.returncode == 0, r.stderr
    assert bridge.bodies[-1]["target"] == uuid
    assert bridge.bodies[-1]["tab"] == 7


# --------------------------------------------------------------------------- #
# The host check — the cross-paste case
# --------------------------------------------------------------------------- #
def test_a_reference_from_the_OTHER_host_is_refused_before_any_command(bridge):
    bridge.handler.host_label = "laptop"
    r = bridge.run(CANONICAL, "text")
    assert r.returncode != 0
    assert "minted on 'workbench'" in r.stderr
    assert "this bridge is 'laptop'" in r.stderr
    # 🔴 The point: NOTHING was sent. A refusal that still drove a tab would be
    # the bug with a warning printed over it.
    assert bridge.bodies == [], bridge.bodies


def test_a_matching_host_is_verified_against_the_bridge_not_assumed(bridge):
    """The check must be a real read, so a wrong host can be SEEN.

    A positive control for the assertion above: if /whoami were never called,
    the cross-host test would pass for the wrong reason (nothing to disagree
    with), so pin that the request happens.
    """
    before = bridge.handler.whoami_hits
    r = bridge.run(CANONICAL, "text")
    assert r.returncode == 0, r.stderr
    assert bridge.handler.whoami_hits == before + 1


def test_a_bridge_that_cannot_name_its_host_warns_and_proceeds(bridge):
    """FAIL OPEN on ignorance, closed on disagreement.

    A host with no ACTIVITY_HOST resolves to "unknown"; refusing there would
    strand every reference on that host while catching nothing.
    """
    bridge.handler.host_label = "unknown"
    r = bridge.run(CANONICAL, "text")
    assert r.returncode == 0, r.stderr
    assert "NOT verified" in r.stderr
    assert bridge.bodies and bridge.bodies[-1]["target"] == CANONICAL_INSTANCE
    # The warning goes to stderr ONLY — stdout stays parseable JSON.
    assert "NOT verified" not in r.stdout
    json.loads(r.stdout)


def test_a_missing_host_object_is_treated_as_unknown_not_as_a_match(bridge):
    bridge.handler.host_label = None                 # /whoami answers host: null
    r = bridge.run(CANONICAL, "text")
    assert r.returncode == 0, r.stderr
    assert "NOT verified" in r.stderr


def test_no_reference_means_no_whoami_call(bridge):
    """INVARIANT GUARD (green at d202ef59): the check is scoped to the feature.

    An ordinary command must not grow a round trip. Green before this change
    too — it pins back-compat the change must not take away, and is NOT evidence
    the feature works.
    """
    before = bridge.handler.whoami_hits
    r = bridge.run("--instance", "main", "text")
    assert r.returncode == 0, r.stderr
    assert bridge.handler.whoami_hits == before


# --------------------------------------------------------------------------- #
# Refusals — a malformed reference must never be split into three fields
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ref, expect", [
    ("bw://workbench/main", "reference must be bw://"),
    ("bw://workbench", "reference must be bw://"),
    ("bw://", "reference must be bw://"),
    ("bw://workbench/main/12345/extra", "too many '/'"),
    ("bw://workbench/main/notanumber", "tab id must be numeric"),
    ("bw://workbench/main/", "tab id must be numeric"),
    ("bw://workbench//12345", "instance is not ref-safe"),
    ("bw:///main/12345", "host is not ref-safe"),
    ("bw://work bench/main/12345", "host is not ref-safe"),
    ("bw://workbench/ma in/12345", "instance is not ref-safe"),
    ("bw://workbench/ma;rm/12345", "instance is not ref-safe"),
])
def test_a_malformed_reference_is_refused_and_sends_nothing(bridge, ref, expect):
    r = bridge.run(ref, "text")
    assert r.returncode != 0, f"{ref} was ACCEPTED: {r.stdout}"
    assert expect in r.stderr, f"{ref}: {r.stderr}"
    assert bridge.bodies == [], f"{ref} still reached the bridge: {bridge.bodies}"


@pytest.mark.parametrize("argv", [
    ["--instance", "other", CANONICAL, "text"],
    [CANONICAL, "--instance", "other", "text"],
    ["--instance", "other", "text", CANONICAL],
])
def test_a_reference_and_an_explicit_instance_is_refused_not_ranked(bridge, argv):
    """No precedence rule is safe here — whichever loses, the wrong tab runs."""
    r = bridge.run(*argv)
    assert r.returncode != 0, r.stdout
    assert "--instance conflicts with" in r.stderr
    assert bridge.bodies == []


@pytest.mark.parametrize("argv", [
    ["--tab", "999", CANONICAL, "text"],
    [CANONICAL, "--tab", "999", "text"],
])
def test_a_reference_and_an_explicit_tab_is_refused_not_ranked(bridge, argv):
    r = bridge.run(*argv)
    assert r.returncode != 0, r.stdout
    assert "--tab conflicts with" in r.stderr
    assert bridge.bodies == []


def test_two_references_are_refused(bridge):
    r = bridge.run(CANONICAL, "text", "bw://workbench/other/999")
    assert r.returncode != 0
    assert "two bw:// references" in r.stderr
    assert bridge.bodies == []


# --------------------------------------------------------------------------- #
# The free-text subcommands
# --------------------------------------------------------------------------- #
def test_a_trailing_reference_is_TEXT_for_the_free_text_subcommands(bridge):
    """INVARIANT GUARD (green at d202ef59): `browser type bw://…` types the
    literal string; it does not route.

    Green before this change because nothing parsed references at all — which is
    exactly what makes it the right guard: the feature must not TAKE THIS AWAY.
    It is not evidence the feature works.

    This is the one place the two positions deliberately differ, and it is why
    the trailing form is not simply "strip any bw:// token": `type`, `js`/`eval`
    and `agent` take arbitrary operator text, and silently eating a value that
    happens to start `bw://` would be a different silent-wrong-answer.
    """
    r = bridge.run("type", CANONICAL, "--selector", "#x")
    assert r.returncode == 0, r.stderr
    body = bridge.bodies[-1]
    assert body["op"] == "type"
    assert body["text"] == CANONICAL, body
    assert "target" not in body, body
    assert "tab" not in body, body


@pytest.mark.parametrize("sub", ["js", "eval"])
def test_js_takes_a_trailing_reference_as_the_expression(bridge, sub):
    """INVARIANT GUARD (green at d202ef59) — see the test above."""
    ref_expr = f'"{CANONICAL}"'
    r = bridge.run(sub, ref_expr)
    assert r.returncode == 0, r.stderr
    assert "target" not in bridge.bodies[-1], bridge.bodies[-1]


def test_the_LEADING_position_still_routes_a_free_text_subcommand(bridge):
    """…so the feature is not simply unavailable for `type`."""
    r = bridge.run(CANONICAL, "type", "hello", "--selector", "#x")
    assert r.returncode == 0, r.stderr
    body = bridge.bodies[-1]
    assert body["target"] == CANONICAL_INSTANCE
    assert body["tab"] == CANONICAL_TAB
    assert body["text"] == "hello"


# --------------------------------------------------------------------------- #
# Interaction with the env defaults
# --------------------------------------------------------------------------- #
def test_a_reference_overrides_the_env_defaults(bridge):
    """BB_INSTANCE/BB_TAB are DEFAULTS; the reference is on the command line.

    The precedence the CLI already documents is "an explicit flag always wins
    over the environment". A reference is command-line, so it wins — and unlike
    two flags it cannot be a typo the operator can see, which is why this is
    pinned rather than left to read off the assignment order.
    """
    r = bridge.run(CANONICAL, "text",
                   env={"BB_INSTANCE": "envprofile", "BB_TAB": "999"})
    assert r.returncode == 0, r.stderr
    body = bridge.bodies[-1]
    assert body["target"] == CANONICAL_INSTANCE
    assert body["tab"] == CANONICAL_TAB


# --------------------------------------------------------------------------- #
# The seam: the CLI and the extension must agree on the scheme
# --------------------------------------------------------------------------- #
def test_the_scheme_literal_is_the_same_on_both_sides_of_the_seam():
    """No component owns this format, so nothing else would notice a rename.

    The extension MINTS references and the CLI CONSUMES them, in different
    languages, with no shared module and no schema between them. Renaming the
    scheme on one side leaves the other silently unable to parse anything the
    first produces — and every existing test on each side keeps passing, because
    each is internally consistent.
    """
    protocol = (BB / "extension" / "protocol.js").read_text(encoding="utf-8")
    cli = CLI.read_text(encoding="utf-8")

    marker = 'export const TAB_REF_SCHEME = "'
    assert marker in protocol, (
        "TAB_REF_SCHEME vanished from protocol.js — this guard now checks "
        "nothing. Re-point it at whatever defines the scheme.")
    scheme = protocol.split(marker, 1)[1].split('"', 1)[0]
    assert scheme, "empty scheme parsed out of protocol.js"

    # The CLI strips the scheme and dispatches on it; both spellings must be the
    # value the extension mints.
    assert f'rest="${{raw#{scheme}}}"' in cli, (
        f"the CLI does not strip {scheme!r} — the builder and the parser have "
        f"diverged")
    assert f"    {scheme}*)" in cli, (
        f"the CLI's global-flag loop does not dispatch on {scheme!r}")
