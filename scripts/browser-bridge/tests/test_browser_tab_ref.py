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
            29 FAILED / 4 passed at d202ef59  →  33 passed at the first tip
    node    tests/tab_ref.test.mjs + tests/action_click.test.mjs
            34 FAILED / 0 passed at d202ef59  →  34 passed at the first tip

The 4 that were already green are labelled ``INVARIANT GUARD`` in place: they
pin behaviour this change must NOT take away, and none of them is evidence the
feature works.

Audit rounds 1-3 added the `agent`, ``--``, /whoami-shape, SKILL.md-ledger and
env-leak cases. (No count here on purpose: the first version of this paragraph
carried one, and it was stale within the same session. Count the tests if you
need the number.) 🔴 Two of those additions FAILED IN THE NIX SANDBOX while passing on
the dev host, because they asserted that `browser agent` had reached the wire —
which it does only once its model-backend prerequisites are met, and those come
from the developer's own environment. They now key on `browser-agent`'s
missing-goal refusal, which is decided before any network or backend work and
therefore reads the same in both tiers. **Ask of any assertion here which tier
it can be true in.**

Run: nix develop ~/workspace/devrc -c python3 -m pytest \\
       scripts/browser-bridge/tests/test_browser_tab_ref.py -q
"""
import json
import os
import re
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from cli_budget import CLI_TIMEOUT_S  # noqa: E402
from testlib import mockbin  # noqa: E402

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
    host_payload = None
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
        # `open` must answer with a tabId: `browser agent` opens its own tab
        # first and refuses to continue without one, so a stub that omits it
        # fails every agent case for a harness reason.
        data = {"value": 1}
        try:
            if json.loads(raw).get("op") == "open":
                data = {"tabId": 4242, "url": "about:blank"}
        except ValueError:
            pass
        self._reply(200, {"ok": True, "result": {"id": "c", "ok": True,
                                                 "data": data}})

    def do_GET(self):
        if self.path.startswith("/whoami"):
            type(self).whoami_hits += 1
            # `host_payload`, when set, REPLACES the whole body — the CLI's parser
            # has to survive shapes server.py would never emit, and a test that
            # can only vary the label cannot express those.
            if self.host_payload is not None:
                self._reply(200, self.host_payload)
                return
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
        host_payload = None
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
        def env_for_stub():
            """The same env `run` uses — for tests that invoke a COPY of the CLI
            beside a stub `browser-agent`, rather than the real one."""
            return dict(env)

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


def _run_beside_stub_agent(bridge, tmp_path, tag, *args):
    """Run a COPY of the real CLI beside a STUB `browser-agent` that ECHOES the
    goal it was handed, and refuses exactly as the real one does when handed none.

    🔴 WHY A STUB, when these two cases used the real wrapper for a year: the real
    `browser-agent` WARMS an isolated opencode config dir (bounded by
    BROWSER_AGENT_WARM_TIMEOUT, default 90 s) and then runs a REAL model session
    (bounded by its own --timeout, default 120 s). Under a pytest tmp_path HOME the
    warm cache is cold on EVERY test, and the warm needs network to npm-install
    @opencode-ai/plugin, so it burns the full 90 s every time. MEASURED 2026-09-04
    via --durations: these two cases cost 155.98 s and 234.22 s while the other 51
    in this file cost ~0.5 s each. Against this file's CLI_TIMEOUT_S of 300 s that
    left a 66 s margin, and CI load erased it — `test_the_free_text_list_covers_
    agent_too` died in the devrc-pytests gate as a bare subprocess.TimeoutExpired
    with NO assertion ever executed, which reads as a code failure and is not one.

    Nothing in either case is about browser-agent's BEHAVIOUR. Both ask what the
    `browser` CLI FORWARDS — a question a stub answers exactly, and instantly.

    🔴 The stub ECHOES the goal so the assertion can be POSITIVE. The original pair
    asserted only that two error strings were ABSENT, which is the shape that needs
    a separate positive control to be worth anything (see
    test_POSITIVE_CONTROL_the_missing_goal_error_is_producible, which still drives
    the REAL wrapper — it costs 9 ms now that argument validation runs before the
    warm block). Asserting the goal ARRIVED tests the same discriminator directly
    and cannot rot if the wrapper's wording changes.
    """
    stub_dir = tmp_path / f"stub-{tag}"
    stub_dir.mkdir()
    (stub_dir / "browser").write_text(CLI.read_text(encoding="utf-8"),
                                      encoding="utf-8")
    # write_exec, NOT a hand-written shebang — see the note on the BB_* stubs below:
    # `#!/usr/bin/env bash` does not exist in the nix build sandbox.
    mockbin.write_exec(stub_dir / "browser-agent", r'''goal=""
while [ $# -gt 0 ]; do
  case "$1" in
    --instance|--allow-domains|--deny-domains|--steps|--timeout) shift ;;
    --*) : ;;
    *) goal="$1" ;;
  esac
  shift
done
[ -n "$goal" ] || { printf 'browser-agent: a goal is required: browser agent "<goal>"\n' >&2; exit 2; }
printf 'STUB_GOAL=[%s]\n' "$goal"
''')
    return subprocess.run(["bash", str(stub_dir / "browser"), *args],
                          env=bridge.env_for_stub(), capture_output=True,
                          text=True, timeout=CLI_TIMEOUT_S)


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


@pytest.mark.parametrize("sub, field", [("js", "js"), ("eval", "js")])
def test_js_takes_a_trailing_reference_as_the_expression(bridge, sub, field):
    """INVARIANT GUARD (green at d202ef59) — see the test above.

    🔴 THE FIXTURE IS UNQUOTED, AND THAT IS THE WHOLE TEST. An earlier version
    passed ``'"bw://workbench/main/12345"'`` — a JS string *literal*, which does
    not start with ``bw://``, so the ``bw://*`` glob could never match it and the
    guard could not fail however the exemption list was mutated. An audit
    measured it: dropping ``eval`` from REF_FREE_TEXT_SUBCOMMANDS SURVIVED.
    A fixture that cannot reach the branch it names is not a guard.
    """
    r = bridge.run(sub, CANONICAL)
    assert r.returncode == 0, r.stderr
    body = bridge.bodies[-1]
    assert body[field] == CANONICAL, body
    assert "target" not in body, body
    assert "tab" not in body, body


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


# --------------------------------------------------------------------------- #
# `agent` cannot keep the promise a reference makes
# --------------------------------------------------------------------------- #
def test_agent_REFUSES_a_reference_rather_than_honouring_half_of_it(bridge):
    """🔴 The wrong-answer case an audit found, and why refusing beats routing.

    `browser-agent` has no `--tab`: the agent arm forwards `--instance` only, and
    the autonomous agent always works in its OWN freshly-opened tab. So a routed
    reference kept the instance and silently dropped the tab — the operator
    clicks the icon on the tab they care about, pastes the ref, and gets a
    confident answer about a BLANK page, with no error anywhere.

    A reference is a promise about ONE tab. A subcommand that cannot keep it must
    refuse, not keep the half it happens to support.
    """
    r = bridge.run(CANONICAL, "agent", "--dry-run", "do a thing")
    assert r.returncode != 0, r.stdout
    assert "agent cannot honour" in r.stderr
    assert "--instance main" in r.stderr, "the error must name the way forward"
    assert bridge.bodies == [], f"nothing may be dispatched: {bridge.bodies}"


def test_POSITIVE_CONTROL_the_missing_goal_error_is_producible(bridge):
    """🔴 The two tests below assert an ABSENCE, which is worth nothing until
    the string is shown to be producible HERE, in THIS environment.

    Both read "`a goal is required` must not appear". A typo in that literal, a
    reworded error in `browser-agent`, or an environment where the agent path
    dies even earlier would make each of them pass while testing nothing — the
    reassuring-zero shape. This case feeds an input that MUST produce it (an
    `agent` invocation with no goal at all) and watches the string appear.
    """
    r = bridge.run("agent", "--dry-run")
    assert "a goal is required" in r.stderr, (
        "the missing-goal error is not reachable here, so the two absence "
        f"assertions below are vacuous. stderr was: {r.stderr!r}")


def test_the_free_text_list_covers_agent_too(bridge, tmp_path):
    """A TRAILING bw:// is a GOAL for `agent`, not a route.

    Dropping `agent` from REF_FREE_TEXT_SUBCOMMANDS SURVIVED an audit's mutation
    because nothing exercised it. The discriminator is deterministic and needs no
    backend: with `agent` on the list the reference survives as the goal, so
    `browser-agent` gets one; with `agent` dropped, the token is stripped out as
    routing and the agent is left with NO goal at all — which it refuses by name,
    before any network work.
    """
    r = _run_beside_stub_agent(bridge, tmp_path, "free-text", "agent",
                               "--dry-run", CANONICAL)
    # POSITIVE: the reference must ARRIVE as the goal. This is the discriminator
    # the docstring describes, asserted directly rather than via two absences.
    assert f"STUB_GOAL=[{CANONICAL}]" in r.stdout, (
        "the trailing reference did not reach browser-agent as the goal; "
        f"stdout={r.stdout!r} stderr={r.stderr!r}")
    # The original absence assertions are KEPT: they are what regressed before.
    assert "a goal is required" not in r.stderr, (
        "the trailing reference was consumed as a route, leaving no goal: "
        + r.stderr)
    assert "agent cannot honour" not in r.stderr, (
        "a TRAILING reference must be text for `agent`, not a refused route: "
        + r.stderr)


# --------------------------------------------------------------------------- #
# `--` ends reference scanning
# --------------------------------------------------------------------------- #
def test_a_double_dash_ENDS_reference_scanning(bridge):
    """An explicitly-escaped positional is not a route.

    The strip loop runs BEFORE each subcommand's own parser, so without honouring
    `--` a `browser text -- 'bw://…'` had its selector eaten and sent NO selector
    at all — the escaped argument vanished and the command silently re-targeted.
    `pos_rest` exists in this file precisely because `--` was dropped once before.
    """
    r = bridge.run("text", "--", CANONICAL)
    assert r.returncode == 0, r.stderr
    body = bridge.bodies[-1]
    assert body.get("selector") == CANONICAL, body
    assert "target" not in body, body
    assert "tab" not in body, body


def test_a_reference_BEFORE_a_double_dash_still_routes(bridge):
    """CONTROL for the test above: `--` ends scanning, it does not disable it."""
    r = bridge.run("text", CANONICAL, "--", "main")
    assert r.returncode == 0, r.stderr
    body = bridge.bodies[-1]
    assert body["target"] == CANONICAL_INSTANCE, body
    assert body.get("selector") == "main", body


# --------------------------------------------------------------------------- #
# The /whoami parse cannot raise, and says WHICH thing went wrong
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("payload, why", [
    ({"ok": True, "host": "workbench"}, "host is a string, not an object"),
    ({"ok": True, "host": {"label": 5}}, "label is not a string"),
    ({"ok": True}, "no host key at all"),
    ([1, 2, 3], "the whole body is not an object"),
])
def test_an_unreadable_whoami_never_raises_and_fails_open(bridge, payload, why):
    """A PARSE failure must not surface as a Python traceback, and must not be
    reported as "the bridge cannot identify its host" — that is a different fact
    and sends the operator somewhere else entirely."""
    bridge.handler.host_payload = payload
    r = bridge.run(CANONICAL, "text")
    assert r.returncode == 0, f"{why}: {r.stderr}"
    assert "Traceback" not in r.stderr, f"{why}: {r.stderr}"
    assert "AttributeError" not in r.stderr, f"{why}: {r.stderr}"
    assert "NOT verified" in r.stderr, why
    assert bridge.bodies and bridge.bodies[-1]["target"] == CANONICAL_INSTANCE
    if why != "no host key at all":
        assert "could not read" in r.stderr, (
            f"{why}: an unreadable SHAPE must be named as such, not reported as "
            f"an unidentifiable host")


# --------------------------------------------------------------------------- #
# SKILL.md names the exemption list — pin it to the CLI's
# --------------------------------------------------------------------------- #
def test_SKILL_md_names_every_free_text_subcommand_the_CLI_exempts():
    """🔴 SKILL.md is the file an AGENT reads; the CLI header is not.

    The exemption is a hand-maintained list in two places, in two languages, and
    the failure is silent in the direction that matters: add a subcommand to
    `REF_FREE_TEXT_SUBCOMMANDS` without updating SKILL.md and an agent reads
    "either side of the op", writes `browser <that-sub> bw://…` expecting
    routing, and instead sends the reference as TEXT — rc 0, wrong tab, no error.
    That is the same class as the README permission list that had already gone
    stale, and this file already carries a seam test of exactly this shape.

    Pinned as a SET, so the order and the surrounding prose stay free.
    """
    cli = CLI.read_text(encoding="utf-8")
    m = re.search(r'^REF_FREE_TEXT_SUBCOMMANDS="([^"]*)"', cli, re.M)
    assert m, ("REF_FREE_TEXT_SUBCOMMANDS vanished from the CLI — this guard now "
               "checks nothing. Re-point it at whatever defines the list.")
    declared = set(m.group(1).split())
    assert declared, "the CLI's exemption list parsed EMPTY — the pin would be vacuous"

    skill = (BB / "SKILL.md").read_text(encoding="utf-8")
    # 🔴 PIN THE EXEMPTION SENTENCE, NOT THE PARAGRAPH. The first version of this
    # guard collected every `backticked` lowercase word in the surrounding
    # paragraph — and SURVIVED its own negative control, because deleting `agent`
    # from the exemption list left it named one sentence later ("`agent` REFUSES a
    # leading one"). A guard satisfied by an unrelated mention of the word is
    # satisfied by the very rot it exists to catch. Match the slash-separated run
    # that carries the claim, and nothing else.
    # `\s+`, not a literal space: SKILL.md is hard-wrapped, so the list can sit on
    # the line after "For". The first version required a space and went red on a
    # pure reflow — which was the guard behaving correctly (it said "re-point me"
    # rather than passing), but the pattern should tolerate wrapping.
    m2 = re.search(
        r"For\s+((?:`[a-z]+`/)*`[a-z]+`)\s+it is a reference only BEFORE",
        skill)
    assert m2, ("SKILL.md's free-text exemption sentence is gone or reworded past "
                "this pattern — re-point this guard rather than deleting it; "
                "without it the list silently stops being a claim about the CLI.")
    named = set(re.findall(r"`([a-z]+)`", m2.group(1)))
    assert named, "the exemption sentence parsed EMPTY — the pin would be vacuous"
    assert named == declared, (
        f"SKILL.md's free-text list and the CLI's disagree. SKILL.md names "
        f"{sorted(named)}; the CLI exempts {sorted(declared)}. An agent reading "
        f"SKILL.md will expect routing for anything missing here and get TEXT — "
        f"rc 0, wrong tab, no error.")


# --------------------------------------------------------------------------- #
# `agent` refuses a TAB, not a SPELLING
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("argv, env, source", [
    (["--tab", "12345", "--instance", "main", "agent", "--dry-run", "g"], {}, "--tab"),
    (["--instance", "main", "agent", "--dry-run", "g"], {"BB_TAB": "12345"}, "$BB_TAB"),
])
def test_agent_refuses_a_tab_from_ANY_source(bridge, argv, env, source):
    """🔴 The first version of this guard refused only the `bw://` spelling, and
    an audit walked around it in two steps.

    `browser --tab 12345 agent …` reproduced the exact wrong answer the guard
    exists to prevent — the tab discarded in silence, the agent answering about
    its own blank tab — with no mention of the dropped flag. An exported
    `$BB_TAB` was worse: it leaked into browser-agent's OWN child `browser`
    calls, so the tab reappeared on the wire while `_note_env_routing` announced
    a route `agent` had never honoured.

    The guard now keys on the STATE (a tab is set) and names whichever source
    supplied it, which is what makes it cover a hazard rather than a word.
    """
    r = bridge.run(*argv, env=env)
    assert r.returncode != 0, r.stdout
    assert "agent cannot honour a tab" in r.stderr, r.stderr
    assert source in r.stderr, f"the refusal must name the source: {r.stderr}"
    assert bridge.bodies == [], f"nothing may be dispatched: {bridge.bodies}"


def test_agent_without_any_tab_is_untouched(bridge, tmp_path):
    """INVARIANT GUARD: the refusal is scoped to a ROUTING VARIABLE, not to `agent`.

    A guard widened onto the subcommand rather than the state would break the
    documented way to run the agent, which is the regression a too-wide fix
    introduces here — and every widening in this ladder (bw:// -> --tab -> $BB_TAB
    -> --frame -> $BB_FRAME) had to keep this case green.

    This replaces a near-duplicate (`test_agent_still_works_with_an_explicit_
    instance`) that had identical input and the same two assertions, differing
    only in its failure text.
    """
    r = _run_beside_stub_agent(bridge, tmp_path, "no-tab", "--instance", "main",
                               "agent", "--dry-run", "do a thing")
    assert "STUB_GOAL=[do a thing]" in r.stdout, (
        "the documented way to run the agent stopped forwarding its goal; "
        f"stdout={r.stdout!r} stderr={r.stderr!r}")
    assert "agent cannot honour" not in r.stderr, r.stderr
    assert "a goal is required" not in r.stderr, r.stderr


# --------------------------------------------------------------------------- #
# `agent` refuses EVERY routing variable it cannot keep — and leaks none
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("argv, env, source, word", [
    (["--frame", "evil.example", "--instance", "main", "agent", "g"], {},
     "--frame", "frame"),
    (["--instance", "main", "agent", "g"], {"BB_FRAME": "evil.example"},
     "$BB_FRAME", "frame"),
])
def test_agent_refuses_a_frame_from_any_source(bridge, argv, env, source, word):
    """🔴 The SECOND widening, found the same way as the first.

    Keying the guard on `TAB` alone left the sibling routing variable untouched:
    `browser --frame evil.example agent …` discarded the flag in silence — the
    original defect's exact shape — and an exported `$BB_FRAME` reached
    browser-agent while `_note_env_routing` announced a route `agent` had never
    honoured. browser-agent accepts neither flag, so neither can mean anything
    past this point.
    """
    r = bridge.run(*argv, env=env)
    assert r.returncode != 0, r.stdout
    assert f"agent cannot honour a {word}" in r.stderr, r.stderr
    assert source in r.stderr, f"the refusal must name the source: {r.stderr}"
    assert bridge.bodies == [], f"nothing may be dispatched: {bridge.bodies}"


@pytest.mark.parametrize("var", ["BB_TAB", "BB_FRAME", "BB_INSTANCE"])
def test_the_agent_arm_does_not_LEAK_routing_env_to_its_children(bridge, tmp_path, var):
    """🔴 The guard closes the DOOR; this closes the SEAM.

    `--tab ""` is an explicit-empty that clears the shell variable while leaving
    the EXPORT intact — and that is the idiom this file documents for
    `--instance`. So an operator who exports BB_TAB, hits the refusal, and clears
    it the documented way still hands browser-agent an environment in which every
    child `browser` call routes to that tab: a confident answer about the wrong
    page, rc 0, no error anywhere.

    Driven against a STUB browser-agent that prints its inherited environment, so
    this needs no model backend and reads the same in both tiers.
    """
    stub_dir = tmp_path / f"stub-{var}"
    stub_dir.mkdir()
    (stub_dir / "browser").write_text(CLI.read_text(encoding="utf-8"), encoding="utf-8")
    # 🔴 write_exec, NOT a hand-written shebang. `#!/usr/bin/env bash` execs on
    # the NixOS dev host and does NOT exist in the nix build sandbox, so a stub
    # written that way is green here and ENOENT in the tier the merge gates on.
    # This file learned it the way the repo already had five times over — see
    # scripts/testlib/mockbin.py, which exists precisely so the rule is not
    # re-derived at a sixth call site. The body is POSIX sh; write_exec owns the
    # shebang so no call site can reintroduce the trap.
    mockbin.write_exec(stub_dir / "browser-agent",
                       f'printf "SEEN {var}=[%s]\\n" "${{{var}-<unset>}}"\n')

    flag = {"BB_TAB": "--tab", "BB_FRAME": "--frame",
            "BB_INSTANCE": "--instance"}[var]
    r = subprocess.run(
        # "" is the EXPLICIT-EMPTY this file documents as the way to clear an
        # inherited BB_*: it zeroes the shell variable while leaving the export
        # intact, which is exactly the shape that leaked.
        ["bash", str(stub_dir / "browser"), flag, "", "--instance", "main",
         "agent", "some goal"],
        env={**bridge.env_for_stub(), var: "999"},
        capture_output=True, text=True, timeout=CLI_TIMEOUT_S)

    assert "SEEN" in r.stdout, (
        f"the stub agent never ran, so this test proves nothing: "
        f"{r.stdout!r} / {r.stderr!r}")
    assert f"SEEN {var}=[<unset>]" in r.stdout, (
        f"{var} leaked into browser-agent's environment; its child `browser` "
        f"calls would route with it. stdout: {r.stdout!r}")


def test_POSITIVE_CONTROL_the_stub_agent_can_see_an_inherited_var(bridge, tmp_path):
    """The assertions above are ABSENCES — prove the stub can see a leak.

    If the stub never ran, or never observed the environment, `[<unset>]` would
    be reported for a variable that WAS inherited and every leak assertion would
    pass while testing nothing. That is not hypothetical: it is exactly what
    happened when the stubs carried a `/usr/bin/env` shebang and could not exec
    in the nix sandbox — this control is what said so.
    """
    stub_dir = tmp_path / "stub-control"
    stub_dir.mkdir()
    (stub_dir / "browser").write_text(CLI.read_text(encoding="utf-8"), encoding="utf-8")
    # 🔴 THE CONTROL MUST NOT REST ON THE DEFECT IT CONTROLS FOR. This used
    # BB_INSTANCE, on the stated reasoning that the agent arm deliberately does
    # not unset it — and that reasoning WAS the bug: `--instance ""` forwarded
    # nothing while BB_INSTANCE survived the exec, so browser-agent's children
    # drove the wrong Brave profile. Closing that leak would have broken this
    # control, which is the tell that the control was load-bearing on a defect.
    # BROWSER_BRIDGE_PORT is read by the CLI and never unset by it, so it
    # measures inheritance without measuring the thing under test.
    mockbin.write_exec(
        stub_dir / "browser-agent",
        'printf "SEEN BROWSER_BRIDGE_PORT=[%s]\\n" "${BROWSER_BRIDGE_PORT-<unset>}"\n')
    r = subprocess.run(
        ["bash", str(stub_dir / "browser"), "agent", "some goal"],
        env=bridge.env_for_stub(),
        capture_output=True, text=True, timeout=CLI_TIMEOUT_S)
    assert "SEEN BROWSER_BRIDGE_PORT=[" in r.stdout, (
        f"the stub never ran or never read the environment, so the leak "
        f"assertions above are vacuous. stdout: {r.stdout!r}")
    assert "<unset>" not in r.stdout, (
        f"an inherited variable did NOT reach the stub, so an absence there "
        f"proves nothing about unsetting. stdout: {r.stdout!r}")


def test_SKILL_md_names_every_routing_flag_the_agent_guard_REFUSES():
    """🔴 The second ledger: the refusal list, not the exemption list.

    The free-text pin above caught SKILL.md drifting on which subcommands treat
    a trailing reference as text. It does NOT see the other list this sentence
    carries — which routing flags `agent` refuses — and that one drifted the
    moment the guard was widened from `--tab` to `--frame`: SKILL.md kept saying
    "`agent` refuses a LEADING one and any `--tab`", so an agent routing off it
    writes `browser --frame checkout-iframe agent "fill the form"` and gets rc 1
    for a reason the file it read does not mention.

    Derived from the guard itself: each `[ -z "$VAR" ] || die "agent cannot
    honour ..."` line names a variable, and each variable has a flag spelling
    SKILL.md must name.
    """
    cli = CLI.read_text(encoding="utf-8")
    refused = set(re.findall(
        r'\[ -z "\$([A-Z]+)" \][^\n]*\|\| die "agent cannot honour', cli))
    assert refused, (
        "no `agent cannot honour` refusals parsed out of the CLI — this guard "
        "now checks nothing. Re-point it at whatever implements the refusal.")
    flags = {v: f"--{v.lower()}" for v in refused}

    skill = (BB / "SKILL.md").read_text(encoding="utf-8")
    sentence = re.search(r"`agent` refuses[^\n]*(\n[^\n]*)?", skill)
    assert sentence, ("SKILL.md no longer describes the `agent` refusal at all — "
                      "re-point this guard rather than deleting it.")
    text = sentence.group(0)
    missing = {v: f for v, f in flags.items() if f"`{f}`" not in text}
    assert not missing, (
        f"the CLI refuses {sorted(flags.values())} for `agent`, but SKILL.md's "
        f"refusal sentence does not name {sorted(missing.values())}. An agent "
        f"reading SKILL.md will use it and get rc 1 with no warning there. "
        f"Sentence: {text!r}")


def test_an_explicitly_cleared_instance_forwards_NOTHING_and_leaks_NOTHING(bridge, tmp_path):
    """The exact shape the BB_INSTANCE fix's own comment describes.

    The leak matrix above passes `--instance "" --instance main`, so INSTANCE
    ends up "main" — that pins the unconditional `unset`, but it never exercises
    the case the defect actually lived in: `--instance ""` ALONE, where nothing
    is forwarded as an argument and the export was the only thing left carrying
    a profile. An audit noted the gap; this closes it.

    Both halves matter and they are different claims: no `--instance` reaches
    browser-agent's argv (so it does not silently route), AND no BB_INSTANCE
    reaches its environment (so its own child `browser` calls do not either).
    """
    stub_dir = tmp_path / "stub-cleared"
    stub_dir.mkdir()
    (stub_dir / "browser").write_text(CLI.read_text(encoding="utf-8"), encoding="utf-8")
    mockbin.write_exec(
        stub_dir / "browser-agent",
        'printf "ARGV=[%s]\\n" "$*"\n'
        'printf "ENV BB_INSTANCE=[%s]\\n" "${BB_INSTANCE-<unset>}"\n')
    r = subprocess.run(
        ["bash", str(stub_dir / "browser"), "--instance", "", "agent", "some goal"],
        env={**bridge.env_for_stub(), "BB_INSTANCE": "work"},
        capture_output=True, text=True, timeout=CLI_TIMEOUT_S)
    assert "ARGV=[" in r.stdout, (
        f"the stub agent never ran, so this test proves nothing: {r.stdout!r} "
        f"/ {r.stderr!r}")
    assert "--instance" not in r.stdout.split("ENV ")[0], (
        f"an explicitly-cleared instance was still forwarded: {r.stdout!r}")
    assert "ENV BB_INSTANCE=[<unset>]" in r.stdout, (
        f"BB_INSTANCE survived into browser-agent's environment, so its child "
        f"`browser` calls would drive profile 'work' — the wrong Brave profile, "
        f"with nothing on stderr saying so. stdout: {r.stdout!r}")
