"""`screenshot --json` and the `BB_INSTANCE`/`BB_TAB`/`BB_FRAME` targeting
defaults — all HEADLESS.

Fully offline: a loopback stub HTTP server stands in for server.py (the same
pattern as ``test_browser_cli_args.py``), so no Brave, no extension, no bridge.
It records every /cmd body and answers a scripted envelope per op.

WHY EACH GROUP EXISTS
---------------------
1. ``screenshot --json`` — the CLI's advisories were ALREADY on stderr (measured
   at ``origin/main`` 2026-08-20;
   ``test_stdout_is_pure_json_even_when_the_hidden_advisory_fires`` pins it, and
   its passing THERE is the measurement that refutes the "advisories pollute
   stdout" premise). The one command whose stdout genuinely is not JSON is
   ``screenshot <path>``, which prints a bare path plus a ``#`` comment. That is
   what breaks ``json.loads(stdout)``.
2. ``BB_INSTANCE`` / ``BB_TAB`` / ``BB_FRAME`` — callers hoisted the three
   targeting flags into a shell variable and splatted it (``F="--instance work
   --tab 12"; browser $F text``). zsh does not field-split an unquoted ``$var``,
   so the whole string arrives as ONE argument and the CLI reports an unknown
   subcommand naming something the caller never typed.

   Precedence is only half the contract. The other half is LIFETIME: an exported
   ``BB_*`` is inherited by every descendant process, so a later destructive op
   can be routed with nothing in its argv saying so. The routing NOTE tests below
   cover that half.

RED/GREEN MATRIX — measured, not assumed; see the PR body for the run.

Run: nix-shell -p python312Packages.pytest curl --run \\
       "pytest scripts/browser-bridge/tests/test_browser_screenshot_json_and_env.py"
"""
import base64
import json
import os
import shutil
import subprocess
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

BB = Path(__file__).resolve().parent.parent
CLI = BB / "browser"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("curl") is None,
    reason="the browser CLI is bash and talks to the bridge with curl")


def _ok(data):
    return {"ok": True, "result": {"id": "c", "ok": True, "data": data}}


@pytest.fixture
def bridge(tmp_path):
    """A stub bridge whose reply is chosen by a per-op QUEUE.

    ``b.script("eval", [r1, r2, ...])`` queues responses for that op; the LAST
    entry repeats once the queue drains, so a test only has to describe the
    interesting prefix. Anything unscripted gets a generic success envelope.
    """
    scripts: dict = {}
    bodies: list = []

    class _H(BaseHTTPRequestHandler):
        def _reply(self, code, payload):
            raw = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode())
            bodies.append(body)
            op = body.get("op")
            queue = scripts.get(op)
            if queue:
                item = queue.pop(0) if len(queue) > 1 else queue[0]
            else:
                item = _ok({"url": "https://a.test", "value": None})
            code, payload = item if isinstance(item, tuple) else (200, item)
            self._reply(code, payload)

        def do_GET(self):
            self._reply(200, {"ok": True, "instances": []})

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    tok = tmp_path / "token"
    tok.write_text("screenshot-env-token\n")
    env = dict(os.environ)
    env.update({
        "BROWSER_BRIDGE_HOST": "127.0.0.1",
        "BROWSER_BRIDGE_PORT": str(srv.server_address[1]),
        "BROWSER_BRIDGE_TOKEN_FILE": str(tok),
        "CLAUDE_CODE_SESSION_ID": "pytest-screenshot-env",
        "HOME": str(tmp_path),
    })
    # Never inherit the developer's own targeting defaults — that would make the
    # BB_* tests below pass or fail on the ambient environment.
    for k in ("BB_INSTANCE", "BB_TAB", "BB_FRAME", "BB_NO_ROUTE_NOTE"):
        env.pop(k, None)

    class _B:
        @staticmethod
        def script(op, responses):
            scripts[op] = list(responses)

        @staticmethod
        def run(*args, **kw):
            e = dict(_B.env)
            e.update(kw.pop("extra_env", {}) or {})
            return subprocess.run(["bash", str(CLI), *args], env=e,
                                  capture_output=True, text=True, timeout=60)

        @staticmethod
        def ops():
            return [b.get("op") for b in _B.bodies]

    # Assigned AFTER the class body on purpose: a class body does not close over
    # the enclosing function's scope, so `bodies = bodies` in there is a
    # NameError, not a copy (the same note the sibling fixture in
    # test_browser_cli_args.py carries).
    _B.bodies = bodies
    _B.env = env

    try:
        yield _B
    finally:
        srv.shutdown()
        srv.server_close()


# --------------------------------------------------------------------------- #
# 1. stdout purity — the advisory premise, and the one real offender
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("args", [
    ["text"], ["html"], ["type", "hi", "--selector", "#q"], ["context"],
])
def test_stdout_is_pure_json_even_when_the_hidden_advisory_fires(bridge, args):
    """MEASURED at origin/main 2026-08-20: the advisories were ALREADY on stderr,
    so `json.loads(stdout)` worked. This is the guard that keeps it that way —
    the reported breakage was `screenshot <path>` (below), not this."""
    bridge.script("getHtml", [_ok({"html": "<i>", "hidden": True})])
    bridge.script("text", [_ok({"text": "hi", "hidden": True})])
    bridge.script("type", [_ok({"typed": 2, "hidden": True})])
    bridge.script("context", [_ok({"url": "https://a.test", "hidden": True})])
    cp = bridge.run(*args)
    assert cp.returncode == 0, cp.stderr
    json.loads(cp.stdout)                       # would raise on any stray line
    assert "browser: tab is hidden" in cp.stderr, (
        "positive control: the advisory must actually have FIRED, or this test "
        "proves stdout purity for a case that never emits an advisory")


def _png_1x1():
    def chunk(tag, data):
        return (len(data).to_bytes(4, "big") + tag + data
                + zlib.crc32(tag + data).to_bytes(4, "big"))
    ihdr = (1).to_bytes(4, "big") + (1).to_bytes(4, "big") + bytes([8, 0, 0, 0, 0])
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"\x00\x00")) + chunk(b"IEND", b""))


def _shot_reply(**extra):
    d = {"dataUrl": "data:image/png;base64," + base64.b64encode(_png_1x1()).decode(),
         "url": "https://a.test", "via": "cdp"}
    d.update(extra)
    return _ok(d)


@pytest.fixture
def shot(bridge):
    bridge.script("screenshot", [_shot_reply()])
    return bridge


def test_screenshot_with_a_path_still_prints_the_bare_path(shot, tmp_path):
    """INVARIANT GUARD: the default output is back-compat and must NOT change —
    `--json` is additive. This is also the negative control for the next test:
    it demonstrates the stdout that breaks `json.loads`."""
    out = tmp_path / "s.png"
    cp = shot.run("screenshot", str(out))
    assert cp.returncode == 0, cp.stderr
    assert cp.stdout.splitlines()[0] == str(out)
    with pytest.raises(ValueError):
        json.loads(cp.stdout)


def test_screenshot_json_makes_stdout_parseable(shot, tmp_path):
    out = tmp_path / "s.png"
    cp = shot.run("screenshot", str(out), "--json")
    assert cp.returncode == 0, cp.stderr
    res = json.loads(cp.stdout)
    assert res["ok"] is True
    assert res["path"] == str(out)
    assert res["bytes"] == out.stat().st_size > 0
    assert res["url"] == "https://a.test" and res["via"] == "cdp"
    # The Read hint survives, in a FIELD instead of a comment line.
    assert "Read the file" in res["note"]


def test_screenshot_without_a_path_is_json_either_way(shot):
    """The temp-file branch already printed JSON; --json must not change it, and
    both branches must emit the SAME keys so one parser handles both."""
    plain = json.loads(shot.run("screenshot").stdout)
    shot.script("screenshot", [_shot_reply()])
    flagged = json.loads(shot.run("screenshot", "--json").stdout)
    assert set(plain) == set(flagged)
    for p in (plain["path"], flagged["path"]):
        os.unlink(p)


@pytest.mark.parametrize("explicit", [True, False])
def test_screenshot_json_payload_is_on_stdout_not_stderr(bridge, tmp_path, explicit):
    """Reported 2026-08-21 after the live gate: `screenshot <path> --json` was
    seen printing its envelope on STDERR with stdout empty. This pins the
    routing with the two streams captured on SEPARATE pipes, so a payload on the
    wrong one cannot satisfy both halves of the assertion.

    🔴 POSITIVE CONTROL: the tab is hidden, so the advisory MUST land on stderr.
    Without it, "no envelope on stderr" is also true of a run where stderr was
    never written to at all, and the test would prove nothing about routing."""
    bridge.script("screenshot", [_shot_reply(hidden=True)])
    args = ["screenshot"] + ([str(tmp_path / "s.png")] if explicit else []) + ["--json"]
    cp = bridge.run(*args)
    assert cp.returncode == 0, cp.stderr
    assert "browser: tab is hidden" in cp.stderr, (
        "positive control: stderr must actually have been written to, or this "
        "test passes for a run that never emits on stderr at all")
    res = json.loads(cp.stdout)
    assert res["ok"] is True and res["bytes"] > 0
    assert '"path"' not in cp.stderr and '"bytes"' not in cp.stderr, (
        "the envelope must be on stdout ONLY, never duplicated onto stderr")
    if explicit:
        assert res["path"] == str(tmp_path / "s.png")
    else:
        os.unlink(res["path"])


def test_screenshot_an_unwritable_path_says_so_instead_of_a_traceback(shot, tmp_path):
    """A caller-supplied path can be a directory or a read-only dir. An uncaught
    OSError printed a TRACEBACK on stderr and NOTHING on stdout — which reads
    exactly like "the --json payload went to stderr", and is how a failed write
    gets misfiled as a routing bug."""
    d = tmp_path / "adir"
    d.mkdir()
    cp = shot.run("screenshot", str(d), "--json")
    assert cp.returncode == 1
    assert "could not write" in cp.stderr
    assert "Traceback" not in cp.stderr, "a stack trace is not a diagnosis"
    assert cp.stdout == ""


def test_data_url_and_json_are_refused_rather_than_one_being_ignored(shot):
    """`--data-url` prints the RAW response envelope and never builds the compact
    {ok,path,bytes,…} shape, so `--json` has nothing to opt into. Silently
    ignoring it is the worst outcome — the caller asked for a parseable envelope,
    would get a DIFFERENT parseable envelope with none of the keys they were
    about to read, and nothing would say so.

    The negative control is the pre-existing `--data-url` + path rejection, which
    proves this branch is a real refusal and not a no-op that happens to exit 1
    for some other reason."""
    cp = shot.run("screenshot", "--data-url", "--json")
    assert cp.returncode == 1, cp.stdout
    assert "--data-url and --json are mutually exclusive" in cp.stderr
    assert shot.ops() == [], "the refusal must precede the wire op"


def test_data_url_alone_still_prints_the_raw_envelope(shot):
    """CONTROL for the refusal above: `--data-url` on its own is unchanged, so
    the new check cannot be passing by breaking the flag it guards."""
    cp = shot.run("screenshot", "--data-url")
    assert cp.returncode == 0, cp.stderr
    assert "data:image/png;base64," in cp.stdout
    assert shot.ops() == ["screenshot"]


def test_unknown_screenshot_flag_names_json(bridge):
    cp = bridge.run("screenshot", "--nope")
    assert cp.returncode == 1
    assert "--json" in cp.stderr


# --------------------------------------------------------------------------- #
# 2. BB_INSTANCE / BB_TAB / BB_FRAME — precedence
# --------------------------------------------------------------------------- #
def test_env_vars_supply_the_targeting_defaults(bridge):
    cp = bridge.run("text", extra_env={"BB_INSTANCE": "work", "BB_TAB": "1234",
                                       "BB_FRAME": "7"})
    assert cp.returncode == 0, cp.stderr
    body = bridge.bodies[0]
    assert body["target"] == "work"
    assert body["tab"] == 1234
    assert body["frame"] == "7"


@pytest.mark.parametrize("flag,env,key,want", [
    (["--instance", "home"], {"BB_INSTANCE": "work"}, "target", "home"),
    (["--tab", "99"], {"BB_TAB": "1234"}, "tab", 99),
    (["--frame", "2"], {"BB_FRAME": "7"}, "frame", "2"),
])
def test_an_explicit_flag_beats_the_env_default(bridge, flag, env, key, want):
    cp = bridge.run(*flag, "text", extra_env=env)
    assert cp.returncode == 0, cp.stderr
    assert bridge.bodies[0][key] == want


def test_an_explicit_empty_instance_clears_an_inherited_one(bridge):
    """`--instance ''` is an explicit empty, not an absent flag — it must be able
    to undo an ambient BB_INSTANCE, or a caller in an exported environment has no
    way back to single-instance routing."""
    cp = bridge.run("--instance", "", "text", extra_env={"BB_INSTANCE": "work"})
    assert cp.returncode == 0, cp.stderr
    assert "target" not in bridge.bodies[0]


def test_a_malformed_BB_TAB_names_BB_TAB_not_the_flag(bridge):
    """Reporting "--tab must be a numeric tab id" for a value that came from the
    environment sends the reader hunting a flag that appears nowhere in their
    command line."""
    cp = bridge.run("text", extra_env={"BB_TAB": "not-a-number"})
    assert cp.returncode == 1
    assert "BB_TAB" in cp.stderr
    assert "--tab must be" not in cp.stderr
    assert bridge.ops() == [], "nothing may reach the wire on a bad target"


def test_the_env_vars_are_documented_where_the_flags_are():
    """A targeting default nobody can discover is not a fix. Both the CLI's own
    --help header and the agent-facing SKILL.md must name all three."""
    help_text = subprocess.run(["bash", str(CLI), "--help"],
                               capture_output=True, text=True, timeout=60).stdout
    skill = (BB / "SKILL.md").read_text()
    for var in ("BB_INSTANCE", "BB_TAB", "BB_FRAME"):
        assert var in help_text, f"{var} missing from `browser --help`"
        assert var in skill, f"{var} missing from SKILL.md"


# --------------------------------------------------------------------------- #
# 3. BB_* lifetime — an inherited route must be VISIBLE at the moment it is used
#
# Precedence is the easy half. An exported BB_TAB/BB_FRAME/BB_INSTANCE steers
# every later `browser` call in that shell and in every descendant process,
# including a destructive `type`/`click`/`upload` whose argv mentions no routing
# at all — and the result envelope is identical either way.
# --------------------------------------------------------------------------- #
def test_an_env_routed_op_says_so_on_stderr(bridge):
    cp = bridge.run("type", "secret", "--selector", "#q",
                    extra_env={"BB_FRAME": "evil.example"})
    assert cp.returncode == 0, cp.stderr
    assert "routed from the ENVIRONMENT" in cp.stderr
    assert "$BB_FRAME=evil.example" in cp.stderr
    json.loads(cp.stdout), "the note must not touch stdout"


def test_the_route_note_names_every_variable_that_supplied_a_target(bridge):
    cp = bridge.run("text", extra_env={"BB_INSTANCE": "work", "BB_TAB": "1234",
                                       "BB_FRAME": "7"})
    assert cp.returncode == 0, cp.stderr
    for frag in ("$BB_INSTANCE=work", "$BB_TAB=1234", "$BB_FRAME=7"):
        assert frag in cp.stderr, f"{frag} missing from the routing note"


def test_a_flag_supplied_target_is_NOT_reported_as_env_routed(bridge):
    """The note claims 'this came from the environment'. A flag that has beaten
    the env default must therefore drop OUT of it, or the note is a false
    statement about the very case it exists to distinguish."""
    cp = bridge.run("--frame", "2", "text",
                    extra_env={"BB_FRAME": "7", "BB_TAB": "1234"})
    assert cp.returncode == 0, cp.stderr
    assert "$BB_TAB=1234" in cp.stderr, "BB_TAB really did supply the tab"
    assert "$BB_FRAME" not in cp.stderr, "--frame won; the env did not supply it"


def test_no_note_at_all_when_nothing_came_from_the_environment(bridge):
    """The negative control. A note that fires unconditionally carries no
    information, and would train a reader to skip the line that matters."""
    cp = bridge.run("--tab", "99", "text")
    assert cp.returncode == 0, cp.stderr
    assert "routed from the ENVIRONMENT" not in cp.stderr


def test_the_route_note_is_printed_once_per_invocation(bridge):
    """A verified/multi-op command calls cmd_op several times. Three copies of
    the same advisory is noise, and noise is what gets skimmed."""
    cp = bridge.run("nav", "https://a.test", "--wake",
                    extra_env={"BB_INSTANCE": "work"})
    assert bridge.ops() == ["nav", "wake"], "this command really is two ops"
    assert cp.stderr.count("routed from the ENVIRONMENT") == 1


def test_BB_NO_ROUTE_NOTE_silences_it_without_changing_the_routing(bridge):
    cp = bridge.run("text", extra_env={"BB_TAB": "1234", "BB_NO_ROUTE_NOTE": "1"})
    assert cp.returncode == 0, cp.stderr
    assert "routed from the ENVIRONMENT" not in cp.stderr
    assert bridge.bodies[0]["tab"] == 1234, "silencing must not change the route"
